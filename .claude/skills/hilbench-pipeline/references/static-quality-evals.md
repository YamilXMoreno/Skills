# Static-quality evals (registry gate)

Individually runnable, tiered quality checks for `/hilbench-validate-registry`, restricted to
the checks that can run on the REGISTRY (and modified text if present) WITHOUT needing the
golden/test patches. The patch-dependent evals (test-list coverage, test-relevance/enforcement,
golden-patch completeness, request audit, alignment map) run later in
`/hilbench-validate-artifacts` (step 08) — listed at the bottom so you know where they live.

**Fidelity rule (important).** The Tier 1 `model evals` checks below are run **VERBATIM** from
[`eval-prompts.md`](./eval-prompts.md) — do NOT use the one-line summaries here as the actual
prompt. The summaries exist only to tell you WHICH eval to run and WHY it is at this gate; the
graded judgment must use the full prompt text (its carve-outs prevent false-positive FALSEs).
`guessability`, `description_type_coherence`, and `registry_field_leakage` are NOT from
`model evals` (they are separate linter prompts) and their text lives here.

Run each check independently. Report `PASS`/`FAIL` per check with a one-line reason on
failure. Output plaintext, reference blockers by id/title. If ANY hard check FAILS, the
gate FAILS.

## Tier 0 — Schema / shape (mechanical; hard)

Validate `$DELIVERABLES/blocker_registry.json` against
`references/blocker_registry.schema.json`:

- Valid JSON; top-level object with a `blockers` array; no extra top-level keys.
- Each blocker has: `id`, `area_of_obstruction`, `type_of_obstruction`, `description`,
  `resolution`, `trigger_questions`.
- `id`: snake_case, unique across the registry, descriptive, does not hint the answer.
- `area_of_obstruction`: non-empty array; each item EXACTLY one of
  `Problem statement | Requirements | Interfaces | CodeBase` (case-sensitive).
- `type_of_obstruction`: EXACTLY one of
  `Missing Parameters | Ambiguous requirements | Contradictory requirements`.
- `trigger_questions`: 3-5 items (MAX 5); each a single sentence; no empty strings.
- `description` / `resolution`: non-empty; no markdown fences; English-only.
- Emit `REGISTRY_INVALID` and STOP the gate if any Tier 0 check fails.

## Tier 1 — Static quality (LLM judgment; hard)

