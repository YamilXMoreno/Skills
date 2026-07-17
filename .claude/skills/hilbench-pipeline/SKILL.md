---
name: hilbench-pipeline
description: >
  Segmented HiL-Bench (outcome_ladybug) pipeline for the in-task contributor sandbox.
  Use when a contributor needs to provision the SWEAP container, author blockers,
  inject them, run the alignment gate, optionally pre-screen with Attempter Check 1 /
  Check 2 inside the container, and run the required Harbor evaluation. Unlike the older
  all-in-one run, every stage is a separate command that STOPS for human review and
  never auto-advances. Trigger phrases: "provision the task", "run check 1",
  "run check 2", "author the registry", "inject blockers", "validate the task",
  "evaluate the task".
---

# HiL-Bench pipeline (segmented, container-backed)

This skill turns the outcome_ladybug workflow into small, independently runnable,
reviewable stages instead of one long autonomous run. It also fills the gap the older
skills assumed away: it actually **provisions and verifies the SWEAP repo/container**,
and it runs **Attempter Check 1 / Check 2 as a real agent solve inside that container**.

This package is self-contained: it bundles the `input_validation` and `evaluate_task`
grading skills it delegates to (so the Stage-5 evaluation commands work with no separate
`hilbench-evaluation` install). It does NOT modify the older `hilbench-blocker-injection` /
`hilbench-evaluation` packages if they are installed alongside it; they can coexist.

## Core principles

1. Segment, don't monolith. Each stage is its own command and STOPS for review.
2. The human owns the blockers. Gate the registry hard and early.
3. Two environments:
   - Sandbox = durable control plane (inputs, deliverables, this skill, scripts, the model).
   - Container (SWEAP image) = disposable runtime that holds the `/app` repo + toolchain.
   Nothing important lives only in the container.
4. Scripts for determinism, model for intelligence. Provisioning, file-shuffling,
   verification, and test execution are scripts. Only authoring and the check "solve" are model work.
5. Isolation by clean disk + scoped inputs, not process jailing.
6. Cheap gates before expensive ones: schema -> static quality -> alignment -> container checks -> Harbor.

## Resolve paths first (do NOT hardcode `/app`)

Resolve and print these once at the start of any command:

- `$TASK_FILES` — dir containing `task_info.txt`. First hit wins:
  `$HILBENCH_TASK_FILES` (if it holds `task_info.txt`), then `/app/task_files`,
  `/home/sandbox/task_files`, `./task_files`, `.`, then a shallow
  `find . /app /home/sandbox -maxdepth 3 -name task_info.txt`.
- `$DELIVERABLES` — output dir for everything authored (never `$TASK_FILES`).
  `$HILBENCH_DELIVERABLES` if set, else `<parent of $TASK_FILES>/deliverables` (`mkdir -p`).
- `$REPO_ROOT` — the git working tree. NOTE: in this sandbox the repo lives inside the
  **container**, not the sandbox filesystem. Repo operations run via
  `docker exec "$HILBENCH_CONTAINER" ... /app`. Only use a sandbox-local `$REPO_ROOT`
  if a repo genuinely exists there (`git rev-parse --show-toplevel`).
- `$HILBENCH_CONTAINER` — the running SWEAP container name (set by `/hilbench-provision`).
- `$HILBENCH_INSTANCE_ID` — the instance id parsed from `task_info.txt`.

If `$TASK_FILES` cannot be resolved, STOP with `REQUIRED_INPUT_FILE_MISSING` and list
the directories searched.

## Environment model

```
SANDBOX (persistent)                 CONTAINER (disposable)
  task_files/    inputs                /app   repo @ parent commit + toolchain
  deliverables/  authored outputs      (started per execution step; holds no unique state)
  scripts/       provision/check       torn down between steps; rebuildable from image
  the model + API access
```

Bridge for checks/eval: `docker cp` the checker + patches into the container and run
`task_checker.py` there (it defaults cwd to `/app` and resets git each run).

## Stage map (each command STOPS for review)

