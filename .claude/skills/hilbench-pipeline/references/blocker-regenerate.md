# Selective blocker regeneration (regenerate named blockers, keep the rest)

Use this when a task's registry is mostly good but **one or a few specific blockers** need to be
re-done (e.g. a blocker came back `FAIL_GUESSABLE`, or a static eval flagged one blocker as
non-independent / mistyped / self-referential and a reword is not enough). It regenerates ONLY the
named blockers and **merges** them back into the existing registry — the retained blockers are left
untouched.

This is the surgical counterpart to `blocker-registry-generator.md` (which regenerates the WHOLE
registry and overwrites it). Reuse that file's **GOOD-blocker criteria verbatim** (realistic,
critical, objective, vast search space / non-guessable, independent, not self-referential,
anti-leak) — this doc only adds the *constraints* that make a partial regeneration safe.

## Inputs

- `$DELIVERABLES/blocker_registry.json` (+ `.md`) — the current registry (source of the retained
  blockers). REQUIRED; if missing, STOP with `REQUIRED_INPUT_FILE_MISSING`.
- The ORIGINAL task inputs from `$TASK_FILES`: `task_info.txt` (incl. the required distribution),
  `test_patch.diff`, `golden_patch.diff`.
- The list of target blocker id(s) to regenerate (from the command arguments).

## Rules (in addition to the GOOD-blocker criteria)

1. **Regenerate only the targets.** Produce new content ONLY for the named ids. Do not alter any
   retained blocker's `description`, `resolution`, `trigger_questions`, `type_of_obstruction`, or
   `area_of_obstruction`.
2. **Retained blockers are HARD constraints.** Each regenerated blocker MUST be:
   - **Independent** of every retained blocker — its resolution must not reveal, force, or
     partially answer any retained blocker, and no retained blocker may answer it.
   - **Non-overlapping** — it must not occupy the same decision space as a retained blocker
     (no near-duplicate of an existing ambiguity).
3. **Type-lock to preserve the distribution.** Keep each regenerated blocker's
   `type_of_obstruction` the SAME as the entry it replaces, so the required distribution in
   `task_info.txt` still holds. (Only change a type if the caller explicitly overrides it — then
   re-verify the whole-set distribution.)
4. **Keep the id stable.** Reuse the same blocker id for each regenerated entry (so downstream
   references stay valid) unless the caller says otherwise.
5. **Merge, don't overwrite.** Write the full registry back with retained entries byte-for-byte
   and only the target entries replaced. Keep `blocker_registry.md` an identical mirror.
6. Standard hygiene: English-only, leak-free, non-self-referential (no "blocker", "registry",
   "hidden tests", "PS", "removed to create", etc.).

## Output

- `$DELIVERABLES/blocker_registry.json` — merged registry (valid per
  `references/blocker_registry.schema.json`; retained entries unchanged, targets replaced).
- `$DELIVERABLES/blocker_registry.md` — identical mirror.
- Print (chat only): which ids were regenerated, the summary counts by type/area (must still match
  the required distribution), and the independence matrix over the **full** set (all off-diagonal
  cells must be NO).

## After regenerating (the registry is only the first third)

The registry is the source of truth; the modified specs and obstructed patches are **derived** from
it, so they are now stale for the regenerated blocker(s). Re-derive them from the updated registry
via the normal pipeline rather than hand-editing — do NOT try to surgically patch the specs/patches.
The command emits this stream (do NOT auto-run; each stage STOPS for review):

```
/hilbench-validate-registry     # confirm the merged registry + full set
/hilbench-inject                # re-derive ALL modified specs + obstructed patches from the registry
/hilbench-validate-artifacts    # alignment map + patch-dependent evals
/hilbench-validate-obstructed   # dynamic FAIL→PASS on the re-authored patches
/hilbench-check1                # guessability — the reason you regenerated
/hilbench-check2                # solvable with the new resolution
/hilbench-evaluate-full --fresh # authoritative sign-off
```

**Sequencing caveat.** `/hilbench-inject` regenerates *all* derived artifacts from scratch, so it
(a) re-authors the unchanged blockers' specs/patches too — the whole task must re-pass the gates,
not just the regenerated blocker; and (b) **overwrites any artifact-only manual fixes** (e.g. a
golden-patch bug fix or a `setup_patch.diff` tweak from `optional-repair.md`). So regenerate +
re-inject FIRST, then do any artifact-only hand-tuning — never the other way around.
