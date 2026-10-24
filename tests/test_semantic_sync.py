"""Deterministic E3 continuation, durable-boundary and cancellation regressions."""

import os
import threading
from concurrent.futures import TimeoutError
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.repositories.semantic import SemanticRepositorySession
from app.repositories.semantic_binding import IndexBindingUnavailableError
from app.services.semantic_search import (
    IndexState,
    SemanticSearchUnavailableError,
    SynchronizationCancelledError,
    TargetedSynchronizationError,
)
from app.services.semantic_sync import SyncContinuation, SyncStepState
from app.services.vault import VaultService
from tests.test_semantic_resources import launch, owner
from tests.test_semantic_resources import shared as shared
from tests.test_semantic_search_service import FakeEmbedder, TrackingRepository, semantic_service


def notes(service, count):
    for index in range(count):
        (service.vault_root / f"note-{index:04}.md").write_text(
            f"TrueNAS backup {index}.", encoding="utf-8",
        )


def drain(service, handle, *, budget=3):
    results = []
    for _ in range(5000):
        result = service.sync_step(handle, discovery_budget=budget)
        results.append(result)
        assert not service._sync_lock.locked()
        assert not service._configuration_lock.locked()
        assert not service.repository._sessions
        assert not service._sync_operations
        if result.state is SyncStepState.COMPLETE:
            assert all(step.state is SyncStepState.INCOMPLETE for step in results[:-1])
            return results
        assert service.state is IndexState.INDEXING
    pytest.fail("continuation did not converge")


def phase(service, handle, desired):
    for _ in range(5000):
        if service._active_sync.phase == desired:
            return
        assert service.sync_step(handle, discovery_budget=2).state is SyncStepState.INCOMPLETE
    pytest.fail("phase was not reached")


@pytest.mark.parametrize("count", [0, 1, 43])
def test_bounded_steps_converge_without_eager_discovery_or_metadata(tmp_path, monkeypatch, count):
    repository = TrackingRepository(tmp_path / "index.sqlite3")
    service = semantic_service(tmp_path, repository=repository, index_batch_size=4)
    notes(service, count)
    folder = service.vault_root / "nested"
    folder.mkdir()
    (folder / "Extra.md").write_text("Oracle APEX", encoding="utf-8")
    for index in range(10):
        (folder / f"ignored-{index}.txt").touch()

    def forbidden(*args, **kwargs):
        pytest.fail("eager synchronization metadata/discovery")

    monkeypatch.setattr(SemanticRepositorySession, "load_notes", forbidden)
    monkeypatch.setattr(service, "_eligible_files", forbidden)
    handle = service.begin_sync()
    results = drain(service, handle)
    assert len(results) > count // 4
    assert results[-1].counts() == {"indexed": count + 1, "unchanged": 0, "removed": 0}
    assert repository.committed_batch_sizes == [4] * ((count + 1) // 4) + (
        [(count + 1) % 4] if (count + 1) % 4 else []
    )
    assert len(repository.load_chunks()) == count + 1
    assert service.state is IndexState.READY
    assert repository.get_metadata("last_successful_sync") is not None
    with pytest.raises(ValueError):
        service.sync_step(handle)


