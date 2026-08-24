from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from types import SimpleNamespace

import numpy as np
import pytest

from app.api.notes import AppendNoteRequest, CreateNoteRequest, append_note, create_note
from app.core.logging import JsonLogFormatter, configure_application_logging, log_event
from app.main import lifespan
from app.repositories.semantic import SemanticRepository
from app.services.indexer import BackgroundSemanticIndexer
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService


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


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
    ],
)
def test_json_formatter_emits_core_fields_levels_unicode_and_context(level, expected):
    logger = logging.getLogger("vaultbridge.test.formatter")

    with capture_application_logs() as stream:
        log_event(
            logger,
            level,
            "test_event",
            "Nočné zálohy",
            operation="test",
            note_path="Poznámky\\Zálohy.md",
            indexed_notes=2,
        )

    [record] = parsed_records(stream)
    timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    assert timestamp.tzinfo == timezone.utc
    assert record == {
        "timestamp": record["timestamp"],
        "level": expected,
        "logger": "vaultbridge.test.formatter",
        "event": "test_event",
        "message": "Nočné zálohy",
        "operation": "test",
        "note_path": "Poznámky/Zálohy.md",
        "indexed_notes": 2,
    }


def test_formatter_escapes_control_characters_without_corrupting_adjacent_records():
    logger = logging.getLogger("vaultbridge.test.controls")
    message = "Prvý riadok\nDruhý\tstĺpec\rNUL:\x00 koniec"

    with capture_application_logs() as stream:
        log_event(logger, logging.INFO, "control_event", message)
        log_event(logger, logging.INFO, "adjacent_event", "Ďalší záznam")

    physical_lines = stream.getvalue().splitlines()
    assert len(physical_lines) == 2
    records = [json.loads(line) for line in physical_lines]
    assert records[0]["message"] == message
    assert records[1]["message"] == "Ďalší záznam"
    assert [record["event"] for record in records] == [
        "control_event",
        "adjacent_event",
    ]


def test_formatter_exception_is_structured_valid_json_and_omits_sensitive_message():
    logger = logging.getLogger("vaultbridge.test.exception")

    with capture_application_logs() as stream:
        try:
            raise RuntimeError("Authorization: Bearer secret-api-key private note content")
        except RuntimeError:
            log_event(
                logger,
                logging.ERROR,
                "safe_failure",
                "A safe operation failed",
                exc_info=True,
                authorization="Bearer secret-api-key",
                query="private semantic query",
            )

    raw = stream.getvalue()
    [record] = parsed_records(stream)
    assert record["error_type"] == "RuntimeError"
    assert record["exception"]["type"] == "RuntimeError"
    assert record["exception"]["frames"]
    assert all("/" not in frame["file"] and "\\" not in frame["file"] for frame in record["exception"]["frames"])
    assert "secret-api-key" not in raw
    assert "private note content" not in raw
    assert "private semantic query" not in raw
    assert "authorization" not in record
    assert "query" not in record


@pytest.mark.parametrize(
    "note_path",
    [
        "C:relative.md",
        "C:folder\\secret.md",
        "C:\\vault\\secret.md",
        "C:/vault/secret.md",
        "\\\\server\\share\\secret.md",
        "/vault/secret.md",
        "../secret.md",
    ],
)
def test_formatter_omits_unsafe_note_paths(note_path):
    logger = logging.getLogger("vaultbridge.test.paths")

    with capture_application_logs() as stream:
        log_event(logger, logging.INFO, "path_test", "Safe message", note_path=note_path)

    [record] = parsed_records(stream)
    assert "note_path" not in record


@pytest.mark.parametrize(
    ("note_path", "expected"),
    [
        ("Folder/Note.md", "Folder/Note.md"),
        ("Poznámky\\Zálohy.md", "Poznámky/Zálohy.md"),
    ],
)
def test_formatter_accepts_and_normalizes_vault_relative_note_paths(note_path, expected):
    logger = logging.getLogger("vaultbridge.test.safe_paths")

    with capture_application_logs() as stream:
        log_event(logger, logging.INFO, "path_test", "Safe message", note_path=note_path)

    [record] = parsed_records(stream)
    assert record["note_path"] == expected


