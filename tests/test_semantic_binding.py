"""E1 private binding safety; synthetic SQLite and model-free dependencies."""

import dataclasses
import hashlib
import os
import shutil
import sqlite3
from contextlib import closing

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository, SemanticRepositorySession, StoredChunk, StoredNote
from app.repositories.semantic_binding import (
    BINDING_KEYS,
    ExpectedIndexBinding,
    IndexBindingUnavailableError,
)
from app.repositories.semantic_reads import SemanticReadUnavailableError
from app.services.knowledge_spaces import SpaceError, SpaceId
from app.services.scoped_budget import BudgetExhausted, CancellationToken, ScopedBudget
from app.services.semantic_search import SemanticSearchService, SemanticSearchUnavailableError

SID = SpaceId("research")


def bound(tmp_path, *, path=None, root=None, sid="research"):
    root = root or tmp_path / "root"
    root.mkdir(exist_ok=True)
    binding = ExpectedIndexBinding(sid, root)
    return SemanticRepository(path or tmp_path / "data" / "index.sqlite3", _expected_binding=binding)


def legacy(path):
    repository = SemanticRepository(path)
    repository.prepare_index("old")
    # All handles closed: no active sidecars during immutable lifecycle preflight.
    return repository


def populate(repository):
    with repository.transaction() as session:
        session.replace_note(StoredNote("a.md", 0, 1, "hash", "now"), [
            StoredChunk("a.md", 0, None, "private", b"1234", 1),
        ])


def snapshot(path):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.parent.iterdir() if p.is_file()}


def read(repository, *, budget=None, cancel=None, sid=SID):
    return repository.read_eligible_snapshot(
        ("a.md",), budget=budget or ScopedBudget((sid,)), space_id=sid,
        cancel=cancel or CancellationToken(),
    )


def metadata(path):
    with closing(sqlite3.connect(path)) as connection:
        return dict(connection.execute("SELECT key,value FROM meta"))


def test_missing_initialization_exact_private_immutable_contract(tmp_path):
    repository = bound(tmp_path)
    binding = repository._expected_binding
    repository.validate_expected_binding()
    assert not repository.db_path.parent.exists()
    assert binding.root_sha256 == hashlib.sha256(
        os.path.normcase(str((tmp_path / "root").resolve(strict=True))).encode("utf-8")
    ).hexdigest()
    assert str(tmp_path) not in repr(binding)
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.space_id = "other"
    with pytest.raises(AttributeError):
        repository._expected_binding = None
    repository.prepare_index("sig")
    assert tuple(metadata(repository.db_path)[key] for key in BINDING_KEYS) == binding.values
    populate(repository)
    assert read(repository).chunks[0].content == "private"


def test_empty_legacy_is_initialized_only_by_lifecycle(tmp_path):
    path = tmp_path / "index.sqlite3"
    legacy(path)
    repository = bound(tmp_path, path=path)
    before = snapshot(path)
    repository.validate_expected_binding()
    assert repository.read_immutable_status().storage_initialized
    assert snapshot(path) == before
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)
    assert snapshot(path) == before
    repository.prepare_index("new")
    assert tuple(metadata(path)[key] for key in BINDING_KEYS) == repository._expected_binding.values


@pytest.mark.parametrize("rows", ["notes", "chunks", "both"])
def test_populated_unbound_never_adopted(tmp_path, rows):
    path = tmp_path / "index.sqlite3"
    old = legacy(path)
    populate(old)
    with closing(sqlite3.connect(path)) as connection:
        if rows == "notes":
            connection.execute("DELETE FROM chunks")
        elif rows == "chunks":
            connection.execute("DELETE FROM notes")  # Deliberate orphan, foreign_keys off.
        connection.commit()
    assert_rejected_untouched(bound(tmp_path, path=path))


def assert_rejected_untouched(repository):
    before = snapshot(repository.db_path)
    for action in (
        lambda: repository.prepare_index("different"),
        lambda: repository.reset_index("different", "uninitialized"),
        lambda: repository.set_metadata("index_state", "ready"),
        lambda: repository.finalize_full_sync_success(last_successful_sync="now", ready_state="ready"),
        repository.load_chunks, repository.load_chunks_read_only,
        lambda: repository.get_metadata("index_signature"),
    ):
        with pytest.raises(IndexBindingUnavailableError):
            action()
        assert snapshot(repository.db_path) == before
    with pytest.raises(IndexBindingUnavailableError), repository.transaction():
        pytest.fail("transaction admitted")
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)
    assert snapshot(repository.db_path) == before
    assert repository.read_immutable_status().storage_error
    assert not repository.read_availability_status().has_chunks
    assert snapshot(repository.db_path) == before


@pytest.mark.parametrize("key,value", [
    (BINDING_KEYS[0], "2"), (BINDING_KEYS[0], "01"), (BINDING_KEYS[0], b"1"),
    (BINDING_KEYS[1], "other"), (BINDING_KEYS[1], " research"),
    (BINDING_KEYS[2], "0" * 64), (BINDING_KEYS[2], "invalid"),
    *[(key, None) for key in BINDING_KEYS],
])
@pytest.mark.parametrize("populated", [False, True])
def test_contradictory_or_partial_binding_untouched_even_empty(tmp_path, key, value, populated):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    if populated:
        populate(repository)
    with closing(sqlite3.connect(repository.db_path)) as connection:
        if value is None:
            connection.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            connection.execute("UPDATE meta SET value=? WHERE key=?", (value, key))
        connection.commit()
    assert_rejected_untouched(repository)


@pytest.mark.parametrize("changed", ["id", "root"])
def test_copied_database_is_not_rebound(tmp_path, changed):
    original = bound(tmp_path)
    original.prepare_index("sig")
    populate(original)
    target = tmp_path / "copy.sqlite3"
    shutil.copyfile(original.db_path, target)
    repository = bound(tmp_path, path=target,
                       sid="other" if changed == "id" else "research",
                       root=tmp_path / "other-root" if changed == "root" else None)
    assert_rejected_untouched(repository)


