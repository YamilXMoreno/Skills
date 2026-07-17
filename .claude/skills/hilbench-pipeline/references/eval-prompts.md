# Model evals — verbatim prompts (source of truth)

These are the **full, unmodified** grading prompts ported from `ots_project_material/model
evals`. The gates (`/hilbench-validate-registry`, `/hilbench-validate-artifacts`) run these
**verbatim** — do NOT paraphrase or condense them. The long carve-outs and "do NOT return
FALSE for…" guidance are load-bearing (they prevent false-positive FALSEs); keep them intact.

If this file ever drifts from `model evals`, `model evals` wins — re-copy from there.

## How to run one

1. Substitute every `{{placeholder}}` with the actual file contents:

| Placeholder | Source |
|-------------|--------|
| `{{relevant_tests_list}}` | `$DELIVERABLES/relevant_tests.txt` |
| `{{test_patch_contents}}` | `$DELIVERABLES/test_patch_obstructed.diff` (S2) or `$TASK_FILES/test_patch.diff` (S1) |
| `{{full_blocker_registry}}` | `$DELIVERABLES/blocker_registry.json` |
| `{{golden_patch_contents}}` | `$DELIVERABLES/golden_patch_obstructed.diff` |
| `{{golden_patch_init}}` | `$TASK_FILES/golden_patch.diff` (original golden) |
| `{{setup_patch}}` | `$DELIVERABLES/setup_patch.diff` (may be empty) |
| `{{problem_statement_modified}}` | `$DELIVERABLES/modified_problem_statement.txt` |
| `{{requirements_modified}}` | `$DELIVERABLES/modified_requirements.txt` |
| `{{interfaces_modified}}` | `$DELIVERABLES/modified_public_interfaces.txt` |
| `{{original_problem_statement}}` | `$TASK_FILES/task_info.txt` (original PS section) |
| `{{original_requirements}}` | `$TASK_FILES/task_info.txt` (original requirements) |
| `{{original_interfaces}}` | `$TASK_FILES/task_info.txt` (original interfaces) |
| `{{distribution_of_blockers}}` | Required Blocker Distribution in `$TASK_FILES/task_info.txt` |

2. Run each eval **independently** (its own judgment). Output plaintext, reference blockers by
   title/id. On FALSE, keep the eval's own reason format.

## Where each eval runs

| Eval | Runs at | Notes |
|------|---------|-------|
| 10 distribution ⭐ | **registry gate** | registry + distribution only; earliest, hard STOP |
| 5 independence ⭐ | **registry gate** | registry-intrinsic judgment |
| 6 objective | registry gate | |
| 4 blocker_type | registry gate | incl. Condition 1 contamination (was previously dropped) |
| 8 descriptions | registry gate | |
| 9 questions | registry gate | |
| 11 self_reference | registry gate | |
| 7 critical_implementation ⭐ | **split** | Test B+C at registry; Test A (necessity) at artifacts; empirical confirm at Check 1 — see below |
| 1 test_list | **artifacts gate** | needs test patch |
| 2 test_relevance | artifacts gate | needs test patch |
| 3 golden_patch | artifacts gate | needs golden + setup |
| 12 request | **artifacts gate** | needs golden (was previously unmapped) |

Evals 5/6/4 list the golden patch as an input. At the registry gate (pre-injection) the golden
patch may not exist yet — run them on the registry + modified specs (if present, else the
originals) and treat the golden-patch input as empty. Their core judgments (independence,
objectivity, type/contamination) do not require the golden patch.

## Eval 7 split (the three-gate critical eval)

Eval 7 (`eval_blocker_critical_implementation`) is one prompt with three internal tests. Run
the SAME verbatim prompt at each gate, but scope which test is authoritative:

- **Registry gate — Tests B & C.** Relevant tests / test patch / golden patch are absent, so
  Test A (necessity) is `UNVERIFIED` by the prompt's own rule. Judge **Test B (non-guessable)**
  and **Test C (realistic)** now, and STOP the gate on a clear B or C failure.
- **Artifacts gate — Test A.** The relevant tests, test patch, and golden patch now exist, so
  run the full prompt and resolve **Test A (necessity)** — is each resolution actually required
  to pass a relevant test? STOP the artifacts gate on a Test A failure.
- **Empirical confirmation — Check 1.** Check 1 is the no-clarification / no-resolutions agent
  run, which the prompt calls the *decisive* evidence for Test B. If Check 1 returns
  `FAIL_GUESSABLE`, eval 7 Test B is FALSE for the blocker(s) the agent solved, regardless of
  the earlier static verdict.

---

## 1. eval_test_list
[GATE: artifacts]

Your job is to determine whether you can find the tests from the relevant test list within the test patch.

Inputs:
Relevant tests:
{{relevant_tests_list}}

Test Patch:
{{test_patch_contents}}

Workflow:
If you cannot find one or more relevant tests within the test patch, return FALSE
If you can find all relevant tests within the test patch, return TRUE

Output:
<TRUE/FALSE - if FALSE, explain which tests are missing>

Example:
FALSE - could not find test/units/plugins/connection/test_winrm.py::TestWinRMKerbAuth::test_kinit_success_pexpect[options3-expected3] within the test patch.


Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 2. eval_test_relevance
[GATE: artifacts]

Determine whether each blocker resolution is VALIDATED by the relevant tests. A resolution is validated if a relevant test would FAIL when the resolution is implemented WRONG. Default to TRUE. Return FALSE only when you can demonstrate a plausible WRONG implementation that would still PASS the relevant tests.

Inputs:
Blocker Registry (use blocker_resolution in each blocker):
{{full_blocker_registry}}
Test Patch:
{{test_patch_contents}}
Relevant Tests:
{{relevant_tests_list}}

THE BAR (apply strictly):
- A resolution is NOT validated (return FALSE) only if you can name a concrete, plausible INCORRECT implementation of that resolution that would STILL PASS all relevant tests. To fail you MUST state that wrong implementation and show why the test passes anyway - for example: the test compares a value to itself (so a wrong value passes); the test asserts only a count or total that a wrong behavior also produces; or no relevant test touches the resolution's outcome at all.
- A resolution IS validated (return TRUE) if ANY wrong implementation of it would cause a relevant test to fail - even if the test is indirect, checks only the OUTCOME, or does not name the implementation mechanism.

