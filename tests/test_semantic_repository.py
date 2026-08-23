import sqlite3

import numpy as np

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

    with repository.transaction(INDEX_SIGNATURE) as session:
        session.replace_note(note, [chunk])

    reopened = SemanticRepository(db_path)
    with reopened.transaction(INDEX_SIGNATURE) as session:
        assert session.load_notes() == {note.path: note}
    assert reopened.load_chunks(INDEX_SIGNATURE) == [chunk]
    assert reopened.get_metadata("index_signature") == INDEX_SIGNATURE
    assert reopened.is_initialized() is True


def test_repository_deletes_stale_note_and_cascades_chunks(tmp_path):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")
    note = stored_note()

    with repository.transaction(INDEX_SIGNATURE) as session:
        session.replace_note(note, [stored_chunk()])
    with repository.transaction(INDEX_SIGNATURE) as session:
        _, removed = session.load_notes_and_remove_stale(set())
        assert removed == 1

    assert repository.load_chunks(INDEX_SIGNATURE) == []
    assert repository.is_initialized() is False


def test_repository_invalidates_data_when_index_signature_changes(tmp_path):
    repository = SemanticRepository(tmp_path / "semantic-index.sqlite3")

    with repository.transaction(INDEX_SIGNATURE) as session:
        session.replace_note(stored_note(), [stored_chunk()])

    assert repository.load_chunks("v1|different/model|300|50") == []
    with repository.transaction("v1|different/model|300|50") as session:
        assert session.load_notes() == {}


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

    assert repository.load_chunks(INDEX_SIGNATURE) == [chunk]
    assert repository.get_metadata("index_signature") == INDEX_SIGNATURE
