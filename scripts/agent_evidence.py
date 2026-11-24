#!/usr/bin/env python3
"""Validate an explicitly declared repository evidence manifest."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import ntpath
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

if __package__:
    from .agent_evidence_io import repository_reader
else:
    from agent_evidence_io import repository_reader

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class EvidenceError(ValueError):
    """Declared evidence is missing or cannot be verified."""


@dataclass(frozen=True)
class Evidence:
    manifest: str
    sha256: str
    artifacts: tuple[str, ...]


def _windows_reserved(name: str) -> bool:
    if hasattr(ntpath, "isreserved"):
        return ntpath.isreserved(name)
    return PureWindowsPath(name).is_reserved()


def _artifact_parts(name: object) -> tuple[str, ...]:
    if not isinstance(name, str) or any(
        ord(char) < 32 or ord(char) == 127 or char in '\\:<>|"*?' for char in name
    ):
        raise EvidenceError("evidence paths must be plain repository-relative paths")
    parts = tuple(name.split("/"))
    if any(
        not part or part.endswith((".", " ")) or _windows_reserved(part)
        for part in parts
    ) or any(part.casefold() in {".agent", ".git"} for part in parts):
        raise EvidenceError("evidence paths must be canonical and outside .agent and .git")
    name.encode("utf-8")
    return parts


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise EvidenceError("manifest contains a duplicate field")
    return fields


def _check_git_ignored(root: Path, paths: Sequence[str]) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=root,
        input=b"".join(os.fsencode(path) + b"\0" for path in paths),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        ignored = ", ".join(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)
        raise EvidenceError(f"required evidence is Git-ignored: {ignored}")
    if result.returncode != 1:
        raise EvidenceError("Git ignore rules could not be checked")


def _read_evidence(reader, parts: tuple[str, ...], *, manifest: bool = False) -> bytes | str:
    try:
        with reader(parts) as stream:
            return stream.read() if manifest else hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        name = "/".join(parts)
        if error.errno == errno.ENOENT:
            raise EvidenceError(f"missing required evidence file: {name}") from error
        raise EvidenceError(
            f"could not safely open required evidence (possible reparse point or repository escape): {name}"
        ) from error


def validate(root: Path, manifest: str, *, expected_sha256: str | None = None) -> Evidence:
    try:
        with repository_reader(root) as reader:
            return _validate(root, manifest, reader, expected_sha256)
    except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("could not safely read the evidence manifest and artifacts") from error


def _validate(root: Path, manifest: str, reader, expected_sha256: str | None) -> Evidence:
    manifest_bytes = _read_evidence(reader, _artifact_parts(manifest), manifest=True)
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise EvidenceError("evidence manifest SHA-256 changed after preflight")
    data = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_unique_fields)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "artifacts"}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
        or not isinstance(data["artifacts"], list)
        or not data["artifacts"]
    ):
        raise EvidenceError("expected a schema version 1 manifest with required artifacts")

    artifacts: dict[str, tuple[tuple[str, ...], str]] = {}
    references: list[tuple[str, str]] = []
    seen_paths = {os.path.normcase(manifest)}
    for item in data["artifacts"]:
        if (
            not isinstance(item, dict)
            or not {"path", "sha256"} <= item.keys() <= {"path", "sha256", "references"}
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise EvidenceError("each artifact requires a path and lowercase SHA-256")
        name = item["path"]
        parts = _artifact_parts(name)
        identity = os.path.normcase(name)
        if identity in seen_paths:
            raise EvidenceError(f"duplicate evidence path: {name}")
        seen_paths.add(identity)
        artifacts[name] = (parts, item["sha256"])
        links = item.get("references", [])
        if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
            raise EvidenceError(f"references must be a list of artifact paths: {name}")
        references.extend((name, link) for link in links)

    for name, link in references:
        if link not in artifacts or link == name:
            raise EvidenceError(f"reference does not name another declared artifact: {name}")
    for name, (parts, expected) in artifacts.items():
        actual = _read_evidence(reader, parts)
        if actual != expected:
            raise EvidenceError(f"SHA-256 mismatch for required evidence: {name}")
    _check_git_ignored(root, (manifest, *artifacts))

    return Evidence(manifest, digest, tuple(artifacts))


def main(argv: Sequence[str] | None = None, *, root: Path = REPOSITORY_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="repository-relative JSON manifest path")
    args = parser.parse_args(argv)
    try:
        evidence = validate(root, args.manifest)
    except EvidenceError as error:
        print(f"Evidence preflight: FAIL ({error})", file=sys.stderr)
        return 1
    print(f"Evidence preflight: PASS ({len(evidence.artifacts)} required artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