Do NOT return FALSE for any of these:
- A test that does not verify every edge case or aspect - UNLESS you can name a specific wrong resolution that passes because of the gap (as in the truthiness/`value || null` case).
- The absence of a test that names a specific implementation MECHANISM (a particular function, hook, state technique, or call site). If the resolution's required OUTCOME is checked such that a wrong outcome fails, the mechanism does not need to be tested.
- "Could not find a test for [mechanism]." Reframe it: is the resolution's OUTCOME validated? Only fail if a wrong outcome would pass.

Workflow (per resolution):
1. Identify the resolution's required OUTCOME - the observable result that must hold if it is implemented correctly.
2. Find the relevant test(s) that touch that outcome.
3. Ask: could a plausible WRONG implementation still pass them? If yes, return FALSE and exhibit the wrong implementation plus why the test passes. If no, return TRUE.

Output:
<TRUE/FALSE - if FALSE, name the blocker and state the concrete wrong implementation that would still pass, quoting the relevant test that fails to catch it>

Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 3. eval_golden_patch
[GATE: artifacts]

Your job is to determine whether the golden patch (1) implements everything the task requires, AND (2) contains nothing beyond what the task requires.

The golden patch must implement the problem statement, the requirements, the public interface (if any), and every blocker resolution in the blocker registry. The problem statement, requirements, and public interface may contain ambiguities or blockers; these are described in the blocker registry. Read each blocker description to understand the blocker, and treat its resolution as the intended solution.

The golden patch must ALSO be limited to those changes. Every modification in the patch must trace to one of: a requirement, the public interface, a blocker resolution, or a change directly necessary to support one of those (e.g., a helper function, an import, or a refactor of the code path being modified). A change that does not trace to the task — for example, a modification to an unrelated file, module, or behavior — is out of scope, even if it applies cleanly and does not break any test.

The in-scope source files are defined by the original golden patch and the setup patch: the golden patch should modify only files that one of those two also modifies. The original golden patch is the pre-blocker solution and establishes which source files the task legitimately touches; the setup patch contains any blocker-supporting source changes.

Inputs:
Original golden patch (defines the in-scope source files — the files the obstructed golden patch is allowed to modify):
{{golden_patch_init}}

Setup patch (any blocker-supporting source changes — also in scope; may be empty):
{{setup_patch}}

Problem Statement:
{{problem_statement_modified}}

Requirements:
{{requirements_modified}}

Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}

Golden Patch:
{{golden_patch_contents}}

Workflow:
1. Completeness: For each requirement, public interface element, and blocker resolution, check that it is implemented in the golden patch. If one or more are missing, return FALSE.
2. File scope: List every file the golden patch modifies. List every file modified by the original golden patch and by the setup patch. If the golden patch modifies any file that neither the original golden patch nor the setup patch touches, return FALSE and name the out-of-scope file(s).
3. Change scope: For each remaining change, check that it traces to a requirement, the public interface, a blocker resolution, or a change directly necessary to support one of those. If any change does not trace to the task, return FALSE and describe it. Do NOT flag helper functions, imports, or refactors that directly support an in-scope change.
4. If the patch implements everything required AND contains no out-of-scope or extraneous changes, return TRUE.

Report ALL applicable failures, not just the first one found (e.g., if a resolution is missing AND an unrelated file is modified, cite both).

Output:
<TRUE/FALSE>
- If FALSE because something is missing: state which requirement or blocker resolution is missing, where it should appear, and the missing implementation details.
- If FALSE because of an out-of-scope or extraneous change: name the file and/or change and explain why it does not trace to the problem statement, requirements, public interface, or any blocker resolution.

Examples:
FALSE - golden patch did not implement the resolution for <blocker_title>, which requires ... It also did not implement the requirement where ...
FALSE - golden patch modifies <path/to/unrelated_file>, which neither the original golden patch nor the setup patch modifies, and the change (<brief description>) does not trace to any requirement, interface, or blocker resolution.

Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 4. eval_blocker_type
[GATE: registry] — includes CONDITION 1 (contamination), previously dropped from the paraphrase.

Determine, for each blocker in the registry, whether it is INVALID by contamination or has a CLEARLY WRONG type tag. Default to TRUE (valid and correctly typed). Return FALSE only when one of the two narrow, quotable conditions below is met.

You are given the original (unobstructed) and modified problem statement / requirements / interfaces, the blocker registry, and the golden patch. Do NOT evaluate the obstruction-area field; it is not graded.

Return FALSE only if ANY blocker meets ONE of these two conditions:

CONDITION 1 - CONTAMINATION (resolution derivable from the spec text)

For MISSING-PARAMETERS and AMBIGUOUS blockers:
The blocker's specific answer appears as explicit, near-identical text in the modified problem statement, requirements, or interfaces. To fail, quote (a) the resolution and (b) the spec sentence; they must state the SAME thing.

For CONTRADICTORY blockers (read carefully - this is where over-flagging happens):
The resolution of a contradictory blocker is, BY DESIGN, one of the conflicting options stated in the spec. Therefore finding the resolution's value or wording in the spec is NOT contamination by itself - the whole point of a contradiction is that the candidate answers are visible and in conflict.
- It is contamination ONLY if the spec also contains language that marks which conflicting side is AUTHORITATIVE or INTENDED - e.g., "always", "even when", "regardless of", "in all cases", "takes precedence", "overrides", "must ... even if". Such language lets an agent determine the answer without asking.
- To fail a contradictory blocker on contamination you MUST quote that authoritative/overriding statement (not merely the value). If both conflicting options appear but NEITHER is marked as winning, the agent still cannot tell which is intended - return TRUE (valid by design).

For ALL types: do NOT fail merely because the answer is "guessable", "inferable", or a "reasonable default". Guessability is a holistic judgment evaluated elsewhere, not here. Only an explicit, quotable giveaway (or, for contradictions, an explicit authoritative marker) counts.

