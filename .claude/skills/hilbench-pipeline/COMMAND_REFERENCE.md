# Command reference — what each `/hilbench-*` command actually runs

Precise, per-command lookup: inputs consumed, the script/reference it executes, the checks it
performs, what it writes, and the result sentinels it can emit. For the narrative "how to build
a task" walkthrough, see [`CONTRIBUTOR_GUIDE.md`](./CONTRIBUTOR_GUIDE.md); for architecture,
[`README.md`](./README.md).

**Conventions.** `/hilbench-*` commands run in the **chat window**; `bash`/`docker`/`source`
lines run in the **terminal**. Paths: `$TASK_FILES` = read-only inputs, `$DELIVERABLES` = your
authored outputs. Container env (set by `/hilbench-provision`, loaded via
`source deliverables/.hilbench_env`): `$HILBENCH_CONTAINER` (default `hilbench_task`),
`$HILBENCH_REPO` (default `/app`), `$HILBENCH_INSTANCE_ID`. Every command **STOPS for review** —
nothing auto-advances.

Legend: **Reads** = inputs consumed · **Runs** = script/reference executed · **Checks** = what
it verifies (gates) · **Writes** = files produced · **Result** = sentinels + next step.
⭐ marks the critical evals.

---

## Which command runs which eval (at a glance)

Every numbered model eval (verbatim prompt in `references/eval-prompts.md`) runs inside exactly
one command, except eval 7 which is split across three. The two Tier-0/linter checks are not
model evals — they run mechanically at the registry gate. "Needs" = why it can't run earlier.

| # | Eval | Command that runs it | Stage / gate | Needs |
|---|------|----------------------|--------------|-------|
| 10 | distribution ⭐ | `/hilbench-validate-registry` | GATE 1 (registry) | registry + required distribution |
| 5 | independence ⭐ | `/hilbench-validate-registry` | GATE 1 (registry) | registry |
| 6 | objective | `/hilbench-validate-registry` | GATE 1 (registry) | registry |
| 4 | blocker_type | `/hilbench-validate-registry` | GATE 1 (registry) | registry + specs |
| 8 | descriptions | `/hilbench-validate-registry` | GATE 1 (registry) | registry + specs |
| 9 | questions | `/hilbench-validate-registry` | GATE 1 (registry) | registry + specs |
| 11 | self_reference | `/hilbench-validate-registry` | GATE 1 (registry) | registry + specs |
| 7 | critical_implementation ⭐ **(Test B + C)** | `/hilbench-validate-registry` | GATE 1 (registry) | registry (Test A stays UNVERIFIED) |
| 1 | test_list | `/hilbench-validate-artifacts` | GATE 2 (artifacts) | test patch |
| 2 | test_relevance | `/hilbench-validate-artifacts` | GATE 2 (artifacts) | test patch |
| 3 | golden_patch | `/hilbench-validate-artifacts` | GATE 2 (artifacts) | golden + setup patch |
| 12 | request | `/hilbench-validate-artifacts` | GATE 2 (artifacts) | golden patch |
| 7 | critical_implementation ⭐ **(Test A / necessity)** | `/hilbench-validate-artifacts` | GATE 2 (artifacts) | tests + golden now exist |
| 7 | critical_implementation ⭐ **(empirical Test B)** | `/hilbench-check1` | Stage 3 (Attempter) | a live no-ask solve in the container |
| — | **guessability** (linter, not a model eval) | `/hilbench-validate-registry` | GATE 1 (registry) | registry |
| — | **description_type_coherence** (linter) | `/hilbench-validate-registry` | GATE 1 (registry) | registry |

Harbor (Stage 4) runs a different set of judges (trajectory "guessed" / "technically passed" /
summary), not these numbered registry/artifact evals — see Stage 4 below.

---

## Stage 0 — Provision

### `/hilbench-provision  [--image REF --commit HASH --instance-id ID --container NAME --checkout --no-checkout --validate-original]`
- **Surface:** chat
- **Reads:** `$TASK_FILES/task_info.txt` (docker image source, `base_commit_hash`, `instance_id`);
  `$DELIVERABLES/Dockerfile` if it is a buildable `FROM …` recipe
