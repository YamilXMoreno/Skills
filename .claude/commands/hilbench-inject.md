---
description: Injection (steps 03-06, plus step 07 obstructed patches on Scenario 2) - write the modified problem statement, requirements, public interfaces, relevant_tests.txt, and blocker_test_map.txt from the validated registry, and (Scenario 2) generate setup/test/golden obstructed diffs against the container repo. STOPS for review.
argument-hint: "[optional: --scenario 1|2]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). Precondition: the registry passed
`/hilbench-validate-registry`.

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both).

## Determine the Scenario (do NOT assume `plan.md` exists)

The Scenario decides whether you touch the patches:
- **Scenario 1** — text-only injection. The ORIGINAL test patch already contains narrow
  assertions that pin down the blocker resolutions, so you only rewrite the problem
  statement / requirements / interfaces and reuse the original patches unchanged.
- **Scenario 2** — the original tests are too broad to enforce the blockers, so you must ALSO
  author obstructed patches: add narrow tests (`test_patch_obstructed.diff`), an
  implementation (`golden_patch_obstructed.diff`), and optionally a `setup_patch.diff`.

Resolve the Scenario in this order (stop at the first that applies):
1. The `--scenario 1|2` argument in $ARGUMENTS, if given.
2. The "Decision" line in `$DELIVERABLES/plan.md`, if that file exists (numbered authoring path).
3. Otherwise (generator or manual authoring — no `plan.md`): determine it yourself using the
   Core decision rule / Narrow Tests analysis in `references/01-scenario-blocker-planning.md`
   against `$TASK_FILES/test_patch.diff`. Print the decision and a one-line justification
   (which narrow assertions do or do not already enforce each blocker) BEFORE injecting.

If you cannot confidently decide and no override was given, STOP and ask the contributor to
pass `--scenario 1|2` rather than guessing.

Perform the independent text-generation work first:

1. **Parallel text fan-out.** Launch three NEW isolated workers in one batch:
   - Step 03 worker follows `references/03-modified-problem-statement.md`.
   - Step 04 worker follows `references/04-modified-requirements.md`.
   - Step 05 worker follows `references/05-modified-public-interfaces.md`.

   Give every worker the same validated registry and originals, but only its assigned prompt.
   Workers return draft content and MUST NOT write final files, inspect sibling drafts, or start
   a repair/eval loop.
2. **Serial consistency merge.** Wait for all three drafts, then have the parent check them
   together for cross-document contradictions, leaks, duplicated requirements, missing blocker
   coverage, and interface/requirement mismatch. For each blocker, reconstruct what an agent can
   infer from all three visible texts together; fail if the resolution or a uniquely identifying
   constant/name/example is exposed, or if fewer than three plausible choices remain. Collect all issues before applying one batch
   correction. Only then atomically write:
   `$DELIVERABLES/modified_problem_statement.txt`,
   `$DELIVERABLES/modified_requirements.txt`, and
   `$DELIVERABLES/modified_public_interfaces.txt`.
3. Scenario 2 ONLY - step 07 patch outputs, following `references/07-patch-outputs.md`
   (Parts 1-4): produce `setup_patch.diff` (if needed), `test_patch_obstructed.diff`, and
   `golden_patch_obstructed.diff`. Make real edits in the container repo and capture diffs
   via `docker exec "$HILBENCH_CONTAINER" git -C "$HILBENCH_REPO" diff`; run
   `git apply --check` inside the container on each diff. (Alternative one-shot path:
   `references/generate-golden-and-tests.md`.)
   - Before authoring tests/golden, audit the entire agent-visible parent codebase once per
     blocker. Use `setup_patch.diff` to remove or neutralize every safe-to-remove answer-bearing
     clue in source, comments, docs, examples, fixtures, defaults, names, config, and errors.
     Re-audit after setup. If a required original contract itself reveals the answer, regenerate
     the blocker rather than deleting legitimate behavior.
   - Scenario 1: do NOT modify patch logic. Copy the originals to
     `$DELIVERABLES/test_patch_obstructed.diff` and `golden_patch_obstructed.diff` (headers/
   whitespace only if needed to apply cleanly), normalizing file line endings to LF.
4. Step 06 - relevant tests, following `references/06-relevant-tests.md` -> write
   `$DELIVERABLES/relevant_tests.txt` AND `$DELIVERABLES/blocker_test_map.txt`. The
   authoritative test patch is `$DELIVERABLES/test_patch_obstructed.diff` (Scenario 2) or
   `$TASK_FILES/test_patch.diff` (Scenario 1). For relevant_tests.txt: write ONLY a JSON array
   of strings: ALL tests present in that test patch (not blocker-dependent only), each
   discoverable verbatim (or a valid runner-level suite entry-point). Preserve exact
   spelling/casing/spacing; do NOT rename or normalize. For blocker_test_map.txt: a
   reviewer-facing `blocker title -> validating tests` map (registry order), each listed test
   drawn verbatim from relevant_tests.txt; emit a blocker with `- (no relevant test found in
   the test patch)` when nothing in the patch enforces it, per the rules in
   `references/06-relevant-tests.md`.

5. **Patch hygiene and authored-test scope gate.**
   - Write every generated text/diff with UTF-8 LF line endings only; CRLF/lone CR is forbidden.
   - Run `python3 scripts/validate_patch_artifacts.py --deliverables "$DELIVERABLES"` and require
     `PATCH_FORMAT_OK LF_ONLY VALID_UNIFIED_DIFF`.
   - Compare `test_patch_obstructed.diff` with the original test patch. Preserve original task
     tests, but remove every test introduced during blocker authoring that maps to no blocker in
     `blocker_test_map.txt`.
   - Re-run the mechanical validator after any removal and regenerate
     `relevant_tests.txt`/`blocker_test_map.txt` from the final test patch.

All authored file content must be English-only, leak-free, and non-self-referential.

Then STOP with `STAGE inject: DONE` and a summary of which artifacts were written (including
the relevant_tests.txt count and, from blocker_test_map.txt, any blocker with no enforcing
test). Next: `/hilbench-validate-artifacts`.
