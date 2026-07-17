
# Patch Content Validator (must PASS)

Run this in a fresh, isolated chat. For best-effort resolution-only behavior, do NOT provide
$DELIVERABLES/blocker_registry.md to this validator; supply only the resolution list below.

Paths: `$TASK_FILES` = read-only inputs (`task_info.txt`, `test_patch.diff`, `golden_patch.diff`);
`$DELIVERABLES` = the separate output folder with the authored artifacts (modified text, relevant
tests, obstructed patches). Substitute your resolved absolute paths.

Replace the placeholders with the actual contents. Please use the modified versions of the problem statement, requirements and interfaces.

Provide the Blocker resolutions in a list (resolution-only; do NOT include extra metadata):
- <Blocker ID 1>: <resolution text>
- <Blocker ID 2>: <resolution text>
- <Blocker ID 3>: <resolution text>
- etc.

Your job is to determine the following:
1) Whether you can find the tests from the relevant test list within the test patch.
2) Whether you can find at least one enforcing test for each blocker resolution:
   - Each resolution must have at least one concrete assertion that would FAIL without the resolution and PASS with it.
3) Whether the golden patch implements:
   - the problem statement, AND
   - the requirements, AND
   - the public interface (if any), AND
   - the blocker resolutions.
4) Whether patch separation is coherent (overlap rules).
   - Overlap check scope:
     - Only check overlap between test patch and golden patch.
     - Ignore setup patch for overlap purposes (setup may touch the same paths as either patch).
   - Disallowed overlap (hard):
     - test patch and golden patch must NOT overlap on the same file path.
5) Whether authored test scope is minimal:
   - Preserve original task tests.
   - Every test/assertion added beyond the original test patch must enforce at least one blocker
     resolution. Unmapped blocker-authoring tests are extraneous and must be removed.

Hard constraints:
- Output plaintext only (no markdown).
- English-only response.
- Do NOT modify any of the artifacts under validation (specs, patches, tests, registry). The
  only file you may write is the designated results file
  `$DELIVERABLES/validate_artifacts_result.txt` (the gate verdict + alignment map), and only
  after emitting the verdict.
- Do NOT output any *.diff content (and never write diff content or blocker resolutions into the results file).
- Use the blocker id when referencing a blocker.
- Patch-visible tests only:
  - Treat the provided test patch diff content as the ONLY test surface.
- relevant_tests scope:
  - relevant_tests.txt must list ALL tests in the test patch (not blocker-dependent only).
  - Do NOT treat relevant_tests.txt as "blocker-dependent only".

Inputs:
Problem Statement:
$DELIVERABLES/modified_problem_statement.txt
Requirements:
$DELIVERABLES/modified_requirements.txt
Public Interface:
$DELIVERABLES/modified_public_interfaces.txt
Blocker Resolutions:
{{BLOCKER_RESOLUTIONS_LIST}}
Relevant tests:
$DELIVERABLES/relevant_tests.txt
Blocker-to-test map:
$DELIVERABLES/blocker_test_map.txt
Original Test Patch:
$TASK_FILES/test_patch.diff
Golden Patch:
$DELIVERABLES/golden_patch_obstructed.diff
Setup Patch (optional; may be empty):
$DELIVERABLES/setup_patch.diff
Test Patch:
$DELIVERABLES/test_patch_obstructed.diff

Workflow (return FALSE immediately on first hard failure):
1) relevant_tests resolvability:
   - A relevant test is "resolvable" if EITHER (a) its identifier appears verbatim in the test
     patch diff content, OR (b) it is a runner-level suite entry-point: an identifier matching the
     runner's selection format (e.g., Go: a `Test*` function such as `TestPersistence`) whose
     governing test file IS modified by the test patch (same package/dir per the `diff --git`
     headers), even though the entry-point name itself is not a literal substring of the diff.
   - Only if a relevant test is neither verbatim-present nor a valid suite entry-point, return:
     FALSE - could not find relevant test in test patch: <entry>