CONDITION 2 - CLEARLY WRONG TYPE TAG
Return FALSE only for an unambiguous mis-tag:
- Tagged CONTRADICTORY but there is no genuine conflict. Fail ONLY if the statements are co-satisfiable under EVERY reasonable reading. If the blocker's description gives a plausible basis for the conflict - including domain- or framework-specific tension (e.g., a framework that requires state to trigger a re-render conflicting with a "no local state" rule) - the tag is valid; return TRUE. Do not overturn a contradictory tag on a surface reading that ignores the mechanism the description names.
- Tagged MISSING PARAMETERS but NO specific value is absent at all (the blocker is purely a behavioral choice).
- Tagged AMBIGUOUS but it is purely one unspecified value with no behavioral choice.
Do NOT fail dual-readable cases: if a blocker involves BOTH a missing value AND a behavioral choice, either tag is acceptable - return TRUE. Quote the blocker text and the relevant spec text before failing.

Do NOT return FALSE for any of the following - none are judged in this check:
- "Not critical", "gotcha", "small or weak search space", or any subjective validity concern.
- Incomplete or incorrect obstruction-area lists.
- Imperfect, paraphrased, or semantically loose wording.
- A resolution being more complete or specific than its description.

Blocker/Obstruction Type Definitions:
1. Missing Parameters - a required value that is not specified and cannot be reasonably guessed.
2. Ambiguous Requirements - underspecified behavior with multiple valid implementations.
3. Contradictory Requirements - conflicting instructions that require clarification. (Both sides of the conflict appearing in the spec is expected and correct.)

Inputs:
Original Problem Statement:
{{original_problem_statement}}
Original Requirements:
{{original_requirements}}
Original Public Interface:
{{original_interfaces}}
Modified Problem Statement:
{{problem_statement_modified}}
Modified Requirements:
{{requirements_modified}}
Modified Public Interface:
{{interfaces_modified}}
Blocker Registry:
{{full_blocker_registry}}
Golden Patch:
{{golden_patch_contents}}

Workflow (per blocker):
1. Identify the blocker's type.
2. Contamination (Condition 1): apply the type-specific rule above. For contradictory blockers, fail ONLY if you can quote an authoritative/overriding statement that resolves the conflict; the conflicting values merely being present is not contamination. For other types, fail only on a verbatim giveaway. Never fail for guessability.
3. Type (Condition 2): fail only for an unambiguous mis-tag; apply the dual-readable pass rule and the plausible-basis rule for contradictions. Quote the blocker and spec text before failing.
4. Ignore obstruction areas, criticality, gotcha, search-space size, and guessability entirely.
5. If no blocker meets Condition 1 or Condition 2 with the required quotes, return TRUE.

Output:
<TRUE/FALSE - if FALSE, name the blocker title, state whether the issue is contamination or type, and include the quoted blocker text plus the quoted spec passage (for contradiction contamination, the quoted authoritative/overriding statement)>

Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 5. eval_blocker_independence ⭐
[GATE: registry]

Your job is to determine whether the blocker resolutions are independent. You will be given an original problem statement, requirements, public interface without any ambiguities.  You will also be given modified problem statement, requirements, public interface which contain blockers. These blockers are further described within the blocker registry which contains a blocker description and a resolution to the blocker.  You will also be given the golden patch which is the solution to the modified problem statement, requirements, public interface and blocker resolutions. 


Blocker Independence Criteria:
1. Resolving one blocker doesn't reveal or hint at others.
2. The blocker resolution of one blocker must not contain the resolution fully or partially to another blocker.

Examples:

Not Dependent:
Blocker 1 Description: In the problem statement the requirement establishes that timeout values must align with the task execution subsystem’s timer granularity to ensure reliable enforcement. However, the specific granularity to use for larger timeout values is not defined.
Blocker 1 Resolution: Timeout values must enforce 10-second granularity for all enabled values greater than or equal to 10. Any enabled timeout >= 10 that is not an exact multiple of 10 must be rejected during semantic validation. Values 0 and 5–9 are exempt from the granularity rule.

Blocker 2 Description: In the problem statement the requirement establishes that a minimum timeout value must be enforced to prevent timeouts that are too short and cause false positives or system instability. Very short timeouts may trigger prematurely due to network latency, system load, or other transient conditions. This minimum threshold must be validated to ensure reliable operation, but the specific minimum value is not specified.
Blocker 2 Resolution: The minimum allowed timeout value is exactly 5 seconds when the timeout is enabled. A value of 0 is always valid and disables the timeout. Any value from 1 to 4 must be rejected during semantic validation with a deterministic error indicating that enabled timeouts must be either 0 or at least 5 seconds.

These are not dependent because one covers granularity for values greater than 10 and some exceptions. The other covers the minimum allowed timeout with the meaning of 0 timeout.

Dependent:
Blocker 1 Description: In the problem statement the requirement establishes that timeout values must align with the task execution subsystem’s timer granularity to ensure reliable enforcement. However, the specific granularity to use for larger timeout values is not defined.
Blocker 1 Resolution: Timeout values must enforce 10-second granularity for all enabled values greater than or equal to 10. Any enabled timeout >= 10 that is not an exact multiple of 10 must be rejected during semantic validation. Values 0 and 5–9 are exempt from the granularity rule because 0 means disabled and 5 is the minumum timeout.

Blocker 2 Description: In the problem statement the requirement establishes that a minimum timeout value must be enforced to prevent timeouts that are too short and cause false positives or system instability. Very short timeouts may trigger prematurely due to network latency, system load, or other transient conditions. This minimum threshold must be validated to ensure reliable operation, but the specific minimum value is not specified.
Blocker 2 Resolution: The minimum allowed timeout value is exactly 5 seconds when the timeout is enabled. A value of 0 is always valid and disables the timeout. Any value from 1 to 4 must be rejected during semantic validation with a deterministic error indicating that enabled timeouts must be either 0 or at least 5 seconds.

These are dependent because the first resolution leaks the significance of 0 and 5 which is important for resolution 2..

Inputs:
Original Problem Statement:
{{original_problem_statement}}

Original Requirements:
{{original_requirements}}

