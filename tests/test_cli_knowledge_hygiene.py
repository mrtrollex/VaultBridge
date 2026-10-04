from __future__ import annotations

import io
import json
from dataclasses import replace
from typing import get_args
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app import cli
from app.knowledge_hygiene_transport import serialize_hygiene_result
from app.mcp_server import create_mcp_server
from app.repositories.semantic import SemanticRepository
from app.services import knowledge_hygiene as d
from app.services.semantic_search import FastEmbedder, SemanticSearchService
from app.services.vault import VaultService
from tests.test_api import auth
from tests.test_api_knowledge_hygiene import PRIVATE, ROUTE, injected_client, result_fixture, wire_json
from tests.test_cli import service_for, settings_for
from tests.test_mcp_knowledge_hygiene import invoke, server_for


def injected_runner(monkeypatch, fixture=None):
    service = Mock(spec=d.KnowledgeHygieneService)
    service.scan.return_value = fixture if fixture is not None else result_fixture()
    original = cli.run_hygiene_scan
    monkeypatch.setattr(cli, "run_hygiene_scan", lambda *a, **kw: original(*a, **kw, service=service))
    return service


@pytest.mark.parametrize("status", get_args(d.DerivedStatus))
def test_json_defaults_partial_order_and_clean_stdout(tmp_path, monkeypatch, capsys, status):
    fixture = result_fixture(status)
    service = injected_runner(monkeypatch, fixture)
    assert cli.main(["hygiene", "scan", "--json"], settings=settings_for(tmp_path)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n") and captured.out.count("\n") == 1
    assert json.loads(captured.out) == wire_json(fixture)
    assert json.loads(captured.out) == serialize_hygiene_result(fixture).model_dump(mode="json")
    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())


@pytest.mark.parametrize("flags,changes", [
    ([], {}), (["--no-groups"], {"groups": frozenset()}),
    *[(["--group", group], {"groups": frozenset({group})}) for group in get_args(d.Group)],
    (["--group", "aliases", "--group", "frontmatter", "--group", "aliases"],
     {"groups": frozenset({"aliases", "frontmatter"})}),
    (["--finding-limit", "1"], {"finding_limit": 1}),
    (["--finding-limit", "500"], {"finding_limit": 500}),
    (["--duplicate-source-limit", "0"], {"duplicate_source_limit": 0}),
    (["--duplicate-source-limit", "20"], {"duplicate_source_limit": 20}),
    (["--semantic-candidates", "--duplicate-source-limit", "1"],
     {"semantic_candidates": True, "duplicate_source_limit": 1}),
    (["--inspect-derived-index"], {"inspect_derived_index": True}),
])
def test_valid_mapping(tmp_path, monkeypatch, capsys, flags, changes):
    service = injected_runner(monkeypatch)
    assert cli.main(["hygiene", "scan", *flags], settings=settings_for(tmp_path)) == 0
    service.scan.assert_called_once_with(replace(d.KnowledgeHygieneRequest(), **changes))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("flags", [
    ["--group", PRIVATE], ["--group", "Aliases"], ["--group", "alias"],
    ["--group", "aliases", "--no-groups"], ["--fix", PRIVATE], ["--gro", "aliases"],
    ["--semantic-candidates", PRIVATE], ["--semantic-candidates"],
    ["--semantic-candidates", "--duplicate-source-limit", "0"],
    *[["--finding-limit", value] for value in ("0", "501", "-1", "1.0", "10.5", PRIVATE)],
    *[["--duplicate-source-limit", value] for value in ("-1", "21", "1.0", "10.5", PRIVATE)],
    ["--finding-limit"],
])
def test_invalid_arguments_are_private_and_never_scan(tmp_path, monkeypatch, capsys, flags):
    service = injected_runner(monkeypatch)
    owners = Mock(side_effect=AssertionError("constructed owners on invalid request"))
    monkeypatch.setattr(cli, "_vault_service", owners)
    monkeypatch.setattr(cli, "semantic_search_service_from_settings", owners)
    try:
        code = cli.main(["hygiene", "scan", *flags], settings=settings_for(tmp_path))
    except SystemExit as exc:
        code = exc.code
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Knowledge hygiene failed: invalid_request.\n"
    service.scan.assert_not_called()
    owners.assert_not_called()


@pytest.mark.parametrize("flags", [[], ["repair"], ["scan", PRIVATE]])
def test_hierarchy_errors_are_sanitized(capsys, flags):
    with pytest.raises(SystemExit) as failure:
        cli.main(["hygiene", *flags])
    assert failure.value.code == 2
    assert capsys.readouterr().err == "Knowledge hygiene failed: invalid_request.\n"


