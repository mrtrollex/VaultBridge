"""Real runtime reproductions for storage isolation and preparation cleanup."""

import os
import shutil
import sqlite3
import sys
import threading
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from app.core.config import Settings
from app.repositories.semantic import SemanticRepositorySession
from app.repositories.semantic_binding import BINDING_KEYS, IndexBindingUnavailableError
from app.services.knowledge_spaces import QualifiedPath, ReadScope, SpaceId
from app.services.semantic_search import FastEmbedder, IndexState
from app.services.semantic_sync import SyncStepState, SyncWork
from app.services.space_runtime import compose_space_runtime
from tests.test_semantic_resources import launch
from tests.test_space_runtime import backend as backend
from tests.test_space_runtime import binding, configuration


def database_rows(path):
    with closing(sqlite3.connect(path)) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1"))
                     for table in ("meta", "notes", "chunks"))


def redirect(path, target):
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(path))
    else:
        path.symlink_to(target, target_is_directory=True)


def remove_redirect(path):
    if sys.platform == "win32":
        path.rmdir()
    else:
        path.unlink()


def test_default_storage_redirect_cannot_corrupt_peer(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    default, peer = binding(runtime)._owners, binding(runtime, "space-1")._owners
    data, peer_data = default.semantic.db_path.parent, peer.semantic.db_path.parent
    saved = data.with_name("default-data-original")
    original = database_rows(peer.semantic.db_path)
    original_bytes = peer.semantic.db_path.read_bytes()
    data.rename(saved)
    redirect(data, peer_data)
    try:
        (default.vault.vault_root / "same.md").write_text("# DEFAULT_ONLY_SECRET\nDefault content", encoding="utf-8")
        assert default.scheduler.request_full_sync()
        runtime.scheduler.wait(20)
        result = runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("space-1"),)))
        changed = database_rows(peer.semantic.db_path) != original
        leaked = any("DEFAULT_ONLY_SECRET" in item.snippet for item in result.items)
        print(dict(peer_modified=changed, peer_search_leaked=leaked, default_paused=default.scheduler.status().paused))
        assert not changed and not leaked
        assert peer.semantic.db_path.read_bytes() == original_bytes
        assert default.scheduler.status().paused and default.scheduler.status().requires_full_sync
        assert result.items and result.state == "complete"
        assert not peer.scheduler.status().paused
    finally:
        remove_redirect(data)
        saved.rename(data)
        runtime.shutdown()


@pytest.mark.parametrize("persistent", [False, True])
def test_constructor_spool_and_disposal_failure_retains_owned_workspace(tmp_path, backend, monkeypatch, persistent):
    runtime = compose_space_runtime(configuration(tmp_path))
    service = binding(runtime)._owners.semantic
    spool = SyncWork.spool
    works, attempts = [], []
    allow_delete = False

    @contextmanager
    def failing(work):
        if work.repository is service.repository and not works:
            works.append(work)
            remove = work.temporary._rmtree

            def denied(*args, **kwargs):
                assert not work.turn_lock.locked() and not service._sync_lock.locked()
                assert not service._configuration_lock.locked() and not service._sync_admin_lock.locked()
                assert runtime.resource._pins == 0
                attempts.append(args[0])
                if not allow_delete:
                    raise PermissionError("private workspace")
                return remove(*args, **kwargs)

            monkeypatch.setattr(work.temporary, "_rmtree", denied)
            raise OSError("first spool initialization")
        with spool(work) as connection:
            yield connection

    monkeypatch.setattr(SyncWork, "spool", failing)
    runtime.start()
    runtime.scheduler.wait(20)
    work = works[0]
    workspace = Path(work.temporary.name)
    try:
        assert workspace.is_dir() and not work.temporary._finalizer.alive
        print(dict(service_owns_workspace=service._active_sync is work, cleanup_attempts=len(attempts)))
        with pytest.raises(RuntimeError, match="cleanup is incomplete"):
            runtime.shutdown()
        assert runtime._state == "stopping" and service._active_sync is work
        assert binding(runtime, "space-1")._owners.semantic._active_sync is None
        assert workspace.is_dir()
        if persistent:
            for _ in range(2):
                with pytest.raises(RuntimeError, match="cleanup is incomplete"):
                    runtime.shutdown()
                assert workspace.is_dir() and service._active_sync is work and runtime._state == "stopping"
        allow_delete = True
        def forbidden(*args, **kwargs):
            pytest.fail("constructor cleanup retry repeated indexing or SQLite writes")
        with monkeypatch.context() as patch:
            patch.setattr(SyncWork, "spool", forbidden)
            patch.setattr(service.repository, "transaction", forbidden)
            patch.setattr(service.repository, "_connect", forbidden)
            runtime.shutdown()
        assert runtime._state == "stopped" and not workspace.exists()
        assert len(attempts) == (4 if persistent else 2) and set(attempts) == {str(workspace)}
    finally:
        allow_delete = True
        runtime.shutdown()
        work.close()


