---
description: Reviewer tool - run one or more named static model evals verbatim in isolated workers; multiple selected evals run concurrently. Read-only diagnostic; does not modify artifacts.
argument-hint: "<eval_name[,eval_name...]>  (e.g. eval_request | independence,golden_patch)"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is a **reviewer** diagnostic:
it runs a targeted set of model evals so a reviewer can reproduce or re-verify flags from the
async evaluation sheet without running all 12. It is **read-only** — it reports
`PASS`/`FAIL` and never edits an artifact.

Follow `references/reviewer-evals.md` for the eval-name table, aliases, and rules; run the
prompt body **verbatim** from `references/eval-prompts.md`.

Do this:

1. Resolve and print `$TASK_FILES` and `$DELIVERABLES`.

2. Parse `$ARGUMENTS` as one or more comma/space-separated eval names (full `eval_*` or short
   aliases, case-insensitive). Remove duplicates while preserving requested order.
   - If any token is not one of the 12 in `references/reviewer-evals.md`, STOP before launching
     workers with
     `UNRECOGNIZED_EVAL <token>` and print the list of valid names.
   - If any token is `check1` / `check2`, STOP and tell the reviewer these are Harbor verdicts — the
     async sheet does not ship the agent patches to diagnose, so reproduce with
     `/hilbench-evaluate-check2` instead.

3. Gather each selected eval's inputs by substituting its `{{placeholders}}` using the table at
   the top of `references/eval-prompts.md` (completed-task artifacts under `$DELIVERABLES`,
   originals under `$TASK_FILES`). If a required input file is missing, STOP with
   `REQUIRED_INPUT_FILE_MISSING` and name it. On a completed task, run eval 7 as its FULL prompt.

4. Expand each selected eval using the internal shard map in
   `references/reviewer-evals.md` (per blocker, blocker pair, question, test, artifact, or
   criterion as applicable). Launch one NEW isolated read-only worker per shard, with every
   shard across all selected evals launched in one parallel batch. Even when exactly one eval
   was requested, all of its independent internal checks run concurrently.

   Each worker receives its eval's scoped inputs, FULL prompt body **verbatim**, and only a
   wrapper naming the authoritative shard. Do not paraphrase, combine shards, or allow workers
   to edit files. Wait for every shard; one failure never cancels siblings. An eval passes only
   if all of its required shards pass, and its failure reason includes every failed shard.

5. Emit one result line per eval in requested order, then STOP:
   - `EVAL <full_name>: PASS`, or
   - `EVAL <full_name>: FAIL — <the eval's own one-line reason>`
   If multiple evals were selected, append `EVALS: <passCount> PASS / <failCount> FAIL`.
   On FAIL, add the fix-location hint for that eval from `references/reviewer-evals.md`, but do
   NOT apply any fix — the reviewer decides how to fix (there is no send-back).

Never modify the registry, modified specs, or patches. Never auto-run other evals or advance a
stage.
