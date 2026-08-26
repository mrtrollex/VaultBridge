from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import StringIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import (
    JsonLogFormatter,
    current_request_id,
    log_event,
    request_id_context,
)
from app.core.observability import RequestObservabilityMiddleware
from app.services.indexer import BackgroundSemanticIndexer
from tests.test_api import RecordingIndexer, auth, client_for

logger = logging.getLogger("vaultbridge.test.request_observability")


@contextmanager
def capture_application_logs() -> Iterator[StringIO]:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    application_logger = logging.getLogger("vaultbridge")
    previous_level = application_logger.level
    application_logger.setLevel(logging.DEBUG)
    application_logger.addHandler(handler)
    try:
        yield stream
    finally:
        application_logger.removeHandler(handler)
        application_logger.setLevel(previous_level)


def parsed_records(stream: StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


def lifecycle_records(records: list[dict]) -> list[dict]:
    return [record for record in records if record["event"].startswith("request_")]


def http_scope(*, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/test/request-observability",
        "raw_path": b"/test/request-observability",
        "query_string": b"",
        "headers": headers or [],
        "client": ("test", 1),
        "server": ("testserver", 80),
        "root_path": "",
    }


def test_request_id_header_lifecycle_application_context_latency_and_privacy(tmp_path):
    indexer = RecordingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)
    api_key = "test-secret"
    note_content = "private request body that must never be logged"

    with capture_application_logs() as stream:
        response = client.post(
            "/notes",
            headers={**auth(), "X-Request-ID": "caller-id-must-not-be-trusted"},
            json={
                "title": "Correlated",
                "folder": "Inbox",
                "content": note_content,
                "tags": [],
            },
        )

    request_id = response.headers["x-request-id"]
    records = parsed_records(stream)
    lifecycle = lifecycle_records(records)
    note_created = next(record for record in records if record["event"] == "note_created")
    raw = stream.getvalue()

    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert request_id != "caller-id-must-not-be-trusted"
    assert [record["event"] for record in lifecycle] == [
        "request_started",
        "request_completed",
    ]
    assert {record["request_id"] for record in lifecycle} == {request_id}
    assert note_created["request_id"] == request_id
    assert lifecycle[0]["method"] == "POST"
    assert lifecycle[1]["route"] == "/notes"
    assert lifecycle[1]["status_code"] == 200
    assert lifecycle[1]["duration_ms"] >= 0
    assert note_content not in raw
    assert api_key not in raw
    assert "Authorization" not in raw


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [("/health/live", 200), ("/health/ready", 503)],
)
def test_health_probes_use_standard_request_observability(tmp_path, path, expected_status):
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())

    with capture_application_logs() as stream:
        response = client.get(path)

    records = parsed_records(stream)
    lifecycle = lifecycle_records(records)
    request_id = response.headers["x-request-id"]

    assert response.status_code == expected_status
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert records == lifecycle
    assert [record["event"] for record in lifecycle] == [
        "request_started",
        "request_completed",
    ]
    assert {record["request_id"] for record in lifecycle} == {request_id}
    assert lifecycle[0]["method"] == "GET"
    assert lifecycle[1]["route"] == path
    assert lifecycle[1]["status_code"] == expected_status


