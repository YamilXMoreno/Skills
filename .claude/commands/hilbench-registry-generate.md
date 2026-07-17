---
description: Authoring (one-shot) - generate blocker candidates concurrently from the ORIGINAL task inputs, then merge and validate the complete registry once. Alternative to the /hilbench-plan + /hilbench-registry two-step path. STOPS for review; always follow with /hilbench-validate-registry.
argument-hint: "[optional: distribution override]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is one of THREE ways to
author the registry (generator | numbered plan+registry | manual). Pick one.

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Read the ORIGINAL inputs from
`$TASK_FILES` (`task_info.txt`, `test_patch.diff`, `golden_patch.diff`) and the required
blocker distribution from `task_info.txt` ($ARGUMENTS may override it).

Follow `references/blocker-registry-generator.md` exactly. First have the parent lock the
required blocker slots (id + type from the distribution). Then launch one NEW isolated worker
per slot in one parallel batch. Each worker receives the original inputs, its locked id/type,
and the full GOOD-blocker criteria, and returns exactly one candidate blocker without writing
files. Workers MUST NOT see another candidate or retry themselves.

Wait for every worker before accepting or repairing anything. The parent then performs one
whole-set distribution, overlap, independence, leak, schema, and text-hygiene review. Resolve
cross-candidate conflicts in one bounded merge pass; do not start a generate→evaluate→fix loop.
Only the parent writes:
- Write `$DELIVERABLES/blocker_registry.json` (valid JSON per
  `references/blocker_registry.schema.json`).
- Write `$DELIVERABLES/blocker_registry.md` (identical human-readable mirror).
- Match the required distribution exactly. If it is missing, output `CONFIG_MISSING` and
  STOP.
- Print (chat only) the summary counts and the independence matrix.

If the merged set has issues, report all issues together and apply one batch correction before
writing. Never fix the first candidate issue while other generation workers are still running.

Then STOP with `STAGE registry-generate: DONE`. Do NOT inject anything yet. The registry
is NOT trusted until it passes `/hilbench-validate-registry` — tell the contributor to run
that next (and to review the resolutions themselves, since they own the answer key).