2) Per-resolution enforcement:
   - If there is one or more missing test that should address one of the blocker resolutions, return:
     FALSE - missing enforcing test for blocker: <blocker_id>
   - If you find a test but it does not actually check whether the resolution was applied (too broad), return:
     FALSE - test does not validate resolution for blocker: <blocker_id>
2.1) Extra authored-test audit:
   - Compare the obstructed test patch with the original test patch.
   - For every newly introduced or expanded test/assertion, require a concrete mapping to at
     least one blocker resolution in `blocker_test_map.txt`.
   - Preserve original task tests even if they do not map to blockers.
   - If a blocker-authoring test is neither original nor mapped to a blocker, return:
     FALSE - extra authored test not tied to a blocker: <test/path>
3) Golden patch completeness:
   - If there are one or more requirements missing from the problem statement, requirements, public interfaces, or blocker resolutions within the golden patch, return:
     FALSE - golden patch missing required behavior: <short reason>
4) Golden patch spec fidelity (hard; prevents subtle mismatch):
   - Formula fidelity:
     - If a requirement/interface/resolution specifies an explicit formula, return FALSE if the golden patch implements a different computation that is not equivalent for all inputs.
   - Criteria completeness:
     - If the spec lists multiple selection criteria (recency/expiry/validity/tie-break/etc.), return FALSE if any criterion is missing in any branch/code path.
   - Branch consistency:
     - Return FALSE if one branch applies the full rule set but another branch applies only a subset (early-return partial logic).
4.1) Consistency mapping (hard; required to avoid late QC failures):
   - Before returning TRUE, you MUST produce a minimal, mechanical alignment map that ties together:
     - Spec anchor (exact quote from Requirements or Public Interface, OR a resolution-derived rule)
     - Enforcing test anchor (a concrete test case name OR a test file path that appears in the test patch diff headers)
     - Golden patch anchor (a file path + function/symbol name or diff context showing where it is implemented)
   - Requirements:
     - For EACH blocker id in the provided resolutions list, output exactly one mapping line:
       - <blocker_id> | Spec anchor: <quote or resolution rule> | Test anchor: <test name/path> | Golden anchor: <file + symbol/context>
     - Additionally, for EACH multi-criteria requirement (selection rules with 2+ criteria), output one mapping line:
       - Spec anchor: <verbatim quote> | Criteria list: <criteria> | Test anchor: <...> | Golden anchor: <...>
       - Do NOT invent stable keys (e.g., `kube_cluster_cli_precedence`). If you need a label, add it in parentheses after the quote.
       - Prefer a test case name anchor when available; file-path anchors are acceptable only when no test name/title is discoverable in the diff text.
       - IMPORTANT: Multi-criteria requirement Test anchors may be baseline spec tests (non-blocker) and do not need to be tied to a blocker resolution.
   - If you cannot produce the mapping for any blocker id or any multi-criteria requirement, return:
     FALSE - missing alignment mapping for: <blocker_id or requirement>
5) Patch separation:
   - Overlap rules:
     - Ignore setup patch for overlap purposes.
     - Test patch must NOT overlap with golden patch:
       - If any file path is modified by the test patch AND also modified by the golden patch, return:
         FALSE - test patch overlaps with golden patch for file: <path>
   - If the golden patch modifies files in the test folder, return:
     FALSE - golden patch modifies a test file: <path>
   - If the test patch modifies files outside of the test folder, return:
     FALSE - test patch modifies a non-test file: <path>

Output:
- If PASS, output:
  - ALIGNMENT MAP (plaintext; one line per item)
  - TRUE
- or: FALSE - <single concise reason line>

Example:
FALSE - could not find test/units/plugins/connection/test_winrm.py::TestWinRMKerbAuth::test_kinit_success_pexpect[options3-expected3] within the test patch.
