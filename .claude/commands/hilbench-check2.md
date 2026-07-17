---
description: Attempter Check 2 (solvability) - prepare a clean container baseline, run a FRESH subagent that solves WITH the blocker resolutions (still no tests), pause for review, then score with task_checker.py. PASS means the task is solvable with resolutions.
argument-hint: "[optional: --instance-id ID --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Check 2 confirms the task is
SOLVABLE once the blocker resolutions are known. It is an **optional but recommended** fast,
single-model pre-screen before the required Stage 4 Harbor grade; it is NOT a substitute for
that grade. Three steps; STOPS for review before scoring.

For the fastest combined prescreen, use `/hilbench-prescreen`; it runs the Check 1 solve and
this Check 2 solve concurrently in separate worktrees. Continue below only when running Check 2
by itself.

Preconditions: the container is running and `$DELIVERABLES` holds the
injection artifacts plus `blocker_registry.md`. If the container is not running, STOP with
`CONTAINER_NOT_RUNNING`.

### Step 1 — Prepare (scripted)

```bash
bash scripts/prepare_check.sh check2 $ARGUMENTS
```

Resets the repo to a clean committed baseline and scrubs the test patch, golden patch, and
relevant-tests list. Confirm `CHECK_PREPARED check2`.

### Step 2 — Fresh subagent solve (WITH resolutions)

Launch a NEW subagent with a CLEAN context. Give it ONLY the prompt body and scoped inputs
in `references/attempter-check2.md`: the three `modified_*` artifacts PLUS the blocker
RESOLUTIONS (id + resolution text only — extract from `$DELIVERABLES/blocker_registry.md`;
do NOT include trigger questions or other metadata). Do NOT pass the test patch, golden
patch, or `relevant_tests.txt`. It edits the real repo via `docker exec` under
`$HILBENCH_REPO` (source files only; no test files; no commits).

### Review pause (hard)

STOP. Show the contributor the captured agent changes
(`docker exec "$HILBENCH_CONTAINER" git -C "$HILBENCH_REPO" diff`) before scoring.

### Step 3 — Evaluate (scripted)

```bash
bash scripts/evaluate_check.sh check2 $ARGUMENTS
```

Report the `CHECK_RESULT check2 <verdict>` line and STOP:
- `PASS` → task solvable with resolutions; proceed to the required Stage 4 grade
  (`/hilbench-evaluate-full`).
- `FAIL_UNSOLVABLE` → some relevant tests fail even with resolutions. The spec is
  underspecified or the golden/tests are misaligned. Add the minimal missing detail to the
  correct artifact (PS / requirements / interface / the relevant resolution) and
  regenerate the golden patch — do NOT weaken the tests. See `references/optional-repair.md`.
- `CHECK_ERROR_PATCH` / `CHECK_ERROR_ENV` → fix the diff/env and re-run Step 3.
