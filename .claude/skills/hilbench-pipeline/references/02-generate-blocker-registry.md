
# Generate blocker_registry.json and blocker_registry.md (UI schema)

Role and objective:
- You are an expert task builder. Generate the blocker registries as the hidden answer key for the injected blockers.
- Goal: produce machine-parseable JSON + a human-readable MD mirror, with zero format contamination.

Shared rules (apply to this step):
- Any content you WRITE into output files MUST be English-only.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schema or expected outputs.
- Do NOT use self-referential wording anywhere in output artifacts ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: ensure no resolution is reliably inferable via defaults, conventions, or simple elimination from agent-visible artifacts.

CRITICAL format requirements:
- $DELIVERABLES/blocker_registry.json MUST be valid JSON (no trailing commentary, no markdown fences, no extra keys outside the schema).
- $DELIVERABLES/blocker_registry.md MUST be plain markdown text (do NOT embed JSON; do NOT embed code fences with the JSON).
- Do NOT append the summary counts or independence matrix to ANY file (chat only).
- Do NOT paste the full JSON into chat. Only print the summary counts + independence matrix in chat after writing the files.

Inputs:
- $DELIVERABLES/plan.md (Scenario Decision + Config Lock + Blocker Plan ids)
- @task_info.txt
- @test_patch.diff
- @golden_patch.diff

Task:
1) Use $DELIVERABLES/plan.md as the source of truth for:
   - Scenario Decision
   - total blocker count + type distribution
   - planned blocker ids (and their intended type)
2) Write $DELIVERABLES/blocker_registry.json following the UI schema below.
3) Write $DELIVERABLES/blocker_registry.md as a human-readable mirror with identical content.

Hard requirements:
- You MUST generate exactly the ids listed in $DELIVERABLES/plan.md (no extras, no missing).
- The count by type_of_obstruction MUST match the Config Lock in $DELIVERABLES/plan.md exactly.
- If $DELIVERABLES/plan.md is missing the blocker id list or the config numbers, output: PLAN_INVALID then STOP.
- Linter-aligned hard gate (objective + test relevance):
  - Each resolution MUST be specific enough to be verified by tests/contract checks, and MUST NOT include extra detail that tests will not verify.
  - Check 2 solvability gate (design logic):
    - Resolutions must be directly implementable as deterministic rules.
    - Avoid descriptive-only phrasing ("deterministic", "canonical", "standard", "consistent") unless you also specify the exact rule.
    - If the tests/assertions will check string formatting, ordering/tie-breaks, boundary behavior, or error shapes:
      - The resolution MUST include the exact rule, and SHOULD include a short Example and Counterexample.
  - Avoid writing "industry standard" resolutions (Docker/OCI/etc.) unless you can justify (in your own reasoning, not in files) that multiple plausible alternatives remain and none is reliably inferable.
  - Guessability gate:
    - If a resolution matches an obvious industry default/spec with no equally plausible alternatives, treat it as guessable and redesign the blocker.
    - If a resolution is a 2-choice decision (A vs B), treat it as guessable and redesign the blocker.
    - If a resolution is one of only 2-3 obvious policies/choices, treat it as guessable and redesign the blocker (expand the plausible alternative set or change the blocker).
  - Independence gate:
    - Do NOT write resolutions whose rules implicitly choose outcomes for another blocker's resolution (deterministic cross-fix).
    - Hard rule: A resolution MUST NOT specify contract aspects that belong to other blockers' decision spaces, even if you do not mention any other blocker id.
      - Examples of forbidden cross-contract specification (non-exhaustive):
        - response field sets / data shape / mapping rules
        - ordering / sorting / tie-break policies
        - key formats / index naming / storage schema
  - Error-message resolution gate (hard):
    - Avoid using "error type + exact error message" as a blocker resolution.
    - If you use this pattern, you MUST keep it rare:
      - Do NOT create more than ONE blocker whose resolution is an error type + exact error message combo.
    - Do NOT create any blocker whose resolution is ONLY an error message without a concrete error type.
  - Description-to-Type coherence gate (hard):
    - The blocker description MUST match the selected type_of_obstruction.
    - Use these definitions:
      - Contradictory requirements: there are 2+ explicit instructions that cannot all be satisfied simultaneously.
      - Ambiguous requirements: the requirement is underspecified and admits multiple valid interpretations, but does not contain mutually conflicting instructions.
      - Missing Parameters: a concrete value/enum/limit/format choice is missing (not a multi-policy design debate).
    - If you cannot justify "Contradictory requirements" by pointing to two conflicting instructions in the (original) task_info content you are modifying, do NOT label it contradictory.
  - Area-of-obstruction selection gate (hard):
    - Only select areas where YOU actually introduce the ambiguity/contradiction in the modified artifacts later.
    - Do NOT select "Interfaces" unless the ambiguity/contract gap is introduced in modified_public_interfaces.txt.
    - Do NOT select "CodeBase" unless the obstruction truly originates from code behavior/structure rather than the text artifacts.
  - Registry text hygiene gate (hard):
    - Descriptions and resolutions must be clean English with no typos, duplicated fragments, or accidental pasted blocks.
    - If you see obvious grammar/typo artifacts (e.g., "the it") or a corrupted inserted paragraph, STOP and rewrite that blocker entry before writing files.
  - Trigger Question Relevance gate (hard):
    - Each trigger question MUST be relevant only to its own blocker's decision space.
    - Do NOT include extra concepts not required to resolve that blocker.
    - One question per question space:
      - A single trigger question MUST ask exactly ONE distinct decision.
      - Do NOT combine multiple independent decisions in one sentence (e.g., "case sensitivity AND unicode normalization").
    - If you need multiple decisions, split them into separate trigger questions OR redesign the blocker so it only contains one decision space.
  - Description/resolution/question leakage gate (hard):
    - Description must not state, exemplify, or strongly narrow its own resolution.
    - Resolution must be one complete deterministic rule, with no placeholders, alternatives,
      vague defaults, answer-bearing identifier names, or details belonging to another blocker.
    - Trigger questions must be neutral: no leading wording, correct option embedded in an
      either/or list, resolution constants/names/examples, or clues to another blocker.
    - Read description + all trigger questions without the resolution. If the correct answer is
      reliably inferable or fewer than three plausible answers remain, redesign the entry.

