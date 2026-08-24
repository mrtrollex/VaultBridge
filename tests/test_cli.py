from __future__ import annotations

import builtins
import hashlib
import io
import sqlite3
import stat
from pathlib import Path

import numpy as np
import pytest

from app.cli import (
    EXIT_CLI_FAILURE,
    EXIT_INTEGRITY_PROBLEM,
    EXIT_SUCCESS,
    main,
    run_index_check,
    run_index_rebuild,
)
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import (
    FastEmbedder,
    IndexState,
    SemanticIndexOperationError,
    SemanticSearchService,
)


class FakeEmbedder:
    def embed(self, texts):
        return [
            np.array(
                [
                    1.0,
                    float("truenas" in text.casefold()),
                    float("slov" in text.casefold()),
                ],
                dtype=np.float32,
            )
            for text in texts
        ]


class RecordingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return super().embed(texts)


class FailingEmbedder:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    def embed(self, texts):
        del texts
        raise self.failure


class FailingFinalizationConnection:
    def __init__(self, connection: sqlite3.Connection, failure_point: str) -> None:
        self.connection = connection
        self.failure_point = failure_point

    def execute(self, sql, parameters=()):
        if parameters:
            key = parameters[0]
            if self.failure_point == "timestamp" and key == "last_successful_sync":
                raise sqlite3.OperationalError("timestamp write fixture failure")
            if self.failure_point == "ready" and key == "index_state":
                raise sqlite3.OperationalError("ready write fixture failure")
        return self.connection.execute(sql, parameters)

    def commit(self):
        if self.failure_point == "commit":
            raise sqlite3.OperationalError("commit fixture failure")
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()

    def close(self):
        return self.connection.close()


def settings_for(tmp_path: Path, *, vault_path: Path | None = None, model: str = "example/model"):
    vault = vault_path or tmp_path / "vault"
    if vault_path is None:
        vault.mkdir(exist_ok=True)
    return Settings(
        vault_path=vault,
        semantic_data_path=tmp_path / "data",
        semantic_model=model,
        semantic_chunk_chars=300,
        semantic_chunk_overlap=50,
        semantic_index_batch_size=1,
    )


def service_for(settings: Settings, *, embedder=None) -> SemanticSearchService:
    return SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(settings.semantic_data_path / "semantic-index.sqlite3"),
        model_name=settings.semantic_model,
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=settings.semantic_chunk_chars,
        chunk_overlap=settings.semantic_chunk_overlap,
        index_batch_size=settings.semantic_index_batch_size,
        embedder=embedder or FakeEmbedder(),
    )


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def storage_snapshot(storage_root: Path) -> dict[str, tuple[int, int, str]]:
    if not storage_root.exists():
        return {}
    return {
        path.relative_to(storage_root).as_posix(): (
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_size,
            file_digest(path),
        )
        for path in sorted(storage_root.rglob("*"))
        if path.is_file()
    }