@pytest.mark.parametrize("changes", [
    {"finding_limit": True}, {"finding_limit": 1.0}, {"finding_limit": "1"},
    {"duplicate_source_limit": False}, {"duplicate_source_limit": 1.0},
    {"duplicate_source_limit": "1"}, {"groups": frozenset({"Aliases"})},
    {"semantic_candidates": 1}, {"inspect_derived_index": "false"},
])
def test_programmatic_requests_keep_strict_domain_validation(tmp_path, changes):
    service = Mock(spec=d.KnowledgeHygieneService)
    output, errors = io.StringIO(), io.StringIO()
    assert cli.run_hygiene_scan(settings_for(tmp_path),
                                request=replace(d.KnowledgeHygieneRequest(), **changes),
                                service=service, output=output, error_output=errors) == 2
    assert output.getvalue() == ""
    assert errors.getvalue() == "Knowledge hygiene failed: invalid_request.\n"
    service.scan.assert_not_called()


@pytest.mark.parametrize("failure,code,message", [
    (d.KnowledgeHygieneError("invalid_request"), 2, "Knowledge hygiene failed: invalid_request."),
    (d.KnowledgeHygieneError("scan_unavailable"), 1, "Knowledge hygiene failed: scan_unavailable."),
    (RuntimeError(PRIVATE), 2, "VaultBridge CLI failed (RuntimeError)."),
])
def test_failure_boundary(tmp_path, monkeypatch, capsys, failure, code, message):
    service = injected_runner(monkeypatch)
    service.scan.side_effect = failure
    assert cli.main(["hygiene", "scan", "--json"], settings=settings_for(tmp_path)) == code
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == message + "\n"
    assert PRIVATE not in captured.err and "Traceback" not in captured.err
    service.scan.assert_called_once()


