
# Write modified_public_interfaces.txt

Shared rules (apply to this step):
- English-only file content.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording anywhere in this artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: do not describe interfaces so the exact resolutions become reliably inferable via defaults, conventions, or simple elimination.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

CRITICAL format requirements:
- $DELIVERABLES/modified_public_interfaces.txt MUST be plain text (no markdown code fences).
- Use ONE of these two shapes (pick one and be consistent):
  A) A single line: "No new interfaces are introduced"
  B) A repeated 6-line block per interface, where EVERY line MUST start with "- " and uses EXACT keys:
     - Path: <absolute file path>
     - Name: <exact symbol name>
     - Type: <file|function|class>
     - Input: <comma-separated params with types if available>
     - Output: <return type / outputs>
     - Description: <1-2 sentences>
     (blank line between interface entries is allowed)
- Do NOT add any other sections/headings beyond the fields above.
- Do NOT write or modify any files except $DELIVERABLES/modified_public_interfaces.txt.

Embedded rules:
- English-only file content.
- Public interfaces must not leak blocker resolutions.
- Do not introduce fake interfaces; only document true public surfaces.
- Definition (use this to decide what to include):
  - A public interface is any newly introduced file, function, or class that external callers can reach under the language's normal visibility rules.
  - Nested functions, private helpers (including those marked private by naming convention), and non-exported symbols are internal and MUST NOT be documented as public.
- Scope rule for this workflow (hard):
  - Only document NEW public interfaces introduced by the golden patch:
    - new file
    - new function
    - new class
  - Do NOT document environment/setup/scaffolding changes (e.g., build/test runner config, CI wiring, toolchain setup, or other non-interface setup).
- Path rule (hard):
  - Path MUST be an absolute path as it appears in the patch context.
  - Use the repo workspace absolute form: `$REPO_ROOT/<repo-relative-path>`.
  - Do NOT use relative paths.
- Name rule (hard):
  - Provide the name exactly as it appears in the patch (including casing).

Inputs:
- $DELIVERABLES/plan.md (Scenario Decision is the source of truth)
- @task_info.txt (original public interfaces)
- $DELIVERABLES/blocker_registry.json

Task:
Produce modified_public_interfaces.txt:
- If unchanged, copy the original interfaces content.
- If changed, ensure descriptions do not leak hidden answers.

Write output file:
- $DELIVERABLES/modified_public_interfaces.txt

Stop immediately after writing $DELIVERABLES/modified_public_interfaces.txt.
