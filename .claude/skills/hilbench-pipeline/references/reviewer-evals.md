# Reviewer eval runner — mechanics (shared by `/hilbench-run-evals` and `/hilbench-run-eval`)

These two commands let a **reviewer** re-run the 12 static model evals against a task that already
has a blocker set, to reproduce/verify a specific flag the async evaluation reported. They
run the SAME prompts the authoring gates run — **verbatim from
[`eval-prompts.md`](./eval-prompts.md)** — so a result here matches what
`/hilbench-validate-registry` and `/hilbench-validate-artifacts` would produce. They do NOT
introduce any new judgment.

> **Read-only.** Both commands are diagnostic. They MUST NOT modify any artifact under
> `$DELIVERABLES` (registry, modified specs, patches). The reviewer edits artifacts by hand (see
> [`optional-repair.md`](./optional-repair.md)); these commands only report `PASS`/`FAIL`.

## The 12 evals (names + aliases)

Accept either the full `eval_*` name or the short alias (case-insensitive). Each maps to a
numbered prompt in `eval-prompts.md`, whose placeholder table says which files to substitute.

| # | Full name | Alias | Needs (beyond registry + modified specs) |
|---|-----------|-------|------------------------------------------|
| 1 | `eval_test_list` | `test_list` | test patch |
| 2 | `eval_test_relevance` | `test_relevance` | test patch |
| 3 | `eval_golden_patch` | `golden_patch` | golden + setup patch |
| 4 | `eval_blocker_type` | `blocker_type` | — |
| 5 | `eval_blocker_independence` | `independence` | — |
| 6 | `eval_blocker_objective` | `objective` | — |
| 7 | `eval_blocker_critical_implementation` | `critical_implementation` | tests + golden (run full A/B/C on a completed task) |
| 8 | `eval_blocker_descriptions` | `descriptions` | — |
| 9 | `eval_blocker_questions` | `questions` | — |
| 10 | `eval_blocker_distribution` | `distribution` | required distribution from `task_info.txt` |
| 11 | `eval_self_reference` | `self_reference` | — |
| 12 | `eval_request` | `request` | golden patch |

On a completed task every input exists, so all 12 are runnable (eval 7 runs its full prompt —
Tests A, B, and C — not the split scoping the gates use). If a required input is genuinely
missing, STOP that eval with `REQUIRED_INPUT_FILE_MISSING` and name the file.

## How to run (both commands)

1. Resolve and print `$TASK_FILES` and `$DELIVERABLES` (per `SKILL.md`).
2. Expand every selected eval into the independent shards below. Launch every shard from every
   selected eval in one parallel batch.
3. Each shard receives the FULL prompt body **verbatim**, all normal placeholder inputs, and a
   wrapper that says which unit is authoritative for that shard. The wrapper may narrow scope
   but must not alter the prompt's definitions, carve-outs, or verdict standard.
4. Wait for all shards. An eval passes only when every required shard passes. Preserve every
   failing shard reason in the aggregated reason.
5. Emit one line per eval: `EVAL <full_name>: PASS` or `EVAL <full_name>: FAIL — <one-line reason>`
   (keep the eval's own reason wording on FALSE).
6. These are diagnostic and STOP for review; never auto-advance into a fix.

### Internal parallel shard map

- `eval_test_list`: one shard per `relevant_tests.txt` entry.
- `eval_test_relevance`: one shard per blocker resolution.
- `eval_golden_patch`: one shard per requirement/interface/blocker-resolution completeness item,
  plus one file-scope shard and one extraneous-change shard.
- `eval_blocker_type`: one shard per blocker.
- `eval_blocker_independence`: one shard per unordered blocker pair. Each shard receives the full
  registry so it can detect indirect forcing, but returns only its assigned pair verdict.
- `eval_blocker_objective`: one shard per blocker.
- `eval_blocker_critical_implementation`: one shard per blocker per internal Test A/B/C.
- `eval_blocker_descriptions`: one shard per blocker.
- `eval_blocker_questions`: one shard per trigger question; include the full registry so
  cross-blocker relevance can still be checked.
- `eval_blocker_distribution`: one mechanical whole-registry shard; it has no independent
  internal units to parallelize.
- `eval_self_reference`: one shard per blocker.
- `eval_request`: one shard for each modified artifact (problem statement, requirements, public
  interfaces) plus one combined-artifacts shard for leaks inferable only across files.

Shard workers are read-only and never launch children. The parent only fans out and aggregates;
it must not re-judge a returned shard. Missing/empty shard sets are failures except where the
source collection is legitimately optional (for example, no public-interface requirements).

`check1` / `check2` are **Harbor** verdicts, not model evals — these commands cannot recompute
them. If asked for one (e.g. it appears in `failing_items.txt`), skip it and tell the reviewer to
reproduce it in the full sandbox with `/hilbench-evaluate-check2` (which already summarizes the
failing trajectories).

## `failing_items.txt` — the minimal convention

The reviewer pastes the failing rows from the async results into a plain-text file (default
`$DELIVERABLES/review/failing_items.txt`). Format:

```
# blank lines and lines starting with # are ignored
# one item per line:  <name>: <VERDICT> — <optional reason>
eval_request: FALSE — spec reveals the 30s timeout resolution
eval_blocker_independence: FALSE — blocker_a resolution forces blocker_b
check2: FAIL — only 1 of 3 models solved with resolutions
```

Parsing rules (be tolerant):
- Take the token before the first `:` as the item name; match it against the full names/aliases
  above. Unknown names → report `UNRECOGNIZED_EVAL <token>` and skip (do not guess).
- The verdict (`FALSE`/`FAIL`/`F`) and any reason after `—`/`-`/`:` are context only — the
  command re-judges from scratch; it does not trust the pasted verdict.
- `check1`/`check2` lines are recognized but not runnable here (see above).

## Sentinels

- Per eval: `EVAL <full_name>: PASS` | `EVAL <full_name>: FAIL — <reason>`
- `/hilbench-run-evals` summary (last line): `EVALS: <passCount> PASS / <failCount> FAIL`
  (and, if `--from` was used, `(<n> selected from <file>)`).
- `UNRECOGNIZED_EVAL <token>` — a name that isn't one of the 12.
- `REQUIRED_INPUT_FILE_MISSING` — a needed input file is absent.

## Which artifact each FAIL implicates (fix hints for the reviewer)

Diagnosis only — the reviewer decides how to fix (there is no send-back).

| Eval(s) | Usually fixed in |
|---------|------------------|
| `eval_request` | modified problem statement / requirements / interfaces (remove the leak) |
| `eval_golden_patch` | `golden_patch_obstructed.diff` (missing impl or out-of-scope change) |
| `eval_test_list`, `eval_test_relevance` | `test_patch_obstructed.diff` / `relevant_tests.txt` (never weaken — add/tighten) |
| `eval_blocker_critical_implementation` (Test A) | golden / tests / the relevant resolution |
| `eval_blocker_objective`, `eval_blocker_descriptions`, `eval_blocker_questions`, `eval_blocker_type`, `eval_self_reference` | `blocker_registry.json` (resolution/description/trigger-question/type wording) |
| `eval_blocker_independence` | `blocker_registry.json` (reword, or regenerate the coupled resolutions) |
| `eval_blocker_distribution` | `blocker_registry.json` (type counts) — often a regenerate if it needs new blockers |

After a fix, re-run **all 12** evals (`/hilbench-run-evals`) — fixing one eval can regress another —
plus the owning gate (`/hilbench-validate-registry` / `/hilbench-validate-artifacts`), and do a
final `/hilbench-evaluate-full` (incremental) if a Harbor-relevant input changed.
