from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.api.notes import router as notes_router
from app.api.search import router as search_router
from tests.test_api import RecordingIndexer, auth, client_for
from tests.test_request_observability import capture_application_logs, lifecycle_records, parsed_records


@dataclass(frozen=True)
class ApiContractCase:
    method: str
    legacy_path: str
    v1_path: str
    legacy_operation_id: str
    v1_operation_id: str
    success_kwargs: dict
    validation_kwargs: dict
    failure_kwargs: dict
    service_owner: str
    service_method: str


API_CONTRACT_CASES = (
    ApiContractCase(
        method="POST",
        legacy_path="/notes",
        v1_path="/api/v1/notes",
        legacy_operation_id="createNote",
        v1_operation_id="createNoteV1",
        success_kwargs={
            "json": {"title": "Created", "folder": "Inbox", "content": "Body", "tags": []}
        },
        validation_kwargs={"json": {}},
        failure_kwargs={
            "json": {"title": "Conflict", "folder": "Inbox", "content": "Changed", "tags": []}
        },
        service_owner="vault_service",
        service_method="create_note",
    ),
    ApiContractCase(
        method="POST",
        legacy_path="/notes/append",
        v1_path="/api/v1/notes/append",
        legacy_operation_id="appendNote",
        v1_operation_id="appendNoteV1",
        success_kwargs={"json": {"path": "Seed.md", "content": "Addition"}},
        validation_kwargs={"json": {}},
        failure_kwargs={"json": {"path": "Missing.md", "content": "Addition"}},
        service_owner="vault_service",
        service_method="append_note",
    ),
    ApiContractCase(
        method="GET",
        legacy_path="/notes/read",
        v1_path="/api/v1/notes/read",
        legacy_operation_id="readNote",
        v1_operation_id="readNoteV1",
        success_kwargs={"params": {"path": "Seed.md"}},
        validation_kwargs={},
        failure_kwargs={"params": {"path": "Missing.md"}},
        service_owner="vault_service",
        service_method="read_note",
    ),
    ApiContractCase(
        method="POST",
        legacy_path="/notes/search",
        v1_path="/api/v1/notes/search",
        legacy_operation_id="searchNotes",
        v1_operation_id="searchNotesV1",
        success_kwargs={"json": {"query": "seed"}},
        validation_kwargs={"json": {}},
        failure_kwargs={"json": {"query": "seed", "folder": "../"}},
        service_owner="vault_service",
        service_method="search_notes",
    ),
    ApiContractCase(
        method="POST",
        legacy_path="/notes/related",
        v1_path="/api/v1/notes/related",
        legacy_operation_id="findRelatedNotes",
        v1_operation_id="findRelatedNotesV1",
        success_kwargs={"json": {"text": "seed concept"}},
        validation_kwargs={"json": {}},
        failure_kwargs={"json": {"text": "seed concept", "folder": "../"}},
        service_owner="semantic_search_service",
        service_method="search",
    ),
    ApiContractCase(
        method="POST",
        legacy_path="/notes/duplicates",
        v1_path="/api/v1/notes/duplicates",
        legacy_operation_id="findDuplicateCandidates",
        v1_operation_id="findDuplicateCandidatesV1",
        success_kwargs={"json": {"title": "Seed"}},
        validation_kwargs={"json": {}},
        failure_kwargs={"json": {"title": "Seed", "folder": "../"}},
        service_owner="duplicate_candidate_service",
        service_method="find_candidates",
    ),
    ApiContractCase(
        method="GET",
        legacy_path="/notes/list",
        v1_path="/api/v1/notes/list",
        legacy_operation_id="listNotes",
        v1_operation_id="listNotesV1",
        success_kwargs={},
        validation_kwargs={"params": {"limit": 0}},
        failure_kwargs={"params": {"folder": "../"}},
        service_owner="vault_service",
        service_method="list_notes",
    ),
)


def prepared_client(root: Path) -> TestClient:
    root.mkdir(parents=True)
    seed = root / "Seed.md"
    seed.write_text("Seed body for literal search.", encoding="utf-8")
    os.utime(seed, (1_700_000_000, 1_700_000_000))
    return client_for(root, semantic_indexer=RecordingIndexer())


