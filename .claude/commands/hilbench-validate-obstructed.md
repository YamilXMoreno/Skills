---
description: Stage 2.5 - dynamic FAIL->PASS on the AUTHORED obstructed patches, with independent preflight checks run in parallel before the ordered checker. Bakes setup_patch as the baseline, then confirms the obstructed test fails on baseline+setup and the authored golden makes it pass. STOPS for review.
argument-hint: "[optional: --instance-id ID --golden-patch PATH --tests-file PATH --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Run this right after
`/hilbench-validate-artifacts` (GATE 2) and before the Attempter Checks. It is the
dynamic-execution counterpart to `/hilbench-validate-original`, but for the post-injection
artifacts and the **authored** obstructed golden: it proves your `golden_patch_obstructed.diff`
actually executes to a clean FAIL->PASS against `test_patch_obstructed.diff`.

Why it exists: GATE 2 (`/hilbench-validate-artifacts`) validates the authored golden only
**statically** (an LLM reads the diffs). The Attempter Checks run the runner, but they grade an
**agent-generated** patch, not your authored golden. This command closes that gap — it applies
setup, then your golden, then runs the relevant tests in the container.

Precondition: `/hilbench-provision` succeeded (container running). If the container is not
running, STOP with `CONTAINER_NOT_RUNNING`. Requires in `$DELIVERABLES`:
`golden_patch_obstructed.diff`, `test_patch_obstructed.diff`, `relevant_tests.txt` (and
`setup_patch.diff` if the task has one). If any required file is missing, STOP with
`REQUIRED_INPUT_FILE_MISSING`.

Resolve `$TASK_FILES` / `$DELIVERABLES` first (print both).

### Parallel preflight (mandatory)

Launch three NEW, isolated, read-only workers in one parallel batch:

- **Environment worker:** verify the container is running and the parent commit, instance id,
  repo path, and checker are resolvable. Do not reset or mutate the repo.
- **Artifact worker:** verify the required test patch, golden patch, relevant-tests file, and
  optional setup patch exist and are readable; validate their basic diff/file syntax without
  applying them. Run `scripts/validate_patch_artifacts.py` and require LF-only valid unified
  diffs; CRLF, malformed/duplicate blocks, unsafe paths, prose/fences, or invalid test-list JSON
  fail immediately.
- **Test-list worker:** validate the relevant-tests JSON shape and confirm its identifiers are
  discoverable in the test patch using the language-specific conventions. Compare against the
  original test patch and `blocker_test_map.txt`: preserve original task tests, but fail any test
  introduced during blocker authoring that maps to no blocker. Do not execute tests.

Give each worker only the inputs required for its check. Each returns
`OBSTRUCTED PREFLIGHT <name>: PASS` or
`OBSTRUCTED PREFLIGHT <name>: FAIL — <actionable reason>`. Workers MUST NOT edit files, launch
other checks, or receive another worker's output. Wait for all workers and aggregate results in
the parent; do not repeat their analysis. STOP before dynamic execution if any worker fails or
returns a malformed result.

### Run the ordered validation process

```bash
bash scripts/validate_obstructed.sh $ARGUMENTS
```

Run this as one isolated process after all preflight workers pass. Resetting the repo, applying
and committing setup, checking the expected baseline failure, applying the authored golden,
and checking the final pass are state-dependent and MUST remain ordered. Never run multiple
copies against the same repo or container concurrently.

Resets the repo to the parent commit, applies + commits `setup_patch.diff` as the baseline
(if present), then runs `task_checker.py` with the obstructed test + golden patches. Persists
the checker log to `$DELIVERABLES/obstructed_after_stderr.log` and the verdict (with the
run context) to `$DELIVERABLES/validate_obstructed_result.txt`.

### Interpret and STOP

- `OBSTRUCTED_PATCHES_OK` → the authored obstructed task is sound (test fails on baseline+setup,
  golden makes it pass); print `STAGE validate-obstructed: DONE` and proceed to `/hilbench-check1`.
- `OBSTRUCTED_TEST_PATCH_APPLY_FAIL` / `OBSTRUCTED_GOLDEN_PATCH_APPLY_FAIL` /
  `OBSTRUCTED_SETUP_PATCH_APPLY_FAIL` → a diff does not apply on the baseline. Regenerate the
  offending `*.diff` (see `references/07-patch-outputs.md`) and re-run.
- `OBSTRUCTED_F2P_FAIL` → the authored golden does not make the relevant tests pass (or a test
  does not fail without it). Align the golden / tests / spec — prefer fixing the golden patch or
  adding the missing enforcing test; do NOT weaken tests. See `references/optional-repair.md`,
  then re-run GATE 2 and this check.
- `OBSTRUCTED_CHECK_ERROR` → runner/parser/env issue (download, parse, timeout); fix and re-run.

This is a review gate: never auto-advance to the Attempter Checks.