@pytest.mark.parametrize(
    "caller_request_id",
    [
        b"hostile\nrequest-id",
        b"hostile\rrequest-id",
        b"hostile\trequest-id",
        b"x" * 4096,
    ],
)
def test_hostile_incoming_request_ids_are_ignored_at_the_asgi_boundary(caller_request_id):
    sent_messages: list[dict] = []

    async def application(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent_messages.append(message)

    with capture_application_logs() as stream:
        asyncio.run(
            RequestObservabilityMiddleware(application)(
                http_scope(headers=[(b"x-request-id", caller_request_id)]),
                receive,
                send,
            )
        )

    records = lifecycle_records(parsed_records(stream))
    response_headers = dict(sent_messages[0]["headers"])
    request_id = response_headers[b"x-request-id"].decode("ascii")
    caller_text = caller_request_id.decode("ascii")
    escaped_caller_text = json.dumps(caller_text)[1:-1]
    raw = stream.getvalue()

    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert request_id != caller_text
    assert [record["event"] for record in records] == [
        "request_started",
        "request_completed",
    ]
    assert {record["request_id"] for record in records} == {request_id}
    assert caller_text not in raw
    assert escaped_caller_text not in raw


def test_handled_auth_validation_http_and_not_found_errors_have_one_terminal_event(tmp_path):
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())
    calls = [
        lambda: client.get(
            "/notes/list",
            headers={"Authorization": "Bearer wrong-private-api-key"},
        ),
        lambda: client.post("/notes", headers=auth(), json={"content": "private validation body"}),
        lambda: client.get("/notes/read", headers=auth(), params={"path": "Missing private.md"}),
        lambda: client.get("/unmatched-private-location", headers=auth()),
    ]

    for call, expected_status in zip(calls, (401, 422, 404, 404), strict=True):
        with capture_application_logs() as stream:
            response = call()

        records = lifecycle_records(parsed_records(stream))
        terminal = [record for record in records if record["event"] != "request_started"]
        assert response.status_code == expected_status
        assert response.headers["x-request-id"] == records[0]["request_id"]
        assert len(terminal) == 1
        assert terminal[0]["event"] == "request_completed"
        assert terminal[0]["status_code"] == expected_status
        assert terminal[0]["request_id"] == response.headers["x-request-id"]
        raw = stream.getvalue()
        assert "wrong-private-api-key" not in raw
        assert "private validation body" not in raw
        assert "Missing private.md" not in raw
        assert "unmatched-private-location" not in raw


def test_rate_limited_response_keeps_request_id_lifecycle_and_log_privacy(tmp_path):
    client = client_for(
        tmp_path,
        semantic_indexer=RecordingIndexer(),
        rate_limit_requests=1,
    )
    invalid_headers = {"Authorization": "Bearer private-rate-limit-secret"}

    assert client.get("/notes/list", headers=invalid_headers).status_code == 401
    with capture_application_logs() as stream:
        response = client.get("/notes/list", headers=invalid_headers)

    records = lifecycle_records(parsed_records(stream))
    request_id = response.headers["x-request-id"]
    assert response.status_code == 429
    assert response.json() == {"detail": "Rate limit exceeded"}
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert [record["event"] for record in records] == [
        "request_started",
        "request_completed",
    ]
    assert {record["request_id"] for record in records} == {request_id}
    assert records[1]["route"] == "/notes/list"
    assert records[1]["status_code"] == 429
    assert "private-rate-limit-secret" not in stream.getvalue()


def test_search_query_and_raw_query_string_are_not_logged(tmp_path):
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())
    search_query = "private semantic search phrase"
    query_path = "private raw query path.md"

    with capture_application_logs() as stream:
        search_response = client.post(
            "/notes/search",
            headers=auth(),
            json={"query": search_query},
        )
        read_response = client.get(
            "/notes/read",
            headers=auth(),
            params={"path": query_path},
        )

    assert search_response.status_code == 200
    assert read_response.status_code == 404
    assert search_query not in stream.getvalue()
    assert query_path not in stream.getvalue()


def test_unexpected_exception_logs_one_failed_terminal_event_without_secret_message(tmp_path):
    application = client_for(tmp_path, semantic_indexer=RecordingIndexer()).app

    @application.get("/test/failure")
    def fail_request() -> None:
        raise RuntimeError("Bearer secret-key and private note contents")

    client = TestClient(application, raise_server_exceptions=False)
    with capture_application_logs() as stream:
        response = client.get("/test/failure")

    records = lifecycle_records(parsed_records(stream))
    assert response.status_code == 500
    assert response.headers["x-request-id"] == records[0]["request_id"]
    assert [record["event"] for record in records] == ["request_started", "request_failed"]
    assert records[0]["request_id"] == records[1]["request_id"]
    assert response.headers["x-request-id"] == records[1]["request_id"]
    assert records[1]["route"] == "/test/failure"
    assert records[1]["status_code"] == 500
    assert records[1]["duration_ms"] >= 0
    assert records[1]["error_type"] == "RuntimeError"
    assert "secret-key" not in stream.getvalue()
    assert "private note contents" not in stream.getvalue()