def test_concurrent_application_logs_remain_one_valid_json_object_per_line():
    logger = logging.getLogger("vaultbridge.test.concurrent")

    with capture_application_logs() as stream:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    log_event,
                    logger,
                    logging.INFO,
                    "concurrent_event",
                    "Concurrent event",
                    indexed_notes=index,
                )
                for index in range(50)
            ]
            for future in futures:
                future.result()

    records = parsed_records(stream)
    assert len(records) == 50
    assert {record["indexed_notes"] for record in records} == set(range(50))
    assert {record["event"] for record in records} == {"concurrent_event"}


def test_log_event_swallows_logging_infrastructure_failure():
    class BrokenLogger:
        def log(self, *_args, **_kwargs):
            raise RuntimeError("broken logging handler")

    log_event(BrokenLogger(), logging.INFO, "ignored", "Ignored logging failure")


def test_application_logging_configuration_does_not_duplicate_json_handler():
    application_logger = configure_application_logging()
    configure_application_logging()

    json_handlers = [
        handler
        for handler in application_logger.handlers
        if isinstance(handler.formatter, JsonLogFormatter)
    ]
    assert len(json_handlers) == 1
    assert application_logger.propagate is False


class ConstantEmbedder:
    def embed(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [np.asarray([1.0, 0.0], dtype=np.float32) for _ in texts]


def semantic_service(tmp_path, embedder=None) -> SemanticSearchService:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True)
    return SemanticSearchService(
        vault_root=vault_root,
        repository=SemanticRepository(tmp_path / "data" / "semantic-index.sqlite3"),
        model_name="logging/test",
        embedder=embedder or ConstantEmbedder(),
    )


def delay_callback_registration_until_worker_completes(monkeypatch, worker_completed):
    original_add_done_callback = Future.add_done_callback

    def controlled_add_done_callback(future, callback):
        assert worker_completed.wait(timeout=2)
        return original_add_done_callback(future, callback)

    monkeypatch.setattr(Future, "add_done_callback", controlled_add_done_callback)


def test_fast_full_sync_logs_scheduling_before_worker_lifecycle(monkeypatch, tmp_path):
    service = semantic_service(tmp_path)
    (service.vault_root / "note.md").write_text("indexed content", encoding="utf-8")
    worker_completed = threading.Event()

    def sync(cancel_event):
        try:
            return service.sync(cancel_event)
        finally:
            worker_completed.set()

    indexer = BackgroundSemanticIndexer(sync, service.sync_paths)
    delay_callback_registration_until_worker_completes(monkeypatch, worker_completed)
    try:
        with capture_application_logs() as stream:
            assert indexer.start() is True
            assert indexer.wait(timeout=2) == {
                "indexed": 1,
                "unchanged": 0,
                "removed": 0,
            }
    finally:
        indexer.shutdown()

    records = parsed_records(stream)
    assert [record["event"] for record in records] == [
        "semantic_full_sync_scheduled",
        "semantic_sync_started",
        "semantic_sync_completed",
    ]
    assert records[0]["full_sync_required"] is True
    assert indexer.requires_full_sync is False


def test_fast_targeted_refresh_logs_queue_before_worker_lifecycle(monkeypatch, tmp_path):
    service = semantic_service(tmp_path)
    note_path = service.vault_root / "note.md"
    note_path.write_text("initial content", encoding="utf-8")
    service.sync()
    note_path.write_text("changed content", encoding="utf-8")
    worker_completed = threading.Event()

    def sync_paths(paths, cancel_event):
        try:
            return service.sync_paths(paths, cancel_event)
        finally:
            worker_completed.set()

    indexer = BackgroundSemanticIndexer(service.sync, sync_paths)
    delay_callback_registration_until_worker_completes(monkeypatch, worker_completed)
    try:
        with capture_application_logs() as stream:
            assert indexer.enqueue("note.md") is True
            assert indexer.wait(timeout=2) == {
                "indexed": 1,
                "unchanged": 0,
                "removed": 0,
            }
    finally:
        indexer.shutdown()

    records = parsed_records(stream)
    assert [record["event"] for record in records] == [
        "targeted_reindex_queued",
        "targeted_reindex_started",
        "targeted_reindex_completed",
    ]
    assert records[0]["queued_notes"] == 1
    assert indexer.queued_paths == ()


