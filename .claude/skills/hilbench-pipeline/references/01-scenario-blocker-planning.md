
# Scenario + Config Lock + Analysis + Blocker Plan (Merged)

Role and objective:
- You are an expert task builder for blocker injection.
- Goal: decide Scenario (1/2) using the Core decision rule, and produce a config-locked blocker plan that can be turned into registries and artifacts later WITHOUT format mistakes.

Shared rules (apply to this step):
- If you WRITE any output file, file content MUST be English-only.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording in any output artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Interpretation Standard (use this everywhere must-ask/guessability is checked):
  - FAIL must-ask only if a capable engineer/agent can reliably infer the exact resolution WITHOUT asking, using defaults, conventions, or simple elimination from visible artifacts.
  - A lucky guess does NOT count as reliably inferable.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

Linter-aligned hard gates (you MUST satisfy these in later steps):
- Patch-visible tests only (hard rule):
  - Treat the provided test patch diff as the authoritative test surface for designing/enforcing blockers.
  - Do NOT rely on or reference tests that exist elsewhere in the codebase but are not visible in the provided test patch diff content.
- Test relevance: every blocker MUST be enforced by at least one concrete, narrow assertion.
  - Scenario 1: enforcement MUST already exist in @test_patch.diff (you cannot rely on new tests).
  - Scenario 2: enforcement MUST be planned as a specific assertion you will add to test_patch_obstructed.diff later.
- Test list resolvability: every test name you plan to list in relevant_tests.txt MUST be discoverable verbatim in the corresponding test patch diff content.
  - Scenario 1: it must appear in $TASK_FILES/test_patch.diff.
  - Scenario 2: it must appear in $DELIVERABLES/test_patch_obstructed.diff.
- Criticality: if a blocker is not enforced anywhere (tests or contract checks), it is NOT critical. Do not create it.
- Objective wording: do NOT write vague resolutions like "expand to concrete keys" or "apply strict syntax". Resolutions must be concrete and uniquely checkable.
- Independence (deterministic): do NOT create blocker resolutions where resolving one implicitly chooses the outcome of another.
  - Hard rule: if blocker A's resolution contains a rule that logically forces a unique choice needed for blocker B (even partially), A and B are NOT independent.
  - Avoid precedence rules embedded in other blockers (e.g., a construction rule that implicitly resolves a separate conflict-handling rule).
- Guessability (reliably inferable) hard gate:
  - Each blocker MUST have at least 3 plausible alternative resolutions (3 is the minimum, not the target).
  - Prefer a larger solution space when possible (aim for 4-6 plausible alternatives) to avoid "2-3 obvious choices" guessability failures.
  - You MUST explain why a capable engineer cannot reliably infer the exact correct one via:
    - obvious industry defaults / "standard spec" assumptions, or
    - simple elimination from visible artifacts.
  - If a blocker reduces to a 2-choice decision (e.g., 0 vs 1 indexing; A vs B precedence; accept vs reject) it is HIGH RISK and should be redesigned.
  - If a blocker reduces to only 2-3 obvious choices/policies, it is HIGH RISK and should be redesigned.
    - Exception: if tests/requirements already force one choice, then it is reliably inferable and must-ask FAIL anyway.

Reviewer lens (hard gate for this step):
- Before writing $DELIVERABLES/plan.md, you MUST act as the reviewer and try to reject your own blocker plan.
- For EACH planned blocker, you must be able to answer "PASS" to all of the following (using only patch-visible evidence):
  - Enforceable: there is a concrete verification anchor (Scenario 1: a narrow assertion visible in @test_patch.diff; Scenario 2: a specific assertion you will add later).
  - Objective: the resolution can be uniquely checked (not vague, not preference-based).
  - Independent: resolving this blocker does not deterministically force another blocker's resolution.
  - Not reliably inferable: the exact resolution is not a default/convention/spec, not eliminable to 1 obvious choice, and not a 2-3 obvious-choice policy.
- If ANY blocker fails any check above, STOP and redesign the blocker set (or switch to Scenario 2 to add narrow tests) before writing plan.md.

Core decision rule (this prompt MUST decide Scenario using this rule):
- Question: Can the current patches fully support the intended blocker distribution WITHOUT patch modification?
  - If YES -> Scenario 1
  - If NO  -> Scenario 2

Narrow Tests signal (for reporting only; NOT the primary scenario rule):
- Narrow Tests = YES if @test_patch.diff contains narrow assertions (exact strings/numbers/formats, specific error types/messages, deterministic edge-case behavior).
- Narrow Tests = NO if tests are broad/generic and do not pin down a uniquely correct behavior.
- IMPORTANT: Narrow Tests = YES does NOT automatically imply Scenario 1. Narrowness may be insufficient for the required blocker distribution.
- IMPORTANT (patch-visible test constraint): when judging whether narrow tests are "sufficient", you MUST use ONLY the narrow assertions visible in the provided test patch diff content (@test_patch.diff). Do NOT rely on any other tests in the codebase.

Config Lock to follow exactly (fill # manually; do NOT guess):
- Total blockers: #
- Type distribution (exact counts):
  - missing_parameter: #
  - ambiguous_requirement: #
  - contradictory_requirement: #

If the config numbers are not provided to you, output:
- CONFIG_MISSING
Then STOP.

CRITICAL plan.md format requirements:
- You MUST write $DELIVERABLES/plan.md by REWRITING the entire file (do NOT append).
- $DELIVERABLES/plan.md MUST use the exact headings/ordering shown in the template below.
- Do NOT add extra sections/headings.
- Do NOT write or modify any other files in this step.

Scenario constraints (apply after scenario is chosen):
- Scenario 1:
  - Text-only injection; do NOT change the logic/content of the original patches.
  - You may only adjust diff headers/context/whitespace if needed for clean apply.
  - Added/removed content lines must not change.
- Scenario 2:
  - You must create narrowness by changing/adding tests and producing obstructed diffs with clean separation later.
  - Output setup_patch.diff only if additional visible setup/environment/codebase changes are required.

Inputs:
- @task_info.txt
- @test_patch.diff
- @golden_patch.diff

Task:
1) Determine Narrow Tests = YES/NO:
   - Quote 5-15 short snippets from @test_patch.diff as evidence.