@pytest.mark.parametrize("stage", ["construction", "serialization"])
def test_internal_failures_stay_private(tmp_path, monkeypatch, capsys, stage):
    failure = Mock(side_effect=RuntimeError(PRIVATE))
    if stage == "construction":
        monkeypatch.setattr(cli, "semantic_search_service_from_settings", failure)
    else:
        injected_runner(monkeypatch)
        monkeypatch.setattr(cli, "serialize_hygiene_result", failure)
    assert cli.main(["hygiene", "scan", "--json"], settings=settings_for(tmp_path)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "VaultBridge CLI failed (RuntimeError).\n"


def test_invalid_result_is_internal_not_configuration_failure(tmp_path, monkeypatch, capsys):
    service = injected_runner(monkeypatch)
    service.scan.return_value = replace(result_fixture(), derived_index=d.DerivedIndexEvidence(PRIVATE))
    assert cli.main(["hygiene", "scan", "--json"], settings=settings_for(tmp_path)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "VaultBridge CLI failed (ValidationError).\n"


def test_configuration_failure_keeps_existing_message(monkeypatch, capsys):
    failure = ValidationError.from_exception_data("Settings", [])
    monkeypatch.setattr(cli.Settings, "from_env", Mock(side_effect=failure))
    assert cli.main(["hygiene", "scan", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "VaultBridge configuration is invalid.\n"


def test_human_output_preserves_every_evidence_field_and_order(tmp_path, monkeypatch, capsys):
    fixture = result_fixture()
    injected_runner(monkeypatch, fixture)
    assert cli.main(["hygiene", "scan"], settings=settings_for(tmp_path)) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    wire = wire_json(fixture)
    for line, field in zip(lines[:4], ("scan", "candidates", "derived_index", "findings_truncated"), strict=True):
        assert line.startswith(field + ": ")
        assert json.loads(line.split(": ", 1)[1]) == wire[field]
    assert [json.loads(line) for line in lines[4:]] == wire["findings"]
    assert captured.err == ""


@pytest.mark.parametrize("partial", [False, True])
def test_empty_human_result_is_bounded_and_partial_visible(tmp_path, monkeypatch, capsys, partial):
    fixture = replace(result_fixture(), findings=(),
                      scan=d.ScanCompleteness("partial" if partial else "complete", (), 0, 0, 0))
    injected_runner(monkeypatch, fixture)
    assert cli.main(["hygiene", "scan"], settings=settings_for(tmp_path)) == 0
    captured = capsys.readouterr()
    assert "No findings in this bounded scan" in captured.out
    assert ("evidence is partial" in captured.out) == partial
    assert captured.err == ""


@pytest.mark.parametrize("flags", [["--help"], ["scan", "--help"]])
def test_help(capsys, flags):
    with pytest.raises(SystemExit) as result:
        cli.main(["hygiene", *flags])
    assert result.value.code == 0
    captured = capsys.readouterr()
    assert all(text in captured.out for text in ("bounded", "read-only", "No repair action"))
    assert captured.err == ""


def test_cli_rest_mcp_fixture_parity(tmp_path, monkeypatch, capsys):
    fixture = result_fixture("inspection_unavailable")
    service = injected_runner(monkeypatch, fixture)
    rest, rest_service = injected_client(tmp_path, fixture)
    mcp, mcp_service = server_for(tmp_path, fixture)
    arguments = {"groups": ["aliases", "frontmatter"], "finding_limit": 500,
                 "duplicate_source_limit": 20, "semantic_candidates": True, "inspect_derived_index": True}
    assert cli.main(["hygiene", "scan", "--json", "--group", "aliases", "--group", "frontmatter",
                     "--finding-limit", "500", "--duplicate-source-limit", "20",
                     "--semantic-candidates", "--inspect-derived-index"], settings=settings_for(tmp_path)) == 0
    captured = capsys.readouterr()
    response = rest.post(ROUTE, json=arguments, headers=auth())
    result = invoke(mcp, arguments)
    assert response.status_code == 200 and result.is_error is False
    assert json.loads(captured.out) == response.json() == result.structured_content == wire_json(fixture)
    assert service.scan.call_args == rest_service.scan.call_args == mcp_service.scan.call_args
    assert captured.err == ""


@pytest.mark.parametrize("missing_vault", [False, True])
@pytest.mark.parametrize("persisted", [False, True])
def test_real_cli_is_read_only_including_construction(tmp_path, monkeypatch, capsys, missing_vault, persisted):
    settings = settings_for(tmp_path)
    if missing_vault:
        settings.vault_path.rmdir()
    else:
        (settings.vault_path / "a.md").write_text("---\naliases: [private-alias, private-alias]\n---\nprivate-body")
    if persisted:
        service_for(settings).sync()
    forbidden = Mock(side_effect=AssertionError("write or ordinary semantic search invoked"))
    for name in ("create_note", "append_note"):
        monkeypatch.setattr(VaultService, name, forbidden)
    for name in ("sync", "sync_paths", "search", "inspect_index", "use_persisted_index_for_read_only_search"):
        monkeypatch.setattr(SemanticSearchService, name, forbidden)
    for name in ("_connect", "prepare_index", "reset_index", "transaction", "set_metadata"):
        monkeypatch.setattr(SemanticRepository, name, forbidden)
    monkeypatch.setattr(FastEmbedder, "embed", forbidden)
    monkeypatch.setattr(FastEmbedder, "resolve_embedding_fingerprint", forbidden)
    monkeypatch.setattr(cli, "BackgroundSemanticIndexer", forbidden)
    before = snapshot(tmp_path)
    code = cli.main(["hygiene", "scan", "--json", "--duplicate-source-limit", "1",
                     "--semantic-candidates", "--inspect-derived-index"], settings=settings)
    captured = capsys.readouterr()
    if missing_vault:
        assert code == 1
        assert captured.out == ""
        assert captured.err == "Knowledge hygiene failed: scan_unavailable.\n"
    else:
        assert code == 0
        result = json.loads(captured.out)
        assert "semantic_unavailable" in result["candidates"]["reasons"]
        assert result["derived_index"]["status"] == ("inspection_unavailable" if persisted else "missing")
        assert captured.err == ""
    assert all(text not in captured.out for text in ("private-alias", "private-body", str(tmp_path)))
    forbidden.assert_not_called()
    assert before == snapshot(tmp_path)


def snapshot(root):
    return {p.relative_to(root): p.read_bytes() if p.is_file() else None for p in root.rglob("*")}


def test_real_domain_cli_transport_parity(tmp_path, monkeypatch, capsys):
    rest, _ = injected_client(tmp_path)
    rest.app.dependency_overrides.clear()
    state = rest.app.state
    (state.settings.vault_path / "a.md").write_text("---\naliases: [private, private]\n---\n[[missing]]")
    request = d.KnowledgeHygieneRequest(duplicate_source_limit=1, semantic_candidates=True, inspect_derived_index=True)
    direct = state.knowledge_hygiene_service.scan(request)
    original = cli.run_hygiene_scan
    monkeypatch.setattr(cli, "run_hygiene_scan", lambda *a, **kw: original(
        *a, **kw, service=state.knowledge_hygiene_service,
    ))
    assert cli.main(["hygiene", "scan", "--json", "--duplicate-source-limit", "1",
                     "--semantic-candidates", "--inspect-derived-index"], settings=state.settings) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == wire_json(direct) == serialize_hygiene_result(direct).model_dump(mode="json")
    mcp = create_mcp_server(
        settings=state.settings, vault_service=state.vault_service,
        semantic_search_service=state.semantic_search_service,
        duplicate_candidate_service=state.duplicate_candidate_service,
        relationship_service=state.relationship_service,
        knowledge_hygiene_service=state.knowledge_hygiene_service,
    )
    arguments = {"duplicate_source_limit": 1, "semantic_candidates": True, "inspect_derived_index": True}
    response = rest.post(ROUTE, json=arguments, headers=auth())
    result = invoke(mcp, arguments)
    assert response.status_code == 200 and result.is_error is False
    assert json.loads(captured.out) == response.json() == result.structured_content
    assert direct.findings and direct.candidates.reasons == ("semantic_unavailable",)
    assert captured.err == ""