def test_failed_submissions_do_not_log_successful_scheduling_events(monkeypatch):
    def sync(_cancel_event):
        return {"indexed": 0, "unchanged": 0, "removed": 0}

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("executor submission failed")

    full_indexer = BackgroundSemanticIndexer(sync)
    monkeypatch.setattr(full_indexer._executor, "submit", fail_submit)
    try:
        with capture_application_logs() as full_stream:
            assert full_indexer.start() is False
    finally:
        full_indexer.shutdown()

    full_events = [record["event"] for record in parsed_records(full_stream)]
    assert full_events == ["semantic_worker_submission_failed"]
    assert "semantic_full_sync_scheduled" not in full_events

    targeted_indexer = BackgroundSemanticIndexer(sync, lambda _paths, _cancel: None)
    original_targeted_submit = targeted_indexer._executor.submit
    monkeypatch.setattr(targeted_indexer._executor, "submit", fail_submit)
    try:
        with capture_application_logs() as targeted_stream:
            assert targeted_indexer.enqueue("Folder/Note.md") is True
            assert targeted_indexer.queued_paths == ("Folder/Note.md",)
        monkeypatch.setattr(
            targeted_indexer._executor,
            "submit",
            original_targeted_submit,
        )
        with capture_application_logs() as retry_stream:
            assert targeted_indexer.enqueue("Folder/Note.md") is False
            assert targeted_indexer.wait(timeout=2) is None
    finally:
        targeted_indexer.shutdown()

    targeted_events = [record["event"] for record in parsed_records(targeted_stream)]
    assert targeted_events == ["semantic_worker_submission_failed"]
    assert "targeted_reindex_queued" not in targeted_events
    [retry_record] = parsed_records(retry_stream)
    assert retry_record["event"] == "targeted_reindex_queued"
    assert retry_record["queued_notes"] == 1


def test_semantic_lifecycle_logs_safe_start_completion_and_failure_events(tmp_path):
    service = semantic_service(tmp_path / "success")
    private_content = "private note content secret-api-key"
    note_path = service.vault_root / "Zálohy.md"
    note_path.write_text(private_content, encoding="utf-8")

    with capture_application_logs() as success_stream:
        assert service.sync() == {"indexed": 1, "unchanged": 0, "removed": 0}
        note_path.write_text(f"{private_content}\nupdated", encoding="utf-8")
        assert service.sync_paths(["Zálohy.md"]) == {
            "indexed": 1,
            "unchanged": 0,
            "removed": 0,
        }
        service.search("private semantic query", min_score=0.0)

    success_raw = success_stream.getvalue()
    success_records = parsed_records(success_stream)
    assert [record["event"] for record in success_records] == [
        "semantic_sync_started",
        "semantic_sync_completed",
        "targeted_reindex_started",
        "targeted_reindex_completed",
    ]
    assert success_records[1]["operation"] == "full"
    assert success_records[1]["indexed_notes"] == 1
    assert success_records[1]["index_state"] == "ready"
    assert success_records[3]["operation"] == "targeted"
    assert success_records[3]["indexed_notes"] == 1
    assert private_content not in success_raw
    assert "private semantic query" not in success_raw

    class FailingEmbedder:
        def embed(self, _texts):
            raise RuntimeError("private note content secret-api-key")

    failed = semantic_service(tmp_path / "failure", embedder=FailingEmbedder())
    (failed.vault_root / "secret.md").write_text(private_content, encoding="utf-8")
    with capture_application_logs() as failure_stream:
        with pytest.raises(RuntimeError, match="private note content"):
            failed.sync()

    failure_raw = failure_stream.getvalue()
    failure_records = parsed_records(failure_stream)
    assert [record["event"] for record in failure_records] == [
        "semantic_sync_started",
        "semantic_sync_failed",
    ]
    assert failure_records[1]["error_type"] == "RuntimeError"
    assert failure_records[1]["index_state"] == "error"
    assert private_content not in failure_raw
    assert "secret-api-key" not in failure_raw


def test_targeted_reindex_failure_event_is_safe(tmp_path):
    class SwitchableEmbedder(ConstantEmbedder):
        fail = False

        def embed(self, texts):
            if self.fail:
                raise RuntimeError("Authorization: Bearer secret-api-key private query")
            return super().embed(texts)

    embedder = SwitchableEmbedder()
    service = semantic_service(tmp_path, embedder=embedder)
    note_path = service.vault_root / "safe.md"
    note_path.write_text("initial private content", encoding="utf-8")
    service.sync()
    note_path.write_text("changed private content", encoding="utf-8")
    embedder.fail = True

    with capture_application_logs() as stream:
        with pytest.raises(RuntimeError, match="secret-api-key"):
            service.sync_paths(["safe.md"])

    raw = stream.getvalue()
    assert [record["event"] for record in parsed_records(stream)] == [
        "targeted_reindex_started",
        "targeted_reindex_failed",
    ]
    assert "secret-api-key" not in raw
    assert "private content" not in raw


