
# Patch Outputs (Scenario 2)

Run this step ONLY if the plan decided Scenario 2 (patches must change). Work through the four
parts below in order: (1) Patch plan, then generate (2) setup_patch.diff, (3)
test_patch_obstructed.diff, and (4) golden_patch_obstructed.diff.

CRITICAL diff requirements (apply to ANY provided *.diff output in this workflow):
- The file content MUST be a valid unified diff (git-style).
- Do NOT wrap diffs in markdown code fences.
- The diff file must contain ONLY the diff (no prose before/after).
- Every diff MUST use UTF-8 with LF (`\n`) line endings only. CRLF and lone CR are hard
  failures; normalize the working files and regenerate with `git diff` rather than editing
  newline bytes inside a generated patch.
- Diff paths MUST be repo-relative (no absolute paths inside the diff).
- Diff generation hard rules:
  - Do NOT hand-assemble or manually splice unified diffs except for trivial one-file edits.
  - Prefer generating diffs from a clean working tree using `git diff` after making real file changes.
  - When writing any *.diff output file, overwrite the entire file content (do NOT append).
  - Sanity check the diff structure:
    - Each modified file should appear exactly once as a `diff --git a/... b/...` block.
    - Do NOT duplicate `diff --git` blocks for the same path.

Shared rules (apply to every part):
- If you WRITE any output file, file content MUST be English-only.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording in any output artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: do not design patches/tests so the exact resolutions become reliably inferable via defaults, conventions, or simple elimination.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

NOTE (container-backed workflow): all `git diff` / `git apply --check` operations run inside
the provisioned container against the real repo, e.g.
`docker exec "$HILBENCH_CONTAINER" git -C "$HILBENCH_REPO" apply --check <diff>`. Make edits
in the container work tree, then capture the diff with
`docker exec "$HILBENCH_CONTAINER" git -C "$HILBENCH_REPO" diff` and write it to the
corresponding `$DELIVERABLES/*.diff` file.

---

## Part 1 - Patch plan (no diffs yet)

Embedded rules:
- Create narrowness: new tests must be deterministic and specific.
- Patch-visible tests only (hard rule):
  - The blocker enforcement surface is the tests you provide in $DELIVERABLES/test_patch_obstructed.diff.
  - Do NOT rely on or reference any other existing tests in the repo to enforce blockers.
- Clean separation (across any provided patch outputs):
  - test_patch_obstructed.diff: tests only (NO implementation)
  - golden_patch_obstructed.diff: implementation only (NO tests)
  - setup_patch.diff (if present): visible codebase/environment changes only (NO tests)
- No contamination: setup/tests/text must not leak the hidden resolutions.
- Anti-leak hard rule: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.
- Linter-aligned hard gate (test relevance):
  - For EACH blocker id in $DELIVERABLES/blocker_registry.json, you MUST be able to point to at least one concrete test assertion in test_patch_obstructed.diff that will fail without the correct resolution and pass with it.
  - Do NOT write resolutions that are more detailed than what tests will verify.

Codebase leak audit (hard; run once per blocker before planning patches):
- Inspect the agent-visible repository at the parent commit, including source, comments,
  documentation, examples, fixtures, configuration, defaults, names, and error messages.
- For each blocker, list any clue that states its resolution, makes the correct choice the only
  plausible implementation, copies a distinctive constant/name/example from the resolution, or
  exposes the answer through an already-implemented code path.
- `setup_patch.diff` MUST remove or neutralize every confirmed codebase clue for every blocker.
  Prefer deleting answer-bearing comments/examples/defaults or replacing them with neutral
  scaffolding that preserves buildability and at least three plausible implementations.
- Do not remove behavior required by the original task, damage unrelated functionality, alter
  tests, or insert new hints. If a leak cannot be removed without changing the task's legitimate
  contract, reject/regenerate that blocker instead of hiding required behavior.
- After applying setup in a clean checkout, repeat the audit against the resulting visible
  repository. Any remaining blocker with a reliably inferable resolution is a hard failure.

