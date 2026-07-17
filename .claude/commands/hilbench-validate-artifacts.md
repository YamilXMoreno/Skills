---
description: GATE (step 08) - validate LF-only diff integrity, blocker/codebase leakage, minimal blocker test scope, alignment, and patch-dependent model evals in parallel isolated workers.
argument-hint: ""
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first). This is the SECOND hard gate,
run after injection and before the container-backed Attempter Checks. It is the alignment
gate that catches "text spec -> tests -> implementation" mismatches early, and it runs the
model evals that could not run at the registry gate because they need the patches.

Resolve `$TASK_FILES` and `$DELIVERABLES` first (print both). Require in `$DELIVERABLES`:
`modified_problem_statement.txt`, `modified_requirements.txt`,
`modified_public_interfaces.txt`, `relevant_tests.txt`, `blocker_test_map.txt`,
`test_patch_obstructed.diff`,
`golden_patch_obstructed.diff` (and `setup_patch.diff` if present). If any required file is
missing, STOP with `REQUIRED_INPUT_FILE_MISSING`.

Before launching model workers, run the cheap mechanical preflight:

```bash
python3 scripts/validate_patch_artifacts.py --deliverables "$DELIVERABLES"
```

Require `PATCH_FORMAT_OK LF_ONLY VALID_UNIFIED_DIFF`. On `PATCH_FORMAT_FAIL`, STOP the gate and
regenerate the named diff from a clean working tree; never hand-repair corrupted hunks or retain
CRLF.

Then expand every semantic check into the shards below and launch all FRESH, isolated,
read-only shard workers in one parallel batch. Do not wait for one check before starting
another and do not combine shards.

**Part A — patch-content validator + alignment map.** Follow
`references/08-patch-content-validator.md` exactly:
- Provide ONLY the resolution list (id: resolution) as the blocker input — do NOT paste the
  full registry.
- Work through the workflow, returning FALSE on the first hard failure.
- Before returning TRUE, produce the mechanical ALIGNMENT MAP (one line per blocker id, plus
  one per multi-criteria requirement): `Spec anchor | Test anchor | Golden anchor`.
  (08 operationalizes evals 1/2/3 — test_list, test_relevance, golden_patch — plus overlap
  and separation rules and the alignment map, which are value-adds not in `model evals`.)

Shard Part A into: one worker per relevant-test entry, one per blocker enforcement check, one
per requirement/interface/blocker golden-completeness item, one per formula/multi-criteria
fidelity item, one per alignment-map row, one per authored-test scope item, and one whole-patch
separation/file-scope worker. Every shard receives the full validator prompt and inputs but
returns only its authoritative unit.

**Part B — patch-dependent model evals (run VERBATIM from
`references/eval-prompts.md`).** The patches already passed the command's required-input
preflight:
- `eval_request` (#12) — launch its per-artifact plus combined-artifacts shards from
  `references/reviewer-evals.md`. The modified spec must not leak any resolution, embed the solution,
  use a placeholder, narrate itself, or state a no-purpose requirement.
- `eval_blocker_critical_implementation` (#7) — launch one **Test A (necessity)** shard per
  blocker: for each
  blocker, would at least one relevant test FAIL under the most plausible no-ask
  implementation? Test B/C were pre-screened at the registry gate; Check 1 confirms B
  empirically afterward. FAIL the gate on a Test A failure.

**Codebase leak and setup-patch audit.** Launch one worker per blocker. Give each worker its
blocker plus the full registry for cross-leak context, original task contract, parent-commit
repository, and setup patch only. In an isolated temporary checkout,
inspect the agent-visible parent repository, apply `setup_patch.diff` if present, and inspect the
resulting visible repository. For every blocker:
- find answer-bearing code, comments, docs, examples, fixtures, defaults, identifiers, errors, or
  existing behavior that states or uniquely implies the resolution;
- verify setup removes/neutralizes every confirmed clue without touching tests, deleting required
  original behavior, or adding a new clue;
- verify no copied setup diff is present in the agent-visible worktree or recoverable from the
  prepared baseline's git history;
- reconstruct the plausible answer space after setup and require at least three credible choices.

Return one line per blocker:
`CODEBASE LEAK <id>: PASS` or
`CODEBASE LEAK <id>: FAIL — <path/symbol + exact clue or setup flaw>`.
Any FAIL fails the artifacts gate. If a legitimate original contract makes a blocker inferable,
the blocker must be regenerated; do not hide required behavior with setup.

Give each worker only its own instructions and required scoped inputs. Part A workers receive
ONLY the resolution list (`id: resolution`) as blocker input, never the full registry. Part B
workers receive only the placeholder inputs required by their verbatim prompts. Every worker MUST
return its `TRUE`/`FALSE` line and one-line reason, MUST NOT edit any file, and MUST NOT launch
another eval.

Wait for every shard. The parent aggregates with strict all-pass semantics and MUST NOT re-run
or independently re-judge shard work. Emit Part A's aggregated result and ALIGNMENT MAP, then
each Part-B eval's `TRUE/FALSE`, then the per-blocker leak results, then a single final line.
A shard error or malformed result fails the owning check while
preserving the completed results from the other workers.

- On all-pass: `ARTIFACTS GATE: PASS` — proceed to `/hilbench-validate-obstructed`.
- On any FALSE: `ARTIFACTS GATE: FAIL - <which eval + reason>` — prefer fixing the golden
  patch or adding the missing enforcing test (see `references/optional-repair.md`); for an
  `eval_request` leak, fix the modified spec text; do NOT weaken tests. Re-run this gate
  after repair. Do not run the Attempter Checks until this passes.

### Save the results (required)

After emitting the verdict, write the full result to
`$DELIVERABLES/validate_artifacts_result.txt` (overwrite) so the gate outcome is captured with
the deliverables. Include, in order:
- a header line with a UTC timestamp,
- Part A: the `TRUE`/`FALSE` line plus the full ALIGNMENT MAP (one line per blocker id / multi-criteria requirement),
- Part B: each eval's `TRUE/FALSE` with its one-line reason,
- Codebase leak audit: one PASS/FAIL line per blocker,
- the final `ARTIFACTS GATE: PASS` / `ARTIFACTS GATE: FAIL - <reason>` line.

Do NOT write any diff content or blocker resolutions into this file. This results file is the
ONLY file this gate may write — do not modify any of the artifacts under validation.