def test_cancelled_request_omits_unobserved_status_and_restores_context():
    sent_messages: list[dict] = []

    async def cancelled_application(_scope, _receive, _send) -> None:
        raise asyncio.CancelledError()

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent_messages.append(message)

    async def run_cancelled_request() -> None:
        with pytest.raises(asyncio.CancelledError):
            await RequestObservabilityMiddleware(cancelled_application)(
                http_scope(),
                receive,
                send,
            )
        assert current_request_id() is None
        log_event(logger, logging.INFO, "outside_cancelled_request", "Outside cancelled request")

    with capture_application_logs() as stream:
        asyncio.run(run_cancelled_request())

    records = parsed_records(stream)
    lifecycle = lifecycle_records(records)
    outside_record = next(
        record for record in records if record["event"] == "outside_cancelled_request"
    )

    assert sent_messages == []
    assert [record["event"] for record in lifecycle] == [
        "request_started",
        "request_failed",
    ]
    assert lifecycle[0]["request_id"] == lifecycle[1]["request_id"]
    assert "status_code" not in lifecycle[1]
    assert lifecycle[1]["duration_ms"] >= 0
    assert lifecycle[1]["error_type"] == "CancelledError"
    assert current_request_id() is None
    assert "request_id" not in outside_record


def test_concurrent_requests_keep_response_and_log_context_isolated():
    application = FastAPI()
    barrier = threading.Barrier(2)

    @application.get("/test/concurrent/{marker}")
    def concurrent_request(marker: str) -> dict[str, str]:
        log_event(logger, logging.INFO, "concurrent_application_event", "Concurrent request event", operation=marker)
        barrier.wait(timeout=2)
        return {"marker": marker}

    client = TestClient(RequestObservabilityMiddleware(application))
    with capture_application_logs() as stream:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = {
                marker: executor.submit(client.get, f"/test/concurrent/{marker}")
                for marker in ("alpha", "beta")
            }
            responses = {marker: future.result() for marker, future in responses.items()}

    records = parsed_records(stream)
    application_records = {
        record["operation"]: record
        for record in records
        if record["event"] == "concurrent_application_event"
    }
    completed_records = {
        record["request_id"]: record
        for record in records
        if record["event"] == "request_completed"
    }

    assert set(application_records) == {"alpha", "beta"}
    response_ids = {response.headers["x-request-id"] for response in responses.values()}
    assert len(response_ids) == 2
    assert set(completed_records) == response_ids
    for marker, response in responses.items():
        request_id = response.headers["x-request-id"]
        assert application_records[marker]["request_id"] == request_id
        assert completed_records[request_id]["route"] == "/test/concurrent/{marker}"


def test_request_context_is_restored_and_outside_logs_omit_request_id():
    active_request_ids: list[str | None] = []
    sent_messages: list[dict] = []

    async def application(_scope, _receive, send) -> None:
        active_request_ids.append(current_request_id())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent_messages.append(message)

    async def run_request() -> None:
        await RequestObservabilityMiddleware(application)(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/test/context",
                "raw_path": b"/test/context",
                "query_string": b"",
                "headers": [],
                "client": ("test", 1),
                "server": ("testserver", 80),
                "root_path": "",
            },
            receive,
            send,
        )
        assert current_request_id() is None
        log_event(logger, logging.INFO, "outside_request", "Outside request")

    with capture_application_logs() as stream:
        asyncio.run(run_request())

    records = parsed_records(stream)
    outside_record = next(record for record in records if record["event"] == "outside_request")
    response_headers = dict(sent_messages[0]["headers"])
    assert active_request_ids == [response_headers[b"x-request-id"].decode("ascii")]
    assert current_request_id() is None
    assert "request_id" not in outside_record


