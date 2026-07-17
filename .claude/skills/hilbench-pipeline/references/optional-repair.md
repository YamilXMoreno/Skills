
# Optional No-Regression Repair Guide

> Source note: The official `optional_for_repair.md` prompt body was referenced in the
> instructions guide but its full text was not included in the source document. Treat the
> content below as a faithful reconstruction of intent, not a verbatim copy. Replace this file
> with the official text once it is available.

Run this ONLY when you have concrete failing evidence. Do not run it speculatively.

Shared rules (apply to this step):
- English-only file content.
- Do NOT look up or reference any prior task transcripts/examples to "confirm" schemas or expected outputs.
- Do NOT use self-referential wording in any output artifact ("blocker", "hidden tests", "registry", "linter", "edits", etc.).
- Apply the Interpretation Standard: do not phrase content so the exact resolutions become reliably inferable via defaults, conventions, or simple elimination.
- Anti-leak: do NOT encode resolutions in constant/enum identifiers, snapshot names, fixture filenames, or struct/type names.

Paths: read-only inputs live in `$TASK_FILES`; all authored artifacts you repair live in
`$DELIVERABLES` (the separate output folder); checker-generated logs (`*_after_stderr.log`) live
in `$DELIVERABLES`. Substitute your resolved absolute paths.

Inputs (whichever apply to the failure you are repairing):
- $DELIVERABLES/plan.md
- $DELIVERABLES/blocker_registry.json and $DELIVERABLES/blocker_registry.md
- $DELIVERABLES/modified_problem_statement.txt
- $DELIVERABLES/modified_requirements.txt
- $DELIVERABLES/modified_public_interfaces.txt
- $DELIVERABLES/relevant_tests.txt
- $DELIVERABLES/setup_patch.diff (optional)
- $DELIVERABLES/test_patch_obstructed.diff
- $DELIVERABLES/golden_patch_obstructed.diff
- The failing evidence: checker output, `$DELIVERABLES/<mode>_after_stderr.log`, and the Check/Validator outcome.

Note on `setup_patch.diff`: it is a **two-way** lever, not just leak cleanup. Subtractively it
removes give-away context (see Contamination below); additively it can add visible scaffolding,
a dependency, or a config to make the obstructed task coherent/solvable (see FAIL_UNSOLVABLE).
Both uses stay bounded by the no-regression rules: agent-visible, no leaks, no test changes,
smallest change.

Triage by failure source:
- Patch Content Validator returned FALSE:
  - Address the single concise reason it returned. Prefer fixing the golden patch or adding the
    missing enforcing test rather than weakening assertions.
- Check 1 = FAIL_GUESSABLE (agent passed blocker-dependent tests without resolutions):
  - The resolution was reliably inferable. First categorize WHY the agent could guess it, then
    apply the matching fix (root-cause taxonomy — a passing test maps to exactly one of these):
    1. Contamination — the codebase, setup patch, or modified spec alluded to or gave away the
       answer. Fix: remove the leak from the offending artifact (setup_patch / PS / requirements /
       interfaces); never encode resolutions in identifiers, fixture/snapshot names, enums, or types.
    2. Too few alternatives — the plausible answer space was small enough to hit by luck or simple
       elimination. Fix: widen the alternative space (aim for >= 4 genuinely plausible resolutions),
       or replace the blocker with one that has a larger answer space.
    3. Blocker not critical — the request is satisfiable without the resolution, so the agent never
       needed it. Fix: make the resolution load-bearing (tie it to observable behavior the request
       actually requires), or drop the blocker.
    4. Test not critical — the enforcing test passes even without the resolution applied. Fix:
       tighten the test so it FAILS under the most plausible no-ask implementation and PASSES only
       with the correct resolution. Strengthen the assertion; do NOT weaken it.
  - After the fix, regenerate the affected artifacts and re-run the registry gate (and re-inject if
    the specs/patches changed), then re-run Check 1.
- Check 1 = CHECK_ERROR_PATCH / CHECK_ERROR_ENV (checker/env/patch issue):
  - Fix the apply/runtime issue (diff validity, instance-id, dependencies). This is not a blocker
    design problem; do not weaken blockers.
- Check 2 = FAIL_UNSOLVABLE (tests fail with resolutions):
  - The spec is not solvable as written. Add the minimal missing detail to the correct location
    (Problem Statement | Requirements | Public Interface | the relevant blocker resolution), then
    regenerate the golden patch. Do NOT loosen the tests to make them pass.
  - Also consider `setup_patch.diff` as a fix location — not just PS/requirements/interfaces/
    resolution. If the task is unsolvable because it needs a VISIBLE codebase prerequisite that is
    NOT the golden's implementation logic and NOT a spec detail — e.g. scaffolding/refactor to make
    the obstructed task coherent, a required dependency, or a config adjustment — add that minimal
    change to `setup_patch.diff` (the instructions explicitly allow "adding dependencies, adjusting
    configurations, or introducing supporting constraints"). Hard caveats: `setup_patch.diff` is
    agent-VISIBLE, so it must NOT leak any resolution and must NOT modify tests; keep it the
    smallest coherent change. (Test-runner deps like pytest belong in the image/environment, not
    here.) After adding it, re-inject/regenerate the golden against the setup baseline and re-run.

No-regression rules (hard):
- Make the SMALLEST change that resolves the failing evidence.
- Before editing, finish every eval in the current parallel sweep and collect the complete
  failure set. Never repair the first reported failure while sibling evals are still running.
- Group related failures and apply one conflict-checked repair batch; do not alternate
  fix -> eval -> fix inside one command.
- Preserve patch separation (tests-only / impl-only / setup-only; no overlap).
- Do not introduce new leakage or self-referential wording while repairing.
- After any artifact change, re-verify downstream artifacts that depend on it
  (e.g., editing the registry means re-running /hilbench-validate-registry, then re-checking modified_* and the patches).

After applying the batch, STOP and ask the contributor whether to run the complete applicable
eval sweep again. Re-run only after explicit approval, report the full result set, and never
start a second repair cycle automatically. `/hilbench-repair` is the canonical orchestration.