Reviewer lens (hard gate for this step):
- Before writing $DELIVERABLES/blocker_registry.json or $DELIVERABLES/blocker_registry.md, you MUST act as the reviewer and try to reject your own registry entries.
- For EACH blocker entry, verify all of the following:
  - Type/description coherence: the description matches the selected type_of_obstruction (contradictory vs ambiguous vs missing parameter).
  - Area correctness: area_of_obstruction only includes areas where the ambiguity/contradiction is actually introduced in the modified artifacts.
  - Independence: resolution text does not embed another blocker's resolution (no copied key formats/index names/precedence rules that belong to other blockers).
  - Not guessable: resolution is not a 2-choice or 2-3 obvious-choice policy, and is not an obvious default/spec.
  - Trigger questions: 3-5 total (MAX 5), each is single-decision, and each is relevant only to this blocker's decision space.
  - Field leakage: description/questions do not reveal or uniquely narrow the resolution, and
    resolution is deterministic, minimal, implementable, and independent.
  - Text hygiene: no typos, no duplicated fragments, no accidental pasted blocks.
- If ANY entry fails any check above, STOP and fix it BEFORE writing output files.

UI schema (MUST follow):
{
  "blockers": [
    {
      "id": "key_validation_error_codes",
      "area_of_obstruction": ["Problem statement", "Requirements"],
      "type_of_obstruction": "Missing Parameters",
      "description": "Standalone explanation of WHY this blocks correct implementation (no leak; no self-reference).",
      "resolution": "The exact answer (values/format/case/spacing). Include Example and Counterexample when helpful.",
      "trigger_questions": [
        "3-5 varied, standalone questions (MAX 5)..."
      ]
    }
  ]
}

Hard rules:
- id: snake_case, unique, descriptive; prefer no numeric suffix (use a numeric suffix only if needed to avoid collisions).
- If you use a numeric suffix, use it sparingly and keep ids descriptive.
- id must NOT reveal/hint the answer.
- area_of_obstruction: array of 1+ unique items from exactly:
  - Problem statement
  - Requirements
  - Interfaces
  - CodeBase
- area_of_obstruction item matching:
  - Items must match the allowed strings exactly (case-sensitive; no extra whitespace).
- type_of_obstruction: exactly one string from exactly:
  - Missing Parameters
  - Ambiguous requirements
  - Contradictory requirements
- description: standalone; no self-referential wording; MUST NOT leak resolution; MUST NOT mention other blocker ids.
- Prefer concise wording (ideally <= 2-3 sentences) for description and resolution. Clarity and precision take precedence over strict brevity.
- resolution: obsessively specific; resolves ONLY this blocker; must not contain extra hidden parameters that answer other blockers.
- The resolution text must not presuppose that any other blocker has already been resolved.
- If the resolution needs examples, keep them short (Example/Counterexample fragments are OK). Avoid paragraphs; keep only essentials.
- Resolution minimality (required):
  - The resolution must contain only the minimal policy/value necessary to resolve THIS blocker.
  - Do NOT bundle multiple independent policy decisions into a single blocker resolution.
- trigger_questions: 3-5 items (MAX 5); full sentences; varied phrasing; standalone; non-self-referential; must not mention other blocker ids.
- Trigger Questions (hard):
  - You MUST write between 3 and 5 trigger questions inclusive. Do NOT write more than 5.
  - Each item must be a single-decision question (one question space only).
  - Do NOT bundle two distinct questions into one entry (avoid "and/or" that introduces a second independent decision).
  - Do NOT introduce unrelated terms/concepts that do not change the blocker's resolution.
- Independence (deterministic): avoid dependent clusters where resolving one blocker would deterministically fix another (definition + conflict-handling split, tightly-coupled policy splitting, etc.).

Also write a human-readable mirror:
- blocker_registry.md must contain the same blockers and same content.
- Format per blocker:
  ## <id>
  **Area of obstruction**: <comma-separated>
  **Type of obstruction**: <...>
  **Description**
  <text>
  **Resolution**
  <text>
  **Trigger Questions**
  - ...

After writing:
1) Print summary counts:
   - total blockers
   - count by type_of_obstruction
   - count by area_of_obstruction (count each area entry)
1.5) Chat-only independence self-check (required):
   - For EACH blocker id, print one line:
     - `Does not specify:` <2-4 contract aspects this resolution intentionally does NOT decide>
   - Do NOT write this self-check into any output file (chat only).
2) Independence matrix (required):
   - rows=ids, cols=ids; mark YES only if resolving the row would deterministically eliminate the need to ask the column
   - Clarifier: mark YES only if resolving the row alone (with no additional clarification) logically forces a unique resolution for the column
   - requirement: all off-diagonal cells must be NO under this definition

Hard constraint:
- Print the summary counts and independence matrix to chat (stdout) only. Do NOT append them to any output file.
- Do NOT reopen or modify any output file after writing them.
- Do NOT add the summary counts or independence matrix as code blocks inside blocker_registry.md.

Write output files:
- $DELIVERABLES/blocker_registry.json
- $DELIVERABLES/blocker_registry.md

Stop after writing the two files and printing the chat-only summary/matrix.
