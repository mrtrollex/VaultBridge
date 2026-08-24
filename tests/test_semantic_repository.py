import sqlite3
import threading

import numpy as np
import pytest

from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote

INDEX_SIGNATURE = "v1|example/model|300|50"


def stored_note(path: str = "Folder/note.md") -> StoredNote:
    return StoredNote(
        path=path,
        mtime_ns=123,
        size=42,
        sha256="abc123",
        indexed_at="2026-08-23T10:00:00+00:00",
    )


def stored_chunk(path: str = "Folder/note.md") -> StoredChunk:
    vector = np.array([0.6, 0.8], dtype=np.float32)
    return StoredChunk(
        path=path,
        chunk_index=0,
        heading="Heading",
        content="# Heading\n\nIndexed content.",
        embedding=vector.tobytes(),
        dimensions=2,
    )


def test_repository_persists_existing_schema_and_index_metadata(tmp_path):
    db_path = tmp_path / "semantic-index.sqlite3"
    repository = SemanticRepository(db_path)
    note = stored_note()
    chunk = stored_chunk()

    assert repository.storage_initialized() is False
    repository.prepare_index(INDEX_SIGNATURE)
    with repository.transaction() as session:
        session.replace_note(note, [chunk])

    reopened = SemanticRepository(db_path)
    reopened.prepare_index(INDEX_SIGNATURE)
    with reopened.transaction() as session:
        assert session.load_notes() == {note.path: note}
    assert reopened.load_chunks() == [chunk]
    assert reopened.get_metadata("index_signature") == INDEX_SIGNATURE
    assert reopened.storage_initialized() is True


def test_repository_status_is_read_only_and_reports_cheap_counts(tmp_path):
    db_path = tmp_path / "semantic-index.sqlite3"
    repository = SemanticRepository(db_path)

    missing = repository.read_status()
    assert missing.storage_initialized is False
    assert missing.storage_error is False
    assert missing.indexed_notes == 0
    assert missing.semantic_chunks == 0
    assert db_path.exists() is False

    repository.prepare_index(INDEX_SIGNATURE)
    repository.set_metadata("index_state", "ready")
    repository.set_metadata("last_successful_sync", "2026-08-23T12:00:00+00:00")
    with repository.transaction() as session:
        session.replace_note(stored_note(), [stored_chunk()])

    status = repository.read_status()
    assert status.storage_initialized is True
    assert status.storage_error is False
    assert status.index_signature == INDEX_SIGNATURE
    assert status.index_state == "ready"
    assert status.indexed_notes == 1
    assert status.semantic_chunks == 1
    assert status.last_successful_sync == "2026-08-23T12:00:00+00:00"

    availability = repository.read_availability_status()
    assert availability.storage_initialized is True
    assert availability.storage_error is False
    assert availability.index_signature == INDEX_SIGNATURE
    assert availability.index_state == "ready"
    assert availability.has_chunks is True


@pytest.mark.parametrize(
    "error",
    [PermissionError("semantic path denied"), OSError("semantic path unavailable")],
)
def test_repository_availability_handles_expected_path_errors(tmp_path, monkeypatch, error):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")

    def unavailable(_path):
        raise error

    monkeypatch.setattr(type(repository.db_path), "exists", unavailable)

    status = repository.read_availability_status()

    assert status.storage_initialized is False
    assert status.storage_error is True
    assert status.has_chunks is False


def test_repository_availability_does_not_hide_sqlite_programming_errors(tmp_path, monkeypatch):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")
    repository.db_path.touch()

    def fail_to_connect(*_args, **_kwargs):
        raise sqlite3.ProgrammingError("invalid readiness query")

    monkeypatch.setattr(sqlite3, "connect", fail_to_connect)

    with pytest.raises(sqlite3.ProgrammingError, match="invalid readiness query"):
        repository.read_availability_status()


