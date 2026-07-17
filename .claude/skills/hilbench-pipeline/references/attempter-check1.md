# Attempter Check 1 — solve prompt (resolution-FREE)

This is the prompt body for the FRESH subagent that performs the Check 1 solve. Check 1
verifies the blockers are NOT reliably inferable: a capable agent, given only the
obstructed problem statement / requirements / interfaces (and NO resolutions, NO tests,
NO golden patch), should NOT be able to produce a patch that passes the blocker-enforcing
tests.

## Preconditions (already handled by the caller, do not repeat)

- `scripts/prepare_check.sh check1` has reset the repo to a clean, committed baseline
  (parent commit, plus `setup_patch.diff` if any) and scrubbed all answer-key files
  (test/golden patches, relevant tests, blocker registry) from the container.
- The subagent runs in a clean context with NO memory of authoring the blockers.

## Isolation rules (hard)

- The solving subagent receives ONLY: `modified_problem_statement.txt`,
  `modified_requirements.txt`, `modified_public_interfaces.txt`.
- It MUST NOT be given: the test patch, the golden patch, `relevant_tests.txt`, the
  blocker registry, blocker resolutions, or any hidden values.
- It operates on the real repo inside the container via `docker exec` (edit files under
  `$HILBENCH_REPO`, default `/app`). It must NOT read files outside the repo that could
  contain the answer key.

## Prompt

> Examine the problem statement, requirements, and interfaces. Treat these as the entire
> user request, and produce a solution that can be applied to the codebase to satisfy it.
>
> Modify the codebase inside the container (under the repo root) to implement the request.
> You are NOT allowed to ask any clarifying questions. If any implementation detail is
> unclear, assume parameters/values/requirements using your best judgement and
> understanding of the codebase. Do not leave placeholder values or placeholder
> implementations — implement everything fully, as if it were the final product.
>
> Important: do NOT modify any test files (nothing under a `test`/`tests` directory or any
> test in the codebase). Modify source files only.
>
> Inputs:
> - Problem Statement: contents of `$DELIVERABLES/modified_problem_statement.txt`
> - Requirements: contents of `$DELIVERABLES/modified_requirements.txt`
> - Public Interface: contents of `$DELIVERABLES/modified_public_interfaces.txt`
>
> Work by running `docker exec "$HILBENCH_CONTAINER" ...` to read and edit files under
> `$HILBENCH_REPO`. When finished, stop. Do not create commits.

## After the solve

The caller runs `scripts/evaluate_check.sh check1`, which captures your changes as a
patch, applies the (hidden) obstructed test patch + your patch via `task_checker.py`, and
maps the result:

- `PASS` — your patch did NOT pass the blocker tests ⇒ blockers are not guessable. Good.
- `FAIL_GUESSABLE` — your patch passed without resolutions ⇒ a blocker is reliably
  inferable; the guessable blocker must be redesigned.

## Reconciliation with eval 7 (critical_implementation) — Test B

Check 1 IS the "no-clarification / no-ask agent run" that eval 7 (`eval-prompts.md` #7)
calls the **decisive** evidence for Test B (non-guessable). This is the final step of the
eval-7 split (registry gate did Test B/C statically; artifacts gate did Test A):

- `FAIL_GUESSABLE` here ⇒ eval 7 **Test B is FALSE** for the blocker(s) the agent solved,
  regardless of the earlier static verdict. Redesign that blocker (widen the plausible
  answers / remove leakage), re-run the registry gate, re-inject, and re-check.
- `PASS` here ⇒ empirical confirmation of the registry-gate Test B pre-screen.
