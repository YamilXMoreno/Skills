
# Write relevant_tests.txt

Shared rules (apply to this step):
- English-only file content.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording anywhere in this artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- @task_info.txt may contain a relevant-tests list, but it is NOT the source of truth if it conflicts with the provided test patch diff content.

CRITICAL format requirements:
- $DELIVERABLES/relevant_tests.txt MUST contain ONLY raw JSON (a JSON array of strings).
- Do NOT include markdown, code fences, comments, explanations, or any additional keys/objects.
- Do NOT write or modify any files except $DELIVERABLES/relevant_tests.txt.

Linter-aligned hard gate (test list resolvability):
- Every test name you include MUST be discoverable in the provided test patch diff content that will be used by the runner:
  - Scenario 1: $TASK_FILES/test_patch.diff
  - Scenario 2: $DELIVERABLES/test_patch_obstructed.diff
- "Discoverable" means EITHER (a) the exact test identifier appears verbatim in the diff text, OR
  (b) it qualifies under the Suite entry-point exception below.
- If a listed test name from @task_info.txt is neither verbatim-present nor a valid suite
  entry-point, do NOT include it in relevant_tests.txt.
- Do NOT invent or rename tests to make them "sound right". Use the exact strings as they appear in the patch (or the exact suite entry-point name from @task_info.txt).

Suite entry-point exception (language-aware; prevents dropping valid runner-level tests):
- Some ecosystems run tests through a single runner-level entry-point function whose NAME does
  NOT appear in the diff, even though the diff modifies the specs/cases that entry-point executes.
  The canonical case is Go: `func TestXxx(t *testing.T)` (often a Ginkgo/Testify suite bootstrap
  like `TestPersistence`) is what `go test -run TestXxx` selects, while the diff only touches
  `Describe(...)` / `It(...)` / `t.Run(...)` blocks in the same package.
- An entry-point identifier from @task_info.txt is VALID (keep it) when BOTH hold:
  1. It matches the runner's selection format (e.g., Go: a `Test*` function name), AND
  2. The test patch diff modifies at least one `*_test.go` (or equivalent test file) in the SAME
     package/directory that this entry-point governs (check the `diff --git a/<path>` headers).
- In that case list the entry-point identifier (e.g., `TestPersistence`) EXACTLY as given in
  @task_info.txt, even though it is not a literal substring of the diff. Do NOT instead list the
  individual `Describe`/`It` titles unless the runner actually selects/report by those.
- This exception is for runner-level entry-points only. Do NOT use it to keep arbitrary names
  that have no governing test file in the diff.

Guideline update (test list scope):
- relevant_tests.txt MUST include ALL tests in the authoritative test patch diff that will be used by the runner.
  - Scenario 1: $TASK_FILES/test_patch.diff
  - Scenario 2: $DELIVERABLES/test_patch_obstructed.diff
- relevant_tests.txt is no longer "blocker-dependent only".
- Before deriving the list for Scenario 2, remove any test introduced during blocker authoring
  that neither (a) exists in/traces to the original test patch nor (b) enforces at least one
  blocker resolution. Preserve original task tests even when they are not blocker-specific.
- Every non-original test remaining in `test_patch_obstructed.diff` MUST appear under at least
  one blocker in `blocker_test_map.txt`; an unmapped authored test is an extra-test failure.

Test identifier selection rule (to avoid "Expected tests not found" failures):
- Prefer test identifiers that are most likely to appear in test runner output AND are discoverable verbatim in the patch text.
  - Prefer concrete test case identifiers when they exist in the patch text:
    - Go: `Test*` function names (e.g., `TestIsValidImage`)
    - Similar patterns in other frameworks/languages (e.g., `test_*`, `Test*`) if present verbatim in the diff text.
  - Use the test FILE PATH (from diff headers like `diff --git a/<path> b/<path>`) ONLY as a fallback when no concrete test case identifiers are discoverable verbatim in the patch text.
- Only list a test "title" (e.g., `<file> - <test name>`) if that exact full string appears verbatim in the patch text AND you expect the runner output to use that exact title.
- Do NOT normalize paths or extensions (e.g., do NOT turn `.ts` into `.js`, do NOT add suffixes like `| test suite`).