@pytest.mark.parametrize("failures", [1, 3])
def test_one_lifecycle_retry_disposes_terminal_work_and_completes_sync(tmp_path, backend, monkeypatch, failures):
    runtime = compose_space_runtime(configuration(tmp_path))
    service = binding(runtime)._owners.semantic
    spool = SyncWork.spool
    works, attempts = [], []
    commits = []
    replace_note = SemanticRepositorySession.replace_note

    def record(session, note, chunks):
        if session._repository is service.repository:
            commits.append(note.path)
        return replace_note(session, note, chunks)

    monkeypatch.setattr(SemanticRepositorySession, "replace_note", record)

    @contextmanager
    def inject(work):
        if work.repository is service.repository and not works:
            works.append(work)
            remove = work.temporary._rmtree

            def once(*args, **kwargs):
                attempts.append(args[0])
                if len(attempts) <= failures:
                    raise PermissionError("workspace cleanup once")
                return remove(*args, **kwargs)

            monkeypatch.setattr(work.temporary, "_rmtree", once)
        with spool(work) as connection:
            yield connection

    monkeypatch.setattr(SyncWork, "spool", inject)
    runtime.start()
    runtime.scheduler.wait(20)
    work = works[0]
    workspace = Path(work.temporary.name)
    try:
        assert service._active_sync is work and workspace.is_dir()
        assert work.handle.state is SyncStepState.FAILED
        for count in range(2, failures + 1):
            assert binding(runtime)._owners.scheduler.retry()
            runtime.scheduler.wait(20)
            assert len(attempts) == count and workspace.is_dir() and service._active_sync is work
            status = binding(runtime)._owners.scheduler.status()
            assert status.paused and status.requires_full_sync and status.error_type == "PermissionError"
        peer = binding(runtime, "space-1")._owners
        (peer.vault.vault_root / "queued.md").write_text("peer queued work", encoding="utf-8")
        assert peer.scheduler.enqueue(QualifiedPath(SpaceId("space-1"), "queued.md"))
        assert binding(runtime)._owners.scheduler.retry()
        runtime.scheduler.wait(20)
        status = binding(runtime)._owners.scheduler.status()
        print(dict(paused_after_one_retry=status.paused, error_type=status.error_type,
                   requires_full_sync=status.requires_full_sync, cleanup_attempts=len(attempts)))
        assert not workspace.exists() and service._active_sync is None
        assert not status.paused and not status.requires_full_sync
        assert status.progress.state is SyncStepState.COMPLETE
        assert service.state is IndexState.READY and service.repository.load_chunks()
        assert commits == ["same.md"] and len(attempts) == failures + 1
        assert "queued.md" in {chunk.path for chunk in peer.semantic.repository.load_chunks()}
        assert not binding(runtime, "space-1")._owners.scheduler.status().paused
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("replacement", ["directory", "database", "wal", "shm", "journal"])
def test_default_storage_replacement_keeps_debt_and_peer_intact(tmp_path, backend, replacement):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    default, peer = binding(runtime)._owners, binding(runtime, "space-1")._owners
    database = default.semantic.db_path
    peer_before = database_rows(peer.semantic.db_path)
    if replacement == "directory":
        target = database.parent
        saved = target.with_name("saved-data")
        target.rename(saved)
        shutil.copytree(saved, target)
    elif replacement == "database":
        target, saved = database, database.with_name("saved.sqlite3")
        target.rename(saved)
        shutil.copyfile(peer.semantic.db_path, target)
    else:
        target = Path(str(database) + {"wal": "-wal", "shm": "-shm", "journal": "-journal"}[replacement])
        saved = None
        marker = tmp_path / "external-sidecar-target"
        marker.write_bytes(b"must not be changed by SQLite")
        os.link(marker, target)
    try:
        (default.vault.vault_root / "same.md").write_text("modified default", encoding="utf-8")
        assert default.scheduler.request_full_sync()
        runtime.scheduler.wait(20)
        assert default.scheduler.status().paused and default.scheduler.status().requires_full_sync
        assert default.scheduler.retry()
        runtime.scheduler.wait(20)
        assert default.scheduler.status().paused and default.scheduler.status().requires_full_sync
        assert database_rows(peer.semantic.db_path) == peer_before
        if saved is None:
            assert marker.read_bytes() == b"must not be changed by SQLite"
    finally:
        if replacement == "directory":
            # Both are explicitly owned disposable paths under this test root.
            assert target.parent == tmp_path and saved.parent == tmp_path
            assert target.resolve().is_relative_to(tmp_path.resolve())
            shutil.rmtree(target)
            saved.rename(target)
        else:
            target.unlink()
            if saved is not None:
                saved.rename(target)
        assert default.scheduler.status().requires_full_sync
        assert default.scheduler.retry()
        runtime.scheduler.wait(20)
        assert not default.scheduler.status().paused and not default.scheduler.status().requires_full_sync
        runtime.shutdown()