def test_index_check_reports_healthy_ready_index_without_mutating_or_loading_fastembed(
    tmp_path,
    monkeypatch,
):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Ready.md"
    note.write_text("# Storage\n\nTrueNAS backup.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    before = storage_snapshot(settings.semantic_data_path)

    def unexpected_fastembed(*_args, **_kwargs):
        raise AssertionError("index check constructed FastEmbed")

    monkeypatch.setattr(FastEmbedder, "__init__", unexpected_fastembed)
    output = io.StringIO()

    exit_code = run_index_check(settings, output=output)

    assert exit_code == EXIT_SUCCESS
    assert "Signature:                compatible" in output.getvalue()
    assert "Persisted state:          ready" in output.getvalue()
    assert "Standalone searchable:    yes" in output.getvalue()
    assert "Live process availability: use /health and /health/ready" in output.getvalue()
    assert "Status:                   healthy" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_accepts_compatible_legacy_index_without_persisted_state(tmp_path):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Legacy.md"
    note.write_text("Legacy compatible content.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    connection = sqlite3.connect(service.db_path)
    try:
        connection.execute("DELETE FROM meta WHERE key='index_state'")
        connection.commit()
    finally:
        connection.close()
    before = storage_snapshot(settings.semantic_data_path)

    output = io.StringIO()
    exit_code = run_index_check(settings, output=output)

    assert exit_code == EXIT_SUCCESS
    assert "not recorded (compatible legacy index)" in output.getvalue()
    assert "Standalone searchable:    yes" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_distinguishes_missing_database(tmp_path):
    settings = settings_for(tmp_path)
    output = io.StringIO()
    before = storage_snapshot(settings.semantic_data_path)

    exit_code = run_index_check(settings, output=output)

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert "Database:                 missing" in output.getvalue()
    assert "Schema:                   not present" in output.getvalue()
    assert "Status:                   database missing" in output.getvalue()
    assert not (settings.semantic_data_path / "semantic-index.sqlite3").exists()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_distinguishes_corrupt_sqlite(tmp_path):
    settings = settings_for(tmp_path)
    settings.semantic_data_path.mkdir()
    (settings.semantic_data_path / "semantic-index.sqlite3").write_bytes(b"not sqlite")
    output = io.StringIO()
    before = storage_snapshot(settings.semantic_data_path)

    exit_code = run_index_check(settings, output=output)

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert "Database:                 unreadable" in output.getvalue()
    assert "Status:                   database unreadable or corrupt" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_distinguishes_unusable_schema(tmp_path):
    settings = settings_for(tmp_path)
    settings.semantic_data_path.mkdir()
    connection = sqlite3.connect(settings.semantic_data_path / "semantic-index.sqlite3")
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.close()
    output = io.StringIO()
    before = storage_snapshot(settings.semantic_data_path)

    exit_code = run_index_check(settings, output=output)

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert "Database:                 OK" in output.getvalue()
    assert "Schema:                   unusable" in output.getvalue()
    assert "Status:                   schema unusable" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_distinguishes_incompatible_signature(tmp_path):
    old_settings = settings_for(tmp_path, model="old/model")
    (old_settings.vault_path / "Old.md").write_text("Old derived content.", encoding="utf-8")
    service_for(old_settings).sync()
    current_settings = settings_for(tmp_path, model="current/model")
    output = io.StringIO()
    before = storage_snapshot(current_settings.semantic_data_path)

    exit_code = run_index_check(current_settings, output=output)

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert "Signature:                incompatible" in output.getvalue()
    assert "Status:                   index signature incompatible" in output.getvalue()
    assert "Stored notes:             1" in output.getvalue()
    assert "Stored chunks:            1" in output.getvalue()
    assert "Stored last full sync:    never" not in output.getvalue()
    assert storage_snapshot(current_settings.semantic_data_path) == before


def test_index_check_distinguishes_persisted_error_and_incomplete_indexing(tmp_path):
    settings = settings_for(tmp_path)
    service = service_for(settings)
    service.repository.prepare_index(service.index_signature)

    for persisted_state, expected in (
        (IndexState.ERROR.value, "persisted index error"),
        (IndexState.INDEXING.value, "indexing incomplete"),
    ):
        service.repository.set_metadata("index_state", persisted_state)
        output = io.StringIO()
        before = storage_snapshot(settings.semantic_data_path)

        assert run_index_check(settings, output=output) == EXIT_INTEGRITY_PROBLEM
        assert f"Persisted state:          {persisted_state}" in output.getvalue()
        assert f"Status:                   {expected}" in output.getvalue()
        assert storage_snapshot(settings.semantic_data_path) == before


def test_index_check_distinguishes_missing_vault_and_regular_file(tmp_path):
    missing = tmp_path / "missing-vault"
    regular_file = tmp_path / "vault-file"
    regular_file.write_text("not a vault", encoding="utf-8")

    for vault_path, expected in ((missing, "missing"), (regular_file, "not a directory")):
        output = io.StringIO()
        exit_code = run_index_check(settings_for(tmp_path, vault_path=vault_path), output=output)

        assert exit_code == EXIT_INTEGRITY_PROBLEM
        assert f"Vault:                    {expected}" in output.getvalue()
        assert "Status:                   vault unavailable" in output.getvalue()


def test_index_check_handles_expected_storage_oserror(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    service = service_for(settings)
    service.repository.prepare_index(service.index_signature)
    original_connect = sqlite3.connect

    def unavailable_connection(*args, **kwargs):
        if kwargs.get("uri"):
            raise OSError("private storage detail")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("app.repositories.semantic.sqlite3.connect", unavailable_connection)
    output = io.StringIO()

    assert run_index_check(settings, output=output) == EXIT_INTEGRITY_PROBLEM
    assert "Database:                 unreadable" in output.getvalue()
    assert "private storage detail" not in output.getvalue()


def test_index_check_refuses_existing_sqlite_sidecars_without_changing_storage(tmp_path):
    settings = settings_for(tmp_path)
    service = service_for(settings)
    (settings.vault_path / "Ready.md").write_text("Ready content.", encoding="utf-8")
    service.sync()
    Path(f"{service.db_path}-wal").write_bytes(b"existing wal fixture")
    Path(f"{service.db_path}-shm").write_bytes(b"existing shm fixture")
    before = storage_snapshot(settings.semantic_data_path)
    output = io.StringIO()

    assert run_index_check(settings, output=output) == EXIT_INTEGRITY_PROBLEM
    assert "Persisted inspection:     unavailable" in output.getvalue()
    assert "stop VaultBridge before running index check" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_cli_returns_configuration_and_programming_failure_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setenv("SEMANTIC_CHUNK_CHARS", "invalid")
    assert main(["index", "check"]) == EXIT_CLI_FAILURE
    assert "configuration is invalid" in capsys.readouterr().err

    settings = settings_for(tmp_path)

    def programming_error(*_args, **_kwargs):
        raise sqlite3.ProgrammingError("private query detail")

    monkeypatch.setattr(
        "app.repositories.semantic.SemanticRepository.read_immutable_status",
        programming_error,
    )
    assert main(["index", "check"], settings=settings) == EXIT_CLI_FAILURE
    error = capsys.readouterr().err
    assert "ProgrammingError" in error
    assert "private query detail" not in error


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (sqlite3.OperationalError("expected storage failure"), EXIT_INTEGRITY_PROBLEM),
        (RuntimeError("programmer runtime defect"), EXIT_CLI_FAILURE),
        (ValueError("programmer value defect"), EXIT_CLI_FAILURE),
        (sqlite3.ProgrammingError("programmer SQL defect"), EXIT_CLI_FAILURE),
    ),
)
def test_cli_classifies_expected_and_unexpected_rebuild_failures(
    tmp_path,
    monkeypatch,
    capsys,
    failure,
    expected_code,
):
    settings = settings_for(tmp_path)

    class BrokenService:
        def rebuild(self):
            raise failure

    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setattr(
        "app.cli.semantic_search_service_from_settings",
        lambda *_args, **_kwargs: BrokenService(),
    )

    assert main(["index", "rebuild"], settings=settings) == expected_code
    captured = capsys.readouterr()
    assert str(failure) not in captured.err
    assert "Traceback" not in captured.err


def test_cli_bootstrap_failure_returns_code_two_without_private_details(
    monkeypatch,
    capsys,
):
    def bootstrap_failure():
        raise RuntimeError("private logging bootstrap detail")

    monkeypatch.setattr("app.cli.configure_application_logging", bootstrap_failure)

    assert main(["index", "check"]) == EXIT_CLI_FAILURE
    error = capsys.readouterr().err
    assert "RuntimeError" in error
    assert "private logging bootstrap detail" not in error
    assert "Traceback" not in error


def test_cli_settings_loader_runtime_failure_returns_code_two_without_private_details(
    monkeypatch,
    capsys,
):
    def settings_failure():
        raise RuntimeError("private settings loader detail")

    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setattr("app.cli.Settings.from_env", settings_failure)

    assert main(["index", "check"]) == EXIT_CLI_FAILURE
    error = capsys.readouterr().err
    assert "RuntimeError" in error
    assert "private settings loader detail" not in error
    assert "Traceback" not in error


def test_index_rebuild_replaces_stale_data_and_uses_current_chunk_and_embedding_pipeline(tmp_path):
    settings = settings_for(tmp_path)
    stale = settings.vault_path / "Stale.md"
    stale.write_text("Old note that will be removed.", encoding="utf-8")
    recorder = RecordingEmbedder()
    service = service_for(settings, embedder=recorder)
    service.sync()
    stale.unlink()
    unicode_note = settings.vault_path / "Slovenská poznámka.md"
    markdown = "# Úložisko\n\nZáloha dát na TrueNAS serveri."
    unicode_note.write_text(markdown, encoding="utf-8")
    recorder.calls.clear()
    output = io.StringIO()

    exit_code = run_index_rebuild(settings, output=output, service=service)

    assert exit_code == EXIT_SUCCESS
    status = service.inspect_index()
    assert status.state is IndexState.READY
    assert status.search_available is True
    assert status.last_successful_sync is not None
    with service.repository.transaction() as session:
        assert set(session.load_notes()) == {"Slovenská poznámka.md"}
    chunks = service.repository.load_chunks()
    assert {chunk.path for chunk in chunks} == {"Slovenská poznámka.md"}
    assert any(
        chunk.heading == "Úložisko"
        and chunk.content.replace("\r\n", "\n") == markdown
        for chunk in chunks
    )
    assert any(
        "Slovenská poznámka\n# Úložisko" in embedded.replace("\r\n", "\n")
        for call in recorder.calls
        for embedded in call
    )
    assert "Status: ready" in output.getvalue()


def test_index_rebuild_replaces_incompatible_signature_with_current_signature(tmp_path):
    old_settings = settings_for(tmp_path, model="old/model")
    note = old_settings.vault_path / "Current.md"
    note.write_text("Current Markdown source.", encoding="utf-8")
    old_service = service_for(old_settings)
    old_service.sync()
    current_settings = settings_for(tmp_path, model="current/model")
    current_service = service_for(current_settings)

    assert run_index_rebuild(current_settings, output=io.StringIO(), service=current_service) == 0
    assert current_service.repository.get_metadata("index_signature") == current_service.index_signature
    assert current_service.repository.get_metadata("index_state") == IndexState.READY.value


def test_index_rebuild_recovers_sqlite_reported_as_corrupt(tmp_path):
    settings = settings_for(tmp_path)
    (settings.vault_path / "Recovered.md").write_text(
        "Rebuild from authoritative Markdown.",
        encoding="utf-8",
    )
    settings.semantic_data_path.mkdir()
    database = settings.semantic_data_path / "semantic-index.sqlite3"
    database.write_bytes(b"not a SQLite database")
    service = service_for(settings)

    exit_code = run_index_rebuild(settings, output=io.StringIO(), service=service)

    assert exit_code == EXIT_SUCCESS
    assert service.repository.get_metadata("index_state") == IndexState.READY.value
    assert {chunk.path for chunk in service.repository.load_chunks()} == {"Recovered.md"}


@pytest.mark.parametrize(
    "failure",
    (
        RuntimeError("private runtime embedding defect"),
        ValueError("private value embedding defect"),
    ),
)
def test_generic_embedder_failure_uses_real_service_path_and_returns_code_two(
    tmp_path,
    monkeypatch,
    capsys,
    failure,
):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Source.md"
    note.write_text("Authoritative Markdown stays intact.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    before = note.read_bytes()
    previous_sync = service.repository.get_metadata("last_successful_sync")
    assert previous_sync is not None
    service.embedder = FailingEmbedder(failure)
    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setattr(
        "app.cli.semantic_search_service_from_settings",
        lambda *_args, **_kwargs: service,
    )

    exit_code = main(["index", "rebuild"], settings=settings)
    captured = capsys.readouterr()

    assert exit_code == EXIT_CLI_FAILURE
    assert note.read_bytes() == before
    assert service.repository.get_metadata("index_state") == IndexState.ERROR.value
    assert service.repository.get_metadata("last_successful_sync") == previous_sync
    assert type(failure).__name__ in captured.err
    assert str(failure) not in captured.err
    assert "Traceback" not in captured.err


def test_explicit_semantic_operation_failure_uses_real_service_path_and_returns_code_one(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Source.md"
    note.write_text("Authoritative Markdown stays intact.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    before = note.read_bytes()
    previous_sync = service.repository.get_metadata("last_successful_sync")
    failure = SemanticIndexOperationError("private operational embedding detail")
    service.embedder = FailingEmbedder(failure)
    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setattr(
        "app.cli.semantic_search_service_from_settings",
        lambda *_args, **_kwargs: service,
    )

    exit_code = main(["index", "rebuild"], settings=settings)
    captured = capsys.readouterr()

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert note.read_bytes() == before
    assert service.repository.get_metadata("index_state") == IndexState.ERROR.value
    assert service.repository.get_metadata("last_successful_sync") == previous_sync
    assert "SemanticIndexOperationError" in captured.err
    assert str(failure) not in captured.err
    assert "Traceback" not in captured.err


def test_missing_fastembed_dependency_remains_an_operational_failure(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Source.md"
    note.write_text("Authoritative Markdown stays intact.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    previous_sync = service.repository.get_metadata("last_successful_sync")
    service.embedder = FastEmbedder(settings.semantic_model, tmp_path / "model-cache")
    original_import = builtins.__import__

    def missing_fastembed(name, *args, **kwargs):
        if name == "fastembed":
            raise ImportError("private dependency import detail")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_fastembed)
    monkeypatch.setattr("app.cli.configure_application_logging", lambda: None)
    monkeypatch.setattr(
        "app.cli.semantic_search_service_from_settings",
        lambda *_args, **_kwargs: service,
    )

    exit_code = main(["index", "rebuild"], settings=settings)
    captured = capsys.readouterr()

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert service.repository.get_metadata("index_state") == IndexState.ERROR.value
    assert service.repository.get_metadata("last_successful_sync") == previous_sync
    assert "SemanticIndexOperationError" in captured.err
    assert "private dependency import detail" not in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("failure_point", ("timestamp", "ready", "commit"))
def test_failed_full_sync_finalization_rolls_back_timestamp_and_ready_together(
    tmp_path,
    monkeypatch,
    failure_point,
):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Source.md"
    note.write_text("Authoritative Markdown stays intact.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    previous_sync = service.repository.get_metadata("last_successful_sync")
    source_before = note.read_bytes()
    original_finalize = service.repository.finalize_full_sync_success

    def failing_finalize(**kwargs):
        original_connect = service.repository._connect

        def failing_connect():
            return FailingFinalizationConnection(original_connect(), failure_point)

        service.repository._connect = failing_connect
        try:
            return original_finalize(**kwargs)
        finally:
            service.repository._connect = original_connect

    monkeypatch.setattr(
        service.repository,
        "finalize_full_sync_success",
        failing_finalize,
    )

    assert run_index_rebuild(
        settings,
        output=io.StringIO(),
        error_output=io.StringIO(),
        service=service,
    ) == EXIT_INTEGRITY_PROBLEM
    assert note.read_bytes() == source_before
    assert service.repository.get_metadata("last_successful_sync") == previous_sync
    assert service.repository.get_metadata("index_state") == IndexState.ERROR.value


def test_failed_rebuild_retains_completed_batches_but_not_stale_old_chunks(tmp_path):
    settings = settings_for(tmp_path)
    first = settings.vault_path / "A.md"
    second = settings.vault_path / "B.md"
    first.write_text("First durable batch.", encoding="utf-8")
    second.write_text("Second failing batch.", encoding="utf-8")

    class FailSecondEmbedder(FakeEmbedder):
        def embed(self, texts):
            if any("Second failing batch" in text for text in texts):
                raise SemanticIndexOperationError("second batch failed")
            return super().embed(texts)

    service = service_for(settings, embedder=FailSecondEmbedder())

    assert run_index_rebuild(
        settings,
        output=io.StringIO(),
        error_output=io.StringIO(),
        service=service,
    ) == EXIT_INTEGRITY_PROBLEM
    with service.repository.transaction() as session:
        assert set(session.load_notes()) == {"A.md"}
    assert {chunk.path for chunk in service.repository.load_chunks()} == {"A.md"}


def test_index_rebuild_empty_vault_produces_ready_empty_index(tmp_path):
    settings = settings_for(tmp_path)
    service = service_for(settings)

    exit_code = run_index_rebuild(settings, output=io.StringIO(), service=service)

    assert exit_code == EXIT_SUCCESS
    status = service.inspect_index()
    assert status.state is IndexState.READY
    assert status.search_available is True
    assert status.indexed_notes == 0
    assert status.semantic_chunks == 0
    assert status.last_successful_sync is not None


def test_index_rebuild_rejects_unavailable_vault_before_creating_storage(tmp_path):
    settings = settings_for(tmp_path, vault_path=tmp_path / "missing")
    error_output = io.StringIO()

    exit_code = run_index_rebuild(settings, error_output=error_output)

    assert exit_code == EXIT_INTEGRITY_PROBLEM
    assert "Rebuild not started" in error_output.getvalue()
    assert not settings.semantic_data_path.exists()
