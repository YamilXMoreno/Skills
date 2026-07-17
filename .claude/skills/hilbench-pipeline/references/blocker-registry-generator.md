# Blocker Registry Generator (one-shot, from originals)

Use this when you want the model to propose the whole registry directly from the ORIGINAL
task inputs, instead of the two-step `01 plan -> 02 registry` path. Run it in a fresh
Cursor chat. Output is `$DELIVERABLES/blocker_registry.json` (+ `.md` mirror), which is
then hardened by `/hilbench-validate-registry`.

Replace the placeholders with the ORIGINALS of each input (from `task_info.txt`,
`test_patch.diff`, `golden_patch.diff`).

## Your role

Analyze the provided inputs and generate a `blocker_registry.json` that contains
blockers — ambiguities, contradictions, or missing information that would prevent an AI
agent from correctly solving the coding task without asking clarifying questions.

## What counts as a GOOD blocker (all criteria required)

- Realistic: it must plausibly exist in real-world engineering. Not contrived just to
  have a blocker.
- Critical: the blocker prevents correct completion; its resolution materially changes the
  implementation.
  - NOT critical (avoid): "error type + exact error message" resolutions where the message
    text does not affect behavior; cosmetic re-ordering; output-order rules with no
    downstream effect. See eval 7 (`eval_blocker_critical_implementation`) in
    `eval-prompts.md` for the full criticality/necessity/guessability/realism criteria.
- Objective: exactly one correct resolution, in a single format; not vague/subjective; no
  multiple valid interpretations.
- Vast search space (not guessable): cannot be solved by guessing or brute force. Do NOT
  create blockers with only 2-3 candidate resolutions, and do NOT create blockers whose
  answer is an obvious industry default. Aim for >= 4 plausible alternatives.
- Independent: resolving one blocker must not reveal or partially fix another. A
  resolution MUST NOT embed rules that belong to another blocker's decision space.
- Not self-referential: never hint that content was modified/removed to create the blocker;
  never call the problem statement "PS"; never mention "blocker/registry/hidden tests".
- Anti-leak: never encode the resolution in identifiers, enum names, fixture/snapshot
  names, or type names.
- Field-level anti-leak:
  - A description identifies the decision gap but never states, exemplifies, or strongly
    narrows the resolution.
  - A resolution is one deterministic, implementable rule with no placeholders, alternatives,
    vague defaults, answer-bearing names, or details from another blocker.
  - Trigger questions are neutral and answer-free: no leading option, embedded correct choice,
    resolution constants/examples, or cross-blocker clue.
  - Read description + all trigger questions without the resolution. Reject the blocker if the
    correct answer becomes reliably inferable or fewer than three plausible answers remain.

## Blocker types (choose exactly one per blocker)

- Missing Parameters: a required concrete value/threshold/limit/format is unspecified and
  cannot be safely defaulted.
- Ambiguous requirements: underspecified behavior with multiple valid implementations; no
  mutually conflicting instructions.
- Contradictory requirements: 2+ explicit instructions cannot all be satisfied at once
  (you must be able to name >= 4 distinct resolution strategy types).

## Obstruction areas (choose 1+)

Problem statement / Requirements / Interfaces / CodeBase. Select only areas where the
ambiguity/contradiction is actually introduced. CodeBase only if the setup patch introduces
it.

## Output

- `$DELIVERABLES/blocker_registry.json` — valid JSON matching
  `references/blocker_registry.schema.json` (no markdown fences, no extra keys).
- `$DELIVERABLES/blocker_registry.md` — human-readable mirror with identical content.
- trigger_questions: 3-5 per blocker (MAX 5), each single-decision, each relevant only to
  that blocker.

Match the required blocker distribution from `task_info.txt` exactly. If the distribution
is missing, output `CONFIG_MISSING` and STOP.

## Inputs (replace)

- Original Problem Statement: {{ORIGINAL_PROBLEM_STATEMENT}}
- Original Requirements: {{ORIGINAL_REQUIREMENTS}}
- Original Public Interfaces: {{ORIGINAL_INTERFACES}}
- Required Blocker Distribution: {{DISTRIBUTION_OF_BLOCKERS}}
- Test Patch (original): {{TEST_PATCH_TEXT}}
- Golden Patch (original): {{GOLDEN_PATCH_TEXT}}

After writing the two files, print (chat only) the summary counts by type/area and an
independence matrix (rows=ids, cols=ids; all off-diagonal cells must be NO). Do NOT write
the summary/matrix into any file.
