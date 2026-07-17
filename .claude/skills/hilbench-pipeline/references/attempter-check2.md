# Attempter Check 2 — solve prompt (WITH resolutions)

This is the prompt body for the FRESH subagent that performs the Check 2 solve. Check 2
verifies the task is SOLVABLE: an agent given the obstructed problem statement /
requirements / interfaces PLUS the blocker resolutions should be able to produce a patch
that passes ALL relevant tests.

## Preconditions (already handled by the caller, do not repeat)

- `scripts/prepare_check.sh check2` has reset the repo to a clean, committed baseline
  (parent commit, plus `setup_patch.diff` if any) and scrubbed the test patch, golden
  patch, and relevant-tests list from the container.
- The subagent runs in a clean context with NO memory of authoring the blockers.

## Isolation rules (hard)

- The solving subagent receives: `modified_problem_statement.txt`,
  `modified_requirements.txt`, `modified_public_interfaces.txt`, AND the blocker
  RESOLUTIONS (resolution text only — id + resolution; no trigger questions, no other
  metadata).
- It MUST NOT be given: the test patch, the golden patch, or `relevant_tests.txt`.
- It operates on the real repo inside the container via `docker exec` (edit files under
  `$HILBENCH_REPO`, default `/app`). Source files only.

## Prompt

> Examine the problem statement, requirements, interfaces, and blocker resolutions. Treat
> these as the entire user request, and produce a solution that can be applied to the
> codebase to satisfy it.
>
> Modify the codebase inside the container (under the repo root) to implement the request.
> You are NOT allowed to ask any clarifying questions. If any implementation detail is
> unclear even after considering the resolutions, assume parameters/values using your best
> judgement. Do not leave placeholder values or placeholder implementations — implement
> everything fully, as if it were the final product.
>
> Important: do NOT modify any test files (nothing under a `test`/`tests` directory or any
> test in the codebase). Modify source files only.
>
> Inputs:
> - Problem Statement: contents of `$DELIVERABLES/modified_problem_statement.txt`
> - Requirements: contents of `$DELIVERABLES/modified_requirements.txt`
> - Public Interface: contents of `$DELIVERABLES/modified_public_interfaces.txt`
> - Blocker Resolutions (id + resolution text only):
>   `<blocker_id_1>`: <resolution text>
>   `<blocker_id_2>`: <resolution text>
>   ...
>
> Work by running `docker exec "$HILBENCH_CONTAINER" ...` to read and edit files under
> `$HILBENCH_REPO`. When finished, stop. Do not create commits.

## After the solve

The caller runs `scripts/evaluate_check.sh check2`, which captures your changes as a
patch, applies the (hidden) obstructed test patch + your patch via `task_checker.py`, and
maps the result:

- `PASS` — your patch passed all relevant tests ⇒ the task is solvable with the
  resolutions. Good.
- `FAIL_UNSOLVABLE` — some relevant tests still fail with the resolutions ⇒ the spec is
  underspecified or golden/tests are misaligned; add the missing detail to the correct
  artifact and regenerate.