- **Runs:** `scripts/provision_repo.sh` — resolves the image source (registry ref, `docker pull`
  cmd, or a URL to a Dockerfile → `docker build` / tarball → `docker load` / pull cmd), pulls/builds
  it once (idempotent tag from `instance_id`), starts a long-lived `sleep infinity` container,
  verifies `HEAD == base_commit_hash` and **auto-checks-out the base commit if HEAD differs**
- **Image precedence:** `--image`/`HILBENCH_IMAGE` > a buildable `$DELIVERABLES/Dockerfile`
  (built from source; content-hash-tagged so edits rebuild) > the `task_info` source. A
  corrected `deliverables/Dockerfile` is how you fix an image missing dependencies — the built
  image is what later stages and the grade run against. The tarball-stub / empty Dockerfile
  (no `FROM`) is ignored.
- **Writes:** `$DELIVERABLES/.hilbench_env` (exports `HILBENCH_*`)
- **Result:** `PROVISION_OK` → `source deliverables/.hilbench_env`, then
  `/hilbench-validate-original` · `UNRECOGNIZED_IMAGE_SOURCE` (STOP; URL payload not a
  Dockerfile/tarball/pull cmd) · `IMAGE_BUILD_FAILED` / `IMAGE_LOAD_FAILED` (STOP; source
  recognized but build/load failed — build auto-retries with `DOCKER_BUILDKIT=0`) ·
  `PARENT_COMMIT_MISMATCH` (STOP; base commit not in history, or
  `--no-checkout` set) · `REQUIRED_INPUT_FILE_MISSING`
- **Note:** `--validate-original` chains Stage 0.5 immediately after a successful provision.
  Auto-checkout is the default; `--no-checkout` surfaces a mismatch instead of fixing it,
  `--checkout` forces a checkout up front.

---

## Stage 0.5 — Baseline sanity