@pytest.mark.parametrize("kind", ["corrupt", "no_tables", "missing_notes", "bad_columns", "view", "duplicate_meta"])
def test_corrupt_or_unrecognized_storage_not_recovered(tmp_path, kind):
    path = tmp_path / "index.sqlite3"
    if kind == "corrupt":
        path.write_bytes(b"not SQLite")
    else:
        legacy(path)
        with closing(sqlite3.connect(path)) as connection:
            if kind == "no_tables":
                connection.executescript("DROP TABLE chunks; DROP TABLE notes; DROP TABLE meta;")
            elif kind == "missing_notes":
                connection.execute("DROP TABLE notes")
            elif kind == "bad_columns":
                connection.executescript("DROP TABLE notes; CREATE TABLE notes(x);")
            elif kind == "view":
                connection.executescript("DROP TABLE notes; CREATE VIEW notes AS SELECT 'x' AS path;")
            elif kind == "duplicate_meta":
                connection.executescript("DROP TABLE meta; CREATE TABLE meta(key,value);")
                connection.executemany("INSERT INTO meta VALUES(?,?)", [(BINDING_KEYS[0], "1")] * 2)
            connection.commit()
    assert_rejected_untouched(bound(tmp_path, path=path))


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("columns", ["path", "path,chunk_index"])
def test_chunks_path_index_accepts_required_leading_column(tmp_path, initialized, columns):
    path = tmp_path / "index.sqlite3"
    repository = bound(tmp_path, path=path)
    if initialized:
        repository.prepare_index("old")
        populate(repository)
    else:
        legacy(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            f"DROP INDEX idx_chunks_path; CREATE INDEX idx_chunks_path ON chunks({columns});"
        )
    before = snapshot(path)
    repository.validate_expected_binding()
    assert snapshot(path) == before
    repository.prepare_index("old")
    assert tuple(metadata(path)[key] for key in BINDING_KEYS) == repository._expected_binding.values
    assert len(read(repository).chunks) == int(initialized)


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("definition", [
    "notes(path)", "chunks(chunk_index,path)", "chunks(path) WHERE chunk_index=0", None,
])
def test_invalid_chunks_path_index_rejected_before_writable_work(
    tmp_path, monkeypatch, initialized, definition,
):
    path = tmp_path / "index.sqlite3"
    repository = bound(tmp_path, path=path)
    if initialized:
        repository.prepare_index("old")
        populate(repository)
    else:
        legacy(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP INDEX idx_chunks_path")
        if definition is not None:
            connection.execute(f"CREATE INDEX idx_chunks_path ON {definition}")
        connection.commit()
    original = sqlite3.connect
    def read_only(database, *args, **kwargs):
        assert "mode=ro" in str(database)
        return original(database, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid schema reached index preparation")
    monkeypatch.setattr(sqlite3, "connect", read_only)
    monkeypatch.setattr(repository, "_prepare_connection", forbidden)
    assert_rejected_untouched(repository)


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("definition", ["notes(path)", "chunks(path) WHERE chunk_index=0"])
def test_invalid_index_rejection_preserves_existing_sidecars(tmp_path, initialized, suffix, definition):
    path = tmp_path / "index.sqlite3"
    repository = bound(tmp_path, path=path)
    if initialized:
        repository.prepare_index("old")
    else:
        legacy(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            f"DROP INDEX idx_chunks_path; CREATE INDEX idx_chunks_path ON {definition};"
        )
    path.with_name(path.name + suffix).write_bytes(b"unowned sidecar must remain unchanged")
    before = snapshot(path)
    for action in (repository.validate_expected_binding, lambda: repository.prepare_index("new")):
        with pytest.raises(IndexBindingUnavailableError):
            action()
        assert snapshot(path) == before


@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("suffix", [None, "-wal", "-shm", "-journal"])
@pytest.mark.parametrize("kind", ["named", "additional", "composite", "table_constraint", "partial"])
def test_unique_chunks_path_index_rejected_without_modification(
    tmp_path, monkeypatch, initialized, populated, suffix, kind,
):
    path = tmp_path / "index.sqlite3"
    repository = bound(tmp_path, path=path)
    if initialized:
        repository.prepare_index("old")
    else:
        legacy(path)
    if populated:
        populate(repository if initialized else SemanticRepository(path))
    with closing(sqlite3.connect(path)) as connection:
        if kind == "named":
            connection.executescript(
                "DROP INDEX idx_chunks_path; CREATE UNIQUE INDEX idx_chunks_path ON chunks(path);"
            )
        elif kind == "table_constraint":
            schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='chunks'").fetchone()[0]
            connection.execute("CREATE TEMP TABLE saved_chunks AS SELECT * FROM chunks")
            connection.execute("DROP TABLE chunks")
            connection.execute(schema.replace("FOREIGN KEY(path)", "UNIQUE(path), FOREIGN KEY(path)"))
            connection.execute("INSERT INTO chunks SELECT * FROM saved_chunks")
            connection.execute("CREATE INDEX idx_chunks_path ON chunks(path)")
            assert connection.execute(
                "SELECT count(*) FROM pragma_index_list('chunks') WHERE origin='u'",
            ).fetchone()[0] == 1
        else:
            columns = "path,dimensions" if kind == "composite" else "path"
            predicate = " WHERE dimensions=1" if kind == "partial" else ""
            connection.execute(f"CREATE UNIQUE INDEX unexpected_unique_path ON chunks({columns}){predicate}")
        connection.commit()
        if kind != "named":
            assert connection.execute(
                'SELECT "unique", partial FROM pragma_index_list(\'chunks\') WHERE name=\'idx_chunks_path\'',
            ).fetchone() == (0, 0)
    before_metadata = metadata(path)
    if not initialized:
        assert not any(key in before_metadata for key in BINDING_KEYS)
    if suffix is not None:
        path.with_name(path.name + suffix).write_bytes(b"unowned sidecar must remain unchanged")
    before = snapshot(path)
    original = sqlite3.connect
    def read_only(database, *args, **kwargs):
        assert "mode=ro" in str(database), "invalid index reached writable preparation"
        return original(database, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid index reached destructive preparation")
    monkeypatch.setattr(sqlite3, "connect", read_only)
    monkeypatch.setattr(repository, "_prepare_connection", forbidden)
    for action in (
        repository.validate_expected_binding,
        lambda: repository.prepare_index("new"),
        lambda: repository.reset_index("new", "uninitialized"),
    ):
        with pytest.raises(IndexBindingUnavailableError):
            action()
        assert snapshot(path) == before  # Includes indexed rows, metadata and sidecars.
    if suffix is None:
        assert_rejected_untouched(repository)
        with closing(original(path)) as connection:
            assert dict(connection.execute("SELECT key,value FROM meta")) == before_metadata
            assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == int(populated)


def test_all_keys_rollback_atomically_with_failed_preparation(tmp_path, monkeypatch):
    path = tmp_path / "index.sqlite3"
    legacy(path)
    repository = bound(tmp_path, path=path)
    before = snapshot(path)
    def failed(connection, signature):
        assert tuple(connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0]
                     for key in BINDING_KEYS) == repository._expected_binding.values
        # A separate observer cannot see any uncommitted binding keys.
        assert not any(key in metadata(path) for key in BINDING_KEYS)
        raise RuntimeError("injected failure")
    monkeypatch.setattr(repository, "_prepare_connection", failed)
    with pytest.raises(RuntimeError, match="injected"):
        repository.prepare_index("new")
    assert snapshot(path) == before


def test_failure_mid_binding_insert_rolls_back_every_key(tmp_path, monkeypatch):
    path = tmp_path / "index.sqlite3"
    legacy(path)
    repository = bound(tmp_path, path=path)
    before = snapshot(path)
    original = sqlite3.connect
    class Connection(sqlite3.Connection):
        def executemany(self, sql, parameters):
            if sql == "INSERT INTO meta(key,value) VALUES(?,?)":
                self.execute(sql, next(iter(parameters)))
                raise RuntimeError("binding insert interrupted")
            return super().executemany(sql, parameters)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
    with pytest.raises(RuntimeError, match="binding insert interrupted"):
        repository.prepare_index("new")
    assert snapshot(path) == before
    assert not any(key in metadata(path) for key in BINDING_KEYS)


@pytest.mark.parametrize("operation", ["prepare", "reset"])
def test_transaction_local_revalidation_before_signature_or_reset(tmp_path, monkeypatch, operation):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    changed = []
    def racing(database, *args, **kwargs):
        if "mode=rw" in str(database):
            with closing(original(repository.db_path)) as writer:
                writer.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
                writer.commit()
            changed.append(snapshot(repository.db_path))
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", racing)
    with pytest.raises(IndexBindingUnavailableError):
        if operation == "prepare":
            repository.prepare_index("new")
        else:
            repository.reset_index("new", "uninitialized")
    assert snapshot(repository.db_path) == changed[0]
    assert metadata(repository.db_path)["index_signature"] == "sig"


def test_authorized_signature_reset_and_rebuild_preserve_binding(tmp_path):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    expected = repository._expected_binding.values
    assert repository.prepare_index("changed").signature_changed
    assert read(repository).chunks == ()
    populate(repository)
    repository.reset_index("reset", "uninitialized")
    assert read(repository).chunks == ()
    assert tuple(metadata(repository.db_path)[key] for key in BINDING_KEYS) == expected
    with pytest.raises(IndexBindingUnavailableError):
        repository.set_metadata(BINDING_KEYS[1], "other")


@pytest.mark.parametrize("resource", ["semantic_bytes", "sqlite_steps", "probes"])
def test_binding_metadata_budget_exhaustion_and_no_partial_result(tmp_path, resource):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    before = snapshot(repository.db_path)
    budget = ScopedBudget((SID,), limits={resource: (0, 0)})
    with pytest.raises(BudgetExhausted) as caught:
        read(repository, budget=budget)
    assert caught.value.resource == resource
    assert snapshot(repository.db_path) == before


def test_binding_length_preflight_prevents_materialization_and_cancels(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    original = sqlite3.connect
    token = CancellationToken()
    seen = []
    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=Cursor)
    class Cursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            seen.append((sql, parameters))
            if sql.startswith("SELECT count(*), max(length"):
                token.cancel()
            return super().execute(sql, parameters)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
    with pytest.raises(SpaceError, match="operation_cancelled"):
        read(repository, cancel=token)
    assert not any(sql.startswith("SELECT value FROM meta") for sql, _ in seen)


def test_bounded_binding_is_in_same_transaction_before_chunk_materialization(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    seen = []
    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=Cursor)
    class Cursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            seen.append((sql, parameters, self.connection.in_transaction))
            return super().execute(sql, parameters)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
    budget = ScopedBudget((SID,))
    assert len(read(repository, budget=budget).chunks) == 1
    binding_reads = [i for i, (sql, args, tx) in enumerate(seen)
                     if sql.startswith("SELECT value FROM meta") and args[0] in BINDING_KEYS]
    assert len(binding_reads) == 6  # Rejection-only preflight, then locked validation.
    assert all(seen[i][2] for i in binding_reads)
    chunk_read = next(i for i, (sql, _, _) in enumerate(seen) if sql.startswith("SELECT CASE"))
    assert max(binding_reads) < chunk_read
    assert budget.used[SID]["semantic_bytes"] >= sum(map(len, repository._expected_binding.values))
    assert budget.used[SID]["sqlite_steps"] > 0


@pytest.mark.parametrize("target", ["db", "directory", "root"])
def test_replacement_since_construction_fails_closed(tmp_path, target):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    if target == "db":
        replacement = tmp_path / "replacement"
        shutil.copyfile(repository.db_path, replacement)
        repository.db_path.rename(tmp_path / "old-db")
        replacement.rename(repository.db_path)
    elif target == "directory":
        repository.db_path.parent.rename(tmp_path / "old-data")
        repository.db_path.parent.mkdir()
        shutil.copyfile(tmp_path / "old-data" / "index.sqlite3", repository.db_path)
    else:
        (tmp_path / "root").rename(tmp_path / "old-root")
        (tmp_path / "root").mkdir()
    assert_rejected_untouched(repository)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_bound_lifecycle_refuses_sidecars_without_modification(tmp_path, suffix):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    sidecar = repository.db_path.with_name(repository.db_path.name + suffix)
    sidecar.write_bytes(b"untrusted sidecar")
    before = snapshot(repository.db_path)
    with pytest.raises(IndexBindingUnavailableError):
        repository.reset_index("new", "uninitialized")
    assert snapshot(repository.db_path) == before


@pytest.mark.parametrize("target", ["db", "-wal", "-shm", "directory"])
def test_symlink_aliases_rejected_without_following(tmp_path, target):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    outside = tmp_path / "outside"
    outside.write_bytes(b"private outside")
    if target == "directory":
        repository.db_path.parent.rename(tmp_path / "old-data")
        link = repository.db_path.parent
        destination = tmp_path / "old-data"
    else:
        link = repository.db_path if target == "db" else repository.db_path.with_name(repository.db_path.name + target)
        if target == "db":
            link.unlink()
        destination = outside
    try:
        link.symlink_to(destination, target_is_directory=target == "directory")
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(IndexBindingUnavailableError):
        repository.prepare_index("new")
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)
    assert outside.read_bytes() == b"private outside"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_database_and_sidecar_hardlinks_rejected(tmp_path, suffix):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    target = repository.db_path.with_name(repository.db_path.name + suffix)
    if suffix:
        target.write_bytes(b"aliased sidecar")
    alias = tmp_path / "alias"
    try:
        os.link(target, alias)
    except OSError:
        pytest.skip("hardlinks unavailable")
    before = snapshot(repository.db_path)
    with pytest.raises(IndexBindingUnavailableError):
        repository.prepare_index("new")
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)
    assert snapshot(repository.db_path) == before


