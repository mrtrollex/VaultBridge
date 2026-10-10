"""Saved-state upgrades and actual unchanged-library rendering for the P2 candidate."""

from __future__ import annotations

import copy
import importlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from jinja2 import Environment
from packaging.version import Version
from pydantic import TypeAdapter, ValidationError

from app.core.config import Settings

ROOT = Path(__file__).parents[1]
PREP = ROOT / "ix-dev/preparations/vaultbridge-mcp"
PREPARE = runpy.run_path(str(ROOT / "scripts/prepare_truenas_mcp.py"))
MIGRATION = runpy.run_path(str(PREP / "migrations/carry_forward_mcp"))
MAPPING = MIGRATION["MCP_SETTINGS"]
DEFAULTS = {field: default for field, default in MAPPING.values()}
LEGACY = {
    "MCP_HTTP_ENABLED": "true",
    "MCP_WRITE_ENABLED": "true",
    "MCP_HTTP_ALLOWED_HOSTS": "mcp-canary.example.test:*, other.example.test:443",
    "MCP_HTTP_ALLOWED_ORIGINS": "https://mcp-canary.example.test",
}


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def saved_values(names=()):
    # Raw current 1.0.3/image 1.3.0 shape; also covers historical retained 1.0.2.
    # Neither pre-migration package has target mcp fields/defaults.
    values = read_yaml(PREP / "upstream/templates/test_values/basic-values.yaml")
    assert "mcp" not in values
    values["vaultbridge"]["additional_envs"] = [{"name": name, "value": LEGACY[name]} for name in names]
    return values


def reserved_name_schema():
    app = PREPARE["prepared_questions"]()["questions"][0]
    envs = next(attr for attr in app["schema"]["attrs"] if attr["variable"] == "additional_envs")
    return envs["schema"]["items"][0]["schema"]["attrs"][0]["schema"]


@pytest.fixture(scope="module")
def candidate(tmp_path_factory):
    path = tmp_path_factory.mktemp("p2") / "candidate"
    PREPARE["materialize"](path)
    return path


@pytest.fixture
def render(candidate, monkeypatch):
    # The unmodified upstream library probes os.uname during import. Windows has
    # no uname; emulate only its non-TrueNAS branch, never a live middleware client.
    if not hasattr(os, "uname"):
        monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(release="Linux"), raising=False)
    monkeypatch.syspath_prepend(str(candidate / "templates/library"))
    module = importlib.import_module("base_v2_3_11.render")
    template = Environment(extensions=["jinja2.ext.do"]).from_string(
        (candidate / "templates/docker-compose.yaml").read_text(encoding="utf-8")
    )

    def execute(values):
        values = copy.deepcopy(values) | read_yaml(candidate / "ix_values.yaml")
        return json.loads(template.render(values=values, ix_lib=SimpleNamespace(base=SimpleNamespace(render=module))))

    return execute


@pytest.mark.parametrize(
    "names",
    [(name,) for name in MAPPING] + [tuple(MAPPING), tuple(MAPPING), ()],
    ids=["1-http", "2-write", "3-host", "4-origin", "5-all", "6-mixed", "7-absent"],
)
def test_saved_fixture_matrix(names, render, request):
    values = saved_values(names)
    unrelated = [{"name": "CANARY_MARKER", "value": " harmless "}, {"name": "OTHER", "value": "unchanged"}]
    if request.node.callspec.id in ("6-mixed", "7-absent"):
        values["vaultbridge"]["additional_envs"][0:0] = unrelated[:1]
        values["vaultbridge"]["additional_envs"] += unrelated[1:]
    else:
        unrelated = []
    before = copy.deepcopy(values)
    migrated = MIGRATION["migrate"](values)
    expected = DEFAULTS | {MAPPING[name][0]: True if name.endswith("ENABLED") else LEGACY[name] for name in names}
    assert migrated["mcp"] == expected
    assert values == before
    assert migrated["vaultbridge"]["additional_envs"] == unrelated
    assert MIGRATION["migrate"](migrated) == migrated
    for key in before.keys() - {"vaultbridge"}:
        assert migrated[key] == before[key]
    assert (
        migrated["vaultbridge"] | {"additional_envs": before["vaultbridge"]["additional_envs"]} == before["vaultbridge"]
    )
    environment = render(migrated)["services"]["vaultbridge"]["environment"]
    for name, (field, _) in MAPPING.items():
        assert list(environment).count(name) == 1
        expected_value = str(expected[field]).lower() if isinstance(expected[field], bool) else expected[field]
        assert environment[name] == expected_value
    for entry in unrelated:
        assert environment[entry["name"]] == entry["value"]
    # Runtime parses allowlists identically before and after carry-forward.
    for name in names:
        if "ALLOWED" in name:
            runtime_field = "mcp_http_" + MAPPING[name][0]
            assert getattr(Settings(_env_file=None, **{name: LEGACY[name]}), runtime_field) == getattr(
                Settings(_env_file=None, **{name: environment[name]}), runtime_field
            )


