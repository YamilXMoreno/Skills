#!/usr/bin/env python3
"""Run one Harbor JobConfig per model without cross-model fail-fast behavior."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


DEAD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AUTH_BLOCKED",
        re.compile(
            r"\b(?:401|403)\b|invalid api[_ ]key|api[_ ]key.*(?:blocked|disabled|expired|"
            r"missing|revoked|unset)|authentication[_ ]error|permission denied|access denied",
            re.IGNORECASE,
        ),
    ),
    (
        "MODEL_UNAVAILABLE",
        re.compile(
            r"model (?:is )?(?:not found|unavailable|disabled|not available)|"
            r"unknown model|unsupported model|invalid model|does not exist",
            re.IGNORECASE,
        ),
    ),
    (
        "PROVIDER_DOWN",
        re.compile(
            r"\b(?:502|503|504)\b|service unavailable|provider unavailable|"
            r"provider status.*down|model status.*down|maintenance window",
            re.IGNORECASE,
        ),
    ),
    (
        "QUOTA_BLOCKED",
        re.compile(
            r"insufficient[_ ]quota|quota exceeded|billing.*(?:disabled|required)|"
            r"credits? exhausted",
            re.IGNORECASE,
        ),
    ),
)


@dataclass
class ModelResult:
    status: str
    config: str
    log: str
    exit_code: int | None = None
    reason_code: str | None = None
    detail: str | None = None
    skipped: bool = False


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "model"


def classify_dead(text: str) -> tuple[str, str] | None:
    for reason, pattern in DEAD_PATTERNS:
        match = pattern.search(text)
        if match:
            detail = " ".join(match.group(0).split())
            return reason, detail[:240]
    return None


def read_log_tail(path: Path, limit: int = 2_000_000) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read().decode("utf-8", errors="replace")


def load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def run_model(
    model: str,
    config: Path,
    harbor_bin: str,
    logs_dir: Path,
) -> ModelResult:
    log_path = logs_dir / f"{slug(model)}.log"
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
            completed = subprocess.run(
                [harbor_bin, "run", "-c", str(config)],
                text=True,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        output = read_log_tail(log_path)
    except OSError as exc:
        output = str(exc)
        log_path.write_text(output + "\n", encoding="utf-8", errors="replace")
        return ModelResult(
            status="FAILED",
            config=str(config),
            log=str(log_path),
            reason_code="LAUNCH_ERROR",
            detail=output[:240],
        )

    if completed.returncode == 0:
        return ModelResult(
            status="COMPLETED",
            config=str(config),
            log=str(log_path),
            exit_code=0,
        )

    dead = classify_dead(output)
    if dead:
        reason, detail = dead
        return ModelResult(
            status="DEAD",
            config=str(config),
            log=str(log_path),
            exit_code=completed.returncode,
            reason_code=reason,
            detail=detail,
        )
    return ModelResult(
        status="FAILED",
        config=str(config),
        log=str(log_path),
        exit_code=completed.returncode,
        reason_code="HARBOR_RUN_FAILED",
        detail="Harbor exited non-zero; inspect the model log.",
    )


def parse_model_config(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected MODEL=CONFIG_PATH")
    model, raw_path = value.split("=", 1)
    if not model.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("expected non-empty MODEL=CONFIG_PATH")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"config does not exist: {path}")
    return model.strip(), path


def parse_mark_dead(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected MODEL=REASON")
    model, reason = value.split("=", 1)
    if not model.strip() or not reason.strip():
        raise argparse.ArgumentTypeError("expected non-empty MODEL=REASON")
    return model.strip(), reason.strip()


def run_batch(
    check: str,
    model_configs: list[tuple[str, Path]],
    state_file: Path,
    logs_dir: Path,
    harbor_bin: str,
    retry_dead: bool,
    marked_dead: dict[str, str] | None = None,
) -> dict:
    logs_dir.mkdir(parents=True, exist_ok=True)
    prior_models = load_state(state_file).get("models", {})
    results: dict[str, ModelResult] = {}
    runnable: list[tuple[str, Path]] = []
    marked_dead = marked_dead or {}

    for model, config in model_configs:
        prior = prior_models.get(model, {})
        if model in marked_dead:
            results[model] = ModelResult(
                status="DEAD",
                config=str(config),
                log="",
                reason_code="PREFLIGHT_DEAD",
                detail=marked_dead[model][:240],
                skipped=True,
            )
        elif prior.get("status") == "DEAD" and not retry_dead:
            results[model] = ModelResult(
                status="DEAD",
                config=str(config),
                log=str(prior.get("log", "")),
                exit_code=prior.get("exit_code"),
                reason_code=prior.get("reason_code", "PREVIOUSLY_DEAD"),
                detail=prior.get("detail", "Skipped persisted dead model."),
                skipped=True,
            )
        else:
            runnable.append((model, config))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(runnable))) as pool:
        futures = {
            pool.submit(run_model, model, config, harbor_bin, logs_dir): model
            for model, config in runnable
        }
        for future in concurrent.futures.as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except Exception as exc:  # defensive fan-in: one future never aborts siblings
                results[model] = ModelResult(
                    status="FAILED",
                    config=str(dict(model_configs)[model]),
                    log="",
                    reason_code="RUNNER_ERROR",
                    detail=str(exc)[:240],
                )

    ordered_results = {
        model: asdict(results[model])
        for model, _ in model_configs
    }
    state = {
        "version": 1,
        "check": check,
        "updated_utc": utc_now(),
        "models": ordered_results,
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=("check1", "check2"))
    parser.add_argument(
        "--model-config",
        action="append",
        type=parse_model_config,
        required=True,
        metavar="MODEL=CONFIG_PATH",
    )
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--harbor-bin", default="harbor")
    parser.add_argument(
        "--mark-dead",
        action="append",
        type=parse_mark_dead,
        default=[],
        metavar="MODEL=REASON",
        help="Persist and skip a model known unavailable before launch.",
    )
    parser.add_argument(
        "--retry-dead",
        action="store_true",
        help="Retry models persisted as DEAD instead of skipping them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    models = [model for model, _ in args.model_config]
    if len(models) != len(set(models)):
        raise SystemExit("duplicate model in --model-config")

    state = run_batch(
        check=args.check,
        model_configs=args.model_config,
        state_file=args.state_file.expanduser().resolve(),
        logs_dir=args.logs_dir.expanduser().resolve(),
        harbor_bin=args.harbor_bin,
        retry_dead=args.retry_dead,
        marked_dead=dict(args.mark_dead),
    )
    statuses = {model: value["status"] for model, value in state["models"].items()}
    completed = sum(status == "COMPLETED" for status in statuses.values())
    print(f"HARBOR_MODEL_BATCH {args.check} completed={completed}/{len(statuses)}")
    for model, status in statuses.items():
        result = state["models"][model]
        suffix = f" reason={result['reason_code']}" if result.get("reason_code") else ""
        skipped = " skipped=true" if result.get("skipped") else ""
        print(f"MODEL_STATE {model} {status}{suffix}{skipped}")
    if completed < 2:
        print(f"HARBOR_CHECK_INCOMPLETE {args.check} quorum={completed}/2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
