from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import get_args
from unittest.mock import Mock

import pytest

from app.api.dependencies import get_knowledge_hygiene_service
from app.api.knowledge_hygiene import KnowledgeHygieneScanResponse
from app.services import knowledge_hygiene as d
from tests.test_api import auth, client_for

ROUTE = "/api/v1/knowledge/hygiene/scan"
PRIVATE = "PRIVATE-CONTENT C:/private/vault secret-alias SELECT credentials"


def wire_json(result):
    return json.loads(json.dumps(asdict(result)))


def result_fixture(status="not_requested"):
    evidence = (
        ("missing_relationship_target", d.RelationshipEvidence("markdown_link", 2)),
        ("unsafe_relationship_target", d.RelationshipEvidence("obsidian_wikilink", 0)),
        ("ambiguous_relationship_target", d.RelationshipEvidence("markdown_link", 1)),
        ("invalid_frontmatter", d.FrontmatterEvidence(1, None)),
        ("invalid_portable_field", d.PortableFieldEvidence("aliases", None)),
        ("empty_portable_field_value", d.PortableFieldEvidence("tags", 255)),
        ("duplicate_alias_in_note", d.DuplicateAliasEvidence((0, 255))),
        ("colliding_alias", d.CollidingAliasEvidence(1, 9999, True)),
        *((kind, d.RuleEvidence()) for kind in (
            "isolated_note", "empty_authored_body", "duplicate_candidate",
            "near_duplicate_candidate", "previous_compatible_index", "derived_index_unavailable",
        )),
    )
    # Deliberately unsorted, including related paths and reasons: the adapter must preserve these.
    findings = tuple(
        d.DiagnosticFinding(
            kind, None if "index" in kind else "z.md", ("z.md", "A.md"),
            "derived_index" if "index" in kind or kind == "near_duplicate_candidate" else "live_markdown",
            "owner_category", item,
        ) for kind, item in evidence
    )
    return d.KnowledgeHygieneResult(
        findings,
        d.ScanCompleteness("partial", tuple(reversed(get_args(d.ScanReason))), 10000, 9999, 1),
        d.CandidateCoverage("partial", 20, tuple(reversed(get_args(d.CandidateReason)))),
        d.DerivedIndexEvidence(status), True,
    )


def injected_client(tmp_path, result=None):
    client = client_for(tmp_path)
    service = Mock(spec=d.KnowledgeHygieneService)
    service.scan.return_value = result if result is not None else result_fixture()
    client.app.dependency_overrides[get_knowledge_hygiene_service] = lambda: service
    return client, service


@pytest.mark.parametrize("status", get_args(d.DerivedStatus))
def test_all_findings_evidence_statuses_and_order_are_faithful(tmp_path, status):
    result = result_fixture(status)
    client, service = injected_client(tmp_path, result)
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 200
    assert response.json() == wire_json(result)
    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())


@pytest.mark.parametrize("groups", [[], ["aliases", "aliases"], *[[g] for g in get_args(d.Group)]])
def test_groups_map_to_domain_set(tmp_path, groups):
    client, service = injected_client(tmp_path)
    assert client.post(ROUTE, json={"groups": groups}, headers=auth()).status_code == 200
    assert service.scan.call_args.args[0].groups == frozenset(groups)


@pytest.mark.parametrize("field,value", [
    ("finding_limit", 1), ("finding_limit", 500),
    ("duplicate_source_limit", 0), ("duplicate_source_limit", 20),
    ("inspect_derived_index", True), ("semantic_candidates", False),
])
def test_valid_bounds_and_flags(tmp_path, field, value):
    client, service = injected_client(tmp_path)
    assert client.post(ROUTE, json={field: value}, headers=auth()).status_code == 200
    assert getattr(service.scan.call_args.args[0], field) == value


