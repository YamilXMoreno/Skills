---
description: Authoring step 02 - generate planned blocker entries concurrently, then merge them into blocker_registry.json + blocker_registry.md with one whole-set review. STOPS for review; always follow with /hilbench-validate-registry.
argument-hint: ""
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is step 02 of the numbered
authoring path (requires `$DELIVERABLES/plan.md` from `/hilbench-plan`).

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Inputs:
`$DELIVERABLES/plan.md` (source of truth), `$TASK_FILES/task_info.txt`,
`$TASK_FILES/test_patch.diff`, `$TASK_FILES/golden_patch.diff`.

Follow `references/02-generate-blocker-registry.md` exactly. After validating the Config Lock,
launch one NEW isolated worker per blocker id from `plan.md` in one parallel batch. Give each
worker the complete plan and originals but assign it exactly one locked id/type. Each worker
returns one candidate and MUST NOT write files, inspect another worker's output, or retry itself.

Wait for all workers before judging any candidate. The parent performs one whole-set hard-gate,
distribution, independence, overlap, and schema review, batches every discovered correction,
and merges once. Do not fix/evaluate candidates one at a time.

- Only the parent may write the final files.
- Generate exactly the blocker ids listed in `plan.md`; counts by type must match the
  Config Lock. If `plan.md` lacks the id list or config, output `PLAN_INVALID` and STOP.
- Apply all hard gates (objective, guessability, independence, description/type coherence,
  area selection, error-message limit, trigger-question relevance, text hygiene).
- Write `$DELIVERABLES/blocker_registry.json` (valid JSON per
  `references/blocker_registry.schema.json`) and `$DELIVERABLES/blocker_registry.md`
  (identical mirror).
- Print (chat only) the summary counts + independence matrix; do NOT write them into files.

Then STOP with `STAGE registry: DONE`. The registry is NOT trusted until it passes
`/hilbench-validate-registry` — run that next.