@pytest.mark.parametrize("name", MAPPING)
@pytest.mark.parametrize("equal", [False, True])
def test_fixture_8_conflict_never_reaches_renderer(name, equal):
    values = saved_values([name])
    field, default = MAPPING[name]
    values["mcp"] = {field: (True if isinstance(default, bool) else LEGACY[name]) if equal else default}
    before = copy.deepcopy(values)
    renderer = Mock()
    with pytest.raises(MIGRATION["MigrationError"]) as error:
        renderer(MIGRATION["migrate"](values))
    renderer.assert_not_called()
    assert values == before
    assert name in str(error.value) and f"mcp.{field}" in str(error.value)
    assert "vaultbridge.additional_envs" in str(error.value)
    assert LEGACY[name] not in str(error.value)


@pytest.mark.parametrize("name", ["MCP_HTTP_ENABLED", "MCP_WRITE_ENABLED"])
@pytest.mark.parametrize(
    "value", ["true", "false", "True", "False", "0", "1", "off", "on", "n", "y", "no", "yes", "t", "f"]
)
def test_boolean_grammar_matches_runtime(name, value, render):
    values = saved_values([name])
    values["vaultbridge"]["additional_envs"][0]["value"] = value
    migrated = MIGRATION["migrate"](values)
    assert migrated["mcp"][MAPPING[name][0]] is TypeAdapter(bool).validate_python(value)
    environment = render(migrated)["services"]["vaultbridge"]["environment"]
    assert TypeAdapter(bool).validate_python(environment[name]) is TypeAdapter(bool).validate_python(value)


@pytest.mark.parametrize("value", ["", " true ", "invalid-private-marker", "2", None, True])
def test_invalid_legacy_boolean_fails_without_saved_mutation_or_value_leak(value):
    values = saved_values(MAPPING)
    values["vaultbridge"]["additional_envs"][1]["value"] = value
    before = copy.deepcopy(values)
    with pytest.raises(MIGRATION["MigrationError"], match="MCP_WRITE_ENABLED") as error:
        MIGRATION["migrate"](values)
    assert values == before
    assert "invalid-private-marker" not in str(error.value)
    if isinstance(value, str):
        with pytest.raises(ValidationError):
            TypeAdapter(bool).validate_python(value)


def test_saved_dedicated_values_remain_saved_and_defaults_only_fill_absence():
    values = saved_values()
    values["mcp"] = {"http_enabled": False, "write_enabled": True, "allowed_origins": ""}
    result = MIGRATION["migrate"](values)
    assert result["mcp"] == DEFAULTS | values["mcp"]
    assert MIGRATION["migrate"](result) == result
    # An explicitly saved default still conflicts: equality never supplies provenance.
    values["vaultbridge"]["additional_envs"] = [{"name": "MCP_HTTP_ENABLED", "value": "false"}]
    with pytest.raises(MIGRATION["MigrationError"]):
        MIGRATION["migrate"](values)


