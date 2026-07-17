---
description: Authoring step 01 - decide Scenario 1 vs 2, lock the blocker config/distribution, and write plan.md (blocker ids + intended types + verification anchors). STOPS for review. Does not write the registry yet.
argument-hint: "[optional: distribution override]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is step 01 of the numbered
authoring path (followed by `/hilbench-registry`).

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Inputs:
`$TASK_FILES/task_info.txt`, `$TASK_FILES/test_patch.diff`, `$TASK_FILES/golden_patch.diff`.
The blocker distribution comes from `task_info.txt` ($ARGUMENTS may override).

Follow `references/01-scenario-blocker-planning.md` exactly:
- Decide Narrow Tests YES/NO and Scenario (1/2) via the Core decision rule.
- Apply the Config Lock (exact type distribution). If the config numbers are missing,
  output `CONFIG_MISSING` and STOP.
- Run the reviewer lens on every planned blocker (enforceable / objective / independent /
  not reliably inferable). Redesign before writing if any fails.
- Write ONLY `$DELIVERABLES/plan.md` using the exact template. Do NOT write the registry,
  modified_* artifacts, or any diffs in this step.

Then STOP with `STAGE plan: DONE` and a one-line scenario + blocker-count summary. Next:
`/hilbench-registry`.
