---
description: Stage 0 - Provision the SWEAP repo/container, verify the parent commit, install and verify manifest-declared repository dependencies, and persist a dependency-complete runtime image for downstream checks.
argument-hint: "[optional: --image REF --commit HASH --instance-id ID --container NAME --checkout --no-checkout --skip-dependency-check --validate-original]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first, then follow it exactly).

This is the FIRST stage. It provisions the runtime the later container-backed stages
(checks, evaluation) depend on. It does not touch deliverables authoring.

Do this:

1. Check whether `$ARGUMENTS` contains `--validate-original`. If so, remember it and REMOVE
   it from the argument string; call the remaining args `$PROVISION_ARGS` (it is a
   convenience flag handled by this command, not by the script).

2. Run the provisioning script (it resolves paths and parses `task_info.txt` itself):

```bash
bash scripts/provision_repo.sh $PROVISION_ARGS
```

   - It parses the docker image source (`docker_pull_command`), `base_commit_hash`, and
     `instance_id` from `task_info.txt`. Override with `--image`, `--commit`,
     `--instance-id`, `--container`, or the matching `HILBENCH_*` env vars.
   - A buildable `deliverables/Dockerfile` (a real `FROM …` recipe, not the tarball-stub
     comment) OVERRIDES the `task_info` image source and is built instead — precedence is
     `--image`/`HILBENCH_IMAGE` > `deliverables/Dockerfile` > `task_info` source. This is how a
     reviewer/contributor fixes an image that is missing dependencies: drop a corrected
     Dockerfile in `deliverables/` and re-provision. The image is tagged by the Dockerfile's
     content hash, so editing it always triggers a rebuild.
   - The image source may be a registry ref, a `docker pull …` command, OR a URL. If it is a
     URL, the script fetches it and figures out what it is: a Dockerfile (it runs
     `docker build`), an image tarball (it runs `docker load`), or a `docker pull …` command
     (it extracts and pulls). The built image gets a deterministic tag from `instance_id`, so
     re-runs are idempotent (no rebuild if it already exists).
   - By default the script AUTO-checks-out `base_commit_hash` when the built/pulled repo is not
     already there (common for date-frozen Dockerfiles). You do NOT need `--checkout` for that.
     Pass `--no-checkout` if you instead want a wrong baseline surfaced as `PARENT_COMMIT_MISMATCH`
     rather than auto-fixed. `--checkout` (force checkout up front) still works.
   - After checkout, it detects supported dependency manifests (`uv.lock`, Poetry/pip files,
     `package*.json`/Yarn/pnpm locks, `go.mod`, `Cargo.toml`, `Gemfile`, Maven, or Gradle), runs
     the matching deterministic install and verification command inside the container, and
     writes `$DELIVERABLES/dependency_install.log`.
   - This applies to Dockerfiles, Docker Hub/registry pull commands, tarballs, and URL-resolved
     images. If dependencies are installed, the script snapshots the corrected container into a
     new `hilbench/<instance>:deps-<id>` image and exports that image as `$HILBENCH_IMAGE`, so
     Harbor and later stages use the dependency-complete runtime rather than an ephemeral fix.
   - A missing runtime/package manager or failed install emits `DEPENDENCY_INSTALL_FAILED`.
     Add the required OS/runtime package to `$DELIVERABLES/Dockerfile` and re-provision; do not
     guess system packages. Use `--skip-dependency-check` only for diagnosis.

3. Interpret the result:
   - `PROVISION_OK` → tell the contributor to `source` the printed `.hilbench_env`
     file so `$HILBENCH_CONTAINER` / `$HILBENCH_INSTANCE_ID` etc. are set for later
     stages. Print a one-line `STAGE provision: DONE` summary (image, container, commit).
   - `PARENT_COMMIT_MISMATCH` → STOP. Do NOT proceed to authoring/injection: every
     patch is relative to the parent commit and a wrong baseline makes all downstream
     diffs invalid. This now only fires when the base commit could NOT be auto-checked-out —
     either it is not in the repo's history (re-check the image tag / that the clone includes
     it) or you passed `--no-checkout`. Report expected vs actual HEAD.
   - `UNRECOGNIZED_IMAGE_SOURCE` → STOP. The `docker_pull_command` value was a URL that the
     script fetched but could not classify as a Dockerfile, image tarball, or pull command.
     Report the sniffed content type it printed and check the source URL.
   - `IMAGE_BUILD_FAILED` → STOP, but note this is NOT a bad source URL — the source WAS
     recognized as a Dockerfile and the `docker build` itself failed. The script already retried
     with the legacy builder (`DOCKER_BUILDKIT=0`) to work around rootless-Docker/buildx breakage
     (e.g. it cannot chown `~/.docker/buildx/instances`), so both builders failed. Report the
     build error; it is an environment/Dockerfile problem, not a classification problem.
   - `IMAGE_LOAD_FAILED` → STOP. The source WAS recognized as an image tarball but `docker load`
     produced no image. Report the load error (environment/tarball problem, not a bad URL).
   - `DEPENDENCY_INSTALL_FAILED` → STOP. Inspect `dependency_install.log`. If a language runtime,
     compiler, native library, or package manager is absent, add it to the corrected
     `deliverables/Dockerfile`; if a lock/manifest is invalid, fix that input instead.
   - `DEPENDENCY_IMAGE_COMMIT_FAILED` → STOP. Dependencies installed in the current container,
     but the durable image snapshot failed; do not continue because Harbor would use the
     uncorrected base image.
   - `REQUIRED_INPUT_FILE_MISSING` / missing image or commit → STOP and tell the
     contributor exactly which field `task_info.txt` is missing or which flag to pass.

4. If (and only if) provisioning returned `PROVISION_OK` AND `--validate-original` was
   passed: immediately run the Stage 0.5 baseline check by following
   `commands/hilbench-validate-original.md` (build the throwaway original-tests list, then
   `bash scripts/validate_original.sh`). Report both `STAGE provision: DONE` and the
   `validate-original` verdict, then STOP.

By default (no flag) provisioning and original-patch validation are separate stages: they
have different failure modes (infra vs. task-soundness) and different re-run cadences
(you re-provision often; you validate originals once). Run `/hilbench-validate-original`
separately unless you explicitly want them chained.

Never auto-advance past the baseline into authoring. Provisioning is a review gate.
