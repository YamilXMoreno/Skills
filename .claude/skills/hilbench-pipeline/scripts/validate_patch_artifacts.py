#!/usr/bin/env python3
"""Mechanical validation for HiL-Bench patch deliverables."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath


REQUIRED_PATCHES = ("test_patch_obstructed.diff", "golden_patch_obstructed.diff")
OPTIONAL_PATCHES = ("setup_patch.diff",)
DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


def fail(message: str) -> None:
    raise ValueError(message)


def validate_repo_path(path: str, filename: str) -> None:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in path:
        fail(f"{filename}: unsafe or non-repo-relative path: {path}")


def validate_patch(path: Path) -> None:
    raw = path.read_bytes()
    if not raw:
        fail(f"{path.name}: empty patch")
    if b"\r" in raw:
        fail(f"{path.name}: CRLF or CR line endings detected; LF is required")
    if not raw.endswith(b"\n"):
        fail(f"{path.name}: missing final LF newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{path.name}: invalid UTF-8: {exc}")

    lines = text.splitlines()
    if not lines or not lines[0].startswith("diff --git "):
        fail(f"{path.name}: content before first git diff block or missing diff header")
    if "```" in text:
        fail(f"{path.name}: markdown fence found in diff")

    seen: set[tuple[str, str]] = set()
    block_starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    if not block_starts:
        fail(f"{path.name}: no git diff blocks")
    block_starts.append(len(lines))

    for index in range(len(block_starts) - 1):
        start, end = block_starts[index], block_starts[index + 1]
        block = lines[start:end]
        match = DIFF_HEADER.fullmatch(block[0])
        if not match:
            fail(f"{path.name}: malformed diff header: {block[0]}")
        old_path, new_path = match.groups()
        validate_repo_path(old_path, path.name)
        validate_repo_path(new_path, path.name)
        identity = (old_path, new_path)
        if identity in seen:
            fail(f"{path.name}: duplicate diff block for {old_path} -> {new_path}")
        seen.add(identity)

        has_old = any(line.startswith("--- ") for line in block[1:])
        has_new = any(line.startswith("+++ ") for line in block[1:])
        has_hunk = any(line.startswith("@@ ") for line in block[1:])
        metadata_only = any(
            line.startswith(
                (
                    "old mode ",
                    "new mode ",
                    "similarity index ",
                    "rename from ",
                    "rename to ",
                    "Binary files ",
                    "GIT binary patch",
                )
            )
            for line in block[1:]
        )
        if not metadata_only and not (has_old and has_new and has_hunk):
            fail(f"{path.name}: incomplete unified-diff block for {old_path} -> {new_path}")


def validate_relevant_tests(path: Path) -> None:
    raw = path.read_bytes()
    if b"\r" in raw:
        fail(f"{path.name}: CRLF or CR line endings detected; LF is required")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{path.name}: invalid UTF-8 JSON: {exc}")
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        fail(f"{path.name}: expected a JSON array of non-empty strings")
    if len(value) != len(set(value)):
        fail(f"{path.name}: duplicate test identifiers")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deliverables", type=Path)
    parser.add_argument("--patch", action="append", type=Path, default=[])
    args = parser.parse_args(argv)

    try:
        if args.patch:
            for raw_path in args.patch:
                path = raw_path.expanduser().resolve()
                if not path.is_file():
                    fail(f"required patch missing: {path}")
                validate_patch(path)
        else:
            if args.deliverables is None:
                fail("provide --deliverables or one or more --patch values")
            deliverables = args.deliverables.expanduser().resolve()
            for filename in REQUIRED_PATCHES:
                path = deliverables / filename
                if not path.is_file():
                    fail(f"required patch missing: {path}")
                validate_patch(path)
            for filename in OPTIONAL_PATCHES:
                path = deliverables / filename
                if path.exists() and path.stat().st_size:
                    validate_patch(path)
            validate_relevant_tests(deliverables / "relevant_tests.txt")
    except (OSError, ValueError) as exc:
        print(f"PATCH_FORMAT_FAIL {exc}")
        return 2

    print("PATCH_FORMAT_OK LF_ONLY VALID_UNIFIED_DIFF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