Inputs:
- $DELIVERABLES/plan.md
- @task_info.txt
- @test_patch.diff
- @golden_patch.diff
- $DELIVERABLES/blocker_registry.json

Task:
Produce a concrete plan:
A) setup_patch.diff: what visible scaffolding/refactors are needed to make the with-blockers task coherent (without leaking answers).
B) test_patch_obstructed.diff: exact assertions that enforce the narrowed behaviors (without leaking via comments/fixtures).
C) golden_patch_obstructed.diff: implementation changes to pass the obstructed tests once blockers are resolved.

Required output detail (in chat):
- Per blocker id, list:
  - which test(s) will enforce it
  - what exact assertion shape enforces it (no hidden values)
- If you cannot map a blocker to a concrete assertion, STOP and revise blocker_registry (or revise the plan) before generating diffs.

Check 2 solvability gate (hard - design logic):
- This workflow will later be validated by having an agent generate its own golden patch using ONLY:
  - modified_problem_statement.txt / modified_requirements.txt / modified_public_interfaces.txt
  - relevant_tests.txt
  - test_patch_obstructed.diff
  - blocker_registry (resolutions)
- Check 2 FAILS if the generated patch applies but does not pass the relevant tests.
- Therefore, BEFORE generating diffs, you MUST prove (in chat) that the test surface is fully aligned with the contracts + resolutions (no hidden repo conventions required).
- Required chat-only artifacts:
  A) Assertion-to-Contract-to-Resolution mapping (mechanical; no prose):
     - For EACH new/modified assertion you plan to add in test_patch_obstructed.diff, list:
       - Assertion: <short description of what is being asserted>
       - Test anchor: <where in the test file / which test case name>
       - Contract anchor: <exact requirement bullet or PS sentence that justifies this assertion>
       - Resolution anchor: <which blocker id(s) supply the missing rule/value for this assertion>
  B) Implementation landing spot (mechanical):
     - For EACH assertion above, name the intended implementation touchpoint:
       - File path(s) + function/class/symbol name(s) where the agent would implement the behavior.
  C) Determinism check (mechanical):
     - If an assertion checks string formatting, ordering, tie-breaks, boundary behavior, or error shapes,
       confirm the corresponding blocker resolution provides a fully implementable deterministic rule
       (avoid vague words like "deterministic", "canonical", "standard" without an explicit rule).

Hard alignment acceptance checklist (hard):
- No credit for describing behavior without a test that would fail if the behavior is missing.
- Provide a mapping table (chat-only) that binds "text spec -> tests -> implementation" so the agent cannot stop after partial edits.
- Mapping table requirements (chat-only; one row per spec item):
  - Spec item:
    - an exact requirement bullet (verbatim), OR
    - a resolution-derived spec rule (if intentionally omitted from requirements for must-ask)
  - Test assertion(s) that lock it:
    - test file + test case name + assertion summary
  - Code location(s) that make it pass:
    - file path + symbol name(s)
  - Adapter coverage (hard):
    - If multiple adapters/backends exist, list code locations for EACH adapter (e.g., mongo/postgres/redis).
    - FAIL if you only implement one adapter while the tests/spec imply cross-adapter consistency.
- Minimum counterexample tests (hard):
  - For each high-risk spec item (Unicode/case-fold, ordering/tie-break, boundary behavior, return object shape),
    add/adjust at least one test case that:
    - fails before the behavior exists, and
    - passes only when the exact behavior exists, and
    - includes data that forces the behavior (e.g., Unicode variants + different casing; missing-target requiring a specific object shape).
- Self-verification statement (hard; chat-only):
  - For at least 2 high-risk spec items, state:
    - "If I remove <exact code location>, test <test name> would fail because <one sentence reason>."

Linter-aligned hard gate (patch validity preflight - REQUIRED):
- After you generate EACH diff file, you MUST do an "apply dry-run" check before proceeding:
  - Prefer: `git apply --check <that_diff_file>` (run inside the container against $HILBENCH_REPO)
  - If git is unavailable: `patch -p1 --dry-run < <that_diff_file>`