Runner/parser alignment gate (hard):
- relevant_tests.txt is consumed by the runner/parser, not by humans.
- Your entries MUST match the runner/parser's actual test identifier format AND be discoverable verbatim in the provided test patch diff text.
- If your ecosystem commonly reports/selects tests using a composite identifier (e.g., `<file> | <suite> | <title>`), then:
  - Use that exact composite string as the relevant_tests entry, AND
  - Ensure the exact same composite string appears verbatim in the test patch diff text (e.g., as a suite/title string in the test code).
- If you cannot guarantee runner/parser identifier alignment, STOP and revise the test patch so the intended identifiers appear verbatim and unambiguously.

Embedded rules:
- English-only file content.
- relevant_tests.txt MUST be valid JSON: it must contain ONLY a JSON array of test names (strings).
- Source of truth: the relevant test patch diff content (Scenario 1: $TASK_FILES/test_patch.diff; Scenario 2: $DELIVERABLES/test_patch_obstructed.diff).
- If a test name from @task_info.txt is NOT discoverable verbatim in the relevant test patch diff content AND does not qualify under the Suite entry-point exception above, do NOT include it.
- Preserve exact spelling, casing, spacing, and punctuation.
- Scenario 1: list all tests present in $TASK_FILES/test_patch.diff.
- Scenario 2: list all tests present in $DELIVERABLES/test_patch_obstructed.diff.

Inputs:
- $DELIVERABLES/plan.md (Scenario Decision is the source of truth)
- @task_info.txt (original relevant tests list)
- Scenario 1: $TASK_FILES/test_patch.diff (for verification)
- Scenario 2: $DELIVERABLES/test_patch_obstructed.diff (for verification)

Task:
Write the relevant tests list accordingly (all tests in the authoritative test patch).

Write output file:
- $DELIVERABLES/relevant_tests.txt

After writing relevant_tests.txt, also write blocker_test_map.txt (see below).

# Also write blocker_test_map.txt (blocker -> validating tests)

Purpose: a reviewer-facing map from each blocker to the specific test(s) that would FAIL if that
blocker's resolution were implemented wrong. It complements relevant_tests.txt (a flat list) by
showing coverage per blocker and surfacing any blocker with no enforcing test.

Inputs:
- $DELIVERABLES/blocker_registry.json (each blocker's `title` + `resolution`)
- Authoritative test patch (Scenario 2: $DELIVERABLES/test_patch_obstructed.diff;
  Scenario 1: $TASK_FILES/test_patch.diff)
- $DELIVERABLES/relevant_tests.txt (the identifiers you just wrote)

Rules:
- Group by blocker, one block per blocker, in registry order. The header line is the blocker
  `title` wrapped in backticks.
- Under each header, list each validating test on its own `- ` line. A test validates a blocker
  if a plausible WRONG implementation of that blocker's resolution would make that test fail
  (same standard as eval_test_relevance / eval 7 Test A).
- Every test you list MUST appear verbatim in relevant_tests.txt (identical spelling, casing,
  spacing). Never introduce a name that is not in relevant_tests.txt / the authoritative patch.
- A test MAY appear under more than one blocker if it genuinely enforces more than one.
- If a blocker has NO enforcing test in the authoritative patch, still emit its header with a
  single line `- (no relevant test found in the test patch)`. Do NOT drop it silently - an
  unenforced blocker is a coverage gap the reviewer must see.
- Do NOT list general/regression tests that map to no blocker; this file is blocker-centric
  (relevant_tests.txt already lists every patch test).
- English-only. Use the blocker `title` exactly. This is an internal reviewer aid; it is not
  shipped to the solving agent.

Format:
  `blocker_title_one`
  - test.identifier.one
  - test.identifier.two

  `blocker_title_two`
  - test.identifier.three

Write output file:
- $DELIVERABLES/blocker_test_map.txt

Stop immediately after writing $DELIVERABLES/relevant_tests.txt and
$DELIVERABLES/blocker_test_map.txt.