def test_discovery_budget_charges_non_markdown_entries_and_directory_opens(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    for index in range(21):
        (service.vault_root / f"ignored-{index}.txt").touch()
    consumed = []
    original = os.scandir

    class Cursor:
        def __init__(self, path):
            self.cursor = original(path)

        def __next__(self):
            item = next(self.cursor)
            consumed.append(item.name)
            return item

        def close(self):
            self.cursor.close()

    monkeypatch.setattr("app.services.semantic_sync.os.scandir", Cursor)
    handle = service.begin_sync()
    while service._active_sync.phase == "discover":
        before = len(consumed)
        result = service.sync_step(handle, discovery_budget=3)
        assert len(consumed) - before <= 3
        assert result.state is SyncStepState.INCOMPLETE
        assert service.repository.get_metadata("last_successful_sync") is None
    assert len(consumed) == 21
    monkeypatch.setattr("app.services.semantic_sync.os.scandir", original)
    drain(service, handle)


def test_processing_and_reconciliation_never_enumerate_siblings(tmp_path, monkeypatch):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes(service, 17)
    service.sync()
    for path in service.vault_root.iterdir():
        path.write_text("Changed content with CRLF\r\n", encoding="utf-8", newline="")
    handle = service.begin_sync()
    phase(service, handle, "process")
    original = os.scandir

    def forbidden(path):
        if not isinstance(path, int) and Path(path).is_relative_to(service.vault_root):
            pytest.fail("unbudgeted filesystem enumeration during processing")
        return original(path)

    monkeypatch.setattr(os, "scandir", forbidden)
    result = drain(service, handle)[-1]
    assert result.indexed == 17
    assert all(chunk.content.endswith("\r\n") for chunk in service.repository.load_chunks())


def test_target_admission_cancellation_does_not_mutate_notes(tmp_path):
    service = semantic_service(tmp_path)
    notes(service, 3)
    service.sync()
    previous = service.repository.load_chunks()
    event = threading.Event()

    def targets():
        yield "note-0000.md"
        event.set()
        yield "note-0001.md"
        pytest.fail("cancelled admission consumed remaining targets")

    handle = service.begin_sync(relative_paths=targets())
    with pytest.raises(SynchronizationCancelledError):
        service.sync_step(handle, event)
    assert handle.state is SyncStepState.CANCELLED
    assert service.repository.load_chunks() == previous
    assert service.state is IndexState.ERROR


def test_standalone_step_waiting_on_configuration_lock_can_be_closed(tmp_path):
    from tests.test_semantic_resources import ObservedLock

    service = semantic_service(tmp_path)
    notes(service, 3)
    handle = service.begin_sync()
    observed = ObservedLock(service._configuration_lock)
    service._configuration_lock = observed
    observed.lock.acquire()
    active = launch(lambda: service.sync_step(handle))
    try:
        assert observed.waiting.wait(3)
        closing = launch(service.close_synchronization)
        closing.result(2)
        with pytest.raises(SynchronizationCancelledError):
            active.result(2)
        assert observed.releases == 0 and observed.lock.locked()
        assert handle.state is SyncStepState.CANCELLED
        assert service.repository.get_metadata("last_successful_sync") is None
        assert service._active_sync is None
    finally:
        observed.lock.release()


def test_durable_batch_survives_abandonment_and_owner_restart(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes(service, 7)
    handle = service.begin_sync()
    phase(service, handle, "process")
    assert service.sync_step(handle).indexed == 2
    workspace = Path(service._active_sync.temporary.name)
    service.close_synchronization()
    assert handle.state is SyncStepState.CANCELLED
    assert not workspace.exists()
    assert len(service.repository.load_chunks()) == 2
    assert service.repository.get_metadata("last_successful_sync") is None
    with pytest.raises(ValueError):
        service.begin_sync()
    restarted = semantic_service(tmp_path, index_batch_size=2)
    assert restarted.sync() == {"indexed": 5, "unchanged": 2, "removed": 0}
    assert restarted.state is IndexState.READY


def test_later_batch_failure_keeps_earlier_commits_and_terminalizes(tmp_path):
    class FailsLater(FakeEmbedder):
        calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 4:
                raise RuntimeError("later batch failure")
            return super().embed(texts)

    service = semantic_service(tmp_path, embedder=FailsLater(), index_batch_size=2)
    notes(service, 7)
    handle = service.begin_sync()
    phase(service, handle, "process")
    assert service.sync_step(handle).indexed == 2
    with pytest.raises(RuntimeError, match="later batch"):
        service.sync_step(handle)
    assert handle.state is SyncStepState.FAILED
    assert len(service.repository.load_chunks()) == 2
    assert service.repository.get_metadata("last_successful_sync") is None
    assert service.state is IndexState.ERROR
    with pytest.raises(ValueError):
        service.sync_step(handle)
    service.embedder = FakeEmbedder()
    # Full recovery debt cannot be erased by a targeted refresh.
    assert service.sync_paths(("note-0000.md",)) == {"indexed": 5, "unchanged": 2, "removed": 0}


def test_stale_deletion_waits_for_complete_discovery(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=1)
    notes(service, 6)
    service.sync()
    (service.vault_root / "note-0000.md").unlink()
    handle = service.begin_sync()
    phase(service, handle, "process")
    # Even complete discovery does not imply reconciled metadata.
    assert len(service.repository.load_chunks()) == 6
    assert service.sync_step(handle).state is SyncStepState.INCOMPLETE
    assert len(service.repository.load_chunks()) == 6
    results = drain(service, handle)
    assert results[-1].counts() == {"indexed": 0, "unchanged": 5, "removed": 1}


def test_targeted_batch_rolls_back_missing_deletion_and_replacements(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=3)
    for name in ("a", "b", "c"):
        (service.vault_root / f"{name}.md").write_text(f"Original {name}", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    (service.vault_root / "a.md").unlink()
    (service.vault_root / "b.md").write_text("Replacement b", encoding="utf-8")
    (service.vault_root / "c.md").write_bytes(b"\xff")
    handle = service.begin_sync(relative_paths=("c.md", "a.md", "b.md"))
    phase(service, handle, "process")
    with pytest.raises(TargetedSynchronizationError):
        service.sync_step(handle)
    assert handle.state is SyncStepState.FAILED
    assert service.repository.load_chunks() == previous
    (service.vault_root / "c.md").write_text("Recovered c", encoding="utf-8")
    handle = service.begin_sync(relative_paths=("c.md", "a.md", "b.md", "b.md"))
    assert drain(service, handle)[-1].counts() == {"indexed": 2, "unchanged": 0, "removed": 1}


def test_target_admission_is_bounded_and_validates_all_before_mutations(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes(service, 9)
    service.sync()
    consumed = []

    def targets():
        for index in range(9):
            consumed.append(index)
            yield f"note-{index:04}.md"
        yield "../escape.md"

    before = service.repository.load_chunks()
    handle = service.begin_sync(relative_paths=targets())
    for _ in range(3):
        previous = len(consumed)
        assert service.sync_step(handle, discovery_budget=2).state is SyncStepState.INCOMPLETE
        assert len(consumed) - previous == 2
    with pytest.raises(TargetedSynchronizationError):
        drain(service, handle, budget=2)
    assert service.repository.load_chunks() == before
    assert handle.state is SyncStepState.FAILED


@pytest.mark.parametrize("when", ["before_begin", "between", "discovery", "metadata", "stale"])
def test_cancellation_never_publishes_success(tmp_path, monkeypatch, when):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes(service, 5)
    event = threading.Event()
    if when == "before_begin":
        event.set()
        with pytest.raises(SynchronizationCancelledError):
            service.begin_sync(cancel_event=event)
        assert not service.db_path.exists()
        return
    handle = service.begin_sync()
    if when == "between":
        event.set()
    elif when == "discovery":
        original = os.scandir

        def cancel(path):
            result = original(path)
            event.set()
            return result

        monkeypatch.setattr("app.services.semantic_sync.os.scandir", cancel)
    elif when == "metadata":
        phase(service, handle, "process")
        original = SemanticRepositorySession.get_note

        def cancel(session, path):
            result = original(session, path)
            event.set()
            return result

        monkeypatch.setattr(SemanticRepositorySession, "get_note", cancel)
    else:
        phase(service, handle, "stale")
        original = SemanticRepositorySession.note_paths_after

        def cancel(session, after, limit):
            result = original(session, after, limit)
            event.set()
            return result

        monkeypatch.setattr(SemanticRepositorySession, "note_paths_after", cancel)
    with pytest.raises(SynchronizationCancelledError):
        service.sync_step(handle, event)
    assert handle.state is SyncStepState.CANCELLED
    assert service.state is IndexState.ERROR
    assert service.repository.get_metadata("last_successful_sync") is None
    assert not service.repository._sessions and not service._sync_lock.locked()


@pytest.mark.parametrize("change", ["config", "root_loss", "root_replaced", "database", "signature"])
def test_resumed_work_rejects_changed_owner_basis(tmp_path, change):
    service = semantic_service(tmp_path, index_batch_size=1)
    notes(service, 3)
    handle = service.begin_sync()
    phase(service, handle, "process")
    service.sync_step(handle)
    if change == "config":
        service.reconfigure(max_note_bytes=500_000)
    elif change == "root_loss":
        service.vault_root.rename(tmp_path / "gone")
    elif change == "root_replaced":
        service.vault_root.rename(tmp_path / "gone")
        service.vault_root.mkdir()
        notes(service, 3)
    elif change == "database":
        service.db_path.rename(service.db_path.with_suffix(".old"))
        service.db_path.write_bytes(b"replacement")
    else:
        service.repository.set_metadata("index_signature", "foreign-signature")
    before = service.db_path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        service.sync_step(handle)
    assert handle.state is SyncStepState.FAILED
    if change != "database":
        assert service.repository.get_metadata("last_successful_sync") is None
    if change != "config":
        assert service.db_path.read_bytes() == before


def test_foreign_forged_conflicting_and_closed_handles_do_no_work(tmp_path):
    service = semantic_service(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = semantic_service(other_dir)
    notes(service, 2)
    notes(other, 3)
    first = service.begin_sync()
    second = other.begin_sync()
    before = service.db_path.read_bytes()
    for handle in (second, SyncContinuation(), None):
        with pytest.raises(ValueError):
            service.sync_step(handle)
    with pytest.raises(ValueError):
        service.begin_sync()
    assert service.db_path.read_bytes() == before
    assert drain(service, first)[-1].indexed == 2
    assert drain(other, second)[-1].indexed == 3
    service.close_synchronization()
    with pytest.raises(ValueError):
        service.begin_sync()


def test_bound_metadata_change_between_turns_is_non_destructive(shared, tmp_path):
    import sqlite3

    resource, _ = shared
    service = owner(tmp_path, resource, "work")
    handle = service.begin_sync()
    with sqlite3.connect(service.db_path) as connection:
        connection.execute("UPDATE meta SET value='foreign' WHERE key='knowledge_space_id'")
    before = service.db_path.read_bytes()
    with pytest.raises(IndexBindingUnavailableError):
        service.sync_step(handle)
    assert service.db_path.read_bytes() == before
    assert handle.state is SyncStepState.FAILED
    assert resource._pins == 0


def test_yields_unpin_and_generation_change_rejects_stale_handle(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource)
    handle = service.begin_sync()
    assert resource._pins == 0
    service.sync_step(handle, discovery_budget=1)
    assert resource._pins == 0 and not resource._busy
    generation = resource.generation
    resource.invalidate_embedding_compatibility_cache()
    assert resource.generation == generation + 1
    calls = state.calls.copy()
    with pytest.raises(SemanticSearchUnavailableError):
        service.sync_step(handle)
    assert handle.state is SyncStepState.FAILED
    assert state.calls == calls and resource._pins == 0
    assert service.sync()["indexed"] == 1


@pytest.mark.parametrize("wait_on", ["owner", "sync", "backend"])
def test_step_wait_cancellation_has_no_pin_leak(shared, tmp_path, wait_on):
    resource, state = shared
    service = owner(tmp_path, resource)
    service.sync()
    (service.vault_root / "a.md").write_text("changed", encoding="utf-8")
    handle = service.begin_sync()
    phase(service, handle, "process")
    event = threading.Event()
    if wait_on in {"owner", "sync"}:
        from tests.test_semantic_resources import ObservedLock

        name = "_fingerprint_lock" if wait_on == "owner" else "_sync_lock"
        observed = ObservedLock(getattr(service, name))
        setattr(service, name, observed)
        observed.lock.acquire()
        waiting = launch(lambda: service.sync_step(handle, event))
        assert observed.waiting.wait(3)
        event.set()
        try:
            with pytest.raises(SynchronizationCancelledError):
                waiting.result(1)
            assert observed.lock.locked() and observed.releases == 0
        finally:
            observed.lock.release()
            setattr(service, name, observed.lock)
    else:
        other = owner(tmp_path, resource, "work")
        other.sync()
        state.block = threading.Event()
        active = launch(lambda: other.search("blocking query"))
        assert state.entered.wait(3)
        entered = threading.Event()
        embed = resource.embed

        def waiting_embed(texts):
            entered.set()
            return embed(texts)

        # Test instrumentation observes admission to the resource lease wait.
        resource.embed = waiting_embed
        waiting = launch(lambda: service.sync_step(handle, event))
        assert entered.wait(3)
        event.set()
        try:
            with pytest.raises(SynchronizationCancelledError):
                waiting.result(1)
            assert resource._pins == 1 and resource._busy
        finally:
            state.block.set()
            active.result(3)
    assert handle.state is SyncStepState.CANCELLED
    assert resource._pins == 0 and not service._sync_lock.locked()


def test_queued_file_replaced_by_escape_fails_without_reading_external(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    notes(service, 1)
    handle = service.begin_sync()
    phase(service, handle, "process")
    outside = tmp_path / "outside.md"
    outside.write_text("outside bytes", encoding="utf-8")
    path = service.vault_root / "note-0000.md"
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        service.cancel_sync(handle)
        pytest.skip("symlink creation is unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("external content read")

    monkeypatch.setattr(VaultService, "read_verified_markdown_snapshot", forbidden)
    with pytest.raises(TargetedSynchronizationError):
        service.sync_step(handle)
    assert handle.state is SyncStepState.FAILED


@pytest.mark.parametrize("entry", ["sync", "sync_paths", "rebuild"])
def test_legacy_entry_points_return_only_complete_results(tmp_path, entry):
    service = semantic_service(tmp_path, index_batch_size=2)
    notes(service, 11)
    service.sync()
    for path in service.vault_root.iterdir():
        path.write_text("Changed TrueNAS", encoding="utf-8")
    result = (service.sync_paths([path.name for path in service.vault_root.iterdir()])
              if entry == "sync_paths" else getattr(service, entry)())
    assert result == {"indexed": 11, "unchanged": 0, "removed": 0}
    assert service.state is IndexState.READY and service._active_sync is None


def test_disabled_owner_does_no_model_storage_or_discovery_work(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    service._indexing_enabled = False

    def forbidden(*args, **kwargs):
        pytest.fail("disabled owner did work")

    monkeypatch.setattr(service.embedder, "resolve_embedding_fingerprint", forbidden)
    monkeypatch.setattr(service, "_validate_source_root", forbidden)
    monkeypatch.setattr(service.repository, "validate_expected_binding", forbidden)
    monkeypatch.setattr("app.services.semantic_sync.os.scandir", forbidden)
    with pytest.raises(SemanticSearchUnavailableError):
        service.begin_sync()
    with pytest.raises(SemanticSearchUnavailableError):
        service.sync_paths(("a.md",))
    assert service.sync_paths(()) == {"indexed": 0, "unchanged": 0, "removed": 0}
    assert not service.db_path.exists()


def test_shutdown_signals_active_step_and_rejects_overlapping_steps(shared, tmp_path):
    resource, state = shared
    service = owner(tmp_path, resource)
    notes(service, 8)
    handle = service.begin_sync()
    phase(service, handle, "process")
    state.block = threading.Event()
    active = launch(lambda: service.sync_step(handle))
    assert state.entered.wait(3)
    with pytest.raises(ValueError, match="already executing"):
        service.sync_step(handle)
    closing = launch(service.close_synchronization)
    # Wait for the cancellation request rather than a timing guess.
    assert service._active_sync.cancel_event.wait(3)
    assert not closing.done() and not active.done()
    state.block.set()
    with pytest.raises(SynchronizationCancelledError):
        active.result(3)
    closing.result(3)
    assert handle.state is SyncStepState.CANCELLED
    assert len(service.repository.load_chunks()) <= service.index_batch_size
    assert service.repository.get_metadata("last_successful_sync") is None
    assert resource._pins == 0 and service._active_sync is None


def test_cancellation_during_final_commit_retains_genuine_completed_result(shared, tmp_path, monkeypatch):
    resource, _ = shared
    service = owner(tmp_path, resource)
    handle = service.begin_sync()
    phase(service, handle, "stale")
    # One indexed metadata row is visited before the empty cursor finalizes.
    assert service.sync_step(handle).state is SyncStepState.INCOMPLETE
    event = threading.Event()
    finalize = service.repository.finalize_full_sync_success

    def final_commit(**kwargs):
        finalize(**kwargs)
        event.set()

    monkeypatch.setattr(service.repository, "finalize_full_sync_success", final_commit)
    result = service.sync_step(handle, event)
    assert result.state is SyncStepState.COMPLETE
    assert handle.state is SyncStepState.COMPLETE
    assert service.state is IndexState.READY
    assert service.repository.get_metadata("last_successful_sync") is not None
    assert resource._pins == 0


@pytest.mark.parametrize("use_shared", [False, True])
@pytest.mark.parametrize("storage", ["missing", "empty", "populated"])
def test_close_drains_preparation_before_reset(shared, tmp_path, monkeypatch, use_shared, storage):
    from tests.test_semantic_resources import ObservedLock

    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    if storage == "populated":
        notes(service, 2)
    else:
        for path in service.vault_root.iterdir():
            path.unlink()
    if storage != "missing":
        service.sync()
    previous = service.repository.load_chunks() if storage != "missing" else None
    entered, release, closed = (threading.Event() for _ in range(3))

    class ClosingLock(ObservedLock):
        def release(self):
            closing = service._sync_closed
            super().release()
            if closing:
                closed.set()

    service._sync_admin_lock = ClosingLock(service._sync_admin_lock)
    validate = service._validate_backend_before_destructive_prepare

    def blocked():
        entered.set()
        assert release.wait(5)

    if use_shared:
        resource.resolve_embedding_fingerprint()
        backend = resource._backend
        original = backend.validate_embedding_backend

        def validate_backend():
            blocked()
            original()

        monkeypatch.setattr(backend, "validate_embedding_backend", validate_backend)

    def validation(**kwargs):
        if use_shared:
            resource.validate_embedding_backend()
        else:
            blocked()
        validate(**kwargs)

    monkeypatch.setattr(service, "_validate_backend_before_destructive_prepare", validation)
    active = launch(lambda: service.begin_sync(rebuild=True))
    assert entered.wait(3)
    closing = launch(service.close_synchronization)
    try:
        assert closed.wait(3)
        # Close has crossed its admission fence, but the non-interruptible
        # validator is held by this barrier and must still be drained.
        with pytest.raises(TimeoutError):
            closing.result(0.1)
        if use_shared:
            assert resource._pins == 1 and resource._busy
    finally:
        release.set()
        with pytest.raises((SynchronizationCancelledError, ValueError)):
            active.result(3)
        closing.result(3)
    if previous is None:
        assert not service.db_path.exists()
    else:
        assert service.repository.load_chunks() == previous
    assert service._active_sync is None and not service._sync_lock.locked()
    assert resource._pins == 0 and not resource._busy
    with pytest.raises(ValueError):
        service.begin_sync(rebuild=True)


def test_standalone_preparation_cancellation_preserves_existing_index(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    previous = service.repository.load_chunks()
    entered, release, event = (threading.Event() for _ in range(3))

    def validation(**kwargs):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(service, "_validate_backend_before_destructive_prepare", validation)
    active = launch(lambda: service.begin_sync(rebuild=True, cancel_event=event))
    try:
        assert entered.wait(3)
        event.set()
    finally:
        release.set()
    with pytest.raises(SynchronizationCancelledError):
        active.result(3)
    assert service.repository.load_chunks() == previous
    assert service._active_sync is None and not service._sync_lock.locked()


def test_missing_target_recreated_before_processing_is_reindexed(tmp_path):
    service = semantic_service(tmp_path)
    note = service.vault_root / "a.md"
    note.write_text("Original indexed content", encoding="utf-8")
    service.sync()
    stamp = service.repository.get_metadata("last_successful_sync")
    note.unlink()
    handle = service.begin_sync(relative_paths=("a.md",))
    phase(service, handle, "process")
    note.write_text("Recreated changed content", encoding="utf-8")
    result = drain(service, handle)[-1]
    assert result.counts() == {"indexed": 1, "unchanged": 0, "removed": 0}
    assert service.repository.load_chunks()[0].content == "Recreated changed content"
    assert service.state is IndexState.READY
    assert service.repository.get_metadata("last_successful_sync") == stamp


@pytest.mark.parametrize("use_shared", [False, True])
@pytest.mark.parametrize("boundary", ["reset", "prepare"])
@pytest.mark.parametrize("action", ["close", "cancel"])
def test_preparation_fence_rechecks_before_destructive_boundary(
    shared, tmp_path, monkeypatch, use_shared, boundary, action,
):
    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    previous = service.repository.load_chunks()
    signature = service.repository.get_metadata("index_signature")
    if boundary == "prepare":
        # Exercise automatic signature reset as well as explicit rebuild.
        service.chunk_chars += 1
        service._resolved_index_signature = None
        service._state_initialized = False
    entered, release, event = (threading.Event() for _ in range(3))
    guard = service._sync_preparation

    @contextmanager
    def delayed(check):
        entered.set()
        assert release.wait(5)
        with guard(check):
            yield

    monkeypatch.setattr(service, "_sync_preparation", delayed)
    active = launch(lambda: service.begin_sync(rebuild=boundary == "reset", cancel_event=event))
    assert entered.wait(3)
    closing = None
    try:
        if action == "close":
            cancelled = next(iter(service._sync_operations))
            closing = launch(service.close_synchronization)
            assert cancelled.wait(3)
        else:
            event.set()
    finally:
        release.set()
    with pytest.raises(SynchronizationCancelledError):
        active.result(3)
    if closing is not None:
        closing.result(3)
    assert service.repository.load_chunks() == previous
    assert service.repository.get_metadata("index_signature") == signature
    assert not service._sync_operations and service._active_sync is None
    assert resource._pins == 0 and not resource._busy
    assert not service._sync_lock.locked() and not service._state_init_lock.locked()


@pytest.mark.parametrize("use_shared", [False, True])
def test_close_wins_before_preparation_admission(shared, tmp_path, monkeypatch, use_shared):
    resource, state = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    admission = service._sync_admission

    @contextmanager
    def delayed(event):
        entered.set()
        assert release.wait(5)
        with admission(event) as check:
            yield check

    monkeypatch.setattr(service, "_sync_admission", delayed)
    calls = state.calls.copy()
    active = launch(lambda: service.begin_sync(rebuild=True))
    try:
        assert entered.wait(3)
        service.close_synchronization()
    finally:
        release.set()
    with pytest.raises(ValueError, match="closed"):
        active.result(3)
    assert not service.db_path.exists()
    assert state.calls == calls and resource._pins == 0
    assert not service._sync_operations and service._active_sync is None


@pytest.mark.parametrize("use_shared", [False, True])
@pytest.mark.parametrize("boundary", ["reset_index", "prepare_index"])
def test_close_drains_already_admitted_storage_boundary(shared, tmp_path, monkeypatch, use_shared, boundary):
    from tests.test_semantic_resources import ObservedLock

    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    if boundary == "prepare_index":
        service._state_initialized = False
    entered, release, returned = (threading.Event() for _ in range(3))
    observed = ObservedLock(service._sync_admin_lock)
    service._sync_admin_lock = observed
    operation = getattr(service.repository, boundary)
    calls = []

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        assert not returned.is_set()
        calls.append(boundary)
        return operation(*args, **kwargs)

    monkeypatch.setattr(service.repository, boundary, blocked)
    active = launch(lambda: service.begin_sync(rebuild=boundary == "reset_index"))
    assert entered.wait(3)

    def close():
        service.close_synchronization()
        returned.set()

    closing = launch(close)
    try:
        # The guarded SQLite boundary already owns the fence. Close must
        # wait for that boundary, without admitting any operation after return.
        assert observed.waiting.wait(3)
        assert not closing.done()
    finally:
        release.set()
    try:
        active.result(3)
    except SynchronizationCancelledError:
        pass
    closing.result(3)
    assert calls == [boundary] and returned.is_set()
    assert not service._sync_operations and service._active_sync is None
    assert resource._pins == 0 and not resource._busy
    with pytest.raises(ValueError):
        service.begin_sync(rebuild=True)
    assert calls == [boundary]


def test_close_drains_completed_turn_resource_exit(shared, tmp_path, monkeypatch):
    resource, _ = shared
    service = owner(tmp_path, resource)
    handle = service.begin_sync()
    phase(service, handle, "stale")
    assert service.sync_step(handle).state is SyncStepState.INCOMPLETE
    entered, release = threading.Event(), threading.Event()
    operation = service._embedding_operation

    @contextmanager
    def delayed(*args, **kwargs):
        with operation(*args, **kwargs):
            yield
            if handle.state is SyncStepState.COMPLETE:
                entered.set()
                assert release.wait(5)

    monkeypatch.setattr(service, "_embedding_operation", delayed)
    active = launch(lambda: service.sync_step(handle))
    assert entered.wait(3)
    assert service._active_sync is None and resource._pins == 1
    cancelled = next(iter(service._sync_operations))
    closing = launch(service.close_synchronization)
    try:
        assert cancelled.wait(3)
        assert not closing.done()
    finally:
        release.set()
    assert active.result(3).state is SyncStepState.COMPLETE
    closing.result(3)
    assert resource._pins == 0 and not resource._busy
    assert not service._sync_operations


@pytest.mark.parametrize("use_shared", [False, True])
def test_preparation_failure_releases_reservation_and_allows_retry(shared, tmp_path, monkeypatch, use_shared):
    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    previous = service.repository.load_chunks()
    validate = service._validate_backend_before_destructive_prepare

    def failure(**kwargs):
        raise RuntimeError("validation failed")

    monkeypatch.setattr(service, "_validate_backend_before_destructive_prepare", failure)
    with pytest.raises(RuntimeError, match="validation failed"):
        service.begin_sync(rebuild=True)
    assert service.repository.load_chunks() == previous
    assert not service._sync_operations and service._active_sync is None
    assert resource._pins == 0 and not resource._busy
    monkeypatch.setattr(service, "_validate_backend_before_destructive_prepare", validate)
    assert service.rebuild()["indexed"] == len(previous)
    service.close_synchronization()
    service.close_synchronization()


@pytest.mark.parametrize("replacement", [
    "missing", "changed", "invalid_utf8", "directory", "oversized", "escape", "inaccessible", "cancel",
])
def test_targeted_missing_revalidation_is_atomic_for_mixed_batch(tmp_path, monkeypatch, replacement):
    service = semantic_service(tmp_path, index_batch_size=3)
    for name in ("a", "b", "c"):
        (service.vault_root / f"{name}.md").write_text(f"Original {name}", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    for name in ("a", "b"):
        (service.vault_root / f"{name}.md").unlink()
    (service.vault_root / "c.md").write_text("Changed current c", encoding="utf-8")
    handle = service.begin_sync(relative_paths=("c.md", "b.md", "a.md", "b.md"))
    phase(service, handle, "process")
    path = service.vault_root / "b.md"
    if replacement == "changed":
        path.write_text("Recreated changed b", encoding="utf-8")
    elif replacement == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif replacement == "directory":
        path.mkdir()
    elif replacement == "oversized":
        path.write_bytes(b"a" * (service.max_note_bytes + 1))
    elif replacement == "escape":
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        try:
            path.symlink_to(outside)
        except OSError:
            service.cancel_sync(handle)
            pytest.skip("symlink creation is unavailable")
    elif replacement == "inaccessible":
        original = Path.resolve

        def inaccessible(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError("target inaccessible")
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", inaccessible)
    event = threading.Event()
    if replacement == "cancel":
        original = service._target_files

        def cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            event.set()
            return result

        monkeypatch.setattr(service, "_target_files", cancel)
    if replacement in {"missing", "changed"}:
        results = drain(service, handle)
        assert results[-1].counts() == {
            "indexed": 2 if replacement == "changed" else 1,
            "unchanged": 0,
            "removed": 1 if replacement == "changed" else 2,
        }
        assert service.state is IndexState.READY
        current = {chunk.path: chunk.content for chunk in service.repository.load_chunks()}
        assert current == ({"b.md": "Recreated changed b", "c.md": "Changed current c"}
                           if replacement == "changed" else {"c.md": "Changed current c"})
    else:
        error = SynchronizationCancelledError if replacement == "cancel" else TargetedSynchronizationError
        with pytest.raises(error):
            service.sync_step(handle, event)
        assert service.repository.load_chunks() == previous
        assert service.state is IndexState.ERROR
        assert service._active_sync is None
    assert not service._sync_operations and not service.repository._sessions


def test_repair2_identical_reconfigure_preserves_continuation(tmp_path):
    service = semantic_service(tmp_path, index_batch_size=1)
    notes(service, 4)
    handle = service.begin_sync()
    revision = service._sync_configuration_revision
    for _ in range(4):
        service.reconfigure(vault_root=service.vault_root, max_note_bytes=service.max_note_bytes)
        service.reconfigure()
    assert drain(service, handle)[-1].counts() == {"indexed": 4, "unchanged": 0, "removed": 0}
    assert service._sync_configuration_revision == revision


def test_repair2_related_route_preserves_yielded_full_sync(tmp_path):
    from tests.test_api import auth, client_for

    (tmp_path / "a.md").write_text("TrueNAS backup", encoding="utf-8")
    client = client_for(tmp_path)
    service = client.app.state.semantic_search_service
    service.sync()
    handle = service.begin_sync()
    assert service.sync_step(handle, discovery_budget=1).state is SyncStepState.INCOMPLETE
    response = client.post("/notes/related", headers=auth(), json={"text": "TrueNAS", "min_score": 0.0})
    assert response.status_code == 200
    assert drain(service, handle)[-1].counts() == {"indexed": 0, "unchanged": 1, "removed": 0}
    assert service.state is IndexState.READY


@pytest.mark.parametrize("stage", ["resolve", "stat"])
def test_repair2_discovery_inspection_error_preserves_index(tmp_path, monkeypatch, stage):
    service = semantic_service(tmp_path)
    note = service.vault_root / "a.md"
    note.write_text("Previously indexed TrueNAS", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    stamp = service.repository.get_metadata("last_successful_sync")
    handle = service.begin_sync()
    original = getattr(Path, stage)

    def denied(path, *args, **kwargs):
        if path == note:
            raise PermissionError("temporary discovery failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, stage, denied)
    with pytest.raises(OSError):
        drain(service, handle, budget=1)
    monkeypatch.setattr(Path, stage, original)
    assert handle.state is SyncStepState.FAILED
    assert service.repository.load_chunks() == previous
    assert service.state is IndexState.ERROR
    assert service.repository.get_metadata("last_successful_sync") == stamp
    assert service.sync() == {"indexed": 0, "unchanged": 1, "removed": 0}


def test_repair2_dangling_parent_cannot_authorize_targeted_deletion(tmp_path):
    service = semantic_service(tmp_path)
    folder = service.vault_root / "sub"
    folder.mkdir()
    note = folder / "a.md"
    note.write_text("Previously indexed", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    note.unlink()
    handle = service.begin_sync(relative_paths=("sub/a.md",))
    phase(service, handle, "process")
    folder.rmdir()
    try:
        folder.symlink_to(service.vault_root / "absent", target_is_directory=True)
    except OSError:
        service.cancel_sync(handle)
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(TargetedSynchronizationError):
        drain(service, handle)
    assert service.repository.load_chunks() == previous
    assert service.state is IndexState.ERROR


@pytest.mark.parametrize("second_entry", ["sync", "rebuild", "sync_paths"])
def test_repair2_concurrent_legacy_calls_wait_for_complete_operation(tmp_path, monkeypatch, second_entry):
    service = semantic_service(tmp_path, index_batch_size=1)
    notes(service, 3)
    service.sync()
    entered, release, started = (threading.Event() for _ in range(3))
    step = service.sync_step

    def paused(*args, **kwargs):
        result = step(*args, **kwargs)
        if not entered.is_set():
            assert result.state is SyncStepState.INCOMPLETE
            assert not service._sync_lock.locked() and not service.repository._sessions
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(service, "sync_step", paused)
    first = launch(service.sync)
    assert entered.wait(3)

    def second():
        started.set()
        if second_entry == "sync_paths":
            return service.sync_paths(("note-0000.md",))
        return getattr(service, second_entry)()

    waiting = launch(second)
    try:
        assert started.wait(3)
        with pytest.raises(TimeoutError):
            waiting.result(0.1)
    finally:
        release.set()
        first.result(3)
        # Always consume the second future, including the original conflict.
        try:
            result = waiting.result(3)
        except ValueError:
            result = None
    assert result is not None
    assert result == ({"indexed": 3, "unchanged": 0, "removed": 0} if second_entry == "rebuild" else
                      {"indexed": 0, "unchanged": 1 if second_entry == "sync_paths" else 3, "removed": 0})
    assert service.state is IndexState.READY and service._active_sync is None


@pytest.mark.parametrize("changed", [False, True])
def test_reconfiguration_serializes_with_active_turn(tmp_path, monkeypatch, changed):
    from tests.test_semantic_resources import ObservedLock

    service = semantic_service(tmp_path)
    notes(service, 3)
    service.sync()
    previous = service.repository.load_chunks()
    handle = service.begin_sync()
    work = service._active_sync
    discover = work.discover
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return discover(*args)

    monkeypatch.setattr(work, "discover", blocked)
    observed = ObservedLock(service._configuration_lock)
    service._configuration_lock = observed
    active = launch(lambda: service.sync_step(handle, discovery_budget=1))
    assert entered.wait(3)
    updating = launch(lambda: service.reconfigure(max_note_bytes=service.max_note_bytes - int(changed)))
    try:
        assert observed.waiting.wait(3)
        assert not updating.done()
    finally:
        release.set()
    assert active.result(3).state is SyncStepState.INCOMPLETE
    updating.result(3)
    service._configuration_lock = observed.lock
    assert service.repository.load_chunks() == previous
    if changed:
        with pytest.raises(ValueError, match="configuration changed"):
            service.sync_step(handle)
        assert handle.state is SyncStepState.FAILED and service.state is IndexState.ERROR
    else:
        assert drain(service, handle)[-1].unchanged == 3


@pytest.mark.parametrize("use_shared", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_reconfiguration_during_preparation_cannot_reset_on_changed_configuration(
    shared, tmp_path, monkeypatch, use_shared, changed,
):
    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    previous = service.repository.load_chunks()
    entered, release = threading.Event(), threading.Event()
    validate = service._validate_backend_before_destructive_prepare

    def blocked(**kwargs):
        entered.set()
        assert release.wait(5)
        validate(**kwargs)

    monkeypatch.setattr(service, "_validate_backend_before_destructive_prepare", blocked)
    active = launch(lambda: service.begin_sync(rebuild=True))
    try:
        assert entered.wait(3)
        service.reconfigure(max_note_bytes=service.max_note_bytes - int(changed))
    finally:
        release.set()
    if changed:
        with pytest.raises(ValueError, match="configuration changed during preparation"):
            active.result(3)
        assert service.repository.load_chunks() == previous
        assert service._active_sync is None
    else:
        assert drain(service, active.result(3))[-1].indexed == len(previous)
    assert resource._pins == 0 and not service._sync_operations


def test_noop_reconfigure_keeps_binding_validation(shared, tmp_path, monkeypatch):
    resource, _ = shared
    service = owner(tmp_path, resource)
    handle = service.begin_sync()
    revision = service._sync_configuration_revision

    def rejected(root):
        raise IndexBindingUnavailableError

    with monkeypatch.context() as patch:
        patch.setattr(service, "_validate_source_root", rejected)
        with pytest.raises(IndexBindingUnavailableError):
            service.reconfigure(vault_root=service.vault_root, max_note_bytes=service.max_note_bytes)
    assert service._sync_configuration_revision == revision
    assert drain(service, handle)[-1].indexed == 1


@pytest.mark.parametrize("use_shared", [False, True])
def test_close_does_not_deadlock_with_noop_reconfiguration(shared, tmp_path, monkeypatch, use_shared):
    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    handle = service.begin_sync()
    entered, release = threading.Event(), threading.Event()
    marker = threading.local()
    validate = service._validate_source_root

    def blocked(root):
        if getattr(marker, "updating", False):
            entered.set()
            assert release.wait(5)
        validate(root)

    monkeypatch.setattr(service, "_validate_source_root", blocked)

    def update():
        marker.updating = True
        service.reconfigure()

    updating = launch(update)
    try:
        assert entered.wait(3)
        launch(service.close_synchronization).result(2)
    finally:
        release.set()
    updating.result(3)
    assert handle.state is SyncStepState.CANCELLED
    assert not service._sync_operations and resource._pins == 0


def test_partial_discovery_entry_error_never_authorizes_stale_deletion(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    notes(service, 3)
    service.sync()
    previous = service.repository.load_chunks()
    stamp = service.repository.get_metadata("last_successful_sync")
    handle = service.begin_sync()
    original = os.scandir
    consumed = []

    class Entry:
        def __init__(self, entry):
            self.entry = entry

        def __getattr__(self, name):
            return getattr(self.entry, name)

        def is_dir(self, **kwargs):
            consumed.append(self.entry.name)
            if self.entry.name == "note-0001.md":
                raise PermissionError("entry inspection unavailable")
            return self.entry.is_dir(**kwargs)

    class Cursor:
        def __init__(self):
            with original(service.vault_root) as entries:
                self.entries = iter(sorted(entries, key=lambda entry: entry.name))

        def __next__(self):
            return Entry(next(self.entries))

        def close(self):
            pass

    def scan(path):
        return Cursor() if path == service.vault_root else original(path)

    monkeypatch.setattr(os, "scandir", scan)
    with pytest.raises(PermissionError):
        drain(service, handle, budget=1)
    monkeypatch.setattr(os, "scandir", original)
    assert consumed == ["note-0000.md", "note-0001.md"]
    assert service.repository.load_chunks() == previous
    assert service.state is IndexState.ERROR and handle.state is SyncStepState.FAILED
    assert service.repository.get_metadata("last_successful_sync") == stamp
    assert service.sync()["unchanged"] == 3


def test_discovery_oserror_during_resolution_is_failure(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    notes(service, 1)
    handle = service.begin_sync()
    resolve = Path.resolve

    def failed(path, *args, **kwargs):
        if path.name == "note-0000.md":
            raise OSError("transient filesystem failure")
        return resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failed)
    with pytest.raises(OSError):
        drain(service, handle)
    assert handle.state is SyncStepState.FAILED and service.state is IndexState.ERROR
    assert service.repository.get_metadata("last_successful_sync") is None


@pytest.mark.parametrize("stage", ["resolve", "stat"])
def test_discovery_race_with_confirmed_ordinary_deletion_can_complete(tmp_path, monkeypatch, stage):
    service = semantic_service(tmp_path)
    notes(service, 2)
    service.sync()
    handle = service.begin_sync()
    note = service.vault_root / "note-0001.md"
    original = getattr(Path, stage)
    deleted = False

    def deleting(path, *args, **kwargs):
        nonlocal deleted
        if path == note and not deleted:
            deleted = True
            note.unlink()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, stage, deleting)
    assert drain(service, handle)[-1].counts() == {"indexed": 0, "unchanged": 1, "removed": 1}
    assert service.state is IndexState.READY


@pytest.mark.parametrize("parent", [
    "ordinary", "missing", "dangling", "escape", "valid_alias", "inaccessible", "reappeared",
])
def test_missing_target_checks_ancestor_evidence(tmp_path, monkeypatch, parent):
    service = semantic_service(tmp_path)
    folder = service.vault_root / "sub"
    folder.mkdir()
    note = folder / "a.md"
    note.write_text("Previous valid content", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    stamp = service.repository.get_metadata("last_successful_sync")
    note.unlink()
    handle = service.begin_sync(relative_paths=("sub/a.md",))
    phase(service, handle, "process")
    if parent in {"missing", "dangling", "escape", "valid_alias"}:
        folder.rmdir()
    if parent in {"dangling", "escape", "valid_alias"}:
        target = tmp_path / "outside" if parent == "escape" else service.vault_root / "alias-target"
        if parent != "dangling":
            target.mkdir()
        try:
            folder.symlink_to(target, target_is_directory=True)
        except OSError:
            service.cancel_sync(handle)
            pytest.skip("directory symlink creation is unavailable")
    if parent == "inaccessible":
        original = Path.lstat

        def inaccessible(path, *args, **kwargs):
            if path == folder:
                raise PermissionError("ancestor unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", inaccessible)
    elif parent == "reappeared":
        note.write_text("Recreated current content", encoding="utf-8")
    if parent in {"dangling", "escape", "inaccessible"}:
        with pytest.raises(TargetedSynchronizationError):
            drain(service, handle)
        assert service.repository.load_chunks() == previous
        assert service.state is IndexState.ERROR and handle.state is SyncStepState.FAILED
    else:
        result = drain(service, handle)[-1]
        assert result.counts() == {
            "indexed": int(parent == "reappeared"), "unchanged": 0, "removed": int(parent != "reappeared"),
        }
        assert service.state is IndexState.READY
    assert service.repository.get_metadata("last_successful_sync") == stamp


def test_parent_alias_replaced_during_absence_check_preserves_rows(tmp_path, monkeypatch):
    service = semantic_service(tmp_path)
    folder = service.vault_root / "sub"
    folder.mkdir()
    note = folder / "a.md"
    note.write_text("Previous valid content", encoding="utf-8")
    service.sync()
    previous = service.repository.load_chunks()
    note.unlink()
    handle = service.begin_sync(relative_paths=("sub/a.md",))
    phase(service, handle, "process")
    folder.rmdir()
    target = service.vault_root / "real"
    target.mkdir()
    try:
        folder.symlink_to(target, target_is_directory=True)
    except OSError:
        service.cancel_sync(handle)
        pytest.skip("directory symlink creation is unavailable")
    original = Path.lstat
    swapped = False

    def replaced(path, *args, **kwargs):
        nonlocal swapped
        try:
            return original(path, *args, **kwargs)
        except FileNotFoundError:
            if path == note and not swapped:
                swapped = True
                folder.unlink()
                folder.symlink_to(service.vault_root / "absent", target_is_directory=True)
            raise

    monkeypatch.setattr(Path, "lstat", replaced)
    with pytest.raises(TargetedSynchronizationError):
        drain(service, handle)
    assert swapped and service.repository.load_chunks() == previous
    assert handle.state is SyncStepState.FAILED and service.state is IndexState.ERROR


@pytest.mark.parametrize("use_shared", [False, True])
def test_legacy_waiter_spans_multiple_yielded_turns(shared, tmp_path, monkeypatch, use_shared):
    from tests.test_semantic_resources import ObservedLock

    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    notes(service, 3)
    service.sync()
    observed = ObservedLock(service._legacy_sync_lock)
    service._legacy_sync_lock = observed
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    calls = 0
    step = service.sync_step

    def paused(*args, **kwargs):
        nonlocal calls
        result = step(*args, **kwargs)
        index = calls
        calls += 1
        if index < 2:
            entered[index].set()
            assert release[index].wait(5)
        return result

    monkeypatch.setattr(service, "sync_step", paused)
    active = launch(service.sync)
    assert entered[0].wait(3)
    waiting = launch(service.sync)
    try:
        assert observed.waiting.wait(3)
        for index in range(2):
            assert entered[index].wait(3)
            assert not waiting.done()
            assert resource._pins == 0 and not service._sync_lock.locked()
            with pytest.raises(ValueError, match="reserved"):
                service.begin_sync()
            release[index].set()
    finally:
        for event in release:
            event.set()
    count = 4 if use_shared else 3
    assert active.result(3) == waiting.result(3) == {"indexed": 0, "unchanged": count, "removed": 0}
    assert not service._sync_operations and resource._pins == 0


@pytest.mark.parametrize("use_shared", [False, True])
@pytest.mark.parametrize("action", ["cancel_waiter", "close"])
def test_queued_legacy_cancellation_and_close_drain_safely(shared, tmp_path, monkeypatch, use_shared, action):
    from tests.test_semantic_resources import ObservedLock

    resource, _ = shared
    service = owner(tmp_path, resource) if use_shared else semantic_service(tmp_path)
    service.sync()
    entered, release, event = (threading.Event() for _ in range(3))
    observed = ObservedLock(service._legacy_sync_lock)
    service._legacy_sync_lock = observed
    step = service.sync_step

    def paused(*args, **kwargs):
        result = step(*args, **kwargs)
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(service, "sync_step", paused)
    active = launch(service.sync)
    assert entered.wait(3)
    handle = service._active_sync.handle
    waiting = launch(lambda: service.rebuild(event))
    closing = None
    try:
        assert observed.waiting.wait(3)
        if action == "close":
            closing = launch(service.close_synchronization)
            assert service._active_sync.cancel_event.wait(3)
        else:
            event.set()
        if use_shared:
            with pytest.raises(SynchronizationCancelledError):
                waiting.result(1)
        assert not active.done() and resource._pins == 0
    finally:
        release.set()
    with pytest.raises(SynchronizationCancelledError):
        waiting.result(3)
    if closing is not None:
        with pytest.raises(SynchronizationCancelledError):
            active.result(3)
        closing.result(3)
        assert handle.state is SyncStepState.CANCELLED
        assert service._active_sync is None
        with pytest.raises(ValueError, match="closed"):
            service.sync()
    else:
        assert active.result(3)["removed"] == 0
        assert handle.state is SyncStepState.COMPLETE
        assert service.sync()["indexed"] == 0
    assert resource._pins == 0 and not service._sync_operations
    assert not observed.lock.locked() and not service._sync_lock.locked()
