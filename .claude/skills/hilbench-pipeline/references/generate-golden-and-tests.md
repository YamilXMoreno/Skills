# Generate Golden Patch and Test Cases (Scenario 2, one-shot alternative)

Alternative to the multi-part `references/07-patch-outputs.md` for producing the obstructed
test + golden patches in one pass. Before running: apply `setup_patch.diff` and commit it to
the container repo so the codebase obstructions are present (this is exactly what
`scripts/prepare_check.sh` does when baking the baseline).

Output: `$DELIVERABLES/test_patch_obstructed.diff`, `$DELIVERABLES/golden_patch_obstructed.diff`,
`$DELIVERABLES/relevant_tests.txt`, plus a chat-only tests-to-blockers map.

## Prompt

You are a test case and solution generator agent. Read the problem statement, requirements,
public interfaces, and blocker resolutions, and understand the codebase.

Workflow:
- Implement the solution first within the relevant SOURCE files. The golden patch must NOT
  modify any test files. Ensure the golden solution addresses every instruction in the problem
  statement, requirements, interfaces, and blocker resolutions.
- If any instruction is still ambiguous in terms of implementation detail AFTER considering the
  blocker resolutions, STOP and output:
  `UNDOCUMENTED BLOCKERS - your instructions contain blockers not documented in the blocker registry`
  followed by the list of ambiguous instructions. Do not assume undocumented implementation
  details — they must be documented as a blocker or clarified.
- If all instructions are clear, complete the golden solution and produce
  `golden_patch_obstructed.diff`.
- Implement test cases that fully validate the solution. Keep a tab of which test covers which
  blocker resolution. Each test MUST fail before the golden patch is applied and pass after. A
  test that passes before the golden patch, or fails after, is invalid.
- Produce `test_patch_obstructed.diff` (tests only).
- Output the relevant tests as a JSON array compatible with the checker
  (`references/06-relevant-tests.md` rules apply: discoverable verbatim in the test patch, exact
  strings, no renaming).
- Output (chat only) which tests are relevant to which blocker resolution.
- You may run the checker (`docker exec "$HILBENCH_CONTAINER" python task_files/task_checker.py ...`)
  to confirm all relevant tests fail before and pass after the golden solution.

## Inputs (replace)

- Problem Statement (with blockers): `$DELIVERABLES/modified_problem_statement.txt`
- Requirements (with blockers): `$DELIVERABLES/modified_requirements.txt`
- Public Interfaces (with blockers): `$DELIVERABLES/modified_public_interfaces.txt`
- Blocker resolutions (id + description + resolution): from `$DELIVERABLES/blocker_registry.md`

## Undocumented-blocker output

`UNDOCUMENTED BLOCKERS - your instructions contain blockers not documented in the blocker registry`
followed by the list of ambiguous instructions.
