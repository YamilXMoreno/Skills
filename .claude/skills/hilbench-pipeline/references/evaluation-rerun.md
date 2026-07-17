# Incremental re-run spec — `/hilbench-evaluate-full`

Authoritative spec for how the REQUIRED Harbor grade decides which stages to re-run after a fix.
Implemented by `skills/evaluate_task/eval_rerun.py` and wired into `evaluate_task/SKILL.md`
(step 1.5 = plan, step 8 = update). This document is the source of truth for the dependency map
and the algorithm; the script and skill must match it.

## Goal

The grade runs three stages — **input validation**, **Check 1** (baseline / guessability), and
**Check 2** (full-info / solvability) — for up to **12 SWE-Agent runs**. After the first run, most
edits touch only a subset of the task's artifacts. A re-run should re-execute **only the stages a
change can actually affect**, and reuse the previous verdict for the rest.

`/hilbench-evaluate-full` is incremental by default. `--fresh` (or no prior manifest) forces a full
re-grade.

## The core rule

> Skip a stage (reuse its previous verdict) **iff** a manifest exists, the stage has a recorded
> PASS/FAIL verdict, and **none of that stage's dependency fields changed** since the manifest was written.
> Otherwise, run the stage.

- "Changed" = the content hash of the field differs from the hash stored in the manifest.
- The reused verdict may be **PASS or FAIL** — identical inputs give the same result, so re-running
  an unchanged stage is pointless either way. (A reused FAIL just means "you haven't yet changed
  anything that would fix it.")
- **INCOMPLETE is never reused.** It means fewer than two Harbor model lanes completed because of
  dead/unavailable models or infrastructure, so the stage runs again on the next invocation.
- Overall grade = **PASS only if all three stages' (reused-or-fresh) verdicts are PASS**.

## Dependency map

Re-run the stage if any ✅ field changed:

| Field changed | Input validation | Check 1 | Check 2 |
|---|:---:|:---:|:---:|
| setup patch | ✅ | ✅ | ✅ |
| test patch | ✅ | ✅ | ✅ |
| golden patch | ✅ | ✅ | ➖ |
| relevant tests | ✅ | ➖ | ➖ |
| problem statement | ➖ | ✅ | ✅ |
| requirements | ➖ | ✅ | ✅ |
| interfaces | ➖ | ✅ | ✅ |
| blocker description + resolution | ➖ | ➖ | ✅ |
| blocker id / trigger questions | ➖ | ➖ | ➖ |

### Why each dependency exists

A stage's outcome is driven by three things: what the **image / `test.sh`** contains, what the
**agent sees** in `instruction.md`, and what the **LLM judges** read. The map follows from those.

- **Input validation** is purely structural: it checks out the base commit, applies the setup
  patch, confirms the test files exist / are covered, and applies the golden + test patches
  cleanly. So it depends on exactly setup / test / golden patches + the relevant-tests list.
- **Check 1** (baseline instruction, no blocker details):
  - problem statement / requirements / interfaces → they *are* the agent's instruction.
  - setup patch → the image the agent starts from.
  - test patch → builds the graded `test.sh` (a run "fails" if it doesn't pass the tests).
  - **golden patch → the guessed-blocker judge** (`identify_blockers_guessed_swe.jinja2`) uses the
    golden as its reference solution to decide whether the agent independently arrived at the
    intended resolution. Even though the golden is never in the agent's image, editing it can flip
    a "guessed" verdict, which is half of Check 1's pass criteria.
- **Check 2** (full-info instruction, adds `# BLOCKER DETAILS`):
  - everything Check 1's instruction/image/test.sh needs, **minus golden** — Check 2 has no
    guessed-blocker criterion and its judge (`classify_trajectory_technically_passed_swe.jinja2`)
    reads only the trajectory summary, never the golden.
  - **plus blocker description + resolution** — these are the `# BLOCKER DETAILS` text shown to the
    agent.

### Two deliberate design decisions (not oversights)

1. **relevant tests is NOT a Check 1/Check 2 dependency.** Mechanically, the relevant-tests list
   defines the graded `tests_to_pass` and *could* change a check's pass/fail independently of the
   test patch. We exclude it from the checks on the **project invariant that `relevant_tests.txt`
   is only ever edited alongside `test_patch.diff`**. It remains an input-validation dependency. If
   that invariant is ever broken (e.g. adding a pre-existing repo test to the list without touching
   the patch), run `--fresh`.
2. **description + resolution is NOT a Check 1 dependency**, even though the guessed-blocker judge
   reads them. Rationale: a *semantic* resolution change requires the golden patch to change too
   (GATE 2 / `/hilbench-validate-obstructed` enforce golden↔resolution alignment), and **golden is
   already a Check 1 dependency** — so meaningful changes re-trigger Check 1 via the golden. A
   *cosmetic* resolution reword leaves the golden unchanged and shouldn't move the judge's verdict.
   Editing a blocker `id` or `trigger_questions` affects no stage (they appear in neither the
   instruction nor any judge that gates a verdict).

## Registry hashing (field-level, not file-level)

`blocker_registry.json` is **not** hashed as raw bytes. `eval_rerun.py` hashes a normalized
projection: the list of `(description, resolution)` pairs, each stripped, **sorted by content**
(order-independent), then serialized and hashed. Consequences:

- Editing a `description` or `resolution` → hash changes → Check 2 re-runs. ✅
- Renaming an `id`, reordering blockers, or editing `trigger_questions` → hash unchanged → no
  re-run. ✅
- An unparseable registry falls back to a raw-bytes hash (fail-safe: any edit re-runs the check).

Every other field is hashed as raw file bytes; a missing file hashes to the sentinel `ABSENT`.

## Field → file resolution (scenario-aware)

| Field | Scenario 2 path | Scenario 1 path |
|---|---|---|
| setup patch | `$DELIVERABLES/setup_patch.diff` | (same; often absent) |
| test patch | `$DELIVERABLES/test_patch_obstructed.diff` | `$TASK_FILES/test_patch.diff` |
| golden patch | `$DELIVERABLES/golden_patch_obstructed.diff` | `$TASK_FILES/golden_patch.diff` |
| relevant tests | `$DELIVERABLES/relevant_tests.txt` | (same) |
| problem statement | `$DELIVERABLES/modified_problem_statement.txt` | (same) |
| requirements | `$DELIVERABLES/modified_requirements.txt` | (same) |
| interfaces | `$DELIVERABLES/modified_public_interfaces.txt` | (same) |
| registry | `$DELIVERABLES/blocker_registry.json` | (same) |

Scenario is taken from `--scenario`, else auto-detected (obstructed patches present → Scenario 2).

## Manifest

Location: `$DELIVERABLES/harbor/eval_manifest.json`. Written by `eval_rerun.py update`; **do not
hand-edit**. Shape:

```json
{
  "version": 2,
  "updated_utc": "2026-07-04T15:00:00Z",
  "scenario": 2,
  "hashes": { "setup_patch": "…", "test_patch": "…", "…": "…", "registry": "…" },
  "verdicts": { "input_validation": "PASS", "check1": "PASS", "check2": "INCOMPLETE" }
}
```

### Two invariants that keep skips correct

1. **Refresh the full snapshot every run.** `update` rewrites hashes for *all* fields, including
   those belonging to skipped stages. This makes "the manifest" always mean "the most recent state,"
   so the comparison is always current-vs-last-run — never anchored to a stale older run. (A stage
   skipped across several runs stays valid because its fields never differed across any of those
   refreshes.)
2. **Carry verdicts forward.** On a skip, pass the reused verdict back into `update` so it persists.
   This is why the model tracks *state*, not pass/fail history: there is no "last passing run" to
   privilege — only the last recorded state and its verdicts.

## Algorithm

**Plan** (`eval_rerun.py plan`, step 1.5):
1. Compute current hashes for all fields.
2. If `--fresh` or no manifest → every stage `RUN`.
3. Else, per stage: if it has no recorded verdict or its verdict is `INCOMPLETE` → `RUN`; if any
   dependency field differs from the manifest → `RUN (changed=…)`; otherwise →
   `SKIP (reuse=<verdict>)`.
4. Emit rebuild hints: `image_rebuild` = setup patch changed (or first run); `test_sh_rebuild` =
   test patch or relevant tests changed. (Always build the image anyway if it's absent in the
   session — Docker images don't persist across sandboxes.)

**Execute** (SKILL.md steps 2–7): run the `RUN` stages; for `SKIP` stages reuse the printed verdict
and the recorded feedback in `harbor/evaluation_feedback.md`.

**Update** (`eval_rerun.py update`, step 8): rewrite the manifest with current hashes + the per-stage
verdicts (reused verdicts included). Omit a stage flag only if it has no verdict at all (e.g. checks
that never ran because input validation failed).

## Commands / overrides

- `/hilbench-evaluate-full` — incremental by default.
- `/hilbench-evaluate-full --fresh` — force a full re-grade. **Do this once as final sign-off**
  before a task is considered done: the skip logic is only as sound as this dependency map, so a
  clean full pass is the safety net.
- `/hilbench-evaluate-check1` / `/hilbench-evaluate-check2` — explicit single-check force, bypassing
  the planner entirely (use when you know exactly which check you want to re-run).

## The one invariant everything rests on

**Reusing a verdict is safe only if the dependency map is complete.** If a stage depends on
something not listed above, the mechanism could wrongly skip it. Any change to what an
`instruction.md` or an LLM judge reads MUST be reflected in both this table and `eval_rerun.py`.
When in doubt, `--fresh`.
