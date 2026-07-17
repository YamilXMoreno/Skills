---
description: Reviewer tool - run the 12 static model evals verbatim in parallel isolated subagents against a completed task and report each PASS/FAIL, mirroring the async evaluation sheet. Default runs all 12; --from <file> runs only the evals flagged in a failing_items.txt. Read-only diagnostic; does not modify any artifact.
argument-hint: "[--from <path to failing_items.txt>]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is a **reviewer** diagnostic
that reproduces the static model-eval portion of the async evaluation sheet locally. It runs the
SAME prompts the authoring gates run — **verbatim from `references/eval-prompts.md`** — so it
introduces no new judgment. It is **read-only**: it reports results and never edits an artifact.

Follow `references/reviewer-evals.md` for the eval-name table, the `failing_items.txt`
convention, aliases, and output/sentinel rules.

Do this:

1. Resolve and print `$TASK_FILES` and `$DELIVERABLES`.

2. Decide which evals to run:
   - **No `--from`** → run **all 12** model evals.
   - **`--from <file>`** (default path `$DELIVERABLES/review/failing_items.txt`) → parse it per
     the convention in `references/reviewer-evals.md`: take the token before each `:`, match it
     to a full name/alias, and run **only** those evals. Ignore blank/`#` lines. For an
     unmatched token, print `UNRECOGNIZED_EVAL <token>` and skip it. For a `check1`/`check2`
     line, note it is a Harbor verdict (reproduce via `/hilbench-evaluate-check2`) and skip it.
     If the file is missing, STOP with `REQUIRED_INPUT_FILE_MISSING`.

3. **Nested parallel fan-out (mandatory).** Expand every selected eval using the internal shard
   map in `references/reviewer-evals.md`, then launch one NEW, isolated, read-only subagent
   process per shard. Launch every shard from every eval in one parallel batch — do not run
   evals or their internal checks one at a time. Give each worker only:
   - that eval's FULL prompt body **verbatim** from `references/eval-prompts.md` (no paraphrase),
   - a wrapper identifying its authoritative blocker/pair/question/test/artifact/criterion shard,
   - the files/content required by that prompt's `{{placeholders}}`, using the table at the top
     of that reference, and
   - the exact shard result contract:
     `EVAL_SHARD <full_name> <shard>: PASS` or
     `EVAL_SHARD <full_name> <shard>: FAIL — <one-line reason>`.

   Do not combine shards in one worker or give it sibling results. On a completed task,
   run eval 7 as its full A/B/C prompt. Every worker is diagnostic only: it MUST NOT edit files,
   start another eval, or advance a stage. If a required input is genuinely missing, record
   that eval as `REQUIRED_INPUT_FILE_MISSING`; launch all other ready workers without waiting
   for it.

4. **Parent aggregation.** Wait for every launched shard, collect each result, and reduce shards
   with strict all-pass semantics: an eval passes only if every required shard passes. Include
   every failed shard in that eval's reason. Then emit results
   in the canonical eval order from `references/reviewer-evals.md` (not completion order).
   The parent orchestrates and aggregates only; it MUST NOT re-run or independently re-judge
   an eval after a worker returns.

5. Emit one line per eval, then a final summary line, then STOP:
   - `EVAL <full_name>: PASS` | `EVAL <full_name>: FAIL — <reason>`
   - Final: `EVALS: <passCount> PASS / <failCount> FAIL` (append `(<n> selected from <file>)`
     when `--from` was used).
   For each FAIL, add the fix-location hint from `references/reviewer-evals.md`. Do NOT apply any
   fix — the reviewer decides how to fix (there is no send-back).

This command never modifies the registry, modified specs, or patches, and never advances a
stage. After a fix, re-run **all 12** here (fixing one eval can regress another) rather than
re-checking a single flag.