Original Public Interface:
{{original_interfaces}}

Modified Problem Statement:
{{problem_statement_modified}}

Modified Requirements:
{{requirements_modified}}

Modified Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}

Golden Patch for modified Problem statement, requirements and public interface plus blocker resolutions):
{{golden_patch_contents}}

Workflow:
1. Examine each blocker within the blocker registry carefully and locate their blocker resolutions.
2. Determine whether the blocker resolutions are independent. If they are independent, return TRUE. If not return FALSE and explain what is the overlap or dependence.


Output:
<TRUE/FALSE - if FALSE, explain which are the blockers that are dependent with each other and why>

Example:
Example 1:
FALSE - blocker 1 and blocker 2 share the same resolution ....

Example 2:
FALSE - blocker 2 hints at the resolution for blocker 1 by ...


Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 6. eval_blocker_objective
[GATE: registry]

Your job is to determine whether the blocker resolutions are objective. You will be given an original problem statement, requirements, public interface without any ambiguities.  You will also be given modified problem statement, requirements, public interface which contain blockers. These blockers are further described within the blocker registry which contains a blocker description and a resolution to the blocker.  You will also be given the golden patch which is the solution to the modified problem statement, requirements, public interface and blocker resolutions. 


CRITICAL SCOPE LIMIT - read first:
This eval checks ONLY whether each blocker resolution is unique / single-valued (one correct answer vs. vague/subjective). You are given the golden patch as context, but you must NOT judge whether the patch implements the resolution correctly, whether the resolution "contradicts" the patch, or whether the code captures the right value/behavior. That is a different eval's job and is the most common way this eval fires wrongly. If your reasoning starts comparing a resolution to what the golden patch does, STOP and return TRUE for that blocker. The only question here is: does the resolution have a single correct answer, or could many different answers be equally valid?

A specific, single-valued resolution is OBJECTIVE even if its value seems unusual or arbitrary. Only fire when the resolution itself genuinely admits multiple equally-valid answers (it is vague or subjective), NOT when you believe the patch implements it incorrectly.

Blocker Resolution Objective Criteria:
1. The blocker has an objectively correct resolution. The blocker resolution should be unique or in a single format and should not be vague/subjective.
2. The blocker resolution should be unique or in a single format and should not be vague/subjective.
3. There cannot be multiple interpretations to the blocker resolution

Inputs:
Original Problem Statement:
{{original_problem_statement}}

Original Requirements:
{{original_requirements}}

Original Public Interface:
{{original_interfaces}}

Modified Problem Statement:
{{problem_statement_modified}}

Modified Requirements:
{{requirements_modified}}

Modified Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}

Golden Patch for modified Problem statement, requirements and public interface plus blocker resolutions):
{{golden_patch_contents}}

Workflow:
1. Examine each blocker within the blocker registry carefully and locate their blocker resolutions.
2. Determine whether the blocker resolutions are objective. If they are objective, return TRUE. If not return FALSE and explain what is not objective.


Output:
<TRUE/FALSE - if FALSE, explain which are the blocker resolutions that are not objective and why>

Example:
Example 1:
FALSE - blocker 1 resolution is not objective because ...


Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 7. eval_blocker_critical_implementation ⭐ (SPLIT — see "Eval 7 split" above)
[GATE: registry (Test B+C) → artifacts (Test A) → Check 1 (empirical Test B)]

You are auditing the blockers in a software-engineering task. Each blocker is an intentionally injected gap (a missing parameter, an ambiguity, or a contradiction) whose purpose is to FORCE an AI agent to ask a clarifying question before it can solve the task correctly. Your job is to decide whether EVERY blocker is genuinely effective.

A blocker is effective only if it passes BOTH core tests:
(A) NECESSARY — its resolution is actually required to pass the relevant tests, and
(B) NON-GUESSABLE — an agent cannot reliably arrive at the correct resolution without asking.
A blocker must also rest on a (C) REALISTIC requirement, but this is a high-bar backstop, not the main check.

Return FALSE if ANY blocker fails ANY of (A), (B), or (C). Return TRUE only if every blocker passes all three.

