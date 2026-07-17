---
description: Run the optional Check 1 guessability and Check 2 solvability prescreens concurrently in isolated container worktrees, pause for review of both agent patches, then evaluate both concurrently. This is not the required Harbor grade.
argument-hint: "[optional: --instance-id ID --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This command is the fastest local
prescreen: it runs one Check 1 attempt and one Check 2 attempt at the same time. It is optional
and does NOT replace `/hilbench-evaluate-full` or either Harbor grade.

Preconditions: `/hilbench-validate-obstructed` passed, the provisioned container is running,
and the injection artifacts exist in `$DELIVERABLES`. Resolve and print `$TASK_FILES`,
`$DELIVERABLES`, `$HILBENCH_CONTAINER`, `$HILBENCH_REPO`, and the parent commit. STOP on a
missing input or stopped container.

### Step 1 — Prepare one clean baseline

```bash
bash scripts/prepare_check.sh check1 $ARGUMENTS
```

Require `CHECK_PREPARED check1`. Read the prepared commit from
`$DELIVERABLES/.hilbench_check_baseline`.

Create two uniquely named detached git worktrees inside the container, outside
`$HILBENCH_REPO`, both at that exact prepared commit: one for Check 1 and one for Check 2.
Record their paths as `$CHECK1_REPO` and `$CHECK2_REPO`. Never run either solve in the shared
`$HILBENCH_REPO`. The detached worktrees must not contain test/golden patches, relevant-tests
files, or blocker registries.

### Step 2 — Parallel solve fan-out

Launch exactly two NEW subagents with CLEAN contexts in one parallel batch:

- **Check 1 worker:** follow `references/attempter-check1.md`. Give it only the three
  `modified_*` artifacts. Do not provide tests, patches, registry data, or resolutions.
  It may edit source files only inside `$CHECK1_REPO`.
- **Check 2 worker:** follow `references/attempter-check2.md`. Give it only the three
  `modified_*` artifacts plus blocker `id + resolution` text extracted in the parent from
  `$DELIVERABLES/blocker_registry.md`. Do not provide trigger questions, metadata, tests, or
  patches. It may edit source files only inside `$CHECK2_REPO`.

The workers MUST NOT access each other's worktree or output, launch another check, commit,
or edit test files. Wait for both workers. Capture each complete worktree diff into:

- `$DELIVERABLES/check1_agent_patch.diff`
- `$DELIVERABLES/check2_agent_patch.diff`

### Review pause (hard)

STOP. Show both labeled diffs to the contributor. Do not evaluate either patch until the
contributor explicitly resumes after reviewing both.

### Step 3 — Parallel evaluation fan-out

After approval, launch both commands concurrently as separate processes:

```bash
bash scripts/evaluate_check.sh check1 --repo "$CHECK1_REPO" --agent-patch "$DELIVERABLES/check1_agent_patch.diff" $ARGUMENTS
bash scripts/evaluate_check.sh check2 --repo "$CHECK2_REPO" --agent-patch "$DELIVERABLES/check2_agent_patch.diff" $ARGUMENTS
```

Each evaluator must operate only in its assigned worktree. Wait for both and print both
`CHECK_RESULT` lines in Check 1, then Check 2 order, regardless of completion order.

- Check 1 keeps its existing semantics: `PASS` means the blockers were not guessed;
  `FAIL_GUESSABLE` means they were.
- Check 2 keeps its existing semantics: `PASS` means the task was solved with resolutions;
  `FAIL_UNSOLVABLE` means it was not.
- A `CHECK_ERROR_*` affects only that check; preserve and report the other result.

Remove both temporary worktrees only after their patches, logs, and results are safely stored
under `$DELIVERABLES`. Then print `PRESCREEN: PASS` only when both checks returned `PASS`;
otherwise print `PRESCREEN: FAIL - <check + verdict>`. STOP. Never auto-run Harbor grading.
