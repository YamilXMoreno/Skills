import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generator, Optional

import boto3
import pandas as pd
from jinja2 import Environment
from pydantic import BaseModel, Field
from scaleml_core.utils import make_logger

from genai.common.caching import CacheConfig, CacheFactory
from genai.common.secrets import (
    _ARCHIEQC_LITELLM_KEY_NAME,
    LITELLM_BASE_URL,
    PUBLIC_LITELLM_BASE_URL,
)
from genai.common.utils.docker_utils import docker_login_hub, get_dockerhub_env_vars
from genai.common.utils.gist_utils import create_public_gist
from genai.common.utils.jinja_utils import load_environment
from genai.common.utils.keystore import get_from_env_or_secrets
from genai.common.utils.vm_registry import (
    VM_TIMEOUT_SECONDS,
    adelete_vm_entry,
    aget_available_vm,
    aput_vm_entry,
    aregister_vm,
    arelease_lock,
    compute_hil_bench_hash,
    create_registry_entry,
    parse_disk_size_gi,
)
from genai.models.llm import ChainedLLM
from genai.system.agentic_autoreviewer.agents.mixins.in_product_agent_mixin import (
    InProductAgentMixin,
    export_field,
)
from genai.system.agentic_autoreviewer.core.agent import (
    AgentFeedbacks,
    AgentInput,
    BaseFeedbackItem,
    ReviewAgent,
)
from genai.system.agentic_autoreviewer.core.data_wrapper import ArchieDataRow
from genai.system.agentic_autoreviewer.core.utils import get_raw_rtd_field, get_rtd_field
from genai.system.agentic_autoreviewer.scores import GQAScore

logger = make_logger(__name__)


class SWEInputValidationError(Exception):
    """Exception raised for SWE input validation failures in sandbox mode.

    This exception is used to distinguish validation errors (bad input data)
    from infrastructure errors (VM issues, network problems, etc.).
    The error message should already include the 'SWE INPUT VALIDATION FAILED:' prefix.
    """

    pass


SWEAP_REPO_RAW_URL = "https://raw.githubusercontent.com/scaleapi/SWE-bench_Pro-os/main"
_SWEAP_INFRASTRUCTURE_FILES = {"parser.py", "run_script.sh"}
_DEBUG_SANDBOX_ID_FILE = Path("/tmp/hil_bench_debug_sandbox_id")
_PYTHON_LANGUAGES = ("python", "py")
_GO_LANGUAGES = ("go", "golang")
_JS_TS_LANGUAGES = ("js", "ts", "javascript", "typescript", "jsx", "tsx")
_JAVA_LANGUAGES = ("java",)
_RUST_LANGUAGES = ("rust", "rs")
_CPP_LANGUAGES = ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus")
_CPP_EXTENSIONS = (".cc", ".cpp", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx")
_SUPPORTED_SWE_LANGUAGES = (
    _PYTHON_LANGUAGES
    + _GO_LANGUAGES
    + _JS_TS_LANGUAGES
    + _JAVA_LANGUAGES
    + _RUST_LANGUAGES
    + _CPP_LANGUAGES
)
_AZURE_MODELS = {
    "gpt-4o",
    "gpt-4o-2024-08-06",
    "o3-mini",
    "o3",
    "gpt-5-2025-08-07",
    "gpt-5-mini-2025-08-07",
}
MODEL_SUBSTRING_TO_NUMBER = {
    "gpt": 1,
    "claude": 2,
    "gemini": 3,
}
STDERR_LIMIT = 1000
LOG_FILE_CHAR_LIMIT = 2000
MAX_DB_CONNECTIONS = 20
_SHARED_ATTEMPT_STATE_BASE_DIR = (
    Path(__file__).resolve().parents[5] / "genai" / "genai" / "applications" / "hil_bench_agent"
)
ATTEMPT_LOCK_DIR = str(_SHARED_ATTEMPT_STATE_BASE_DIR / ".hil_bench_attempt_locks")
ATTEMPT_OWNER_DIR = str(_SHARED_ATTEMPT_STATE_BASE_DIR / ".hil_bench_attempt_owners")
_SWE_ATTEMPT_ID_FROM_IMAGE_RE = re.compile(r"hilbench-swe:([0-9a-f]{24})")
_SWE_ATTEMPT_ID_FROM_NAME_RE = re.compile(r"hilbench-swe[-_]?([0-9a-f]{24})")
DEBUG_LOG_FILES = [
    "instances.json",
    "batch_config.json",
    "consolidated_metrics.json",
    "consolidated_results.json",
    "*.info.log",
    "*.debug.log",
    # SWE
    "run_batch.log",
    "run_batch_exit_statuses.yaml",
    "report.json",
    # SQL
    "sql_query_results.json",
]

# Patterns for files that should be filtered from patches (generated/binary files)
# This is a local copy of hil_bench.utils.custom_eval.PATCH_FILTER_PATTERNS to avoid import dependency
_PATCH_FILTER_PATTERNS = [
    r"__pycache__/",  # Python bytecode cache
    r"node_modules/",  # Node.js dependencies
    r"\.egg-info/",  # Python egg info
    r"diff --git a/\S+\.pyc ",  # Python compiled files
    r"diff --git a/\S+\.pyo ",  # Python optimized files
    r"diff --git a/\S+\.so ",  # Shared objects
    r"diff --git a/\S+\.dll ",  # Windows DLLs
    r"diff --git a/\S+\.dylib ",  # macOS dynamic libraries
    # HIL-bench infrastructure files
    r"diff --git a/parser\.py b/parser\.py",  # SWEAP parser script
    r"diff --git a/run_script\.sh b/run_script\.sh",  # SWEAP run script
    # Redis persistence files
    r"appendonlydir/",  # Redis AOF persistence directory
    r"diff --git a/\S*dump\.rdb ",  # Redis RDB snapshot
    r"diff --git a/\S*appendonly\.aof ",  # Redis AOF persistence file
]


def _sandbox_identifier(sandbox: Any) -> str:
    """Return sandbox id across old/new scale_sandbox SDKs."""
    return str(getattr(sandbox, "sandbox_id", None) or getattr(sandbox, "id", "unknown"))


def _normalize_sandbox_base_url(base_url: str) -> str:
    """Normalize root sandbox URLs to the ARCP API base path expected by new SDKs."""
    normalized = base_url.rstrip("/")
    if re.search(r"/v[0-9]+(?:beta[0-9]+)?/sandbox$", normalized):
        return normalized
    if normalized == "https://sandbox.ml-serving-internal.scale.com":
        return "https://sandbox-arp.ml-serving-internal.scale.com/v0beta1/sandbox"
    return f"{normalized}/v0beta1/sandbox"


@contextmanager
def _sandbox_base_url(base_url: str) -> Generator[None, None, None]:
    """Temporarily set SANDBOX_BASE_URL for SDK versions that read env config."""
    prev = os.environ.get("SANDBOX_BASE_URL")
    os.environ["SANDBOX_BASE_URL"] = _normalize_sandbox_base_url(base_url)
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("SANDBOX_BASE_URL", None)
        else:
            os.environ["SANDBOX_BASE_URL"] = prev


class _ScaleSandboxClientCompat:
    """Compatibility layer for old/new scale_sandbox client APIs."""

    def __init__(self, base_url: str, timeout: float):
        self._base_url = base_url
        self._timeout = timeout
        self._legacy_client = None
        try:
            from scale_sandbox.client import HostedSandboxClient  # type: ignore

            self._legacy_client = HostedSandboxClient(base_url=base_url, timeout=timeout)
        except Exception:
            self._legacy_client = None

    async def create(self, **kwargs: Any) -> Any:
        if self._legacy_client is not None:
            return await self._legacy_client.create(**kwargs)

        from scale_sandbox import VmSandbox
        from scale_sandbox.models import ComputeConfig, SandboxSpec

        labels = {
            "product": str(kwargs.get("product", "hil-bench")),
            "customer": str(kwargs.get("customer", "internal")),
            "team": str(kwargs.get("team", "gen_ai")),
        }
        storage_gb = parse_disk_size_gi(str(kwargs.get("disk_size", "0Gi")))
        spec = SandboxSpec(
            compute=ComputeConfig(storage_gb=float(storage_gb)) if storage_gb > 0 else None
        )
        timeout_seconds = kwargs.get("timeout")
        # New SDK validates timeout_seconds <= 86400; old HostedSandboxClient accepted larger.
        if isinstance(timeout_seconds, int):
            timeout_seconds = max(1, min(timeout_seconds, 86400))
        with _sandbox_base_url(self._base_url):
            return await VmSandbox.create(
                image=str(kwargs["image"]),
                cpu=kwargs.get("cpu"),
                memory=kwargs.get("memory"),
                timeout=timeout_seconds,
                name=kwargs.get("name"),
                labels=labels,
                spec=spec,
                wait=False,
            )

    async def from_id(self, sandbox_id: str) -> Any:
        if self._legacy_client is not None:
            return await self._legacy_client.from_id(sandbox_id)

        from scale_sandbox import Sandbox

        with _sandbox_base_url(self._base_url):
            return await Sandbox.from_id(sandbox_id)

    async def close(self) -> None:
        if self._legacy_client is not None:
            await self._legacy_client.close()


def _filter_patch(patch: str) -> str:
    if not patch:
        return patch

    # Split patch into individual file diffs (each starts with "diff --git")
    file_diffs = re.split(r"(?=diff --git )", patch)

    filtered_diffs = []
    for diff in file_diffs:
        if not diff.strip():
            continue

        # Check if this diff is for a file that should be filtered
        should_filter = False
        for pattern in _PATCH_FILTER_PATTERNS:
            if re.search(pattern, diff):
                should_filter = True
                break

        if not should_filter:
            filtered_diffs.append(diff)

    return "".join(filtered_diffs)


def _normalize_swe_language(language: str | None) -> str:
    return (language or "").lower()


def _extract_test_file_path_for_language(test_name: str, language: str) -> str | None:
    """Extract a runnable test file path from a SWEAP test identifier."""
    lang = _normalize_swe_language(language)
    path_before_pytest_sep = test_name.split("::", 1)[0]
    generic_path_extensions = (
        ".py",
        ".java",
        ".rs",
        ".go",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".mjs",
        ".cjs",
        ".mts",
        ".cts",
        *_CPP_EXTENSIONS,
    )
    if (
        ("::" in test_name or "/" in path_before_pytest_sep or "\\" in path_before_pytest_sep)
        and path_before_pytest_sep.lower().endswith(generic_path_extensions)
    ):
        return path_before_pytest_sep
    if lang in _PYTHON_LANGUAGES:
        return test_name.split("::")[0] if "::" in test_name else test_name
    if lang in _GO_LANGUAGES:
        if "/" in test_name and "_test.go" in test_name.lower():
            return test_name
        return None
    if lang in _JAVA_LANGUAGES:
        path_part = test_name
        for separator in ("::", "#"):
            if separator in path_part:
                path_part = path_part.split(separator, 1)[0]
        if " " in path_part and "/" not in path_part and "\\" not in path_part:
            return None
        if "/" in path_part or "\\" in path_part or path_part.endswith(".java"):
            return path_part
        return None
    if lang in _RUST_LANGUAGES:
        path_part = test_name
        if ".rs" in path_part and "::" in path_part:
            rs_idx = path_part.find(".rs")
            path_part = path_part[: rs_idx + len(".rs")]
        if "/" in path_part or "\\" in path_part or path_part.endswith(".rs"):
            return path_part
        return None
    if lang in _CPP_LANGUAGES:
        path_part = test_name
        for separator in ("::", "#"):
            if separator in path_part:
                candidate = path_part.split(separator, 1)[0]
                if any(candidate.lower().endswith(ext) for ext in _CPP_EXTENSIONS):
                    path_part = candidate
                    break
        if "/" in path_part or "\\" in path_part or any(
            path_part.lower().endswith(ext) for ext in _CPP_EXTENSIONS
        ):
            return path_part
        return None
    path_part = test_name
    if "|" in test_name:
        path_part = test_name.split("|")[0].strip()
    if "/" in path_part or path_part.endswith(
        (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
    ):
        return path_part
    return None


def _is_go_function_name(test_name: str) -> bool:
    """Check if test_name looks like a Go test/benchmark/example/fuzz function name."""
    if "/" in test_name or "\\" in test_name:
        return False
    return bool(re.match(r"^(Test|Benchmark|Fuzz)[A-Z_]", test_name)) or bool(
        re.match(r"^Example([A-Z_]|$)", test_name)
    )


def _is_java_test_identifier(test_name: str) -> bool:
    """Check if a Java test identifier is class/method-like rather than a file path."""
    if not test_name or "/" in test_name or "\\" in test_name or test_name.endswith(".java"):
        return False
    if test_name.startswith("-"):
        return False
    return bool(
        re.match(
            r"^[A-Za-z_$][A-Za-z0-9_$.]*(?:(?:#|::)[A-Za-z_$][A-Za-z0-9_$]*)?$",
            test_name,
        )
    )


def _is_rust_test_identifier(test_name: str) -> bool:
    """Check if a Rust test identifier is module/function-like rather than a file path."""
    if not test_name or "/" in test_name or "\\" in test_name or test_name.endswith(".rs"):
        return False
    if test_name.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$", test_name))


def _is_cpp_test_identifier(test_name: str) -> bool:
    """Check if a C/C++ test identifier is suite/test-like rather than a file path."""
    if (
        not test_name
        or "\\" in test_name
        or any(test_name.lower().endswith(ext) for ext in _CPP_EXTENSIONS)
    ):
        return False
    if test_name.startswith("-"):
        return False
    return bool(
        re.match(
            r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::|/)[A-Za-z_][A-Za-z0-9_]*)*$",
            test_name,
        )
    )