Inputs:
Original Problem Statement:
{{original_problem_statement}}
Original Requirements:
{{original_requirements}}
Original Public Interface:
{{original_interfaces}}
Modified Problem Statement (contains the blockers):
{{problem_statement_modified}}
Modified Requirements:
{{requirements_modified}}
Modified Public Interface:
{{interfaces_modified}}
Blocker Registry (each blocker's type, description, resolution, trigger questions):
{{full_blocker_registry}}
Relevant tests (the ONLY tests used to validate this task):
{{relevant_tests_list}}
Test patch (the actual test code — read what each test asserts):
{{test_patch_contents}}
Golden patch (the reference implementation of all resolutions):
{{golden_patch_contents}}

Diffing the original vs modified specs tells you what information was REMOVED to create each blocker — that removed information is what the agent must ask about. The relevant tests + test patch tell you what is actually checked. The golden patch tells you the intended resolution.

--- Test A: NECESSARY (resolution is required to pass the relevant tests) ---
For each blocker:
1. Find the relevant test(s) meant to validate its resolution. Read what they actually assert.
2. Imagine the most plausible implementation an agent would produce WITHOUT asking — it leaves the removed detail out, or fills it with the most natural default/guess.
3. Would at least one relevant test FAIL under that implementation?
   - YES -> the resolution is necessary (passes Test A).
   - NO (the unresolved or wrongly-guessed implementation still passes every relevant test) -> the blocker is NOT necessary -> FALSE.
4. If NO relevant test asserts the behavior the resolution controls, the resolution cannot be necessary -> FALSE.
Notes:
- Necessity is about observable, test-checked behavior, NOT about whether the resolution names a specific mechanism. A resolution that over-specifies an implementation detail (a particular method name or call site) beyond what the test checks is NOT a failure here, as long as the core behavior it describes is required to pass a relevant test. Over-specification is at most a minor issue; do not return FALSE for it.
- Error-message / error-type / output-order / formatting details are necessary ONLY IF a relevant test asserts them. If a test checks the exact message or order, the blocker is necessary; if no test does, it fails Test A.
- If you genuinely cannot tell from the provided code whether a wrong implementation would still pass (e.g., the behavior depends on repository code not shown here), do NOT assert necessity and do NOT silently pass. Mark that blocker UNVERIFIED and explain what you would need to check.

--- Test B: NON-GUESSABLE (agent cannot derive the answer without asking) ---
For each blocker, decide whether a competent agent could reach the EXACT correct resolution WITHOUT asking, using only: the modified spec, the repository identifiers/conventions visible in the patches and interfaces, the relevant tests, and ordinary engineering knowledge and obvious defaults. The blocker FAILS Test B (return FALSE) if any of these holds:
- An obvious default would be correct (e.g., "en" for a default language, UTF-8 for encoding, the only sensible error type).
- The answer is reconstructable from naming/conventions or visible identifiers (e.g., an error code "INC" sitting next to a "LoginIncomplete" error; a property name implied by an existing interface).
- A plausible wrong guess would still pass the relevant tests (this overlaps Test A and is strong evidence the blocker is both guessable and unnecessary).
- The modified spec, requirements, interfaces, or golden patch effectively reveal the resolution.
Carve-outs — do NOT fail Test B for these:
- CONTRADICTORY blockers: both conflicting options being visible in the spec is BY DESIGN and is not "guessable." It is guessable only if the agent could determine WHICH option is intended without asking.
- A small but risky search space (e.g., a boolean where the wrong choice crashes, or a 2-value enum where context makes one risky) is a MINOR weakness, not an automatic Test-B failure — pass it unless one option is clearly the obvious/derivable answer.
The strongest guessability evidence is empirical: if a "no-clarification / no-ask" agent run is available and it reconstructs the resolution on its own, that is decisive — the blocker fails Test B.

--- Test C: REALISTIC (high-bar backstop) ---
The requirement that creates the blocker must be something a real product team could plausibly write. CONTRIVED IS A HIGH BAR — default to realistic. Return FALSE on realism ONLY when a requirement serves NO conceivable product purpose (clearest case: banning natural domain terminology, e.g. forbidding "email"/"contact" in an email app). If you can state ANY plausible product reason the requirement might exist, it is realistic.
Do NOT treat any of these as contrived (none of them are):
- An unusual, specific, or seemingly-arbitrary value, threshold, version, or rule — you cannot judge from outside whether it is realistic for this project; assume it is.
- Intentionally-planted ambiguity/contradiction, phrasing that reads as "engineered to force a question," or a requirement that gives the developer freedom to choose. Blockers are injected on purpose — that is the task. Never cite "artificial", "planted", "manufactured", "no team would write this just to make a blocker", or "the spec gives freedom to choose" as grounds.
- A "meta/descriptive" requirement, or one you judge poorly-designed or risky downstream. Whether a requirement is well-designed is not your call.
- A missing-parameter resolution whose value looks arbitrary — check whether the requirement that OMITS the value is unrealistic, not whether the value itself is unusual.

Workflow:
1. Diff original vs modified specs to identify what each blocker removed or obscured.
2. For each blocker, run Test A, then Test B, then Test C, citing the specific relevant test(s) by name for Test A.
3. If any blocker clearly fails any test, the task fails.

Output (plaintext only, no markdown):
- First line: exactly `TRUE` (every blocker passes all tests, or only has UNVERIFIED notes with no clear failure) or `FALSE - <one short sentence naming the failing blocker and which test failed: A/necessity, B/guessable, or C/realism>`.
- Then one line per blocker: `<blocker_name>` -> PASS, or FAIL (which test + why, citing the relevant test for necessity), or UNVERIFIED (necessity needs execution or repo context not provided here).

Example first lines:
FALSE - blocker `external_artist_image_fallback` fails Test A: its only relevant test passes even without the resolution because the existing reader-fallback already returns the placeholder.
FALSE - blocker `default_language_unspecified` fails Test B: "en" is the obvious default an agent would use without asking.
TRUE

## 8. eval_blocker_descriptions
[GATE: registry]

You must judge the `blocker_description` and `blocker_resolution` of each blocker in the blocker registry against the actual task contents, applying the project's grading rubric. You will be given a JSON blocker registry (3 to 5 blockers) plus the modified problem statement, requirements, and public interfaces.

Return FALSE only when a blocker registry entry meets the FAILING bar below. Many imperfections are explicitly NON-FAILING and must return TRUE. Read both lists carefully before deciding.

FAILING criteria (return FALSE if ANY blocker meets ANY of these):
- The description identifies a fundamentally different issue than the actual blocker (the wrong ambiguity / contradiction / missing value, not merely an imperfectly worded version of the right one).
- The description reveals the resolution (it states the answer the agent is supposed to ask for).
- The description is self-referential: it explicitly states that task content was modified, removed, or added FOR THE PURPOSE of creating the blocker (e.g., "...because the definition was removed to create this blocker", "...since the field was deleted from the spec"). Referencing the deliberate act of modification is what fails.
  IMPORTANT - describing the CURRENT STATE of the spec is NOT self-referential and must return TRUE: phrases like "the requirements no longer specify X", "the spec does not define Y", "X is not stated", "there is no longer a defined Z" describe what the spec currently does or does not say. They do NOT reference an act of modification and are PERFECTLY ACCEPTABLE. Only fail if the text explicitly attributes the gap to a deliberate edit made to manufacture the blocker. When in doubt about whether wording is "state-of-the-spec" vs "act-of-modification", treat it as state-of-the-spec and return TRUE.
- The resolution does not actually resolve the blocker, OR it solves a clearly different problem than the description identifies, OR it is nonsensical for the described issue.
- The resolution is self-referential, OR it reveals/hints at another blocker's answer such that the agent could resolve that other blocker without asking.

NON-FAILING - return TRUE for these (do NOT fail on them):
- The description does not perfectly describe the blocker, but the mismatch is small or semantic. Only a FUNDAMENTAL mis-description fails.
- The description paraphrases or restates a requirement in different words. Matching exact wording is NOT required; a faithful paraphrase is correct.
- The description or resolution references a concept, symbol, function, file, class, or build artifact from the codebase that is not spelled out in the problem statement, requirements, or interfaces. Blockers may legitimately reference the codebase. Do NOT fail a blocker merely because a referenced element is absent from the prose spec.
- For CONTRADICTORY blockers: both sides of the conflict appearing in the problem statement / requirements / interfaces is BY DESIGN and correct. Do NOT flag a contradictory blocker as invalid or "not a real contradiction" merely because both conflicting statements are visible, and do NOT dispute which of several possible contradictions the description names. Identifying one valid conflict is sufficient.
- The resolution specifies additional values, parameters, behaviors, or call sites needed to fully resolve the blocker. Resolutions are expected to be more complete than the one-line description. Do NOT flag resolution content as "out of scope" simply because it covers more than the description's summary; only flag it if it solves a different problem than described or contradicts the description.
- Categorizing the blocker TYPE (missing parameter vs ambiguous vs contradictory) is evaluated elsewhere. If the description accurately describes the blocker but only the type tag seems off, return TRUE here.

GROUNDING REQUIREMENT (mandatory before returning FALSE):
Before failing any blocker you MUST (a) quote the exact description and/or resolution text at issue, and (b) quote the exact problem-statement / requirements / interface passage you believe it mis-describes or conflicts with. If you cannot point to a specific quoted passage that demonstrates a FAILING criterion above, return TRUE. Do not fail based on a general impression, an inferred intent, or an assumed contradiction you cannot quote.

Blocker/Obstruction Type Definitions (use ONLY to judge whether the description matches the blocker; not independent fail conditions):
1. Missing Parameters - Required values that are not specified (a numeric parameter, threshold, limit, specific string key, or special value the implementation needs that cannot be reasonably guessed).
2. Ambiguous Requirements - Underspecified behavior where multiple valid implementations exist (e.g., "handle errors" could mean log, raise, or return None).
3. Contradictory Requirements - Conflicting instructions that require clarification (e.g., the problem statement says "use X" but the requirements/code imply "use Y").

Inputs:
Modified Problem Statement:
{{problem_statement_modified}}

Modified Requirements:
{{requirements_modified}}

Modified Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}

