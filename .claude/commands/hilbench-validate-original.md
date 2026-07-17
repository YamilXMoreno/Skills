---
description: Stage 0.5 - validate the ORIGINAL patches on the verified parent commit, parallelizing independent preflight checks before the ordered FAIL->PASS run. Confirms the original test + golden patches apply and show a clean FAIL->PASS before any blocker injection. STOPS for review.
argument-hint: "[optional: --instance-id ID --tests-file PATH --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Run this right after
`/hilbench-provision` and before any authoring. It is the "Validate the Original Patches"
step from the Codebase Editing Workflow: prove the baseline SWE task is sound so you do not
build blockers on top of a broken baseline.

Precondition: `/hilbench-provision` succeeded (container running, parent commit verified). If
the container is not running, STOP with `CONTAINER_NOT_RUNNING`.

Resolve `$TASK_FILES` / `$DELIVERABLES` first (print both).

### Step 1 — parallel preflight

Launch two NEW, isolated workers in one parallel batch:

- **Relevant-tests worker:** For the full FAIL->PASS (F2P) check, `task_checker.py` needs a
  JSON array of the tests in the ORIGINAL test patch. If
  `$TASK_FILES/relevant_tests_original.txt` does not already exist, create it by reading
  `$TASK_FILES/test_patch.diff` and writing a JSON array of ALL test identifiers (same rules
  as `references/06-relevant-tests.md`: exact strings, discoverable verbatim, no renaming).
  This is the only worker allowed to write, and it may write only
  `$TASK_FILES/relevant_tests_original.txt`.
- **Environment worker:** Read-only check that the container is running, the verified parent
  commit is resolvable, both original patches exist, and `task_checker.py` plus the instance id
  are available for a full F2P run. Run `scripts/validate_patch_artifacts.py --patch <test>
  --patch <golden>` and require LF-only valid unified diffs. Do not reset or mutate the repo.

Each worker returns `ORIGINAL PREFLIGHT <name>: PASS` or
`ORIGINAL PREFLIGHT <name>: FAIL — <reason>`. Wait for both and aggregate in the parent. Do not
repeat their analysis. STOP on failure before running the stateful validation script.

### Step 2 — run the ordered validation process

```bash
bash scripts/validate_original.sh $ARGUMENTS
```

Run this as one isolated process after both preflight workers pass. The internal reset,
test-patch application, baseline-failure test, golden-patch application, and final-pass test
are state-dependent and MUST remain ordered. Never run multiple copies against the same repo
or container concurrently.

- With a tests file + instance id: runs `task_checker.py` on the ORIGINAL test + golden
  patches (expects the test patch to FAIL, the golden patch to make it PASS).
- Without them: falls back to confirming both original patches apply cleanly on the parent
  commit, and tells you to pass `--tests-file` / `--instance-id` for the full F2P.

### Interpret and STOP

- `ORIGINAL_PATCHES_OK` (F2P) or `ORIGINAL_APPLY_OK` (apply-only) → baseline is sound; print
  `STAGE validate-original: DONE` and proceed to authoring (`/hilbench-registry-generate` or
  `/hilbench-plan`).
- `ORIGINAL_TEST_PATCH_APPLY_FAIL` / `ORIGINAL_GOLDEN_PATCH_APPLY_FAIL` → the originals do not
  apply on this commit. Most likely a wrong baseline (re-check `/hilbench-provision`,
  possibly `--checkout`) or corrupted input patches. Do NOT start injection.
- `ORIGINAL_F2P_FAIL` → the originals do not show a clean FAIL->PASS for the given tests.
  Re-check the tests list you built and the baseline. Do NOT start injection until the
  original task is verified sound.
- `ORIGINAL_CHECK_ERROR` → runner/parser/env issue (download, parse, timeout); fix and re-run.

This is a review gate: never auto-advance to authoring.