### `/hilbench-validate-original  [--instance-id ID --tests-file PATH --container NAME]`
- **Surface:** chat (needs a running container)
- **Reads:** `$TASK_FILES/test_patch.diff`, `$TASK_FILES/golden_patch.diff`,
  `$TASK_FILES/relevant_tests_original.txt` (built if absent — throwaway list of the ORIGINAL
  test patch's tests, per `references/06-relevant-tests.md` rules)
- **Runs:** `scripts/validate_original.sh` — full F2P via `task_checker.py` (with tests-file +
  instance id), else falls back to an apply-only check of both original patches
- **Checks:** original test+golden patches apply on the parent commit and show a clean
  FAIL→PASS (Codebase Editing Workflow step 5)
- **Writes:** `$TASK_FILES/relevant_tests_original.txt` (throwaway, if built)
- **Result:** `ORIGINAL_PATCHES_OK` (F2P) / `ORIGINAL_APPLY_OK` (apply-only) → author the
  registry · `ORIGINAL_TEST_PATCH_APPLY_FAIL` / `ORIGINAL_GOLDEN_PATCH_APPLY_FAIL` /
  `ORIGINAL_F2P_FAIL` / `ORIGINAL_CHECK_ERROR` (STOP; do not inject) · `CONTAINER_NOT_RUNNING`

---

## Stage 1 — Author the registry (pick ONE path)

### `/hilbench-registry-generate  [distribution override]`  (one-shot)
- **Surface:** chat
- **Reads:** `$TASK_FILES/task_info.txt`, `test_patch.diff`, `golden_patch.diff`; required
  distribution from `task_info.txt`
- **Runs:** `references/blocker-registry-generator.md` — drafts the whole registry from the
  originals, applying the criticality / objectivity / guessability / independence criteria
- **Writes:** `$DELIVERABLES/blocker_registry.json` + `blocker_registry.md` (mirror)
- **Result:** `STAGE registry-generate: DONE` · `CONFIG_MISSING` → then
  `/hilbench-validate-registry` (always)

### `/hilbench-plan  [distribution override]`  (numbered step 01)
- **Surface:** chat
- **Reads:** `$TASK_FILES/task_info.txt`, `test_patch.diff`, `golden_patch.diff`
- **Runs:** `references/01-scenario-blocker-planning.md` — decides **Scenario 1 vs 2**, locks
  the type distribution, runs the reviewer lens (enforceable / objective / independent / not
  reliably inferable) on each planned blocker
- **Writes:** `$DELIVERABLES/plan.md` ONLY (blocker ids + intended types + verification
  anchors; scenario decision). Does NOT write the registry.
- **Result:** `STAGE plan: DONE` · `CONFIG_MISSING` → then `/hilbench-registry`

### `/hilbench-registry`  (numbered step 02)
- **Surface:** chat
- **Reads:** `$DELIVERABLES/plan.md` (source of truth), `$TASK_FILES/task_info.txt`,
  `test_patch.diff`, `golden_patch.diff`
- **Runs:** `references/02-generate-blocker-registry.md` — turns `plan.md` into the registry,
  applying all hard gates (objective, guessability, independence, description/type coherence,
  area selection, error-message limit, trigger-question relevance, text hygiene)
- **Writes:** `$DELIVERABLES/blocker_registry.json` + `blocker_registry.md`
- **Result:** `STAGE registry: DONE` · `PLAN_INVALID` → then `/hilbench-validate-registry`

> Manual path: hand-edit `$DELIVERABLES/blocker_registry.json` yourself, then validate.

### `/hilbench-registry-regenerate  <id[,id...]>  [--distribution override]`  (repair: selected blockers)
- **Surface:** chat
- **Reads:** `$DELIVERABLES/blocker_registry.json` (retained blockers), `$TASK_FILES/task_info.txt`,
  `test_patch.diff`, `golden_patch.diff`
- **Runs:** `references/blocker-regenerate.md` (reuses the `blocker-registry-generator.md` criteria)
  — regenerates ONLY the named ids, constrained to stay independent of / non-overlapping with the
  retained blockers, **type-locked** so the distribution holds; **merges** back (retained entries
  unchanged)
- **Writes:** `$DELIVERABLES/blocker_registry.json` + `blocker_registry.md` (target entries replaced)
- **Result:** `STAGE registry-regenerate: DONE` · `REQUIRED_INPUT_FILE_MISSING` ·
  `UNKNOWN_BLOCKER_ID <id>`. Then run the emitted stream: `/hilbench-validate-registry` →
  `/hilbench-inject` (re-derives specs/patches) → `/hilbench-validate-artifacts` →
  `/hilbench-validate-obstructed` → `/hilbench-check1` + `/hilbench-check2` →
  `/hilbench-evaluate-full --fresh`. (Surgical alternative to the destructive
  `/hilbench-registry-generate`; re-inject overwrites all derived artifacts, so regenerate FIRST,
  hand-tune artifacts after.)

### `/hilbench-validate-registry`  — **GATE 1** (before injection)
- **Surface:** chat
- **Reads:** `$DELIVERABLES/blocker_registry.json` (+`.md`), `$TASK_FILES/task_info.txt`
  (distribution); modified specs only if they already exist, else the originals
- **Runs:** `references/static-quality-evals.md` (Tier 0 schema + linters) +
  `references/eval-prompts.md` (model evals, run **verbatim**)
- **Checks:**
  - Tier 0: schema/shape (mechanical)
  - Verbatim model evals: **10** distribution ⭐, **5** independence ⭐, **6** objective,
    **8** descriptions, **9** questions, **11** self_reference, **4** blocker_type,
    **7** critical_implementation **(Test B/C only** — necessity is UNVERIFIED until the patches exist**)**
  - Linters (not from model evals): **guessability**, **description_type_coherence**
- **Writes:** nothing (verdict only)
- **Result:** `REGISTRY GATE: PASS` → `/hilbench-inject` · `REGISTRY GATE: FAIL` (fix + re-run)
  · `REGISTRY_INVALID` (schema fail; Tier 1 skipped)

---

## Stage 2 — Inject

### `/hilbench-inject  [--scenario 1|2]`
- **Surface:** chat (Scenario 2 edits the container repo)
- **Reads:** `$DELIVERABLES/blocker_registry.json`; `$TASK_FILES` originals; scenario resolved
  from `--scenario` → `plan.md` → agent analysis (Narrow Tests indicator)
- **Runs:** `references/03/04/05` (modified text), `references/07-patch-outputs.md` (Scenario 2
  obstructed patches), `references/06-relevant-tests.md` (step 06)
- **Writes:** `modified_problem_statement.txt`, `modified_requirements.txt`,
  `modified_public_interfaces.txt`, `relevant_tests.txt`; Scenario 2 also
  `test_patch_obstructed.diff`, `golden_patch_obstructed.diff`, `setup_patch.diff?`
  (Scenario 1 copies the originals)
- **Result:** `STAGE inject: DONE` (with relevant_tests count) → `/hilbench-validate-artifacts`

### `/hilbench-validate-artifacts`  — **GATE 2** (after injection)
- **Surface:** chat (run in a FRESH, isolated chat)
- **Reads:** `modified_*` specs, `relevant_tests.txt`, `test_patch_obstructed.diff`,
  `golden_patch_obstructed.diff`, `setup_patch.diff?`
- **Runs:** Part A → `references/08-patch-content-validator.md`; Part B →
  `references/eval-prompts.md` (verbatim)
- **Checks:**
  - Part A (08): **1** test_list, **2** test_relevance, **3** golden_patch, patch
    separation/overlap rules, the **alignment map** (spec→test→golden per blocker),
    **4** CodeBase-area confirmation (needs the setup patch)
  - Part B (verbatim): **12** request, **7** critical_implementation **Test A (necessity)** ⭐
- **Writes:** `$DELIVERABLES/validate_artifacts_result.txt` (verdict + alignment map + Part-B eval
  lines; no diff content or resolutions)
- **Result:** `ARTIFACTS GATE: PASS` → `/hilbench-validate-obstructed` · `ARTIFACTS GATE: FAIL - <eval>`
  (fix golden/tests or spec leak; never weaken tests; see `references/optional-repair.md`)

---

## Stage 2.5 — Obstructed baseline sanity

### `/hilbench-validate-obstructed  [--instance-id ID --golden-patch PATH --tests-file PATH --container NAME]`
- **Surface:** chat (needs a running container)
- **Reads:** `$DELIVERABLES/golden_patch_obstructed.diff`, `test_patch_obstructed.diff`,
  `relevant_tests.txt`, `setup_patch.diff?` (falls back to `$TASK_FILES` originals if a
  deliverable is absent)
- **Runs:** `scripts/validate_obstructed.sh` — resets to the parent commit, applies + commits
  `setup_patch.diff` as the baseline, then runs `task_checker.py` with the obstructed test +
  **authored** golden patches
- **Checks:** the obstructed test patch FAILs on baseline+setup and the authored
  `golden_patch_obstructed.diff` makes it PASS (dynamic FAIL→PASS — the execution signal that
  GATE 2's static validator cannot give; grades the authored golden, not an agent patch)
- **Writes:** `$DELIVERABLES/obstructed_after_stderr.log`
- **Result:** `OBSTRUCTED_PATCHES_OK` → `/hilbench-check1` ·
  `OBSTRUCTED_TEST_PATCH_APPLY_FAIL` / `OBSTRUCTED_GOLDEN_PATCH_APPLY_FAIL` /
  `OBSTRUCTED_SETUP_PATCH_APPLY_FAIL` (fix the diff) · `OBSTRUCTED_F2P_FAIL` (align
  golden/tests/spec; never weaken tests) · `OBSTRUCTED_CHECK_ERROR` · `CONTAINER_NOT_RUNNING` ·
  `REQUIRED_INPUT_FILE_MISSING`

---

## Stage 3 — Attempter Checks (container; scripted F2P; optional pre-screen, recommended)

> Optional fast, single-model smoke test before the required Stage 4 Harbor grade — catches
> obvious guessable/unsolvable tasks cheaply. Not a substitute for Stage 4 (a single-model
> local PASS can still fail Harbor's multi-model threshold).

### `/hilbench-check1  [--instance-id ID --container NAME]`  — pre-screen (guessability)
- **Surface:** chat + a terminal review pause
- **Reads:** `modified_*` specs ONLY (resolution-free); the container repo
- **Runs:** `scripts/prepare_check.sh check1` (clean baseline + scrub answer key) → FRESH
  subagent solve (`references/attempter-check1.md`, no resolutions/tests/golden) →
  `scripts/evaluate_check.sh check1` (`task_checker.py`)
- **Checks:** empirical **eval 7 Test B** — can a no-ask agent pass the blocker tests?
- **Writes:** `check1_agent_patch.diff` (+ `*_after_stderr.log`)
- **Review pause:** inspect `docker exec -w "$HILBENCH_REPO" "$HILBENCH_CONTAINER" git diff`
- **Result:** `CHECK_RESULT check1 PASS` (not guessable) → `/hilbench-check2` · `FAIL_GUESSABLE`
  (redesign the guessed blocker; overrides the static Test B verdict) ·
  `CHECK_ERROR_PATCH` / `CHECK_ERROR_ENV` · `CONTAINER_NOT_RUNNING`

### `/hilbench-check2  [--instance-id ID --container NAME]`  — pre-screen (solvability)
- **Surface:** chat + a terminal review pause
- **Reads:** `modified_*` specs PLUS the blocker **resolutions** (id + resolution text from
  `blocker_registry.md`); the container repo. NOT the tests/golden.
- **Runs:** `scripts/prepare_check.sh check2` → FRESH subagent solve
  (`references/attempter-check2.md`, with resolutions) → `scripts/evaluate_check.sh check2`
- **Checks:** with the answers known, do all relevant tests pass?
- **Writes:** `check2_agent_patch.diff` (+ `*_after_stderr.log`)
- **Result:** `CHECK_RESULT check2 PASS` (solvable) → proceed to the required Stage 4 grade ·
  `FAIL_UNSOLVABLE` (add minimal missing detail; regenerate golden; don't weaken tests) ·
  `CHECK_ERROR_PATCH` / `CHECK_ERROR_ENV`

---

## Stage 4 — Evaluation (**REQUIRED**; uses the bundled `input_validation` / `evaluate_task` skills)

> The authoritative grade, required for every task. Run `/hilbench-evaluate-full` first; if a
> check fails, fix the task and re-run — it is **incremental** (skips unchanged stages), or use
> `/hilbench-evaluate-check1/2` to force one check.

### `/hilbench-evaluate-full  [--fresh]`  — REQUIRED grade: input validation + full agentic checks (heaviest: 12 runs)
- **Runs:** `evaluate_task/eval_rerun.py plan` (decides which stages to run) → `input_validation`
  then `evaluate_task` for the stages marked RUN → `eval_rerun.py update` (records the manifest)
- **Incremental:** on a re-run, skips input validation / Check 1 / Check 2 whose dependency fields
  are unchanged since the last run and reuses their verdict. `--fresh` (or no manifest) forces a
  full re-grade — do one `--fresh` run as final sign-off. Dependency map:
  `references/evaluation-rerun.md`.
- **Reads/Writes:** manifest `$DELIVERABLES/harbor/eval_manifest.json` (hashes + per-stage verdicts)
- **Result:** `EVALUATION: PASS` only if BOTH Harbor checks pass, else `EVALUATION: FAIL` ·
  `REQUIRED_INPUT_FILE_MISSING`

### `/hilbench-evaluate-check1`  — re-check Check 1 only after a fix (heavy: 6 runs)
- **Runs:** delegates to `evaluate_task` — Harbor SWE-Agent on the BASELINE `instruction.md`
  (no `# BLOCKER DETAILS`), 3 models × 2 runs
- **Result:** `CHECK 1 (Harbor): PASS/FAIL` (PASS iff ≥2 models fail AND zero blockers guessed)

### `/hilbench-evaluate-check2`  — re-check Check 2 only after a fix (heavy: 6 runs)
- **Runs:** delegates to `evaluate_task` — Harbor on the FULL-INFO `instruction.md` (with
  `# BLOCKER DETAILS`), 3 models × 2 runs
- **Result:** `CHECK 2 (Harbor): PASS/FAIL` (PASS iff ≥2 models pass)

### `/hilbench-validate-input`  — standalone input validation (optional; `-full` runs it)
- **Runs:** delegates to the `input_validation` skill — repo builds at base commit, test files
  exist, test-list coverage, patches apply cleanly
- **Result:** `INPUT VALIDATION: PASS` (precondition for the agentic checks) · else early-exit with feedback

---

## Lifecycle

### `/hilbench-teardown  [--stop-only --container NAME]`
- **Runs:** `scripts/teardown.sh` — default stops AND removes the container; `--stop-only`
  stops but keeps it for a fast `docker start`. Never deletes deliverables; keeps the image.
- **Result:** `TEARDOWN_OK`. Re-run `/hilbench-provision` before any container-backed stage.

---

## Reviewer commands (reviewing a task that already has blockers)

For a REVIEWER reviewing a task that already has a blocker set, against its independent async
evaluation (see [`REVIEWER_GUIDE.md`](./REVIEWER_GUIDE.md)). Both are **read-only** — they reproduce
eval results and never modify artifacts. Mechanics/aliases/`failing_items.txt` convention live in
`references/reviewer-evals.md`; prompt bodies are the verbatim ones in `references/eval-prompts.md`.

### `/hilbench-run-evals  [--from review/failing_items.txt]`
- **Surface:** chat
- **Reads:** the completed task's `$DELIVERABLES` artifacts (+ `$TASK_FILES` originals); with
  `--from`, the flagged names in `failing_items.txt` (minimal convention: `<name>: VERDICT — reason`)
- **Runs:** the 12 model evals **verbatim** from `references/eval-prompts.md` — all 12 by default,
  or only the flagged ones with `--from`
- **Checks:** reproduces the static-eval portion of the async sheet (same judgments as the gates)
- **Writes:** nothing (read-only)
- **Result:** per eval `EVAL <name>: PASS|FAIL — <reason>`; summary `EVALS: <n> PASS / <m> FAIL` ·
  `UNRECOGNIZED_EVAL <token>` · `REQUIRED_INPUT_FILE_MISSING`. `check1`/`check2` lines are skipped
  (Harbor verdicts — reproduce with `/hilbench-evaluate-check2`).

### `/hilbench-run-eval  <eval_name>`
- **Surface:** chat
- **Reads:** the inputs for the one named eval (full `eval_*` or short alias)
- **Runs:** that single eval **verbatim** from `references/eval-prompts.md`
- **Checks:** fast single-flag reproduce/re-verify (e.g. after a targeted fix)
- **Writes:** nothing (read-only)
- **Result:** `EVAL <name>: PASS` | `EVAL <name>: FAIL — <reason>` (+ fix-location hint) ·
  `UNRECOGNIZED_EVAL <token>` · `REQUIRED_INPUT_FILE_MISSING`

---

## Where eval 7 lives (the only check that spans stages)

`eval_blocker_critical_implementation` (#7) is split across three commands:

1. `/hilbench-validate-registry` — Test B (non-guessable) + Test C (realistic); Test A UNVERIFIED
2. `/hilbench-validate-artifacts` — Test A (necessity), now that tests + golden exist
3. `/hilbench-check1` — decisive empirical confirmation of Test B (`FAIL_GUESSABLE` overrides)

See the "Eval 7 split" section of `references/eval-prompts.md`.
