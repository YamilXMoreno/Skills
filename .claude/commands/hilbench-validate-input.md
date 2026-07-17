---
description: Input validation (optional standalone; also run as the first step of /hilbench-evaluate-full) - run independent structural checks in parallel isolated workers (repo state, test files, test-list coverage, patch application). Delegates check logic to the input_validation skill. Non-interactive.
argument-hint: "[optional: instance-id or path overrides]"
---

Use the `hilbench-pipeline` skill for path/stage conventions, and delegate the actual work
to the bundled `input_validation` skill (read `input_validation/SKILL.md`, which ships with
this package at `~/.claude/skills/input_validation/`, and follow it exactly).

This is distinct from the fast container Attempter Checks (`/hilbench-check1/2`): input validation
is structural validation that the obstructed task is buildable/applies cleanly, a precondition
for the heavier Harbor agentic checks.

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Resolve inputs from disk (only
ask if a required file is genuinely missing):
- instance id, repo, language, base image tag, base commit → `$TASK_FILES/task_info.txt`
- patches: Scenario 2 → `$DELIVERABLES/{test_patch_obstructed,golden_patch_obstructed}.diff`
  (+ `setup_patch.diff` if present); Scenario 1 → `$TASK_FILES/{test_patch,golden_patch}.diff`
- relevant tests → `$DELIVERABLES/relevant_tests.txt`

$ARGUMENTS may supply an instance-id or path overrides.

Use the exact checks and verdict semantics from `input_validation/SKILL.md`, but schedule them
as follows:

1. Resolve all required fields once in the parent. If this fails, STOP immediately.
2. Prepare the working repo state (base commit plus `setup_patch.diff`, if present) once. This
   remains serial because every later check depends on the same prepared baseline.
3. **Parallel fan-out (mandatory).** Launch three NEW, isolated workers in one parallel batch:
   - **Test-existence worker:** perform skill step 3.
   - **Test-list-coverage worker:** perform skill step 4.
   - **Patch-application worker:** perform skill step 5 in its own temporary checkout/worktree
     cloned from the prepared baseline. It MUST NOT mutate the shared working repo.

Give each worker only the resolved inputs required for its check. Workers MUST NOT edit source
artifacts, launch other checks, or receive another worker's result. Each returns
`INPUT CHECK <name>: PASS` or `INPUT CHECK <name>: FAIL — <actionable reason>`.

Wait for every worker and aggregate results in skill-step order. The parent MUST NOT repeat a
worker's analysis. Any failed or malformed worker result fails input validation, but does not
cancel or discard the other workers' diagnostics. On success print `INPUT VALIDATION: PASS`,
then STOP. Do NOT auto-run the agentic checks.