@pytest.mark.parametrize("disabled", [False, True])
def test_binding_failure_or_disabled_index_never_resolves_or_embeds(tmp_path, disabled):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    with closing(sqlite3.connect(repository.db_path)) as connection:
        connection.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
        connection.commit()
    class Forbidden:
        def resolve_embedding_fingerprint(self):
            pytest.fail("fingerprint/model work")
        def embed(self, texts):
            pytest.fail("inference")
    service = SemanticSearchService(vault_root=tmp_path / "root", repository=repository,
                                    embedder=Forbidden(), indexing_enabled=not disabled)
    before = snapshot(repository.db_path)
    for action in (service.sync, service.rebuild, lambda: service.sync_paths(["a.md"]),
                   lambda: service._embed(["query"])):
        with pytest.raises((IndexBindingUnavailableError, SemanticSearchUnavailableError)):
            action()
    assert snapshot(repository.db_path) == before


def test_default_legacy_data_and_signature_behavior_unchanged(tmp_path):
    repository = legacy(tmp_path / "index.sqlite3")
    populate(repository)
    before = snapshot(repository.db_path)
    assert len(repository.load_chunks_read_only()) == 1
    assert snapshot(repository.db_path) == before
    assert not any(key in metadata(repository.db_path) for key in BINDING_KEYS)
    assert not repository.prepare_index("old").signature_changed
    assert len(repository.load_chunks()) == 1
    assert repository.prepare_index("new").signature_changed
    assert repository.load_chunks() == []


