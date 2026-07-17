---
description: GATE - validate the blocker registry (schema/shape + parallel isolated model evals and description/resolution/question leakage checks) before any injection. Runs on ANY authoring path.
argument-hint: ""
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is the FIRST hard gate. It
runs regardless of how the registry was authored (generator / numbered / manual / your own
prompt), so the human-owned answer key is always hardened before injection.

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Require
`$DELIVERABLES/blocker_registry.json` (and `.md` if present). If the JSON is missing, STOP
with `REQUIRED_INPUT_FILE_MISSING`. Read the required distribution from
`$TASK_FILES/task_info.txt`.

Run every check in `references/static-quality-evals.md`:
1. Tier 0 schema/shape (mechanical). If it fails, emit `REGISTRY_INVALID` and STOP — do not
   run Tier 1.
2. Tier 1 static-quality evals. Run all Tier 1 checks in parallel isolated subagent processes
   after Tier 0 passes. The model-eval checks (distribution, self_reference,
   blocker_objective, blocker_independence, blocker_type, blocker_critical_implementation,
   blocker_descriptions, blocker_questions) MUST be run **VERBATIM** from
   `references/eval-prompts.md` — do NOT paraphrase; substitute the placeholders per that
   file's table. The three linters (guessability, description_type_coherence,
   registry_field_leakage) use the text in
   `static-quality-evals.md`. Use the modified artifacts as context only if they already
   exist; otherwise judge against the originals and leave any golden/test-patch input empty.
   - `blocker_critical_implementation` (eval 7) runs **Tests B/C only** here: judge Test B
     (non-guessable) + Test C (realistic); Test A (necessity) is `UNVERIFIED` until the
     artifacts gate (patches don't exist yet). See the "Eval 7 split" note in `eval-prompts.md`.

### Tier 1 parallel execution (mandatory)

Expand every model eval using the internal shard map in `references/reviewer-evals.md`. At this
gate, eval 7 creates only per-blocker Test B and Test C shards. Also shard guessability,
description/type coherence, and field leakage per blocker. Launch exactly one NEW, isolated,
read-only subagent per resulting shard, with all shards launched in one parallel batch. Do not
run internal checks serially or assign multiple shards to one worker. Give each worker only its
own prompt/check text, authoritative shard, and required inputs, with this result contract:
`CHECK_SHARD <check_name> <shard>: PASS` or
`CHECK_SHARD <check_name> <shard>: FAIL — <one-line reason>`.

Workers MUST NOT edit files, launch other checks, or receive another worker's output. Wait for
all workers, reduce each check with strict all-pass semantics, then have the parent emit checks
in the order defined by `references/static-quality-evals.md`. The parent orchestrates and
aggregates only; it MUST NOT re-run or independently re-judge a completed shard. A worker
failure or malformed result fails that check without discarding sibling results.

Emit the per-check `PASS/FAIL` block and a final `REGISTRY GATE: PASS` or
`REGISTRY GATE: FAIL`. Then STOP.

- On PASS: tell the contributor they may proceed to `/hilbench-inject`.
- On FAIL: list the failing checks with concrete fixes (which blocker, why). Do NOT
  proceed to injection until the registry is re-authored and re-validated. The
  patch-dependent evals (test coverage, enforcement, golden completeness, request audit,
  eval-7 necessity, alignment) run later in `/hilbench-validate-artifacts`.
