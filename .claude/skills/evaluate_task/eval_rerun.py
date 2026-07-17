#!/usr/bin/env python3
"""
eval_rerun.py — incremental re-run planner for /hilbench-evaluate-full.

WHY: the Harbor grade is expensive (up to 12 SWE-Agent runs). After the first
run, most re-runs only touch a subset of the task's artifacts. This tool decides
— deterministically — which of the three evaluation stages (input_validation,
check1, check2) actually need to re-run, by comparing a content hash of each
stage's *dependency fields* against the last recorded run (the "manifest").

A stage is SKIPPED (its previous verdict is reused) iff a manifest exists, the
stage has a recorded verdict, and none of its dependency fields changed since
that manifest was written. Otherwise it is RUN. `--fresh` (or a missing manifest)
forces every stage to RUN.

The verdict reused on SKIP can be PASS or FAIL: identical inputs produce the same
outcome, so re-running an unchanged stage is pointless either way. INCOMPLETE is
never reused because model availability may recover. The overall grade is PASS
only if the (reused-or-fresh) verdicts for all three stages are PASS
(input_validation is a precondition; check1 + check2 must both pass).

DEPENDENCY MAP (see references/evaluation-rerun.md for the full rationale):

  input_validation : setup_patch, test_patch, golden_patch, relevant_tests
  check1           : problem_statement, requirements, interfaces,
                     setup_patch, test_patch, golden_patch
  check2           : problem_statement, requirements, interfaces,
                     setup_patch, test_patch, registry(desc+resolution)

Notes baked into the map:
  * relevant_tests is intentionally NOT a check1/check2 dependency — the project
    invariant is that relevant_tests.txt is only ever edited alongside the test
    patch. It stays an input_validation dependency.
  * golden_patch IS a check1 dependency: the "guessed blocker" judge uses the
    golden as its reference solution. It is NOT a check2 dependency (check2's
    judge never reads it).
  * check2 depends only on the blocker DESCRIPTION + RESOLUTION text (the
    `# BLOCKER DETAILS` content), hashed order-independently. Editing a blocker
    `id` or `trigger_questions` does not trigger a re-run.

Manifest: <deliverables>/harbor/eval_manifest.json

Usage:
  eval_rerun.py plan   --task-files DIR --deliverables DIR [--scenario 1|2] [--fresh]
  eval_rerun.py update --task-files DIR --deliverables DIR [--scenario 1|2] \
                       [--input-validation PASS|FAIL] \
                       [--check1 PASS|FAIL|INCOMPLETE] [--check2 PASS|FAIL|INCOMPLETE]

`plan` prints a human summary plus machine-readable `PLAN ...` sentinel lines
(and `--json` emits the plan as JSON). `update` rewrites the manifest with the
current hashes and the given verdicts (omitted stages keep their prior verdict).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

MANIFEST_VERSION = 2
STAGES = ("input_validation", "check1", "check2")

# Dependency map: stage -> the fields whose change forces a re-run.
DEPS = {
    "input_validation": ["setup_patch", "test_patch", "golden_patch", "relevant_tests"],
    "check1": [
        "problem_statement", "requirements", "interfaces",
        "setup_patch", "test_patch", "golden_patch",
    ],
    "check2": [
        "problem_statement", "requirements", "interfaces",
        "setup_patch", "test_patch", "registry",
    ],
}

ABSENT = "ABSENT"  # sentinel hash for a missing file


def _resolve_scenario(task_files: Path, deliverables: Path, scenario: int | None) -> int:
    if scenario in (1, 2):
        return scenario
    # Auto-detect: obstructed patches in deliverables => Scenario 2.
    if (deliverables / "test_patch_obstructed.diff").exists() or (
        deliverables / "golden_patch_obstructed.diff"
    ).exists():
        return 2
    return 1


def field_paths(task_files: Path, deliverables: Path, scenario: int) -> dict[str, Path]:
    s2 = scenario == 2
    return {
        "setup_patch": deliverables / "setup_patch.diff",
        "test_patch": (deliverables / "test_patch_obstructed.diff") if s2
        else (task_files / "test_patch.diff"),
        "golden_patch": (deliverables / "golden_patch_obstructed.diff") if s2
        else (task_files / "golden_patch.diff"),
        "relevant_tests": deliverables / "relevant_tests.txt",
        "problem_statement": deliverables / "modified_problem_statement.txt",
        "requirements": deliverables / "modified_requirements.txt",
        "interfaces": deliverables / "modified_public_interfaces.txt",
        "registry": deliverables / "blocker_registry.json",
    }


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ABSENT
    return _hash_bytes(path.read_bytes())


def _hash_registry_projection(path: Path) -> str:
    """Hash only the (description, resolution) pairs, order-independent.

    Excludes id and trigger_questions so renaming a blocker id or editing its
    trigger questions does not force a check2 re-run.
    """
    if not path.exists() or not path.is_file():
        return ABSENT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Unparseable registry: fall back to a raw-bytes hash so any edit is seen
        # (and the check re-runs) rather than silently treating it as unchanged.
        return "UNPARSEABLE:" + _hash_file(path)
    blockers = data.get("blockers", data) if isinstance(data, dict) else data
    if not isinstance(blockers, list):
        return "UNPARSEABLE:" + _hash_file(path)
    pairs = []
    for b in blockers:
        if not isinstance(b, dict):
            continue
        desc = (b.get("description") or "").strip()
        res = (b.get("resolution") or "").strip()
        pairs.append([desc, res])
    pairs.sort()
    canon = json.dumps(pairs, ensure_ascii=False, sort_keys=True)
    return _hash_bytes(canon.encode("utf-8"))


def compute_hashes(task_files: Path, deliverables: Path, scenario: int) -> dict[str, str]:
    paths = field_paths(task_files, deliverables, scenario)
    hashes: dict[str, str] = {}
    for field, p in paths.items():
        hashes[field] = _hash_registry_projection(p) if field == "registry" else _hash_file(p)
    return hashes


def manifest_path(deliverables: Path) -> Path:
    return deliverables / "harbor" / "eval_manifest.json"


def load_manifest(deliverables: Path) -> dict | None:
    p = manifest_path(deliverables)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def build_plan(task_files: Path, deliverables: Path, scenario: int, fresh: bool) -> dict:
    current = compute_hashes(task_files, deliverables, scenario)
    manifest = None if fresh else load_manifest(deliverables)
    prior_hashes = (manifest or {}).get("hashes", {})
    prior_verdicts = (manifest or {}).get("verdicts", {})

    stages: dict[str, dict] = {}
    for stage in STAGES:
        deps = DEPS[stage]
        if manifest is None:
            stages[stage] = {
                "action": "RUN",
                "reason": "fresh" if fresh else "no_prior_manifest",
                "reuse_verdict": None,
                "changed": [],
            }
            continue
        if stage not in prior_verdicts:
            stages[stage] = {
                "action": "RUN",
                "reason": "no_prior_verdict",
                "reuse_verdict": None,
                "changed": [],
            }
            continue
        if prior_verdicts.get(stage) == "INCOMPLETE":
            stages[stage] = {
                "action": "RUN",
                "reason": "prior_incomplete",
                "reuse_verdict": None,
                "changed": [],
            }
            continue
        changed = [f for f in deps if current.get(f) != prior_hashes.get(f)]
        if changed:
            stages[stage] = {
                "action": "RUN",
                "reason": "changed",
                "reuse_verdict": None,
                "changed": changed,
            }
        else:
            stages[stage] = {
                "action": "SKIP",
                "reason": "unchanged",
                "reuse_verdict": prior_verdicts.get(stage),
                "changed": [],
            }

    # Image / test.sh rebuild hints (independent of the check re-run decision).
    setup_changed = manifest is None or current.get("setup_patch") != prior_hashes.get("setup_patch")
    tests_changed = manifest is None or any(
        current.get(f) != prior_hashes.get(f) for f in ("test_patch", "relevant_tests")
    )

    return {
        "scenario": scenario,
        "has_manifest": manifest is not None,
        "fresh": fresh,
        "current_hashes": current,
        "stages": stages,
        "image_rebuild": bool(setup_changed),
        "image_rebuild_reason": "setup_patch_changed" if setup_changed else "unchanged",
        "test_sh_rebuild": bool(tests_changed),
    }


def print_plan(plan: dict) -> None:
    print("=== /hilbench-evaluate-full — incremental re-run plan ===")
    print(f"scenario: {plan['scenario']}   prior manifest: "
          f"{'yes' if plan['has_manifest'] else 'no (fresh full run)'}")
    print("")
    for stage in STAGES:
        s = plan["stages"][stage]
        if s["action"] == "SKIP":
            print(f"  {stage:<17} SKIP   (reuse previous verdict: {s['reuse_verdict']})")
        else:
            extra = ""
            if s["reason"] == "changed":
                extra = f" [changed: {', '.join(s['changed'])}]"
            elif s["reason"] in ("no_prior_manifest", "fresh"):
                extra = " [first/forced full run]"
            elif s["reason"] == "no_prior_verdict":
                extra = " [no recorded verdict]"
            elif s["reason"] == "prior_incomplete":
                extra = " [prior Harbor run lacked two completed model lanes]"
            print(f"  {stage:<17} RUN{extra}")
    print("")
    print(f"  image rebuild:   {'YES' if plan['image_rebuild'] else 'no'} "
          f"({plan['image_rebuild_reason']})")
    print(f"  test.sh rebuild: {'YES' if plan['test_sh_rebuild'] else 'no'}")
    print("  (If the Docker image is absent in this session, build it regardless.)")
    print("")
    # Machine-readable sentinels for the agent to parse.
    for stage in STAGES:
        s = plan["stages"][stage]
        if s["action"] == "SKIP":
            print(f"PLAN {stage} SKIP reuse={s['reuse_verdict']}")
        elif s["reason"] == "changed":
            print(f"PLAN {stage} RUN changed={','.join(s['changed'])}")
        else:
            print(f"PLAN {stage} RUN {s['reason']}")
    print(f"PLAN image_rebuild {'YES' if plan['image_rebuild'] else 'NO'} "
          f"{plan['image_rebuild_reason']}")
    print(f"PLAN test_sh_rebuild {'YES' if plan['test_sh_rebuild'] else 'NO'}")


def cmd_plan(args) -> int:
    task_files = Path(args.task_files).expanduser().resolve()
    deliverables = Path(args.deliverables).expanduser().resolve()
    scenario = _resolve_scenario(task_files, deliverables, args.scenario)
    plan = build_plan(task_files, deliverables, scenario, args.fresh)
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print_plan(plan)
    return 0


def _norm_verdict(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().upper()
    if v in ("PASS", "FAIL", "INCOMPLETE"):
        return v
    if v in ("", "NONE", "SKIP"):
        return None
    raise SystemExit(f"invalid verdict '{v}' (expected PASS, FAIL, or INCOMPLETE)")


def cmd_update(args) -> int:
    task_files = Path(args.task_files).expanduser().resolve()
    deliverables = Path(args.deliverables).expanduser().resolve()
    scenario = _resolve_scenario(task_files, deliverables, args.scenario)
    current = compute_hashes(task_files, deliverables, scenario)

    prior = load_manifest(deliverables) or {}
    verdicts = dict(prior.get("verdicts", {}))
    for stage, val in (
        ("input_validation", args.input_validation),
        ("check1", args.check1),
        ("check2", args.check2),
    ):
        nv = _norm_verdict(val)
        if nv is not None:
            verdicts[stage] = nv

    manifest = {
        "version": MANIFEST_VERSION,
        "updated_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario,
        "hashes": current,
        "verdicts": verdicts,
    }
    mp = manifest_path(deliverables)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if any(verdicts.get(s) == "INCOMPLETE" for s in STAGES):
        overall = "INCOMPLETE"
    else:
        overall = "PASS" if all(verdicts.get(s) == "PASS" for s in STAGES) else "FAIL"
    print(f"MANIFEST_UPDATED {mp}")
    print(f"  scenario={scenario} verdicts={json.dumps(verdicts)}")
    print(f"  overall={overall}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Incremental re-run planner for /hilbench-evaluate-full")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--task-files", required=True, help="Path to the task_files directory")
    common.add_argument("--deliverables", required=True, help="Path to the deliverables directory")
    common.add_argument("--scenario", type=int, choices=(1, 2), default=None,
                        help="Scenario (auto-detected from obstructed patches if omitted)")

    p_plan = sub.add_parser("plan", parents=[common], help="Decide which stages to run/skip")
    p_plan.add_argument("--fresh", action="store_true", help="Force a full run (ignore the manifest)")
    p_plan.add_argument("--json", action="store_true", help="Emit the plan as JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_upd = sub.add_parser("update", parents=[common], help="Record hashes + verdicts to the manifest")
    p_upd.add_argument("--input-validation", default=None, help="PASS or FAIL (omit to keep prior)")
    p_upd.add_argument("--check1", default=None, help="PASS, FAIL, or INCOMPLETE (omit to keep prior)")
    p_upd.add_argument("--check2", default=None, help="PASS, FAIL, or INCOMPLETE (omit to keep prior)")
    p_upd.set_defaults(func=cmd_update)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
