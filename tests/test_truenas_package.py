from __future__ import annotations

from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).parents[1] / "ix-dev" / "community" / "vaultbridge"


def test_mcp_questions_are_first_class_safe_and_conditional():
    questions = yaml.safe_load((PACKAGE_ROOT / "questions.yaml").read_text(encoding="utf-8"))
    assert any(group["name"] == "MCP Configuration" for group in questions["groups"])
    mcp = next(question for question in questions["questions"] if question["variable"] == "mcp")
    fields = {field["variable"]: field for field in mcp["schema"]["attrs"]}

    assert {field["label"] for field in fields.values()} == {
        "Enable MCP HTTP",
        "Enable MCP Writes",
        "MCP Allowed Hosts",
        "MCP Allowed Origins",
    }
    assert fields["http_enabled"]["schema"]["default"] is False
    assert fields["write_enabled"]["schema"]["default"] is False
    assert fields["allowed_hosts"]["schema"]["default"] == (
        "127.0.0.1:*,localhost:*,[::1]:*"
    )
    assert fields["allowed_origins"]["schema"]["default"] == (
        "http://127.0.0.1:*,http://localhost:*,http://[::1]:*"
    )
    for field_name in ("write_enabled", "allowed_hosts", "allowed_origins"):
        assert fields[field_name]["schema"]["show_if"] == [["http_enabled", "=", True]]


def test_template_maps_all_mcp_values_without_another_port_or_portal():
    template = (PACKAGE_ROOT / "templates" / "docker-compose.yaml").read_text(encoding="utf-8")

    for environment_name, value_path in {
        "MCP_HTTP_ENABLED": "values.mcp.http_enabled",
        "MCP_WRITE_ENABLED": "values.mcp.write_enabled",
        "MCP_HTTP_ALLOWED_HOSTS": "values.mcp.allowed_hosts",
        "MCP_HTTP_ALLOWED_ORIGINS": "values.mcp.allowed_origins",
    }.items():
        assert f'app.environment.add_env("{environment_name}", {value_path})' in template
    assert template.count("app.add_port(") == 1
    assert 'tpl.portals.add(values.network.web_port, {"scheme": "http", "path": "/ui/"})' in template


def test_synthetic_values_cover_safe_default_and_enabled_mcp_paths():
    test_values = PACKAGE_ROOT / "templates" / "test_values"
    basic = yaml.safe_load((test_values / "basic-values.yaml").read_text(encoding="utf-8"))
    enabled = yaml.safe_load(
        (test_values / "mcp-enabled-values.yaml").read_text(encoding="utf-8")
    )

    assert basic["mcp"] == {
        "http_enabled": False,
        "write_enabled": False,
        "allowed_hosts": "127.0.0.1:*,localhost:*,[::1]:*",
        "allowed_origins": "http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
    }
    assert enabled["mcp"] == {
        "http_enabled": True,
        "write_enabled": True,
        "allowed_hosts": "truenas.example.test:*",
        "allowed_origins": "https://vaultbridge.example.test",
    }