@pytest.mark.parametrize("outcome", ["complete", "rows", "bytes", "vm", "cancel"])
def test_rollback_writer_cannot_mix_binding_and_chunks(tmp_path, monkeypatch, outcome):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    repository = bound(tmp_path)
    repository.prepare_index("sig")
    repository.set_metadata("index_state", "ready")
    populate(repository)
    original = sqlite3.connect
    begin_write, commit_attempted, reader_finished = Event(), Event(), Event()
    blocked = []
    connections = []
    token = CancellationToken()
    budget = ScopedBudget((SID,))

    def write():
        assert begin_write.wait(10)
        with closing(original(repository.db_path, timeout=0)) as writer:
            assert writer.execute("PRAGMA journal_mode").fetchone() == ("delete",)
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
            writer.execute("UPDATE meta SET value='foreign' WHERE key='index_signature'")
            writer.execute("UPDATE meta SET value='error' WHERE key='index_state'")
            writer.execute("UPDATE chunks SET content='foreign namespace'")
            try:
                writer.commit()
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
                blocked.append(True)
            else:
                blocked.append(False)
            commit_attempted.set()
            assert reader_finished.wait(10)
            writer.commit()  # Proves that every reader exit releases its lock.

    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=Cursor)

    class Cursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            # Binding, signature and state have been read; chunks have not.
            if sql == "SELECT EXISTS(SELECT 1 FROM chunks LIMIT 1)":
                begin_write.set()
                assert commit_attempted.wait(10)
                if outcome == "cancel":
                    token.cancel()
                elif outcome != "complete":
                    resource = {"rows": "semantic_rows", "bytes": "semantic_bytes",
                                "vm": "sqlite_steps"}[outcome]
                    budget.charge(SID, resource, budget.remaining(SID, resource))
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        assert "mode=ro" in str(args[0])
        connection = original(*args, **kwargs, factory=Connection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with ThreadPoolExecutor(max_workers=1) as executor:
        writer = executor.submit(write)
        try:
            if outcome == "complete":
                result = read(repository, budget=budget, cancel=token)
                assert (result.index_signature, result.index_state) == ("sig", "ready")
                assert [chunk.content for chunk in result.chunks] == ["private"]
            elif outcome == "cancel":
                with pytest.raises(SpaceError, match="operation_cancelled"):
                    read(repository, budget=budget, cancel=token)
            else:
                with pytest.raises(BudgetExhausted):
                    read(repository, budget=budget, cancel=token)
        finally:
            reader_finished.set()
            writer.result(timeout=15)
    assert blocked == [True]
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    with closing(original(repository.db_path)) as connection:
        assert connection.execute("SELECT content FROM chunks").fetchone() == ("foreign namespace",)
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)