def test_explicit_safe_request_id_is_not_overwritten_by_context():
    with capture_application_logs() as stream:
        with request_id_context("active-context"):
            log_event(
                logger,
                logging.INFO,
                "explicit_request_id",
                "Explicit request ID",
                request_id="explicit-safe-id",
            )

    [record] = parsed_records(stream)
    assert record["request_id"] == "explicit-safe-id"


def test_background_indexer_worker_does_not_inherit_request_context(tmp_path):
    worker_finished = threading.Event()

    def sync_paths(paths, _cancel_event):
        log_event(
            logger,
            logging.INFO,
            "background_worker_event",
            "Background worker event",
            indexed_notes=len(paths),
        )
        worker_finished.set()
        return {"indexed": len(paths), "unchanged": 0, "removed": 0}

    indexer = BackgroundSemanticIndexer(
        lambda _cancel_event: {"indexed": 0, "unchanged": 0, "removed": 0},
        sync_paths,
    )
    client = client_for(tmp_path, semantic_indexer=indexer)
    try:
        with capture_application_logs() as stream:
            response = client.post(
                "/notes",
                headers=auth(),
                json={"title": "Background boundary", "content": "Body", "tags": []},
            )
            assert worker_finished.wait(timeout=2)
            indexer.wait(timeout=2)

        request_id = response.headers["x-request-id"]
        records = parsed_records(stream)
        note_created = next(record for record in records if record["event"] == "note_created")
        queued = next(record for record in records if record["event"] == "targeted_reindex_queued")
        worker = next(record for record in records if record["event"] == "background_worker_event")
        assert note_created["request_id"] == request_id
        assert queued["request_id"] == request_id
        assert "request_id" not in worker
    finally:
        indexer.shutdown()


def test_queue_failure_keeps_committed_write_and_request_correlation(tmp_path):
    class FailingIndexer:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def enqueue(self, path: str) -> None:
            self.paths.append(path)
            raise RuntimeError("Bearer queue-secret")

    indexer = FailingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)
    note_content = "private committed queue-degradation body"

    with capture_application_logs() as stream:
        response = client.post(
            "/notes",
            headers=auth(),
            json={
                "title": "Queue degradation",
                "folder": "Inbox",
                "content": note_content,
                "tags": [],
            },
        )
        log_event(logger, logging.INFO, "outside_queue_failure_request", "Outside request")

    request_id = response.headers["x-request-id"]
    records = parsed_records(stream)
    correlated_events = [
        record
        for record in records
        if record["event"]
        in {
            "request_started",
            "note_created",
            "targeted_reindex_queue_failed",
            "request_completed",
        }
    ]
    outside_record = next(
        record for record in records if record["event"] == "outside_queue_failure_request"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    assert (tmp_path / "Inbox" / "Queue degradation.md").read_text(encoding="utf-8").endswith(
        f"{note_content}\n"
    )
    assert indexer.paths == [response.json()["path"]]
    assert [record["event"] for record in correlated_events] == [
        "request_started",
        "note_created",
        "targeted_reindex_queue_failed",
        "request_completed",
    ]
    assert {record["request_id"] for record in correlated_events} == {request_id}
    assert "request_id" not in outside_record
    assert note_content not in stream.getvalue()
    assert "queue-secret" not in stream.getvalue()


def test_logging_failure_does_not_change_response_semantics(tmp_path):
    class RaisingHandler(logging.Handler):
        def emit(self, _record: logging.LogRecord) -> None:
            raise RuntimeError("logging is unavailable")

    application_logger = logging.getLogger("vaultbridge")
    handler = RaisingHandler()
    application_logger.addHandler(handler)
    try:
        response = client_for(tmp_path, semantic_indexer=RecordingIndexer()).get(
            "/notes/list",
            headers=auth(),
        )
    finally:
        application_logger.removeHandler(handler)

    assert response.status_code == 200
    assert response.json()["notes"] == []
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])