@pytest.mark.parametrize("body", [
    {"groups": [PRIVATE]}, {"groups": PRIVATE}, {"groups": [1]},
    {"finding_limit": 0}, {"finding_limit": 501}, {"finding_limit": True},
    {"finding_limit": "1"}, {"finding_limit": 1.0},
    {"duplicate_source_limit": -1}, {"duplicate_source_limit": 21},
    {"duplicate_source_limit": False}, {"duplicate_source_limit": "1"},
    {"duplicate_source_limit": 1.0}, {"semantic_candidates": True},
    {"semantic_candidates": 1}, {"semantic_candidates": "true"},
    {"inspect_derived_index": 0}, {"inspect_derived_index": "false"},
    {PRIVATE: PRIVATE}, [], PRIVATE,
    *[{field: None} for field in asdict(d.KnowledgeHygieneRequest())],
])
def test_invalid_requests_are_private_and_do_not_scan(tmp_path, body, caplog):
    client, service = injected_client(tmp_path)
    response = client.post(ROUTE, json=body, headers=auth())
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    service.scan.assert_not_called()
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("body", [None, "{\"private-secret\":", "null"])
def test_missing_malformed_and_null_body_are_private(tmp_path, body):
    client, service = injected_client(tmp_path)
    response = client.post(ROUTE, content=body, headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    service.scan.assert_not_called()


@pytest.mark.parametrize("body", [
    pytest.param(b"\xff", id="invalid_utf8"),
    pytest.param(b'{"finding_limit":', id="malformed_json"),
])
def test_malformed_body_bytes_are_private_caller_errors(tmp_path, body, caplog):
    client, service = injected_client(tmp_path)
    response = client.post(ROUTE, content=body, headers={**auth(), "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert service.scan.call_count == 0
    for marker in ("UnicodeDecodeError", "JSONDecodeError", "codec", "parsing the body", "finding_limit"):
        assert marker not in response.text
        assert marker not in caplog.text


@pytest.mark.parametrize("code,detail", [
    (400, "There was an error parsing the body"),  # Marker alone is insufficient.
    (401, "Invalid API key"), (403, "Forbidden"), (429, "Rate limit exceeded"),
    (500, "Server API_KEY is not configured"), (503, "Service unavailable"),
])
def test_unrelated_starlette_http_errors_preserve_status_detail_and_headers(tmp_path, code, detail):
    from starlette.exceptions import HTTPException

    client, service = injected_client(tmp_path)

    def failed_dependency():
        raise HTTPException(status_code=code, detail=detail, headers={"Retry-After": "7"})

    client.app.dependency_overrides[get_knowledge_hygiene_service] = failed_dependency
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == code
    assert response.json() == {"detail": detail}
    assert response.headers["Retry-After"] == "7"
    service.scan.assert_not_called()


def test_unwrapped_programming_decode_error_remains_internal_error(tmp_path):
    client, service = injected_client(tmp_path)
    service.scan.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, PRIVATE)
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 500
    assert response.json() == {"detail": "internal_error"}
    service.scan.assert_called_once()


@pytest.mark.parametrize("error,code,detail", [
    (d.KnowledgeHygieneError("invalid_request"), 422, "invalid_request"),
    (d.KnowledgeHygieneError("scan_unavailable"), 503, "scan_unavailable"),
    (RuntimeError(PRIVATE), 500, "internal_error"),
])
def test_domain_and_unexpected_errors_are_private(tmp_path, error, code, detail, caplog):
    client, service = injected_client(tmp_path)
    service.scan.side_effect = error
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == code
    assert response.json() == {"detail": detail}
    service.scan.assert_called_once()
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("phase", ["dependency", "serialization"])
def test_construction_and_serialization_failure_boundary(tmp_path, phase, monkeypatch, caplog):
    client, service = injected_client(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError(PRIVATE)

    if phase == "dependency":
        # No arguments: otherwise FastAPI interprets them as request parameters.
        def failed_dependency():
            raise RuntimeError(PRIVATE)
        client.app.dependency_overrides[get_knowledge_hygiene_service] = failed_dependency
    else:
        monkeypatch.setattr(KnowledgeHygieneScanResponse, "model_validate", fail)
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 500
    assert response.json() == {"detail": "internal_error"}
    assert PRIVATE not in caplog.text
    if phase == "dependency":
        service.scan.assert_not_called()


def test_auth_configuration_rate_limit_and_docs(tmp_path):
    client = client_for(tmp_path, rate_limit_requests=3)
    assert client.post(ROUTE, json={}).status_code == 401
    assert client.post(ROUTE, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(ROUTE, json={}, headers=auth()).status_code == 200
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    unconfigured = client_for(tmp_path, api_key="")
    assert unconfigured.post(ROUTE, json={}, headers=auth()).status_code == 500
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_ui_session_auth_uses_existing_dependency(tmp_path):
    from app.core.ui_session import UI_SESSION_COOKIE_NAME, create_ui_session_token

    client = client_for(tmp_path)
    client.cookies.set(UI_SESSION_COOKIE_NAME, create_ui_session_token(client.app.state.settings))
    response = client.post(ROUTE, json={}, headers={"X-VaultBridge-UI-Request": "1"})
    assert response.status_code == 200


@pytest.mark.parametrize("state", ["not_requested", "complete", "partial", "unavailable"])
def test_empty_and_partial_sub_evidence_remains_successful(tmp_path, state):
    result = replace(
        result_fixture(), findings=(), findings_truncated=False,
        candidates=d.CandidateCoverage(state, 0),
    )
    client, _ = injected_client(tmp_path, result)
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 200
    assert response.json() == wire_json(result)


def test_real_domain_availability_failure_is_503(tmp_path, monkeypatch):
    client = client_for(tmp_path)

    def unavailable():
        raise OSError(PRIVATE)

    monkeypatch.setattr(client.app.state.vault_service, "bounded_markdown_snapshot", unavailable)
    response = client.post(ROUTE, json={}, headers=auth())
    assert response.status_code == 503
    assert response.json() == {"detail": "scan_unavailable"}


def test_create_app_accepts_injected_hygiene_owner(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app

    original = client_for(tmp_path).app
    service = Mock(spec=d.KnowledgeHygieneService)
    service.scan.return_value = result_fixture()
    application = create_app(
        settings=original.state.settings,
        semantic_search_service=original.state.semantic_search_service,
        knowledge_hygiene_service=service,
    )
    assert application.state.knowledge_hygiene_service is service
    response = TestClient(application).post(ROUTE, json={}, headers=auth())
    assert response.json() == wire_json(service.scan.return_value)
    service.scan.assert_called_once_with(d.KnowledgeHygieneRequest())


@pytest.mark.parametrize("options", [
    {}, {"groups": [], "duplicate_source_limit": 2, "semantic_candidates": True, "inspect_derived_index": True},
    {"finding_limit": 1, "inspect_derived_index": True},
])
def test_real_domain_parity_and_read_only(tmp_path, options, monkeypatch):
    (tmp_path / "z.md").write_text(
        "---\naliases: [private-alias, private-alias]\n---\n[[private-link]]", encoding="utf-8",
    )
    (tmp_path / "Empty.md").write_text("", encoding="utf-8")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    client = client_for(tmp_path)
    state = client.app.state
    service = state.knowledge_hygiene_service
    assert service._vault is state.vault_service
    assert service._relationships is state.relationship_service
    assert service._duplicates is state.duplicate_candidate_service
    assert service._semantic is state.semantic_search_service

    def forbidden(*args, **kwargs):
        pytest.fail("Mutation or unbounded semantic path invoked")

    for owner, names in (
        (state.vault_service, ("create_note", "append_note")),
        (state.semantic_search_service, ("sync", "sync_paths", "search", "query_basis")),
        (state.semantic_indexer, ("enqueue", "start")),
        (state.duplicate_candidate_service, ("find_candidates",)),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    domain_options = dict(options)
    if "groups" in domain_options:
        domain_options["groups"] = frozenset(domain_options["groups"])
    request = d.KnowledgeHygieneRequest(**domain_options)
    expected = service.scan(request)
    response = client.post(ROUTE, json=options, headers=auth())
    assert response.status_code == 200
    assert response.json() == wire_json(expected)
    assert "private-alias" not in response.text
    assert "private-link" not in response.text
    assert str(tmp_path) not in response.text
    if options.get("semantic_candidates"):
        assert response.json()["candidates"]["reasons"] == ["semantic_unavailable"]
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert not (tmp_path / ".test-semantic").exists()


def test_openapi_exact_route_request_and_response_contract(tmp_path):
    schema = client_for(tmp_path).app.openapi()
    operation = schema["paths"][ROUTE]["post"]
    assert operation["operationId"] == "scanKnowledgeHygieneV1"
    assert operation["tags"] == ["knowledge"]
    assert operation["requestBody"]["required"] is True
    assert "/knowledge/hygiene/scan" not in schema["paths"]
    models = schema["components"]["schemas"]
    request = models["KnowledgeHygieneScanRequest"]
    assert request["additionalProperties"] is False
    fields = request["properties"]
    assert set(fields) == set(asdict(d.KnowledgeHygieneRequest()))
    assert fields["groups"]["default"] == ["relationships", "isolation", "frontmatter", "aliases"]
    assert fields["groups"]["items"]["enum"] == list(get_args(d.Group))
    for field, low, high, default in (("finding_limit", 1, 500, 500), ("duplicate_source_limit", 0, 20, 0)):
        assert fields[field]["type"] == "integer"
        assert (fields[field]["minimum"], fields[field]["maximum"], fields[field]["default"]) == (low, high, default)
    assert fields["semantic_candidates"]["default"] is False
    assert "duplicate_source_limit > 0" in fields["semantic_candidates"]["description"]
    assert fields["inspect_derived_index"]["default"] is False
    response = models["KnowledgeHygieneScanResponse"]["properties"]
    assert set(response) == {"findings", "scan", "candidates", "derived_index", "findings_truncated"}
    findings = response["findings"]
    assert findings["maxItems"] == 500
    mapping = findings["items"]["discriminator"]["mapping"]
    assert set(mapping) == set(get_args(d.DiagnosticKind))
    for kind, ref in mapping.items():
        model = models[ref.rsplit("/", 1)[-1]]
        assert model["properties"]["related_paths"]["maxItems"] == 10
        assert model["properties"]["evidence"]["$ref"].startswith("#/components/schemas/")
        assert model["additionalProperties"] is False
    assert models["DerivedIndexEvidence"]["properties"]["status"]["enum"] == list(get_args(d.DerivedStatus))
    for code in ("422", "503", "500"):
        error_schema = operation["responses"][code]["content"]["application/json"]["schema"]
        assert error_schema["$ref"].endswith("HygieneErrorResponse")
    for forbidden in ("severity", "repair", "finding_id", "vault_root", "snippet", "embedding", "alias_key"):
        assert forbidden not in response
