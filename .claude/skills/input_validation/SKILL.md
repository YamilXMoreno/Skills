---
description: Validates that the inputs to creating a Harbor task are correct and execute without error. Use this skill when requested to do input validation or the user asks to check their task setup or environment setup.
---

## Overview
This process is to assess if the provided components to create a HiL-Bench task, based on a SWE-Bench Pro task, are sound. **Follow the evaluation steps exactly.** If any step fails, early exit from the evaluation and provide feedback to the user immediately.

Use the `hil_bench_agent_swe_input_validation.py` and `custom_eval.py` files as references for the exact logic in running these input validation steps, though keep in mind not every line of code there is necessary for this purpose.

## Resolve paths first (non-interactive)
Resolve these once and use them everywhere; print them. Do NOT pause to ask the user for anything that can be read from disk — only ask if a required file genuinely cannot be found.
- `$TASK_FILES` — the directory containing `task_info.txt`. Try in order: `$HILBENCH_TASK_FILES` (if it contains `task_info.txt`), `/app/task_files`, `/home/sandbox/task_files`, `./task_files`, `.`, then a shallow `find . /app /home/sandbox -maxdepth 3 -name task_info.txt`.
- `$DELIVERABLES` — the blocker-injection output directory. Try `$HILBENCH_DELIVERABLES`, else `<parent of $TASK_FILES>/deliverables`.
If `task_info.txt` cannot be found, early exit with `REQUIRED_INPUT_FILE_MISSING` and list the directories searched.

## Evaluation Steps
### 1) Resolve required fields
Resolve the fields below from disk (do NOT ask the user unless a file is genuinely missing); print where each was resolved from:
- Original instance ID, repo name, programming language, base image tag, base commit hash → `$TASK_FILES/task_info.txt`
- setup_patch.diff → `$DELIVERABLES/setup_patch.diff` if present (Scenario 2), else none
- golden_patch.diff → `$DELIVERABLES/golden_patch_obstructed.diff` if present (Scenario 2), else `$TASK_FILES/golden_patch.diff` (Scenario 1)
- test_patch.diff → `$DELIVERABLES/test_patch_obstructed.diff` if present (Scenario 2), else `$TASK_FILES/test_patch.diff` (Scenario 1)
- Relevant tests (tests_to_pass) → `$DELIVERABLES/relevant_tests.txt` if present, else the Relevant Tests list in `task_info.txt`

### 2) Repo validity
In the base image for the repo, check out the base commit hash then apply the setup_patch.diff (if exists). This is now the working repo state. If it fails, early exit and provide feedback. If the setup_patch.diff doesn't exist or is empty, just validate the base commit hash checkout step.

### 3) Test existence
Check that all test **FILES** listed in the relevant tests list (aka tests_to_pass list) either (a) already exist in the working repo state or (b) will be added by the provided test_patch.diff. **Make sure you handle different ways this specific programming language formats tests.** If any test files are missing from repo or patch, early exit and provide feedback.

### 4) Test list coverage
Check that all tests modified or added inside test_patch.diff are included in the relevant tests list. **Make sure you handle different ways this specific programming language formats tests.** If any test_patch test is missing from the list, early exit and provide feedback.

### 5) Golden and test patches application
In order, apply the golden_patch.diff then the test_patch.diff to the working repo state. Both must apply cleanly. If either fail, early exit and provide feedback.

## Output
If the user's provided task passes all of the above steps, they have passed input validation. Else, give them clear, actionable feedback on what to fix.