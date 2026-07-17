---
description: Authoring (repair) - regenerate ONLY the named blocker id(s) and merge them back into the existing registry, keeping every other blocker untouched. Constrained by the retained blockers (independent, non-overlapping, type-locked so the distribution holds). Surgical alternative to the destructive whole-registry /hilbench-registry-generate. STOPS; must be followed by re-inject + revalidation.
argument-hint: "<id[,id...]>  (blocker ids to regenerate; optional --distribution override)"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This regenerates specific blockers
in place — use it when the registry is mostly good but one or a few blockers must be re-done
(e.g. a blocker came back `FAIL_GUESSABLE`, or a static eval flagged one blocker and a reword is
not enough). It is the surgical counterpart to `/hilbench-registry-generate`, which regenerates
and OVERWRITES the whole registry (destroying any curation of the other blockers).

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both).

1. Require `$DELIVERABLES/blocker_registry.json`. If it is missing, STOP with
   `REQUIRED_INPUT_FILE_MISSING`.
2. Parse the target blocker id(s) from `$ARGUMENTS` (comma/space separated). If none are given,
   STOP and ask which ids to regenerate. If any id is not present in the current registry, STOP
   with `UNKNOWN_BLOCKER_ID <id>` and list the valid ids.
3. Read the ORIGINAL inputs from `$TASK_FILES` (`task_info.txt`, `test_patch.diff`,
   `golden_patch.diff`) and the required distribution from `task_info.txt`
   (`--distribution` in `$ARGUMENTS` may override it).

Follow `references/blocker-regenerate.md` exactly (which reuses the GOOD-blocker criteria in
`references/blocker-registry-generator.md` verbatim). Launch one NEW isolated worker per target
id in one parallel batch. Each worker receives the originals, its existing type-locked entry,
and every retained blocker as hard read-only constraints. It returns one replacement candidate
and MUST NOT write the registry, inspect sibling output, or retry itself.

Wait for every replacement worker. The parent then evaluates all candidates together against
the retained set and each other, collects every conflict before changing anything, and performs
one bounded correction/merge pass. Regenerate ONLY the named blockers, constrained by the
retained blockers:
- independent of and non-overlapping with every retained blocker,
- **type-locked** (keep each target's `type_of_obstruction`) so the distribution still matches,
- id stable (reuse the same id), leak-free, non-self-referential.

Then **merge once in the parent**: write `$DELIVERABLES/blocker_registry.json` back with the retained entries
UNCHANGED and only the target entries replaced; keep `$DELIVERABLES/blocker_registry.md` an
identical mirror. Print (chat only) which ids were regenerated, the summary counts by type/area
(must still match the required distribution), and the independence matrix over the FULL set (all
off-diagonal cells `NO`).

STOP with `STAGE registry-regenerate: DONE`. Do NOT re-inject or validate automatically. The
registry is NOT trusted until re-validated, and the modified specs + obstructed patches are now
stale for the regenerated blocker(s) — they must be re-derived from the updated registry. Tell the
contributor to run this stream next (each stage STOPS for review):

```
/hilbench-validate-registry     # confirm the merged registry + full set
/hilbench-inject                # re-derive ALL modified specs + obstructed patches from the registry
/hilbench-validate-artifacts    # alignment map + patch-dependent evals
/hilbench-validate-obstructed   # dynamic FAIL→PASS on the re-authored patches
/hilbench-check1                # guessability — the reason you regenerated
/hilbench-check2                # solvable with the new resolution
/hilbench-evaluate-full --fresh # authoritative sign-off
```

**Sequencing caveat (state it to the contributor):** `/hilbench-inject` regenerates ALL derived
artifacts from scratch, so it re-authors the unchanged blockers' specs/patches too (the whole task
re-passes the gates) and **overwrites any artifact-only manual fixes**. So regenerate + re-inject
FIRST, then do any artifact-only hand-tuning — never the reverse.
