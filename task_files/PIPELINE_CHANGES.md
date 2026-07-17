# HiL-Bench Pipeline Changes

This document summarizes the orchestration, validation, repair, prescreen, Harbor, leak
prevention, patch hygiene, and dependency-provisioning changes added to this sandbox.

## 1. Parallel static evaluations

- `/hilbench-run-evals` launches one isolated read-only worker per selected model eval and runs
  all selected evals concurrently.
- `/hilbench-run-eval` accepts one or more named evals; multiple requested names run in parallel
  while a single name keeps the same targeted behavior.
- `/hilbench-validate-registry` keeps the mechanical schema check first, then runs all registry
  model evals and linters concurrently.
- `/hilbench-validate-artifacts` runs patch-content, deferred model evals, and codebase-leak
  analysis concurrently after a fast mechanical patch preflight.
- Parent agents only aggregate worker results; they do not repeat or override worker judgments.
- Each eval is also internally sharded when possible: per blocker, blocker pair, trigger
  question, relevant test, artifact, requirement, or A/B/C criterion. This applies even when
  only one eval is requested.
- Shards use strict all-pass aggregation; every failed shard reason is preserved.
- Results are emitted in canonical order rather than completion order.
- Stateful checkout, patch-application, and FAIL-to-PASS operations remain ordered to avoid
  shared-repository races.

## 2. Parallel validation stages

- `/hilbench-validate-input` runs test-existence, test-list coverage, and isolated patch
  application checks concurrently after preparing the baseline.
- `/hilbench-validate-original` runs relevant-test extraction and environment/patch preflight
  concurrently before the ordered dynamic check.
- `/hilbench-validate-obstructed` runs environment, artifact-format, and test-list checks
  concurrently before the ordered dynamic check.
- Registry and artifact gates wait for every worker before returning a final verdict.

## 3. Parallel Check 1 and Check 2 prescreen

- Added `/hilbench-prescreen`.
- It prepares one clean baseline and creates separate detached worktrees for Check 1 and Check 2.
- The resolution-free Check 1 solve and resolution-aware Check 2 solve run concurrently.
- Both patches are shown to the contributor at the existing hard review pause.
- After approval, both checker evaluations run concurrently in their assigned worktrees.
- `evaluate_check.sh` now honors explicit `--repo` and `--container` overrides so parallel
  worktrees remain isolated.
- Individual `/hilbench-check1` and `/hilbench-check2` commands remain available.

## 4. Parallel generation and injection

- One-shot and planned registry generation launch one isolated draft worker per blocker slot.
- Selective registry regeneration launches one worker per requested blocker id.
- Workers produce drafts only; the parent performs one whole-registry distribution,
  independence, overlap, schema, and leak review and writes the final registry once.
- Injection generates the modified problem statement, requirements, and public interfaces in
  parallel, followed by one serial cross-document consistency merge.
- Container patch generation remains ordered because its working-tree states are dependent.

## 5. Collect-all repair behavior

- Added `/hilbench-repair`.
- It launches every applicable eval before consuming failures.
- A first failure never cancels sibling evals or triggers an immediate fix.
- All failures are persisted to `deliverables/repair/diagnostic_sweep.txt`.
- Failures are grouped by root cause and artifact, then applied as one conflict-checked repair
  batch.
- Repair summaries are stored in `deliverables/repair/repair_batch.txt`.
- After applying fixes, the command asks whether the contributor wants to run the complete eval
  sweep again.
- There is no automatic fix-to-eval-to-fix loop. A further repair cycle requires explicit user
  approval.

## 6. Harbor model isolation and dead states

- Harbor grading now uses one JobConfig and one Harbor process per model instead of one combined
  multi-model process.
- When the full grade must run both checks, Check 1 and Check 2 batches launch concurrently, and
  each batch concurrently launches its three model lanes.
- Added `evaluate_task/run_harbor_models.py` to launch model lanes concurrently and wait for all
  lanes even if one fails.
- Each of the three configured models has an independent persisted state:
  - `COMPLETED`
  - `DEAD`
  - `FAILED`
- Definitive model-route failures are classified as:
  - `AUTH_BLOCKED`
  - `MODEL_UNAVAILABLE`
  - `PROVIDER_DOWN`
  - `QUOTA_BLOCKED`
  - `PREFLIGHT_DEAD`
- A known unavailable model can be skipped before launch with
  `--mark-dead "MODEL=reason"`.
