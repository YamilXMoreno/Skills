
# Write modified_requirements.txt

Shared rules (apply to this step):
- English-only file content.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording anywhere in this artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: do not overconstrain so the exact resolution becomes reliably inferable via defaults, conventions, or simple elimination.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

CRITICAL format requirements:
- $DELIVERABLES/modified_requirements.txt MUST contain ONLY the bullet list.
- EVERY non-empty line MUST start with "- " (dash + space).
- Do NOT add headings, numbering, labels, "Traceability mapping", or any extra sections.
- Do NOT wrap the content in markdown code fences.
- Do NOT write or modify any files except $DELIVERABLES/modified_requirements.txt.

Embedded rules:
- English-only file content.
- Requirements must not leak resolutions.
- Contradictory requirements must not enumerate both concrete implementations as MUST statements.
- Maintain must-ask: do not overconstrain away the ambiguity.
- Avoid binary contradictions:
  - Do NOT write contradictions that collapse to a 2-choice or 2-3 obvious-choice policy (e.g., "fail vs empty").
  - Prefer constraints/tensions that keep a broader strategy space plausible (aim for >= 4 reasonable strategy types).

Format requirement:
- Write the requirements as a bullet list where EACH requirement line starts with "- " (dash + space).
- Do NOT label requirements as "R1", "R2", etc.
- Do NOT add a "Traceability mapping" (or any requirement -> blocker-id mapping) section.

Inputs:
- $DELIVERABLES/plan.md (Scenario Decision is the source of truth)
- @task_info.txt (original requirements)
- $DELIVERABLES/blocker_registry.json

Task:
Rewrite requirements to reflect the with-blockers task while preserving no-leak and must-ask.

Output:
1) Final Requirements text (bullet list only; no extra sections).

Write output file:
- $DELIVERABLES/modified_requirements.txt

Stop immediately after writing $DELIVERABLES/modified_requirements.txt.