The checks marked **[verbatim → eval-prompts.md #N]** are run using the FULL prompt text in
[`eval-prompts.md`](./eval-prompts.md) (substitute the placeholders per that file's table).
The one-liner here is only a pointer + the reason it lives at this gate; do NOT grade off the
one-liner. At the registry gate the golden/test patches usually do not exist yet — run these on
the registry + modified specs (if present, else the originals) and leave any golden/test-patch
input empty; their core judgments do not require the patches.

### EVAL: distribution  ⭐  [verbatim → eval-prompts.md #10]
Counts by `type_of_obstruction` must match the Required Blocker Distribution in
`task_info.txt` (order irrelevant). Earliest, cheapest whole-task check — hard STOP on FALSE.

### EVAL: self_reference  [verbatim → eval-prompts.md #11]
FAIL if any description, resolution, or trigger question attributes a gap to a deliberate
edit made to manufacture the blocker, refers to the problem statement as "PS", or says
"resolved by other blockers". State-of-the-spec wording ("no longer specifies X") is fine.

### EVAL: blocker_objective  [verbatim → eval-prompts.md #6]
Each resolution must have a single objectively-correct answer; no vague/subjective wording;
no multiple interpretations. Do NOT compare the resolution to the golden patch (scope limit).

### EVAL: blocker_independence  ⭐  [verbatim → eval-prompts.md #5]
No resolution may reveal or partially contain another resolution. Resolving blocker A must
not deterministically force blocker B's answer. FAIL naming the dependent pair and the overlap.

### EVAL: blocker_type  [verbatim → eval-prompts.md #4]
Two narrow, quotable conditions: (1) CONTAMINATION — the resolution is derivable from the
spec text (for contradictions, only when an authoritative/overriding marker is quoted); and
(2) CLEARLY WRONG TYPE TAG. Default TRUE; fail only with the required quotes. (The `CodeBase`
obstruction-area check needs the setup patch and is confirmed later at the artifacts gate.)

### EVAL: blocker_critical_implementation  ⭐  [verbatim → eval-prompts.md #7 — TESTS B/C ONLY]
Run the verbatim eval-7 prompt, but at this gate the relevant tests / test patch / golden
patch are absent, so **Test A (necessity) is UNVERIFIED here** (the prompt's own rule). Judge
only **Test B (non-guessable)** and **Test C (realistic)**; STOP the gate on a clear B or C
failure. Test A is resolved at the artifacts gate, and Check 1 is the decisive empirical
confirmation of Test B. See the "Eval 7 split" section of `eval-prompts.md`.

### EVAL: blocker_descriptions  [verbatim → eval-prompts.md #8]
Description correctly identifies the blocker for its area, contains NO resolution, is not
self-referential; the resolution actually resolves the described blocker and does not reveal
another. Quote the offending text before failing (grounding requirement).

### EVAL: blocker_questions  [verbatim → eval-prompts.md #9]
Each trigger question is a relevant clarifying question for its own blocker only; not
appropriate for a different blocker; not a duplicate; exactly one decision per question.
FAIL naming the blocker + question.

### EVAL: guessability (guessability linter — NOT from model evals; text lives here)
For each blocker, enumerate >= 3 (prefer 4-6) plausible alternative resolutions and confirm
the correct one is NOT reliably inferable via industry default, convention, or elimination
from the modified artifacts (if present) or originals. A 2-choice or 2-3 obvious-choice
decision FAILS. Exception: if the visible spec/tests already force one answer, the blocker
is guessable and FAILS.

### EVAL: description_type_coherence (blocker-type linter — NOT from model evals; text lives here)
The description must match the declared `type_of_obstruction`: contradictory ⇒ 2+
conflicting instructions; ambiguous ⇒ underspecified, multiple interpretations, no direct
conflict; missing parameter ⇒ a single missing concrete value. FAIL with the correct type
otherwise. (Complementary to `blocker_type` #4 Condition 2; keep both.)

### EVAL: registry_field_leakage (description/resolution/questions linter — NOT from model evals)
Audit every field of every blocker and report ALL failures:
- **Description:** must identify only the missing/ambiguous/contradictory decision. FAIL if it
  states or strongly narrows its own resolution, embeds answer-bearing examples/defaults,
  exposes another resolution, or leaves fewer than three genuinely plausible answers.
- **Resolution:** although hidden from the solving agent, it must be one complete,
  deterministic, implementable decision. FAIL if it contains alternatives, unresolved
  placeholders, vague words without an exact rule, contradictions, answer-bearing identifier
  names that will leak into visible artifacts, or any part of another blocker's answer.
- **Trigger questions:** each question must be neutral and answer-free. FAIL leading questions,
  either/or questions containing the correct choice, names/constants/examples copied from the
  resolution, questions that reveal another blocker, or wording that reduces the answer space
  below three plausible choices.
- **Cross-field flaw:** reconstruct what a solver can infer from description + all trigger
  questions without seeing the resolution. If the correct resolution is reliably inferable,
  FAIL and quote the exact leaking phrases.

## Output format

```
Tier 0 schema: PASS/FAIL - reason
distribution: PASS/FAIL - reason
self_reference: PASS/FAIL - reason
blocker_objective: PASS/FAIL - reason
blocker_independence: PASS/FAIL - reason
blocker_type: PASS/FAIL - reason
blocker_critical_implementation (B/C): PASS/FAIL/UNVERIFIED(A) - reason
blocker_descriptions: PASS/FAIL - reason
blocker_questions: PASS/FAIL - reason
guessability: PASS/FAIL - reason
description_type_coherence: PASS/FAIL - reason
registry_field_leakage: PASS/FAIL - reason
REGISTRY GATE: PASS/FAIL
```

## Deferred to /hilbench-validate-artifacts (step 08; need patches)

Run these VERBATIM from `eval-prompts.md` once the patches exist:

- eval_test_list (#1) — every relevant test is discoverable in the test patch.
- eval_test_relevance (#2) — each resolution is enforced by >= 1 assertion that fails without it.
- eval_golden_patch (#3) — golden patch implements PS + requirements + interfaces + resolutions.
- eval_request (#12) — the modified spec does not leak a resolution / embed the solution / narrate itself.
- eval_blocker_critical_implementation (#7) — **Test A (necessity)** now that tests + golden exist.
- eval_blocker_type CodeBase area (#4) — needs the setup patch to confirm the `CodeBase` area.
- alignment map (spec anchor -> test anchor -> golden anchor) per blocker.

These are intentionally NOT run here because the registry gate happens before injection,
when the patches may not exist yet.
