
# Write modified_problem_statement.txt

Shared rules (apply to this step):
- English-only file content.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording anywhere in this artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: do not phrase content so the exact resolutions become reliably inferable via defaults, conventions, or simple elimination.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

CRITICAL format requirements:
- $DELIVERABLES/modified_problem_statement.txt MUST match the exact template below (Title + Description only).
- Do NOT add any other headings/sections (e.g., "Expected behavior", "Requirements", "Notes", "Output:", etc.).
- Do NOT include bullets in the file. Bullets are chat-only.
- Do NOT wrap the content in markdown code fences.
- Do NOT write or modify any files except $DELIVERABLES/modified_problem_statement.txt.

Embedded rules:
- English-only file content.
- No self-referential wording.
- No contamination: do not leak any blocker resolutions (values, formats, ordering policies).
- Not a gotcha: keep it realistic; mention the "surface area" of the issue without giving the hidden answers.
- Avoid binary contradictions:
  - Do NOT phrase tensions so the reader can reduce them to a 2-choice or 2-3 obvious-choice policy.
  - Prefer wording that keeps a broader strategy space plausible (aim for >= 4 reasonable strategy types).

Format requirement (must match exactly):
## Title: <Add Title Here>

### Description

<Add Description Here>

Inputs:
- $DELIVERABLES/plan.md (Scenario Decision is the source of truth)
- @task_info.txt (original PS)
- $DELIVERABLES/blocker_registry.json

Task:
Rewrite the Problem Statement so it supports the blockers without revealing their resolutions.
- Scenario 1: remove/generalize test-enforced narrow details that were previously stated.
- Scenario 2: keep/inject the intended gaps/tensions without encoding the hidden answers.

Output:
In chat:
1) Full rewritten PS (exact required format).
2) 3-8 bullets describing what was intentionally generalized/removed (do not reveal the hidden answers).

In file:
- Write the PS only (exact required format). Do NOT include the bullets in the file.

Write output file:
- $DELIVERABLES/modified_problem_statement.txt

Stop immediately after writing $DELIVERABLES/modified_problem_statement.txt.