- If the dry-run fails for ANY diff, STOP and fix the diff until it applies cleanly.
- Keep relevant_tests aligned with what the runner can find:
  - Every entry in relevant_tests.txt MUST be discoverable verbatim in the provided test patch diff text.
  - Prefer concrete test case identifiers (e.g., Go `Test*` function names) when they appear verbatim in the diff text.
  - Use file paths from diff headers only as a fallback when no concrete test case identifiers are discoverable.
- relevant_tests scope (guideline update):
  - relevant_tests.txt MUST list ALL tests in the test patch.

Output:
- File paths + bullet points of exact behaviors/assertions.

Do NOT output diffs yet.

---

## Part 2 - Generate setup_patch.diff

Embedded rules:
- setup_patch.diff is visible to the agent; it must NOT leak hidden resolutions.
- setup_patch.diff must NOT modify tests.
- Do not encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.
- Include every safe codebase leak-removal identified in Part 1. For each blocker, verify the
  post-setup repository no longer states, implements, or uniquely implies its resolution.

Task:
If (and only if) your plan requires visible setup/environment/codebase changes, generate a unified diff containing ONLY setup changes from your plan.
If no setup changes are needed, do NOT write $DELIVERABLES/setup_patch.diff.

Write output file:
- $DELIVERABLES/setup_patch.diff

---

## Part 3 - Generate test_patch_obstructed.diff

Embedded rules:
- Tests must be narrow, deterministic, and specific.
- Do not leak answers in comments, test names, or fixtures.
- Do not encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.
- test_patch_obstructed.diff must modify tests only.
- Preserve tests needed by the original task. Every test assertion newly introduced during
  blocker authoring must map to at least one blocker in `blocker_test_map.txt`.
- Remove unrelated tests introduced by blocker authoring: tests that neither originate in the
  original test patch nor enforce a blocker resolution. Do not remove original task tests merely
  because they are not blocker-specific.

Task:
Generate a unified diff implementing the obstructed tests from your plan.

Write output file:
- $DELIVERABLES/test_patch_obstructed.diff

---

## Part 4 - Generate golden_patch_obstructed.diff

Embedded rules:
- golden_patch_obstructed.diff must implement the resolved behavior and must NOT modify tests.
- Keep it consistent with the obstructed tests and the blocker resolutions (as the answer key).
- Do not encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.
- Linter-aligned hard gate (golden patch completeness):
  - The golden diff MUST include ALL required code changes implied by:
    - $DELIVERABLES/modified_public_interfaces.txt
    - $DELIVERABLES/modified_requirements.txt
    - $DELIVERABLES/blocker_registry.json (resolutions as the answer key)
  - Do NOT reference helpers/fields/types that are not defined in the diff (no "used but missing definition").
- Linter-aligned hard gate (spec fidelity; prevents "golden patch mismatch" failures):
  - If requirements/interfaces/resolutions specify an explicit formula, the implementation MUST compute that exact formula.
    - Do NOT substitute a "similar" computation unless it is provably equivalent for all inputs.
  - If requirements/interfaces/resolutions specify multiple selection criteria (e.g., recency + validity + expiry + tie-break),
    the implementation MUST apply ALL specified criteria in ALL relevant code paths.
    - Do NOT implement criteria only in one branch and skip them in another branch with an early return.
  - If requirements/interfaces/resolutions specify "regardless of which value is larger" (or equivalent),
    ensure the implementation is symmetric with respect to the compared values.

Task:
Generate a unified diff implementing the correct resolved behavior that passes the obstructed tests.

Post-generation self-check (required; outside the diff file):
- After writing $DELIVERABLES/golden_patch_obstructed.diff, you MUST run the Patch Content Validator (references/08-patch-content-validator.md).
- If the validator returns FALSE for any golden/spec mismatch, revise the golden patch diff (preferred) rather than weakening tests.
- Run `scripts/validate_patch_artifacts.py --deliverables "$DELIVERABLES"` and require
  `PATCH_FORMAT_OK LF_ONLY VALID_UNIFIED_DIFF`.

Write output file:
- $DELIVERABLES/golden_patch_obstructed.diff
