---
description: Tear down the HiL-Bench container when done (or to stop it during human review). Keeps the pulled image and all deliverables. STOPS after teardown.
argument-hint: "[optional: --stop-only --container NAME]"
---

Use the `hilbench-pipeline` skill (read `SKILL.md` first).

This stops/removes the disposable SWEAP container. It never deletes deliverables, and
by default keeps the pulled image so re-provisioning is fast.

Do this:

1. Run:

```bash
bash scripts/teardown.sh $ARGUMENTS
```

   - Default: stop and remove the container (`$HILBENCH_CONTAINER`, or `--container NAME`).
   - `--stop-only`: stop but keep the container so `docker start` resumes it quickly.
     Prefer `--stop-only` when pausing for human review between stages.

2. Report `TEARDOWN_OK` with a one-line summary of what was removed/kept, then STOP.

Note: the sandbox (task_files, deliverables, skills) is durable and unaffected. If you
tear down mid-pipeline, re-run `/hilbench-provision` before any container-backed stage
(`/hilbench-check1`, `/hilbench-check2`, evaluation).