def test_runtime_default_legacy_index_needs_no_new_metadata(tmp_path, backend):
    settings = configuration(tmp_path)
    with compose_space_runtime(settings) as first:
        first.scheduler.wait(20)
        default = binding(first)._owners.semantic
        original = default.repository.load_chunks()
        with closing(sqlite3.connect(default.db_path)) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM meta"))
        assert not set(BINDING_KEYS) & metadata.keys()
    with compose_space_runtime(settings) as second:
        second.scheduler.wait(20)
        default = binding(second)._owners.semantic
        assert default.repository._expected_binding is None
        assert default.repository.load_chunks() == original
        with closing(sqlite3.connect(default.db_path)) as connection:
            current = dict(connection.execute("SELECT key,value FROM meta"))
        assert not set(BINDING_KEYS) & current.keys()
        assert current["index_signature"] == metadata["index_signature"]
        assert second.operations.scoped_semantic_search("notes").items


@pytest.mark.parametrize("stage", ["allocation", "lazy_inference"])
def test_redirected_model_cache_cannot_reload_backend(tmp_path, backend, stage):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    cache = runtime.resource.cache_dir
    saved = cache.with_name("models-original")
    external = tmp_path / "external-model-cache"
    external.mkdir()
    cache.rename(saved)
    redirect(cache, external)
    try:
        lazy = FastEmbedder("test", cache)
        lazy._cache_guard = runtime.resource._cache_guard
        if stage == "allocation":
            with pytest.raises(IndexBindingUnavailableError):
                lazy._get_model()
            assert lazy._model is None
        else:
            class Model:
                def embed(self, *args, **kwargs):
                    pytest.fail("a lazy model read redirected cache artifacts")
            lazy._model = Model()
            with pytest.raises(IndexBindingUnavailableError):
                lazy.embed(("first inference",))
        assert list(external.iterdir()) == []
        # Already loaded shared state remains usable by the healthy peer.
        assert runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("space-1"),))).items
    finally:
        remove_redirect(cache)
        saved.rename(cache)
        runtime.shutdown()


@pytest.mark.parametrize("action", ["cancel", "shutdown"])
def test_cleanup_retry_obeys_concurrent_cancellation_and_shutdown(tmp_path, backend, monkeypatch, action):
    runtime = compose_space_runtime(configuration(tmp_path))
    service = binding(runtime)._owners.semantic
    handle = binding(runtime)._owners.scheduler
    spool = SyncWork.spool
    works, attempts = [], []
    entered, resume = threading.Event(), threading.Event()

    @contextmanager
    def inject(work):
        if work.repository is service.repository:
            if not works:
                remove = work.temporary._rmtree

                def paused(*args, **kwargs):
                    attempts.append(args[0])
                    if len(attempts) == 1:
                        raise PermissionError("once")
                    assert not work.turn_lock.locked() and not service._sync_lock.locked()
                    assert not service._configuration_lock.locked() and not service._sync_admin_lock.locked()
                    entered.set()
                    assert resume.wait(20)
                    return remove(*args, **kwargs)

                monkeypatch.setattr(work.temporary, "_rmtree", paused)
            if work not in works:
                works.append(work)
        with spool(work) as connection:
            yield connection

    monkeypatch.setattr(SyncWork, "spool", inject)
    runtime.start()
    runtime.scheduler.wait(20)
    old = works[0]
    assert old.handle.state is SyncStepState.FAILED
    assert handle.retry()
    assert entered.wait(10)
    closing = None
    try:
        concurrent = [launch(handle.retry) for _ in range(3)]
        assert all(not caller.result(10) for caller in concurrent)
        peer = binding(runtime, "space-1")._owners
        (peer.vault.vault_root / "queued.md").write_text("peer continues", encoding="utf-8")
        assert peer.scheduler.enqueue(QualifiedPath(SpaceId("space-1"), "queued.md"))
        if action == "cancel":
            handle.cancel()
        else:
            closing = launch(runtime.shutdown)
            with runtime.scheduler._condition:
                assert runtime.scheduler._condition.wait_for(lambda: runtime.scheduler._closed, 10)
            assert runtime.admission._stopped.is_set() and not closing.done()
        resume.set()
        if closing is not None:
            closing.result(20)
            assert runtime._state == "stopped" and not handle.retry()
        else:
            runtime.scheduler.wait(20)
            assert handle.status().paused and handle.status().requires_full_sync
            assert "queued.md" in {row.path for row in peer.semantic.repository.load_chunks()}
        assert len(works) == 1 and len(attempts) == 2
        assert service._active_sync is None and not Path(old.temporary.name).exists()
    finally:
        resume.set()
        runtime.shutdown()