def send(
    client: TestClient,
    case: ApiContractCase,
    path: str,
    kwargs: dict,
    *,
    headers: dict[str, str] | None = None,
):
    return client.request(case.method, path, headers=headers, **kwargs)


def assert_same_response(legacy_response, v1_response) -> None:
    assert v1_response.status_code == legacy_response.status_code
    assert v1_response.headers["content-type"] == legacy_response.headers["content-type"]
    assert v1_response.content == legacy_response.content


def without_schema_titles(value):
    if isinstance(value, dict):
        return {
            key: without_schema_titles(item)
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [without_schema_titles(item) for item in value]
    return value


def prepare_representative_failure(
    client: TestClient,
    case: ApiContractCase,
    path: str,
) -> None:
    if case.legacy_operation_id == "createNote":
        response = client.post(
            path,
            headers=auth(),
            json={"title": "Conflict", "folder": "Inbox", "content": "Original", "tags": []},
        )
        assert response.status_code == 200


def test_openapi_contract_matrix_has_stable_unique_operation_ids_and_identical_shapes():
    schema = main.app.openapi()
    expected_paths = {"/health", "/health/live", "/health/ready"}
    operation_ids: list[str] = []

    for case in API_CONTRACT_CASES:
        expected_paths.update((case.legacy_path, case.v1_path))
        method = case.method.casefold()
        legacy_operation = schema["paths"][case.legacy_path][method]
        v1_operation = schema["paths"][case.v1_path][method]
        assert legacy_operation["operationId"] == case.legacy_operation_id
        assert v1_operation["operationId"] == case.v1_operation_id

        legacy_shape = without_schema_titles(
            {key: value for key, value in legacy_operation.items() if key != "operationId"}
        )
        v1_shape = without_schema_titles(
            {key: value for key, value in v1_operation.items() if key != "operationId"}
        )
        assert v1_shape == legacy_shape

    assert set(schema["paths"]) == expected_paths
    for path_item in schema["paths"].values():
        operation_ids.extend(operation["operationId"] for operation in path_item.values())
    assert len(operation_ids) == len(set(operation_ids))


def test_legacy_and_v1_routes_share_endpoint_models_and_dependencies():
    routes = {route.path: route for route in (*notes_router.routes, *search_router.routes)}

    for case in API_CONTRACT_CASES:
        legacy_route = routes[case.legacy_path]
        v1_route = routes[case.v1_path]
        assert v1_route.endpoint is legacy_route.endpoint
        assert v1_route.response_model is legacy_route.response_model
        assert v1_route.dependencies == legacy_route.dependencies


@pytest.mark.parametrize("case", API_CONTRACT_CASES, ids=lambda case: case.legacy_operation_id)
def test_legacy_and_v1_success_validation_and_failure_responses_match(tmp_path, case):
    legacy_client = prepared_client(tmp_path / "legacy")
    v1_client = prepared_client(tmp_path / "v1")

    legacy_success = send(legacy_client, case, case.legacy_path, case.success_kwargs, headers=auth())
    v1_success = send(v1_client, case, case.v1_path, case.success_kwargs, headers=auth())
    assert legacy_success.status_code == 200
    assert_same_response(legacy_success, v1_success)

    legacy_validation = send(
        legacy_client,
        case,
        case.legacy_path,
        case.validation_kwargs,
        headers=auth(),
    )
    v1_validation = send(v1_client, case, case.v1_path, case.validation_kwargs, headers=auth())
    assert legacy_validation.status_code == 422
    assert_same_response(legacy_validation, v1_validation)

    prepare_representative_failure(legacy_client, case, case.legacy_path)
    prepare_representative_failure(v1_client, case, case.v1_path)
    legacy_failure = send(
        legacy_client,
        case,
        case.legacy_path,
        case.failure_kwargs,
        headers=auth(),
    )
    v1_failure = send(v1_client, case, case.v1_path, case.failure_kwargs, headers=auth())
    assert legacy_failure.status_code >= 400
    assert_same_response(legacy_failure, v1_failure)


@pytest.mark.parametrize("case", API_CONTRACT_CASES, ids=lambda case: case.legacy_operation_id)
def test_legacy_and_v1_authentication_match_for_missing_invalid_and_valid_keys(tmp_path, case):
    for index, path in enumerate((case.legacy_path, case.v1_path)):
        missing_client = prepared_client(tmp_path / f"missing-{index}")
        invalid_client = prepared_client(tmp_path / f"invalid-{index}")
        valid_client = prepared_client(tmp_path / f"valid-{index}")

        missing = send(missing_client, case, path, case.success_kwargs)
        invalid = send(
            invalid_client,
            case,
            path,
            case.success_kwargs,
            headers={"Authorization": "Bearer invalid-key"},
        )
        valid = send(valid_client, case, path, case.success_kwargs, headers=auth())

        assert missing.status_code == 401
        assert invalid.status_code == 401
        assert missing.json() == invalid.json() == {"detail": "Invalid API key"}
        assert valid.status_code == 200


@pytest.mark.parametrize("case", API_CONTRACT_CASES, ids=lambda case: case.legacy_operation_id)
def test_each_legacy_and_v1_request_calls_its_domain_operation_once(tmp_path, case):
    for index, path in enumerate((case.legacy_path, case.v1_path)):
        client = prepared_client(tmp_path / f"single-call-{index}")
        owner = getattr(client.app.state, case.service_owner)
        method = getattr(owner, case.service_method)
        with patch.object(owner, case.service_method, wraps=method) as operation:
            response = send(client, case, path, case.success_kwargs, headers=auth())

        assert response.status_code == 200
        assert operation.call_count == 1


def test_operational_and_hidden_routes_remain_unversioned(tmp_path):
    schema = main.app.openapi()
    assert schema["paths"]["/health"]["get"]["operationId"] == "healthCheck"
    assert schema["paths"]["/health/live"]["get"]["operationId"] == "livenessCheck"
    assert schema["paths"]["/health/ready"]["get"]["operationId"] == "readinessCheck"
    assert not any(path.startswith("/api/v1/health") for path in schema["paths"])

    client = prepared_client(tmp_path / "operational")
    privacy = client.get("/privacy")
    versioned_privacy = client.get("/api/v1/privacy")
    assert privacy.status_code == 200
    assert privacy.headers["content-type"].startswith("text/plain")
    assert "/privacy" not in schema["paths"]
    assert versioned_privacy.status_code == 404


def test_v1_requests_keep_request_ids_safe_logs_and_versioned_route_templates(tmp_path):
    client = prepared_client(tmp_path / "observability")
    private_body = "private versioned request body"

    with capture_application_logs() as stream:
        response = client.post(
            "/api/v1/notes",
            headers={**auth(), "X-Request-ID": "caller-id"},
            json={"title": "Versioned", "content": private_body, "tags": []},
        )

    lifecycle = lifecycle_records(parsed_records(stream))
    assert response.status_code == 200
    assert response.headers["x-request-id"] != "caller-id"
    assert [record["event"] for record in lifecycle] == ["request_started", "request_completed"]
    assert lifecycle[1]["route"] == "/api/v1/notes"
    assert {record["request_id"] for record in lifecycle} == {response.headers["x-request-id"]}
    assert private_body not in stream.getvalue()


def test_v1_unexpected_failure_uses_standard_failed_request_event(tmp_path):
    application = prepared_client(tmp_path / "failure-observability").app
    with patch.object(
        application.state.vault_service,
        "list_notes",
        side_effect=RuntimeError("private versioned failure"),
    ):
        client = TestClient(application, raise_server_exceptions=False)
        with capture_application_logs() as stream:
            response = client.get("/api/v1/notes/list", headers=auth())

    lifecycle = lifecycle_records(parsed_records(stream))
    assert response.status_code == 500
    assert [record["event"] for record in lifecycle] == ["request_started", "request_failed"]
    assert lifecycle[1]["route"] == "/api/v1/notes/list"
    assert lifecycle[1]["request_id"] == response.headers["x-request-id"]
    assert "private versioned failure" not in stream.getvalue()


def test_runtime_openapi_endpoints_remain_disabled(tmp_path):
    client = prepared_client(tmp_path / "disabled-docs")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
