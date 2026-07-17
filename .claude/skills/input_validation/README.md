# input_validation (bundled worker skill)

Structural, non-interactive validation that a HiL-Bench obstructed task is well-formed and
buildable **before** the expensive Harbor agentic grade runs. It does not solve or grade the
task — it only confirms the inputs are sound.

## How it's invoked

You normally don't run this skill directly. It is driven by the pipeline:

- `/hilbench-validate-input` — runs it standalone as a precondition check.
- `/hilbench-evaluate-full` — runs it automatically as step 1 (the Harbor checks proceed only
  if it passes).

The `evaluate_task` skill also references it as its step 2.

## What it checks (in order, early-exits on first failure)

1. **Repo validity** — check out the base commit and apply `setup_patch.diff` (if any); the
   working repo state must build.
2. **Test existence** — every test file in `relevant_tests.txt` either already exists in the
   repo or is added by `test_patch_obstructed.diff`.
3. **Test-list coverage** — every test added/modified by the test patch appears in
   `relevant_tests.txt`.
4. **Patch application** — the golden patch then the test patch both apply cleanly.

Passing prints `INPUT VALIDATION: PASS`; any failure gives actionable feedback and stops.

## Contents

| File | Role |
|------|------|
| `SKILL.md` | The entry point the agent follows (authoritative steps) |
| `hil_bench_agent_swe_input_validation.py` | Reference implementation of the validation logic |
| `custom_eval.py` | Shared evaluation helpers referenced by the steps |

See `SKILL.md` for the exact, authoritative procedure.
