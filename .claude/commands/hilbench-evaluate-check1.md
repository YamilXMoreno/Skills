---
description: Check 1 only (heavy; for targeted re-checks after a fix). Harbor SWE-Agent baseline grading (no blocker details) - build the image + baseline task, run 3 models x 2 runs, apply the Check 1 pass criteria. Delegates to the evaluate_task skill. Assumes input validation already passed.
argument-hint: "[optional: models, instance-id, or path overrides]"
---

Use the `hilbench-pipeline` skill for conventions, and delegate to the bundled `evaluate_task`
skill (read `evaluate_task/SKILL.md`, which ships with this package at
`~/.claude/skills/evaluate_task/`).

Scope: AUTHORITATIVE Harbor grading, HEAVY (6 SWE-Agent runs). Use this to re-check Check 1 on
its own after a fix, instead of re-running the whole `/hilbench-evaluate-full`. It is separate
from and stronger than the optional fast pre-screen `/hilbench-check1`.

Precondition: input validation (`/hilbench-validate-input`) has passed — SKIP input validation here.

Resolve `$TASK_FILES` / `$DELIVERABLES` and all inputs from disk exactly as
`evaluate_task/SKILL.md` step 1 describes (prefer the `$DELIVERABLES` obstructed artifacts).
$ARGUMENTS may override the model list.

Run ONLY the Check 1 path from `evaluate_task/SKILL.md`:
- Step 3 (image build) and Step 4 (task setup) for the BASELINE task `instruction.md`
  (problem statement + requirements + public interfaces; NO `# BLOCKER DETAILS`).
- Generate one JobConfig per model and run the three model lanes concurrently through
  `evaluate_task/run_harbor_models.py` (2 attempts per lane). Wait for all lanes; one model's
  auth/provider/model failure marks only that model DEAD and never cancels its siblings.
- Apply Step 5 criteria: Check 1 PASSES iff (a) at least 2 models FAIL the task AND (b) zero
  blockers are guessed. DEAD models do not count as task failures. If fewer than two model
  lanes complete, report `CHECK 1 (Harbor): INCOMPLETE`.

Print `CHECK 1 (Harbor): PASS/FAIL/INCOMPLETE` with all three model states and per-component
feedback (summaries of any passing trajectories + their patches; any guessed blockers and which
models/runs guessed them). STOP.