```
0.  /hilbench-provision           pull image once, start container, verify base commit
                                  (--validate-original chains stage 0.5)
0.5 /hilbench-validate-original   original test+golden patches apply + FAIL->PASS on parent
1. author registry (choose one):
     /hilbench-registry-generate  (generator, from originals)
     /hilbench-plan + /hilbench-registry   (numbered 01 -> 02)
     manual / own prompt
     /hilbench-registry-regenerate <ids>  (repair: re-do selected blockers, keep the rest -> re-inject)
   /hilbench-validate-registry    schema + verbatim model evals (eval 7 B/C) [GATE]
   /hilbench-repair --registry-only  optional collect-all + one repair batch (asks before rerun)
2. /hilbench-inject               steps 03-06 (+07 obstructed patches, Scenario 2)
3. /hilbench-validate-artifacts   step 08 + alignment + verbatim evals 1/2/3/12/7A [GATE]
3.5 /hilbench-validate-obstructed dynamic FAIL->PASS on the AUTHORED obstructed patches
                                  (setup baked, then obstructed test+golden run in container)
4. (optional pre-screen, recommended before Stage 5)
   /hilbench-check1               prep -> fresh subagent solve -> eval   [STOP]
   /hilbench-check2               prep -> fresh subagent solve -> eval   [STOP]
5. REQUIRED Harbor grade
   /hilbench-evaluate-full        input validation + Check 1 + Check 2 (up to 12 runs) [GATE]
                                  incremental on re-run (skips unchanged stages); --fresh forces full
                                  (see references/evaluation-rerun.md)
   /hilbench-evaluate-check1/2    force one check unconditionally (6 runs each)
   /hilbench-validate-input       standalone input validation (optional; -full runs it)
6. Capture Files -> deliverables/ -> TextCollection
```

## Reviewer commands (reviewing a task that already has blockers; separate from the authoring pipeline)

For a REVIEWER reviewing a task that already ships with a blocker set (generated by the pipeline or
authored by an earlier contributor), against its independent async evaluation (Harbor
`check1`/`check2` + the 12 static model evals, run async). These are read-only diagnostics — they
reproduce eval results and never modify artifacts. See [`REVIEWER_GUIDE.md`](./REVIEWER_GUIDE.md)
and `references/reviewer-evals.md`.

```
/hilbench-run-evals [--from review/failing_items.txt]   all 12 model evals (or just the flagged ones), verbatim
/hilbench-run-eval  <eval_name>                          one model eval by name, for a fast single re-check
/hilbench-repair                                         all evals -> one repair batch -> ask before rerun
```

To reproduce a Harbor `check2` failure (the sheet does NOT ship the async agent patches), the
reviewer re-runs `/hilbench-evaluate-check2`.

## Review-gate rule (hard)

After each command, the FIRST line of your chat reply MUST be the exact result sentinel line(s)
copied verbatim from the tool output — e.g. `PROVISION_OK`, `COMMIT_OK <hash>`, `INPUT VALIDATION:
PASS`, `REGISTRY GATE: PASS`, `EVALUATION: FAIL`, or any `STOP - <reason>`. Quote it literally; do
not paraphrase it and do not bury it under command/build output. If a stage prints more than one
sentinel (e.g. provisioning prints `COMMIT_OK` then `PROVISION_OK`), quote each. Only after that
line, print the one-line `STAGE <name>: DONE` / `STOP - <reason>` status plus a short summary of
what was produced, then STOP. Never chain into the next stage automatically. The contributor
decides when to proceed.

## Isolation rule for Check 1 / Check 2 (hard)

The check "solve" MUST be run by a FRESH subagent (clean context, no memory of authoring)
that receives ONLY the scoped inputs, and MUST run AFTER `scripts/prepare_check.sh` has
scrubbed the answer-key files from the container working tree. Never hand the test patch,
golden patch, or (for Check 1) the resolutions to the solving subagent. See
`references/attempter-check1.md` / `references/attempter-check2.md`.

## Commands and references

Commands live in `commands/`; deterministic scripts in `scripts/`; prompt bodies in
`references/`. Open the relevant command file, follow it exactly, and use the referenced
prompt body as the source of truth for formats and hard gates.

## STOP sentinels

Halt on any of: `REQUIRED_INPUT_FILE_MISSING`, `UNRECOGNIZED_IMAGE_SOURCE`,
`IMAGE_BUILD_FAILED`, `IMAGE_LOAD_FAILED`,
`DEPENDENCY_INSTALL_FAILED`, `DEPENDENCY_IMAGE_COMMIT_FAILED`,
`PARENT_COMMIT_MISMATCH`, `CONTAINER_NOT_RUNNING`, `CONFIG_MISSING`, `PLAN_INVALID`,
`CHECKER_NOT_AVAILABLE`, `INSTANCE_ID_MISSING`, `REGISTRY_INVALID`, `UNRECOGNIZED_EVAL`,
`UNKNOWN_BLOCKER_ID`,
`ORIGINAL_TEST_PATCH_APPLY_FAIL`, `ORIGINAL_GOLDEN_PATCH_APPLY_FAIL`, `ORIGINAL_F2P_FAIL`.
Also halt on `ORIGINAL_PATCH_FORMAT_FAIL`, `OBSTRUCTED_PATCH_FORMAT_FAIL`, or
`PATCH_FORMAT_FAIL` (corrupted/non-unified patch or non-LF line endings).