Workflow (per blocker):
1. Read the description, resolution, and obstruction area.
2. Decide whether the description identifies the RIGHT blocker for its area. A faithful-but-imperfect description passes; only a fundamentally wrong one fails, and only with a quoted mismatch.
3. Check the description does not reveal the resolution and is not self-referential.
4. Check the resolution actually resolves the described blocker, is not self-referential, and does not reveal another blocker. A more-complete resolution is fine; only a different-problem, contradictory, or nonsensical resolution fails, and only with a quoted mismatch.
5. Apply the NON-FAILING list. If the only issues present are non-failing, return TRUE.
6. Return FALSE only if some blocker meets a FAILING criterion with a quoted demonstration.

Output:
<TRUE/FALSE - if FALSE, name the blocker title, state whether the issue is the description, the resolution, or their alignment, and include the quoted description/resolution text plus the quoted spec passage that demonstrates the failure>

Only output plaintext and no markdown. Use the blocker title when referencing a blocker.

## 9. eval_blocker_questions
[GATE: registry]

Examine the problem statement, requirements, public interfaces and blocker registry. Pay attention to the blocker descriptions and trigger questions. Determine if the acceptable questions are appropriate followup questions that an LLM would ask to figure out the ambiguous task presented in the request. Note that the blocker registry frames each ambiguity separately, so each set of trigger questions should only be evaluated in terms of that blocker. 

Main request includes: problem statement, requirements and public interfaces

Inputs:
Modified Problem Statement:
{{problem_statement_modified}}

Modified Requirements:
{{requirements_modified}}

Modified Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}


Example of acceptable question that is not appropriate:
Question: Which teams from the England League finished both at the top and bottom three positions of the table in 2016 with high offensive performance, ranked by their top-tier competitive status for that season? Return team name and rank.
Blocker description: The term "high offensive performance" lacks specific criteria. No threshold value, metric definition (goals scored, shots on target, possession in attacking third), or comparison baseline (league average, historical data) is provided.
Blocker trigger question 1: Should the performance from the England league include home matches only, away matches only, or both?
Blocker trigger question 2: Are you asking for the overall league standings or home/away-specific standings?
Blocker trigger question 3: Which table format should be used to determine team positions?
Blocker trigger question 4: Do you want combined results for home and away performance or separated home/away performance?

Question 1 and 4 are appropriate since it is asking directly about the criteria for performance. Question 2 and 3 are not appropriate since question 2 asks about league standings which has nothing to do with performance and blocker 3 is about team positions and table format which also does not follw up about high offensive performance.

Another Example: 
Question: How many grant-eligible schools that have students enrolled to FRPM in grades ranging from Kindergarten through 8th grade across all academic years? List each city along with the count of qualifying schools and also include the number of magnet schools currently active in the same city.
Blocker Description: The column GSserved represents the enrollment but the values in the column are unclear.
Trigger question: Which value from the schools.GSserved column should I use to identify middle school enrollment?

The trigger question is not appropriate because it talks about middle school enrollment when the question is about K through 8th grade.

Workflow:
- Return TRUE if each trigger question is relevant to its blocker description. 
- Return FALSE if any of the trigger questions references a term or concept that is not relevant to that specific blocker.
- Return FALSE if any trigger question is appropriate for another blocker.
- Return FALSE if any trigger question is repeated word for word more than once.
- Return FALSE if more than one question is being asked in a single question space.
- Return FALSE if any trigger question is not dierectly relevant to asking a clarifying question about the blocker.


Output:
<TRUE/FALSE. -reason if false>

Example output:
FALSE - Blocker `title_of_blocker` trigger question 3 is not relevant because it references a term that is not present...

Remember, do not generate any markdown. I just want plaintext.

## 10. eval_blocker_distribution ⭐
[GATE: registry]

You must check that the blocker entries for the input JSON matches the blocker distribution. The JSON has these fields that you must look into to see if the JSON matches the distribution. Each object should have a "criteria_category" which can be "contradictory requirements", "ambiguous requirements" or "missing parameters"


