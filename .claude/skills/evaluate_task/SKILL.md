---
description: Evaluates the quality of a task using Harbor. Use this skill when the user asks to evaluate the task or blockers, or mentions running checks on the task or blockers.
---

## Overview
This is an end-to-end evaluation of a HiL-Bench task with injected "blockers" (aka ambiguities) using the SWE-Agent harness on the Harbor framework. **Follow these evaluation steps exactly.**

Use the `hil_bench_agent_swe_input_validation.py` and `custom_eval.py` files as references for the exact logic in running these input validation steps, though keep in mind not every line of code there is necessary for this purpose.

## Resolve paths first (non-interactive)
Resolve these once and use them everywhere; print them. Do NOT pause to ask the user for anything that can be read from disk — only ask if a required file genuinely cannot be found.
- `$TASK_FILES` — the directory containing `task_info.txt`. Try in order: `$HILBENCH_TASK_FILES` (if it contains `task_info.txt`), `/app/task_files`, `/home/sandbox/task_files`, `./task_files`, `.`, then a shallow `find . /app /home/sandbox -maxdepth 3 -name task_info.txt`.
- `$DELIVERABLES` — the injection output directory (the obstructed task). Try `$HILBENCH_DELIVERABLES`, else `<parent of $TASK_FILES>/deliverables`.
If `task_info.txt`, the `modified_*` artifacts, or `blocker_registry.json` cannot be found, early exit with `REQUIRED_INPUT_FILE_MISSING` (the authoring/injection stages — e.g. `/hilbench-inject` — must run first to produce the obstructed artifacts).

## Evaluation Steps
### 1) Resolve required fields
Resolve the fields below from disk (do NOT ask the user unless a file is genuinely missing). The task evaluated here is the OBSTRUCTED task authored by the pipeline's injection stage, so prefer the `$DELIVERABLES` artifacts over the originals:
- Original instance ID, repo name, programming language, base image tag, base commit hash → `$TASK_FILES/task_info.txt`
- Problem statement text → `$DELIVERABLES/modified_problem_statement.txt`
- Requirements text → `$DELIVERABLES/modified_requirements.txt`
- Public interfaces text → `$DELIVERABLES/modified_public_interfaces.txt`
- Blocker registry (each entry: id, description, resolution, trigger questions) → `$DELIVERABLES/blocker_registry.json`
- Relevant tests (tests_to_pass) → `$DELIVERABLES/relevant_tests.txt`
- setup_patch.diff → `$DELIVERABLES/setup_patch.diff` if present (Scenario 2), else none
- golden_patch.diff → `$DELIVERABLES/golden_patch_obstructed.diff` (Scenario 2), else `$TASK_FILES/golden_patch.diff` (Scenario 1)
- test_patch.diff → `$DELIVERABLES/test_patch_obstructed.diff` (Scenario 2), else `$TASK_FILES/test_patch.diff` (Scenario 1)

Additional required fields (auto-download, do not ask):
- run_script.sh and parser.py → `https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/main/run_scripts/<instance-id>/run_script.sh` and the equivalent `parser.py`.