def test_targeted_queue_and_note_mutation_events_exclude_note_content(tmp_path):
    class RecordingIndexer:
        def __init__(self):
            self.paths = []

        def enqueue(self, path):
            self.paths.append(path)
            return True

    vault = VaultService(vault_root=tmp_path / "vault", max_note_bytes=1_000_000)
    indexer = RecordingIndexer()
    note_content = "súkromný obsah Authorization Bearer secret-api-key"

    with capture_application_logs() as stream:
        created = create_note(
            CreateNoteRequest(
                title="Nočné zálohy",
                folder="Poznámky",
                content=note_content,
                tags=[],
            ),
            vault,
            indexer,
        )
        append_note(
            AppendNoteRequest(path=created.path, content=note_content),
            vault,
            indexer,
        )

    raw = stream.getvalue()
    records = parsed_records(stream)
    assert [record["event"] for record in records] == ["note_created", "note_appended"]
    assert all(record["note_path"] == "Poznámky/Nočné zálohy.md" for record in records)
    assert indexer.paths == [created.path, created.path]
    assert note_content not in raw
    assert "secret-api-key" not in raw


def test_note_write_survives_targeted_queue_failure_and_logs_warning(tmp_path):
    class FailingIndexer:
        def enqueue(self, _path):
            raise RuntimeError("Bearer secret-api-key")

    vault = VaultService(vault_root=tmp_path / "vault", max_note_bytes=1_000_000)
    with capture_application_logs() as stream:
        result = create_note(
            CreateNoteRequest(title="Bezpečná", content="never log this", tags=[]),
            vault,
            FailingIndexer(),
        )

    records = parsed_records(stream)
    assert result.status == "created"
    assert [record["event"] for record in records] == [
        "note_created",
        "targeted_reindex_queue_failed",
    ]
    assert records[1]["level"] == "WARNING"
    assert "secret-api-key" not in stream.getvalue()
    assert "never log this" not in stream.getvalue()


def test_note_write_survives_real_stream_handler_failure(tmp_path):
    class FailingStream:
        def write(self, _value):
            raise OSError("stderr is unavailable")

        def flush(self):
            return None

    class PropagatingStreamHandler(logging.StreamHandler):
        def handleError(self, _record):
            raise RuntimeError("stream handler failed")

    class RecordingIndexer:
        def __init__(self):
            self.paths = []

        def enqueue(self, path):
            self.paths.append(path)
            return True

    vault = VaultService(vault_root=tmp_path / "vault", max_note_bytes=1_000_000)
    indexer = RecordingIndexer()
    handler = PropagatingStreamHandler(FailingStream())
    handler.setFormatter(JsonLogFormatter())
    application_logger = logging.getLogger("vaultbridge")
    application_logger.addHandler(handler)
    try:
        result = create_note(
            CreateNoteRequest(title="Durable", content="committed content", tags=[]),
            vault,
            indexer,
        )
    finally:
        application_logger.removeHandler(handler)
        handler.close()

    assert result.status == "created"
    assert indexer.paths == [result.path]
    assert vault.read_note(result.path).content.endswith("committed content\n")


def test_background_indexer_and_application_lifecycle_events_are_structured():
    indexer = BackgroundSemanticIndexer(
        lambda _cancel_event: {"indexed": 0, "unchanged": 0, "removed": 0},
        lambda paths, _cancel_event: {
            "indexed": len(paths),
            "unchanged": 0,
            "removed": 0,
        },
    )
    application = SimpleNamespace(state=SimpleNamespace(semantic_indexer=indexer))

    async def run_lifespan():
        async with lifespan(application):
            indexer.wait(timeout=2)
            assert indexer.enqueue("Poznámky/Nočné zálohy.md") is True
            indexer.wait(timeout=2)

    with capture_application_logs() as stream:
        asyncio.run(run_lifespan())

    records = parsed_records(stream)
    events = [record["event"] for record in records]
    assert "application_starting" in events
    assert "application_started" in events
    assert "semantic_full_sync_scheduled" in events
    assert "targeted_reindex_queued" in events
    assert "application_stopping" in events
    assert "semantic_indexer_shutdown_requested" in events
    assert "semantic_indexer_stopped" in events
    assert "application_stopped" in events