Input JSON (blocker registry): {{full_blocker_registry}}

Required Blocker Distribution:
{{distribution_of_blockers}}

Workflow:
1.  Examine the criteria category for each blocker within the blocker registry input JSON.
2. If the blocker criteria categories for all the blockers in the provided input JSON matches the required blocker distribution then return TRUE, 
3. If at least one blocker criteria category from the input JSON causes the blocker distribution in the blocker registry to not match the required blocker distribution, return FALSE.  
Note: Do not worry about the ordering. The order does not matter. As long as there is an entry for each blocker criteria category required in the distribution then return TRUE. 


Output: <TRUE/FALSE - state why the distribution of the blocker criteria category within the blocker registry input JSON is different than the required blocker distribution>

Example:
FALSE - The blocker distribution requires 'a' amount of 'x' type blocker and 'b' amount of 'y' type blocker but the blocker registry does not match, instead it contains ...

Remember, do not generate any markdown. I just want plaintext.

## 11. eval_self_reference
[GATE: registry]

You must check that the task contents do not contain self referencing statement that insinuates that the data has been purposefully modified. You will be given a Problem Statement, Requirements, Public Interfaces and Blocker Registry JSON input that contains 3 to 5 objects where each object is a blocker. 

Self referential content:
- A field is self referential ONLY if it explicitly states or clearly implies that content was deliberately modified, added, or removed FOR THE PURPOSE of creating the blocker (e.g., "the definition was removed to create this", "a requirement was added to introduce a conflict"). Referencing the deliberate act of modification is what fails.
- IMPORTANT: describing the CURRENT STATE of the spec is NOT self referential and must PASS. Phrases like "the requirements no longer specify X", "X is not defined", "the spec does not state Y", "there is no longer a defined Z" describe what the spec currently does or does not contain. They do NOT reference an act of modification. Do NOT flag them. Only flag when the text attributes the gap to a deliberate edit made to manufacture the blocker. When in doubt, treat wording as state-of-the-spec and PASS.
- Any field in the blocker registry is also self referential if it references the problem statement as PS since this is an unnatural reference.

Inputs:
Modified Problem Statement:
{{problem_statement_modified}}

Modified Requirements:
{{requirements_modified}}

Modified Public Interface:
{{interfaces_modified}}

Blocker Registry:
{{full_blocker_registry}}


Workflow:
- Examine the Problem Statement. Return FALSE if the problem statement contains a self-referencing statement.
- Examine the Requirements. Return FALSE if the requirements contains a self-referencing statement.
- Examine the Interfaces. Return FALSE if the interfaces contains a self-referencing statement.
- Examine the blocker descriptions. Return FALSE if any blocker description contains a self-referencing statement.
- Examine the blocker resolutions. Return FALSE if any blocker resolution contains a self-referencing statement.
- Examine the blocker trigger questions. Return FALSE if any of the trigger questions contains a self-referencing statement.
- If none of the contents contain self referencing statements, return TRUE.

Note: A blocker description describing why there is a blocker is not a self reference, so if it describes that there is a contradiction, that's fine. But it should not say that requirements were added to create a contradiction.. It just must not state that content was purposefully modified. Similarly, a blocker resolution that provides the solution to the blocker is not a self reference. It is only self referential if it claims to fix a purposefully added blocker.

Examples:

Non self referencing:
Blocker_1: "What criteria define a customer as "most important" in this context?", "Which customer segment corresponds to the most important customers in the database?"
Blocker_2: "Which specific currencies qualify as "west European" for filtering customers?", "What is the exact list of currency codes that represent west European currencies in this database?"
Blocker_3: "What consumption threshold defines "deficient consumption" for this analysis?"

These are not self referencing because the questions are only trying to ask about the ambiguity, not actively stating that there was a manually added blocker.

Self referencing:
Blocker_1: "What criteria define a customer as "most important" based on the removal of the definition?"

Note that this is self referencing because "based on the removal of the definition" explicitly references a deliberate modification (something was removed).

NOT self referencing (this would PASS):
Blocker_2: "Since there is no longer a definition of west Europe, which specific currencies qualify as "west European"?"

Although it says "no longer a definition", this only describes the current state of the spec (there is no definition present). It does NOT say the definition was deliberately removed to create a blocker. Phrases like "no longer specify", "no longer defined", "is not stated" describe what the spec currently lacks and are ACCEPTABLE. Only fire if the text explicitly attributes the gap to a deliberate edit (e.g., "the definition was removed to create this blocker").


Self referencing:
Blocker 3 Resolution: Include BOTH fields: auth_method (short form, e.g. 'token') AND auth_method_enum (enum string, e.g. 'METHOD_TOKEN'). Apply both conditions. Example (method fields only): {"auth_method": "token", "auth_method_enum": "METHOD_TOKEN"}. Do NOT use only one form. Do NOT specify auth_id length, key naming, or payload scope, those are resolved by other blockers.

Note that it mentions "resolved by other blockers". This is a self referencing statement.

Not a self reference:
Requirements contain these contradictions:
- "The run-start marker must only be written when `run_delimiter_mode` is `always`."
- "The run-start marker should only be written when `run_delimiter_mode` is `on_restart`."
- "The run-start marker should only be written when `run_delimiter_mode` is `per_session`."

Note that this is not a self reference because the contradictions just exist, which is fine. There is no mention that these were added for the purpose of creating a contradiction



Output: <true/false - list of task contents that contained a self referencing statement>

Example output:
FALSE - the problem statement contained ... The blocker 3 resolution also contained a self referencing statement ...

## 12. eval_request
[GATE: artifacts] — previously unmapped; needs the golden patch to detect embedded solutions.

You are auditing the MAIN REQUEST of a software-engineering task — the modified problem statement, requirements, and public interfaces an agent must implement. The task INTENTIONALLY contains blockers: a blocker is created by making a requirement VAGUE, UNDER-SPECIFIED, or CONTRADICTORY so the agent must ask a clarifying question. THAT VAGUE/UNDER-SPECIFIED LANGUAGE IS SUPPOSED TO BE IN THE SPEC. It is NOT a defect and you must NOT flag it.

