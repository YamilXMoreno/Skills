---
description: Run every applicable static eval concurrently, wait for the complete failure set, draft and apply one conflict-checked repair batch, then ask whether to run the eval sweep again. Never loops automatically.
argument-hint: "[optional: --registry-only | --dry-run]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Resolve and print `$TASK_FILES` and
`$DELIVERABLES`. Require `blocker_registry.json`; with no `--registry-only`, also require all
completed-task inputs used by `/hilbench-run-evals`.

### Phase 1 — complete read-only diagnosis

- With `--registry-only`, run every Tier 1 registry eval and linter using the same parallel
  fan-out and prompts as `/hilbench-validate-registry`.
- Otherwise, run all 12 static model evals using the same parallel fan-out, scoped inputs,
  verbatim prompts, and output order as `/hilbench-run-evals`; also include the three registry
  linters and the artifact alignment/overlap checks.

Launch the entire applicable set before consuming results. If one worker reports a failure,
record it and continue waiting; NEVER edit an artifact, cancel sibling evals, or start a fix
while any eval is still running. A worker error is part of the final diagnostic report.

Persist the complete ordered result set with a UTC timestamp to
`$DELIVERABLES/repair/diagnostic_sweep.txt`. If everything passes, print
`REPAIR: NOT_NEEDED` and STOP.

### Phase 2 — one batched repair

After every eval has returned, group failures by source artifact and root cause. Collapse
duplicate symptoms into one fix and identify conflicts where two suggested fixes touch the same
behavior. Print the full repair plan before changing anything.

For independent artifact groups, launch one read-only repair-draft worker per group in one
parallel batch. Workers receive the complete diagnostic report but may propose changes only for
their assigned files. They MUST NOT edit files or run evals.

Wait for all repair drafts. The parent resolves overlaps and applies one serial, minimal,
conflict-checked batch. Preserve registry distribution, blocker independence, patch separation,
and all passing behavior. With `--dry-run`, print the batch without applying it.

Write the applied/dry-run summary and touched-file list to
`$DELIVERABLES/repair/repair_batch.txt`. Print `REPAIR BATCH APPLIED` or
`REPAIR BATCH DRY RUN`.

### Phase 3 — explicit rerun decision

Do NOT run any eval or gate automatically after applying fixes. Ask the contributor:
`The repair batch is complete. Run the full applicable eval sweep again now?`

- **Yes:** run the same complete Phase 1 sweep once, report all results, and STOP. Do not apply
  another repair automatically.
- **No:** print `EVAL RERUN: DECLINED` and STOP with the pending command
  (`/hilbench-validate-registry` for registry-only or `/hilbench-run-evals` for a completed task).

There is no automatic fix→eval→fix loop. Every additional repair cycle requires a new explicit
user request.