### 1.5) Incremental re-run planning (skip unchanged stages)
This grade is expensive (up to 12 SWE-Agent runs), so on a re-run only the stages whose inputs
changed need to execute. Ask the planner what to do (it compares a content hash of each stage's
dependency fields against the last run's manifest at `$DELIVERABLES/harbor/eval_manifest.json`):

```bash
python3 skills/evaluate_task/eval_rerun.py plan \
  --task-files "$TASK_FILES" --deliverables "$DELIVERABLES" [--scenario 1|2] [--fresh]
```

- **First run** (no manifest) or **`--fresh`** → every stage prints `RUN`; do the full grade.
- Otherwise each of `input_validation`, `check1`, `check2` prints `RUN` or `SKIP reuse=<verdict>`.
  For a `SKIP`, do NOT re-run that stage — reuse the printed previous verdict. For a `RUN`, execute
  that stage below as normal.
- Honor the `PLAN image_rebuild YES|NO` and `PLAN test_sh_rebuild YES|NO` hints in step 3 (but ALWAYS
  build if the Docker image is absent in this session — images do not persist across sandboxes).
- The full dependency map + rationale is documented in
  `~/.claude/skills/hilbench-pipeline/references/evaluation-rerun.md`.

Pass `--fresh` (from `/hilbench-evaluate-full --fresh`) to force a complete re-grade — recommended
as the final sign-off before a task is considered done, since the skip logic is only as sound as
the dependency map.

### 2) Input validation
**Skip if the plan said `input_validation SKIP`** (reuse the previous verdict; proceed only if it
was PASS). Otherwise run input validation on the fields used for task creation. Reference
skills/input_validation/SKILL.md for the complete walkthrough. **Proceed with the following
evaluation steps ONLY if input validation passes.**

### 3) Image building
**Honor the plan's rebuild hints.** If both checks were `SKIP`, you do not need the image at all.
If `PLAN image_rebuild NO` and the task image from a prior run still exists in this session, reuse
it; only `PLAN test_sh_rebuild YES` means you must regenerate `test.sh` (not the image). If the
image is absent (new sandbox), build it regardless. Otherwise:

Build the new image, based on the base image, for this task, and update the Dockerfile where needed. Specific steps:
- Get the base image
- Check out to the base commit hash
- Apply the setup patch, if exists, else skip this step
- Delete every copied `setup_patch.diff` file BEFORE creating the fresh commit. The resulting
  code is visible, but the patch itself reveals removed clues and must not exist in the worktree
  or recoverable git history.
- Nuclear reset the git history (`rm -rf .git`), make sure no branches are left in the repo
- Create exactly one fresh commit, "Initial commit for SWE-agent", using placeholder credentials
- Make sure `git rev-list --count HEAD == 1`
- Create the new image FROM THE BASE IMAGE with the repo at this current working state. 
    - Required directories:
        - `/app`: where the repo lives. Set this as default working directory (`WORKDIR`)
        - `/testbed`: symlink to `/app`
        - `/root`: put run_script.sh and parser.py in here
    - Required tweaks:
        - ENV `PIP_INDEX_URL=https://pypi.org/simple/`, overrides a broken base image pip config that points to a non-existent 127.0.0.1:9876
        - `/root/.bashrc` gets `PATH` entries for Go (`/go/bin`, `/usr/local/go/bin`) and Node (`/usr/local/node/bin`)
        - Install `patch` utility if missing
        - Set `ENTRYPOINT ["/bin/sh", "-c", "sleep infinity"]`
- Update the Dockerfile to reflect the new image

### 3.5) Route the SWE-Agent runs through the sandbox proxy (REQUIRED in the sandbox)
The sandbox has **no per-provider API keys**. Every model call must go through the anonymizer
"blind" proxy (which listens on the host loopback and holds the real upstream credentials). A bare
`harbor run -m <model>` lets litellm resolve provider-direct keys from the container env, so
`gemini/...` hard-fails with `GEMINI_API_KEY unset` (and gpt/claude only "work" by accident). Wire
the proxy once, before firing any runs:

1. Run the helper — it resolves the anonymizer creds (env first, then `~/.claude/settings.json`
   `.env`) and rewrites each loopback proxy base URL to the rootless-docker host alias `10.0.2.2`
   (slirp host-loopback), keeping the same port/path, so the swe-agent **container** can reach the
   host proxy. It then preflight-probes that URL from a throwaway container:
   ```bash
   skills/evaluate_task/sandbox_proxy_setup.sh --out-dir "$DELIVERABLES"
   ```
   Under rootless Docker the container runs in RootlessKit's own network namespace, so `127.0.0.1`
   and the legacy docker bridge gateway `172.17.0.1` do not reach the host proxy — only `10.0.2.2`
   does. Override the alias with `--host-alias IP` / `$SANDBOX_HOST_ALIAS` if the platform differs.
   It writes `$DELIVERABLES/.hilbench_proxy_env.sh` (key exports + in-container base URLs) and
   `$DELIVERABLES/.hilbench_proxy_agent_env.json` (the JobConfig `agents[].env` block). If preflight
   fails, STOP and report the exact URL/var it prints — do not spend the 12 runs.
2. `source "$DELIVERABLES/.hilbench_proxy_env.sh"` in the shell you launch harbor from, so harbor
   can resolve the `${...}` key templates in the env block.
3. **Scope:** this touches ONLY the SWE-Agent agentic-check path. Do NOT alter the LLM-judge/eval
   calls (`custom_eval.py` → `LITELLM_BASE_URL` / `PUBLIC_LITELLM_BASE_URL` with `HIL_BENCH_*` keys);
   that is a separate, working mechanism.
4. No on-host forwarder is started, so there is nothing to tear down; `--stop` remains a harmless
   no-op kept for compatibility.

### 4) Set up for checks 1 and 2
For check 1, what we call "baseline" mode, create a new Harbor task based on the existing one, but with the new Dockerfile above, a new `instruction.md`, and a new `test.sh`. The `instruction.md` must be formatted as follows:
"""
# PROBLEM STATEMENT
[user's provided problem statement text here]

# REQUIREMENTS
[user's provided requirements text here]

# PUBLIC INTERFACES
[user's provided public interfaces text here]
"""

The new `test.sh` must incorporate the `run_script.sh`, `parser.py` (if needed), and the relevant tests/tests_to_pass provided by the user. `test.sh` is used by Harbor to evaluate the agent's solution, so make sure all tests we care about are supported.

For check 2, what we call "full info" mode, create a new Harbor task exactly the same as the check 1 task. The only difference is the check 2 `instruction.md` must ADDITIONALLY have this at the bottom:
"""
# BLOCKER DETAILS
## [blocker 1 description]
[blocker 1 resolution]

## [blocker 2 description]
[blocker 2 resolution]

...
"""

For both checks 1 and 2, run SWE-Agent via generated Harbor **JobConfigs** — **not** a bare
`harbor run -p ... -m ...`, which bypasses the proxy. Each config's agent block must carry the
`env` block from step 3.5.

Build **one config per model per check**, each with one agent, `n_attempts: 2`, and
`n_concurrent_trials: 2`. Keeping models in separate Harbor processes prevents one provider,
key, or model outage from aborting the other model lanes. The required models are "gpt-5.4",
"claude-sonnet-4-6", and "gemini/gemini-3.1-pro-preview-customtools". Template for one lane:

```json
{
  "job_name": "hilbench-check1-gpt",
  "jobs_dir": "<out>/check1-runs/gpt",
  "n_attempts": 2,
  "n_concurrent_trials": 2,
  "environment": {"type": "docker"},
  "agents": [
    {"name": "swe-agent", "model_name": "gpt-5.4", "env": {"__FROM__": ".hilbench_proxy_agent_env.json"}}
  ],
  "tasks": [{"path": "<check1 baseline task path>"}]
}
```

Replace each `{"__FROM__": ...}` with the literal object in `$DELIVERABLES/.hilbench_proxy_agent_env.json`
(base URLs are literals; keys are `${VAR}` templates harbor resolves from the env you sourced in
step 3.5). Create equivalent configs for Claude and Gemini and for Check 2's full-info task.

Launch all model lanes for a check through the fault-isolating runner:

```bash
python3 skills/evaluate_task/run_harbor_models.py \
  --check check1 \
  --model-config "gpt-5.4=$DELIVERABLES/harbor/configs/check1-gpt.json" \
  --model-config "claude-sonnet-4-6=$DELIVERABLES/harbor/configs/check1-claude.json" \
  --model-config "gemini/gemini-3.1-pro-preview-customtools=$DELIVERABLES/harbor/configs/check1-gemini.json" \
  --state-file "$DELIVERABLES/harbor/check1-model-state.json" \
  --logs-dir "$DELIVERABLES/harbor/check1-launcher-logs"
```

Use the analogous `check2-*` paths for Check 2. The runner starts all non-dead model processes
concurrently, always waits for every lane, and never lets one lane's non-zero exit cancel its
siblings. A model persisted as `DEAD` is skipped on subsequent invocations. Use `--retry-dead`
only after the key, model id, quota, or provider status has been fixed. If a provider status
check or known credential diagnosis proves a model unavailable before launch, pass
`--mark-dead "MODEL=reason"`; the runner persists and skips that lane without spending a trial.

When both checks are marked RUN by the incremental plan, launch the Check 1 and Check 2 runner
commands themselves concurrently after both task variants/config sets are ready. Each runner
fans out its three model lanes, so all eligible trials across both checks progress together.
Wait for both runner processes before grading either check; a failed/dead lane in one check must
not cancel the other check batch.

### 4.5) Model dead states and quorum

The runner marks only the affected model `DEAD` for definitive model-route failures:
`AUTH_BLOCKED` (401/403, missing/revoked/disabled key), `MODEL_UNAVAILABLE` (invalid, removed,
or disabled model), `PROVIDER_DOWN` (provider/model unavailable after Harbor exits), or
`QUOTA_BLOCKED`. Normal task failure, the 200-step limit, test failure, and shared Docker/task
errors are NOT dead-model signals.

After all lanes finish:
- `COMPLETED` models contribute their completed trials normally.
- `DEAD` models are excluded; never count one as task pass or task fail.
- `FAILED` without a dead reason is an infrastructure error and does not count as a model
  result.
- At least two completed model lanes are required. With fewer than two, emit
  `CHECK <n> (Harbor): INCOMPLETE`, persist the model states/reasons, and do not manufacture a
  task-quality PASS/FAIL.
- Record all three model states, skipped attempts, reason codes, and log paths in
  `$DELIVERABLES/harbor/evaluation_feedback.md`.

More details on the actual checks below.

### 5) Check 1
**Skip if the plan said `check1 SKIP`** — reuse the printed previous verdict and its recorded
feedback in `$DELIVERABLES/harbor/evaluation_feedback.md`; do not fire the 6 runs. Otherwise:

Check 1 definitions:
- A run "passes" the task if its solution passes ALL relevant tests, otherwise it "fails". **Agents must run for a maximum of 200 steps; if the run cannot complete the task in that time then it's a fail.**
- A model "passes" the task if at least one of its run passes, otherwise it "fails".
- A blocker is "guessed" if ALL models guess it in ANY of their runs. E.g. if model 1 guesses in run 1, model 2 guesses in run 2, and model 3 guesses in run 1, the blocker is considered guessed. It does NOT count as guessed if, say, model 1 guesses in both runs and model 2 guesses in both runs, but model 3 doesn't guess at all.

To determine if a blocker is guessed, use the logic in `identify_blockers_guessed_swe.jinja2`.

Check 1 passes if BOTH of the following hold:
- At least 2 models FAIL the task
- Zero blockers are guessed

Wait for every non-dead Check 1 model lane before determining the verdict. If fewer than two
lanes completed, return `INCOMPLETE` rather than PASS/FAIL. Otherwise determine the result from
completed lanes only. If not enough models failed the task, summarize the trajectories that
PASSED the task with `summarize_trajectory_swe.jinja2` and show them to the user, along with the
corresponding patches generated by the agent. If any blockers were guessed, tell the user which
ones and which models/runs guessed them.

**Persist the feedback (deliverable).** In addition to showing it in chat, write this Check 1 result to `$DELIVERABLES/harbor/evaluation_feedback.md` (create the `harbor/` dir and the file if needed; add or replace a `## Check 1` section): the PASS/FAIL verdict, the per-model/per-run pass-or-fail table, any guessed blockers (with which models/runs guessed them), and the `summarize_trajectory_swe.jinja2` summaries of any PASSING trajectories with the path to each agent patch. This file must be written even when the automated LITELLM judge is unavailable and you are judging manually.

### 6) Run check 2
**Skip if the plan said `check2 SKIP`** — reuse the printed previous verdict and its recorded
feedback in `$DELIVERABLES/harbor/evaluation_feedback.md`; do not fire the 6 runs. Otherwise:

Check 2 definitions:
- A run "passes" the task if its solution passes ALL relevant tests, otherwise it "fails". **Agents must run for a maximum of 200 steps; if the run cannot complete the task in that time then it's a fail.**
- A run "technically passes" the task if it's considered to fail, BUT its failure was due to some non-task-related factor.
- A model "passes" the task if at least one of its runs actually passes OR technically passes.

To determine if a run "technically passes," use the logic in `classify_trajectory_technically_passed_swe.jinja2`.

Check 2 passes if at least 2 models pass.

Wait for every non-dead Check 2 model lane. If fewer than two lanes completed, return
`INCOMPLETE` rather than PASS/FAIL. Otherwise determine the result from completed lanes only.
If it's a fail, summarize the trajectories that FAILED with `summarize_trajectory_swe.jinja2`
and show them to the user, along with the corresponding patches generated.

**Persist the feedback (deliverable).** In addition to showing it in chat, write this Check 2 result to `$DELIVERABLES/harbor/evaluation_feedback.md` (add or replace a `## Check 2` section): the PASS/FAIL verdict, the per-model/per-run pass / technically-pass / fail breakdown, and the `summarize_trajectory_swe.jinja2` summaries of any FAILING trajectories with the path to each agent patch. This file must be written even when judging manually.

### 7) Provide overall verdict
The task's overall evaluation result is a PASS **only if both check 1 and check 2 pass.** If
either check is `INCOMPLETE`, the overall result is `EVALUATION: INCOMPLETE` (infrastructure /
model availability, not task quality). Otherwise, the task fails. Compile all verdicts and
feedbacks to the user in an organized, clear manner.

**Persist the verdict (deliverable).** Finalize `$DELIVERABLES/harbor/evaluation_feedback.md` so it is a self-contained record: it must start with an `# Evaluation feedback` header and an `## Overall verdict` section (`EVALUATION: PASS` only if BOTH checks pass, else `EVALUATION: FAIL`, stating which check(s) failed and why), followed by the `## Check 1` and `## Check 2` sections written above. Always write this file — it is the captured artifact for review, regardless of whether the automated LITELLM judge ran or you judged manually.

### 8) Update the re-run manifest (required)
Record this run's field hashes + per-stage verdicts so the next `/hilbench-evaluate-full` can skip
unchanged stages. Pass the verdict for every stage you determined this run — for a stage you SKIPPED,
pass the reused verdict (omit a flag only if that stage has no verdict at all):

```bash
python3 skills/evaluate_task/eval_rerun.py update \
  --task-files "$TASK_FILES" --deliverables "$DELIVERABLES" [--scenario 1|2] \
  --input-validation PASS|FAIL --check1 PASS|FAIL|INCOMPLETE --check2 PASS|FAIL|INCOMPLETE
```

This writes `$DELIVERABLES/harbor/eval_manifest.json` (current hashes + verdicts + UTC timestamp).
It is the source of truth for the next incremental plan — do not hand-edit it. If input validation
FAILED (so the checks never ran), still update with `--input-validation FAIL` and omit the checks.