You flag the request ONLY when the spec does something the blocker design does not want: reveals the actual answer, hands over the implementation, uses a literal blank, narrates ITSELF or "the task", or states a requirement with no possible product purpose.

THE GATE — apply to every candidate phrase before flagging it:
Does this phrase describe what the SYSTEM/PRODUCT must do — even vaguely, by deferring a value to the project/configuration/standards, or by presenting two options it must choose between?
- YES → it is a (possibly vague or ambiguous) requirement = a blocker. DO NOT FLAG.
- Only flag if instead it (a) reveals the specific answer, (b) spells out the implementation, (c) is a literal placeholder, (d) is unnatural narration about "the task" or the spec's own omissions, or (e) has no conceivable product purpose.

Inputs:
Original Problem Statement: {{original_problem_statement}}
Original Requirements: {{original_requirements}}
Original Public Interface: {{original_interfaces}}
Modified Problem Statement: {{problem_statement_modified}}
Modified Requirements: {{requirements_modified}}
Modified Public Interface: {{interfaces_modified}}
Blocker Registry (resolutions — to detect leakage): {{full_blocker_registry}}
Golden Patch (to detect embedded solution): {{golden_patch_contents}}

DEFECTS TO FLAG — find every genuine instance; quote the text:

1. Resolution leakage (FALSE). For EACH blocker in the registry, check whether the spec states that blocker's SPECIFIC resolution — the actual value, format, or, for a contradiction, which side wins. Flag only when the specific answer is revealed.
   - DO NOT flag the existence of the gap. "the timeout is defined by project configuration" describes the gap (missing-param blocker) → leave. "the timeout is 30 seconds" reveals the answer → flag.
   - Contradiction carve-out: showing BOTH conflicting options is by design and is NOT leakage. Flag only if the spec also says which option is correct / should be used / should be ignored.

2. Embedded solution (FALSE). The spec hands over the actual algorithm/implementation the golden patch contains, so no design work remains. HIGH BAR. Naming a function or class, or describing required behavior, is NOT embedding. Cross-check the golden patch: would a developer still have to design the solution, or is it written out for them?

3. Placeholders (FALSE). Literal [REDACTED], ???, ____, TODO, TBD, XXX used in place of real language.

4. Self-referential / meta phrasing (FALSE) — THE KEY DISTINCTION. Flag only when the spec narrates THE TASK ITSELF or its OWN OMISSIONS in language a real spec would never contain — not when it states a vague requirement or presents a visible ambiguity.
   FLAG (unnatural narration about the task / the spec's gaps):
   - "for this task", "in this task", "the task's <X> policy/behavior"   ← references "the task"
   - "<X> is left unspecified", "intentionally left open"
   - "the requirements do not state / do not specify / do not pin down whether <X>"   ← FLAG only when this is unnatural narration ABOUT the spec. A real spec describes the system; it does not describe its own gaps. If the SAME ambiguity is instead presented as the system's behavior ("the system may do X or Y", "either X or Y is acceptable depending on …"), that is a by-design ambiguous/contradictory blocker → DO NOT FLAG.
   DO NOT FLAG (references the project/system, states a vague requirement, or is ordinary spec structure):
   - "according to the project's <X> policy", "defined by project standards", "supplied by project configuration"   ← references "the project"; value withheld = missing-parameter blocker
   - "offline mode must define whether <X> or <Y>", "the system must <do X> appropriately"   ← vague forward requirement = ambiguous blocker
   - Cross-references to other requirements: "defined in the requirements", "the requirement above", "the sentinel described in the first requirement above"   ← ordinary spec structure, not a gap admission. (Note the direction: "defined IN the requirements" says something IS specified — the opposite of "the requirements do NOT specify".)
   - Boilerplate scope statements: "the required code changes for this task are limited to …", "implement the documented public interfaces"   ← routine scaffolding. Flag "for this task" ONLY when it sits inside a vague or contrived requirement, not in this kind of scope/scaffolding text.
   Discriminator: unnatural narration about "the task" or about what the spec itself does/doesn't say → meta → FLAG. A statement of what the project/system must do, a vague requirement, a visible either/or ambiguity, or a cross-reference to another requirement → blocker or normal structure → LEAVE.

5. No conceivable product purpose (FALSE). A requirement that could serve no real product goal and exists only to force a question (e.g., "the cache size must be a prime number"; banning natural domain terms like "email" in an email app). HIGH BAR. A requirement that merely withholds or defers a value is a missing-parameter blocker, NOT this. If you can state ANY plausible product reason, do not flag.

NEVER FLAG (blocker mechanism or out of scope):
- A requirement being vague, under-specified, or deferring a value to the project/configuration/standards — that is a missing-parameter or ambiguous blocker.
- A planted contradiction, or both sides of a contradiction being visible (see carve-out 1).
- A visible either/or ambiguity presented as the system's behavior.
- A cross-reference to another requirement, or boilerplate scope/scaffolding text.
- An unusual or arbitrary-looking value — you cannot judge a value's realism from outside.
- Whether files/tests exist or commands run — another check's job.

Severity: MAJOR for leakage, embedded solution, placeholders, no-purpose requirements, and meta phrasing that reveals where/what a blocker is. MINOR for isolated awkward wording that leaks/embeds nothing.

Workflow: for each blocker in the registry, locate its gap in the spec and confirm the spec does NOT reveal its resolution; then scan every line for unnatural task/spec self-narration, placeholders, embedded implementation, and no-purpose requirements — applying THE GATE to each candidate before flagging, and checking it against the DO NOT FLAG list (cross-references, scope boilerplate, visible ambiguities, vague forward requirements) before deciding.

Output (plaintext, no markdown):
- First line: exactly TRUE, or  FALSE - <MAJOR|MINOR> <issue type> — "<quoted text>"  (most serious first).
- One additional line per remaining genuine issue, same format.

Example outputs:
FALSE - MAJOR meta phrasing — "the requirements do not state whether to reuse the entity abort classification"
MINOR contrived wording — "for this task"

FALSE - MAJOR leakage — "defaults to the English locale (en)" reveals the missing-parameter resolution

TRUE
