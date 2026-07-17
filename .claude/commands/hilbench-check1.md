---
description: Attempter Check 1 (guessability) - prepare a clean container baseline, run a FRESH subagent that solves WITHOUT resolutions/tests, pause for review of the agent patch, then score with task_checker.py. PASS means blockers are not guessable.
argument-hint: "[optional: --instance-id ID --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Check 1 confirms the blockers
are NOT reliably inferable. It is an **optional but recommended** fast, single-model
pre-screen — run it to catch an obviously guessable task cheaply before spending the required
Stage 4 Harbor grade; it is NOT a substitute for that grade. It has three steps and STOPS for
review before scoring.

For the fastest combined prescreen, use `/hilbench-prescreen`; it runs this Check 1 solve and
the Check 2 solve concurrently in separate worktrees. Continue below only when running Check 1
by itself.

Preconditions: `/hilbench-provision` succeeded (container up, parent commit verified) and
the injection artifacts exist in `$DELIVERABLES` (`modified_*`, and for Scenario 2 the
obstructed patches + `relevant_tests.txt`). If the container is not running, STOP with
`CONTAINER_NOT_RUNNING`.

### Step 1 — Prepare (scripted)

```bash
bash scripts/prepare_check.sh check1 $ARGUMENTS
```

This resets the repo to a clean committed baseline (parent + `setup_patch.diff` if any)
and SCRUBS every answer-key file (test/golden patches, relevant tests, registry) from the
container. Confirm it prints `CHECK_PREPARED check1`.

### Step 2 — Fresh subagent solve (resolution-FREE)

Launch a NEW subagent with a CLEAN context (no memory of authoring). Give it ONLY the
prompt body and scoped inputs in `references/attempter-check1.md`:
`modified_problem_statement.txt`, `modified_requirements.txt`,
`modified_public_interfaces.txt`. Do NOT pass the test patch, golden patch,
`relevant_tests.txt`, blocker registry, or any resolution. Instruct it to edit the real
repo inside the container via `docker exec "$HILBENCH_CONTAINER" ...` under
`$HILBENCH_REPO` (source files only; no test files; no commits).

### Review pause (hard)

STOP. Show the contributor the captured agent changes
(`docker exec "$HILBENCH_CONTAINER" git -C "$HILBENCH_REPO" diff`). Let them inspect
before scoring. Do not auto-advance.

### Step 3 — Evaluate (scripted)

```bash
bash scripts/evaluate_check.sh check1 $ARGUMENTS
```

Report the `CHECK_RESULT check1 <verdict>` line and STOP:
- `PASS` → blockers not guessable; proceed to `/hilbench-check2`.
- `FAIL_GUESSABLE` → a blocker was solved without resolutions. Identify which blocker(s)
  the agent's patch satisfied, then classify WHY using the four-reason root-cause taxonomy in
  `references/optional-repair.md` (contamination / too few alternatives / blocker not critical /
  test not critical) and apply the matching fix. Re-author and re-run Check 1.
- `CHECK_ERROR_PATCH` / `CHECK_ERROR_ENV` → not a blocker-design signal; fix the diff/env
  and re-run Step 3.