- Persisted dead models remain skipped until explicitly retried with `--retry-dead`.
- Dead models do not count as task passes or task failures.
- At least two completed model lanes are required. Otherwise the check and overall grade are
  `INCOMPLETE`, not a task-quality failure.
- Incremental manifests were upgraded to version 2. An `INCOMPLETE` stage is never reused and is
  planned to run again.

## 7. Stage 1 registry leak and flaw validation

- Added the `registry_field_leakage` linter.
- It checks every blocker description, resolution, and trigger question.
- Descriptions fail if they state or strongly narrow their resolution, embed answer-bearing
  examples/defaults, expose another resolution, or leave too few plausible answers.
- Resolutions fail if they contain alternatives, placeholders, vague rules, contradictions,
  answer-bearing names, or another blocker's decision.
- Trigger questions fail when they are leading, expose the correct option, copy constants or
  examples from the answer, or leak another blocker.
- The combined description and questions are evaluated without the resolution; the blocker
  fails if the answer becomes reliably inferable.
- Registry generation prompts now apply the same field-level anti-leak rules before writing.

## 8. Stage 2 codebase leak removal

- The agent-visible parent repository is audited once per blocker for clues in source, comments,
  documentation, examples, fixtures, defaults, identifiers, configuration, errors, and existing
  behavior.
- `setup_patch.diff` must remove or neutralize every safely removable codebase clue.
- The post-setup repository is audited again and must retain at least three credible resolution
  choices per blocker.
- Setup changes must not remove legitimate original requirements, damage unrelated behavior,
  modify tests, or introduce replacement clues.
- If the legitimate original contract itself reveals the answer, the blocker must be
  regenerated instead of hiding required behavior.
- The copied setup diff is deleted before the prescreen baseline commit. This prevents a solving
  agent from recovering removed clues through the worktree or Git history.
- Harbor image setup also requires copied setup-diff files to be deleted before creating its
  fresh commit.

## 9. Patch integrity and LF-only validation

- Added `scripts/validate_patch_artifacts.py`.
- Generated and original diffs must be UTF-8 with LF (`\n`) line endings only.
- The validator rejects:
  - CRLF or lone CR line endings
  - empty patches
  - missing final LF
  - invalid UTF-8
  - prose or Markdown fences around a diff
  - malformed or incomplete unified-diff blocks
  - duplicate file blocks
  - unsafe, absolute, parent-relative, or backslash paths
  - invalid or duplicate relevant-test entries
- Original and obstructed dynamic validators run this preflight before applying patches.
- Corrupt patches must be regenerated from a clean working tree rather than manually spliced.

## 10. Extra-test validation

- Original task tests are preserved, even when they are not blocker-specific.
- Every test or assertion introduced during blocker authoring must enforce at least one blocker.
- Newly authored tests that map to no blocker are removed.
- `blocker_test_map.txt` is used to verify blocker coverage and identify unmapped authored tests.
- After test removal, `relevant_tests.txt` and `blocker_test_map.txt` must be regenerated from
  the final test patch.

## 11. Stage 0 dependency provisioning

- Stage 0 now detects and installs dependencies declared by supported repository manifests:
  - Python: uv, Poetry, pip requirements, `pyproject.toml`, setuptools
  - Node.js: pnpm, Yarn, npm
  - Go: `go.mod`
  - Rust: `Cargo.toml`
  - Ruby: `Gemfile`
  - Java: Maven and Gradle
- This runs for Dockerfile builds, Docker Hub/registry pull commands, image tarballs, and
  URL-resolved images.
- Installation and verification output is stored in
  `deliverables/dependency_install.log`.
- After successful installation, Stage 0 snapshots the corrected container as a durable
  `hilbench/<instance>:deps-<id>` image.
- The dependency-complete image is exported as `HILBENCH_IMAGE` so downstream checks and Harbor
  do not revert to the incomplete base image.
- Missing runtimes, package managers, compilers, or native libraries produce
  `DEPENDENCY_INSTALL_FAILED` with instructions to correct `deliverables/Dockerfile`.
- Snapshot failures produce `DEPENDENCY_IMAGE_COMMIT_FAILED`.
- `--skip-dependency-check` is available only for diagnostic runs.

## 12. Verification performed

- Harbor runner/dead-state tests: 9 passing.
- Patch-format validator tests: 4 passing.
- Updated Python files compile successfully.
- Updated shell scripts pass `bash -n`.
- No IDE linter errors were reported for the modified files.
- Live Harbor and live Docker provisioning were not executed because their external runtime was
  not available during these edits.
