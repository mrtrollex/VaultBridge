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
    run_index,
    run_index_check,
    run_index_rebuild,
    run_reindex,
    run_related,
    run_search,
    run_status,
)
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import (
    FastEmbedder,
    IndexState,
    SemanticIndexOperationError,
    SemanticResult,
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


def semantic_result(
    path: str,
    *,
    score: float,
    snippet: str = "matching excerpt",
    heading: str | None = None,
) -> SemanticResult:
    return SemanticResult(
        path=path,
        title=Path(path).stem,
        score=score,
        semantic_score=score,
        lexical_score=0.0,
        snippet=snippet,
        heading=heading,
    )


def test_top_level_and_command_help_are_available(capsys):
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])
    top_help = capsys.readouterr().out
    for command in ("status", "index", "reindex", "search", "related"):
        assert command in top_help

    for arguments in (
        ["status", "--help"],
        ["index", "--help"],
        ["reindex", "--help"],
        ["search", "--help"],
        ["related", "--help"],
        ["index", "check", "--help"],
        ["index", "rebuild", "--help"],
    ):
        with pytest.raises(SystemExit, match="0"):
            main(arguments)
        assert "usage:" in capsys.readouterr().out


def test_status_is_the_existing_read_only_index_check(tmp_path):
    settings = settings_for(tmp_path)
    (settings.vault_path / "Ready.md").write_text("Ready content.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    before = storage_snapshot(settings.semantic_data_path)
    output = io.StringIO()

    assert run_status(settings, output=output, service=service) == EXIT_SUCCESS
    assert "Status:                   healthy" in output.getvalue()
    assert storage_snapshot(settings.semantic_data_path) == before


def test_status_reports_unhealthy_vault_with_operational_exit_without_creating_storage(tmp_path):
    settings = settings_for(tmp_path, vault_path=tmp_path / "missing")
    output = io.StringIO()

    assert run_status(settings, output=output) == EXIT_INTEGRITY_PROBLEM
    assert "Vault:                    missing" in output.getvalue()
    assert not settings.semantic_data_path.exists()


def test_index_runs_incremental_sync_without_resetting_compatible_data(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    first = settings.vault_path / "First.md"
    first.write_text("First source note.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    second = settings.vault_path / "Second.md"
    second.write_text("Second source note.", encoding="utf-8")
    source_before = {path: path.read_bytes() for path in (first, second)}

    def forbidden_reset():
        raise AssertionError("incremental index must not reset compatible derived data")

    monkeypatch.setattr(service.repository, "reset_index", forbidden_reset)
    output = io.StringIO()

    assert run_index(settings, output=output, service=service) == EXIT_SUCCESS
    assert {chunk.path for chunk in service.repository.load_chunks()} == {"First.md", "Second.md"}
    assert {path: path.read_bytes() for path in (first, second)} == source_before
    assert "Status: ready" in output.getvalue()


def test_reindex_alias_uses_clean_rebuild_and_preserves_markdown(tmp_path):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Source.md"
    note.write_text("Authoritative Markdown.", encoding="utf-8")
    service = service_for(settings)
    service.sync()
    note_before = note.read_bytes()

    assert run_reindex(settings, output=io.StringIO(), service=service) == EXIT_SUCCESS
    assert note.read_bytes() == note_before
    assert service.repository.get_metadata("index_state") == IndexState.READY.value


def test_literal_search_is_deterministic_folder_scoped_limited_and_embedder_free(
    tmp_path,
    monkeypatch,
):
    settings = settings_for(tmp_path)
    folder = settings.vault_path / "Area"
    folder.mkdir()
    (folder / "B.md").write_text("Needle in B.", encoding="utf-8")
    (folder / "A.md").write_text("Needle in A.", encoding="utf-8")
    (settings.vault_path / "Outside.md").write_text("Needle outside.", encoding="utf-8")

    def forbidden_semantic_service(*_args, **_kwargs):
        raise AssertionError("literal search must not construct a semantic service")

    monkeypatch.setattr(
        "app.cli.semantic_search_service_from_settings",
        forbidden_semantic_service,
    )
    output = io.StringIO()

    assert run_search(settings, "needle", folder="Area", limit=1, output=output) == EXIT_SUCCESS
    rendered = output.getvalue()
    assert "Area/A.md" in rendered
    assert "Area/B.md" not in rendered
    assert "Outside.md" not in rendered


def test_literal_search_displays_both_title_only_and_content_matches(tmp_path):
    settings = settings_for(tmp_path)
    (settings.vault_path / "Needle Title.md").write_text("ordinary body", encoding="utf-8")
    (settings.vault_path / "Content.md").write_text("needle in the body", encoding="utf-8")
    output = io.StringIO()

    assert run_search(settings, "needle", output=output) == EXIT_SUCCESS
    rendered = output.getvalue()
    assert "Needle Title.md" in rendered
    assert "Content.md" in rendered


def test_literal_search_no_matches_is_success_and_unsafe_folders_fail(tmp_path):
    settings = settings_for(tmp_path)
    (settings.vault_path / "Note.md").write_text("ordinary content", encoding="utf-8")
    output = io.StringIO()

    assert run_search(settings, "absent", output=output) == EXIT_SUCCESS
    assert output.getvalue() == "No literal matches.\n"

    for folder in ("../outside", str(tmp_path)):
        error = io.StringIO()
        assert run_search(settings, "ordinary", folder=folder, error_output=error) == 1
        assert "invalid or unavailable" in error.getvalue()


def test_literal_search_rejects_external_directory_symlink_folder(tmp_path):
    settings = settings_for(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Private.md").write_text("needle outside", encoding="utf-8")
    link = settings.vault_path / "External"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    error = io.StringIO()

    assert run_search(settings, "needle", folder="External", error_output=error) == 1
    assert "invalid or unavailable" in error.getvalue()
    assert str(outside) not in error.getvalue()


def test_literal_search_redacts_configured_secrets_and_local_roots(tmp_path):
    settings = Settings.model_validate(
        {
            **settings_for(tmp_path).model_dump(),
            "api_key": "active-secret",
            "previous_api_key": "previous-secret",
        }
    )
    note = settings.vault_path / "Sensitive.md"
    note.write_text(
        f"needle active-secret previous-secret {settings.vault_path} {settings.semantic_data_path}",
        encoding="utf-8",
    )
    output = io.StringIO()

    assert run_search(settings, "needle", output=output) == EXIT_SUCCESS
    rendered = output.getvalue()
    for private_value in (
        "active-secret",
        "previous-secret",
        str(settings.vault_path),
        str(settings.semantic_data_path),
    ):
        assert private_value not in rendered
    assert "[redacted]" in rendered


class FakeRelatedService:
    def __init__(self, results: list[SemanticResult], *, available: bool = True) -> None:
        self.results = results
        self.available = available
        self.search_calls: list[tuple[str, str, int, float]] = []

    def use_persisted_index_for_read_only_search(self) -> bool:
        return self.available

    def search(self, text: str, *, folder: str, limit: int, min_score: float):
        self.search_calls.append((text, folder, limit, min_score))
        return self.results


def test_related_preserves_rank_applies_options_and_filters_non_live_candidates(tmp_path):
    settings = settings_for(tmp_path)
    folder = settings.vault_path / "Area"
    folder.mkdir()
    (folder / "Second.md").write_text("live second", encoding="utf-8")
    (folder / "First.md").write_text("live first", encoding="utf-8")
    (settings.vault_path / "Outside.md").write_text("outside", encoding="utf-8")
    fake = FakeRelatedService(
        [
            semantic_result("Area/Second.md", score=0.91, heading="Heading two"),
            semantic_result("Missing.md", score=0.90),
            semantic_result("../escape.md", score=0.89),
            semantic_result("Outside.md", score=0.88),
            semantic_result("Area/First.md", score=0.87),
        ]
    )
    output = io.StringIO()

    assert (
        run_related(
            settings,
            "concept",
            folder="Area",
            limit=2,
            min_score=0.42,
            output=output,
            service=fake,
        )
        == EXIT_SUCCESS
    )
    rendered = output.getvalue()
    assert rendered.index("Area/Second.md") < rendered.index("Area/First.md")
    assert "Missing.md" not in rendered
    assert "escape.md" not in rendered
    assert "Outside.md" not in rendered
    assert "Heading two" in rendered
    assert "score: 0.910" in rendered
    assert fake.search_calls == [("concept", "Area", 6, 0.42)]


def test_related_unavailable_index_is_an_expected_operational_failure(tmp_path):
    settings = settings_for(tmp_path)
    error = io.StringIO()

    assert (
        run_related(
            settings,
            "concept",
            error_output=error,
            service=FakeRelatedService([], available=False),
        )
        == EXIT_INTEGRITY_PROBLEM
    )
    assert "no compatible searchable semantic index" in error.getvalue()


def test_related_redacts_secrets_and_absolute_roots(tmp_path):
    settings = Settings.model_validate(
        {
            **settings_for(tmp_path).model_dump(),
            "api_key": "active-secret",
            "previous_api_key": "previous-secret",
        }
    )
    (settings.vault_path / "Live.md").write_text("live", encoding="utf-8")
    fake = FakeRelatedService(
        [
            semantic_result(
                "Live.md",
                score=0.75,
                snippet=(
                    "active-secret previous-secret "
                    f"{settings.vault_path} {settings.semantic_data_path}"
                ),
            )
        ]
    )
    output = io.StringIO()

    assert run_related(settings, "concept", output=output, service=fake) == EXIT_SUCCESS
    rendered = output.getvalue()
    for private_value in (
        "active-secret",
        "previous-secret",
        str(settings.vault_path),
        str(settings.semantic_data_path),
    ):
        assert private_value not in rendered


def test_related_uses_persisted_index_without_sync_or_storage_mutation(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    note = settings.vault_path / "Ready.md"
    note.write_text("TrueNAS backup procedure.", encoding="utf-8")
    service_for(settings).sync()
    restarted = service_for(settings)
    source_before = note.read_bytes()
    storage_before = storage_snapshot(settings.semantic_data_path)

    def forbidden_sync(*_args, **_kwargs):
        raise AssertionError("related must not synchronize implicitly")

    monkeypatch.setattr(restarted, "sync", forbidden_sync)
    output = io.StringIO()

    assert (
        run_related(
            settings,
            "TrueNAS backup",
            min_score=-1.0,
            output=output,
            service=restarted,
        )
        == EXIT_SUCCESS
    )
    assert "Ready.md" in output.getvalue()
    assert note.read_bytes() == source_before
    assert storage_snapshot(settings.semantic_data_path) == storage_before


@pytest.mark.parametrize(
    "arguments",
    (
        ["search", "query", "--limit", "0"],
        ["search", "query", "--limit", "51"],
        ["related", "query", "--limit", "21"],
        ["related", "query", "--min-score", "1.1"],
    ),
)
def test_cli_rejects_invalid_search_arguments_with_exit_two(arguments):
    with pytest.raises(SystemExit, match=str(EXIT_CLI_FAILURE)):
        main(arguments)