def test_exclusive_writer_makes_snapshot_unavailable_without_fallback(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        assert "mode=ro" in str(args[0]) and "immutable" not in str(args[0])
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    with closing(original(repository.db_path, timeout=0)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("UPDATE chunks SET content='uncommitted'")
        monkeypatch.setattr(sqlite3, "connect", connect)
        with pytest.raises(SemanticReadUnavailableError):
            read(repository)
        writer.rollback()
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    monkeypatch.setattr(sqlite3, "connect", original)
    assert read(repository).chunks[0].content == "private"


def test_rejection_screening_never_authorizes_live_snapshot(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    opened = []

    def connect(database, *args, **kwargs):
        opened.append(database)
        if "immutable" not in database:
            with closing(original(repository.db_path)) as writer:
                writer.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
                writer.execute("UPDATE chunks SET content='foreign namespace'")
                writer.commit()
        return original(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)
    assert len(opened) == 2
    assert "immutable=1" in opened[0] and opened[1].endswith("?mode=ro")


def test_live_wal_binding_and_chunks_share_snapshot(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    with closing(original(repository.db_path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE chunks SET content='committed WAL'")
        writer.commit()
        class Connection(sqlite3.Connection):
            def cursor(self, *args, **kwargs):
                return super().cursor(factory=Cursor)
        class Cursor(sqlite3.Cursor):
            def execute(self, sql, parameters=()):
                if sql.startswith("SELECT CASE"):
                    writer.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
                    writer.execute("UPDATE chunks SET content='new namespace'")
                    writer.commit()
                return super().execute(sql, parameters)
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
        assert read(repository).chunks[0].content == "committed WAL"
        before = snapshot(repository.db_path)
        with pytest.raises(SemanticReadUnavailableError):
            read(repository)
        after = snapshot(repository.db_path)
        # Live SQLite owns SHM read marks, but neither DB nor WAL may change.
        for name in (repository.db_path.name, repository.db_path.name + "-wal"):
            assert before[name] == after[name]


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_live_sidecar_replacement_discards_snapshot(tmp_path, monkeypatch, suffix):
    if os.name == "nt":
        pytest.skip("Windows locks active SQLite files; exercised on POSIX")
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    from app.repositories import semantic_reads
    original = semantic_reads._storage_identity
    count = 0
    with closing(sqlite3.connect(repository.db_path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE chunks SET content='wal'")
        writer.commit()
        def replaced(path, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                sidecar = path.with_name(path.name + suffix)
                moved = tmp_path / "moved-sidecar"
                sidecar.rename(moved)
                shutil.copyfile(moved, sidecar)
            return original(path, **kwargs)
        monkeypatch.setattr(semantic_reads, "_storage_identity", replaced)
        with pytest.raises(SemanticReadUnavailableError):
            read(repository)


def test_root_replacement_during_bounded_read_discards_snapshot(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    from app.repositories import semantic_reads
    original = semantic_reads._storage_identity
    count = 0
    def replaced(path, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            (tmp_path / "root").rename(tmp_path / "previous-root")
            (tmp_path / "root").mkdir()
        return original(path, **kwargs)
    monkeypatch.setattr(semantic_reads, "_storage_identity", replaced)
    with pytest.raises(SemanticReadUnavailableError):
        read(repository)


def test_binding_byte_exhaustion_stops_before_payload_query(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    original = sqlite3.connect
    seen = []
    class Connection(sqlite3.Connection):
        def cursor(self, *args, **kwargs):
            return super().cursor(factory=Cursor)
    class Cursor(sqlite3.Cursor):
        def execute(self, sql, parameters=()):
            seen.append(sql)
            return super().execute(sql, parameters)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
    with pytest.raises(BudgetExhausted):
        read(repository, budget=ScopedBudget((SID,), limits={"semantic_bytes": (0, 0)}))
    assert any(sql.startswith("SELECT count(*), max(length") for sql in seen)
    assert not any(sql.startswith("SELECT value FROM meta") for sql in seen)


def bound_service(tmp_path, *, batch_size=25):
    repository = bound(tmp_path)
    class LocalEmbedder:
        def embed(self, texts):
            return [np.array([1.0, 0.0], dtype=np.float32) for _ in texts]
    service = SemanticSearchService(
        vault_root=tmp_path / "root", repository=repository, embedder=LocalEmbedder(),
        embedding_fingerprint="embedding-v1:" + "0" * 64, index_batch_size=batch_size,
    )
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / "root" / name).write_text("first content " + name, encoding="utf-8")
    return service


@pytest.mark.parametrize("operation", ["sync", "sync_paths", "rebuild"])
def test_bound_multi_note_batches_succeed(tmp_path, operation):
    service = bound_service(tmp_path)
    assert service.sync()["indexed"] == 3
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / "root" / name).write_text("changed longer content " + name, encoding="utf-8")
    result = (service.sync_paths(["a.md", "b.md", "c.md"])
              if operation == "sync_paths" else getattr(service, operation)())
    assert result["indexed"] == 3
    assert len(service.repository.load_chunks_read_only()) == 3
    assert tuple(metadata(service.db_path)[key] for key in BINDING_KEYS) == service.repository._expected_binding.values


@pytest.mark.parametrize("operation", ["sync", "sync_paths", "rebuild"])
def test_nonunique_path_index_supports_multi_chunk_sync(tmp_path, operation):
    service = bound_service(tmp_path)
    note = service.vault_root / "a.md"
    note.write_text("# First\n\n" + "First section. " * 60 + "\n\n# Second\n\nSecond section.", encoding="utf-8")
    assert service.sync()["indexed"] == 3
    note.write_text(
        "# First\n\n" + "Updated first section. " * 60 + "\n\n# Second\n\nUpdated second section.", encoding="utf-8",
    )
    result = (service.sync_paths(["a.md"]) if operation == "sync_paths" else getattr(service, operation)())
    assert result["indexed"] >= 1
    chunks = [chunk for chunk in service.repository.load_chunks_read_only() if chunk.path == "a.md"]
    assert len(chunks) >= 2
    assert len({chunk.chunk_index for chunk in chunks}) == len(chunks)
    with closing(sqlite3.connect(service.db_path)) as connection:
        assert connection.execute(
            'SELECT "unique", partial FROM pragma_index_list(\'chunks\') WHERE name=\'idx_chunks_path\'',
        ).fetchone() == (0, 0)


def test_bound_batch_metadata_update_before_embedding(tmp_path):
    service = bound_service(tmp_path, batch_size=1)
    service.sync()
    service.index_batch_size = 25
    first = tmp_path / "root" / "a.md"
    info = first.stat()
    os.utime(first, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
    (tmp_path / "root" / "b.md").write_text("new longer content", encoding="utf-8")
    result = service.sync_paths(["a.md", "b.md"])
    assert result == {"indexed": 1, "unchanged": 1, "removed": 0}
    with service.repository.transaction() as session:
        assert session.load_notes()["a.md"].mtime_ns == first.stat().st_mtime_ns


@pytest.mark.parametrize("stage", ["connect", "schema", "binding", "prepare", "transaction"])
def test_missing_storage_failure_restores_missing_and_allows_retry(tmp_path, monkeypatch, stage):
    repository = bound(tmp_path)
    original = sqlite3.connect
    with monkeypatch.context() as patch:
        class Connection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if stage == "schema" and sql.strip().startswith("CREATE TABLE IF NOT EXISTS notes"):
                    raise RuntimeError("initialization failed")
                return super().execute(sql, parameters)
            def executemany(self, sql, parameters):
                if stage == "binding" and sql == "INSERT INTO meta(key,value) VALUES(?,?)":
                    self.execute(sql, next(iter(parameters)))
                    raise RuntimeError("initialization failed")
                return super().executemany(sql, parameters)
        def connect(database, *args, **kwargs):
            if stage == "connect" and "mode=rw" in str(database):
                raise RuntimeError("initialization failed")
            return original(database, *args, **kwargs, factory=Connection)
        patch.setattr(sqlite3, "connect", connect)
        if stage == "prepare":
            def failed(connection, signature):
                raise RuntimeError("initialization failed")
            patch.setattr(repository, "_prepare_connection", failed)
        with pytest.raises(RuntimeError, match="initialization failed"):
            if stage == "transaction":
                with repository.transaction() as session:
                    session.replace_note(StoredNote("a.md", 0, 1, "hash", "now"), [])
                    raise RuntimeError("initialization failed")
            else:
                repository.prepare_index("sig")
    assert not repository.db_path.exists()
    assert not list(repository.db_path.parent.glob("index.sqlite3*"))
    repository.prepare_index("sig")
    assert tuple(metadata(repository.db_path)[key] for key in BINDING_KEYS) == repository._expected_binding.values


def test_index_embedding_requires_issued_active_local_transaction(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    service = bound_service(tmp_path)
    repository = service.repository
    repository.prepare_index(service.index_signature)
    with repository.transaction() as session:
        session.replace_note(StoredNote("a.md", 0, 1, "hash", "now"), [])
        assert len(service._embed(["inside"], _session=session)) == 1
        # An active indexing transaction does not relax external callers.
        with pytest.raises(IndexBindingUnavailableError):
            service._embed(["outside"])
        forged = SemanticRepositorySession(session._connection, _repository=repository)
        with pytest.raises(IndexBindingUnavailableError):
            service._embed(["forged"], _session=forged)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service._embed, ["foreign thread"], _session=session)
            with pytest.raises(IndexBindingUnavailableError):
                future.result()
        other = bound(tmp_path, path=tmp_path / "other.sqlite3", sid="other")
        with other.transaction() as foreign:
            with pytest.raises(IndexBindingUnavailableError):
                service._embed(["foreign repository"], _session=foreign)
    with pytest.raises(IndexBindingUnavailableError):
        service._embed(["stale session"], _session=session)


def test_transaction_binding_mismatch_blocks_embedding_and_preserves_data(tmp_path, monkeypatch):
    service = bound_service(tmp_path)
    service.sync()
    repository = service.repository
    before = snapshot(repository.db_path)
    def forbidden(texts):
        pytest.fail("inference after namespace mismatch")
    monkeypatch.setattr(service.embedder, "embed", forbidden)
    with pytest.raises(IndexBindingUnavailableError), repository.transaction() as session:
        session._connection.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
        service._embed(["query"], _session=session)
    assert snapshot(repository.db_path) == before


@pytest.mark.parametrize("kind", ["nonempty", "sidecar", "hardlink", "replacement", "directory"])
def test_failed_initialization_cleanup_refuses_unproven_ownership(tmp_path, monkeypatch, kind):
    if kind in {"replacement", "directory"} and os.name == "nt":
        pytest.skip("Windows holds the creation descriptor; replacement exercised on POSIX")
    repository = bound(tmp_path)
    original_cleanup = repository._cleanup_initialization
    closed_connection = []
    protected = []
    def failed(connection, signature):
        closed_connection.append(connection)
        raise RuntimeError("initialization failed")
    def cleanup(attempt):
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            closed_connection[0].execute("SELECT 1")
        path = repository.db_path
        if kind == "nonempty":
            path.write_bytes(b"other process storage")
        elif kind == "sidecar":
            path.with_name(path.name + "-journal").write_bytes(b"unowned journal")
        elif kind == "hardlink":
            os.link(path, tmp_path / "alias")
        elif kind == "replacement":
            path.rename(tmp_path / "own-old-db")
            path.write_bytes(b"replacement storage")
        else:
            path.parent.rename(tmp_path / "old-data")
            path.parent.mkdir()
            path.write_bytes(b"replacement directory storage")
        protected.append(snapshot(path))
        original_cleanup(attempt)
        assert snapshot(path) == protected[0]
    monkeypatch.setattr(repository, "_prepare_connection", failed)
    monkeypatch.setattr(repository, "_cleanup_initialization", cleanup)
    with pytest.raises(RuntimeError, match="initialization failed"):
        repository.prepare_index("sig")
    assert repository.db_path.exists()
    with pytest.raises(IndexBindingUnavailableError):
        repository.validate_expected_binding()


def test_failed_prepare_preserves_existing_bound_data_and_metadata(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    before = snapshot(repository.db_path)
    original = repository._prepare_connection
    def failed(connection, signature):
        original(connection, signature)  # Destructive signature work, then failure.
        raise RuntimeError("preparation failed")
    monkeypatch.setattr(repository, "_prepare_connection", failed)
    with pytest.raises(RuntimeError, match="preparation failed"):
        repository.prepare_index("new")
    assert snapshot(repository.db_path) == before
    assert len(repository.load_chunks_read_only()) == 1


def test_cleanup_rechecks_identity_after_releasing_creation_descriptor(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    peer = legacy(tmp_path / "peer.sqlite3")
    populate(peer)
    peer_bytes = peer.db_path.read_bytes()
    original_cleanup = repository._cleanup_initialization
    original_close = os.close
    protected = []
    def failed(connection, signature):
        raise RuntimeError("initialization failed")
    def cleanup(attempt):
        creation_descriptor = attempt[1]
        def close(descriptor):
            original_close(descriptor)
            if descriptor == creation_descriptor:
                repository.db_path.rename(tmp_path / "original-created-file")
                repository.db_path.write_bytes(peer_bytes)
                protected.append(snapshot(repository.db_path))
        with monkeypatch.context() as patch:
            patch.setattr(os, "close", close)
            original_cleanup(attempt)
        assert snapshot(repository.db_path) == protected[0]
    monkeypatch.setattr(repository, "_prepare_connection", failed)
    monkeypatch.setattr(repository, "_cleanup_initialization", cleanup)
    with pytest.raises(RuntimeError, match="initialization failed"):
        repository.prepare_index("sig")
    assert repository.db_path.read_bytes() == peer_bytes
    assert len(SemanticRepository(repository.db_path).load_chunks_read_only()) == 1


def test_bound_source_root_reconfiguration_is_rejected_atomically(tmp_path):
    service = bound_service(tmp_path)
    service.sync()
    other = tmp_path / "other-root"
    other.mkdir()
    (other / "foreign.md").write_text("foreign namespace", encoding="utf-8")
    before_storage = snapshot(service.db_path)
    before_configuration = vars(service).copy()
    with pytest.raises(IndexBindingUnavailableError):
        service.reconfigure(vault_root=other, max_note_bytes=42)
    assert vars(service) == before_configuration
    assert snapshot(service.db_path) == before_storage


def test_bound_construction_rejects_foreign_root_before_model_or_storage(tmp_path, monkeypatch):
    repository = bound(tmp_path)
    other = tmp_path / "other-root"
    other.mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail("model/storage work before source-root validation")
    monkeypatch.setattr("app.services.semantic_search.FastEmbedder.__init__", forbidden)
    monkeypatch.setattr(repository, "validate_expected_binding", forbidden)
    with pytest.raises(IndexBindingUnavailableError):
        SemanticSearchService(vault_root=other, repository=repository)
    assert not repository.db_path.parent.exists()


@pytest.mark.parametrize("representation", ["relative", "dotdot", "symlink", "case"])
def test_bound_root_alternate_representations_are_rejected(tmp_path, monkeypatch, representation):
    service = bound_service(tmp_path)
    root = service.vault_root
    if representation == "relative":
        monkeypatch.chdir(tmp_path)
        alternate = root.relative_to(tmp_path)
    elif representation == "dotdot":
        alternate = root / ".." / root.name
    elif representation == "symlink":
        alternate = tmp_path / "root-alias"
        try:
            alternate.symlink_to(root, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable")
    else:
        alternate = root.with_name(root.name.upper())
        if not alternate.exists():
            pytest.skip("case alias requires case-insensitive filesystem")
    assert alternate.resolve(strict=True) == root.resolve(strict=True)
    before = vars(service).copy()
    with pytest.raises(IndexBindingUnavailableError):
        service.reconfigure(vault_root=alternate, max_note_bytes=42)
    assert vars(service) == before
    with pytest.raises(IndexBindingUnavailableError):
        SemanticSearchService(vault_root=alternate, repository=service.repository)
    assert not service.db_path.exists()


@pytest.mark.parametrize("operation", ["sync", "sync_paths", "empty_sync_paths", "rebuild",
                                       "initialize", "signature", "embed", "prepare"])
@pytest.mark.parametrize("sidecar", [None, "-wal", "-shm", "-journal"])
def test_effective_source_root_mismatch_rejects_before_work(tmp_path, monkeypatch, operation, sidecar):
    service = bound_service(tmp_path)
    service.sync()
    other = tmp_path / "other-root"
    other.mkdir()
    (other / "foreign.md").write_text("foreign namespace", encoding="utf-8")
    if sidecar is not None:
        service.db_path.with_name(service.db_path.name + sidecar).write_bytes(b"untouched sidecar")
    before = snapshot(service.db_path)
    service.vault_root = other  # Simulate an already inconsistent internal caller.
    configuration = vars(service).copy()
    def forbidden(*args, **kwargs):
        pytest.fail("repository/model/discovery work after source-root mismatch")
    monkeypatch.setattr(service.repository, "validate_expected_binding", forbidden)
    monkeypatch.setattr(service.repository, "_connect", forbidden)
    monkeypatch.setattr(service.embedder, "embed", forbidden)
    monkeypatch.setattr(service, "_eligible_files", forbidden)
    monkeypatch.setattr(service, "_target_files", forbidden)
    actions = {
        "sync": service.sync,
        "sync_paths": lambda: service.sync_paths(["foreign.md"]),
        "empty_sync_paths": lambda: service.sync_paths([]),
        "rebuild": service.rebuild,
        "initialize": lambda: service._initialize_state(create_storage=True),
        "signature": lambda: service.index_signature,
        "embed": lambda: service._embed(["query"]),
        "prepare": lambda: service.prepare_federation_candidates(
            ("foreign.md",), space_id=SID, budget=ScopedBudget((SID,)), cancel=CancellationToken(),
        ),
    }
    with pytest.raises(IndexBindingUnavailableError):
        actions[operation]()
    # Ignore test instrumentation when comparing actual service state.
    for name, value in configuration.items():
        assert getattr(service, name) is value
    assert snapshot(service.db_path) == before


def test_same_bound_root_reconfiguration_remains_usable(tmp_path):
    service = bound_service(tmp_path)
    service.sync()
    service.reconfigure(vault_root=service.vault_root, max_note_bytes=500_000)
    assert service.max_note_bytes == 500_000
    service.reconfigure(max_note_bytes=400_000)
    assert service.sync()["unchanged"] == 3
    assert service.rebuild()["indexed"] == 3
    assert tuple(metadata(service.db_path)[key] for key in BINDING_KEYS) == service.repository._expected_binding.values


def test_legacy_unbound_root_reconfiguration_still_indexes_new_root(tmp_path):
    service = bound_service(tmp_path)
    service.repository = SemanticRepository(service.db_path)
    service.sync()
    other = tmp_path / "other-root"
    other.mkdir()
    (other / "foreign.md").write_text("legacy new source", encoding="utf-8")
    service.reconfigure(vault_root=other, max_note_bytes=500_000)
    assert service.rebuild()["indexed"] == 1
    assert {chunk.path for chunk in service.repository.load_chunks_read_only()} == {"foreign.md"}
    assert not any(key in metadata(service.db_path) for key in BINDING_KEYS)

@pytest.mark.parametrize("initialized", [False, True])
@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("defect", [
    "meta_pk", "notes_pk", "chunks_pk", "composite_pk", "integer_pk", "descending_pk",
    "missing_fk", "no_cascade", "wrong_fk", "ctas",
])
def test_required_schema_constraints_reject_before_writable_work(
    tmp_path, monkeypatch, initialized, populated, defect,
):
    path = tmp_path / "index.sqlite3"
    repository = bound(tmp_path, path=path)
    if initialized:
        repository.prepare_index("old")
    else:
        legacy(path)
    if populated:
        populate(repository if initialized else SemanticRepository(path))
    table = {"meta_pk": "meta", "notes_pk": "notes"}.get(defect, "chunks")
    with closing(sqlite3.connect(path)) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table,),
        ).fetchone()[0]
        connection.execute(f"CREATE TEMP TABLE saved AS SELECT * FROM {table}")
        connection.execute(f"DROP TABLE {table}")
        if defect == "ctas":
            connection.execute(f"CREATE TABLE {table} AS SELECT * FROM saved")
        else:
            if defect in {"meta_pk", "notes_pk"}:
                sql = sql.replace("TEXT PRIMARY KEY", "TEXT")
            elif defect == "chunks_pk":
                sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER")
            elif defect == "composite_pk":
                sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER")
                sql = sql.replace("FOREIGN KEY(path)", "PRIMARY KEY(id,path), FOREIGN KEY(path)")
            elif defect == "integer_pk":
                sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INT PRIMARY KEY")
            elif defect == "descending_pk":
                sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "INTEGER PRIMARY KEY DESC")
            elif defect == "missing_fk":
                sql = sql.replace(
                    ",\n                    FOREIGN KEY(path) REFERENCES notes(path) ON DELETE CASCADE", "",
                )
            elif defect == "no_cascade":
                sql = sql.replace("ON DELETE CASCADE", "ON DELETE NO ACTION")
            elif defect == "wrong_fk":
                sql = sql.replace("REFERENCES notes(path)", "REFERENCES meta(key)")
            connection.execute(sql)
            connection.execute(f"INSERT INTO {table} SELECT * FROM saved")
        if table == "chunks":
            connection.execute("CREATE INDEX idx_chunks_path ON chunks(path)")
        connection.commit()
    original = sqlite3.connect
    def read_only(database, *args, **kwargs):
        assert "mode=ro" in str(database)
        return original(database, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid constraints reached preparation")
    monkeypatch.setattr(sqlite3, "connect", read_only)
    monkeypatch.setattr(repository, "_prepare_connection", forbidden)
    assert_rejected_untouched(repository)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_invalid_constraints_preserve_sidecars_and_no_binding_init(tmp_path, suffix):
    path = tmp_path / "index.sqlite3"
    legacy(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE saved AS SELECT * FROM chunks; DROP TABLE chunks; "
            "ALTER TABLE saved RENAME TO chunks; CREATE INDEX idx_chunks_path ON chunks(path);"
        )
    path.with_name(path.name + suffix).write_bytes(b"unowned sidecar")
    repository = bound(tmp_path, path=path)
    before = snapshot(path)
    for action in (repository.validate_expected_binding, lambda: repository.prepare_index("new"),
                   lambda: repository.reset_index("new", "uninitialized")):
        with pytest.raises(IndexBindingUnavailableError):
            action()
        assert snapshot(path) == before


@pytest.mark.parametrize("method", ["load_chunks_read_only", "load_chunks", "get_metadata", "read_status"])
@pytest.mark.parametrize("journal", ["delete", "wal"])
@pytest.mark.parametrize("interrupt", [False, True])
def test_bound_authoritative_reads_pin_namespace_snapshot(
    tmp_path, monkeypatch, method, journal, interrupt,
):
    from concurrent.futures import ThreadPoolExecutor

    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    original = sqlite3.connect
    connections, blocked = [], []
    with ThreadPoolExecutor(max_workers=1) as executor, closing(
        original(repository.db_path, timeout=0, check_same_thread=False),
    ) as writer:
        assert writer.execute(f"PRAGMA journal_mode={journal}").fetchone() == (journal,)
        def write():
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE meta SET value='other' WHERE key=?", (BINDING_KEYS[1],))
            writer.execute("UPDATE meta SET value='foreign' WHERE key='index_signature'")
            writer.execute("UPDATE chunks SET content='foreign namespace'")
            try:
                writer.commit()
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
                blocked.append(True)
            else:
                blocked.append(False)
        # The connection hook is a deterministic barrier AFTER binding validation
        # and BEFORE payload materialization. WAL commits; rollback mode must wait.
        class Connection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                payload = (sql.startswith("SELECT path, chunk_index")
                           or (sql == "SELECT value FROM meta WHERE key=?" and parameters == ("index_signature",))
                           or sql.startswith("SELECT key, value FROM meta "))
                if payload:
                    executor.submit(write).result(timeout=10)
                    if interrupt:
                        self.interrupt()
                        raise sqlite3.OperationalError("interrupted")
                return super().execute(sql, parameters)
        def connect(database, *args, **kwargs):
            # Immutable screening is rejection-only; only locked connections
            # receive the payload barrier.
            if "immutable=1" in str(database):
                return original(database, *args, **kwargs)
            assert str(database).endswith("?mode=ro")
            connection = original(database, *args, **kwargs, factory=Connection)
            connections.append(connection)
            return connection
        monkeypatch.setattr(sqlite3, "connect", connect)
        action = getattr(repository, method)
        if method == "get_metadata":
            def action():
                return repository.get_metadata("index_signature")
        if interrupt and method != "read_status":
            with pytest.raises(IndexBindingUnavailableError):
                action()
        else:
            result = action()
            if interrupt:
                assert result.storage_error
            elif method == "get_metadata":
                assert result == "sig"
            elif method == "read_status":
                assert result.index_signature == "sig" and result.semantic_chunks == 1
            else:
                assert [chunk.content for chunk in result] == ["private"]
        assert blocked == [journal == "delete"]
        writer.commit()  # All exit paths must release the read lock.
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    monkeypatch.setattr(sqlite3, "connect", original)
    with pytest.raises(IndexBindingUnavailableError):
        repository.load_chunks_read_only()


def test_valid_bound_schema_cascades_note_deletion(tmp_path):
    repository = bound(tmp_path)
    repository.prepare_index("sig")
    populate(repository)
    with repository.transaction() as session:
        session.delete_note("a.md")
    assert repository.load_chunks_read_only() == []