@pytest.mark.parametrize("name", MAPPING)
def test_future_edit_rejects_reserved_names_before_render(name):
    schema = reserved_name_schema()
    assert re.match(schema["valid_chars"], name) is None
    for unrelated in ["MY_SETTING", name + "_OTHER", name.lower()]:
        assert re.match(schema["valid_chars"], unrelated)
    assert "dedicated MCP Configuration" in schema["valid_chars_error"]


def test_renderer_collision_guard_is_unchanged(render):
    values = MIGRATION["migrate"](saved_values())
    values["vaultbridge"]["additional_envs"] = [{"name": "MCP_HTTP_ENABLED", "value": "false"}]
    with pytest.raises(Exception, match="already"):
        render(values)


@pytest.mark.parametrize("conflict", [False, True])
def test_real_migration_file_interface(tmp_path, conflict):
    values = saved_values(MAPPING)
    if conflict:
        values["mcp"] = {"allowed_origins": "https://private-marker.example.test"}
    path = tmp_path / "saved.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    original = path.read_bytes()
    process = subprocess.run(
        [sys.executable, str(PREP / "migrations/carry_forward_mcp"), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert path.read_bytes() == original
    if conflict:
        assert process.returncode == 1 and not process.stdout
        assert "MCP_HTTP_ALLOWED_ORIGINS" in process.stderr
        assert "private-marker" not in process.stderr
    else:
        assert process.returncode == 0 and not process.stderr
        assert yaml.safe_load(process.stdout) == MIGRATION["migrate"](values)


def test_candidate_preserves_current_upstream_and_historical_copy(candidate):
    metadata = read_yaml(candidate / "app.yaml")
    upstream = read_yaml(PREP / "upstream/app.yaml")
    assert metadata == upstream | {"version": "1.0.4"}
    assert metadata["app_version"] == "1.3.0"
    assert "media.sys.truenas.net" in metadata["icon"]
    questions = read_yaml(candidate / "questions.yaml")
    baseline = read_yaml(PREP / "upstream/questions.yaml")
    non_mcp = [question for question in questions["questions"] if question["variable"] != "mcp"]
    assert non_mcp[1:] == baseline["questions"][1:]
    mcp = next(question for question in questions["questions"] if question["variable"] == "mcp")
    assert {attr["variable"]: attr["schema"]["default"] for attr in mcp["schema"]["attrs"]} == DEFAULTS
    assert all("show_if" not in attr["schema"] for attr in mcp["schema"]["attrs"])
    assert read_yaml(candidate / "ix_values.yaml") == read_yaml(PREP / "upstream/ix_values.yaml")
    # Historical metadata remains reproduction material, never candidate input.
    historical = read_yaml(ROOT / "ix-dev/community/vaultbridge/app.yaml")
    assert historical["version"] == "1.0.0" and historical["app_version"] == "1.1.0"


def test_materialize_rejects_existing_output(candidate):
    with pytest.raises(ValueError, match="new directory"):
        PREPARE["materialize"](candidate)


def test_documented_baselines_match_pinned_preparation(candidate):
    lock = read_yaml(PREP / "upstream-lock.json")
    upstream = read_yaml(PREP / "upstream/app.yaml")
    assert lock["upstream_package"] == upstream["version"] == "1.0.3"
    assert lock["app_image_version"] == upstream["app_version"] == "1.3.0"
    assert read_yaml(candidate / "app.yaml")["app_version"] == upstream["app_version"]
    assert read_yaml(candidate / "ix_values.yaml")["images"]["image"]["tag"].split("@")[0] == upstream["app_version"]
    document = (PREP / "README.md").read_text(encoding="utf-8")
    assert f"| Current principal baseline | `{upstream['version']}` | `{upstream['app_version']}` |" in document
    assert "| Historical retained baseline | `1.0.2` | `1.3.0` |" in document
    assert "Historical retained baseline coverage must not substitute for current-package coverage." in document


def test_live_baseline_procedures_require_current_package_and_execution_refresh():
    document = (PREP / "README.md").read_text(encoding="utf-8")
    live = document.split("## O1/O3 live canary: NOT YET VERIFIED", 1)[1]
    refresh = live.split("### Mandatory refresh immediately before O1 and again immediately before O3", 1)[1]
    refresh, procedures = refresh.split("### O1 current-baseline candidate procedure", 1)
    for recorded in (
        "upstream commit / catalog revision",
        "package version",
        "application image/tag",
        "migration range",
    ):
        assert recorded in refresh
    assert "newest current\npackage becomes the principal baseline" in refresh
    assert "`1.0.3` becomes historical compatibility" in refresh
    assert "Check migration bounds and fixtures include the newer version before\nproceeding" in refresh
    o1, o3 = procedures.split("### O3 refresh-at-execution catalog procedure", 1)
    assert "current package `1.0.3` /\nimage `1.3.0`" in o1
    assert "historical retained baseline `1.0.2`" in o1
    assert "cannot replace the current-baseline case" in o1
    for required in (
        "MCP_HTTP_ENABLED=true",
        "MCP_HTTP_ALLOWED_HOSTS=mcp-canary.example.test:*",
        "MCP_HTTP_ALLOWED_ORIGINS=https://mcp-canary.example.test",
        "CANARY_MARKER=preserve-this",
        "initialize and perform",
        "HTTP 403",
        "Repeat the accepted read and denied 403 pair",
    ):
        assert required in o1
    assert "package current immediately before\nthe new package is delivered" in o3
    assert "refresh immediately before O3" in o3
    assert "not a permanent version requirement" in o3
    assert "Historical retained cases cannot substitute" in o3


@pytest.mark.parametrize("prior", ["1.0.3", "1.0.2"], ids=["current-principal", "historical-retained"])
def test_migration_bounds_include_current_and_historical_pre_migration_packages(prior, candidate):
    manifest = read_yaml(candidate / "app_migrations.yaml")
    assert manifest == read_yaml(PREP / "app_migrations.yaml")
    (migration,) = manifest["migrations"]
    source = migration["from"]
    target = migration["target"]
    assert Version(source.get("min_version", "0")) <= Version(prior) <= Version(source["max_version"])
    candidate_version = Version(read_yaml(candidate / "app.yaml")["version"])
    assert Version(target["min_version"]) <= candidate_version
    assert candidate_version > Version(source["max_version"])  # already-migrated sources stay excluded


def test_materialized_text_has_posix_line_endings(candidate):
    assert b"\r" not in (candidate / "migrations/carry_forward_mcp").read_bytes()
    for path in candidate.rglob("*.yaml"):
        assert b"\r\n" not in path.read_bytes()


def test_render_only_adds_mcp_environment_to_current_upstream(candidate, render):
    values = saved_values()
    prepared = render(MIGRATION["migrate"](values))
    module = importlib.import_module("base_v2_3_11.render")
    baseline_template = Environment(extensions=["jinja2.ext.do"]).from_string(
        (PREP / "upstream/templates/docker-compose.yaml").read_text(encoding="utf-8")
    )
    baseline = json.loads(
        baseline_template.render(
            values=values | read_yaml(candidate / "ix_values.yaml"),
            ix_lib=SimpleNamespace(base=SimpleNamespace(render=module)),
        )
    )
    environment = prepared["services"]["vaultbridge"]["environment"]
    for name in MAPPING:
        environment.pop(name)
    assert prepared == baseline  # ports, portal, mounts, identity, image, capabilities, auth


@pytest.mark.parametrize("name", MAPPING)
def test_duplicate_legacy_entries_fail_without_mutating_saved_values(name):
    values = saved_values([name, name])
    before = copy.deepcopy(values)
    with pytest.raises(MIGRATION["MigrationError"], match=f"Duplicate {name}"):
        MIGRATION["migrate"](values)
    assert values == before


@pytest.mark.parametrize(
    "values", [None, [], {"mcp": None}, {"vaultbridge": None}, {"vaultbridge": {"additional_envs": [None]}}]
)
def test_malformed_saved_state_fails_safely(values):
    before = copy.deepcopy(values)
    with pytest.raises(MIGRATION["MigrationError"]):
        MIGRATION["migrate"](values)
    assert values == before
