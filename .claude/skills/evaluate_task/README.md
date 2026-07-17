# evaluate_task (bundled worker skill)

The authoritative, end-to-end **Harbor** grade for a HiL-Bench obstructed task. It builds the
task image, runs SWE-Agent across multiple models, and decides whether the task passes both
agentic checks. This is the heavy, expensive grade (12 SWE-Agent runs).

## How it's invoked

You normally don't run this skill directly. It is driven by the pipeline:

- `/hilbench-evaluate-full` — full path: input validation → both checks → overall verdict.
  **Incremental on re-run** (see below).
- `/hilbench-evaluate-check1` — re-runs just Check 1.
- `/hilbench-evaluate-check2` — re-runs just Check 2.

It delegates its input-validation step to the sibling `input_validation` skill.

## Incremental re-run

On a re-run, `eval_rerun.py` compares a content hash of each stage's input files against the last
run's manifest (`$DELIVERABLES/harbor/eval_manifest.json`) and skips the stages whose inputs are
unchanged, reusing their verdict. `SKILL.md` step 1.5 runs the planner; step 8 updates the manifest.
`/hilbench-evaluate-full --fresh` forces a full re-grade. The dependency table (which change
re-triggers which stage) is in `~/.claude/skills/hilbench-pipeline/references/evaluation-rerun.md`.

## What it does (following `SKILL.md`)

1. **Resolve inputs** from disk (prefer the `$DELIVERABLES` obstructed artifacts).
2. **Input validation** (via the `input_validation` skill) — proceed only if it passes.
3. **Image build** — base image → base commit → apply setup patch → single fresh commit →
   new task image with `/app`, `/testbed`, `/root` and the required env tweaks.
4. **Sandbox proxy routing** — `sandbox_proxy_setup.sh` rewrites the anonymizer proxy base URL to
   the rootless-docker host alias `10.0.2.2` so in-container model calls reach the host proxy
   without exposing raw API keys (required in the sandbox).
5. **Two task versions** — baseline `instruction.md` (Check 1) and a full-info `instruction.md`
   with a `# BLOCKER DETAILS` section (Check 2).
6. **12 SWE-Agent runs** — 3 models × 2 runs × 2 checks, via a Harbor JobConfig, in parallel.
7. **Verdicts** — Check 1 passes if ≥2 models FAIL and zero blockers are guessed; Check 2
   passes if ≥2 models pass. Overall `EVALUATION: PASS` only if both checks pass.
8. **Persist feedback** to `$DELIVERABLES/harbor/evaluation_feedback.md` (verdict + per-run
   detail), always — even when judging manually.

The three agent models are `gpt-5.4`, `claude-sonnet-4-6`, and
`gemini/gemini-3.1-pro-preview-customtools`.

## Contents

| File | Role |
|------|------|
| `SKILL.md` | The entry point the agent follows (authoritative steps) |
| `sandbox_proxy_setup.sh` | Rewrites the anonymizer proxy base URL to the rootless host alias (10.0.2.2); emits env |
| `sandbox_proxy_bridge.py` | Legacy TCP forwarder (unused under the host-alias rewrite; kept for compat) |
| `eval_rerun.py` | Incremental re-run planner (`plan` / `update`) + the manifest it maintains |
| `custom_eval.py` | Harbor custom grader / eval helpers |
| `hil_bench_agent_swe_input_validation.py` | Shared input-validation reference logic |
| `summarize_trajectory_swe.jinja2` | LLM-judge prompt: summarize an agent trajectory |
| `classify_trajectory_technically_passed_swe.jinja2` | LLM-judge prompt: "technically passed?" |
| `identify_blockers_guessed_swe.jinja2` | LLM-judge prompt: which blockers were guessed |

See `SKILL.md` for the exact, authoritative procedure.
