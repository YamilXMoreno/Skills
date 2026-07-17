---
description: Input validation + full agentic checks (REQUIRED grade, heaviest) - input validation, then Harbor Check 1 + Check 2 (12 SWE-Agent runs), then the overall verdict (PASS only if both checks pass). Incremental by default - on a re-run it skips stages whose inputs are unchanged; --fresh forces a full re-grade. This is the required Stage 4 gate for every task. Delegates to input_validation + evaluate_task skills.
argument-hint: "[optional: --fresh, models, instance-id, or path overrides]"
---

Use the `hilbench-pipeline` skill for conventions, and delegate to the bundled
`input_validation` and `evaluate_task` skills (read their `SKILL.md`s and follow them
exactly). They ship with this package at `~/.claude/skills/{input_validation,evaluate_task}/`.

Scope: the full authoritative grading path (input validation then the agentic checks), the HEAVIEST option (12
SWE-Agent runs). This is the REQUIRED grade for every task — run it first, then re-run an
individual `/hilbench-evaluate-check1/2` to fix/recheck. It is distinct from the fast scripted
Attempter Checks (`/hilbench-check1/2`), which are an optional lightweight local pre-screen.

Resolve `$TASK_FILES` / `$DELIVERABLES` and all inputs from disk (prefer the `$DELIVERABLES`
obstructed artifacts). If any `modified_*` artifact or `blocker_registry.json` is missing,
STOP with `REQUIRED_INPUT_FILE_MISSING`. $ARGUMENTS may override the model list.

## Incremental re-run (default)

This command is **incremental**: on a re-run it only re-executes the stages whose inputs changed
since the last run, reusing the previous verdict for unchanged stages. This is driven by
`evaluate_task/SKILL.md` step 1.5, which runs `eval_rerun.py plan` against the manifest at
`$DELIVERABLES/harbor/eval_manifest.json`, and step 8, which updates that manifest.

- **First run** (no manifest present): every stage runs — a full grade.
- **`--fresh` in $ARGUMENTS**: forces a full re-grade regardless of the manifest. Remove `--fresh`
  from the argument string before passing the rest to the skill. **Recommended as the final
  sign-off** before a task is considered done, since the skip logic is only as sound as the
  dependency map.
- Which change re-triggers which stage is defined in
  `references/evaluation-rerun.md` (the authoritative dependency table).

Run end to end, following `evaluate_task/SKILL.md`:
0. Incremental plan (`eval_rerun.py plan`) — determine which of the three stages RUN vs SKIP.
1. Input validation (`input_validation` skill) — skip if the plan says so. Proceed ONLY if it passes (or its reused verdict was PASS).
2. Image build + both task versions (baseline for Check 1; full-info with `# BLOCKER DETAILS`
   for Check 2).
2b. Sandbox proxy routing (`evaluate_task/SKILL.md` step 3.5, REQUIRED in-sandbox): run
   `sandbox_proxy_setup.sh` to rewrite the anonymizer proxy base URL to the rootless host alias
   `10.0.2.2` (so the container can reach the host proxy), `source` the emitted env, and give each
   JobConfig agent the emitted `env` block. A bare `harbor run -m <model>` bypasses the proxy and
   fails on `GEMINI_API_KEY unset`. Do not touch the LLM-judge/eval path.
3. For each check marked RUN, generate one JobConfig per model. If both Check 1 and Check 2 are
   marked RUN, launch both `evaluate_task/run_harbor_models.py` check batches concurrently in
   one parent fan-out; each batch concurrently launches its three isolated model lanes. This
   permits all 12 trials (3 models x 2 attempts x 2 checks) to progress concurrently. If only
   one check is RUN, launch its three model lanes concurrently. Always wait for every launched
   lane across both checks. A dead/failed model lane or check batch MUST NOT cancel its siblings.
   Reuse the recorded verdict for a check marked SKIP.
4. Apply Check 1 criteria (>= 2 models fail AND zero blockers guessed) and Check 2 criteria
   (>= 2 models pass).
5. Overall verdict: `EVALUATION: PASS` only if BOTH checks pass. If either check has fewer than
   two completed model lanes, report `EVALUATION: INCOMPLETE`; otherwise report `EVALUATION: FAIL`,
   with organized per-component feedback (trajectory summaries, patches, guessed blockers).
   Also write the verdict + per-run feedback to `$DELIVERABLES/harbor/evaluation_feedback.md`
   (per `evaluate_task/SKILL.md`) so it is captured as a deliverable, not just shown in chat.
6. Update the manifest (`eval_rerun.py update`) with this run's hashes + per-stage
   PASS/FAIL/INCOMPLETE verdicts. INCOMPLETE checks are always planned to RUN next time; persisted
   DEAD model lanes remain skipped until explicitly retried after their availability issue is fixed.

STOP after printing the verdict. When reporting, note which stages ran vs. were skipped-and-reused.