def test_repository_status_uses_one_snapshot_when_writer_commits(tmp_path, monkeypatch):
    db_path = tmp_path / "semantic-index.sqlite3"
    repository = SemanticRepository(db_path)
    repository.prepare_index(INDEX_SIGNATURE)
    repository.set_metadata("index_state", "ready")
    with repository.transaction() as session:
        session.replace_note(stored_note("first.md"), [stored_chunk("first.md")])

    original_connect = sqlite3.connect
    writer = original_connect(db_path, timeout=30)
    writer.execute("PRAGMA foreign_keys=ON")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE meta SET value='indexing' WHERE key='index_state'")
    second_note = stored_note("second.md")
    second_chunk = stored_chunk("second.md")
    writer.execute(
        "INSERT INTO notes(path, mtime_ns, size, sha256, indexed_at) VALUES(?, ?, ?, ?, ?)",
        (
            second_note.path,
            second_note.mtime_ns,
            second_note.size,
            second_note.sha256,
            second_note.indexed_at,
        ),
    )
    writer.execute(
        "INSERT INTO chunks(path, chunk_index, heading, content, embedding, dimensions) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (
            second_chunk.path,
            second_chunk.chunk_index,
            second_chunk.heading,
            second_chunk.content,
            second_chunk.embedding,
            second_chunk.dimensions,
        ),
    )

    metadata_read = threading.Event()
    allow_counts = threading.Event()

    class CoordinatedConnection:
        def __init__(self, connection):
            object.__setattr__(self, "_connection", connection)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __setattr__(self, name, value):
            setattr(self._connection, name, value)

        def execute(self, sql, *args, **kwargs):
            if sql == "SELECT COUNT(*) FROM notes":
                metadata_read.set()
                if not allow_counts.wait(timeout=5):
                    raise RuntimeError("status count query was not released")
            return self._connection.execute(sql, *args, **kwargs)

    def coordinated_connect(*args, **kwargs):
        return CoordinatedConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr("app.repositories.semantic.sqlite3.connect", coordinated_connect)
    statuses = []
    errors = []

    def read_status():
        try:
            statuses.append(repository.read_status())
        except BaseException as exc:
            errors.append(exc)

    reader = threading.Thread(target=read_status)
    reader.start()
    try:
        assert metadata_read.wait(timeout=2)
        writer.commit()
        allow_counts.set()
        reader.join(timeout=2)
    finally:
        allow_counts.set()
        writer.rollback()
        writer.close()
        reader.join(timeout=2)

    assert reader.is_alive() is False
    assert errors == []
    assert len(statuses) == 1
    assert statuses[0].index_state == "ready"
    assert statuses[0].indexed_notes == 1
    assert statuses[0].semantic_chunks == 1

    committed = repository.read_status()
    assert committed.index_state == "indexing"
    assert committed.indexed_notes == 2
    assert committed.semantic_chunks == 2


def test_repository_deletes_stale_note_and_cascades_chunks(tmp_path):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")
    note = stored_note()

    repository.prepare_index(INDEX_SIGNATURE)
    with repository.transaction() as session:
        session.replace_note(note, [stored_chunk()])
    with repository.transaction() as session:
        _, removed = session.load_notes_and_remove_stale(set())
        assert removed == 1

    assert repository.load_chunks() == []
    assert repository.storage_initialized() is True


def test_repository_invalidates_data_when_index_signature_changes(tmp_path):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")

    repository.prepare_index(INDEX_SIGNATURE)
    repository.set_metadata("last_successful_sync", "2026-08-23T12:00:00+00:00")
    with repository.transaction() as session:
        session.replace_note(stored_note(), [stored_chunk()])

    status = repository.prepare_index("v1|different/model|300|50")
    assert status.signature_changed is True
    assert repository.load_chunks() == []
    with repository.transaction() as session:
        assert session.load_notes() == {}
    assert repository.get_metadata("last_successful_sync") is None


def test_repository_reads_pre_extraction_sqlite_index_without_rebuild(tmp_path):
    db_path = tmp_path / "semantic-index.sqlite3"
    note = stored_note()
    chunk = stored_chunk()
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE notes (
                path TEXT PRIMARY KEY,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                FOREIGN KEY(path) REFERENCES notes(path) ON DELETE CASCADE
            );
            CREATE INDEX idx_chunks_path ON chunks(path);
            """
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES('index_signature', ?)",
            (INDEX_SIGNATURE,),
        )
        connection.execute(
            "INSERT INTO notes(path, mtime_ns, size, sha256, indexed_at) VALUES(?, ?, ?, ?, ?)",
            (note.path, note.mtime_ns, note.size, note.sha256, note.indexed_at),
        )
        connection.execute(
            "INSERT INTO chunks(path, chunk_index, heading, content, embedding, dimensions) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (
                chunk.path,
                chunk.chunk_index,
                chunk.heading,
                chunk.content,
                chunk.embedding,
                chunk.dimensions,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    repository = SemanticRepository(db_path)

    assert repository.prepare_index(INDEX_SIGNATURE).signature_changed is False
    assert repository.load_chunks() == [chunk]
    assert repository.get_metadata("index_signature") == INDEX_SIGNATURE
