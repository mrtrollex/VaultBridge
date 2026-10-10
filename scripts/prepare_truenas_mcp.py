#!/usr/bin/env python3
"""Materialize a disposable P2 candidate without changing historical or upstream sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PREPARATION = ROOT / "ix-dev/preparations/vaultbridge-mcp"
MIGRATION = PREPARATION / "migrations/carry_forward_mcp"
MCP_SETTINGS = runpy.run_path(str(MIGRATION))["MCP_SETTINGS"]
CANDIDATE_VERSION = "1.0.4"


def content_hash(path: Path) -> str:
    # Git text content is LF; Windows checkouts may use CRLF.
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def prepared_questions() -> dict:
    questions = yaml.safe_load((PREPARATION / "upstream/questions.yaml").read_text(encoding="utf-8"))
    questions["groups"].insert(1, {"name": "MCP Configuration", "description": "Optional MCP on the Web Port"})
    descriptions = {
        "http_enabled": (
            "Enable MCP HTTP",
            "Optional MCP HTTP at /mcp on the existing Web Port, using the existing API key.",
        ),
        "write_enabled": (
            "Enable MCP Writes",
            "Opt-in create/append access; effective only when MCP HTTP is enabled. Requires writable vault access.",
        ),
        "allowed_hosts": (
            "MCP Allowed Hosts",
            "Comma-separated Host allowlist for MCP HTTP. "
            "Use explicit trusted hosts; broad wildcard allowlists are unsafe.",
        ),
        "allowed_origins": (
            "MCP Allowed Origins",
            "Allowlist checked when an Origin header is present. "
            "Use explicit trusted origins; broad wildcard allowlists are unsafe.",
        ),
    }
    attrs = []
    for field, default in MCP_SETTINGS.values():
        label, description = descriptions[field]
        attrs.append(
            {
                "variable": field,
                "label": label,
                "description": description,
                "schema": {"type": "boolean" if isinstance(default, bool) else "string", "default": default},
            }
        )
    # Keep defaults unconditional: current middleware omits absent show_if-hidden fields.
    questions["questions"].insert(
        1,
        {
            "variable": "mcp",
            "label": "",
            "group": "MCP Configuration",
            "schema": {"type": "dict", "attrs": attrs},
        },
    )
    app = questions["questions"][0]
    envs = next(attr for attr in app["schema"]["attrs"] if attr["variable"] == "additional_envs")
    name = envs["schema"]["items"][0]["schema"]["attrs"][0]
    message = "MCP names are reserved; use the dedicated MCP Configuration fields instead of additional_envs."
    name["description"] = message
    name["schema"]["valid_chars"] = "^(?!(?:" + "|".join(MCP_SETTINGS) + r")(?![\s\S]))[\s\S]*$"
    name["schema"]["valid_chars_error"] = message
    return questions


def prepared_template() -> str:
    template = (PREPARATION / "upstream/templates/docker-compose.yaml").read_text(encoding="utf-8")
    anchor = "{% do c1.environment.add_user_envs(values.vaultbridge.additional_envs) %}"
    assert template.count(anchor) == 1
    mappings = "\n".join(
        '{% do c1.environment.add_env("' + name + '", values.mcp.' + field + ") %}"
        for name, (field, _) in MCP_SETTINGS.items()
    )
    return template.replace(anchor, mappings + "\n" + anchor)


def materialize(output: Path) -> None:
    lock = json.loads((PREPARATION / "upstream-lock.json").read_text(encoding="utf-8"))
    for relative, expected in lock["source_hashes"].items():
        if content_hash(PREPARATION / "upstream" / relative) != expected:
            raise ValueError(f"Pinned upstream source changed: {relative}")
    library = ROOT / "ix-dev/community/vaultbridge/templates/library/base_v2_3_11"
    actual = {path.relative_to(library).as_posix(): content_hash(path) for path in library.rglob("*.py")}
    if actual != lock["library_hashes"]:
        raise ValueError("Historical rendering library differs from pinned upstream; refresh preparation explicitly")
    if output.exists():
        raise ValueError("Candidate output must be a new directory")
    shutil.copytree(PREPARATION / "upstream", output)
    shutil.copytree(library, output / "templates/library/base_v2_3_11", ignore=shutil.ignore_patterns("__pycache__"))
    metadata = yaml.safe_load((output / "app.yaml").read_text(encoding="utf-8"))
    metadata["version"] = CANDIDATE_VERSION
    (output / "app.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    (output / "questions.yaml").write_text(yaml.safe_dump(prepared_questions(), sort_keys=False), encoding="utf-8")
    (output / "templates/docker-compose.yaml").write_text(prepared_template(), encoding="utf-8")
    shutil.copyfile(PREPARATION / "app_migrations.yaml", output / "app_migrations.yaml")
    (output / "migrations").mkdir()
    script = output / "migrations/carry_forward_mcp"
    shutil.copyfile(MIGRATION, script)
    script.chmod(0o755)
    fixture = output / "templates/test_values/basic-values.yaml"
    values = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    values["mcp"] = {field: default for field, default in MCP_SETTINGS.values()}
    fixture.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    values["mcp"] = {
        "http_enabled": True,
        "write_enabled": True,
        "allowed_hosts": "mcp-canary.example.test:*",
        "allowed_origins": "https://mcp-canary.example.test",
    }
    (fixture.parent / "mcp-enabled-values.yaml").write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    # Preserve Git's LF text on every host, including executable shebangs and
    # the upstream library's byte-based catalog hash.
    for path in output.rglob("*"):
        if path.is_file() and path.suffix != ".pyc":
            path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, type=Path, help="new disposable directory; never an upstream checkout"
    )
    args = parser.parse_args()
    materialize(args.output)
    print(f"Prepared TEST candidate {CANDIDATE_VERSION}: {args.output}")


if __name__ == "__main__":
    main()