def test_default_redirect_before_first_index_initialization_is_rejected(tmp_path, backend):
    runtime = compose_space_runtime(configuration(tmp_path))
    default, peer = binding(runtime)._owners, binding(runtime, "space-1")._owners
    runtime.resource.resolve_embedding_fingerprint()  # Warm shared model; no index exists yet.
    data = default.semantic.db_path.parent
    saved = data.with_name("saved-default-data")
    peer.semantic.db_path.parent.mkdir()
    data.rename(saved)
    redirect(data, peer.semantic.db_path.parent)
    try:
        runtime.start()
        runtime.scheduler.wait(20)
        assert default.scheduler.status().paused and default.scheduler.status().requires_full_sync
        assert not peer.scheduler.status().paused
        chunks = peer.semantic.repository.load_chunks()
        assert chunks and all("space-1" in row.content for row in chunks)
        assert not any("Independent default" in text for call in backend.calls for text in call)
    finally:
        remove_redirect(data)
        saved.rename(data)
        runtime.shutdown()


def test_default_storage_redirect_during_resumed_sync_keeps_peer_unchanged(tmp_path, backend, monkeypatch):
    runtime = compose_space_runtime(configuration(tmp_path)).start()
    runtime.scheduler.wait(20)
    default, peer = binding(runtime)._owners, binding(runtime, "space-1")._owners
    service = default.semantic
    step = service.sync_step
    entered, resume = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        result = step(*args, **kwargs)
        if result.indexed and not entered.is_set():
            entered.set()
            assert resume.wait(20)
        return result

    monkeypatch.setattr(service, "sync_step", paused)
    original = database_rows(peer.semantic.db_path)
    (default.vault.vault_root / "same.md").write_text("# DEFAULT_RESUMED_SECRET\nchanged", encoding="utf-8")
    assert default.scheduler.request_full_sync()
    assert entered.wait(10)
    data = service.db_path.parent
    saved = data.with_name("saved-data")
    data.rename(saved)
    redirect(data, peer.semantic.db_path.parent)
    try:
        resume.set()
        runtime.scheduler.wait(20)
        assert default.scheduler.status().paused and default.scheduler.status().requires_full_sync
        assert database_rows(peer.semantic.db_path) == original
        result = runtime.operations.scoped_semantic_search("notes", ReadScope((SpaceId("space-1"),)))
        assert result.items and all("DEFAULT_RESUMED_SECRET" not in row.snippet for row in result.items)
    finally:
        resume.set()
        remove_redirect(data)
        saved.rename(data)
        runtime.shutdown()


def test_implicit_default_preserves_relative_storage_setting(tmp_path, backend, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("legacy relative data", encoding="utf-8")
    settings = Settings(vault_path=vault, semantic_data_path=Path("relative-data"), semantic_watch_enabled=False)
    with compose_space_runtime(settings) as runtime:
        runtime.scheduler.wait(20)
        owners = binding(runtime)._owners
        assert owners.semantic.db_path == Path("relative-data/semantic-index.sqlite3")
        assert not owners.scheduler.status().paused
        assert runtime.operations.scoped_semantic_search("legacy").items