def _extract_java_test_identifiers_from_patch(patch_content: str) -> set[str]:
    """Extract Java test class and method identifiers from added test code."""
    identifiers: set[str] = set()
    current_class: str | None = None
    pending_test_annotation = False
    class_pattern = re.compile(
        r"^\+\s*(?:public\s+)?(?:class|interface|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    )
    method_pattern = re.compile(
        r"^\+\s*(?:public|protected|private)?\s*(?:static\s+)?(?:[\w<>\[\],.?]+\s+)+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
    )
    for line in patch_content.split("\n"):
        class_match = class_pattern.match(line)
        if class_match:
            current_class = class_match.group(1)
            if current_class.endswith(("Test", "Tests", "IT")):
                identifiers.add(current_class)
            continue
        if line.lstrip("+").strip().startswith("@Test"):
            pending_test_annotation = True
            continue
        method_match = method_pattern.match(line)
        if method_match:
            method_name = method_match.group(1)
            if pending_test_annotation or method_name.startswith("test"):
                identifiers.add(method_name)
                if current_class:
                    identifiers.add(f"{current_class}#{method_name}")
                    identifiers.add(f"{current_class}::{method_name}")
            pending_test_annotation = False
    return identifiers


def _extract_rust_test_identifiers_from_patch(patch_content: str) -> set[str]:
    """Extract Rust test function identifiers from added code."""
    identifiers: set[str] = set()
    pending_test_attr = False
    module_stack: list[str] = []
    module_pattern = re.compile(r"^\+\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{?")
    fn_pattern = re.compile(r"^\+\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for line in patch_content.split("\n"):
        stripped = line.lstrip("+").strip()
        mod_match = module_pattern.match(line)
        if mod_match:
            module_stack.append(mod_match.group(1))
            continue
        if stripped.startswith("#[") and "test" in stripped:
            pending_test_attr = True
            continue
        fn_match = fn_pattern.match(line)
        if fn_match:
            fn_name = fn_match.group(1)
            if pending_test_attr or fn_name.startswith("test_"):
                identifiers.add(fn_name)
                if module_stack:
                    identifiers.add("::".join(module_stack + [fn_name]))
            pending_test_attr = False
    return identifiers


def _extract_cpp_test_identifiers_from_patch(patch_content: str) -> set[str]:
    """Extract common C/C++ test identifiers from added code."""
    identifiers: set[str] = set()
    macro_pattern = re.compile(
        r"^\+\s*(?:TYPED_TEST|TEST_P|TEST_F|TEST)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
    )
    fn_pattern = re.compile(
        r"^\+\s*(?:static\s+)?(?:void|int|bool|auto)\s+((?:test|Test)[A-Za-z0-9_]*)\s*\("
    )
    for line in patch_content.split("\n"):
        macro_match = macro_pattern.match(line)
        if macro_match:
            suite, test = macro_match.groups()
            identifiers.add(test)
            identifiers.add(f"{suite}.{test}")
            identifiers.add(f"{suite}::{test}")
            identifiers.add(f"{suite}/{test}")
            continue
        fn_match = fn_pattern.match(line)
        if fn_match:
            identifiers.add(fn_match.group(1))
    return identifiers


def _is_test_file_for_language(filename: str, language: str) -> bool:
    """Return whether a file path should be treated as a test file for validation."""
    lang = _normalize_swe_language(language)
    normalized = filename.lstrip("/")
    basename = normalized.split("/")[-1] if "/" in normalized else normalized
    path_lower = normalized.lower()
    basename_lower = basename.lower()
    if lang in _PYTHON_LANGUAGES:
        return (
            basename_lower.startswith("test_")
            and basename_lower.endswith(".py")
            or basename_lower.endswith("_test.py")
        )
    if lang in _GO_LANGUAGES:
        return basename.endswith("_test.go")
    if lang in _JAVA_LANGUAGES:
        if not basename.endswith(".java"):
            return False
        if basename.endswith(("Test.java", "Tests.java", "IT.java")):
            return True
        path_parts = path_lower.split("/")
        return ("src" in path_parts and "test" in path_parts) or any(
            part in ("test", "tests") for part in path_parts[:-1]
        )
    if lang in _RUST_LANGUAGES:
        if not basename.endswith(".rs"):
            return False
        if basename_lower.endswith("_test.rs"):
            return True
        path_parts = path_lower.split("/")
        return any(part in ("test", "tests") for part in path_parts[:-1])
    if lang in _CPP_LANGUAGES:
        if not any(basename_lower.endswith(ext) for ext in _CPP_EXTENSIONS):
            return False
        if any(
            marker in basename_lower
            for marker in ("_test.", "_tests.", "test_", "tests_", ".test.", ".spec.")
        ):
            return True
        if basename.endswith(("Test.cc", "Test.cpp", "Test.cxx", "Tests.cc", "Tests.cpp", "Tests.cxx")):
            return True
        path_parts = path_lower.split("/")
        return any(part in ("test", "tests") for part in path_parts[:-1])
    js_ts_extensions = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
    if not any(basename.endswith(ext) for ext in js_ts_extensions):
        return False
    if any(suf in basename_lower for suf in (".test.", ".spec.")):
        return True
    if "/__tests__/" in path_lower or path_lower.startswith("__tests__/"):
        return True
    return any(part in ("test", "tests") for part in path_lower.split("/")[:-1])


def _java_identifier_matches(required: str, observed: str) -> bool:
    """Match Java parser output to required test identifiers across common formats."""
    if "|" in required or "|" in observed:
        return False
    req_tail = required.replace("::", "#").split(".")[-1]
    obs_tail = observed.replace("::", "#").split(".")[-1]
    javaish = (
        ".java" in required
        or ".java" in observed
        or "#" in required
        or "#" in observed
        or "::" in required
        or "::" in observed
        or (_is_java_test_identifier(required) and _is_java_test_identifier(observed))
        or req_tail.endswith(("Test", "Tests", "IT"))
        or obs_tail.endswith(("Test", "Tests", "IT"))
    )
    if not javaish:
        return False
    if required == observed:
        return True
    req = required.replace("::", "#")
    obs = observed.replace("::", "#")
    if req == obs:
        return True
    req_tail = req.split(".")[-1]
    obs_tail = obs.split(".")[-1]
    if req_tail == obs_tail:
        return True
    req_dot_parts = req.replace("#", ".").split(".")
    obs_simple = obs.replace("#", ".").split(".")[-1]
    if len(req_dot_parts) >= 2 and obs_simple == req_dot_parts[-2]:
        return True
    if "#" in req_tail or "#" in obs_tail:
        req_class, _, req_method = req_tail.partition("#")
        obs_class, _, obs_method = obs_tail.partition("#")
        if not req_method and obs_method and req_class == obs_method:
            req_package_parts = req.replace("#", ".").split(".")
            req_owner = req_package_parts[-2] if len(req_package_parts) >= 2 else ""
            return obs_class == req_owner or req.endswith("." + obs_class)
        if req_method and not obs_method and obs_class == req_method:
            obs_package_parts = obs.replace("#", ".").split(".")
            obs_owner = obs_package_parts[-2] if len(obs_package_parts) >= 2 else ""
            return req_class == obs_owner or obs.endswith("." + req_class)
        if req_method and obs_method and req_method == obs_method:
            return req_class == obs_class or req.endswith("." + obs_class) or obs.endswith(
                "." + req_class
            )
    return False


def _rust_identifier_matches(required: str, observed: str) -> bool:
    """Match Rust parser output to required test identifiers."""
    if "|" in required or "|" in observed:
        return False
    req = required.replace("/", "::")
    obs = observed.replace("/", "::")
    if req == obs:
        return True
    req_tail = req.split("::")[-1]
    obs_tail = obs.split("::")[-1]
    if req_tail == obs_tail:
        return True
    return req.endswith("::" + obs) or obs.endswith("::" + req)


def _cpp_identifier_matches(required: str, observed: str) -> bool:
    """Match C/C++ parser output to required test identifiers."""
    if "|" in required or "|" in observed:
        return False
    req = required.replace("::", ".").replace("/", ".")
    obs = observed.replace("::", ".").replace("/", ".")
    if req == obs:
        return True
    req_parts = req.split(".")
    obs_parts = obs.split(".")
    if req_parts[-1] == obs_parts[-1]:
        if len(req_parts) == 1 or len(obs_parts) == 1:
            return True
        return req_parts[-2] == obs_parts[-2]
    return False


def _cpp_pytest_wrapper_matches(test_name: str, cpp_case_name: str) -> bool:
    """Return True when a pytest wrapper test covers a same-named C++/Boost case."""
    if cpp_case_name.startswith("test_"):
        return False
    if "::" not in test_name:
        return False
    test_func = test_name.rsplit("::", 1)[-1]
    test_func_base = test_func.split("[")[0] if "[" in test_func else test_func
    pytest_wrapper_name = f"test_{cpp_case_name}"
    return test_func_base == pytest_wrapper_name or test_func_base.lower() == pytest_wrapper_name.lower()


def _format_internal_test_runner_input_error(raw_output: str) -> str | None:
    """Return an input-error message when tests did not run because a runner is unavailable."""
    lowered = raw_output.lower()
    indicators = [
        "command not found",
        "no module named pytest",
        "pytest: not found",
        "mvn: not found",
        "gradle: not found",
        "go: not found",
        "npm: not found",
        "yarn: not found",
        "pnpm: not found",
        "jest: not found",
        "vitest: not found",
        "mocha: not found",
        "cargo: not found",
        "rustc: not found",
        "cmake: not found",
        "ctest: not found",
        "make: not found",
        "g++: not found",
        "gcc: not found",
        "clang++: not found",
    ]
    dependency_indicators = [
        "importerror while loading conftest",
        "packagenotfounderror",
        "modulenotfounderror",
        "no module named ",
        "no required module provides package",
        "cannot find module",
        "could not resolve dependencies",
    ]
    if any(indicator in lowered for indicator in dependency_indicators):
        excerpt = raw_output.strip()[-STDERR_LIMIT:]
        return (
            "HiL-Bench agent failed due to errors in inputs: Internal SWEAP test command "
            "could not run because the built image is missing repository runtime/test "
            f"dependencies or the project is not installed. Output: {excerpt}"
        )
    if not any(indicator in lowered for indicator in indicators):
        return None
    excerpt = raw_output.strip()[-STDERR_LIMIT:]
    return (
        "HiL-Bench agent failed due to errors in inputs: Internal SWEAP test command "
        f"could not run because the built image is missing a required test runner. Output: {excerpt}"
    )


def _parse_sweap_json_test_status(log: str, fail_to_pass: list[str]) -> dict[str, str]:
    """
    Parse SWEAP JSON output to get test status.
    Copied from custom_eval.py to match original validation logic exactly.

    Returns dict mapping test names to status (PASSED, FAILED, SKIPPED, ERROR).
    """
    test_status_map: dict[str, str] = {}
    required_tests: set[str] = set(fail_to_pass) if fail_to_pass else set()

    data = None

    # Strategy 1: Look for our markers (most reliable)
    # Matches custom_eval.py lines 522-533
    start_marker = "SWEAP_JSON_START"
    end_marker = "SWEAP_JSON_END"
    start_idx = log.find(start_marker)
    end_idx = log.find(end_marker)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        json_section = log[start_idx + len(start_marker) : end_idx].strip()
        try:
            data = json.loads(json_section)
        except json.JSONDecodeError:
            pass

    # Strategy 2: Look for JSON structure directly
    # Matches custom_eval.py lines 536-551
    if data is None:
        json_start = log.find('{\n  "tests"')
        if json_start == -1:
            json_start = log.find('{"tests"')

        if json_start != -1:
            section = log[json_start:]
            for end_pattern in ["\n  ]\n}", "]\n}", "]}"]:
                end_pos = section.rfind(end_pattern)
                if end_pos != -1:
                    json_str = section[: end_pos + len(end_pattern)]
                    try:
                        data = json.loads(json_str)
                        break
                    except json.JSONDecodeError:
                        continue

    # Strategy 3: Try parsing entire log (last resort)
    # Matches custom_eval.py lines 553-558
    if data is None:
        try:
            data = json.loads(log.strip())
        except json.JSONDecodeError:
            pass

    if data is None:
        return test_status_map

    def _extract_pytest_components(test_name: str) -> tuple[str | None, str, str]:
        """Extract components from a pytest-style test name."""
        file_path = None
        func_with_params = test_name
        if "::" in test_name:
            parts = test_name.split("::")
            file_path = parts[0]
            func_with_params = parts[-1]
        func_base = func_with_params.split("[")[0] if "[" in func_with_params else func_with_params
        return file_path, func_with_params, func_base

    def _paths_match(path1: str | None, path2: str | None) -> bool:
        """Check if two file paths match, handling different root prefixes."""
        if path1 is None and path2 is None:
            return True
        if path1 is None or path2 is None:
            return False
        if path1 == path2:
            return True
        return (
            path1.endswith("/" + path2)
            or path2.endswith("/" + path1)
            or path1.endswith(path2)
            or path2.endswith(path1)
        )

    def _find_matching_required_test(parser_test_name: str) -> str | None:
        """Find a matching required test name, handling various format mismatches."""
        # Exact match
        if parser_test_name in required_tests:
            return parser_test_name

        # Java parsers commonly emit ClassName#methodName, package.ClassName.methodName,
        # or file-path-plus-method formats. Match those back to required identifiers.
        for req_test in required_tests:
            if _java_identifier_matches(req_test, parser_test_name):
                return req_test
            if _rust_identifier_matches(req_test, parser_test_name):
                return req_test
            if _cpp_identifier_matches(req_test, parser_test_name):
                return req_test

        # JS/TS pipe format
        if " | " in parser_test_name:
            parser_path, parser_desc = parser_test_name.split(" | ", 1)
            for req_test in required_tests:
                if " | " in req_test:
                    req_path, req_desc = req_test.split(" | ", 1)
                    path_matches = (
                        req_path == parser_path
                        or req_path.endswith(parser_path)
                        or parser_path.endswith(req_path)
                    )
                    desc_matches = (
                        req_desc == parser_desc
                        or req_desc.endswith(" | " + parser_desc)
                        or parser_desc.endswith(" | " + req_desc)
                    )
                    if path_matches and desc_matches:
                        return req_test
                else:
                    if (
                        req_test == parser_path
                        or req_test.endswith(parser_path)
                        or parser_path.endswith(req_test)
                    ):
                        return req_test
            return None

        # Pytest format
        parser_path, parser_func_params, parser_func_base = _extract_pytest_components(
            parser_test_name
        )
        parser_func_base_lower = parser_func_base.lower()
        fallback_match = None

        for req_test in required_tests:
            if " | " in req_test:
                continue
            req_path, req_func_params, req_func_base = _extract_pytest_components(req_test)
            req_func_base_lower = req_func_base.lower()

            if parser_func_base_lower != req_func_base_lower:
                continue

            params_compatible = (
                parser_func_params == req_func_params
                or req_func_params == req_func_base
                or parser_func_params == parser_func_base
            )
            if not params_compatible:
                continue

            if parser_path is not None and req_path is not None:
                if _paths_match(parser_path, req_path):
                    return req_test
                if fallback_match is None:
                    fallback_match = req_test
            else:
                return req_test

        return fallback_match

    # Parse tests from SWEAP JSON
    # Also track raw parser results for JS/TS individual test name mapping
    raw_parser_results = {}
    tests = data.get("tests", [])
    for test in tests:
        test_name = test.get("name", "")
        status_str = test.get("status", "").upper()
        if not test_name:
            continue
        raw_parser_results[test_name] = status_str
        matched_name = test_name
        if required_tests:
            matched_name = _find_matching_required_test(test_name)
            if matched_name is None:
                continue

        test_status_map[matched_name] = status_str

    # === Handle bare test names that have parametrized variants ===
    # Matches custom_eval.py lines 731-772
    # If FAIL_TO_PASS contains both "path::test_foo" (bare) and "path::test_foo[param]" (parametrized),
    # and parametrized variants passed, mark the bare name as passed too.
    # This handles the case where pytest only reports parametrized variants in output.
    if required_tests:
        for req_test in required_tests:
            # Skip if already in test_status_map
            if req_test in test_status_map:
                continue
            # Skip JS/TS format (handled separately)
            if " | " in req_test:
                continue
            # Check if this is a bare name (no parameters)
            if "[" in req_test:
                continue
            # Extract components from the required test
            req_path, _, req_func_base = _extract_pytest_components(req_test)
            req_func_base_lower = req_func_base.lower()
            # Find all parametrized variants of this test in test_status_map
            parametrized_variants = []
            for status_test in test_status_map.keys():
                if "[" not in status_test:
                    continue  # Only look at parametrized tests
                status_path, _, status_func_base = _extract_pytest_components(status_test)
                # Function base names must match
                if status_func_base.lower() != req_func_base_lower:
                    continue
                # If required has a path, status path must match (allowing different roots)
                if req_path is not None:
                    if status_path is None or not _paths_match(req_path, status_path):
                        continue
                parametrized_variants.append(status_test)
            # If we found parametrized variants and ALL of them passed, mark bare name as passed
            if parametrized_variants:
                all_passed = all(test_status_map.get(t) == "PASSED" for t in parametrized_variants)
                if all_passed:
                    test_status_map[req_test] = "PASSED"

    # === NEW: Handle JS/TS test matching edge cases ===
    # Parser providers may output results in different formats than FAIL_TO_PASS entries.
    # This handles cases where the tests are semantically correct but formatted differently:
    #   1. Extension mismatch: FAIL_TO_PASS has .ts but parser outputs .js (TypeScript→JavaScript)
    #   2. Individual test names: FAIL_TO_PASS has "TestName description" but parser outputs "file | test suite"
    #   3. Multi-pipe format: FAIL_TO_PASS has "file | spec | description" but parser outputs "file | test suite"
    # This does NOT modify existing matching - it only adds mappings for previously unmatched tests.
    if required_tests:
        # Valid TypeScript↔JavaScript extension pairs (TypeScript compiles to JavaScript)
        # We ONLY allow these specific pairs, not arbitrary extension swaps
        _VALID_TS_JS_PAIRS = {
            (".ts", ".js"),
            (".js", ".ts"),
            (".tsx", ".jsx"),
            (".jsx", ".tsx"),
            (".mts", ".mjs"),
            (".mjs", ".mts"),
            (".cts", ".cjs"),
            (".cjs", ".cts"),
        }

        def _get_extension(path: str) -> str | None:
            """Get the JS/TS extension from a path, or None if not a JS/TS file."""
            for ext in (".tsx", ".jsx", ".mts", ".mjs", ".cts", ".cjs", ".ts", ".js"):
                if path.endswith(ext):
                    return ext
            return None

        def _is_valid_extension_pair(ext1: str | None, ext2: str | None) -> bool:
            """Check if two extensions are a valid TypeScript↔JavaScript pair."""
            if ext1 is None or ext2 is None:
                return False
            if ext1 == ext2:
                return True  # Same extension is always valid
            return (ext1, ext2) in _VALID_TS_JS_PAIRS

        def _strip_extension(path: str) -> str:
            """Strip JS/TS extension from path."""
            for ext in (".tsx", ".jsx", ".mts", ".mjs", ".cts", ".cjs", ".ts", ".js"):
                if path.endswith(ext):
                    return path[: -len(ext)]
            return path

        # Collect all passing file-level test suites from raw parser output
        # Store path WITHOUT extension and the original extension for validation
        passing_suites: dict[str, tuple[str, str]] = (
            {}
        )  # normalized_path -> (parser_key, parser_ext)
        passing_suites_by_base: dict[str, str] = {}  # filename_base -> parser_key

        for parser_key, status_val in raw_parser_results.items():
            if " | test suite" in parser_key and status_val == "PASSED":
                file_path = parser_key.split(" | ")[0]
                parser_ext = _get_extension(file_path)
                normalized_path = _strip_extension(file_path).lower()

                # Store with extension info for validation
                passing_suites[normalized_path] = (parser_key, parser_ext)

                # Also store filename base for individual test name matching
                filename = file_path.split("/")[-1]
                filename_no_ext = _strip_extension(filename)
                for suffix in ("Test", ".test", ".spec", "_test", "_spec"):
                    if filename_no_ext.endswith(suffix):
                        filename_no_ext = filename_no_ext[: -len(suffix)]
                        break
                passing_suites_by_base[filename_no_ext.lower()] = parser_key

        for req_test in required_tests:
            if req_test in test_status_map:
                continue  # Already matched by existing logic

            # Case 1: File path with possibly different extension (.ts vs .js)
            # e.g., FAIL_TO_PASS: "test/file.ts" vs Parser: "test/file.js | test suite"
            # ONLY match if extensions are a valid TypeScript↔JavaScript pair
            req_ext = _get_extension(req_test)
            if "/" in req_test and req_ext is not None:
                normalized_req = _strip_extension(req_test).lower()
                if normalized_req in passing_suites:
                    parser_key, parser_ext = passing_suites[normalized_req]
                    if _is_valid_extension_pair(req_ext, parser_ext):
                        test_status_map[req_test] = "PASSED"
                continue

            # Case 2: Multi-pipe format "file | spec | description" or "file | description"
            # e.g., FAIL_TO_PASS: "test/file.ts | removeTechnicalFields | it does X"
            # vs Parser: "test/file.js | test suite"
            # ONLY match if extensions are a valid TypeScript↔JavaScript pair
            if " | " in req_test:
                req_file_path = req_test.split(" | ")[0]
                req_ext = _get_extension(req_file_path)
                if req_ext is not None:
                    normalized_req = _strip_extension(req_file_path).lower()
                    if normalized_req in passing_suites:
                        parser_key, parser_ext = passing_suites[normalized_req]
                        if _is_valid_extension_pair(req_ext, parser_ext):
                            test_status_map[req_test] = "PASSED"
                continue

            # Case 3: Individual test name (no path, no pipe)
            # e.g., FAIL_TO_PASS: "ReferralLinkNews isShown returns a Promise"
            # vs Parser: "test/ReferralLinkNewsTest.js | test suite"
            # This case doesn't involve extension matching - just name-based association
            req_test_lower = req_test.lower()
            for suite_base, parser_key in passing_suites_by_base.items():
                if req_test_lower.startswith(suite_base):
                    test_status_map[req_test] = "PASSED"
                    break

    return test_status_map


# Language categories for test argument processing (matches custom_eval.py)
_JS_TS_LANGUAGES = ("js", "ts", "javascript", "typescript", "jsx", "tsx")


def _process_validation_test_args(
    tests_to_pass: list[str],
    run_script_content: str | None,
    language: str | None,
) -> str:
    """
    Process test arguments for validation, matching the logic in custom_eval.py's
    augment_test_spec_with_required_tests() function.

    This handles:
    - ansible-test: strips ::Class::method suffix (ansible-test doesn't understand pytest ID syntax)
    - JS/TS: strips " | description" suffix from test names
    - Proper escaping of single quotes for shell safety
    - Parameterized tests with commas (test_func[1,2,3])

    Args:
        tests_to_pass: List of test identifiers (FAIL_TO_PASS)
        run_script_content: Content of run_script.sh (to detect ansible-test)
        language: Language string (e.g., "python", "javascript", "go")

    Returns:
        Shell-safe quoted arguments string to pass to run_script.sh
    """
    if not tests_to_pass:
        return ""

    args_to_pass = list(tests_to_pass)

    # Special case: ansible-test doesn't understand pytest ID syntax (::Class::method)
    # Matches custom_eval.py lines 269-278
    uses_ansible_test = run_script_content and "ansible-test" in run_script_content
    if uses_ansible_test and any("::" in t for t in args_to_pass):
        # Strip ::Class::method suffix to get file paths
        extracted_files = list(set(t.split("::")[0] for t in args_to_pass if "::" in t))
        if extracted_files:
            args_to_pass = extracted_files

    # JS/TS: Strip " | description" suffix from test names.
    # Matches custom_eval.py lines 280-305
    # SWEAP JS/TS test names use "file | test description" format (e.g., Mocha/Jest).
    # Stripping to just file paths is safe for all cases.
    if language and language.lower() in _JS_TS_LANGUAGES and args_to_pass:
        stripped_args = []
        for t in args_to_pass:
            if " | " in t:
                # Extract just the file path before " | "
                file_path = t.split(" | ")[0].strip()
                stripped_args.append(file_path)
            else:
                stripped_args.append(t)
        # Deduplicate while preserving order (same file may have multiple tests)
        seen: set[str] = set()
        args_to_pass = []
        for arg in stripped_args:
            if arg not in seen:
                seen.add(arg)
                args_to_pass.append(arg)

    if not args_to_pass:
        return ""

    # Quote each test path to handle special characters (matches custom_eval.py lines 307-315)
    # Pass tests as separate quoted arguments (not comma-joined) to avoid
    # comma-splitting issues in run_script.sh. This handles parameterized
    # pytest tests like test_func[1,2,3] which contain commas inside brackets.
    quoted_tests = []
    for t in args_to_pass:
        escaped = t.replace("'", "'\\''")
        quoted_tests.append(f"'{escaped}'")

    return " ".join(quoted_tests)




# Cache for HIL-Bench SWE results
# Key: attempt ID
# Value: list of {"input": HiLBenchAgentSWEData, "result": HILBenchResult}
# TODO BETTER!!!!!
HIL_BENCH_RESULTS_CACHE = CacheFactory.create_cache(
    CacheConfig(
        cache_backend="s3",
        s3_bucket="scale-ml",
        s3_prefix="hil_bench/swe/linter_results/baseline",
    )
)


def _get_hil_bench_project_path() -> Path:
    """Get the path to hil_bench project relative to this file."""
    # This file is at: models/genai/genai/system/agentic_autoreviewer/agents/hil_bench_agent.py
    # We need to go up to models/ and then into research_evals/hil_bench/
    this_file = Path(__file__).resolve()
    # Go up: agents -> agentic_autoreviewer -> system -> genai -> genai -> models
    models_dir = this_file.parents[5]
    hil_bench_path = models_dir / "research_evals" / "hil_bench"
    if not hil_bench_path.exists():
        raise FileNotFoundError(f"Could not find hil_bench at {hil_bench_path}")
    return hil_bench_path


class ProgrammingLanguage(str, Enum):
    PYTHON = "python"
    GO = "go"
    JAVASCRIPT = "javascript"  # Also used for TypeScript
    JAVA = "java"
    RUST = "rust"
    CPP = "cpp"


class TaskSetupContext(BaseModel):
    tmp_dirs: list[str] = Field(default_factory=list)
    task_dir: Path | None = None
    repo_dir: Path | None = None
    db_path: Path | None = None
    instances_file: Path | None = None
    chroma_path: str | None = None
    chroma_collection_name: str | None = None
    database_descriptions_dir: str | None = None
    golden_sql: str | None = None
    golden_output: str | None = None
    golden_patch: str | None = None
    validation_error: str | None = None
    # For sandbox-mode deferred git operations
    repo_github_url: str | None = None
    base_commit: str | None = None
    setup_patch: str | None = None
    docker_image_name: str | None = None
    instance_id: str | None = None
    # For sandbox-mode logging
    fail_to_pass: list[str] | None = None
    test_patch: str | None = None
    llm_extracted_functions: list[str] | None = None
    internal_dockerfile: str | None = None
    internal_repo_url: str | None = None
    # Language for test argument processing
    language: str | None = None

    def add_tmp_dir(self, path: str) -> None:
        self.tmp_dirs.append(path)

    def cleanup(self, ignore_errors: bool = False) -> None:
        for tmp_dir in self.tmp_dirs:
            if tmp_dir and Path(tmp_dir).exists():
                if ignore_errors:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                else:
                    shutil.rmtree(tmp_dir)


def _get_s3_client():
    return boto3.client("s3")


def _get_bucket_key(url: str) -> tuple[str, str]:
    if not url.startswith("s3://"):
        raise ValueError(f"Not an S3 URL: {url}")
    path = url[5:]  # remove "s3://""
    parts = path.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    return bucket, key


def _decode_bytes(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be")
    # Default to UTF-8, with fallback to latin-1
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _download_file(url: str) -> str:
    local_tmp_dir = tempfile.mkdtemp()
    file_name = url.split("/")[-1]
    local_file = Path(local_tmp_dir) / file_name
    if url.startswith("s3://"):
        bucket, key = _get_bucket_key(url)
        _get_s3_client().download_file(bucket, key, str(local_file))
    elif url.startswith("scale-cds://"):
        try:
            from customer_data_service_python_helper.cds_client import CdsClient, ClientType

            with CdsClient(ClientType.live, "ml-worker") as cds_client:
                signed_url = cds_client.fetch_signed_url(url)
                urllib.request.urlretrieve(signed_url, str(local_file))
        except ImportError:
            raise ImportError(
                "customer_data_service_python_helper not installed. "
                "Install it to use scale-cds:// URLs"
            )
    elif url.startswith("http://") or url.startswith("https://"):
        urllib.request.urlretrieve(url, str(local_file))
    else:
        raise ValueError(f"Unsupported URL scheme: {url}")
    return str(local_file)


def _read_file_from_url(url: str) -> str:
    if url.startswith("s3://"):
        bucket, key = _get_bucket_key(url)
        response = _get_s3_client().get_object(Bucket=bucket, Key=key)
        return _decode_bytes(response["Body"].read())
    elif url.startswith("scale-cds://"):
        try:
            from customer_data_service_python_helper.cds_client import CdsClient, ClientType

            with CdsClient(ClientType.live, "ml-worker") as cds_client:
                signed_url = cds_client.fetch_signed_url(url)
                with urllib.request.urlopen(signed_url) as response:
                    return _decode_bytes(response.read())
        except ImportError:
            raise ImportError(
                "customer_data_service_python_helper not installed. "
                "Install it to use scale-cds:// URLs"
            )
    elif url.startswith("http://") or url.startswith("https://"):
        with urllib.request.urlopen(url) as response:
            return _decode_bytes(response.read())
    else:
        raise ValueError(f"Unsupported URL scheme: {url}")


def _download_sweap_parser(instance_id: str) -> str | None:
    url = f"{SWEAP_REPO_RAW_URL}/run_scripts/{instance_id}/parser.py"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return _decode_bytes(response.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"⚠️ Parser script not found at {url}")
            return None
        raise
    except Exception as e:
        print(f"⚠️ Failed to download parser script from {url}: {e}")
        return None


def _filter_infrastructure_from_patch(patch: str) -> str:
    """
    Filter out HIL-bench infrastructure files from a patch. Mostly for backwards compatibility with old patches that might have included these files.
    """
    if not patch or not patch.strip():
        return patch
    file_diffs = re.split(r"(?=diff --git )", patch)
    filtered_diffs = []
    for diff in file_diffs:
        if not diff.strip():
            continue
        should_filter = False
        for infra_file in _SWEAP_INFRASTRUCTURE_FILES:
            if f"a/{infra_file}" in diff and f"b/{infra_file}" in diff:
                should_filter = True
                break
        if not should_filter:
            filtered_diffs.append(diff)
    return "".join(filtered_diffs)


def _normalize_patch_line_endings(patch_content: str, repo_dir: Path) -> str:
    """
    Strip carriage returns from patch text.

    Git patch syntax is line-oriented and `git apply` expects internally consistent
    patch separators. Rewriting individual hunk lines to CRLF while joining the
    patch with LF can corrupt otherwise valid patches, especially for Java files
    checked out with CRLF. Git can apply LF-delimited patches to CRLF worktrees,
    so keep the patch itself consistently LF-delimited.
    """
    if not patch_content or not patch_content.strip():
        return patch_content
    normalized = patch_content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _docker_pull_with_retry(image_name: str) -> None:
    """Pull a Docker image with retries for transient errors."""
    max_retries = 3
    transient_errors = [
        "429",
        "rate limit",
        "toomanyrequests",
        "timeout",
        "connection",
        "503",
        "500",
        "no such container",
        "no such image",
        "daemon",
    ]
    last_error = None
    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["docker", "pull", image_name],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for large images
            )
            if result.returncode == 0:
                return
            error_msg = result.stderr or result.stdout or "Docker pull failed"
            last_error = error_msg
            if any(e in error_msg.lower() for e in transient_errors) and attempt < max_retries - 1:
                delay = 30 * (attempt + 1)
                logger.warning(
                    f"Docker pull failed (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {error_msg[:200]}"
                )
                time.sleep(delay)
                continue
            raise subprocess.CalledProcessError(result.returncode, "docker pull", error_msg)
        except subprocess.TimeoutExpired:
            last_error = "Docker pull timed out after 10 minutes"
            if attempt < max_retries - 1:
                logger.warning(f"{last_error} (attempt {attempt + 1}/{max_retries}), retrying...")
                continue
            raise RuntimeError(last_error)
    raise RuntimeError(f"Docker pull failed after {max_retries} attempts: {last_error}")


def _docker_image_pull_candidates(image_name: str) -> list[str]:
    """Return ordered Docker image candidates.

    Order preference:
    1) non-truncated tags
    2) truncated (128-char) tags
    And for each, include the element/element-web alias variant when applicable.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    _append(image_name)
    repo_prefix = "jefzda/sweap-images:"
    if not image_name.startswith(repo_prefix):
        return candidates

    tag = image_name[len(repo_prefix) :]
    element_web_prefix = "element-hq.element-web-element-hq__element-web-"
    element_prefix = "element-hq.element-element-hq__element-web-"
    alias_tag = None
    if tag.startswith(element_web_prefix):
        alias_tag = f"{element_prefix}{tag[len(element_web_prefix):]}"
    elif tag.startswith(element_prefix):
        alias_tag = f"{element_web_prefix}{tag[len(element_prefix):]}"

    if alias_tag:
        _append(f"{repo_prefix}{alias_tag}")

    # Last fallback: truncate tag to 128 chars (Docker tag hard limit in many environments).
    if len(tag) > 128:
        _append(f"{repo_prefix}{tag[:128]}")
        if alias_tag:
            _append(f"{repo_prefix}{alias_tag[:128]}")
    return candidates


def _is_transient_docker_build_error(error_msg: str) -> bool:
    transient_errors = [
        "grpc",
        "connection",
        "timeout",
        "unavailable",
        "docker.io",
        "buildx",
        "429",
        "rate limit",
        "toomanyrequests",
        "500",
        "503",
        "daemon",
    ]
    return any(e in error_msg.lower() for e in transient_errors)


def _internal_test_runner_install_script(language: str) -> str:
    """Best-effort installer for generic test runner commands on internal SWEAP images."""
    lang = _normalize_swe_language(language)
    return f"""#!/bin/sh
set -eu

LANGUAGE={shlex.quote(lang)}
export DEBIAN_FRONTEND=noninteractive

has_cmd() {{
    command -v "$1" >/dev/null 2>&1
}}

apt_install() {{
    if has_cmd apt-get; then
        apt-get update
        apt-get install -y --no-install-recommends "$@"
        rm -rf /var/lib/apt/lists/*
        return 0
    fi
    return 1
}}

apk_install() {{
    if has_cmd apk; then
        apk add --no-cache "$@"
        return 0
    fi
    return 1
}}

yum_install() {{
    if has_cmd microdnf; then
        microdnf install -y "$@" && microdnf clean all
        return 0
    fi
    if has_cmd yum; then
        yum install -y "$@" && yum clean all
        return 0
    fi
    if has_cmd dnf; then
        dnf install -y "$@" && dnf clean all
        return 0
    fi
    return 1
}}

install_os_packages() {{
    apt_install "$@" || apk_install "$@" || yum_install "$@" || true
}}

install_python_runners() {{
    if ! has_cmd python3 && has_cmd python; then
        ln -sf "$(command -v python)" /usr/local/bin/python3 || true
    fi
    if ! has_cmd python3; then
        apt_install python3 python3-pip || apk_install python3 py3-pip || yum_install python3 python3-pip || true
    fi
    if has_cmd python3; then
        python3 -m ensurepip --upgrade >/dev/null 2>&1 || true
        python3 -m pip install --no-cache-dir --upgrade pytest pytest-asyncio >/dev/null 2>&1 || \\
            python3 -m pip install --no-cache-dir --break-system-packages --upgrade pytest pytest-asyncio >/dev/null 2>&1 || true
        if [ -f /app/pyproject.toml ] || [ -f /app/setup.py ]; then
            (cd /app && python3 -m pip install --no-cache-dir -e . >/dev/null 2>&1) || \\
                (cd /app && python3 -m pip install --no-cache-dir --break-system-packages -e . >/dev/null 2>&1) || \\
                echo "[internal-runner-install] Editable install of /app failed; continuing"
        fi
    fi
}}

install_go_runners() {{
    if ! has_cmd go; then
        apt_install golang-go || apk_install go || yum_install golang || yum_install golang-go || true
    fi
}}

install_js_runners() {{
    if ! has_cmd npm; then
        apt_install nodejs npm || apk_install nodejs npm || yum_install nodejs npm || true
    fi
    if has_cmd npm; then
        npm install -g jest vitest mocha karma-cli yarn pnpm >/dev/null 2>&1 || true
    fi
    if has_cmd corepack; then
        corepack enable >/dev/null 2>&1 || true
    fi
}}

install_java_runners() {{
    if ! has_cmd java; then
        apt_install default-jdk || apt_install openjdk-17-jdk || apk_install openjdk17-jdk || yum_install java-17-openjdk-devel || true
    fi
    if ! has_cmd mvn; then
        apt_install maven || apk_install maven || yum_install maven || true
    fi
    if ! has_cmd gradle; then
        apt_install gradle || apk_install gradle || yum_install gradle || true
    fi
}}

install_rust_runners() {{
    if ! has_cmd cargo || ! has_cmd rustc; then
        apt_install cargo rustc || apk_install cargo rust || yum_install cargo rust || true
    fi
}}

install_cpp_runners() {{
    if ! has_cmd gcc || ! has_cmd g++; then
        apt_install build-essential g++ gcc make cmake ninja-build pkg-config || \\
            apk_install build-base cmake ninja pkgconf || \\
            yum_install gcc gcc-c++ make cmake ninja-build pkgconfig || true
    fi
    if ! has_cmd cmake; then
        apt_install cmake || apk_install cmake || yum_install cmake || true
    fi
    if ! has_cmd make; then
        apt_install make || apk_install make || yum_install make || true
    fi
    if ! has_cmd ninja && ! has_cmd ninja-build; then
        apt_install ninja-build || apk_install ninja || yum_install ninja-build || true
    fi
}}

case "$LANGUAGE" in
    python|py)
        install_python_runners
        ;;
    go|golang)
        install_go_runners
        ;;
    javascript|js|typescript|ts|jsx|tsx)
        install_js_runners
        ;;
    java)
        install_java_runners
        ;;
    rust|rs)
        install_rust_runners
        ;;
    c++|cpp|cxx|cc|cplusplus|cpluscplus)
        install_cpp_runners
        ;;
    *)
        echo "[internal-runner-install] No generic runner bootstrap for language: $LANGUAGE"
        ;;
esac
"""


def _build_internal_sweap_base_image(
    dockerfile_content: str, image_name: str, repo_url: str, base_commit: str, language: str
) -> None:
    """Build the internal SWEAP image from a cloned repo root as Docker context."""
    build_dir = Path(tempfile.mkdtemp())
    try:
        dockerfile_path = build_dir / "Dockerfile"
        repo_dir = build_dir / "repo"
        dockerfile_path.write_text(dockerfile_content)
        logger.info(
            "Cloning internal SWEAP repo %s as Docker build context for %s", repo_url, image_name
        )
        clone_result = subprocess.run(
            ["git", "clone", "--no-checkout", repo_url, str(repo_dir)],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if clone_result.returncode != 0:
            error_msg = clone_result.stderr or clone_result.stdout or "git clone failed"
            raise ValueError(
                f"Internal SWEAP repo_url failed to clone ({repo_url}): {error_msg[-2000:]}"
            )
        checkout_result = subprocess.run(
            ["git", "checkout", base_commit],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if checkout_result.returncode != 0:
            error_msg = checkout_result.stderr or checkout_result.stdout or "git checkout failed"
            raise ValueError(
                f"Internal SWEAP repo_url {repo_url} does not contain base_commit "
                f"'{base_commit}': {error_msg[-2000:]}"
            )
        logger.info(
            "Building internal SWEAP Docker image %s from metadata Dockerfile with repo root context",
            image_name,
        )
        try:
            docker_login_hub()
        except Exception as e:
            logger.warning(
                "Docker Hub login failed before internal SWEAP build; continuing unauthenticated: %s",
                e,
            )
        build_env = os.environ.copy()
        build_env["DOCKER_BUILDKIT"] = "1"
        max_build_retries = 3
        last_error = None
        base_image_name = f"hilbench-internal-sweap-base:{hashlib.sha256(image_name.encode()).hexdigest()[:16]}"
        for attempt in range(max_build_retries):
            try:
                result = subprocess.run(
                    ["docker", "build", "-f", str(dockerfile_path), "-t", base_image_name, "."],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    env=build_env,
                )
            except subprocess.TimeoutExpired:
                last_error = "Docker build timed out after 60 minutes"
                if attempt < max_build_retries - 1:
                    logger.warning("%s, retrying", last_error)
                    continue
                raise RuntimeError(last_error)
            if result.returncode == 0:
                logger.info("Successfully built internal SWEAP base image: %s", base_image_name)
                break
            error_msg = result.stderr or result.stdout or "Docker build failed"
            last_error = error_msg
            if _is_transient_docker_build_error(error_msg) and attempt < max_build_retries - 1:
                logger.warning(
                    "Internal SWEAP Docker build failed (attempt %s/%s), retrying: %s",
                    attempt + 1,
                    max_build_retries,
                    error_msg[-500:],
                )
                time.sleep(30)
                continue
            raise ValueError(
                f"Internal SWEAP Dockerfile failed to build image {base_image_name}: {error_msg[-2000:]}"
            )
        else:
            raise RuntimeError(
                f"Internal SWEAP Docker build failed after {max_build_retries} attempts: {last_error}"
            )

        install_script_path = build_dir / "install_internal_sweap_test_runners.sh"
        install_script_path.write_text(_internal_test_runner_install_script(language))
        runner_dockerfile_path = build_dir / "Dockerfile.internal-runner"
        runner_dockerfile_path.write_text(
            "\n".join(
                [
                    f"FROM {base_image_name}",
                    "USER root",
                    "COPY install_internal_sweap_test_runners.sh /tmp/install_internal_sweap_test_runners.sh",
                    "RUN /bin/sh /tmp/install_internal_sweap_test_runners.sh && rm -f /tmp/install_internal_sweap_test_runners.sh",
                    "",
                ]
            )
        )
        logger.info(
            "Adding internal-only generic test runner layer for language=%s on image %s",
            language,
            image_name,
        )
        result = subprocess.run(
            ["docker", "build", "-f", str(runner_dockerfile_path), "-t", image_name, "."],
            cwd=build_dir,
            capture_output=True,
            text=True,
            timeout=1800,
            env=build_env,
        )
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "internal test runner layer build failed"
            raise ValueError(
                f"Internal SWEAP test runner installation failed for image {image_name}: {error_msg[-2000:]}"
            )
        logger.info("Successfully built internal SWEAP Docker image: %s", image_name)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _extract_repo_from_docker_image(image_name: str, dest_dir: str) -> str:
    """Extract /app from a Docker image and return the resolved image reference used."""
    image_candidates = _docker_image_pull_candidates(image_name)
    selected_image_name = image_name
    # Check if image already exists locally to avoid unnecessary Docker Hub rate limit hits
    try:
        for candidate in image_candidates:
            check_result = subprocess.run(
                ["docker", "image", "inspect", candidate],
                capture_output=True,
                text=True,
            )
            if check_result.returncode == 0:
                selected_image_name = candidate
                logger.info(
                    "Docker image %s already exists locally, skipping pull",
                    selected_image_name,
                )
                break
        else:
            # Image doesn't exist, need to pull
            # Login to Docker Hub for higher rate limits (authenticated: 200 pulls/6h vs unauthenticated: 100 pulls/6h)
            try:
                docker_login_hub()
                logger.info("Logged in to Docker Hub for higher rate limits")
            except Exception as e:
                logger.warning(
                    f"Docker Hub login failed, continuing with unauthenticated pulls: {e}"
                )
            last_pull_error = None
            for candidate in image_candidates:
                logger.info("Pulling Docker image: %s", candidate)
                try:
                    _docker_pull_with_retry(candidate)
                    selected_image_name = candidate
                    if candidate != image_name:
                        logger.warning(
                            "Using fallback Docker image tag alias: %s (primary=%s)",
                            selected_image_name,
                            image_name,
                        )
                    break
                except Exception as e:
                    last_pull_error = e
                    logger.warning("Docker pull failed for candidate %s: %s", candidate, e)
            else:
                raise RuntimeError(
                    f"Failed to pull any Docker image candidate for {image_name}: {last_pull_error}"
                )
    except Exception as e:
        logger.warning(f"Failed to check if image exists, will attempt pull: {e}")
        # Login and pull as fallback
        try:
            docker_login_hub()
        except Exception:
            pass
        last_pull_error = None
        for candidate in image_candidates:
            logger.info("Pulling Docker image: %s", candidate)
            try:
                _docker_pull_with_retry(candidate)
                selected_image_name = candidate
                if candidate != image_name:
                    logger.warning(
                        "Using fallback Docker image tag alias: %s (primary=%s)",
                        selected_image_name,
                        image_name,
                    )
                break
            except Exception as pull_error:
                last_pull_error = pull_error
                logger.warning("Docker pull failed for candidate %s: %s", candidate, pull_error)
        else:
            raise RuntimeError(
                f"Failed to pull any Docker image candidate for {image_name}: {last_pull_error}"
            )

    # Retry docker create + cp for transient errors (daemon issues, etc.)
    max_retries = 3
    transient_errors = ["500", "503", "daemon", "timeout", "connection"]
    last_error = None
    for attempt in range(max_retries):
        container_id = None
        try:
            logger.info(
                f"Creating temporary container from {selected_image_name} (attempt {attempt + 1}/{max_retries})"
            )
            result = subprocess.run(
                ["docker", "create", selected_image_name],
                capture_output=True,
                text=True,
                timeout=1200,
            )
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "docker create failed"
                raise subprocess.CalledProcessError(result.returncode, "docker create", error_msg)

            container_id = result.stdout.strip()
            if not container_id:
                raise RuntimeError("docker create returned empty container ID")

            # Verify container exists
            verify_result = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True,
                timeout=30,
            )
            if verify_result.returncode != 0:
                raise RuntimeError(f"Container {container_id} does not exist after creation")

            logger.info(f"Extracting /app from container {container_id[:12]} to {dest_dir}")
            subprocess.run(
                ["docker", "cp", f"{container_id}:/app/.", dest_dir],
                check=True,
                capture_output=True,
                text=True,
                timeout=600,  # 10 min timeout for large repos
            )
            logger.info("Successfully extracted repo from Docker image")
            break  # Success, exit retry loop

        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as e:
            error_msg = str(e)
            if hasattr(e, "stderr") and e.stderr:
                error_msg = e.stderr
            elif hasattr(e, "stdout") and e.stdout:
                error_msg = e.stdout
            last_error = error_msg

            if (
                any(err in error_msg.lower() for err in transient_errors)
                and attempt < max_retries - 1
            ):
                delay = 30 * (attempt + 1)
                logger.warning(
                    f"Docker create/cp failed (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {error_msg[:200]}"
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"Failed to extract repo from Docker image: {error_msg}")

        finally:
            if container_id:
                logger.info(f"Removing temporary container {container_id[:12]}")
                subprocess.run(
                    ["docker", "rm", container_id],
                    capture_output=True,
                    text=True,
                )  # Best effort, don't fail if rm fails
    else:
        raise RuntimeError(f"Docker create/cp failed after {max_retries} attempts: {last_error}")

    return selected_image_name


def _build_agent_image(
    base_image: str,
    local_repo_path: Path,
    run_script_path: Path,
    parser_script_path: Path,
    instance_id: str,
    target_image_name: Optional[str] = None,
) -> str:
    """Build a Docker image for validation, agent-solve, and eval phases that has the prepared repo at /app (base commit -> setup_patch if exists -> nuclear-reset single-commit history)."""
    import hashlib

    if target_image_name:
        built_image_name = target_image_name
    else:
        tag_hash = hashlib.sha256(instance_id.encode()).hexdigest()[:16]
        built_image_name = f"hil-bench-agent:{tag_hash}"
    build_dir = Path(tempfile.mkdtemp())
    try:
        logger.info(f"Building agent image from {base_image} with repo from {local_repo_path}")
        local_repo_dest = build_dir / "local_repo"
        shutil.copytree(local_repo_path, local_repo_dest, symlinks=True)
        for root, dirs, files in os.walk(local_repo_dest):
            for name in files + dirs:
                path = Path(root) / name
                if path.is_symlink() and not path.exists():
                    path.unlink()
        run_script_dest = build_dir / "run_script.sh"
        shutil.copy2(run_script_path, run_script_dest)
        parser_dest = build_dir / "parser.py"
        if not parser_script_path.exists():
            raise RuntimeError(f"Missing required parser.py at {parser_script_path}")
        shutil.copy2(parser_script_path, parser_dest)
        dockerfile_content = f"""FROM {base_image}

# Fix pip config: jefzda images have pip.conf pointing to non-existent 127.0.0.1:9876
ENV PIP_INDEX_URL=https://pypi.org/simple/

# Preserve Go/Node toolchain PATHs in .bashrc for SWE-agent shell sessions
# The jefzda base images set ENV PATH=/go/bin:/usr/local/go/bin:... but SWE-agent
# sources .bashrc when starting the shell, which doesn't inherit Docker ENV vars.
# We add these paths to .bashrc so they're available in the agent's shell session.
RUN echo 'export PATH=/go/bin:/usr/local/go/bin:/usr/local/node/bin:$PATH' >> /root/.bashrc && \\
    echo 'export GOPATH=/go' >> /root/.bashrc

# Install patch utility (required by swebench for applying diffs during evaluation)
# Detect package manager and install patch if missing
RUN if ! command -v patch >/dev/null 2>&1; then \\
        if command -v apk >/dev/null 2>&1; then \\
            apk add --no-cache patch; \\
        elif command -v apt-get >/dev/null 2>&1; then \\
            apt-get update && apt-get install -y --no-install-recommends patch && rm -rf /var/lib/apt/lists/*; \\
        elif command -v yum >/dev/null 2>&1; then \\
            yum install -y patch && yum clean all; \\
        fi; \\
    fi

# Nuke the base-image /app BEFORE copying our prepared repo.
# Docker COPY is an overlay: it adds/overwrites files but does NOT delete files that
# exist in the base-image layer but are absent from the COPY source. The jefzda base
# image can have stale source files from a different checkout. Our prepared repo is
# the canonical base_commit + setup_patch state, so hide the whole old /app layer.
RUN rm -rf /app && mkdir -p /app

# Copy local repo to /app with BuildKit's COPY --chmod (faster than separate chmod -R)
COPY --chmod=777 ./local_repo /app
# Ignore file mode changes so git diff doesn't include spurious 644->755 mode diffs
# No need for "git remote remove origin" - nuclear reset left no remote in .git/config
RUN cd /app && git config core.fileMode false

# Copy SWEAP scripts for test execution to /root/ (NOT /app/ so that the agent cannot see them during problem-solving)
COPY ./run_script.sh /root/run_script.sh
COPY ./parser.py /root/parser.py
RUN chmod +x /root/run_script.sh || true

# Create /testbed symlink: swebench hardcodes DOCKER_WORKDIR="/testbed"
RUN ln -sf /app /testbed

WORKDIR /app/

# Fix entrypoint: jefzda images have /bin/bash as entrypoint but bash is at /usr/bin/bash
ENTRYPOINT ["/bin/sh", "-c", "sleep infinity"]
"""
        dockerfile_path = build_dir / "Dockerfile"
        dockerfile_path.write_text(dockerfile_content)
        # Login to Docker Hub to get better rate limits
        docker_login_hub()
        logger.info(f"Building Docker image: {built_image_name}")
        build_env = os.environ.copy()
        build_env["DOCKER_BUILDKIT"] = "1"
        transient_errors = [
            "grpc",
            "connection",
            "timeout",
            "unavailable",
            "docker.io",
            "buildx",
            "429",
            "rate limit",
            "toomanyrequests",
        ]
        max_build_retries = 3
        last_error = None
        for build_attempt in range(max_build_retries):
            # Clean stale BuildKit mounts before each attempt which can accumulate on failures
            try:
                subprocess.run(
                    ["bash", "-c", "rm -rf /var/lib/docker/tmp/buildkit-* 2>/dev/null || true"],
                    capture_output=True,
                )
            except Exception:
                pass  # Best effort cleanup
            try:
                result = subprocess.run(
                    ["docker", "build", "-t", built_image_name, "."],
                    cwd=build_dir,
                    capture_output=True,
                    text=True,
                    timeout=3600,  # for large repos, COPY steps can take 25-40 minutes
                    env=build_env,
                )
            except subprocess.TimeoutExpired:
                logger.error("Agent image build timed out after 60 minutes")
                raise RuntimeError("Agent image build timed out after 60 minutes")
            if result.returncode == 0:
                logger.info(f"Verifying image {built_image_name} exists")
                verify_result = subprocess.run(
                    ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if verify_result.returncode == 0:
                    images_list = verify_result.stdout.strip().split("\n")
                    if built_image_name in images_list:
                        logger.info(f"Verified agent image exists: {built_image_name}")
                    else:
                        logger.error("Agent image NOT FOUND after build!")
                        raise RuntimeError(
                            f"Agent image build reported success but image not found: {built_image_name}"
                        )
                else:
                    logger.warning("Could not verify agent image (docker images failed)")
                break
            error_msg = result.stderr or result.stdout or "Docker build failed"
            last_error = error_msg
            is_transient = any(e in error_msg.lower() for e in transient_errors)
            if is_transient and build_attempt < max_build_retries - 1:
                error_preview = error_msg[-500:] if len(error_msg) > 500 else error_msg
                logger.warning(
                    f"Docker build failed (attempt {build_attempt + 1}/{max_build_retries}), retrying in 30s: {error_preview}"
                )
                time.sleep(30)
                continue
            error_tail = error_msg[-1000:] if len(error_msg) > 1000 else error_msg
            logger.error(f"Docker build failed: {error_tail}")
            raise subprocess.CalledProcessError(result.returncode, "docker build", error_tail)
        else:
            logger.error(f"Docker build failed after {max_build_retries} attempts: {last_error}")
            error_tail = last_error[-500:] if last_error and len(last_error) > 500 else last_error
            raise RuntimeError(
                f"Docker build failed after {max_build_retries} attempts: {error_tail if error_tail else 'unknown error'}"
            )
        logger.info(f"Successfully built agent image: {built_image_name}")
        return built_image_name
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


class HILBenchMode(str, Enum):
    SWE = "swe"
    SQL = "sql"


class SandboxConfig(BaseModel):
    """
    Configuration for Scale Sandbox VM used when use_sandbox=True.

    The sandbox VM provides a Docker-enabled environment for running HIL-Bench commands when the host environment (e.g., K8s pods) do not support Docker-in-Docker.
    """

    cpu: float = Field(
        default=6.0,
        description="Number of CPUs for the VM",
    )
    memory: int = Field(
        default=20 * 1024,
        description="Memory in MB for the VM",
    )
    disk_size: str = Field(
        default="150Gi",
        description="Disk size for the VM (needs space for Docker images + large repos)",
    )
    timeout: int = Field(
        default=VM_TIMEOUT_SECONDS,
        description=f"Maximum VM lifetime in seconds ({VM_TIMEOUT_SECONDS // 3600} hours for persistent VMs)",
    )
    base_url: str = Field(
        default="https://sandbox.ml-serving-internal.scale.com",
        description="Scale Sandbox service URL",
    )
    client_timeout: float = Field(
        default=300.0,
        description="HTTP client timeout for sandbox API calls",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum retries for VM creation",
    )


class HILBenchConfig(BaseModel):
    """
    Configuration for HIL-Bench agent.

    This schema defines all parameters needed to run either the SWE or SQL
    HIL-Bench commands.
    """

    # Common fields
    mode: HILBenchMode = Field(
        ...,
        description="Which HIL-Bench command to run: 'swe' or 'sql'",
    )
    model_names: list[str] = Field(
        ...,
        description="Model name(s) to use (e.g., 'openai/gpt-4', 'anthropic/claude-3-opus')",
    )
    run_name: Optional[str] = Field(
        default=None,
        description="Name for the results folder (auto-generated if not provided)",
    )
    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory for results",
    )
    num_workers: int = Field(
        default=12,
        description="Number of parallel workers",
    )
    num_passes: int = Field(
        default=2,
        description="Number of passes/repetitions to run (2 runs per model per check)",
    )
    ask_human: bool = Field(
        default=False,
        description="Enable ask_human tool (agent can request blockers)",
    )
    with_blockers: bool = Field(
        default=False,
        description="Include blocker info in prompt (blockers provided upfront)",
    )
    all_modes: bool = Field(
        default=False,
        description="Run all three modes: baseline, ask-human, and with-blockers",
    )
    cleanup_docker: bool = Field(
        default=False,
        description="Clean up stale Docker containers before running (handled at script level)",
    )
    path: Optional[str] = Field(
        default=None,
        description="Path to instances.json file, task folder, or tasks directory",
    )

    # SWE-specific fields
    max_runtime: Optional[int] = Field(
        default=None,
        description="[SWE] Maximum runtime per task in seconds",
    )
    no_redo: bool = Field(
        default=False,
        description="[SWE] Skip instances with existing results",
    )
    cleanup_trajectories: bool = Field(
        default=True,
        description="[SWE] Clean up old trajectory data before running",
    )
    generate_instances: bool = Field(
        default=False,
        description="[SWE] Generate instances.json from tasks directory before running",
    )
    swe_baseline_per_instance_cost_limit: float = Field(
        default=5.0,
        description="[SWE] Cost limit per instance for baseline mode",
    )
    swe_baseline_max_steps: int = Field(
        default=200,
        description="[SWE] Maximum number of agent steps for baseline mode",
    )
    swe_with_blockers_per_instance_cost_limit: float = Field(
        default=5,
        description="[SWE] Cost limit per instance for with_blockers mode",
    )
    swe_with_blockers_max_steps: int = Field(
        default=200,
        description="[SWE] Maximum number of agent steps for with_blockers mode",
    )
    dataset: str = Field(
        # default="princeton-nlp/SWE-bench_Verified",
        default="SHOULD_NOT_BE_USED",
        description="[SWE] SWE-bench dataset name for test validation",
    )

    # SQL-specific fields
    instances_files: Optional[list[str]] = Field(
        default=None,
        description="[SQL] List of instances files (e.g., '100_instances.json')",
    )
    sql_baseline_per_instance_cost_limit: float = Field(
        default=2.5,
        description="[SQL] Cost limit per instance for baseline mode",
    )
    sql_baseline_max_steps: int = Field(
        default=100,
        description="[SQL] Maximum number of agent steps for baseline mode",
    )
    sql_with_blockers_per_instance_cost_limit: float = Field(
        default=2.5,
        description="[SQL] Cost limit per instance for with_blockers mode",
    )
    sql_with_blockers_max_steps: int = Field(
        default=100,
        description="[SQL] Maximum number of agent steps for with_blockers mode",
    )

    # Logging/debugging fields
    enable_model_call_logging: bool = Field(
        default=False,
        description="Enable verbose model call logging for Datadog (logs before/after each LLM call)",
    )

    # Sandbox execution fields
    use_sandbox: bool = Field(
        default=False,
        description="Run HIL-Bench inside a Scale Sandbox VM (enables Docker in K8s environments)",
    )
    sandbox_config: Optional[SandboxConfig] = Field(
        default=None,
        description="Configuration for the Sandbox VM (uses defaults if not provided)",
    )


class HILBenchResult(BaseModel):
    """Result from a HIL-Bench run."""

    success: bool = Field(..., description="Whether the run completed successfully")
    # TODO: could store results in s3?
    # results_dir: Optional[str] = Field(
    #     default=None, description="Path to results directory"
    # )
    metrics: Optional[dict[str, Any]] = Field(default=None, description="Metrics from the run")
    results: Optional[dict[str, Any]] = Field(default=None, description="Results from the run")
    error_message: Optional[str] = Field(
        default=None, description="Error message if the run failed"
    )
    subprocess_stderr: Optional[str] = Field(
        default=None,
        description="Subprocess stderr output for debugging when agent-solve fails",
    )


class GuessedBlocker(BaseModel):
    guess_reasoning: str
    blocker_id: str


class GuessedBlockers(BaseModel):
    guessed_blockers: list[GuessedBlocker]


class AlternativeQueries(BaseModel):
    blocker_id: str
    reasoning: str
    queries: list[str]


class AlternativeQueriesList(BaseModel):
    alternative_queries: list[AlternativeQueries]


class NoncriticalBlockerResult(BaseModel):
    blocker_id: str
    is_noncritical: bool
    matching_queries: list[str]


class TrajectorySummary(BaseModel):
    summary: str


class TrajectoryTechnicallyPassed(BaseModel):
    reasoning: str
    classification: bool


class ExtractedTestFunctions(BaseModel):
    functions: list[str]


class AgentRunStatus(Enum):
    AGENT_FAILED = (
        "agent_failed"  # Agent solved + we evaled, but agent didn't pass or hit context/cost limit
    )
    AGENT_PASSED = "agent_passed"  # Agent solved + we evaled, agent passed
    INFRA_ERROR = "infra_error"  # Agent solve or eval interrupted by some error


class AgentRunResult(BaseModel):
    model_name: str
    statuses: list[AgentRunStatus] = []  # one per pass/run
    agent_solutions: list[str] = []
    error_messages: list[Optional[str]] = []

    @property
    def any_passed(self) -> bool:
        return any(s == AgentRunStatus.AGENT_PASSED for s in self.statuses)

    @property
    def all_infra_error(self) -> bool:
        return (
            all(s == AgentRunStatus.INFRA_ERROR for s in self.statuses) if self.statuses else True
        )

    @property
    def passing_pass_index(self) -> int | None:
        for i, status in enumerate(self.statuses):
            if status == AgentRunStatus.AGENT_PASSED:
                return i
        return None

    @property
    def passing_solution(self) -> str:
        idx = self.passing_pass_index
        if idx is not None and idx < len(self.agent_solutions):
            return self.agent_solutions[idx]
        return ""

    @property
    def best_solution_pass_index(self) -> int | None:
        for i, sol in enumerate(self.agent_solutions):
            if sol:
                return i
        return None

    @property
    def best_solution(self) -> str:
        if self.any_passed:
            return self.passing_solution
        idx = self.best_solution_pass_index
        if idx is not None:
            return self.agent_solutions[idx]
        return ""

    def solution_pass_indices(self, solutions: list[str]) -> list[int]:
        indices = []
        for i, sol in enumerate(self.agent_solutions):
            if sol in solutions:
                indices.append(i)
        return indices

    # Backwards compatibility: single status (one run)
    @property
    def status(self) -> AgentRunStatus:
        return self.statuses[0] if self.statuses else AgentRunStatus.INFRA_ERROR

    @property
    def agent_solution(self) -> str:
        return self.agent_solutions[0] if self.agent_solutions else ""

    @property
    def error_message(self) -> Optional[str]:
        return self.error_messages[0] if self.error_messages else None


class HILBenchAgent(ReviewAgent, InProductAgentMixin):
    """
    Agent for running HIL-Bench evaluations (SWE and SQL benchmarks).

    This agent wraps the HIL-Bench CLI commands to integrate them into the ArchieQC pipeline. It supports both SWE-agent runs on code tasks and SQL benchmark tasks.

    Task data from input.data fields:
    - For SWE: repo_github_path, problem_statement, blocker_registry, instance_id (docker image derived from instance_id)
    - For SQL: database_s3_path, question, golden_sql, blocker_registry, instance_id, database_name

    The agent downloads data from S3, sets up the task directory structure, runs the hil_bench command, and returns results as AgentFeedbacks.
    """

    name: str = "HILBenchAgent"
    desc: str = (
        "Run HIL-Bench evaluations for both SWE and SQL tasks: blockers only and with full information"
    )
    user: str | None = None
    api_key_name: str = _ARCHIEQC_LITELLM_KEY_NAME
    display_name: str = "HiL-Bench Agent"
    taxonomies: list[str] = ["prompt", "response", "ground_truth_final_answer"]

    # Core settings
    task_type: str = export_field(
        Field(default=HILBenchMode.SWE.value, description="Running for SWE task or SQL task"),
        display_name="SWE or SQL",
        choices=[mode.value for mode in HILBenchMode],
        required=True,
    )
    mode: HILBenchMode | None = None

    # Run settings
    model_names: list[str] = []
    run_name: Optional[str] = None
    output_dir: Optional[str] = None
    num_workers: int = 12
    num_passes: int = 2  # 2 runs per model per check

    # Agent modes
    ask_human: bool = False
    all_modes: bool = False

    # Cleanup/logging settings
    cleanup_docker: bool = False
    cleanup_trajectories: bool = True
    enable_run_logs: bool = False
    last_task_dir: str | None = None

    # SWE-specific settings
    max_runtime: Optional[int] = None
    validation_only: bool = False
    skip_validation: bool = False
    no_redo: bool = False
    generate_instances: bool = False
    swe_baseline_per_instance_cost_limit: float = 5.0
    swe_baseline_max_steps: int = 200
    swe_with_blockers_per_instance_cost_limit: float = 5
    swe_with_blockers_max_steps: int = 200
    # dataset should NOT be used - we use custom instances via tasks_dir, not SWE-bench dataset
    dataset: str = "SHOULD_NOT_BE_USED"

    # SQL-specific settings
    sql_baseline_per_instance_cost_limit: float = 2.5
    sql_baseline_max_steps: int = 100
    sql_with_blockers_per_instance_cost_limit: float = 2.5
    sql_with_blockers_max_steps: int = 100

    # Logging/debugging settings
    enable_model_call_logging: bool = False

    # Sandbox settings
    use_sandbox: bool = False
    sandbox_config: Optional[dict[str, Any]] = None
    # Combined install script - runs apt-get update ONCE, then installs Docker + Python in PARALLEL
    _SANDBOX_SETUP_SCRIPT: str = """#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

# apt-get update returns exit code 0 even when ALL sources 503 (falls back to stale
# cache and prints "old ones used instead"). This wrapper checks the output for fetch
# failures and returns non-zero so the retry logic actually kicks in.
apt_update_strict() {
    local output
    output=$(apt-get update 2>&1)
    local exit_code=$?
    echo "$output"
    if [ $exit_code -ne 0 ]; then
        return 1
    fi
    if echo "$output" | grep -qi "Failed to fetch"; then
        echo "[SETUP] apt-get update returned 0 but has fetch failures, treating as error"
        return 1
    fi
    return 0
}

# Retry helper for apt commands (handles transient 503 errors from Ubuntu mirrors)
apt_retry() {
    local max_attempts=5
    local delay=10
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if "$@"; then
            return 0
        fi
        echo "[SETUP] apt command failed (attempt $attempt/$max_attempts), retrying in ${delay}s..."
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
        # Clean apt cache AND package lists before retry
        apt-get clean 2>/dev/null || true
        rm -rf /var/lib/apt/lists/* 2>/dev/null || true
        # Flush DNS cache so retries can resolve to different mirror IPs
        resolvectl flush-caches 2>/dev/null || systemd-resolve --flush-caches 2>/dev/null || true
    done
    echo "[SETUP] apt command failed after $max_attempts attempts"
    return 1
}

# Single apt-get update with all prerequisites (uses strict wrapper to catch 503s)
apt_retry apt_update_strict
apt_retry apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 python3-pip python3-venv git

# Retry helper for curl (handles transient network errors)
curl_retry() {
    local max_attempts=3
    local delay=5
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if curl "$@"; then
            return 0
        fi
        echo "[SETUP] curl failed (attempt $attempt/$max_attempts), retrying in ${delay}s..."
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
    echo "[SETUP] curl failed after $max_attempts attempts"
    return 1
}

# Install Docker repo
install -m 0755 -d /etc/apt/keyrings
curl_retry -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list

# Run Docker install and uv install in PARALLEL
(
    apt_retry apt_update_strict
    apt_retry apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin
    if ! id -u sandbox >/dev/null 2>&1; then
        useradd -m -s /bin/bash sandbox
    fi
    usermod -aG docker sandbox
    echo "[SETUP] Docker installed"
) &
DOCKER_PID=$!

(
    curl_retry -LsSf https://astral.sh/uv/install.sh | sh
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> /root/.bashrc
    echo "[SETUP] uv installed"
) &
UV_PID=$!

# Wait for both
wait $DOCKER_PID
DOCKER_EXIT=$?
wait $UV_PID
UV_EXIT=$?

if [ $DOCKER_EXIT -ne 0 ]; then
    echo "Docker installation failed"
    exit 1
fi
if [ $UV_EXIT -ne 0 ]; then
    echo "uv installation failed"
    exit 1
fi

echo "[SETUP] All installations complete"
"""

    # Common data fields
    instance_id: str = export_field(
        Field(default="instance_id", description="Instance ID"),
        display_name="Instance ID Field",
        override_type="data_fields",
        required=False,
    )
    blocker_registry: str = export_field(
        Field(default="blocker_registry", description="Blocker registry"),
        display_name="Blocker Registry Field",
        override_type="data_fields",
        required=False,
    )

    # SWE-specific data fields
    language: str = export_field(
        Field(default="language", description="Programming language"),
        display_name="ProgrammingLanguage Field",
        override_type="data_fields",
        required=False,
    )
    repo_name: str = export_field(
        Field(default="repo_name", description="Repository name"),
        display_name="Repository Name Field",
        override_type="data_fields",
        required=False,
    )
    base_commit: str = export_field(
        Field(default="base_commit", description="Base commit"),
        display_name="Base Commit Field",
        override_type="data_fields",
        required=False,
    )
    problem_statement: str = export_field(
        Field(default="problem_statement", description="Problem statement"),
        display_name="Problem Statement Field",
        override_type="data_fields",
        required=False,
    )
    problem_requirements: str = export_field(
        Field(default="problem_requirements", description="Problem requirements"),
        display_name="Problem Requirements Field",
        override_type="data_fields",
        required=False,
    )
    problem_interfaces: str = export_field(
        Field(default="problem_interfaces", description="Problem interfaces"),
        display_name="Problem Interfaces Field",
        override_type="data_fields",
        required=False,
    )
    setup_patch: dict[str, str] = export_field(
        Field(default="setup_patch", description="Setup patch"),
        display_name="Setup Patch Field",
        override_type="data_fields",
        required=False,
    )
    test_patch: dict[str, str] = export_field(
        Field(default="test_patch", description="Test patch"),
        display_name="Test Patch Field",
        override_type="data_fields",
        required=False,
    )
    golden_patch: dict[str, str] = export_field(
        Field(default="golden_patch", description="Golden patch"),
        display_name="Golden Patch Field",
        override_type="data_fields",
        required=False,
    )
    test_files: list[str] = export_field(
        Field(default="test_files", description="Test files"),
        display_name="Test Files Field",
        override_type="data_fields",
        required=False,
    )
    tests_to_pass: list[str] = export_field(
        Field(default="tests_to_pass", description="Tests to pass"),
        display_name="Tests To Pass Field",
        override_type="data_fields",
        required=False,
    )
    run_script: str = export_field(
        Field(default="run_script", description="Run script"),
        display_name="Test Run Script Field",
        override_type="data_fields",
        required=False,
    )

    # SQL-specific data fields
    database_name: str = export_field(
        Field(default="database_name", description="Name of the database"),
        display_name="Database Name Field",
        override_type="data_fields",
        required=False,
    )
    question: str = export_field(
        Field(default="question", description="SQL question"),
        display_name="SQL Question Field",
        override_type="data_fields",
        required=False,
    )
    business_info: str = export_field(
        Field(default="business_info", description="Business info"),
        display_name="Business Info Field",
        override_type="data_fields",
        required=False,
    )
    schema_descriptions: str = export_field(
        Field(default="schema_descriptions", description="Schema descriptions"),
        display_name="Schema Descriptions Field",
        override_type="data_fields",
        required=False,
    )
    diff_queries: str = export_field(
        Field(default="diff_queries", description="Database diff queries"),
        display_name="Database Diff Queries Field",
        override_type="data_fields",
        required=False,
    )
    golden_sql: str = export_field(
        Field(default="golden_sql", description="SQL ground truth query"),
        display_name="SQL Ground Truth Query Field",
        override_type="data_fields",
        required=False,
    )
    golden_output: str = export_field(
        Field(default="golden_output", description="SQL ground truth output"),
        display_name="SQL Expected Output Field",
        override_type="data_fields",
        required=False,
    )

    @property
    def env(self) -> Environment:
        return load_environment("genai.system.agentic_autoreviewer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.task_type == HILBenchMode.SWE.value:
            self.mode = HILBenchMode.SWE
        elif self.task_type == HILBenchMode.SQL.value:
            self.mode = HILBenchMode.SQL
        else:
            raise ValueError(f"Invalid task type: {self.task_type}")
        assert len(self.model_names) > 0, "no models specified"
        litellm_base_url = (
            PUBLIC_LITELLM_BASE_URL
            if self.api_key_name == "HIL_BENCH_PUBLIC_2"
            else LITELLM_BASE_URL
        )
        self.agent_llm = ChainedLLM(
            model_name="gpt-5-mini",
            api_key=get_from_env_or_secrets(self.api_key_name),
            base_url=litellm_base_url,
        )
        self.agent_llm_max_response_tokens = 16384

    def build_config(self) -> HILBenchConfig:
        """Build HILBenchConfig from agent attributes."""
        # Build sandbox config if provided
        sandbox_cfg = None
        if self.use_sandbox and self.sandbox_config:
            sandbox_cfg = SandboxConfig(**self.sandbox_config)
        elif self.use_sandbox:
            sandbox_cfg = SandboxConfig()

        return HILBenchConfig(
            mode=self.mode,
            model_names=self.model_names,
            run_name=self.run_name,
            output_dir=self.output_dir,
            num_workers=self.num_workers,
            num_passes=self.num_passes,
            ask_human=self.ask_human,
            with_blockers=False,  # doesn't matter the value here; will be overridden in main_act for each mode
            all_modes=self.all_modes,
            cleanup_docker=self.cleanup_docker,
            swe_baseline_per_instance_cost_limit=self.swe_baseline_per_instance_cost_limit,
            swe_baseline_max_steps=self.swe_baseline_max_steps,
            swe_with_blockers_per_instance_cost_limit=self.swe_with_blockers_per_instance_cost_limit,
            swe_with_blockers_max_steps=self.swe_with_blockers_max_steps,
            sql_baseline_per_instance_cost_limit=self.sql_baseline_per_instance_cost_limit,
            sql_baseline_max_steps=self.sql_baseline_max_steps,
            sql_with_blockers_per_instance_cost_limit=self.sql_with_blockers_per_instance_cost_limit,
            sql_with_blockers_max_steps=self.sql_with_blockers_max_steps,
            max_runtime=self.max_runtime,
            enable_model_call_logging=self.enable_model_call_logging,
            no_redo=self.no_redo,
            cleanup_trajectories=self.cleanup_trajectories,
            generate_instances=self.generate_instances,
            dataset=self.dataset,
            use_sandbox=self.use_sandbox,
            sandbox_config=sandbox_cfg,
        )

    def _setup_swe_task(
        self,
        input: AgentInput,
        config: HILBenchConfig,
        ctx: TaskSetupContext,
        target_image_name: str | None = None,
        skip_agent_image_build: bool = False,
    ) -> HILBenchConfig:
        """
        Set up SWE task environment. Creates task directory structure:
            task_dir/
                problem_statement.txt
                blocker_registry.json
                metadata.json
                repo/
        Returns updated HILBenchConfig with path set to task directory
        """
        logger.info("Setting up SWE task environment")
        data = input.data

        # Extract required fields
        logger.info("Extracting data fields from attempt and other instance artifacts")
        language = get_rtd_field(data, "language")
        if language is None:
            raise ValueError("language is required for SWE mode")
        else:
            language = language.lower()

        repo_name = get_rtd_field(data, "repo_name")
        if repo_name is None:
            raise ValueError("repo_name is required for SWE mode")
        else:
            owner, repo = repo_name.split("/")

        base_commit = get_rtd_field(data, "base_commit")
        if base_commit is None:
            raise ValueError("base_commit is required for SWE mode")

        problem_statement = get_rtd_field(data, "problem_statement")
        if problem_statement is None:
            raise ValueError("problem_statement is required for SWE mode")

        problem_requirements = get_rtd_field(data, "problem_requirements")
        if problem_requirements is None:
            raise ValueError("problem_requirements is required for SWE mode")

        problem_interfaces = get_rtd_field(data, "problem_interfaces")
        if problem_interfaces is None:
            raise ValueError("problem_interfaces is required for SWE mode")

        combined_problem_statement = f"# PROBLEM STATEMENT\n{problem_statement}\n\n\n# REQUIREMENTS\n{problem_requirements}\n\n\n# PUBLIC INTERFACES\n{problem_interfaces}"

        blocker_registry = get_rtd_field(data, "blocker_registry")
        if blocker_registry is None:
            raise ValueError("blocker_registry is required for SWE mode")
        else:
            if not isinstance(blocker_registry, list):
                try:
                    blocker_registry = json.loads(blocker_registry)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid blocker_registry JSON: {e}")
            blocker_registry = {"blockers": blocker_registry}

        setup_patch = get_raw_rtd_field(data, "setup_patch")
        if setup_patch is None:
            setup_patch = ""  # Allow empty setup_patch
        else:
            setup_patch = _read_file_from_url(setup_patch)

        test_patch = get_raw_rtd_field(data, "test_patch")
        if test_patch is None:
            raise ValueError("test_patch is required for SWE mode")
        elif test_patch.startswith("http"):  # upload URL, not raw git diff
            test_patch = _read_file_from_url(test_patch)

        golden_patch = get_raw_rtd_field(data, "golden_patch")
        if golden_patch is None:
            raise ValueError("golden_patch is required for SWE mode")
        elif golden_patch.startswith("http"):  # upload URL, not raw git diff
            golden_patch = _read_file_from_url(golden_patch)

        instance_id = get_rtd_field(data, "instance_id")
        if instance_id is None:
            raise ValueError("instance_id is required for SWE mode")
        else:
            # Construct docker image name from instance_id. Images are stored as tags in the jefzda/sweap-images repository
            tag_name = instance_id.removeprefix("instance_")
            # Special case: some instances with the -vnan suffix have images built without the suffix
            if instance_id.endswith("-vnan") and not instance_id.endswith(
                "ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan"
            ):
                tag_name = tag_name[:-5]  # Remove -vnan suffix
            # Keep the canonical non-truncated tag here; pull-time logic will try
            # non-truncated first, then a 128-char truncated fallback if needed.
            full_tag = f"{owner.lower()}.{repo.lower()}-{tag_name}"
            docker_image_name = f"jefzda/sweap-images:{full_tag}"
        sweap_source = get_rtd_field(data, "_sweap_source") or "external"
        internal_sweap = sweap_source == "internal"
        internal_dockerfile = None
        internal_repo_url = None
        if internal_sweap:
            docker_image_name = get_rtd_field(data, "docker_image_name") or docker_image_name
            internal_repo_url = get_raw_rtd_field(data, "repo_url")
            if internal_repo_url is None:
                raise ValueError("repo_url is required for internal SWEAP mode")
            dockerfile_url = get_raw_rtd_field(data, "dockerfile")
            if dockerfile_url is None:
                raise ValueError("dockerfile is required for internal SWEAP mode")
            internal_dockerfile = _read_file_from_url(dockerfile_url)

        tests_to_pass = get_rtd_field(data, "tests_to_pass")
        if tests_to_pass is None or len(tests_to_pass) == 0:
            raise ValueError("non-empty tests_to_pass is required for SWE mode")
        else:
            if not isinstance(tests_to_pass, list):
                try:
                    tests_to_pass = json.loads(tests_to_pass) if tests_to_pass else []
                except json.JSONDecodeError as e:
                    raise ValueError(f"Invalid tests_to_pass JSON: {e}")
            # Downstream processing expects FAIL_TO_PASS and PASS_TO_PASS separately, so we put all tests_to_pass into fail_to_pass and leave pass_to_pass empty
            fail_to_pass = tests_to_pass
            pass_to_pass = []

        test_files = get_rtd_field(data, "test_files")

        run_script = get_raw_rtd_field(data, "run_script")
        if run_script is None:
            raise ValueError("run_script is required for SWE mode")
        else:
            run_script = _read_file_from_url(run_script)

        if internal_sweap:
            parser_script_url = get_raw_rtd_field(data, "parser_script")
            if parser_script_url is None:
                raise ValueError("parser_script is required for internal SWEAP mode")
            parser_script = _read_file_from_url(parser_script_url)
        else:
            parser_script = _download_sweap_parser(instance_id)
            if parser_script is None:
                raise ValueError(
                    f"parser.py is required for SWE mode but could not be downloaded for {instance_id}"
                )
        logger.info("Done extracting data and pulling instance artifacts")

        # Set language enum
        if language in _PYTHON_LANGUAGES:
            plang = ProgrammingLanguage.PYTHON
        elif language in _GO_LANGUAGES:
            plang = ProgrammingLanguage.GO
        elif language in _JS_TS_LANGUAGES:
            plang = ProgrammingLanguage.JAVASCRIPT
        elif language in _JAVA_LANGUAGES:
            plang = ProgrammingLanguage.JAVA
        elif language in _RUST_LANGUAGES:
            plang = ProgrammingLanguage.RUST
        elif language in _CPP_LANGUAGES:
            plang = ProgrammingLanguage.CPP
        else:
            raise ValueError(
                f"Unsupported language: {language}. Supported languages: {', '.join(_SUPPORTED_SWE_LANGUAGES)}"
            )

        # Set test_cmd and log_parser for SWEAP instances
        # test_cmd is a sequential combined command of SWEAP's run_script.sh and parser.py plus a Python line to print out the results JSON with markers for agent parsing
        # CRITICAL: Scripts are at /root/ (NOT /app/) so agent cannot see them during problem-solving
        test_cmd = (
            "bash /root/run_script.sh > /tmp/stdout.log 2> /tmp/stderr.log; "
            "python /root/parser.py /tmp/stdout.log /tmp/stderr.log /tmp/output.json; "
            "python -c \"print('SWEAP_JSON_START'); print(open('/tmp/output.json').read()); print('SWEAP_JSON_END')\""
        )
        log_parser = "sweap_json"  # placeholder for our python snippet
        logger.info(f"Using language = {plang.value}, test_cmd = SWEAP, log_parser = {log_parser}")

        # Create task directory
        logger.info("Creating temp task directory")
        task_dir = Path(tempfile.mkdtemp())
        ctx.add_tmp_dir(str(task_dir))
        ctx.task_dir = task_dir
        logger.info(f"Created temp task directory at {task_dir}")

        # If local: extract repo from Docker and set up the initial state. If on sandbox, defer it to there
        if config.use_sandbox:
            logger.info("Writing repo info and non-repo files to continue setup in VM")
            # Store info in context for VM execution
            ctx.docker_image_name = docker_image_name
            ctx.base_commit = base_commit
            ctx.setup_patch = setup_patch
            ctx.golden_patch = golden_patch
            ctx.instance_id = instance_id
            ctx.fail_to_pass = fail_to_pass
            ctx.test_patch = test_patch
            ctx.language = language
            ctx.internal_dockerfile = internal_dockerfile
            ctx.internal_repo_url = internal_repo_url

            # Write files that don't depend on repo
            problem_statement_file = task_dir / "problem_statement.txt"
            problem_statement_file.write_text(combined_problem_statement)
            blocker_registry_file = task_dir / "blocker_registry.json"
            blocker_registry_file.write_text(json.dumps(blocker_registry, indent=2))
            run_script_path = task_dir / "run_script.sh"
            run_script_path.write_text(run_script)
            parser_script_path = task_dir / "parser.py"
            parser_script_path.write_text(parser_script)
            if internal_dockerfile is not None:
                (task_dir / "Dockerfile.internal").write_text(internal_dockerfile)
                (task_dir / "install_internal_sweap_test_runners.sh").write_text(
                    _internal_test_runner_install_script(language)
                )
            llm_extracted_functions = []
            if test_patch:
                logger.info("[Sandbox] Using LLM to extract test function names from test_patch")
                llm_extracted_functions = self._extract_test_functions_from_patch(
                    test_patch, language, input.data.project_id
                )
                logger.info(
                    f"[Sandbox] LLM extracted {len(llm_extracted_functions)} test functions: {llm_extracted_functions}"
                )
            ctx.llm_extracted_functions = llm_extracted_functions

            metadata = {
                "instance_id": instance_id,
                "repo_name": "app",  # "app" triggers PreExistingRepoConfig
                "base_commit": "HEAD",  # will be correctly HEAD after rest of setup in VM
                "image_name": docker_image_name,  # will be replaced with built image in VM
                "log_parser": log_parser,
                "language": plang.value,
                "blocker_stats": {
                    "total": len(blocker_registry.get("blockers", [])),
                    "missing_parameter": 0,
                    "ambiguous_requirement": 0,
                    "contradictory_requirement": 0,
                },
                "swe_bench_metadata": {
                    "FAIL_TO_PASS": fail_to_pass,
                    "PASS_TO_PASS": pass_to_pass,
                },
                "test_cmd": test_cmd,
                "test_patch": test_patch,  # raw patch - normalization happens in VM if needed
                "run_script_path": str(run_script_path),
                "parser_script_path": str(parser_script_path),
                "test_files": test_files,
                "llm_extracted_functions": llm_extracted_functions,
                "skip_validation": self.skip_validation,
            }
            metadata_file = task_dir / "metadata.json"
            metadata_file.write_text(json.dumps(metadata, indent=2))
            logger.info(
                "[Sandbox] Wrote necessary Git information and instance files to continue setup in VM"
            )

            # Defer input validation to VM
            ctx.validation_error = None
            logger.info(
                "[Sandbox] Finished setting up task environment, save for repo setup which will continue in VM"
            )
            return config.model_copy(update={"path": str(task_dir)})

        # NON-SANDBOX: Extract repo from Docker image and set up everything locally
        repo_dir = task_dir / "app"
        repo_dir.mkdir(parents=True, exist_ok=True)
        resolved_docker_image_name = docker_image_name
        try:
            if internal_sweap:
                if internal_dockerfile is None:
                    raise ValueError("Internal SWEAP Dockerfile was not loaded")
                if internal_repo_url is None:
                    raise ValueError("Internal SWEAP repo_url was not loaded")
                _build_internal_sweap_base_image(
                    internal_dockerfile, docker_image_name, internal_repo_url, base_commit, language
                )
            logger.info(f"Extracting repo from Docker image {docker_image_name}")
            resolved_docker_image_name = _extract_repo_from_docker_image(
                docker_image_name, str(repo_dir)
            )
            if resolved_docker_image_name != docker_image_name:
                logger.warning(
                    "Using resolved Docker image %s for downstream setup (primary=%s)",
                    resolved_docker_image_name,
                    docker_image_name,
                )
            logger.info("Extracted repo successfully; now checking out base commit")
            try:
                subprocess.run(  # checkout the base commit here so we can just start at "HEAD" when the SWE-agent runs
                    ["git", "checkout", base_commit],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.lower() if e.stderr else str(e).lower()
                if internal_sweap:
                    raise ValueError(
                        f"Internal SWEAP image {docker_image_name} does not contain base_commit "
                        f"'{base_commit}' in /app git state: {stderr}"
                    )
                if self.skip_validation:
                    raise RuntimeError(
                        f"Git checkout failed for base_commit '{base_commit}': {stderr}"
                    )
                ctx.validation_error = f"SWE INPUT VALIDATION FAILED: The base_commit '{base_commit}' does not exist in the repository extracted from Docker image. Please verify the commit hash is correct. Error: {stderr}"
                logger.error(
                    "Base commit does not exist in repo, early-returning to surface validation error"
                )
                return config
            logger.info(
                "Checked out base commit successfully; now configuring git user for committing"
            )
            subprocess.run(
                ["git", "config", "user.email", "PLACEHOLDER_EMAIL"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "PLACEHOLDER_USER"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("Configured git user successfully")

            # Apply setup_patch if provided
            if len(setup_patch.strip()) > 0:
                logger.info(f"Setup patch:\n{setup_patch}")
                logger.info("Applying setup_patch to repository")
                setup_patch = _normalize_patch_line_endings(setup_patch, Path(repo_dir))
                patch_file = Path(repo_dir) / "_setup_patch.diff"
                patch_file.write_text(setup_patch)
                try:
                    subprocess.run(
                        ["git", "apply", "-v", str(patch_file)],
                        cwd=repo_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    logger.info("Applied setup_patch successfully; now staging it")
                except subprocess.CalledProcessError as e:
                    error_msg = e.stderr or str(e)
                    patch_file.unlink(missing_ok=True)
                    if self.skip_validation:
                        raise RuntimeError(f"setup_patch failed to apply: {error_msg}")
                    ctx.validation_error = f"SWE INPUT VALIDATION FAILED: setup_patch failed to apply. Potential causes:\n1. Line ending mismatches (CRLF vs LF)\n2. Patch generated against a different version of the file\n3. Context lines don't match the actual file content\n\nError details: {error_msg}"
                    logger.error(
                        "Setup patch application failed, early-returning to surface validation error"
                    )
                    return config
                finally:
                    patch_file.unlink(missing_ok=True)
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logger.info("Staged setup patch successfully; now committing it")
                subprocess.run(
                    ["git", "commit", "--no-verify", "--allow-empty", "-m", "Apply setup patch"],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                logger.info("Committed setup patch successfully")

            # Verify test files exist in repo OR will be added by test_patch
            def extract_test_file_path(test_name: str, lang: ProgrammingLanguage) -> str | None:
                """Extract file path from test name. Preserves original path format."""
                return _extract_test_file_path_for_language(test_name, lang.value)

            def is_go_function_name(test_name: str) -> bool:
                """Check if test_name looks like a Go test/benchmark/example/fuzz function name."""
                return _is_go_function_name(test_name)

            def extract_go_functions_from_patch(patch_content: str) -> set[str]:
                """Extract Go test/benchmark/example/fuzz function names from patch content."""
                # Match: func TestXxx( or func (r *Receiver) TestXxx(
                # Also match Benchmark, Example, Fuzz prefixes
                pattern = r"^\+\s*func\s+(?:\([^)]+\)\s+)?((Test|Benchmark|Example|Fuzz)[A-Za-z0-9_]*)\s*\("
                functions = set()
                for line in patch_content.split("\n"):
                    match = re.match(pattern, line)
                    if match:
                        functions.add(match.group(1))
                return functions

            def check_go_function_in_patch(test_name: str, patch_content: str) -> bool:
                """
                Check if a Go test function will be added by test_patch.
                """
                # Check for "func TestName(" pattern in patch
                if f"func {test_name}(" in patch_content:
                    return True
                # Also check with receiver: "func (r *Type) TestName("
                if re.search(rf"func\s+\([^)]+\)\s+{re.escape(test_name)}\s*\(", patch_content):
                    return True
                return False

            # Cache for Go test functions in repo (scanned once, reused for all checks)
            _go_repo_functions_cache: set[str] | None = None

            def get_go_functions_in_repo(repo_dir_path: Path) -> set[str]:
                """
                Scan all *_test.go files in repo and extract test/benchmark/example/fuzz function names.
                Results are cached for efficiency on large repos.
                """
                nonlocal _go_repo_functions_cache
                if _go_repo_functions_cache is not None:
                    return _go_repo_functions_cache

                _go_repo_functions_cache = set()
                pattern = re.compile(
                    r"^func\s+(?:\([^)]+\)\s+)?((Test|Benchmark|Example|Fuzz)[A-Za-z0-9_]*)\s*\("
                )
                try:
                    for test_file in repo_dir_path.rglob("*_test.go"):
                        try:
                            content = test_file.read_text(errors="replace")
                            for line in content.split("\n"):
                                match = pattern.match(line.strip())
                                if match:
                                    _go_repo_functions_cache.add(match.group(1))
                        except Exception:
                            pass  # Skip files that can't be read
                except Exception:
                    pass  # rglob can fail on permission issues
                return _go_repo_functions_cache

            def check_go_function_exists(
                test_name: str, patch_content: str, repo_dir_path: Path
            ) -> bool:
                """
                Check if a Go test function exists in test_patch OR repo.
                Checks patch first (cheap), then repo (cached) for efficiency.
                """
                # Strategy 1: Check test_patch first (cheap string search)
                if check_go_function_in_patch(test_name, patch_content):
                    return True
                # Strategy 2: Check repo (scanned and cached)
                return test_name in get_go_functions_in_repo(repo_dir_path)

            _java_repo_identifiers_cache: set[str] | None = None

            def get_java_identifiers_in_repo(repo_dir_path: Path) -> set[str]:
                """Scan Java test files for class/method identifiers used by Maven/Gradle/JUnit."""
                nonlocal _java_repo_identifiers_cache
                if _java_repo_identifiers_cache is not None:
                    return _java_repo_identifiers_cache
                _java_repo_identifiers_cache = set()
                class_pattern = re.compile(
                    r"^\s*(?:public\s+)?(?:class|interface|enum)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
                )
                method_pattern = re.compile(
                    r"^\s*(?:public|protected|private)?\s*(?:static\s+)?(?:[\w<>\[\],.?]+\s+)+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
                )
                for test_file in repo_dir_path.rglob("*.java"):
                    rel_path = str(test_file.relative_to(repo_dir_path))
                    if not _is_test_file_for_language(rel_path, ProgrammingLanguage.JAVA.value):
                        continue
                    try:
                        content = test_file.read_text(errors="replace")
                    except Exception:
                        continue
                    current_class = test_file.stem
                    _java_repo_identifiers_cache.add(current_class)
                    pending_test_annotation = False
                    for line in content.split("\n"):
                        class_match = class_pattern.match(line)
                        if class_match:
                            current_class = class_match.group(1)
                            _java_repo_identifiers_cache.add(current_class)
                            continue
                        if line.strip().startswith("@Test"):
                            pending_test_annotation = True
                            continue
                        method_match = method_pattern.match(line)
                        if method_match:
                            method_name = method_match.group(1)
                            if pending_test_annotation or method_name.startswith("test"):
                                _java_repo_identifiers_cache.add(method_name)
                                _java_repo_identifiers_cache.add(f"{current_class}#{method_name}")
                                _java_repo_identifiers_cache.add(f"{current_class}::{method_name}")
                            pending_test_annotation = False
                return _java_repo_identifiers_cache

            def check_java_identifier_exists(
                test_name: str, patch_content: str, repo_dir_path: Path
            ) -> bool:
                patch_identifiers = _extract_java_test_identifiers_from_patch(patch_content)
                if any(_java_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
                    return True
                return any(
                    _java_identifier_matches(test_name, identifier)
                    for identifier in get_java_identifiers_in_repo(repo_dir_path)
                )

            _rust_repo_identifiers_cache: set[str] | None = None

            def get_rust_identifiers_in_repo(repo_dir_path: Path) -> set[str]:
                """Scan Rust test files/source modules for #[test]-style functions."""
                nonlocal _rust_repo_identifiers_cache
                if _rust_repo_identifiers_cache is not None:
                    return _rust_repo_identifiers_cache
                _rust_repo_identifiers_cache = set()
                attr_pattern = re.compile(r"^\s*#\[[^\]]*test[^\]]*\]")
                fn_pattern = re.compile(
                    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\("
                )
                try:
                    rust_files = list(repo_dir_path.rglob("*.rs"))
                except Exception:
                    rust_files = []
                for test_file in rust_files:
                    rel_path = str(test_file.relative_to(repo_dir_path))
                    try:
                        content = test_file.read_text(errors="replace")
                    except Exception:
                        continue
                    if not _is_test_file_for_language(
                        rel_path, ProgrammingLanguage.RUST.value
                    ) and "#[test" not in content:
                        continue
                    pending_test_attr = False
                    for line in content.split("\n"):
                        if attr_pattern.match(line):
                            pending_test_attr = True
                            continue
                        fn_match = fn_pattern.match(line)
                        if fn_match:
                            fn_name = fn_match.group(1)
                            if pending_test_attr or fn_name.startswith("test_"):
                                _rust_repo_identifiers_cache.add(fn_name)
                            pending_test_attr = False
                return _rust_repo_identifiers_cache

            def check_rust_identifier_exists(
                test_name: str, patch_content: str, repo_dir_path: Path
            ) -> bool:
                patch_identifiers = _extract_rust_test_identifiers_from_patch(patch_content)
                if any(_rust_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
                    return True
                return any(
                    _rust_identifier_matches(test_name, identifier)
                    for identifier in get_rust_identifiers_in_repo(repo_dir_path)
                )

            _cpp_repo_identifiers_cache: set[str] | None = None

            def get_cpp_identifiers_in_repo(repo_dir_path: Path) -> set[str]:
                """Scan C/C++ test files for common test macro identifiers."""
                nonlocal _cpp_repo_identifiers_cache
                if _cpp_repo_identifiers_cache is not None:
                    return _cpp_repo_identifiers_cache
                _cpp_repo_identifiers_cache = set()
                macro_pattern = re.compile(
                    r"(?:TYPED_TEST|TEST_P|TEST_F|TEST)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
                )
                try:
                    cpp_files = [
                        path
                        for path in repo_dir_path.rglob("*")
                        if path.is_file() and any(path.name.endswith(ext) for ext in _CPP_EXTENSIONS)
                    ]
                except Exception:
                    cpp_files = []
                for test_file in cpp_files:
                    rel_path = str(test_file.relative_to(repo_dir_path))
                    if not _is_test_file_for_language(rel_path, ProgrammingLanguage.CPP.value):
                        continue
                    try:
                        content = test_file.read_text(errors="replace")
                    except Exception:
                        continue
                    for suite, test in macro_pattern.findall(content):
                        _cpp_repo_identifiers_cache.add(test)
                        _cpp_repo_identifiers_cache.add(f"{suite}.{test}")
                        _cpp_repo_identifiers_cache.add(f"{suite}::{test}")
                        _cpp_repo_identifiers_cache.add(f"{suite}/{test}")
                return _cpp_repo_identifiers_cache

            def check_cpp_identifier_exists(
                test_name: str, patch_content: str, repo_dir_path: Path
            ) -> bool:
                patch_identifiers = _extract_cpp_test_identifiers_from_patch(patch_content)
                if any(_cpp_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
                    return True
                return any(
                    _cpp_identifier_matches(test_name, identifier)
                    for identifier in get_cpp_identifiers_in_repo(repo_dir_path)
                )

            def extract_files_from_patch(patch_content: str) -> set[str]:
                files = set()
                for line in patch_content.split("\n"):
                    # Match lines like: +++ b/path/to/file.py
                    if line.startswith("+++ b/"):
                        files.add(line[6:])  # remove "+++ b/" prefix
                    # Match lines like: diff --git a/path/to/file.py b/path/to/file.py
                    elif line.startswith("diff --git"):
                        parts = line.split()
                        if len(parts) >= 4:
                            files.add(parts[3][2:])  # remove "b/" prefix
                return files

            def _path_matches_suffix(full_path: str, suffix_path: str) -> bool:
                """Check if full_path ends with suffix_path as a proper path suffix."""
                # Normalize both paths (strip leading slashes for comparison)
                full_normalized = full_path.lstrip("/")
                suffix_normalized = suffix_path.lstrip("/")
                if full_normalized == suffix_normalized:
                    return True
                # Ensure we match on directory boundary (not partial filename match)
                return full_normalized.endswith("/" + suffix_normalized)

            def check_test_file_exists(
                repo_dir_path: Path, test_file: str, patch_files: set[str]
            ) -> bool:
                """
                Check if a test file exists, handling various path formats:
                1. Relative paths (test/foo.py)
                2. Absolute paths (/app/test/foo.py)
                3. Workspace-relative paths in monorepos (src/foo.test.ts -> apps/drive/src/foo.test.ts)
                """
                # Normalize: strip leading slashes for relative path checks
                normalized_path = test_file.lstrip("/")

                # === Strategy 1: Check in test_patch_files (exact match) ===
                if test_file in patch_files or normalized_path in patch_files:
                    return True

                # === Strategy 2: Check in test_patch_files (suffix match for monorepos) ===
                for patch_file in patch_files:
                    if _path_matches_suffix(patch_file, normalized_path):
                        return True

                # === Strategy 3: Check as relative path from repo_dir ===
                if (repo_dir_path / normalized_path).exists():
                    return True

                # === Strategy 4: Check as absolute path (if starts with /) ===
                if test_file.startswith("/") and Path(test_file).exists():
                    return True

                # === Strategy 5: Suffix match in repository (for monorepos) ===
                # Find files in repo that end with the normalized path
                # Use rglob with the filename, then check if full path matches suffix
                filename = Path(normalized_path).name
                try:
                    for found_path in repo_dir_path.rglob(filename):
                        rel_path = str(found_path.relative_to(repo_dir_path))
                        if _path_matches_suffix(rel_path, normalized_path):
                            return True
                except Exception:
                    pass  # rglob can fail on permission issues, etc.

                return False

            if self.skip_validation:
                logger.info("Skipping all input validation checks")
            else:
                logger.info(f"Tests to pass:\n{tests_to_pass}")
                logger.info(f"Test patch:\n{test_patch}")
                logger.info(
                    "Verifying that test files already exist in the repo or will be added by the test_patch"
                )
                test_patch_files = extract_files_from_patch(test_patch) if test_patch else set()
                files_to_validate = []
                go_functions_to_validate = []
                java_identifiers_to_validate = []
                rust_identifiers_to_validate = []
                cpp_identifiers_to_validate = []
                for test_name in tests_to_pass:
                    test_file = extract_test_file_path(test_name, plang)
                    if test_file is not None:
                        files_to_validate.append(test_file)
                    elif plang == ProgrammingLanguage.GO and is_go_function_name(test_name):
                        # Go function-only name (e.g., "TestFoo") - validate separately
                        go_functions_to_validate.append(test_name)
                    elif plang == ProgrammingLanguage.JAVA and _is_java_test_identifier(test_name):
                        java_identifiers_to_validate.append(test_name)
                    elif plang == ProgrammingLanguage.RUST and _is_rust_test_identifier(test_name):
                        # Rust/SWEAP identifiers are often generated by macro/snapshot frameworks
                        # and do not necessarily correspond to a literal #[test] function name.
                        # Let the language runner/parser validate these at execution time.
                        continue
                    elif plang == ProgrammingLanguage.CPP and _is_cpp_test_identifier(test_name):
                        cpp_identifiers_to_validate.append(test_name)

                missing_files = []
                repo_dir_path = Path(repo_dir)
                for test_file in files_to_validate:
                    if not check_test_file_exists(repo_dir_path, test_file, test_patch_files):
                        missing_files.append(test_file)

                # Validate Go function names exist in test_patch OR repo
                missing_go_functions = []
                for func_name in go_functions_to_validate:
                    if not check_go_function_exists(func_name, test_patch, repo_dir_path):
                        missing_go_functions.append(func_name)

                if missing_files or missing_go_functions:
                    all_missing = missing_files + missing_go_functions
                    ctx.validation_error = f"SWE INPUT VALIDATION FAILED: The following tests do not exist in the repository or test_patch: {all_missing}. Please verify the test paths/names are correct."
                    logger.error(
                        "Detected missing test files/functions, early-returning to surface validation error"
                    )
                    return config
                logger.info(
                    f"Verified all {len(files_to_validate)} test files, {len(go_functions_to_validate)} Go functions, "
                    f"{len(java_identifiers_to_validate)} Java identifiers, {len(rust_identifiers_to_validate)} Rust identifiers, "
                    f"and {len(cpp_identifiers_to_validate)} C++ identifiers exist in repository or test_patch"
                )

            def is_test_file(filename: str, lang: ProgrammingLanguage) -> bool:
                return _is_test_file_for_language(filename, lang.value)

            def check_patch_file_in_tests_to_pass(
                patch_file: str, tests: list[str], lang: ProgrammingLanguage
            ) -> bool:
                patch_normalized = patch_file.lstrip("/")

                # For Go, skip file-level check for _test.go files
                # Go test coverage is validated via function names separately
                if lang == ProgrammingLanguage.GO and patch_normalized.endswith("_test.go"):
                    return True  # Will be validated via function check
                if lang in (
                    ProgrammingLanguage.JAVA,
                    ProgrammingLanguage.RUST,
                    ProgrammingLanguage.CPP,
                ):
                    return True  # Will be validated via language-specific identifier checks

                for test_name in tests:
                    test_file = extract_test_file_path(test_name, lang)
                    if test_file is None:
                        continue
                    test_normalized = test_file.lstrip("/")
                    # Exact match
                    if patch_normalized == test_normalized:
                        return True
                    # Suffix match (monorepo: patch has full path, test has short path)
                    if _path_matches_suffix(patch_normalized, test_normalized):
                        return True
                    # Reverse suffix match (test has full path, patch has short path)
                    if _path_matches_suffix(test_normalized, patch_normalized):
                        return True
                return False

            def check_go_function_in_tests_to_pass(func_name: str, tests: list[str]) -> bool:
                for test_name in tests:
                    # Exact match
                    if func_name == test_name:
                        return True
                    # test_name might be "TestFoo/SubTest" - func_name is the parent test
                    if test_name.startswith(func_name + "/"):
                        return True
                return False

            def check_language_identifier_in_tests_to_pass(
                identifier: str, tests: list[str], lang: ProgrammingLanguage
            ) -> bool:
                for test_name in tests:
                    if lang == ProgrammingLanguage.JAVA and _java_identifier_matches(
                        test_name, identifier
                    ):
                        return True
                    if lang == ProgrammingLanguage.RUST and _rust_identifier_matches(
                        test_name, identifier
                    ):
                        return True
                    if lang == ProgrammingLanguage.CPP and _cpp_identifier_matches(
                        test_name, identifier
                    ):
                        return True
                return False

            if not self.skip_validation:
                logger.info("Verifying that test files in test_patch are covered by tests_to_pass")
                test_patch_files = extract_files_from_patch(test_patch) if test_patch else set()
                # Filter to only check actual test files (not fixtures, configs, etc.)
                test_files_in_patch = [f for f in test_patch_files if is_test_file(f, plang)]
                non_test_files = len(test_patch_files) - len(test_files_in_patch)
                if non_test_files > 0:
                    logger.info(
                        f"Skipping {non_test_files} non-test files in test_patch (fixtures, configs, etc.)"
                    )

                uncovered_patch_files = []
                for patch_file in test_files_in_patch:
                    if not check_patch_file_in_tests_to_pass(patch_file, tests_to_pass, plang):
                        uncovered_patch_files.append(patch_file)

                # For Go, also check that functions added in test_patch are in tests_to_pass
                uncovered_go_functions = []
                if plang == ProgrammingLanguage.GO and test_patch:
                    patch_go_functions = extract_go_functions_from_patch(test_patch)
                    for func_name in patch_go_functions:
                        if not check_go_function_in_tests_to_pass(func_name, tests_to_pass):
                            uncovered_go_functions.append(func_name)

                uncovered_language_identifiers = []
                if test_patch and plang in (
                    ProgrammingLanguage.JAVA,
                    ProgrammingLanguage.RUST,
                    ProgrammingLanguage.CPP,
                ):
                    if plang == ProgrammingLanguage.JAVA:
                        patch_language_identifiers = _extract_java_test_identifiers_from_patch(
                            test_patch
                        )
                    elif plang == ProgrammingLanguage.RUST:
                        patch_language_identifiers = _extract_rust_test_identifiers_from_patch(
                            test_patch
                        )
                    else:
                        patch_language_identifiers = _extract_cpp_test_identifiers_from_patch(
                            test_patch
                        )
                    for identifier in sorted(patch_language_identifiers):
                        if not check_language_identifier_in_tests_to_pass(
                            identifier, tests_to_pass, plang
                        ):
                            uncovered_language_identifiers.append(identifier)

                uncovered_llm_functions = []
                if test_patch:
                    logger.info("Using LLM to extract test function names from test_patch")
                    llm_extracted_functions = self._extract_test_functions_from_patch(
                        test_patch, language, input.data.project_id
                    )
                    logger.info(
                        f"LLM extracted {len(llm_extracted_functions)} test functions: {llm_extracted_functions}"
                    )
                else:
                    llm_extracted_functions = []
                logger.info(f"LLM extracted functions:\n{llm_extracted_functions}")

                def is_file_level_entry(test_name: str) -> bool:
                    """
                    Check if a tests_to_pass entry is file-level (covers all functions in the file).

                    File-level entries don't specify a specific function:
                    - Python: no '::' (e.g., 'tests/test_auth.py')
                    - JS/TS: no '|' (e.g., 'test/auth.test.js')
                    - Go: ends with '_test.go' (e.g., 'pkg/auth_test.go')
                    """
                    # Python: file-level if no :: and ends with .py
                    if test_name.endswith(".py") and "::" not in test_name:
                        return True
                    # JS/TS: file-level if no | and ends with test file extension
                    js_ts_extensions = (
                        ".test.js",
                        ".test.ts",
                        ".test.jsx",
                        ".test.tsx",
                        ".spec.js",
                        ".spec.ts",
                        ".spec.jsx",
                        ".spec.tsx",
                    )
                    if (
                        any(test_name.endswith(ext) for ext in js_ts_extensions)
                        and "|" not in test_name
                    ):
                        return True
                    # Go: file-level if ends with _test.go
                    if test_name.endswith("_test.go"):
                        return True
                    # Java: file-level if a Java test file path is provided
                    if _is_test_file_for_language(test_name, ProgrammingLanguage.JAVA.value):
                        return True
                    if _is_test_file_for_language(test_name, ProgrammingLanguage.RUST.value):
                        return True
                    if _is_test_file_for_language(test_name, ProgrammingLanguage.CPP.value):
                        return True
                    return False

                def has_full_file_level_coverage(tests: list[str], patch_files: list[str]) -> bool:
                    """
                    Check if ALL files in test_patch are covered by file-level entries in tests_to_pass.

                    Only returns True if EVERY file in patch_files has a matching file-level entry.
                    If any patch file is not covered, returns False (function-level checking needed).
                    """
                    if not patch_files:
                        return False

                    # Get all file-level entries from tests_to_pass
                    file_level_tests = [t for t in tests if is_file_level_entry(t)]
                    if not file_level_tests:
                        return False

                    # Check that EVERY patch file is covered by a file-level entry
                    for patch_file in patch_files:
                        file_is_covered = False
                        for test_name in file_level_tests:
                            if _path_matches_suffix(test_name, patch_file) or _path_matches_suffix(
                                patch_file, test_name
                            ):
                                file_is_covered = True
                                break
                        if not file_is_covered:
                            return False

                    return True

                def check_function_in_tests_to_pass(func_name: str, tests: list[str]) -> bool:
                    """
                    Check if an LLM-extracted function name is covered by tests_to_pass.

                    This function uses EXACT matching (not substring) to avoid false positives.
                    For example, 'test_user' should NOT match 'test_user_authentication'.
                    """
                    # Extract base function name for parameterized tests (e.g., test_foo[param] -> test_foo)
                    func_base = func_name.split("[")[0] if "[" in func_name else func_name
                    func_base_lower = func_base.lower()

                    for test_name in tests:
                        # === JS/TS: Handle pipe-separated format FIRST ===
                        # Check | before :: because JS descriptions can contain ::
                        # but pytest paths would never contain |
                        # file.test.js | describe | test name -> extract "test name" (last part)
                        if "|" in test_name:
                            # Get the last description part after all pipes
                            test_desc = test_name.split("|")[-1].strip()
                            test_desc_lower = test_desc.lower()
                            # EXACT match on description (not substring)
                            if func_base == test_desc or func_base_lower == test_desc_lower:
                                return True
                            # Also check if func matches the full description chain
                            full_desc = " ".join(part.strip() for part in test_name.split("|")[1:])
                            full_desc_lower = full_desc.lower()
                            if func_base == full_desc or func_base_lower == full_desc_lower:
                                return True
                            # Check if test_desc ends with the func_base (for Mocha-style where
                            # describe chains are concatenated with it() description, e.g.
                            # "describe1 describe2 it_name" should match func_base="it_name")
                            if test_desc_lower.endswith(
                                " " + func_base_lower
                            ) or full_desc_lower.endswith(" " + func_base_lower):
                                return True
                            continue  # Don't fall through to other checks for pipe format

                        # === PYTHON: Extract function name from pytest notation ===
                        # path/file.py::ClassName::method_name -> method_name
                        # path/file.py::test_func -> test_func
                        # path/file.py::test_func[param] -> test_func (base)
                        if "::" in test_name:
                            test_func = test_name.rsplit("::", 1)[-1]
                            test_func_base = (
                                test_func.split("[")[0] if "[" in test_func else test_func
                            )
                            test_func_base_lower = test_func_base.lower()
                            # EXACT match on function name (not substring)
                            if (
                                func_base == test_func_base
                                or func_base_lower == test_func_base_lower
                            ):
                                return True
                            if plang == ProgrammingLanguage.CPP and (
                                _cpp_identifier_matches(test_name, func_base)
                                or _cpp_identifier_matches(test_func_base, func_base)
                                or _cpp_pytest_wrapper_matches(test_name, func_base)
                            ):
                                return True
                            continue  # Don't fall through to other checks for pytest format

                        # === GO: Handle subtests with / separator ===
                        # TestFoo/SubTest -> parent is TestFoo
                        if "/" in test_name and not test_name.startswith("/"):
                            test_parent = test_name.split("/")[0]
                            test_parent_lower = test_parent.lower()
                            # func_name matches the parent test
                            if func_base == test_parent or func_base_lower == test_parent_lower:
                                return True
                        if "/" in func_name:
                            func_parent = func_name.split("/")[0]
                            func_parent_lower = func_parent.lower()
                            # test_name matches the parent of func_name
                            test_base = test_name.split("[")[0] if "[" in test_name else test_name
                            test_base_lower = test_base.lower()
                            if func_parent == test_base or func_parent_lower == test_base_lower:
                                return True

                        # === JAVA: Handle Class#method and Class::method formats ===
                        if plang == ProgrammingLanguage.JAVA and _java_identifier_matches(
                            test_name, func_base
                        ):
                            return True
                        if plang == ProgrammingLanguage.RUST and _rust_identifier_matches(
                            test_name, func_base
                        ):
                            return True
                        if plang == ProgrammingLanguage.CPP and _cpp_identifier_matches(
                            test_name, func_base
                        ):
                            return True
                        if plang == ProgrammingLanguage.CPP and _cpp_pytest_wrapper_matches(
                            test_name, func_base
                        ):
                            return True
                        if "#" in test_name or "::" in test_name:
                            java_method = test_name.replace("::", "#").rsplit("#", 1)[-1]
                            java_method_base = (
                                java_method.split("[")[0] if "[" in java_method else java_method
                            )
                            java_method_base_lower = java_method_base.lower()
                            if (
                                func_base == java_method_base
                                or func_base_lower == java_method_base_lower
                            ):
                                return True

                        # === EXACT match on the full test name or base ===
                        # This handles simple cases like test_name = "test_foo" or "TestBar"
                        test_base = test_name.split("[")[0] if "[" in test_name else test_name
                        test_base_lower = test_base.lower()
                        if func_base == test_base or func_base_lower == test_base_lower:
                            return True

                    return False

                # Only skip function-level checking if ALL files in test_patch are covered
                # by file-level entries in tests_to_pass. If any file is uncovered,
                # we must do function-level checking.
                if has_full_file_level_coverage(tests_to_pass, test_patch_files):
                    logger.info(
                        "Skipping function-level checking: ALL test_patch files are covered "
                        "by file-level entries in tests_to_pass"
                    )
                else:
                    for func_name in llm_extracted_functions:
                        if not check_function_in_tests_to_pass(func_name, tests_to_pass):
                            uncovered_llm_functions.append(func_name)

                    if uncovered_llm_functions:
                        logger.warning(
                            f"LLM detected {len(uncovered_llm_functions)} uncovered functions: {uncovered_llm_functions}"
                        )

                if (
                    uncovered_patch_files
                    or uncovered_go_functions
                    or uncovered_language_identifiers
                    or uncovered_llm_functions
                ):
                    all_uncovered = (
                        uncovered_patch_files
                        + uncovered_go_functions
                        + uncovered_language_identifiers
                        + uncovered_llm_functions
                    )
                    # Deduplicate while preserving order
                    seen = set()
                    unique_uncovered = []
                    for item in all_uncovered:
                        if item not in seen:
                            seen.add(item)
                            unique_uncovered.append(item)
                    ctx.validation_error = f"SWE INPUT VALIDATION FAILED: The following tests in test_patch are not covered by relevant tests: {unique_uncovered}. Add them to the relevant tests list or remove them from test_patch."
                    logger.error(
                        "Detected files/functions in test_patch that are not in tests_to_pass, early-returning to surface validation error"
                    )
                    return config
                logger.info(
                    f"Verified all {len(test_patch_files)} files, {len(extract_go_functions_from_patch(test_patch) if plang == ProgrammingLanguage.GO and test_patch else [])} Go functions, and {len(llm_extracted_functions) if test_patch else 0} LLM-extracted functions in test_patch are covered by tests_to_pass"
                )

        except subprocess.CalledProcessError as e:
            stderr = e.stderr if e.stderr else str(e)
            if self.skip_validation:
                raise RuntimeError(f"Git operation failed during repo setup: {stderr}")
            ctx.validation_error = f"SWE INPUT VALIDATION FAILED: Git operation failed during repo setup. Error: {stderr}"
            logger.error("Some git operation failed; early-returning to surface validation error")
            return config
        ctx.repo_dir = repo_dir
        logger.info("Repo setup complete at task_dir/app (pre-git squash)")

        # Normalize patch line endings to match target files
        logger.info("Normalizing patch line endings for test_patch and golden_patch")
        test_patch = _normalize_patch_line_endings(test_patch, repo_dir)
        golden_patch = _normalize_patch_line_endings(golden_patch, repo_dir)
        logger.info("Normalized patch line endings successfully")

        logger.info("Writing other instance files to task directory")
        problem_statement_file = task_dir / "problem_statement.txt"
        problem_statement_file.write_text(combined_problem_statement)
        blocker_registry_file = task_dir / "blocker_registry.json"
        blocker_registry_file.write_text(json.dumps(blocker_registry, indent=2))
        run_script_path = task_dir / "run_script.sh"
        run_script_path.write_text(run_script)
        parser_script_path = task_dir / "parser.py"
        parser_script_path.write_text(parser_script)
        logger.info(f"Saved parser.py to {parser_script_path}")

        # Write metadata with jefzda base image to start
        # Fast validation uses docker exec, then we update to hil-bench-agent image with build after
        metadata = {
            "instance_id": instance_id,
            "repo_name": "app",
            "base_commit": "HEAD",
            "image_name": resolved_docker_image_name,
            "log_parser": log_parser,
            "language": plang.value,
            "blocker_stats": {
                "total": len(blocker_registry.get("blockers", [])),
                "missing_parameter": 0,
                "ambiguous_requirement": 0,
                "contradictory_requirement": 0,
            },
            "swe_bench_metadata": {
                "FAIL_TO_PASS": fail_to_pass,
                "PASS_TO_PASS": pass_to_pass,
            },
            "test_cmd": test_cmd,
            "test_patch": test_patch,
            "run_script_path": str(run_script_path),
            "parser_script_path": str(parser_script_path),
            "test_files": test_files,
        }
        metadata_file = task_dir / "metadata.json"
        metadata_file.write_text(json.dumps(metadata, indent=2))
        logger.info("Finished writing instance files to task directory")

        # FAST VALIDATION: Use docker exec instead of building an image. This spins up a container from jefzda base, copies files in, applies patches, runs tests
        ctx.golden_patch = golden_patch
        if self.skip_validation:
            logger.info("Skipping golden_patch validation (skip_validation=True)")
            ctx.validation_error = None
        else:
            logger.info(f"Golden patch:\n{golden_patch}")
            ctx.validation_error = self._validate_swe_fast(
                task_dir=task_dir,
                docker_image_name=resolved_docker_image_name,
                golden_patch=golden_patch,
                test_patch=test_patch,
                test_cmd=test_cmd,
                instance_id=instance_id,
                tests_to_pass=fail_to_pass,
                language=language,
            )
        if ctx.validation_error:
            logger.error(f"Validation failed, skipping agent image build: {ctx.validation_error}")
            return config.model_copy(update={"path": str(task_dir)})
        if self.validation_only:
            logger.info(
                "validation_only=True, returning after successful validation (no agent image built)"
            )
            return config.model_copy(update={"path": str(task_dir)})
        logger.info("Validation passed, proceeding with git nuclear reset and agent image build")
        try:
            # Wipe the entire .git directory and reinitialize from scratch so that NO git artifacts
            # survive: no branch refs, no remote-tracking refs, no reflog, no object store.
            # The old commit-tree + reset --hard approach left branches (devel/master/main),
            # remote-tracking refs (origin/*), and the reflog intact, letting agents cheat by
            # running "git log --all" or "git show <hash>" to read the solution directly.
            logger.info(
                "Nuclear git reset: removing .git and reinitializing with single clean commit"
            )
            subprocess.run(["rm", "-rf", ".git"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(
                ["git", "init", "-b", "master"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            # Disable auto-GC on the NEW repo to prevent a race condition where git packs
            # loose objects between shutil.copytree's scandir and its per-file copy calls.
            # Must be set here (after git init) — not before, since rm -rf .git above wipes
            # any gc.auto=0 that was set on the old repo.
            subprocess.run(
                ["git", "config", "gc.auto", "0"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "PLACEHOLDER_EMAIL"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "PLACEHOLDER_USER"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=repo_dir, check=True, capture_output=True, text=True
            )
            subprocess.run(
                ["git", "commit", "--no-gpg-sign", "-m", "Initial commit for SWE-agent"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            new_commit_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            commit_count = int(
                subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    cwd=repo_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            if commit_count != 1:
                raise RuntimeError(
                    f"Expected exactly 1 commit after nuclear reset, got {commit_count}"
                )
            logger.info(f"Nuclear git reset complete; HEAD={new_commit_sha[:8]}, commits=1")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr if e.stderr else str(e)
            raise RuntimeError(f"Git nuclear reset failed: {stderr}")
        if skip_agent_image_build:
            if not target_image_name:
                raise ValueError(
                    "skip_agent_image_build=True requires target_image_name so metadata points to a reusable image"
                )
            logger.info(
                f"skip_agent_image_build=True; skipping image build and using existing image {target_image_name}"
            )
            metadata["image_name"] = target_image_name
            metadata_file.write_text(json.dumps(metadata, indent=2))
            return config.model_copy(update={"path": str(task_dir)})
        logger.info("Building agent Docker image with prepared repo")
        try:
            built_image_name = _build_agent_image(
                base_image=resolved_docker_image_name,
                local_repo_path=repo_dir,
                run_script_path=run_script_path,
                parser_script_path=parser_script_path,
                instance_id=instance_id,
                target_image_name=target_image_name,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr if hasattr(e, "stderr") and e.stderr else str(e)
            # Show LAST 1000 chars since Docker errors appear at end of output
            error_tail = stderr[-STDERR_LIMIT:] if len(stderr) > STDERR_LIMIT else stderr
            raise RuntimeError(f"Failed to build agent Docker image: {error_tail}")
        logger.info(f"Successfully built agent image: {built_image_name}")

        # Update metadata with the built agent image name (replacing jefzda base image)
        metadata["image_name"] = built_image_name
        metadata_file.write_text(json.dumps(metadata, indent=2))
        logger.info("Updated metadata.json with agent image name")

        # Return updated config with task path
        logger.info(
            "Finished setting up SWE task environment and validating input; returning updated config with task path"
        )
        return config.model_copy(update={"path": str(task_dir)})





    @contextmanager
    def _setup_task_environment(
        self, input: AgentInput, config: HILBenchConfig
    ) -> Generator[tuple[HILBenchConfig, TaskSetupContext], None, None]:
        """
        Context manager for setting up and cleaning up task environment. Downloads data from S3, creates task files, and updates config with paths. Automatically cleans up temp directories on exit unless enable_run_logs is True.

        Yields:
            Tuple of (Updated HILBenchConfig with paths configured, TaskSetupContext)
        """
        ctx = TaskSetupContext()
        try:
            if config.mode == HILBenchMode.SWE:
                updated_config = self._setup_swe_task(input, config, ctx)
            elif config.mode == HILBenchMode.SQL:
                updated_config = self._setup_sql_task(input, config, ctx)
            else:
                raise ValueError(f"Unknown mode: {config.mode}")
            yield updated_config, ctx
        finally:
            if ctx.task_dir:
                self.last_task_dir = str(ctx.task_dir)
            if self.enable_run_logs:
                self._print_log_paths(ctx)
                logger.info("Skipping cleanup because enable_run_logs=True")
            else:
                try:
                    self._log_temp_dir_files(
                        ctx
                    )  # only log in production because harder to keep/view temp dir
                except Exception as e:
                    logger.debug(f"[Post-run] Failed to log temp directory files: {e}")
                logger.info("Cleaning up task environment")
                ctx.cleanup()

    def _print_log_paths(self, ctx: TaskSetupContext) -> None:
        """Print paths to log/output files for debugging."""
        print("\n" + "=" * 60)
        print("HIL-BENCH RUN LOGS")
        print("=" * 60)
        if ctx.task_dir:
            print(f"\nTask directory: {ctx.task_dir}")
            for mode_suffix in ["baseline", "with_blockers"]:
                output_dir = ctx.task_dir / f"output_{mode_suffix}"
                if output_dir.exists():
                    print(f"\n--- {mode_suffix.upper()} MODE ---")
                    consolidated_metrics = list(output_dir.rglob("consolidated_metrics.json"))
                    consolidated_results = list(output_dir.rglob("consolidated_results.json"))
                    trace_logs = list(output_dir.rglob("*.trace.log"))
                    if consolidated_metrics:
                        print(f"🐼 Metrics file: {consolidated_metrics[0]}")
                    if consolidated_results:
                        print(f"🐭 Results file: {consolidated_results[0]}")
                    if trace_logs:
                        print(f"📜 Trace log files ({len(trace_logs)} found):")
                        for trace_log in trace_logs[:3]:  # Show first 3
                            print(f"\t- {trace_log}")
                        if len(trace_logs) > 3:
                            print(f"\t... and {len(trace_logs) - 3} more")
                    else:
                        print("No trace log files found")
        if ctx.instances_file:
            print(f"\nInstances file: {ctx.instances_file}")
        if ctx.db_path:
            print(f"Database file: {ctx.db_path}")
        print("\n⚠️  These files will NOT be cleaned up. Delete manually when done.")
        print("=" * 60 + "\n")

    def _log_temp_dir_files(self, ctx: TaskSetupContext) -> None:
        logger.info("[Post-run] Logging initial portions of debug files from temp directory")
        if ctx.task_dir is None:
            logger.debug("[Post-run] No task directory was created")
            return
        for mode_suffix in ["baseline", "with_blockers"]:
            output_dir = ctx.task_dir / f"output_{mode_suffix}"
            if not output_dir.exists():
                logger.debug(f"[Post-run] {mode_suffix} output dir does not exist")
                continue
            logger.info(f"[Post-run] === {mode_suffix.upper()} MODE ===")
            for pattern in DEBUG_LOG_FILES:
                for file_path in sorted(output_dir.rglob(pattern)):
                    try:
                        content = file_path.read_text()
                        truncated = content[:LOG_FILE_CHAR_LIMIT]
                        if len(content) > LOG_FILE_CHAR_LIMIT:
                            truncated += f"\n... (truncated, {len(content)} total chars)"
                        relative_path = file_path.relative_to(ctx.task_dir)
                        logger.info(f"[Post-run] {relative_path}:\n{truncated}")
                    except Exception as e:
                        logger.debug(f"[Post-run] Failed to read {file_path}: {e}")

    def _run(self, config: HILBenchConfig, ctx: TaskSetupContext) -> HILBenchResult:
        """
        Run the HIL-Bench command via CLI subprocess.

        Uses `uv run hil swe/sql` for automatic dependency management and
        correct environment setup.

        Args:
            config: HILBenchConfig with all settings
            ctx: TaskSetupContext with temp directories and output location

        Returns:
            HILBenchResult with metrics and results loaded from output files
        """
        try:
            if config.mode == HILBenchMode.SWE:
                return self._run_swe_cli(config, ctx)
            elif config.mode == HILBenchMode.SQL:
                return self._run_sql_cli(config, ctx)
            else:
                return HILBenchResult(
                    success=False,
                    error_message=f"Unknown mode: {config.mode}",
                )
        except Exception as e:
            logger.exception(f"Error running {config.mode.value} command: {e}")
            return HILBenchResult(
                success=False,
                error_message=str(e),
            )

    def _is_deployed_container(self) -> bool:
        """
        Check if we're running in a deployed Docker container.

        If so, packages are already installed and we should use --no-sync in `uv` commands to avoid CodeArtifact authentication issues when the token expires.
        """
        return os.environ.get("HIL_BENCH_DEPLOYED", "").lower() == "true"








    def _is_model_call_log_line(self, line: str) -> bool:
        return "[MODEL CALL]" in line

    def _log_model_call_line(self, line: str) -> None:
        logger.info(line.strip())

    def _run_subprocess_with_realtime_logging(
        self,
        cli_args: list[str],
        cwd: str,
        env: dict[str, str],
        enable_model_call_logging: bool = False,
    ) -> subprocess.CompletedProcess:
        # Force unbuffered output from Python subprocesses
        env = env.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # Accumulate full output for error handling
        stdout_lines: list[str] = []

        process = subprocess.Popen(
            cli_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout to prevent buffer blocking
            text=True,
            cwd=cwd,
            env=env,
            bufsize=1,  # Line buffered
        )

        try:
            # Read stdout line-by-line for real-time logging
            assert process.stdout is not None
            for line in process.stdout:
                stdout_lines.append(line)
                # Log model calls in real-time
                if enable_model_call_logging and self._is_model_call_log_line(line):
                    self._log_model_call_line(line)

            # Wait for process to complete and get return code
            returncode = process.wait()

        finally:
            # Ensure cleanup even on exception
            if process.stdout:
                process.stdout.close()
            # Ensure process is terminated
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

        # Return CompletedProcess-like result for compatibility
        full_stdout = "".join(stdout_lines)
        return subprocess.CompletedProcess(
            args=cli_args,
            returncode=returncode,
            stdout=full_stdout,
            stderr="",  # stderr merged into stdout
        )

    def _get_hil_bench_env(self) -> dict[str, str]:
        """Get environment variables for running hil_bench commands.

        Sets:
        - UV_PROJECT_ENVIRONMENT: venv location (differs for deployed vs local)
        - UV_NO_SYNC, UV_FROZEN, UV_NO_BUILD: prevent UV from modifying venv (deployed only)
        - LITELLM_API_KEY: API key for LLM calls (from api_key_name secret)
        - LITELLM_BASE_URL: Base URL for LiteLLM proxy

        Clears (in deployed container):
        - ddtrace bootstrap path from PYTHONPATH: ddtrace-run adds this, but hil_bench venv doesn't have ddtrace
        """
        env = os.environ.copy()
        if self._is_deployed_container():
            # In deployed container, use the pre-built .venv from Docker image. This is where `uv sync` is run during Docker build
            hil_bench_path = _get_hil_bench_project_path()
            env["UV_PROJECT_ENVIRONMENT"] = str(hil_bench_path / ".venv")

            # Use env vars (not just CLI flags) to prevent UV from syncing/building. These propagate to nested `uv run` calls inside the scripts
            env["UV_NO_SYNC"] = "true"
            env["UV_FROZEN"] = "true"
            env["UV_NO_BUILD"] = "true"

            # Clear ddtrace bootstrap path from PYTHONPATH to avoid "ModuleNotFoundError: No module named 'ddtrace'"
            # ddtrace-run adds its bootstrap dir to PYTHONPATH, which contains a sitecustomize.py that imports ddtrace
            # The hil_bench venv doesn't have ddtrace, so we remove the bootstrap path from PYTHONPATH
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                paths = pythonpath.split(os.pathsep)
                paths = [p for p in paths if "ddtrace" not in p]
                env["PYTHONPATH"] = os.pathsep.join(paths) if paths else ""
        else:
            # Local development: use a user-specific temp directory to avoid re-syncing on every run, permission conflicts with other users, and NFS locking issues
            venv_path = Path("/tmp") / f"hil_bench_venv_{os.getuid()}"
            env["UV_PROJECT_ENVIRONMENT"] = str(venv_path)
        env["LITELLM_API_KEY"] = get_from_env_or_secrets(self.api_key_name)
        env["LITELLM_BASE_URL"] = (
            PUBLIC_LITELLM_BASE_URL
            if self.api_key_name == "HIL_BENCH_PUBLIC_2"
            else LITELLM_BASE_URL
        )
        env["API_BASE"] = env["LITELLM_BASE_URL"]
        return env

    def _parse_running_for_hours(self, running_for: str) -> float:
        running_for = running_for.lower()
        try:
            if "second" in running_for:
                match = re.search(r"(\d+)", running_for)
                return float(match.group(1)) / 3600 if match else 0
            elif "minute" in running_for:
                match = re.search(r"(\d+)", running_for)
                return float(match.group(1)) / 60 if match else 0
            elif "hour" in running_for:
                match = re.search(r"(\d+)", running_for)
                return float(match.group(1)) if match else 0
            elif "day" in running_for:
                match = re.search(r"(\d+)", running_for)
                return float(match.group(1)) * 24 if match else 0
            elif "week" in running_for:
                match = re.search(r"(\d+)", running_for)
                return float(match.group(1)) * 168 if match else 0
        except Exception:
            pass
        return 0

    def _is_active_uv_run_hil_swe_container(self, container_id: str) -> bool:
        """Conservative activity check for runtime containers.

        We treat all running target containers as active to avoid deleting in-flight work.
        """
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Running}}\t{{.Path}}\t{{json .Args}}\t{{json .Config.Cmd}}",
                    container_id,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return False
            parts = result.stdout.strip().split("\t")
            if len(parts) < 4 or parts[0].strip().lower() != "true":
                return False
            argv: list[str] = []
            try:
                args = json.loads(parts[2]) if parts[2] else []
                if isinstance(args, list):
                    argv.extend([str(x) for x in args])
            except Exception:
                pass
            try:
                cmd = json.loads(parts[3]) if parts[3] else []
                if isinstance(cmd, list):
                    argv.extend([str(x) for x in cmd])
            except Exception:
                pass
            full_cmd = " ".join([parts[1]] + argv).lower()
            active_markers = (
                "uv run hil swe",
                "swerex-remote",
                "sleep infinity",
                "tail -f /dev/null",
            )
            if any(marker in full_cmd for marker in active_markers):
                return True
            # Conservative fallback: if it's running at all, consider it active.
            return True
        except Exception:
            return False

    def _register_attempt_owner(self, attempt_id: str | None) -> str | None:
        if not attempt_id:
            return None
        try:
            Path(ATTEMPT_OWNER_DIR).mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            marker = Path(ATTEMPT_OWNER_DIR) / f"{attempt_id}__{os.getpid()}__{token}.owner"
            marker.write_text(datetime.now(timezone.utc).isoformat())
            return token
        except Exception as e:
            logger.warning("Failed to register owner marker for %s: %s", attempt_id, e)
            return None

    def _unregister_attempt_owner(self, attempt_id: str | None, owner_token: str | None) -> None:
        if not attempt_id or not owner_token:
            return
        marker = Path(ATTEMPT_OWNER_DIR) / f"{attempt_id}__{os.getpid()}__{owner_token}.owner"
        marker.unlink(missing_ok=True)

    def _attempt_has_live_owner(self, attempt_id: str) -> bool:
        lock_path = Path(ATTEMPT_LOCK_DIR) / f"{attempt_id}.lock"
        if lock_path.exists():
            try:
                lines = lock_path.read_text().strip().split("\n")
                if lines:
                    pid = int(lines[0])
                    try:
                        os.kill(pid, 0)
                        return True
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        return True
            except Exception:
                pass

        owner_dir = Path(ATTEMPT_OWNER_DIR)
        if not owner_dir.exists():
            return False
        for marker in owner_dir.glob(f"{attempt_id}__*__*.owner"):
            parts = marker.name.split("__")
            if len(parts) < 3:
                marker.unlink(missing_ok=True)
                continue
            try:
                pid = int(parts[1])
            except Exception:
                marker.unlink(missing_ok=True)
                continue
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                marker.unlink(missing_ok=True)
            except PermissionError:
                return True
        return False

    def _extract_swe_attempt_id(self, image_name: str, container_name: str) -> str | None:
        image_match = _SWE_ATTEMPT_ID_FROM_IMAGE_RE.search(image_name or "")
        if image_match:
            return image_match.group(1)
        name_match = _SWE_ATTEMPT_ID_FROM_NAME_RE.search(container_name or "")
        if name_match:
            return name_match.group(1)
        return None

    def _cleanup_docker_containers_local(self) -> None:
        """
        Clean up stale Docker containers before running HIL-Bench commands locally (non-sandbox). This prevents Docker resource exhaustion that could cause the `uv run` command to fail.
        """
        container_threshold = 15
        reclaimable_gb_threshold = 100.0

        def _size_to_gib(size_text: str) -> float:
            head = size_text.split("(", 1)[0].strip().replace(",", "")
            head = re.sub(r"\s+", "", head)
            match = re.match(r"(?i)^([\d.]+)([kmgt]?i?b)$", head)
            if not match:
                return 0.0
            value = float(match.group(1))
            unit = match.group(2).lower()
            scale_gib = {
                "b": 1 / (1024**3),
                "kb": 1 / (1024**2),
                "kib": 1 / (1024**2),
                "mb": 1 / 1024,
                "mib": 1 / 1024,
                "gb": 1.0,
                "gib": 1.0,
                "tb": 1024.0,
                "tib": 1024.0,
            }.get(unit, 0.0)
            return value * scale_gib

        try:
            # Check if Docker is available
            subprocess.run(
                ["docker", "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("Docker not available, skipping local cleanup")
            return

        logger.info("Cleaning up stale Docker containers before HIL-Bench run...")

        try:
            stale_count = 0
            reclaimable_container_gb = 0.0
            build_cache_gb = 0.0

            name_filters = [
                "sweagentswe-agentlatest",
                "hil-bench-agent",
                "hilbench-swe",
                "sweb",
                "rex-deploy",
                "validation-",
            ]
            for name_filter in name_filters:
                for status in ("exited",):
                    result = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "-aq",
                            "--filter",
                            f"status={status}",
                            "--filter",
                            f"name={name_filter}",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    stale_count += len(
                        [cid.strip() for cid in result.stdout.strip().split("\n") if cid.strip()]
                    )

            try:
                result = subprocess.run(
                    ["docker", "system", "df", "--format", "json"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("Type") == "Containers":
                        reclaimable_container_gb = _size_to_gib(str(row.get("Reclaimable", "0B")))
                    elif row.get("Type") == "Build Cache":
                        build_cache_gb = _size_to_gib(str(row.get("Size", "0B")))
            except Exception:
                pass

            if (
                stale_count < container_threshold
                and reclaimable_container_gb < reclaimable_gb_threshold
                and build_cache_gb < reclaimable_gb_threshold
            ):
                logger.info(
                    "Skipping Docker cleanup due to low thresholds "
                    "(stale=%d, reclaimable_containers=%.1fGB, build_cache=%.1fGB)",
                    stale_count,
                    reclaimable_container_gb,
                    build_cache_gb,
                )
                return

            # Remove only exited containers by name filter.
            for name_filter in name_filters:
                for status in ("exited",):
                    result = subprocess.run(
                        [
                            "docker",
                            "ps",
                            "-aq",
                            "--filter",
                            f"status={status}",
                            "--filter",
                            f"name={name_filter}",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    container_ids = [
                        cid.strip() for cid in result.stdout.strip().split("\n") if cid.strip()
                    ]
                    if container_ids:
                        subprocess.run(
                            ["docker", "rm", "-f"] + container_ids,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        logger.info(
                            "Removed %d %s containers (filter: %s)",
                            len(container_ids),
                            status,
                            name_filter,
                        )

            # Clean up containers by image pattern OR name pattern
            # This catches containers whose image shows as ID instead of name
            image_patterns = [
                "hil-bench-agent:",
                "hilbench-swe:",
                "jefzda/sweap-images:",
                "sweb.eval.",
                "local/sweb.eval.",
            ]
            name_patterns = [
                "hil-bench-agent",
                "hilbench-swe",
                "sweagent",
                "sweb",
                "rex-deploy",
                "validation-",
            ]

            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--format",
                    "{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}\t{{.RunningFor}}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            containers_to_remove = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 5:
                    container_id, image, name, status = (
                        parts[0],
                        parts[1],
                        parts[2],
                        parts[3],
                    )
                    should_check = False
                    # Check image patterns
                    if any(pattern in image for pattern in image_patterns):
                        should_check = True
                    # Also check name patterns (more reliable since image might be ID)
                    elif any(pattern in name for pattern in name_patterns):
                        should_check = True

                    if should_check:
                        if status.lower().startswith("exited"):
                            containers_to_remove.append(container_id)
                        elif status.lower().startswith("up"):
                            running_hours = self._parse_running_for_hours(parts[4])
                            attempt_id = self._extract_swe_attempt_id(image, name)
                            if attempt_id and not self._attempt_has_live_owner(attempt_id):
                                containers_to_remove.append(container_id)
                                logger.info(
                                    "Reaping orphan running container %s (%.1fh) for attempt %s (no live owner)",
                                    container_id[:12],
                                    running_hours,
                                    attempt_id,
                                )
                                continue
                            logger.info(
                                "Skipping running container %s (%.1fh) during generic cleanup",
                                container_id[:12],
                                running_hours,
                            )
                            continue

            if containers_to_remove:
                subprocess.run(
                    ["docker", "rm", "-f"] + containers_to_remove,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                logger.info(f"Removed {len(containers_to_remove)} containers by image/name pattern")

            try:
                subprocess.run(
                    ["docker", "builder", "prune", "-f"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                logger.info("Pruned Docker build cache")
            except Exception as e:
                logger.warning(f"Failed to prune Docker build cache: {e}")

        except Exception as e:
            logger.warning(f"Docker cleanup failed: {e}")

    def _cleanup_containers_for_image(self, image_name: str, context_label: str) -> int:
        """Remove stale containers for a finished run image (safe container-only cleanup)."""
        if not image_name:
            return 0
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--format",
                    "{{.ID}}\t{{.Status}}\t{{.RunningFor}}",
                    "--filter",
                    f"ancestor={image_name}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            container_ids = []
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                cid, status, running_for = parts[0], parts[1].lower(), parts[2]
                if status.startswith("exited"):
                    container_ids.append(cid)
                    continue
                if status.startswith("up"):
                    running_hours = self._parse_running_for_hours(running_for)
                    attempt_id = self._extract_swe_attempt_id(image_name, "")
                    if attempt_id and not self._attempt_has_live_owner(attempt_id):
                        container_ids.append(cid)
                        logger.info(
                            "Reaping orphan running container %s (%.1fh) during targeted cleanup for %s",
                            cid[:12],
                            running_hours,
                            context_label,
                        )
                        continue
                    logger.info(
                        "Skipping running container %s (%.1fh) during targeted cleanup for %s",
                        cid[:12],
                        running_hours,
                        context_label,
                    )
                    continue
            if not container_ids:
                return 0
            subprocess.run(
                ["docker", "rm", "-f"] + container_ids,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            logger.info(
                "Removed %d container(s) for finished run (%s) using image %s",
                len(container_ids),
                context_label,
                image_name,
            )
            return len(container_ids)
        except Exception as e:
            logger.warning(
                "Failed targeted container cleanup for %s (image=%s): %s",
                context_label,
                image_name,
                e,
            )
            return 0

    def _get_swe_task_image_name(self, ctx: TaskSetupContext) -> str | None:
        """Read the runtime SWE image_name from task metadata if available."""
        if not ctx.task_dir:
            return None
        metadata_path = Path(ctx.task_dir) / "metadata.json"
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text())
            image_name = metadata.get("image_name")
            return str(image_name) if image_name else None
        except Exception:
            return None

    ##### Scale Sandbox VM Methods #####
    async def _sandbox_exec_with_output(
        self, sandbox: Any, *args: str, timeout: int | None = None
    ) -> tuple[int, str, str]:
        """Execute command in sandbox and return (exit_code, stdout, stderr)."""
        try:
            # New SDK: async exec surface is exec_async(); old SDK uses await sandbox.exec().
            if hasattr(sandbox, "exec_async"):
                process = await sandbox.exec_async(*args, timeout=timeout)
            else:
                process = await sandbox.exec(*args)

            if timeout is not None:
                stdout = await asyncio.wait_for(process.stdout.read(), timeout=timeout)
                stderr = await asyncio.wait_for(process.stderr.read(), timeout=timeout)
                exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
            else:
                stdout = await process.stdout.read()
                stderr = await process.stderr.read()
                exit_code = await process.wait()

            if isinstance(stdout, bytes):
                stdout_text = stdout.decode(errors="replace")
            else:
                stdout_text = stdout or ""
            if isinstance(stderr, bytes):
                stderr_text = stderr.decode(errors="replace")
            else:
                stderr_text = stderr or ""
            return exit_code, stdout_text, stderr_text
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "404" in error_str or "not found" in error_str or "connection" in error_str:
                raise RuntimeError(f"VM connection lost: {e}")
            raise

    async def _sandbox_exec_script(self, sandbox: Any, script: str, description: str) -> str:
        """Execute a bash script in the sandbox with logging."""
        logger.info(f"[Sandbox] {description}...")
        exit_code, stdout, stderr = await self._sandbox_exec_with_output(
            sandbox, "sudo", "bash", "-c", script
        )
        if exit_code != 0:
            # Log full error for Datadog visibility (up to 5000 chars)
            full_error = stderr or stdout or "No output"
            logger.error(
                f"[Sandbox] {description} failed (exit {exit_code}):\n{full_error[:STDERR_LIMIT]}"
            )
            raise RuntimeError(
                f"{description} failed (exit {exit_code}): {full_error[:STDERR_LIMIT]}"
            )
        logger.info(f"[Sandbox] COMPLETED: {description}")
        return stdout

    async def _sandbox_transfer_file(
        self, sandbox: Any, local_data: bytes, remote_path: str
    ) -> None:
        """Transfer file data to sandbox using base64 encoding."""
        import base64

        encoded = base64.b64encode(local_data).decode()
        chunk_size = 100000  # 100KB chunks
        if len(encoded) > chunk_size:
            # Write in chunks for large files
            await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", f"rm -f {remote_path}"
            )
            for i in range(0, len(encoded), chunk_size):
                chunk = encoded[i : i + chunk_size]
                await self._sandbox_exec_with_output(
                    sandbox, "sudo", "bash", "-c", f"echo -n '{chunk}' >> {remote_path}.b64"
                )
            await self._sandbox_exec_with_output(
                sandbox,
                "sudo",
                "bash",
                "-c",
                f"base64 -d {remote_path}.b64 > {remote_path} && rm {remote_path}.b64",
            )
        else:
            await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", f"echo '{encoded}' | base64 -d > {remote_path}"
            )

    async def _sandbox_transfer_dir(
        self,
        sandbox: Any,
        local_dir: Path,
        remote_dir: str,
        exclude_patterns: list[str] | None = None,
    ) -> None:
        """Transfer a directory to sandbox by creating a tar archive.

        Args:
            sandbox: The sandbox instance
            local_dir: Local directory to transfer
            remote_dir: Remote path to extract to
            exclude_patterns: Optional list of patterns to exclude (e.g., ['.venv', 'logs', '*.pyc'])
        """
        import fnmatch
        import io
        import tarfile

        logger.info(f"[Sandbox] Transferring directory {local_dir} to {remote_dir}...")

        # Use provided excludes, or empty list (no default excludes to avoid surprises)
        excludes = exclude_patterns or []

        def filter_func(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            """Filter function for tar to exclude patterns."""
            name = tarinfo.name
            for pattern in excludes:
                # Check if pattern matches any part of the path
                parts = name.split("/")
                for part in parts:
                    if fnmatch.fnmatch(part, pattern):
                        return None
            return tarinfo

        # Create tar archive in memory with filtering
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            tar.add(local_dir, arcname=".", filter=filter_func)
        tar_data = tar_buffer.getvalue()

        # Transfer tar to sandbox
        tar_remote_path = f"/tmp/transfer_{uuid.uuid4().hex[:8]}.tar.gz"
        await self._sandbox_transfer_file(sandbox, tar_data, tar_remote_path)

        # Extract on sandbox
        await self._sandbox_exec_with_output(
            sandbox, "sudo", "bash", "-c", f"mkdir -p {remote_dir}"
        )
        await self._sandbox_exec_with_output(
            sandbox, "sudo", "bash", "-c", f"tar -xzf {tar_remote_path} -C {remote_dir}"
        )
        await self._sandbox_exec_with_output(sandbox, "bash", "-c", f"rm {tar_remote_path}")
        logger.info(f"[Sandbox] Transfer completed from {local_dir} to {remote_dir}")

    async def _sandbox_retrieve_file(self, sandbox: Any, remote_path: str) -> bytes:
        """Retrieve file content from sandbox."""
        import base64

        exit_code, stdout, stderr = await self._sandbox_exec_with_output(
            sandbox, "sudo", "bash", "-c", f"base64 {remote_path}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to retrieve {remote_path}: {stderr}")
        return base64.b64decode(stdout.strip())

    async def _retrieve_sandbox_debug_files(
        self,
        sandbox: Any,
        remote_output_dir: str,
        local_output_dir: Path,
        verbose: bool = False,
    ) -> None:
        """Retrieve debug files from sandbox for error visibility. Called on error (always) or when enable_run_logs=True."""
        files_to_retrieve = [
            ("consolidated_metrics.json", "Metrics"),
            ("consolidated_results.json", "Results"),
            ("preds.json", "Agent solutions"),
            ("batch_config.json", "Batch config"),
            ("instances.json", "Instances input"),
            ("run_batch_exit_statuses.yaml", "Exit statuses"),
            ("report.json", "Test reports"),
            ("*.log", "Log files"),
        ]
        try:
            if verbose:
                exit_code, _, _ = await self._sandbox_exec_with_output(
                    sandbox,
                    "sudo",
                    "bash",
                    "-c",
                    f"find {remote_output_dir} -type f 2>/dev/null | head -50",
                )
            for pattern, _ in files_to_retrieve:
                find_cmd = f"find {remote_output_dir} -name '{pattern}' -type f 2>/dev/null"
                exit_code, found_files, _ = await self._sandbox_exec_with_output(
                    sandbox, "sudo", "bash", "-c", find_cmd
                )
                if exit_code != 0 or not found_files.strip():
                    continue
                for remote_file in found_files.strip().split("\n"):
                    if not remote_file:
                        continue
                    try:
                        # Preserve directory structure relative to output dir
                        rel_path = remote_file.replace(remote_output_dir + "/", "")
                        local_path = local_output_dir / rel_path
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        data = await self._sandbox_retrieve_file(sandbox, remote_file)
                        local_path.write_bytes(data)
                    except Exception as e:
                        logger.debug(f"[Sandbox] Could not retrieve {remote_file}: {e}")
            # Retrieve trajectory files (.traj and .json in trajectories dirs)
            logger.info(f"[Sandbox] Retrieving trajectory files from {remote_output_dir}...")
            traj_cmd = f"find {remote_output_dir} \\( -name '*.traj' -o -path '*trajectories*' -name '*.json' \\) -type f 2>/dev/null"
            exit_code, traj_files, _ = await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", traj_cmd
            )
            if exit_code == 0 and traj_files.strip():
                for traj_file in traj_files.strip().split("\n"):
                    if not traj_file:
                        continue
                    try:
                        rel_path = traj_file.replace(remote_output_dir + "/", "")
                        local_path = local_output_dir / rel_path
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        data = await self._sandbox_retrieve_file(sandbox, traj_file)
                        local_path.write_bytes(data)
                    except Exception as e:
                        logger.debug(f"[Sandbox] Could not retrieve trajectory {traj_file}: {e}")
        except Exception as e:
            logger.warning(f"[Sandbox] Error retrieving debug files: {e}")

    async def _retrieve_sandbox_trajectories(
        self,
        sandbox: Any,
        remote_output_dir: str,
        local_output_dir: Path,
    ) -> None:
        """Retrieve trajectory files (.traj) from sandbox for metadata. Located at model_safe/mode/pass_N/instance_id/instance_id.traj"""
        try:
            traj_cmd = f"find {remote_output_dir} -name '*.traj' -type f 2>/dev/null"
            exit_code, traj_files, _ = await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", traj_cmd
            )
            if exit_code != 0 or not traj_files.strip():
                logger.debug("[Sandbox] No trajectory files found")
                return
            for traj_file in traj_files.strip().split("\n"):
                if not traj_file:
                    continue
                try:
                    rel_path = traj_file.replace(remote_output_dir + "/", "")
                    local_path = local_output_dir / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    data = await self._sandbox_retrieve_file(sandbox, traj_file)
                    local_path.write_bytes(data)
                except Exception as e:
                    logger.debug(f"[Sandbox] Could not retrieve trajectory {traj_file}: {e}")
        except Exception as e:
            logger.warning(f"[Sandbox] Error retrieving trajectory files: {e}")

    async def _wait_for_vm_ready(
        self, sandbox: Any, max_wait: int = 180, poll_interval: int = 5
    ) -> None:
        """Poll until the VM is ready to accept commands.

        The sandbox API returns immediately after VM creation, but the VM needs time
        to boot before it can accept commands. This method polls until a simple
        command succeeds.
        """
        sandbox_id = _sandbox_identifier(sandbox)
        logger.info(f"[Sandbox {sandbox_id}] Waiting for VM to be ready...")
        elapsed = 0
        last_error = None

        while elapsed < max_wait:
            try:
                # Try to execute a simple command
                exit_code, stdout, _ = await self._sandbox_exec_with_output(
                    sandbox, "echo", "ready", timeout=10
                )
                if exit_code == 0 and "ready" in stdout:
                    logger.info(f"[Sandbox {sandbox_id}] VM ready after {elapsed}s")
                    return
                elif exit_code == -1:
                    # Server returned an error (VM not ready yet)
                    last_error = "VM returned error (still booting)"
                else:
                    last_error = f"Unexpected exit code: {exit_code}"
            except asyncio.TimeoutError:
                last_error = "Command timed out"
            except Exception as e:
                last_error = str(e)

            # Log progress every 30 seconds
            if elapsed > 0 and elapsed % 30 == 0:
                logger.info(
                    f"[Sandbox {sandbox_id}] Still waiting for VM... ({elapsed}s elapsed, last: {last_error})"
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise RuntimeError(
            f"VM {sandbox_id} did not become ready within {max_wait}s. Last error: {last_error}"
        )

    async def _create_sandbox_vm(self, config: SandboxConfig) -> Any:
        """Create a Scale Sandbox VM with retry logic."""
        client = _ScaleSandboxClientCompat(
            base_url=config.base_url,
            timeout=config.client_timeout,
        )

        for attempt in range(config.max_retries):
            try:
                request_id = uuid.uuid4().hex[:8]
                vm_name = f"hil-bench-agent-{request_id}"
                create_params = {
                    "image": "quay.io/containerdisks/ubuntu:22.04",
                    "sandbox_type": "vm",
                    "disk_size": config.disk_size,
                    "cpu": config.cpu,
                    "memory": config.memory,
                    "timeout": config.timeout,
                    "name": vm_name,
                    "product": "hil-bench",
                    "customer": "internal",
                    "team": "gen_ai",
                }
                logger.info(
                    f"[Sandbox] Creating VM (attempt {attempt + 1}/{config.max_retries})..."
                )
                start_time = asyncio.get_event_loop().time()
                try:
                    sandbox = await asyncio.wait_for(
                        client.create(**create_params),
                        timeout=180,  # if creation doesn't succeed in 3 minutes, raise
                    )
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    error_details = (
                        f"VM creation timed out after {elapsed:.1f}s. "
                        f"request_id={request_id}, base_url={config.base_url}, "
                        f"params={create_params}"
                    )
                    logger.error(f"[Sandbox] {error_details}")
                    raise RuntimeError(error_details)
                logger.info(
                    f"[Sandbox {_sandbox_identifier(sandbox)}] VM created, waiting for boot..."
                )
                # Poll until VM is actually ready to accept commands
                await self._wait_for_vm_ready(sandbox)
                return sandbox
            except Exception as e:
                if attempt < config.max_retries - 1:
                    delay = 2**attempt
                    logger.warning(
                        f"[Sandbox] Attempt {attempt + 1} failed: {e}, retrying in {delay}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(
                        f"Failed to create sandbox VM after {config.max_retries} attempts: {e}"
                    )

    async def _get_or_create_sandbox(
        self,
        config: SandboxConfig,
        client: Any,
        instance_id: str,
        docker_image_name: str,
        request_id: str,
    ) -> tuple[Any, bool, bool, str]:
        """
        Get existing persistent VM for instance_id or create new one.

        Uses VM locking to prevent concurrent access to the same VM.
        Multiple VMs may exist for the same instance_id; we pick the first available.

        Args:
            config: Sandbox configuration
            client: sandbox client instance
            instance_id: SWE-bench instance ID
            docker_image_name: Expected docker image for this instance
            request_id: Unique ID for this request (used for locking)

        Returns:
            Tuple of (sandbox, is_new_sandbox, needs_hil_bench_transfer, vm_id)
            - vm_id is returned so caller can release the lock when done
        """
        # Compute current hil_bench hash upfront (needed for matching)
        hil_bench_path = _get_hil_bench_project_path()
        current_hil_bench_hash = compute_hil_bench_hash(
            hil_bench_path, self._get_hil_bench_exclude_patterns()
        )

        # Try to find and lock an available VM for this instance_id
        # aget_available_vm handles:
        # - Filtering out locked VMs
        # - Filtering out expiring VMs
        # - Optimistic lock acquisition with verification
        entry = None
        try:
            entry = await aget_available_vm(
                instance_id=instance_id,
                request_id=request_id,
            )
        except Exception as e:
            logger.warning(f"[Sandbox] VM registry lookup failed, will create new VM: {e}")

        if entry:
            # Verify the entry is for the correct docker image
            if entry.docker_image_name != docker_image_name:
                logger.warning(
                    f"[Sandbox] Locked VM {entry.vm_id} has different docker image "
                    f"({entry.docker_image_name} vs {docker_image_name}), releasing lock and creating new..."
                )
                try:
                    await arelease_lock(instance_id, entry.vm_id)
                except Exception as e:
                    logger.warning(f"[Sandbox] Failed to release lock (non-fatal): {e}")
                entry = None

        if entry:
            required_disk_gi = parse_disk_size_gi(config.disk_size)
            entry_disk_gi = parse_disk_size_gi(entry.disk_size)
            if not entry.disk_size:
                logger.info(
                    f"[Sandbox] VM {entry.vm_id} has unknown disk size (legacy entry), replacing with {config.disk_size} VM..."
                )
                try:
                    await arelease_lock(instance_id, entry.vm_id)
                    await adelete_vm_entry(instance_id, entry.vm_id)
                except Exception as e:
                    logger.warning(f"[Sandbox] Failed to release/delete VM entry (non-fatal): {e}")
                entry = None
            elif entry_disk_gi < required_disk_gi:
                logger.info(
                    f"[Sandbox] VM {entry.vm_id} has smaller disk ({entry.disk_size}) than required ({config.disk_size}), replacing..."
                )
                try:
                    await arelease_lock(instance_id, entry.vm_id)
                    await adelete_vm_entry(instance_id, entry.vm_id)
                except Exception as e:
                    logger.warning(f"[Sandbox] Failed to release/delete VM entry (non-fatal): {e}")
                entry = None

        if entry:
            # Try to connect to and health-check the existing VM
            try:
                logger.info(
                    f"[Sandbox] Checking if persistent VM {entry.vm_id} is still alive "
                    f"(remaining: {entry.remaining_time_str()}, locked by: {entry.locked_by})..."
                )
                sandbox = await asyncio.wait_for(client.from_id(entry.vm_id), timeout=10)

                # Comprehensive health check for reusing VMs; verify ALL baseline setup steps completed
                health_check_script = f"""
echo "=== VM Health Check ==="

# Check 1: Basic connectivity
echo "CHECK_CONNECTIVITY=OK"

# Check 2: Python3 is installed (needed for patch normalization)
if python3 --version > /dev/null 2>&1; then
    echo "CHECK_PYTHON3=OK"
else
    echo "CHECK_PYTHON3=FAILED"
    exit 1
fi

# Check 3: Git is installed (needed for checkout, apply, commit)
if git --version > /dev/null 2>&1; then
    echo "CHECK_GIT=OK"
else
    echo "CHECK_GIT=FAILED"
    exit 1
fi

# Check 4: Docker CLI is installed
if docker --version > /dev/null 2>&1; then
    echo "CHECK_DOCKER_CLI=OK"
else
    echo "CHECK_DOCKER_CLI=FAILED"
    exit 1
fi

# Check 5: Docker daemon is running (not just installed)
if docker info > /dev/null 2>&1; then
    echo "CHECK_DOCKER_DAEMON=OK"
else
    echo "CHECK_DOCKER_DAEMON=FAILED"
    exit 1
fi

# Check 6: Docker buildx is available (needed for agent image build)
if docker buildx version > /dev/null 2>&1; then
    echo "CHECK_DOCKER_BUILDX=OK"
else
    echo "CHECK_DOCKER_BUILDX=FAILED"
    exit 1
fi

# Check 7: uv is installed (needed for hil_bench re-transfer)
if sudo [ -f "/root/.local/bin/uv" ]; then
    echo "CHECK_UV=OK"
else
    echo "CHECK_UV=FAILED"
    exit 1
fi

# Check 8: hil_bench project exists (pyproject.toml)
if [ -f "/workspace/hil_bench/pyproject.toml" ]; then
    echo "CHECK_HIL_BENCH_PROJECT=OK"
else
    echo "CHECK_HIL_BENCH_PROJECT=FAILED"
    exit 1
fi

# Check 9: hil_bench .venv exists (uv sync completed)
if [ -d "/workspace/hil_bench/.venv" ]; then
    echo "CHECK_HIL_BENCH_VENV=OK"
else
    echo "CHECK_HIL_BENCH_VENV=FAILED"
    exit 1
fi

# Check 10: hil CLI entry point exists (package actually installed, not just venv created)
if [ -f "/workspace/hil_bench/.venv/bin/hil" ]; then
    echo "CHECK_HIL_CLI=OK"
else
    echo "CHECK_HIL_CLI=FAILED"
    exit 1
fi

# Check 11: SWE-agent trajectories directory exists
if [ -d "/workspace/hil_bench/SWE-agent/trajectories" ]; then
    echo "CHECK_TRAJECTORIES_DIR=OK"
else
    echo "CHECK_TRAJECTORIES_DIR=FAILED"
    exit 1
fi

# Check 12: jefzda base image is already pulled (soft check - image will be pulled later if missing)
if docker images --format '{{{{.Repository}}}}:{{{{.Tag}}}}' | grep -q '{docker_image_name}'; then
    echo "CHECK_DOCKER_IMAGE=OK"
else
    echo "CHECK_DOCKER_IMAGE=MISSING"
fi

# Check 13: Sufficient total disk space (at least 100GB for large JS monorepos)
TOTAL_DISK_GB=$(df -BG / | tail -1 | awk '{{print $2}}' | tr -d 'G')
if [ "$TOTAL_DISK_GB" -ge 100 ]; then
    echo "CHECK_DISK_SIZE=OK (${{TOTAL_DISK_GB}}GB total)"
else
    echo "CHECK_DISK_SIZE=FAILED (${{TOTAL_DISK_GB}}GB < 100GB required)"
    exit 1
fi

echo "=== All Health Checks Passed ==="
"""
                exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                    sandbox, "bash", "-c", health_check_script, timeout=60
                )
                if exit_code == 0 and "All Health Checks Passed" in stdout:
                    if "CHECK_DOCKER_IMAGE=MISSING" in stdout:
                        logger.info(
                            f"[Sandbox] Reusing persistent VM: {entry.vm_id} "
                            f"(all critical checks passed, Docker image not cached - will be pulled later)"
                        )
                    else:
                        logger.info(
                            f"[Sandbox] Reusing persistent VM: {entry.vm_id} (all health checks passed)"
                        )

                    # Check if hil_bench needs re-transfer by comparing hashes
                    needs_hil_bench_transfer = current_hil_bench_hash != entry.hil_bench_hash
                    if needs_hil_bench_transfer:
                        logger.info(
                            f"[Sandbox] hil_bench hash changed ({entry.hil_bench_hash} -> {current_hil_bench_hash}), "
                            "will re-transfer"
                        )
                        # Update the hash in the registry (lock is still held)
                        entry.hil_bench_hash = current_hil_bench_hash
                        entry.last_used_at = datetime.now(timezone.utc).isoformat()
                        try:
                            await aput_vm_entry(entry)
                        except Exception as e:
                            logger.warning(f"[Sandbox] Failed to update VM entry (non-fatal): {e}")
                    else:
                        logger.info("[Sandbox] hil_bench hash unchanged, no re-transfer needed")

                    # Write debug file for local debugging compatibility
                    if self.enable_run_logs:
                        _DEBUG_SANDBOX_ID_FILE.write_text(_sandbox_identifier(sandbox))

                    # Return vm_id so caller can release lock when done
                    return sandbox, False, needs_hil_bench_transfer, entry.vm_id
                else:
                    # Log which health check failed
                    failed_checks = []
                    if "CHECK_PYTHON3=FAILED" in stdout:
                        failed_checks.append("python3")
                    if "CHECK_GIT=FAILED" in stdout:
                        failed_checks.append("git")
                    if "CHECK_DOCKER_CLI=FAILED" in stdout:
                        failed_checks.append("docker cli")
                    if "CHECK_DOCKER_DAEMON=FAILED" in stdout:
                        failed_checks.append("docker daemon")
                    if "CHECK_DOCKER_BUILDX=FAILED" in stdout:
                        failed_checks.append("docker buildx")
                    if "CHECK_UV=FAILED" in stdout:
                        failed_checks.append("uv")
                    if "CHECK_HIL_BENCH_PROJECT=FAILED" in stdout:
                        failed_checks.append("hil_bench project")
                    if "CHECK_HIL_BENCH_VENV=FAILED" in stdout:
                        failed_checks.append("hil_bench .venv")
                    if "CHECK_HIL_CLI=FAILED" in stdout:
                        failed_checks.append("hil CLI")
                    if "CHECK_TRAJECTORIES_DIR=FAILED" in stdout:
                        failed_checks.append("trajectories dir")
                    if (
                        "CHECK_DOCKER_IMAGE=FAILED" in stdout
                        or "CHECK_DOCKER_IMAGE=MISSING" in stdout
                    ):
                        failed_checks.append("docker image (non-critical)")
                    if "CHECK_DISK_SIZE=FAILED" in stdout:
                        failed_checks.append("disk size")
                    if not failed_checks:
                        failed_checks.append("unknown")
                    logger.info(
                        f"[Sandbox] VM {entry.vm_id} health check failed "
                        f"(failed: {', '.join(failed_checks)}), creating new..."
                    )
            except asyncio.TimeoutError:
                logger.info(f"[Sandbox] VM {entry.vm_id} health check timed out, creating new...")
            except Exception as e:
                logger.info(f"[Sandbox] Could not reuse VM {entry.vm_id}: {e}, creating new...")

            # VM unhealthy - release lock and delete registry entry
            try:
                await arelease_lock(instance_id, entry.vm_id)
                await adelete_vm_entry(instance_id, entry.vm_id)
            except Exception as e:
                logger.warning(f"[Sandbox] Failed to release/delete VM entry (non-fatal): {e}")

        # Create new VM
        logger.info(f"[Sandbox] Creating new persistent VM for instance_id={instance_id}")
        sandbox = await self._create_sandbox_vm(config)

        # Write to S3 registry IMMEDIATELY after VM creation (pre-locked)
        new_entry = create_registry_entry(
            vm_id=_sandbox_identifier(sandbox),
            instance_id=instance_id,
            docker_image_name=docker_image_name,
            hil_bench_hash=current_hil_bench_hash,
            locked_by=request_id,  # Pre-lock the new VM
            disk_size=config.disk_size,
        )
        try:
            await aregister_vm(new_entry)
            logger.info(
                f"[Sandbox] Registered new VM {_sandbox_identifier(sandbox)} for instance_id={instance_id}, "
                f"disk_size={config.disk_size}, expires at {new_entry.expires_at}, locked by {request_id}"
            )
        except Exception as e:
            logger.warning(
                f"[Sandbox] Failed to register VM {_sandbox_identifier(sandbox)} in registry (VM still usable, "
                f"but won't be available for reuse): {e}"
            )

        # Write debug file for local debugging compatibility
        if self.enable_run_logs:
            _DEBUG_SANDBOX_ID_FILE.write_text(_sandbox_identifier(sandbox))

        # Return vm_id so caller can release lock when done
        return sandbox, True, True, _sandbox_identifier(sandbox)

    def _get_hil_bench_exclude_patterns(self) -> list[str]:
        """Get the exclude patterns for hil_bench transfer and hashing."""
        return [
            # Python artifacts (from .gitignore)
            ".venv",
            ".venv-skyrl",
            ".venv-vllm",
            "__pycache__",
            "*.pyc",
            "*.pyo",
            "*.egg-info",
            "*.egg",
            "build",
            "dist",
            # Git and IDE
            ".git",
            ".cursor",
            ".devcontainer",
            ".github",
            ".vscode",
            ".idea",
            # Runtime outputs (from .gitignore - not needed for execution)
            "logs",
            "results",
            "trajectories",
            # Test/dev directories (not needed for execution)
            "tests",
            "docs",
            ".pytest_cache",
            ".tox",
            ".mypy_cache",
            ".cache",
            # SQL-specific (not needed for SWE tasks)
            "sql_benchmark",
            "*.sqlite",
            # HIL-bench specific (from .gitignore or not needed)
            "tasks",  # Tasks transferred separately
            "swe_queue",  # CB quick hits
            "setup",  # Setup scripts with env files
            "research_evals",  # Research scripts
            "buffed_business_info",  # Synthetic business info
            "buffed_business_info_by_task",  # Synthetic business info by task
            "demand_gen_demo",  # Standalone demos
            # Training stuff
            "notebooks",  # Finetuning
            "devbox_runs",  # Finetuning logs
            "skyrl_gym_env",  # SkyRL gym environment
            "skyrl_training",  # SkyRL training scripts
            "data",  # datasets
            "task_databases",  # Local modified databases
            "wandb",  # WandB
            # SWE-agent specific (from .gitignore)
            "assets",  # images/docs
            "*.ipynb",  # notebooks
            "*.log",
            "node_modules",
        ]

    async def _setup_sandbox_environment(self, sandbox: Any, config: SandboxConfig) -> None:
        """Install Docker and Python in the sandbox VM."""
        sandbox_id = _sandbox_identifier(sandbox)
        # Poll for cloud-init completion
        logger.info("[Sandbox] Waiting for cloud-init to complete...")
        max_wait = 120
        poll_interval = 5
        elapsed = 0
        while elapsed < max_wait:
            exit_code, stdout, _ = await self._sandbox_exec_with_output(
                sandbox, "cloud-init", "status", timeout=10
            )
            if exit_code == 0 and "done" in stdout.lower():
                logger.info(f"[Sandbox] Cloud-init completed in {elapsed}s")
                break
            if elapsed > 0 and elapsed % 30 == 0:
                logger.info(f"[Sandbox] Still waiting for cloud-init... ({elapsed}s elapsed)")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        else:
            raise RuntimeError(f"Cloud-init did not complete within {max_wait}s on VM {sandbox_id}")

        # Verify connectivity
        exit_code, stdout, _ = await self._sandbox_exec_with_output(sandbox, "whoami")
        logger.info(f"[Sandbox] Connected as: {stdout.strip()}")

        # Install Docker + Python/uv in parallel
        await self._sandbox_exec_script(
            sandbox, self._SANDBOX_SETUP_SCRIPT, "Docker + Python/uv installation"
        )

        # Verify installations
        exit_code, stdout, _ = await self._sandbox_exec_with_output(sandbox, "docker", "--version")
        logger.info(f"[Sandbox] {stdout.strip()}")

    async def _install_hil_bench_in_sandbox(self, sandbox: Any) -> None:
        """Transfer hil_bench project to VM and run uv sync."""
        hil_bench_path = _get_hil_bench_project_path()
        logger.info("[Sandbox] Transferring hil_bench project to VM...")
        exclude_patterns = self._get_hil_bench_exclude_patterns()
        await self._sandbox_transfer_dir(
            sandbox, hil_bench_path, "/workspace/hil_bench", exclude_patterns=exclude_patterns
        )

        # Set up the project with uv. uv.lock contains CodeArtifact URLs which require auth, so we delete the lock file and force uv to use PyPI directly (all hilbench deps are public)
        setup_script = """
cd /workspace/hil_bench
export PATH="$HOME/.local/bin:$PATH"
rm -f uv.lock
rm -f .python-version

# Retry helper for uv sync (handles transient PyPI errors)
uv_sync_retry() {
    local max_attempts=3
    local delay=10
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if uv sync --python 3.11 --index-url https://pypi.org/simple/ 2>&1; then
            return 0
        fi
        echo "[SETUP] uv sync failed (attempt $attempt/$max_attempts), retrying in ${delay}s..."
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done
    echo "[SETUP] uv sync failed after $max_attempts attempts"
    return 1
}

uv_sync_retry
mkdir -p /workspace/hil_bench/SWE-agent/trajectories
"""
        await self._sandbox_exec_script(sandbox, setup_script, "hil_bench uv sync")

    async def _prepare_repo_in_sandbox(
        self,
        sandbox: Any,
        docker_image_name: str,
        base_commit: str,
        setup_patch: Optional[str],
        remote_task_dir: str,
        tests_to_pass: Optional[list[str]] = None,
        test_patch: Optional[str] = None,
        llm_extracted_functions: Optional[list[str]] = None,
        internal_repo_url: Optional[str] = None,
        skip_squash: bool = False,
    ) -> str:
        """Prepare repo inside the sandbox VM:
        1. Extract repo from Docker image (instead of cloning from GitHub)
        2. Checkout base_commit
        3. Apply setup_patch (if exists)
        4. Commit changes
        5. Nuclear git reset (unless skip_squash=True): wipe .git and reinitialize with a single
           clean commit so that no branch refs, remote-tracking refs, reflog, or object-store
           artifacts survive that agents could use to read the solution.
        """
        remote_repo_dir = f"{remote_task_dir}/app"
        logger.info(f"[Sandbox] Extracting repo from Docker image {docker_image_name} inside VM...")
        docker_image_candidates = _docker_image_pull_candidates(docker_image_name)
        candidate_literals = " ".join(shlex.quote(c) for c in docker_image_candidates)
        resolved_image_name = docker_image_name

        dockerhub_creds = get_dockerhub_env_vars()
        dockerhub_username = dockerhub_creds.get("DOCKERHUB_USERNAME", "")
        dockerhub_token = dockerhub_creds.get("DOCKERHUB_TOKEN", "")

        # Extract repo from Docker image using docker create + docker cp
        extract_script = f"""
set -e

if [ -f "{remote_task_dir}/Dockerfile.internal" ]; then
    SELECTED_IMAGE={shlex.quote(docker_image_name)}
    echo "[docker] Building internal SWEAP image $SELECTED_IMAGE from metadata Dockerfile"
    DOCKERHUB_USERNAME='{dockerhub_username}'
    DOCKERHUB_TOKEN='{dockerhub_token}'
    if [ -n "$DOCKERHUB_USERNAME" ] && [ -n "$DOCKERHUB_TOKEN" ]; then
        echo "[docker] Logging in to Docker Hub before internal SWEAP build..."
        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin 2>/dev/null && \
            echo "[docker] Logged in to Docker Hub" || \
            echo "[docker] Docker Hub login failed, continuing with unauthenticated build"
    fi
    BUILD_DIR=$(mktemp -d)
    cleanup_build_dir() {{
        rm -rf "$BUILD_DIR" 2>/dev/null || true
    }}
    trap cleanup_build_dir EXIT
    INTERNAL_REPO_URL={shlex.quote(internal_repo_url or "")}
    if [ -z "$INTERNAL_REPO_URL" ]; then
        echo "ERROR: repo_url is required for internal SWEAP Docker builds"
        exit 1
    fi
    echo "[git] Cloning internal SWEAP repo $INTERNAL_REPO_URL as Docker build context..."
    git clone --no-checkout "$INTERNAL_REPO_URL" "$BUILD_DIR/repo"
    cd "$BUILD_DIR/repo"
    echo "[git] Checking out base commit {shlex.quote(base_commit)} before Docker build..."
    git checkout {shlex.quote(base_commit)}
    BASE_IMAGE="hilbench-internal-sweap-base:$(date +%s)-$$"
    DOCKER_BUILDKIT=1 docker build -f "{remote_task_dir}/Dockerfile.internal" -t "$BASE_IMAGE" .
    cp "{remote_task_dir}/install_internal_sweap_test_runners.sh" "$BUILD_DIR/install_internal_sweap_test_runners.sh"
    cat > "$BUILD_DIR/Dockerfile.internal-runner" <<EOF
FROM $BASE_IMAGE
USER root
COPY install_internal_sweap_test_runners.sh /tmp/install_internal_sweap_test_runners.sh
RUN /bin/sh /tmp/install_internal_sweap_test_runners.sh && rm -f /tmp/install_internal_sweap_test_runners.sh
EOF
    cd "$BUILD_DIR"
    DOCKER_BUILDKIT=1 docker build -f Dockerfile.internal-runner -t "$SELECTED_IMAGE" .
else
# Check if image already exists locally to avoid Docker Hub rate limit hits
SELECTED_IMAGE=""
for candidate in {candidate_literals}; do
    if docker image inspect "$candidate" > /dev/null 2>&1; then
        SELECTED_IMAGE="$candidate"
        echo "[docker] Image $candidate already exists locally, skipping pull"
        break
    fi
done

if [ -z "$SELECTED_IMAGE" ]; then
    # Login to Docker Hub if credentials available (for higher rate limits)
    DOCKERHUB_USERNAME='{dockerhub_username}'
    DOCKERHUB_TOKEN='{dockerhub_token}'
    if [ -n "$DOCKERHUB_USERNAME" ] && [ -n "$DOCKERHUB_TOKEN" ]; then
        echo "[docker] Logging in to Docker Hub..."
        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin 2>/dev/null && \\
            echo "[docker] Logged in to Docker Hub" || \\
            echo "[docker] Docker Hub login failed, continuing with unauthenticated pulls"
    fi

    for candidate in {candidate_literals}; do
        echo "[docker] Pulling image $candidate..."
        if docker pull "$candidate" 2>&1; then
            SELECTED_IMAGE="$candidate"
            break
        fi
    done
fi
fi

if [ -z "$SELECTED_IMAGE" ]; then
    echo "ERROR: failed to pull any candidate image"
    exit 1
fi
echo "[docker] Using image $SELECTED_IMAGE"

echo "[docker] Creating temporary container..."
CONTAINER_ID=$(docker create "$SELECTED_IMAGE")

# Validate container was created
if [ -z "$CONTAINER_ID" ]; then
    echo "ERROR: docker create returned empty container ID"
    exit 1
fi
echo "[docker] Container ID: $CONTAINER_ID"

# Verify container exists before proceeding
if ! docker inspect "$CONTAINER_ID" > /dev/null 2>&1; then
    echo "ERROR: Container $CONTAINER_ID does not exist after creation"
    exit 1
fi

echo "[docker] Extracting /app to {remote_repo_dir}..."
mkdir -p {remote_repo_dir}
docker cp "$CONTAINER_ID:/app/." {remote_repo_dir} 2>&1

echo "[docker] Removing temporary container..."
docker rm "$CONTAINER_ID" 2>&1 || true

echo "[docker] Extraction complete"
"""
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"[Sandbox] Docker extract attempt {attempt + 1}/{max_retries}...")
                exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                    sandbox, "sudo", "bash", "-c", extract_script, timeout=1200
                )
                if exit_code == 0:
                    marker = "[docker] Using image "
                    combined_output = f"{stdout}\n{stderr}"
                    for line in combined_output.splitlines():
                        if line.startswith(marker):
                            resolved_image_name = line[len(marker) :].strip()
                            break
                    if resolved_image_name != docker_image_name:
                        logger.warning(
                            "[Sandbox] Using fallback Docker image tag alias: %s (primary=%s)",
                            resolved_image_name,
                            docker_image_name,
                        )
                    try:
                        verify_exit, verify_out, _ = await self._sandbox_exec_with_output(
                            sandbox,
                            "sudo",
                            "bash",
                            "-c",
                            f"cd {remote_repo_dir} && git log --oneline -1 && git rev-parse HEAD",
                            timeout=10,
                        )
                        if verify_exit == 0:
                            logger.info(
                                f"[Sandbox] Docker extraction OK, repo HEAD: {verify_out.strip()[:80]}"
                            )
                        else:
                            logger.info("[Sandbox] Docker extraction completed successfully")
                    except Exception:
                        logger.info("[Sandbox] Docker extraction completed successfully")
                    break
                else:
                    last_error = stderr or stdout or "Docker extraction failed"
                    if any(
                        x in last_error.lower()
                        for x in [
                            "500",
                            "503",
                            "rate limit",
                            "timeout",
                            "connection",
                            "toomanyrequests",
                            "no such container",
                            "no such image",
                            "daemon",
                        ]
                    ):
                        if attempt < max_retries - 1:
                            delay = 10 * (attempt + 1)  # 10s, 20s, 30s
                            logger.warning(
                                f"[Sandbox] Extract attempt {attempt + 1} failed with retryable error, retrying in {delay}s: {last_error[:200]}"
                            )
                            logger.info("[Sandbox] Cleaning up partial extraction before retry")
                            await self._sandbox_exec_with_output(
                                sandbox, "sudo", "rm", "-rf", remote_repo_dir
                            )
                            await asyncio.sleep(delay)
                            continue
                    # Non-retryable error
                    logger.error(
                        f"[Sandbox] Docker extraction failed:\n{last_error[:STDERR_LIMIT]}"
                    )
                    raise RuntimeError(
                        f"Failed to extract repo from Docker image in VM: {last_error[:STDERR_LIMIT]}"
                    )
            except asyncio.TimeoutError:
                last_error = f"Docker extraction timed out after 20 minutes (attempt {attempt + 1})"
                if attempt < max_retries - 1:
                    logger.warning(f"[Sandbox] {last_error}, retrying...")
                    await self._sandbox_exec_with_output(
                        sandbox, "sudo", "rm", "-rf", remote_repo_dir
                    )
                    continue
                logger.error(
                    f"[Sandbox] Docker extraction timed out after {max_retries} attempts for {docker_image_name}"
                )
                raise RuntimeError(
                    f"Docker extraction timed out after {max_retries} attempts for {docker_image_name}"
                )
        else:
            # All retries exhausted
            logger.error(
                f"[Sandbox] Docker extraction failed after {max_retries} attempts:\n{last_error[:STDERR_LIMIT]}"
            )
            raise RuntimeError(
                f"Failed to extract repo from Docker image after {max_retries} attempts: {last_error[:STDERR_LIMIT]}"
            )

        logger.info("[Sandbox] Docker extraction complete. Checking out base commit...")
        checkout_script = f"""
set -e
cd {remote_repo_dir}
git checkout {base_commit} 2>&1
git config user.email "PLACEHOLDER_EMAIL"
git config user.name "PLACEHOLDER_USER"
echo "[git] Checkout complete"
"""
        try:
            exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", checkout_script, timeout=120
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Git checkout timed out after 2 minutes")
        if exit_code != 0:
            error_msg = stderr or stdout or "Git checkout failed"
            if any(
                indicator in error_msg.lower()
                for indicator in [
                    "did not match any file",
                    "pathspec",
                    "not a valid object name",
                    "reference is not a tree",
                    "invalid reference",
                    "unknown revision",
                    "bad revision",
                ]
            ):
                if internal_repo_url is not None:
                    raise ValueError(
                        f"Internal SWEAP image {docker_image_name} does not contain base_commit "
                        f"'{base_commit}' in /app git state: {error_msg[:STDERR_LIMIT]}"
                    )
                if self.skip_validation:
                    raise RuntimeError(
                        f"Git checkout failed for base_commit '{base_commit}': {error_msg[:STDERR_LIMIT]}"
                    )
                raise SWEInputValidationError(
                    f"SWE INPUT VALIDATION FAILED: The base_commit '{base_commit}' does not exist "
                    f"in the repository extracted from Docker image. Please verify the commit hash "
                    f"is correct. Error: {error_msg[:STDERR_LIMIT]}"
                )
            raise RuntimeError(f"Git checkout failed: {error_msg[:STDERR_LIMIT]}")
        logger.info("[Sandbox] Base commit checkout complete")

        # Apply setup_patch if provided
        if setup_patch and setup_patch.strip():
            logger.info(f"[SANDBOX] Setup patch:\n{setup_patch}")
            setup_patch_data = setup_patch.encode("utf-8")
            await self._sandbox_transfer_file(
                sandbox, setup_patch_data, f"{remote_repo_dir}/_setup_patch.diff"
            )

            # Normalize patch line endings inside the VM (copy and paste of the non-sandbox version's normalization function)
            normalize_script = f"""
import os
import sys

repo_dir = "{remote_repo_dir}"
patch_path = os.path.join(repo_dir, "_setup_patch.diff")

with open(patch_path, "r", encoding="utf-8", errors="replace") as f:
    patch_content = f.read()

if not patch_content.strip():
    sys.exit(0)

lines = patch_content.split("\\n")
result_lines = []
current_file = None
file_has_crlf = {{}}

for line in lines:
    # Always strip \\r from header lines to prevent path corruption
    if (
        line.startswith("diff --git ")
        or line.startswith("--- ")
        or line.startswith("+++ ")
        or line.startswith("index ")
        or line.startswith("@@ ")
    ):
        line = line.rstrip("\\r")

    if line.startswith("diff --git "):
        parts = line.split()
        if len(parts) >= 4:
            file_path = parts[3].rstrip("\\r")
            if file_path.startswith("b/"):
                file_path = file_path[2:]
            current_file = file_path
            # Detect file's line ending style
            target_path = os.path.join(repo_dir, file_path)
            if os.path.exists(target_path):
                try:
                    with open(target_path, "rb") as f:
                        content = f.read(8192)
                    file_has_crlf[file_path] = b"\\r\\n" in content
                except Exception:
                    file_has_crlf[file_path] = False
            else:
                file_has_crlf[file_path] = False
        result_lines.append(line)
        continue

    # Normalize context and added/removed lines to match file's line endings
    if current_file is not None:
        if line.startswith((" ", "+", "-")):
            if file_has_crlf.get(current_file, False):
                if not line.endswith("\\r"):
                    line = line + "\\r"
            else:
                if line.endswith("\\r"):
                    line = line[:-1]

    result_lines.append(line)

normalized = "\\n".join(result_lines)
with open(patch_path, "w", encoding="utf-8") as f:
    f.write(normalized)
"""
            logger.info("[Sandbox] Normalizing patch line endings for setup_patch...")
            try:
                exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                    sandbox, "sudo", "python3", "-c", normalize_script, timeout=30
                )
            except asyncio.TimeoutError:
                logger.warning("[Sandbox] Patch normalization timed out (non-fatal, proceeding)")
            else:
                if exit_code != 0:
                    logger.warning(
                        f"[Sandbox] Patch normalization failed (non-fatal): {stderr[:500]}"
                    )
                else:
                    logger.info("[Sandbox] Patch line endings normalized")

            try:
                patch_info_exit, patch_info_out, _ = await self._sandbox_exec_with_output(
                    sandbox,
                    "sudo",
                    "bash",
                    "-c",
                    f"cd {remote_repo_dir} && wc -c _setup_patch.diff && head -3 _setup_patch.diff",
                    timeout=10,
                )
                if patch_info_exit == 0:
                    logger.info(f"[Sandbox] setup_patch info: {patch_info_out.strip()[:200]}")
            except Exception:
                pass

            apply_script = f"""
set -e
cd {remote_repo_dir}
git apply -v _setup_patch.diff
rm _setup_patch.diff
git add -A
git commit --no-verify --allow-empty -m "Apply setup patch"
"""
            logger.info("[Sandbox] Applying setup_patch to repo")
            try:
                exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                    sandbox, "sudo", "bash", "-c", apply_script, timeout=120  # 2 min timeout
                )
            except asyncio.TimeoutError:
                logger.error("[Sandbox] Setup patch apply timed out after 2 minutes")
                raise RuntimeError("Setup patch apply timed out after 2 minutes")
            if exit_code != 0:
                error_msg = stderr or stdout or "Setup patch apply failed"
                logger.error(f"[Sandbox] Setup patch failed:\n{error_msg[:STDERR_LIMIT]}")
                if self.skip_validation:
                    raise RuntimeError(f"setup_patch failed to apply: {error_msg[:STDERR_LIMIT]}")
                raise SWEInputValidationError(
                    f"SWE INPUT VALIDATION FAILED: setup_patch failed to apply. Potential causes:\n"
                    f"1. Line ending mismatches (CRLF vs LF)\n"
                    f"2. Patch generated against a different version of the file\n"
                    f"3. Context lines don't match the actual file content\n\n"
                    f"Error details: {error_msg[:STDERR_LIMIT]}"
                )
            logger.info("[Sandbox] Setup patch applied successfully")

        # Disable automatic git gc early (needed even if skip_squash for later operations)
        disable_gc_script = f"""
cd {remote_repo_dir}
git config gc.auto 0
"""
        try:
            await self._sandbox_exec_with_output(
                sandbox, "sudo", "bash", "-c", disable_gc_script, timeout=30
            )
        except Exception:
            pass  # Non-fatal
        # Nuclear git reset (can be skipped for fast validation; deferred to post-validation path)
        if skip_squash:
            logger.info(
                "[Sandbox] Skipping git nuclear reset (skip_squash=True, will be done after validation)"
            )
        else:
            logger.info("[Sandbox] Performing nuclear git reset: wiping .git and reinitializing...")
            # Wipe the entire .git directory so NO git artifacts survive (no branch refs, no
            # remote-tracking refs, no reflog, no object store). The old commit-tree approach
            # left these intact, letting agents read the solution via "git log --all" or
            # "git show <hash>". Nuclear reset is atomic and needs no gc or background processes.
            nuclear_reset_script = f"""
set -e
cd {remote_repo_dir}
rm -rf .git
git init -b master
git config user.email "PLACEHOLDER_EMAIL"
git config user.name "PLACEHOLDER_USER"
git add -A
git commit --no-gpg-sign -m "Initial commit for SWE-agent"
COMMIT_COUNT=$(git rev-list --count HEAD)
if [ "$COMMIT_COUNT" != "1" ]; then
    echo "ERROR: Expected 1 commit after nuclear reset, got $COMMIT_COUNT" >&2
    exit 1
fi
echo "[git] Nuclear reset complete; HEAD=$(git rev-parse --short HEAD), commits=$COMMIT_COUNT"
"""
            try:
                exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                    sandbox, "sudo", "bash", "-c", nuclear_reset_script, timeout=120
                )
            except asyncio.TimeoutError:
                logger.warning("[Sandbox] Git nuclear reset timed out after 2 minutes")
                exit_code = -1
                stderr = "timeout"
            if exit_code != 0:
                logger.warning(
                    f"[Sandbox] Git nuclear reset failed (non-fatal): {stderr[:500] if exit_code != -1 else 'timeout'}"
                )
            else:
                logger.info(f"[Sandbox] Git nuclear reset complete: {stdout.strip()[-200:]}")

        # Note: test_patch normalization happens later during metadata update

        # Log validation inputs before they are checked
        logger.info(f"[SANDBOX] Tests to pass:\n{tests_to_pass}")
        logger.info(f"[SANDBOX] Test patch:\n{test_patch}")
        logger.info(f"[SANDBOX] LLM extracted functions:\n{llm_extracted_functions}")

        logger.info(
            "[Sandbox] Verifying that test files exist in the repo or will be added by test_patch"
        )
        test_file_check_script = f'''
import json
import os
import sys
from pathlib import Path

remote_task_dir = "{remote_task_dir}"
remote_repo_dir = "{remote_repo_dir}"

# Load metadata to get tests_to_pass, test_patch, and language
metadata_path = os.path.join(remote_task_dir, "metadata.json")
if not os.path.exists(metadata_path):
    print("WARNING: metadata.json not found, skipping test file validation")
    sys.exit(0)

with open(metadata_path, "r") as f:
    metadata = json.load(f)

# Check skip_validation flag first
if metadata.get("skip_validation", False):
    print("skip_validation=True, skipping all input validation checks")
    sys.exit(0)

# Get tests_to_pass from swe_bench_metadata
swe_bench_metadata = metadata.get("swe_bench_metadata", {{}})
tests_to_pass = swe_bench_metadata.get("FAIL_TO_PASS", [])
test_patch = metadata.get("test_patch", "")
language = metadata.get("language", "python")
llm_extracted_functions = metadata.get("llm_extracted_functions", [])

if not tests_to_pass:
    print("No tests_to_pass found, skipping validation")
    sys.exit(0)

import re

# Helper functions (exact same logic as non-sandbox version)
def extract_test_file_path(test_name, lang):
    """Extract file path from test name. Preserves original path format."""
    path_before_pytest_sep = test_name.split("::", 1)[0]
    generic_path_extensions = (
        ".py", ".java", ".rs", ".go", ".js", ".ts", ".jsx", ".tsx",
        ".mjs", ".cjs", ".mts", ".cts", ".cc", ".cpp", ".cxx", ".c",
        ".h", ".hh", ".hpp", ".hxx",
    )
    if (
        ("::" in test_name or "/" in path_before_pytest_sep or "\\\\" in path_before_pytest_sep)
        and path_before_pytest_sep.lower().endswith(generic_path_extensions)
    ):
        return path_before_pytest_sep
    if lang == "python":
        return test_name.split("::")[0] if "::" in test_name else test_name
    elif lang == "go":
        if "/" in test_name and "_test.go" in test_name.lower():
            return test_name
        return None  # Go function-only names handled separately
    elif lang == "java":
        path_part = test_name
        for separator in ("::", "#"):
            if separator in path_part:
                path_part = path_part.split(separator, 1)[0]
        if " " in path_part and "/" not in path_part and "\\\\" not in path_part:
            return None
        if "/" in path_part or "\\\\" in path_part or path_part.endswith(".java"):
            return path_part
        return None
    elif lang in ("rust", "rs"):
        path_part = test_name
        if ".rs" in path_part and "::" in path_part:
            rs_idx = path_part.find(".rs")
            path_part = path_part[: rs_idx + len(".rs")]
        if "/" in path_part or "\\\\" in path_part or path_part.endswith(".rs"):
            return path_part
        return None
    elif lang in ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus"):
        cpp_extensions = (".cc", ".cpp", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx")
        path_part = test_name
        for separator in ("::", "#"):
            if separator in path_part:
                candidate = path_part.split(separator, 1)[0]
                if any(candidate.lower().endswith(ext) for ext in cpp_extensions):
                    path_part = candidate
                    break
        if "/" in path_part or "\\\\" in path_part or any(path_part.lower().endswith(ext) for ext in cpp_extensions):
            return path_part
        return None
    else:
        # JS/TS tests may use " | ", " |", "| ", or "|" to separate file from description
        path_part = test_name
        if "|" in test_name:
            path_part = test_name.split("|")[0].strip()
        if "/" in path_part or path_part.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
            return path_part
        return None

def is_go_function_name(test_name):
    """Check if test_name looks like a Go test/benchmark/example/fuzz function name."""
    if "/" in test_name or "\\\\" in test_name:
        return False
    # Test, Benchmark, Fuzz must be followed by uppercase or underscore
    # Example can stand alone or have method
    return bool(re.match(r"^(Test|Benchmark|Fuzz)[A-Z_]", test_name)) or bool(
        re.match(r"^Example([A-Z_]|$)", test_name)
    )

def extract_go_functions_from_patch(patch_content):
    """Extract Go test/benchmark/example/fuzz function names from patch content."""
    pattern = r"^\\+\\s*func\\s+(?:\\([^)]+\\)\\s+)?((Test|Benchmark|Example|Fuzz)[A-Za-z0-9_]*)\\s*\\("
    functions = set()
    for line in patch_content.split("\\n"):
        match = re.match(pattern, line)
        if match:
            functions.add(match.group(1))
    return functions

def check_go_function_in_patch(test_name, patch_content):
    """Check if a Go test function will be added by test_patch."""
    if f"func {{test_name}}(" in patch_content:
        return True
    if re.search(rf"func\\s+\\([^)]+\\)\\s+{{re.escape(test_name)}}\\s*\\(", patch_content):
        return True
    return False

# Cache for Go test functions in repo (scanned once, reused for all checks)
_go_repo_functions_cache = None

def get_go_functions_in_repo(repo_dir_path):
    """
    Scan all *_test.go files in repo and extract test/benchmark/example/fuzz function names.
    Results are cached for efficiency on large repos.
    """
    global _go_repo_functions_cache
    if _go_repo_functions_cache is not None:
        return _go_repo_functions_cache

    _go_repo_functions_cache = set()
    pattern = re.compile(
        r"^func\\s+(?:\\([^)]+\\)\\s+)?((Test|Benchmark|Example|Fuzz)[A-Za-z0-9_]*)\\s*\\("
    )
    try:
        for test_file in repo_dir_path.rglob("*_test.go"):
            try:
                content = test_file.read_text(errors="replace")
                for line in content.split("\\n"):
                    match = pattern.match(line.strip())
                    if match:
                        _go_repo_functions_cache.add(match.group(1))
            except Exception:
                pass
    except Exception:
        pass
    return _go_repo_functions_cache

def check_go_function_exists(test_name, patch_content, repo_dir_path):
    """
    Check if a Go test function exists in test_patch OR repo.
    Checks patch first (cheap), then repo (cached) for efficiency.
    """
    # Strategy 1: Check test_patch first (cheap string search)
    if check_go_function_in_patch(test_name, patch_content):
        return True
    # Strategy 2: Check repo (scanned and cached)
    return test_name in get_go_functions_in_repo(repo_dir_path)

def is_java_test_identifier(test_name):
    """Check if a Java test identifier is class/method-like rather than a file path."""
    if not test_name or "/" in test_name or "\\\\" in test_name or test_name.endswith(".java"):
        return False
    if test_name.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z_$][A-Za-z0-9_$.]*(?:(?:#|::)[A-Za-z_$][A-Za-z0-9_$]*)?$", test_name))

def java_identifier_matches(required, observed):
    if required == observed:
        return True
    req = required.replace("::", "#")
    obs = observed.replace("::", "#")
    if req == obs:
        return True
    req_tail = req.split(".")[-1]
    obs_tail = obs.split(".")[-1]
    if req_tail == obs_tail:
        return True
    req_dot_parts = req.replace("#", ".").split(".")
    obs_simple = obs.replace("#", ".").split(".")[-1]
    if len(req_dot_parts) >= 2 and obs_simple == req_dot_parts[-2]:
        return True
    if "#" in req_tail or "#" in obs_tail:
        req_class, _, req_method = req_tail.partition("#")
        obs_class, _, obs_method = obs_tail.partition("#")
        if not req_method and obs_method and req_class == obs_method:
            req_package_parts = req.replace("#", ".").split(".")
            req_owner = req_package_parts[-2] if len(req_package_parts) >= 2 else ""
            return obs_class == req_owner or req.endswith("." + obs_class)
        if req_method and not obs_method and obs_class == req_method:
            obs_package_parts = obs.replace("#", ".").split(".")
            obs_owner = obs_package_parts[-2] if len(obs_package_parts) >= 2 else ""
            return req_class == obs_owner or obs.endswith("." + req_class)
        if req_method and obs_method and req_method == obs_method:
            return req_class == obs_class or req.endswith("." + obs_class) or obs.endswith("." + req_class)
    return False

def extract_java_test_identifiers_from_patch(patch_content):
    identifiers = set()
    current_class = None
    pending_test_annotation = False
    class_pattern = re.compile(r"^\\+\\s*(?:public\\s+)?(?:class|interface|enum)\\s+([A-Za-z_$][A-Za-z0-9_$]*)")
    method_pattern = re.compile(r"^\\+\\s*(?:public|protected|private)?\\s*(?:static\\s+)?(?:[\\w<>\\[\\],.?]+\\s+)+([A-Za-z_$][A-Za-z0-9_$]*)\\s*\\(")
    for line in patch_content.split("\\n"):
        class_match = class_pattern.match(line)
        if class_match:
            current_class = class_match.group(1)
            if current_class.endswith(("Test", "Tests", "IT")):
                identifiers.add(current_class)
            continue
        if line.lstrip("+").strip().startswith("@Test"):
            pending_test_annotation = True
            continue
        method_match = method_pattern.match(line)
        if method_match:
            method_name = method_match.group(1)
            if pending_test_annotation or method_name.startswith("test"):
                identifiers.add(method_name)
                if current_class:
                    identifiers.add(f"{{current_class}}#{{method_name}}")
                    identifiers.add(f"{{current_class}}::{{method_name}}")
            pending_test_annotation = False
    return identifiers

def is_java_test_file(filename):
    normalized = filename.lstrip("/")
    basename = normalized.split("/")[-1] if "/" in normalized else normalized
    path_lower = normalized.lower()
    if not basename.endswith(".java"):
        return False
    if basename.endswith(("Test.java", "Tests.java", "IT.java")):
        return True
    path_parts = path_lower.split("/")
    return ("src" in path_parts and "test" in path_parts) or any(part in ("test", "tests") for part in path_parts[:-1])

_java_repo_identifiers_cache = None

def get_java_identifiers_in_repo(repo_dir_path):
    global _java_repo_identifiers_cache
    if _java_repo_identifiers_cache is not None:
        return _java_repo_identifiers_cache
    _java_repo_identifiers_cache = set()
    class_pattern = re.compile(r"^\\s*(?:public\\s+)?(?:class|interface|enum)\\s+([A-Za-z_$][A-Za-z0-9_$]*)")
    method_pattern = re.compile(r"^\\s*(?:public|protected|private)?\\s*(?:static\\s+)?(?:[\\w<>\\[\\],.?]+\\s+)+([A-Za-z_$][A-Za-z0-9_$]*)\\s*\\(")
    try:
        java_files = repo_dir_path.rglob("*.java")
    except Exception:
        return _java_repo_identifiers_cache
    for test_file in java_files:
        try:
            rel_path = str(test_file.relative_to(repo_dir_path))
        except Exception:
            rel_path = str(test_file)
        if not is_java_test_file(rel_path):
            continue
        try:
            content = test_file.read_text(errors="replace")
        except Exception:
            continue
        current_class = test_file.stem
        _java_repo_identifiers_cache.add(current_class)
        pending_test_annotation = False
        for line in content.split("\\n"):
            class_match = class_pattern.match(line)
            if class_match:
                current_class = class_match.group(1)
                _java_repo_identifiers_cache.add(current_class)
                continue
            if line.strip().startswith("@Test"):
                pending_test_annotation = True
                continue
            method_match = method_pattern.match(line)
            if method_match:
                method_name = method_match.group(1)
                if pending_test_annotation or method_name.startswith("test"):
                    _java_repo_identifiers_cache.add(method_name)
                    _java_repo_identifiers_cache.add(f"{{current_class}}#{{method_name}}")
                    _java_repo_identifiers_cache.add(f"{{current_class}}::{{method_name}}")
                pending_test_annotation = False
    return _java_repo_identifiers_cache

def check_java_identifier_exists(test_name, patch_content, repo_dir_path):
    patch_identifiers = extract_java_test_identifiers_from_patch(patch_content)
    if any(java_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
        return True
    return any(java_identifier_matches(test_name, identifier) for identifier in get_java_identifiers_in_repo(repo_dir_path))

def is_rust_test_identifier(test_name):
    if not test_name or "/" in test_name or "\\\\" in test_name or test_name.endswith(".rs"):
        return False
    if test_name.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$", test_name))

def rust_identifier_matches(required, observed):
    if "|" in required or "|" in observed:
        return False
    req = required.replace("/", "::")
    obs = observed.replace("/", "::")
    if req == obs:
        return True
    req_tail = req.split("::")[-1]
    obs_tail = obs.split("::")[-1]
    if req_tail == obs_tail:
        return True
    return req.endswith("::" + obs) or obs.endswith("::" + req)

def extract_rust_test_identifiers_from_patch(patch_content):
    identifiers = set()
    pending_test_attr = False
    module_stack = []
    module_pattern = re.compile(r"^\\+\\s*(?:pub\\s+)?mod\\s+([A-Za-z_][A-Za-z0-9_]*)\\s*\\{{?")
    fn_pattern = re.compile(r"^\\+\\s*(?:pub(?:\\([^)]*\\))?\\s+)?(?:async\\s+)?fn\\s+([A-Za-z_][A-Za-z0-9_]*)\\s*\\(")
    for line in patch_content.split("\\n"):
        stripped = line.lstrip("+").strip()
        mod_match = module_pattern.match(line)
        if mod_match:
            module_stack.append(mod_match.group(1))
            continue
        if stripped.startswith("#[") and "test" in stripped:
            pending_test_attr = True
            continue
        fn_match = fn_pattern.match(line)
        if fn_match:
            fn_name = fn_match.group(1)
            if pending_test_attr or fn_name.startswith("test_"):
                identifiers.add(fn_name)
                if module_stack:
                    identifiers.add("::".join(module_stack + [fn_name]))
            pending_test_attr = False
    return identifiers

def check_rust_identifier_exists(test_name, patch_content, repo_dir_path):
    patch_identifiers = extract_rust_test_identifiers_from_patch(patch_content)
    if any(rust_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
        return True
    attr_pattern = re.compile(r"^\\s*#\\[[^\\]]*test[^\\]]*\\]")
    fn_pattern = re.compile(r"^\\s*(?:pub(?:\\([^)]*\\))?\\s+)?(?:async\\s+)?fn\\s+([A-Za-z_][A-Za-z0-9_]*)\\s*\\(")
    try:
        rust_files = repo_dir_path.rglob("*.rs")
    except Exception:
        return False
    for test_file in rust_files:
        try:
            content = test_file.read_text(errors="replace")
        except Exception:
            continue
        pending_test_attr = False
        for line in content.split("\\n"):
            if attr_pattern.match(line):
                pending_test_attr = True
                continue
            fn_match = fn_pattern.match(line)
            if fn_match:
                fn_name = fn_match.group(1)
                if (pending_test_attr or fn_name.startswith("test_")) and rust_identifier_matches(test_name, fn_name):
                    return True
                pending_test_attr = False
    return False

def is_cpp_test_identifier(test_name):
    cpp_extensions = (".cc", ".cpp", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx")
    if not test_name or "\\\\" in test_name or any(test_name.lower().endswith(ext) for ext in cpp_extensions):
        return False
    if test_name.startswith("-"):
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:(?:\\.|::|/)[A-Za-z_][A-Za-z0-9_]*)*$", test_name))

def cpp_identifier_matches(required, observed):
    if "|" in required or "|" in observed:
        return False
    req = required.replace("::", ".").replace("/", ".")
    obs = observed.replace("::", ".").replace("/", ".")
    if req == obs:
        return True
    req_parts = req.split(".")
    obs_parts = obs.split(".")
    if req_parts[-1] == obs_parts[-1]:
        if len(req_parts) == 1 or len(obs_parts) == 1:
            return True
        return req_parts[-2] == obs_parts[-2]
    return False

def extract_cpp_test_identifiers_from_patch(patch_content):
    identifiers = set()
    macro_pattern = re.compile(r"^\\+\\s*(?:TYPED_TEST|TEST_P|TEST_F|TEST)\\s*\\(\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*,\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*\\)")
    for line in patch_content.split("\\n"):
        macro_match = macro_pattern.match(line)
        if macro_match:
            suite, test = macro_match.groups()
            identifiers.add(test)
            identifiers.add(f"{{suite}}.{{test}}")
            identifiers.add(f"{{suite}}::{{test}}")
            identifiers.add(f"{{suite}}/{{test}}")
    return identifiers

def check_cpp_identifier_exists(test_name, patch_content, repo_dir_path):
    patch_identifiers = extract_cpp_test_identifiers_from_patch(patch_content)
    if any(cpp_identifier_matches(test_name, identifier) for identifier in patch_identifiers):
        return True
    cpp_extensions = (".cc", ".cpp", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx")
    macro_pattern = re.compile(r"(?:TYPED_TEST|TEST_P|TEST_F|TEST)\\s*\\(\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*,\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*\\)")
    try:
        cpp_files = [path for path in repo_dir_path.rglob("*") if path.is_file() and any(path.name.lower().endswith(ext) for ext in cpp_extensions)]
    except Exception:
        return False
    for test_file in cpp_files:
        try:
            content = test_file.read_text(errors="replace")
        except Exception:
            continue
        for suite, test in macro_pattern.findall(content):
            if (
                cpp_identifier_matches(test_name, test)
                or cpp_identifier_matches(test_name, f"{{suite}}.{{test}}")
                or cpp_identifier_matches(test_name, f"{{suite}}::{{test}}")
                or cpp_identifier_matches(test_name, f"{{suite}}/{{test}}")
            ):
                return True
    return False

def extract_files_from_patch(patch_content):
    files = set()
    for line in patch_content.split("\\n"):
        if line.startswith("+++ b/"):
            files.add(line[6:])
        elif line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4:
                files.add(parts[3][2:])
    return files

def path_matches_suffix(full_path, suffix_path):
    """Check if full_path ends with suffix_path as a proper path suffix."""
    full_normalized = full_path.lstrip("/")
    suffix_normalized = suffix_path.lstrip("/")
    if full_normalized == suffix_normalized:
        return True
    return full_normalized.endswith("/" + suffix_normalized)

def check_test_file_exists(repo_dir_path, test_file, patch_files):
    """Check if a test file exists in repo or will be added by test_patch."""
    normalized_path = test_file.lstrip("/")

    # Strategy 1: Check in test_patch_files (exact match)
    if test_file in patch_files or normalized_path in patch_files:
        return True

    # Strategy 2: Check in test_patch_files (suffix match for monorepos)
    for patch_file in patch_files:
        if path_matches_suffix(patch_file, normalized_path):
            return True

    # Strategy 3: Check as relative path from repo_dir
    if (repo_dir_path / normalized_path).exists():
        return True

    # Strategy 4: Check as absolute path (if starts with /)
    if test_file.startswith("/") and Path(test_file).exists():
        return True

    # Strategy 5: Suffix match in repository (for monorepos)
    filename = Path(normalized_path).name
    try:
        for found_path in repo_dir_path.rglob(filename):
            rel_path = str(found_path.relative_to(repo_dir_path))
            if path_matches_suffix(rel_path, normalized_path):
                return True
    except Exception:
        pass

    return False

# Run validation
test_patch_files = extract_files_from_patch(test_patch) if test_patch else set()
files_to_validate = []
go_functions_to_validate = []
java_identifiers_to_validate = []
rust_identifiers_to_validate = []
cpp_identifiers_to_validate = []
for test_name in tests_to_pass:
    test_file = extract_test_file_path(test_name, language)
    if test_file is not None:
        files_to_validate.append(test_file)
    elif language == "go" and is_go_function_name(test_name):
        go_functions_to_validate.append(test_name)
    elif language == "java" and is_java_test_identifier(test_name):
        java_identifiers_to_validate.append(test_name)
    elif language in ("rust", "rs") and is_rust_test_identifier(test_name):
        continue
    elif language in ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus") and is_cpp_test_identifier(test_name):
        cpp_identifiers_to_validate.append(test_name)

missing_files = []
repo_dir_path = Path(remote_repo_dir)
for test_file in files_to_validate:
    if not check_test_file_exists(repo_dir_path, test_file, test_patch_files):
        missing_files.append(test_file)

# Validate Go function names exist in test_patch OR repo
missing_go_functions = []
for func_name in go_functions_to_validate:
    if not check_go_function_exists(func_name, test_patch, repo_dir_path):
        missing_go_functions.append(func_name)

missing_java_identifiers = []
for identifier in java_identifiers_to_validate:
    if not check_java_identifier_exists(identifier, test_patch, repo_dir_path):
        missing_java_identifiers.append(identifier)

missing_rust_identifiers = []
for identifier in rust_identifiers_to_validate:
    if not check_rust_identifier_exists(identifier, test_patch, repo_dir_path):
        missing_rust_identifiers.append(identifier)

missing_cpp_identifiers = []
for identifier in cpp_identifiers_to_validate:
    if not check_cpp_identifier_exists(identifier, test_patch, repo_dir_path):
        missing_cpp_identifiers.append(identifier)

if missing_files or missing_go_functions or missing_java_identifiers or missing_rust_identifiers or missing_cpp_identifiers:
    all_missing = missing_files + missing_go_functions + missing_java_identifiers + missing_rust_identifiers + missing_cpp_identifiers
    print("VALIDATION_ERROR:MISSING:{{" + json.dumps(all_missing) + "}}")
    sys.exit(1)

print(f"Verified all {{len(files_to_validate)}} files, {{len(go_functions_to_validate)}} Go functions, {{len(java_identifiers_to_validate)}} Java identifiers, {{len(rust_identifiers_to_validate)}} Rust identifiers, and {{len(cpp_identifiers_to_validate)}} C++ identifiers exist")

# Check 2: Verify tests in test_patch are in tests_to_pass
def is_test_file(filename, lang):
    \"\"\"
    Check if a file is a test file that should be validated against tests_to_pass.
    
    Uses an allowlist approach but with expanded patterns to handle
    various project conventions.
    \"\"\"
    normalized = filename.lstrip("/")
    basename = normalized.split("/")[-1] if "/" in normalized else normalized
    path_lower = normalized.lower()
    
    if lang == "python":
        if not basename.endswith(".py"):
            return False
        # pytest default discovery patterns (directory doesn't matter):
        # - test_*.py (prefix)
        # - *_test.py (suffix)
        basename_lower = basename.lower()
        if basename_lower.startswith("test_"):
            return True
        if basename_lower.endswith("_test.py"):
            return True
        return False
    
    elif lang == "go":
        # Go REQUIRES *_test.go suffix - this is a tooling requirement
        return basename.endswith("_test.go")

    elif lang == "java":
        return is_java_test_file(filename)

    elif lang in ("rust", "rs"):
        if not basename.lower().endswith(".rs"):
            return False
        if basename.lower().endswith("_test.rs"):
            return True
        return any(part in ("test", "tests") for part in path_lower.split("/")[:-1])

    elif lang in ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus"):
        cpp_extensions = (".cc", ".cpp", ".cxx", ".c", ".h", ".hh", ".hpp", ".hxx")
        basename_lower = basename.lower()
        if not any(basename_lower.endswith(ext) for ext in cpp_extensions):
            return False
        if any(marker in basename_lower for marker in ("_test.", "_tests.", "test_", "tests_", ".test.", ".spec.")):
            return True
        if basename.endswith(("Test.cc", "Test.cpp", "Test.cxx", "Tests.cc", "Tests.cpp", "Tests.cxx")):
            return True
        return any(part in ("test", "tests") for part in path_lower.split("/")[:-1])
    
    else:
        # JS/TS test files
        # Check file extension first - must be a JS/TS file
        js_ts_extensions = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
        if not any(basename.endswith(ext) for ext in js_ts_extensions):
            return False
        
        # Pattern 1: Jest/Vitest naming convention (*.test.js, *.spec.js)
        test_suffixes = (".test.", ".spec.")
        if any(suf in basename.lower() for suf in test_suffixes):
            return True
        
        # Pattern 2: Jest __tests__ directory convention
        if "/__tests__/" in path_lower or path_lower.startswith("__tests__/"):
            return True
        
        # Pattern 3: Mocha convention - files in test/ or tests/ directory
        # (documented in custom_eval.py: "test/user.js | User socket methods...")
        path_parts = path_lower.split("/")
        for part in path_parts[:-1]:  # Check directory parts, not filename
            if part in ("test", "tests"):
                return True
        
        return False

def check_patch_file_in_tests_to_pass(patch_file, tests, lang):
    \"\"\"
    Check if a file in test_patch is covered by tests_to_pass.
    
    For Go _test.go files: Returns True to skip file-level check.
    Go test functions are validated separately via check_go_function_in_tests_to_pass.
    
    For all other files: Check if tests_to_pass has a matching file path.
    \"\"\"
    patch_normalized = patch_file.lstrip("/")
    
    # For Go, skip file-level check for _test.go files
    # Go test coverage is validated via function names separately
    if lang == "go" and patch_normalized.endswith("_test.go"):
        return True  # Will be validated via function check
    if lang in ("java", "rust", "rs", "c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus"):
        return True  # Will be validated via language-specific identifier checks
    
    for test_name in tests:
        test_file = extract_test_file_path(test_name, lang)
        if test_file is None:
            continue
        test_normalized = test_file.lstrip("/")
        # Exact match
        if patch_normalized == test_normalized:
            return True
        # Suffix match (monorepo: patch has full path, test has short path)
        if path_matches_suffix(patch_normalized, test_normalized):
            return True
        # Reverse suffix match (test has full path, patch has short path)
        if path_matches_suffix(test_normalized, patch_normalized):
            return True
    return False

def check_go_function_in_tests_to_pass(func_name, tests):
    """
    Check if a Go function name from test_patch is in tests_to_pass.
    
    A function is covered if:
    1. Exact match: func_name == test_name
    2. Function is parent of subtest: test_name starts with func_name + "/"
       (e.g., TestFoo covers TestFoo/SubA, TestFoo/SubB)
    """
    for test_name in tests:
        if func_name == test_name:
            return True
        if test_name.startswith(func_name + "/"):
            return True
    return False

# Filter to only check actual test files (not fixtures, configs, etc.)
test_files_in_patch = [f for f in test_patch_files if is_test_file(f, language)]
non_test_files = len(test_patch_files) - len(test_files_in_patch)
if non_test_files > 0:
    print(f"Skipping {{non_test_files}} non-test files in test_patch (fixtures, configs, etc.)")

uncovered_patch_files = []
for patch_file in test_files_in_patch:
    if not check_patch_file_in_tests_to_pass(patch_file, tests_to_pass, language):
        uncovered_patch_files.append(patch_file)

# For Go, also check functions
uncovered_go_functions = []
if language == "go" and test_patch:
    patch_go_functions = extract_go_functions_from_patch(test_patch)
    for func_name in patch_go_functions:
        if not check_go_function_in_tests_to_pass(func_name, tests_to_pass):
            uncovered_go_functions.append(func_name)

def check_language_identifier_in_tests_to_pass(identifier, tests, lang):
    for test_name in tests:
        if lang == "java" and java_identifier_matches(test_name, identifier):
            return True
        if lang in ("rust", "rs") and rust_identifier_matches(test_name, identifier):
            return True
        if lang in ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus") and cpp_identifier_matches(test_name, identifier):
            return True
    return False

uncovered_language_identifiers = []
if test_patch and language in ("java", "rust", "rs", "c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus"):
    if language == "java":
        patch_language_identifiers = extract_java_test_identifiers_from_patch(test_patch)
    elif language in ("rust", "rs"):
        patch_language_identifiers = extract_rust_test_identifiers_from_patch(test_patch)
    else:
        patch_language_identifiers = extract_cpp_test_identifiers_from_patch(test_patch)
    for identifier in sorted(patch_language_identifiers):
        if not check_language_identifier_in_tests_to_pass(identifier, tests_to_pass, language):
            uncovered_language_identifiers.append(identifier)

# Helper to check if a tests_to_pass entry is file-level (covers all functions)
def is_file_level_entry(test_name):
    \"\"\"
    Check if a tests_to_pass entry is file-level (covers all functions in the file).
    
    File-level entries don't specify a specific function:
    - Python: no '::' (e.g., 'tests/test_auth.py')
    - JS/TS: no '|' (e.g., 'test/auth.test.js')
    - Go: ends with '_test.go' (e.g., 'pkg/auth_test.go')
    \"\"\"
    # Python: file-level if no :: and ends with .py
    if test_name.endswith(".py") and "::" not in test_name:
        return True
    # JS/TS: file-level if no | and ends with test file extension
    js_ts_extensions = (".test.js", ".test.ts", ".test.jsx", ".test.tsx", 
                       ".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx")
    if any(test_name.endswith(ext) for ext in js_ts_extensions) and "|" not in test_name:
        return True
    # Go: file-level if ends with _test.go
    if test_name.endswith("_test.go"):
        return True
    # Java: file-level if a Java test file path is provided
    if is_java_test_file(test_name):
        return True
    return False

def has_full_file_level_coverage(tests, patch_files):
    \"\"\"
    Check if ALL files in test_patch are covered by file-level entries in tests_to_pass.
    
    Only returns True if EVERY file in patch_files has a matching file-level entry.
    If any patch file is not covered, returns False (function-level checking needed).
    \"\"\"
    if not patch_files:
        return False
    
    # Get all file-level entries from tests_to_pass
    file_level_tests = [t for t in tests if is_file_level_entry(t)]
    if not file_level_tests:
        return False
    
    # Check that EVERY patch file is covered by a file-level entry
    for patch_file in patch_files:
        file_is_covered = False
        for test_name in file_level_tests:
            if path_matches_suffix(test_name, patch_file) or path_matches_suffix(patch_file, test_name):
                file_is_covered = True
                break
        if not file_is_covered:
            return False
    
    return True

# LLM-extracted function check (augments file-level check for ALL languages)
def check_llm_function_in_tests_to_pass(func_name, tests):
    \"\"\"
    Check if an LLM-extracted function name is covered by tests_to_pass.
    
    This function uses EXACT matching (not substring) to avoid false positives.
    For example, 'test_user' should NOT match 'test_user_authentication'.
    \"\"\"
    # Extract base function name for parameterized tests (e.g., test_foo[param] -> test_foo)
    func_base = func_name.split("[")[0] if "[" in func_name else func_name
    func_base_lower = func_base.lower()
    
    for test_name in tests:
        # === JS/TS: Handle pipe-separated format FIRST ===
        # Check | before :: because JS descriptions can contain ::
        # but pytest paths would never contain |
        # file.test.js | describe | test name -> extract "test name" (last part)
        if "|" in test_name:
            # Get the last description part after all pipes
            test_desc = test_name.split("|")[-1].strip()
            test_desc_lower = test_desc.lower()
            # EXACT match on description (not substring)
            if func_base == test_desc or func_base_lower == test_desc_lower:
                return True
            # Also check if func matches the full description chain
            full_desc = " ".join(part.strip() for part in test_name.split("|")[1:])
            full_desc_lower = full_desc.lower()
            if func_base == full_desc or func_base_lower == full_desc_lower:
                return True
            # Check if test_desc ends with the func_base (for Mocha-style where
            # describe chains are concatenated with it() description, e.g.
            # "describe1 describe2 it_name" should match func_base="it_name")
            if test_desc_lower.endswith(" " + func_base_lower) or full_desc_lower.endswith(" " + func_base_lower):
                return True
            continue  # Don't fall through to other checks for pipe format
        
        # === PYTHON: Extract function name from pytest notation ===
        # path/file.py::ClassName::method_name -> method_name
        # path/file.py::test_func -> test_func
        # path/file.py::test_func[param] -> test_func (base)
        if "::" in test_name:
            test_func = test_name.rsplit("::", 1)[-1]
            test_func_base = test_func.split("[")[0] if "[" in test_func else test_func
            test_func_base_lower = test_func_base.lower()
            # EXACT match on function name (not substring)
            if func_base == test_func_base or func_base_lower == test_func_base_lower:
                return True
            continue  # Don't fall through to other checks for pytest format
        
        # === GO: Handle subtests with / separator ===
        # TestFoo/SubTest -> parent is TestFoo
        if "/" in test_name and not test_name.startswith("/"):
            test_parent = test_name.split("/")[0]
            test_parent_lower = test_parent.lower()
            # func_name matches the parent test
            if func_base == test_parent or func_base_lower == test_parent_lower:
                return True
        if "/" in func_name:
            func_parent = func_name.split("/")[0]
            func_parent_lower = func_parent.lower()
            # test_name matches the parent of func_name
            test_base = test_name.split("[")[0] if "[" in test_name else test_name
            test_base_lower = test_base.lower()
            if func_parent == test_base or func_parent_lower == test_base_lower:
                return True

        # === JAVA: Handle Class#method and Class::method formats ===
        if language == "java" and java_identifier_matches(test_name, func_base):
            return True
        if language in ("rust", "rs") and rust_identifier_matches(test_name, func_base):
            return True
        if language in ("c++", "cpp", "cxx", "cc", "cplusplus", "cpluscplus") and cpp_identifier_matches(test_name, func_base):
            return True
        if "#" in test_name or "::" in test_name:
            java_method = test_name.replace("::", "#").rsplit("#", 1)[-1]
            java_method_base = java_method.split("[")[0] if "[" in java_method else java_method
            java_method_base_lower = java_method_base.lower()
            if func_base == java_method_base or func_base_lower == java_method_base_lower:
                return True
        
        # === EXACT match on the full test name or base ===
        # This handles simple cases like test_name = "test_foo" or "TestBar"
        test_base = test_name.split("[")[0] if "[" in test_name else test_name
        test_base_lower = test_base.lower()
        if func_base == test_base or func_base_lower == test_base_lower:
            return True
    
    return False

# Only skip function-level checking if ALL files in test_patch are covered
# by file-level entries in tests_to_pass. If any file is uncovered,
# we must do function-level checking.
uncovered_llm_functions = []
if has_full_file_level_coverage(tests_to_pass, test_patch_files):
    print("Skipping function-level checking: ALL test_patch files are covered by file-level entries in tests_to_pass")
else:
    for func_name in llm_extracted_functions:
        if not check_llm_function_in_tests_to_pass(func_name, tests_to_pass):
            uncovered_llm_functions.append(func_name)
    
    if uncovered_llm_functions:
        print(f"LLM detected {{len(uncovered_llm_functions)}} uncovered functions: {{uncovered_llm_functions}}")

if uncovered_patch_files or uncovered_go_functions or uncovered_language_identifiers or uncovered_llm_functions:
    all_uncovered = uncovered_patch_files + uncovered_go_functions + uncovered_language_identifiers + uncovered_llm_functions
    # Deduplicate while preserving order
    seen = set()
    unique_uncovered = []
    for item in all_uncovered:
        if item not in seen:
            seen.add(item)
            unique_uncovered.append(item)
    print("VALIDATION_ERROR:UNCOVERED:{{" + json.dumps(unique_uncovered) + "}}")
    sys.exit(1)

print(f"Verified all tests in test_patch are covered by tests_to_pass")
sys.exit(0)
'''
        try:
            exit_code, stdout, stderr = await self._sandbox_exec_with_output(
                sandbox, "sudo", "python3", "-c", test_file_check_script, timeout=120
            )
        except asyncio.TimeoutError:
            logger.warning("[Sandbox] Test file validation timed out (non-fatal, proceeding)")
        else:
            if exit_code != 0:
                # Check if this is a validation error
                if "VALIDATION_ERROR:" in stdout:
                    try:
                        marker_start = stdout.index("VALIDATION_ERROR:")
                        marker_content = stdout[marker_start:]
                        json_start = marker_content.index("{[") + 1
                        json_end = marker_content.index("]}") + 1
                        files_json = marker_content[json_start:json_end]
                        files = json.loads(files_json)
                        # Determine error type from the marker
                        if "UNCOVERED" in marker_content[:json_start]:
                            raise SWEInputValidationError(
                                f"SWE INPUT VALIDATION FAILED: The following tests in test_patch are not covered by relevant tests: {files}. Add them to the relevant tests list or remove them from test_patch."
                            )
                        else:
                            raise SWEInputValidationError(
                                f"SWE INPUT VALIDATION FAILED: The following tests do not exist in the repository or test_patch: {files}. Please verify the test paths/names are correct."
                            )
                    except (ValueError, json.JSONDecodeError) as parse_err:
                        logger.error(
                            f"[Sandbox] Failed to parse validation error output: {parse_err}. "
                            f"Raw stdout: {stdout[:500]}"
                        )
                        raise SWEInputValidationError(
                            f"SWE INPUT VALIDATION FAILED: {stdout[:500]}"
                        )
                else:
                    logger.warning(
                        f"[Sandbox] Test file validation failed (non-fatal): {stderr or stdout}"
                    )
            else:
                logger.info("[Sandbox] Test file validation passed")
        return resolved_image_name





    def _validate_swe_fast(
        self,
        task_dir: Path,
        docker_image_name: str,
        golden_patch: str,
        test_patch: str,
        test_cmd: str,
        instance_id: str,
        tests_to_pass: list[str],
        language: str | None = None,
        timeout: int = 1800,
    ) -> Optional[str]:
        """
        1. Creates a container from the jefzda base image
        2. Copies the prepared repo (with setup_patch applied) into the container
        3. Copies run_script.sh and parser.py into the container
        4. Applies golden_patch and test_patch via docker exec
        5. Runs tests via docker exec
        6. Parses output and returns result
        7. Removes the container

        Returns None if validation passes, error message string if it fails.
        """
        import hashlib

        container_name = f"validation-{hashlib.sha256(instance_id.encode()).hexdigest()[:12]}"
        repo_dir = task_dir / "app"
        run_script_path = task_dir / "run_script.sh"
        parser_script_path = task_dir / "parser.py"
        logger.info(f"Starting validation using container {container_name}")
        try:
            # Step 1: Create container from jefzda base image
            logger.info(f"Creating container from {docker_image_name}")
            create_result = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--label",
                    "hilbench.validation=true",
                    "--label",
                    f"hilbench.instance_id={instance_id}",
                    "--entrypoint",
                    "/bin/bash",
                    docker_image_name,
                    "-c",
                    "sleep infinity",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if create_result.returncode != 0:
                error_msg = create_result.stderr or create_result.stdout or "Unknown error"
                raise RuntimeError(f"Failed to create validation container: {error_msg}")

            # Verify container is running
            inspect_result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if inspect_result.returncode != 0 or inspect_result.stdout.strip() != "true":
                # Get container logs for debugging
                logs_result = subprocess.run(
                    ["docker", "logs", container_name],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                raise RuntimeError(
                    f"Container created but not running. "
                    f"State: {inspect_result.stdout.strip()}, Logs: {logs_result.stderr or logs_result.stdout}"
                )
            logger.info("Container created successfully")

            # Step 2: Copy prepared repo into container (setup_patch already applied on host)
            logger.info("Copying repo to container")
            clear_repo_result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    "rm -rf /app && mkdir -p /app",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if clear_repo_result.returncode != 0:
                error_msg = clear_repo_result.stderr or clear_repo_result.stdout or "Failed to clear /app"
                raise RuntimeError(f"Failed to clear repo in validation container: {error_msg}")
            copy_repo_result = subprocess.run(
                ["docker", "cp", f"{repo_dir}/.", f"{container_name}:/app/"],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            if copy_repo_result.returncode != 0:
                error_msg = copy_repo_result.stderr or "Failed to copy repo"
                raise RuntimeError(f"Failed to copy repo to container: {error_msg}")
            logger.info("Repo copied successfully")

            # Configure git safe.directory to prevent "dubious ownership" errors
            # This is needed because docker cp changes file ownership
            git_config_result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "git",
                    "config",
                    "--global",
                    "--add",
                    "safe.directory",
                    "/app",
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if git_config_result.returncode != 0:
                logger.warning(
                    f"git safe.directory config failed (non-fatal): {git_config_result.stderr}"
                )

            # Step 3: Copy run_script.sh and parser.py to /root/
            logger.info("Copying test scripts to container")
            copy_script_result = subprocess.run(
                ["docker", "cp", str(run_script_path), f"{container_name}:/root/run_script.sh"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if copy_script_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to copy run_script.sh: {copy_script_result.stderr or copy_script_result.stdout}"
                )
            if not parser_script_path.exists():
                raise RuntimeError(f"Missing required parser.py at {parser_script_path}")
            copy_parser_result = subprocess.run(
                ["docker", "cp", str(parser_script_path), f"{container_name}:/root/parser.py"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if copy_parser_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to copy parser.py: {copy_parser_result.stderr or copy_parser_result.stdout}"
                )
            subprocess.run(
                ["docker", "exec", container_name, "chmod", "+x", "/root/run_script.sh"],
                capture_output=True,
                timeout=300,
            )
            logger.info("Test scripts copied successfully")

            # Step 4: Apply golden_patch (with filter_patch - SAME as validate_swe.py)
            if golden_patch and golden_patch.strip():
                logger.info("Applying golden_patch")
                golden_patch_filtered = _filter_patch(golden_patch)
                golden_patch_normalized = _normalize_patch_line_endings(
                    golden_patch_filtered, repo_dir
                )
                # Write patch to temp file and copy it
                golden_patch_file = task_dir / "_golden_patch_validation.diff"
                golden_patch_file.write_text(golden_patch_normalized)
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(golden_patch_file),
                        f"{container_name}:/tmp/golden_patch.diff",
                    ],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                apply_golden_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container_name,
                        "bash",
                        "-c",
                        "cd /app && git apply -v /tmp/golden_patch.diff",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if apply_golden_result.returncode != 0:
                    error_msg = (
                        apply_golden_result.stderr or apply_golden_result.stdout or "Unknown error"
                    )
                    return f"SWE INPUT VALIDATION FAILED: golden_patch failed to apply. Error: {error_msg}"
                logger.info("golden_patch applied successfully")

            # Step 5: Apply test_patch
            if test_patch and test_patch.strip():
                logger.info("Applying test_patch")
                test_patch_normalized = _normalize_patch_line_endings(test_patch, repo_dir)
                test_patch_file = task_dir / "_test_patch_validation.diff"
                test_patch_file.write_text(test_patch_normalized)
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(test_patch_file),
                        f"{container_name}:/tmp/test_patch.diff",
                    ],
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                apply_test_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container_name,
                        "bash",
                        "-c",
                        "cd /app && git apply -v /tmp/test_patch.diff",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if apply_test_result.returncode != 0:
                    error_msg = (
                        apply_test_result.stderr or apply_test_result.stdout or "Unknown error"
                    )
                    return f"SWE INPUT VALIDATION FAILED: test_patch failed to apply. Error: {error_msg}"
                logger.info("test_patch applied successfully")

            # Step 6: Run tests using run_script.sh with specific tests
            # IMPORTANT: run_script.sh's run_all_tests() may have --ignore directives that skip
            # the exact test files we need. By passing tests_to_pass as arguments, we trigger
            # run_selected_tests() which runs only those specific tests without ignoring anything.
            # This matches the behavior in custom_eval.py's augment_test_spec_with_required_tests()
            logger.info("Running tests")
            if tests_to_pass:
                # Read run_script.sh content for special handling (e.g., ansible-test)
                run_script_content = run_script_path.read_text() if run_script_path.exists() else ""

                # Use the helper function that matches custom_eval.py logic exactly
                quoted_args = _process_validation_test_args(
                    tests_to_pass=tests_to_pass,
                    run_script_content=run_script_content,
                    language=language,
                )

                if quoted_args:
                    validation_test_cmd = (
                        f"bash /root/run_script.sh {quoted_args} > /tmp/stdout.log 2> /tmp/stderr.log; "
                        "python /root/parser.py /tmp/stdout.log /tmp/stderr.log /tmp/output.json; "
                        "python -c \"print('SWEAP_JSON_START'); print(open('/tmp/output.json').read()); print('SWEAP_JSON_END')\""
                    )
                else:
                    validation_test_cmd = test_cmd
            else:
                # Fallback to running all tests if no specific tests provided
                validation_test_cmd = test_cmd
            run_tests_result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    f"cd /app && {validation_test_cmd}",
                ],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            test_output = run_tests_result.stdout + run_tests_result.stderr

            # If test output is empty, try to debug by reading the log files directly
            if not test_output.strip() or "SWEAP_JSON_START" not in test_output:
                logger.warning(
                    f"Test output empty or missing SWEAP_JSON. Exit code: {run_tests_result.returncode}"
                )
                # Try to get more debug info
                debug_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container_name,
                        "bash",
                        "-c",
                        "echo '=== stdout.log ===' && cat /tmp/stdout.log 2>/dev/null | tail -100 && "
                        "echo '=== stderr.log ===' && cat /tmp/stderr.log 2>/dev/null | tail -100 && "
                        "echo '=== output.json ===' && cat /tmp/output.json 2>/dev/null",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                logger.warning(f"Debug output: {debug_result.stdout[-2000:]}")
                logger.warning(f"Debug stderr: {debug_result.stderr[-500:]}")

            logger.info(f"Test output (last 500 chars): {test_output[-500:]}")

            # Step 7: Parse SWEAP JSON and check if FAIL_TO_PASS tests passed
            # This matches the logic in custom_eval.py / swebench grading
            test_status = _parse_sweap_json_test_status(test_output, tests_to_pass)
            if tests_to_pass and not test_status:
                raw_logs_result = subprocess.run(
                    [
                        "docker",
                        "exec",
                        container_name,
                        "bash",
                        "-c",
                        "echo '=== stdout.log ==='; cat /tmp/stdout.log 2>/dev/null; "
                        "echo '=== stderr.log ==='; cat /tmp/stderr.log 2>/dev/null; "
                        "echo '=== output.json ==='; cat /tmp/output.json 2>/dev/null",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                runner_error = _format_internal_test_runner_input_error(
                    (raw_logs_result.stdout or "") + (raw_logs_result.stderr or "")
                )
                if runner_error:
                    return runner_error

            if test_status or tests_to_pass:
                # Check each required test - must be in test_status with PASSED
                # Missing tests count as failures (same as swebench)
                passed_tests = []
                failed_tests = []
                for t in tests_to_pass:
                    if t in test_status and test_status[t] == "PASSED":
                        passed_tests.append(t)
                    else:
                        failed_tests.append(t)

                if failed_tests:
                    # Match original error format from validate_swe.py
                    return f"SWE INPUT VALIDATION FAILED: The golden patch did not pass all necessary tests. Expected: {len(tests_to_pass)} tests to pass. Actual: {len(passed_tests)} passed, {len(failed_tests)} failed/missing. Failed: {failed_tests[:5]}"
                logger.info(f"All {len(passed_tests)} required tests passed!")
                return None  # Success!
            else:
                # No tests to check and no test status - fall back to exit code
                if run_tests_result.returncode == 0:
                    logger.info("Tests passed (exit code 0, no SWEAP JSON)")
                    return None  # Success!
                else:
                    return f"SWE INPUT VALIDATION FAILED: Tests failed with exit code {run_tests_result.returncode}. Output: {test_output[-500:]}"

        except subprocess.TimeoutExpired as e:
            actual_timeout = getattr(e, "timeout", "unknown")
            cmd_preview = " ".join(e.cmd[:3]) if hasattr(e, "cmd") and e.cmd else "unknown command"
            return f"SWE INPUT VALIDATION FAILED: Validation timed out after {actual_timeout} seconds (command: {cmd_preview}...)"
        except Exception as e:
            return f"SWE INPUT VALIDATION FAILED: Unexpected error during validation: {e}"
        finally:
            # Step 8: Always clean up container
            logger.info(f"Cleaning up container {container_name}")
            try:
                inspect_result = subprocess.run(
                    [
                        "docker",
                        "inspect",
                        "-f",
                        '{{.Name}}\t{{.Config.Image}}\t{{index .Config.Labels "hilbench.validation"}}',
                        container_name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if inspect_result.returncode != 0:
                    logger.info("Validation container %s already removed", container_name)
                    return
                inspect_parts = inspect_result.stdout.strip().split("\t")
                if len(inspect_parts) < 3:
                    logger.warning(
                        "Skipping cleanup for %s: unexpected docker inspect output",
                        container_name,
                    )
                    return
                inspected_name, inspected_image, validation_label = inspect_parts
                is_validation_container = (
                    inspected_name == f"/{container_name}"
                    and container_name.startswith("validation-")
                    and validation_label == "true"
                    and inspected_image == docker_image_name
                )
                if not is_validation_container:
                    logger.warning(
                        "Skipping cleanup for %s: container does not match validation ownership "
                        "(name=%s image=%s label=%s)",
                        container_name,
                        inspected_name,
                        inspected_image,
                        validation_label,
                    )
                    return

                # Validation containers are temporary and should always be torn down
                # after validation completes.
                subprocess.run(
                    ["docker", "stop", "-t", "30", container_name],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )

                verify_removed = subprocess.run(
                    ["docker", "inspect", container_name],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if verify_removed.returncode == 0:
                    logger.warning(
                        "Validation container %s still exists after cleanup attempt",
                        container_name,
                    )
            except subprocess.TimeoutExpired:
                logger.warning(
                    f"Container cleanup timed out for {container_name}, continuing anyway"
                )
            except Exception as e:
                logger.warning(f"Container cleanup failed for {container_name}: {e}")





















if __name__ == "__main__":
    from genai.system.agentic_autoreviewer.core.data_wrapper import JsonDataRow
    from genai.system.agentic_autoreviewer.core.requirement import Requirement

    agent = HILBenchAgent()
    agent_input = AgentInput(
        data=JsonDataRow(
            data={
                "database_name": "california_schools",
                "question": "Which schools with the educational option of opportunity have a free or reduced price meal count below the average and offer virtual classes?",
                "business_info": '["Free or Reduced Price Meal program = FRPM"]',
                "schema_descriptions": "| table_name | table_description |\n| --- | --- |\n| **satscores** | School-level SAT data for California, with county and district context. Includes grades 1–12 enrollment, number of test takers, average Reading/Math/Writing scores, and counts scoring ≥1500 total. Identified by the CDS code and school name. Supports metrics such as an excellence rate (NumGE1500 / NumTstTakr). |\n| **schools** | Each row represents a California school, with state and NCES identifiers; district/authority classifications; operational status and dates; instructional characteristics (grade span, virtual, charter/magnet, education option/level); location details (street and mailing addresses, city, ZIP, state, latitude/longitude); contact information (phone, website, administrators); coded ownership types for both district (DOC) and school (SOC); and a last-updated timestamp. |\n| **frpm** | California K–12 school and district records by academic year. Contains identifiers (CDS, county, district, school), school/district type, charter status, NSLP provisioning, and grade span. Reports enrollment counts and numbers/percentages eligible for free and free-or-reduced-price meals, including a CALPADS certification indicator for 2013–14. |\n\n### Table: frpm\n\n| column_name | data_format | description |\n| --- | --- | --- |\n| CDSCode | integer | CDSCode |\n| Academic Year | integer | Academic Year |\n| County Code | integer | County Code |\n| District Code | integer | District Code |\n| School Code | integer | School Code |\n| County Name | text | County Name |\n| District Name | text | District Name |\n| School Name | text | School Name |\n| District Type | text | District Type |\n| School Type | text | School Type |\n| Educational Option Type | text | Educational Option Type |\n| NSLP Provision Status | text | NSLP Provision Status |\n| Charter School (Y/N) | integer | Charter School (Y/N). Additional notes: 0 = No, 1 = Yes |\n| Charter School Number | text | Charter School Number |\n| Charter Funding Type | text | Charter Funding Type |\n| IRC | integer | IRC. Additional notes: Not useful |\n| Low Grade | text | Lowest grade offered by the school |\n| High Grade | text | Highest grade offered by the school |\n| Enrollment (K-12) | real | Enrollment (K-12). Additional notes: K–12 refers to grades 1–12 |\n| Free Meal Count (K-12) | real | Free Meal Count (K-12). Additional notes: eligible free rate = Free Meal Count / Enrollment |\n| Percent (%) Eligible Free (K-12) | real | Percent (%) Eligible Free (K-12) |\n| FRPM Count (K-12) | real | Free or Reduced Price Meal Count (K-12). Additional notes: eligible FRPM rate = FRPM / Enrollment |\n| Percent (%) Eligible FRPM (K-12) | real | Percent (%) Eligible FRPM (K-12) |\n| Enrollment (Ages 5–17) | real | Enrollment (Ages 5–17) |\n| Free Meal Count (Ages 5–17) | real | Free Meal Count (Ages 5–17). Additional notes: eligible free rate = Free Meal Count / Enrollment |\n| Percent (%) Eligible Free (Ages 5–17) | real | Percent (%) Eligible Free (Ages 5–17) |\n| FRPM Count (Ages 5–17) | real | FRPM Count (Ages 5–17) |\n| Percent (%) Eligible FRPM (Ages 5–17) | real | Percent (%) Eligible FRPM (Ages 5–17) |\n| 2013–14 CALPADS Fall 1 Certification Status | integer | 2013–14 CALPADS Fall 1 Certification Status |\n\n### Table: satscores\n\n| column_name | data_format | description |\n| --- | --- | --- |\n| cds | text | California Department Schools |\n| rtype | text | rtype. Additional notes: unuseful |\n| sname | text | school name |\n| dname | text | district segment |\n| cname | text | county name |\n| enroll12 | integer | enrollment (1st-12th grade) |\n| NumTstTakr | integer | Number of Test Takers in this school. Additional notes: number of test takers in each school |\n| AvgScrRead | integer | average scores in Reading. Additional notes: average scores in Reading |\n| AvgScrMath | integer | average scores in Math. Additional notes: average scores in Math |\n| AvgScrWrite | integer | average scores in writing. Additional notes: average scores in writing |\n| NumGE1500 | integer | Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500. Additional notes: Number of Test Takers Whose Total SAT Scores Are Greater or Equal to 1500<br>Commonsense evidence: Excellence Rate = NumGE1500 / NumTstTakr |\n\n### Table: schools\n\n| column_name | data_format | description |\n| --- | --- | --- |\n| CDSCode | text | CDSCode |\n| NCESDist | text | This field represents the 7-digit National Center for Educational Statistics (NCES) school district identification number. The first 2 digits identify the state and the last 5 digits identify the school district. Combined, they make a unique 7-digit ID for each school district |\n| NCESSchool | text | This field represents the 5-digit NCES school identification number. The NCESSchool combined with the NCESDist form a unique 12-digit ID for each school |\n| StatusType | text | This field identifies the status of the district. Additional notes: Definitions of the valid status types are listed below: Active, Closed, Merged, Pending |\n| County | text | County name |\n| District | text | District |\n| School | text | School |\n| Street | text | Street |\n| StreetAbr | text | The abbreviated street address of the school, district, or administrative authority’s physical location. Note: Some records (primarily closed or retired schools) may not have data in this field |\n| City | text | City |\n| Zip | text | Zip |\n| State | text | State |\n| MailStreet | text | MailStreet. Additional notes: The unabbreviated mailing address. Unpopulated cells filled with Street data |\n| MailStrAbr | text | MailStrAbr. Additional notes: The abbreviated mailing street address. Unpopulated cells filled with StreetAbr data |\n| MailCity | text | MailCity. Additional notes: City associated with mailing address. Unpopulated cells filled with City data |\n| MailZip | text | MailZip. Additional notes: Zip associated with mailing address. Unpopulated cells filled with Zip data |\n| MailState | text | MailState. Additional notes: State within mailing address. Unpopulated cells filled with State data |\n| Phone | text | Phone |\n| Ext | text | The phone number extension of the school, district, or administrative authority |\n| Website | text | The website address of the school, district, or administrative authority |\n| OpenDate | date | The date the school opened |\n| ClosedDate | date | The date the school closed |\n| Charter | integer | This field identifies a charter school. 1 = charter, 0 = not charter |\n| CharterNum | text | The charter school number, 4-digit number assigned to a charter school |\n| FundingType | text | Indicates the charter school funding type. Values: Not in CS funding model, Locally funded, Directly funded |\n| DOC | text | District Ownership Code. Notes: 00-County Office of Education, 02-State Board, 03-Statewide Benefit Charter, 31-State Special Schools, 34-Non-school Location\\*, 52-Elementary School District, 54-Unified School District, 56-High School District, 98-Regional Occupational Center/Program (ROC/P) |\n| DOCType | text | District Ownership Code Type text description (see DOC values) |\n| SOC | text | School Ownership Code numeric values. Examples: 08-Preschool, 60-Elementary School, 66-High Schools, etc. |\n| SOCType | text | School Ownership Code Type text description |\n| EdOpsCode | text | Education Option Code short text description. Examples: ALTSOC-Alternative School of Choice, COMM-County Community School, etc. |\n| EdOpsName | text | Educational Option Name long text description |\n| EILCode | text | Educational Instruction Level Code short text description. Examples: A-Adult, ELEM-Elementary, HS-High School |\n| EILName | text | Educational Instruction Level Name long text description |\n| GSoffered | text | Grade span offered (lowest and highest grade). May differ from grade span served |\n| GSserved | text | Lowest and highest grade of student enrollment as reported in certified CALPADS Fall 1 data collection |\n| Virtual | text | Type of virtual instruction offered. F=Exclusively Virtual, V=Primarily Virtual, C=Primarily Classroom, N=Not Virtual, P=Partial Virtual |\n| Magnet | integer | Whether school is a magnet school. 1=Yes, 0=No. Note: Preschools and adult education centers do not contain a magnet indicator |\n| Latitude | real | Angular distance (degrees) north/south from the equator |\n| Longitude | real | Angular distance (degrees) west/east from the prime meridian |\n| AdmFName1 | text | Administrator’s first name (superintendent/principal). Only active/pending districts display |\n| AdmLName1 | text | Administrator’s last name (superintendent/principal). Only active/pending districts display |\n| AdmEmail1 | text | Administrator’s email address (superintendent/principal). Only active/pending districts display |\n| AdmFName2 | text | AdmFName2. Same as 1 |\n| AdmLName2 | text | AdmLName2 |\n| AdmEmail2 | text | AdmEmail2 |\n| AdmFName3 | text | AdmFName3. Not useful |\n| AdmLName3 | text | AdmLName3. Not useful |\n| AdmEmail3 | text | AdmEmail3. Not useful |\n| LastUpdate | date | LastUpdate. When this record was last updated |\n",
                "golden_sql": """SELECT 
    s.School, 
    s.County, 
    s.EdOpsName,
    s.Virtual,
    f.`FRPM Count (Ages 5-17)` as frpm_count
FROM schools s
JOIN frpm f ON s.CDSCode = f.CDSCode
WHERE s.Virtual = 'F'  
  AND s.EdOpsName = 'Opportunity School'  
  AND f.`FRPM Count (Ages 5-17)` IS NOT NULL
  AND f.`FRPM Count (Ages 5-17)` < (
      SELECT AVG(`FRPM Count (Ages 5-17)`)
      FROM frpm
      WHERE `FRPM Count (Ages 5-17)` IS NOT NULL
  )
ORDER BY f.`FRPM Count (Ages 5-17)`;
""",
                "blocker_registry": '[{"title": "Blocker_1", "annotations": {"blocker_type": "question", "blocker_subtype_question": "multiple_interpretations", "blocker_underscored_name": "virtual_classes_meaning", "blocker_description": "The question asks for schools that \\"offer virtual classes\\" but does not specify whether this means exclusively virtual instruction or includes partially virtual instruction. The Virtual column contains values F, V, C, N, P without clear definitions.", "blocker_resolution": "\\"Virtual classes\\" refers to exclusively virtual instruction only. Use Virtual = \'F\' in the schools table.", "blocker_acceptable_questions": "- What does \\"virtual classes\\" mean in this context?\\n- Should I include partially virtual schools or only exclusively virtual schools?\\n- Does \\"offer virtual classes\\" mean exclusively virtual or also partially virtual?"}}, {"title": "Blocker_2", "annotations": {"blocker_type": "business info", "blocker_subtype_business_info": "missing_information", "blocker_underscored_name": "frpm_column_specification", "blocker_description": "The question asks for \\"free or reduced price meal count\\" but the business information does not specify which FRPM column to use. The frpm table contains both \\"FRPM Count (K-12)\\" and \\"FRPM Count (Ages 5-17)\\" columns with different values.", "blocker_resolution": "Use \\"FRPM Count (Ages 5-17)\\" column from the frpm table for the free or reduced price meal count metric.", "blocker_acceptable_questions": "- Which FRPM Count column should I use - K-12 or Ages 5-17?\\n- Does \\"meal count\\" refer to the K-12 enrollment or Ages 5-17?\\n- There are two FRPM Count columns, which one should I use?"}}, {"title": "Blocker_3", "annotations": {"blocker_type": "schema", "blocker_subtype_schema": "confusing_table_or_table_definition", "blocker_underscored_name": "opportunity_schools_table_location", "blocker_description": "The question asks for \\"schools with the educational option of opportunity\\" but the table descriptions are vague about where this information is located. The schools table mentions \\"education option/level\\" among other instructional characteristics. The frpm table also contains an \\"Educational Option Type\\" column. Without exploring the actual data, it\'s unclear which table contains the correct opportunity school classification.", "blocker_resolution": "Use schools.EdOpsName = \'Opportunity School\' to filter for schools with the educational option of opportunity.", "blocker_acceptable_questions": "- Where is the educational option information for opportunity schools stored?\\n- Which table should I use to identify opportunity schools?\\n- Should I filter using schools.EdOpsName or frpm\'s Educational Option Type column?\\n- Does frpm.Educational Option Type contain \\"Opportunity School\\" values?"}}]',
                "instance_id": "test",
            },
        ),
        requirement=Requirement(
            index=0,
            requirement="placeholder",
            scoring_rules=[
                "placeholder",
            ],
            related_fields=[
                "database_name",
                "question",
                "business_info",
                "schema_descriptions",
                "golden_sql",
                "blocker_registry",
                "instance_id",
            ],
        ),
    )
    result = asyncio.run(agent._act(agent_input))
    print(result.model_dump_json(indent=2))