2) Feasibility check for the config (the primary decision):
   - Using only what is visible in @task_info.txt, @test_patch.diff, and @golden_patch.diff, decide whether you can create EXACTLY the required number/distribution of high-quality, independent, must-ask blockers WITHOUT modifying patches.
   - Scenario 1 feasibility MUST be justified using ONLY narrow assertions visible in @test_patch.diff (patch-visible tests only).
   - Explain which distinct "blockable surfaces" exist (contracts/policies/behaviors) and whether they can support the required blocker count and type mix.
3) Decide Scenario:
   - Answer: "Can current patches fully support the intended blocker distribution without modification?" YES/NO
   - Apply the Core decision rule to choose Scenario 1 or Scenario 2.
   - Hard gate for Scenario 1:
     - You may choose Scenario 1 ONLY if EVERY planned blocker has an explicit verification anchor pointing to a concrete narrow assertion visible in @test_patch.diff.
4) Draft invariants (these MUST be written into $DELIVERABLES/plan.md):
   - List 5-10 invariants you will not violate in later steps (English-only output file contents, no leaks, independence, schema correctness, patch separation, no self-reference).
5) Draft the blocker plan (exact distribution):
   - Propose exactly # blockers matching the distribution.
   - For each blocker, include:
     - Proposed id (snake_case; descriptive; prefer no numeric suffix; use a numeric suffix only if needed to avoid collisions)
     - Proposed type_of_obstruction (UI strings):
       - Missing Parameters
       - Ambiguous requirements
       - Contradictory requirements
     - Type selection sanity check (MUST be consistent with linter expectations):
       - Missing Parameters: one missing concrete value/parameter that must be provided.
       - Ambiguous requirements: multiple plausible implementations remain; NOT a single missing value.
       - Contradictory requirements: two or more requirements cannot simultaneously be satisfied.
      - Hard gate for Contradictory requirements:
        - If you label a blocker as "Contradictory requirements", you MUST be able to name at least 4 distinct, reasonable resolution strategy TYPES that could satisfy the text (not minor variants of the same strategy).
        - If you cannot reach >= 4 strategy types, redesign the contradiction (or reclassify it as Ambiguous requirements).
     - Proposed primary axis (choose one per blocker; diversify):
       - input_validation / output_format / ordering_rules / boundary_behavior / error_handling /
         compatibility_migration / performance_resource_limits / security_compliance_policy
     - Proposed area_of_obstruction (choose 1+):
       - Problem statement / Requirements / Interfaces / CodeBase
     - Quality criteria check (1 bullet each): Critical / Realistic / Objective / Not reliably inferable (via defaults, conventions, or elimination)
     - Independence check: list any other proposed ids whose exact resolution would be deterministically implied if this one were resolved (target: none)
     - Verification anchor (MUST be specific and non-leaky):
       - Scenario 1: cite the exact narrow test evidence in @test_patch.diff that would enforce this blocker after resolution.
       - Scenario 2: name the specific test(s) you will add/modify and the exact assertion shape it will enforce (do NOT include the hidden resolution values).
     - Alternative-resolutions reasoning (required):
       - List at least 3 plausible alternative resolutions.
       - Explain why none can be eliminated using visible artifacts alone (defaults, conventions, or simple elimination).
6) Confirm the type distribution counts match exactly.

Output (in chat):
A) Scenario Decision Summary:
- Narrow Tests: YES / NO
- Can current patches fully support the intended blocker distribution without modification?: YES / NO
- Scenario: Scenario 1 / Scenario 2
- Reason: 3-8 bullets, each with short quoted evidence

B) Blocker Plan Table:
- One row per blocker with the fields above.
- Then:
  - Count confirmation (by type)
  - Independence target confirmation (no deterministic cross-fix)

Also write $DELIVERABLES/plan.md (English-only) with this structure:

# Plan

## Scenario Decision
- Narrow Tests: YES / NO
- Patch support feasibility for intended distribution (no patch modification): YES / NO
- Decision: Scenario 1 / Scenario 2

## Evidence
- <3-8 bullets>

## Config Lock
- Total blockers: <#>
- Type distribution:
  - missing_parameter: <#>
  - ambiguous_requirement: <#>
  - contradictory_requirement: <#>

## Invariants
- <5-10 bullets>

## Blocker Plan (ids + intended type)
- <id> - <Type of obstruction UI string>
  - Type of obstruction UI string MUST be exactly one of:
    - Missing Parameters
    - Ambiguous requirements
    - Contradictory requirements
  - There MUST be exactly <Total blockers> lines in this section.
  - Verification anchor:
    - Scenario 1: <quoted @test_patch.diff snippet reference>
    - Scenario 2: <test name(s) to add + assertion shape (no hidden values)>

Write output file:
- $DELIVERABLES/plan.md

Hard constraints:
- Do NOT write blocker_registry files yet.
- Do NOT write modified_* artifacts yet.
- Do NOT output any *.diff files yet.
Stop immediately after writing $DELIVERABLES/plan.md.
