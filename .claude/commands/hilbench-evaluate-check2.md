---
description: Check 2 only (heavy; for targeted re-checks after a fix). Harbor SWE-Agent full-info grading (instruction.md includes # BLOCKER DETAILS) - build the image + full-info task, run 3 models x 2 runs, apply the Check 2 pass criteria. Delegates to the evaluate_task skill. Assumes input validation already passed.
argument-hint: "[optional: models, instance-id, or path overrides]"
---

Use the `hilbench-pipeline` skill for conventions, and delegate to the bundled `evaluate_task`
skill (read `evaluate_task/SKILL.md`, which ships with this package at
`~/.claude/skills/evaluate_task/`).

Scope: authoritative Harbor grading, HEAVY (6 SWE-Agent runs). Use this to re-check Check 2 on
its own after a fix, instead of re-running the whole `/hilbench-evaluate-full`. Separate from
and stronger than the optional fast pre-screen `/hilbench-check2`.

Precondition: input validation (`/hilbench-validate-input`) has passed — SKIP input validation here.

Resolve `$TASK_FILES` / `$DELIVERABLES` and all inputs from disk exactly as
`evaluate_task/SKILL.md` step 1 describes. $ARGUMENTS may override the model list.

Run ONLY the Check 2 path from `evaluate_task/SKILL.md`:
- Step 3 (image build) and Step 4 (task setup) for the FULL-INFO task `instruction.md`
  (problem statement + requirements + public interfaces PLUS a `# BLOCKER DETAILS` section
  with each blocker description + resolution).
- Generate one JobConfig per model and run the three model lanes concurrently through
  `evaluate_task/run_harbor_models.py` (2 attempts per lane). Wait for all lanes; one model's
  auth/provider/model failure marks only that model DEAD and never cancels its siblings.
- Apply Step 6 criteria: Check 2 PASSES iff at least 2 models pass (a model passes if any run
  actually passes or "technically passes"). DEAD models do not count as pass or fail. If fewer
  than two model lanes complete, report `CHECK 2 (Harbor): INCOMPLETE`.

Print `CHECK 2 (Harbor): PASS/FAIL/INCOMPLETE` with all three model states and per-component
feedback (summaries of failing trajectories + their patches on failure). STOP.
