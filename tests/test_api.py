import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import SemanticSearchService
from app.services.vault import VaultService


class FakeEmbedder:
    def embed(self, texts):
        vectors = []
        for text in texts:
            value = text.casefold()
            vectors.append(
                np.array(
                    [
                        sum(word in value for word in ("truenas", "nas", "storage", "backup", "server")),
                        sum(word in value for word in ("oracle", "apex", "pl/sql", "database", "ords")),
                        sum(word in value for word in ("music", "audio", "navidrome", "song")),
                        0.1,
                    ],
                    dtype=np.float32,
                )
            )
        return vectors


def client_for(
    tmp_path: Path,
    *,
    api_key: str = "test-secret",
    max_note_bytes: int = 1_000_000,
    embedder=None,
    semantic_indexer=None,
) -> TestClient:
    settings = Settings(api_key=api_key, vault_path=tmp_path, max_note_bytes=max_note_bytes)
    vault_service = VaultService(
        vault_root=settings.vault_path,
        max_note_bytes=settings.max_note_bytes,
    )
    semantic_search_service = SemanticSearchService(
        vault_root=settings.vault_path,
        repository=SemanticRepository(tmp_path / ".test-semantic" / "index.sqlite3"),
        max_note_bytes=settings.max_note_bytes,
        chunk_chars=300,
        chunk_overlap=50,
        embedder=embedder or FakeEmbedder(),
    )
    app = main.create_app(
        settings=settings,
        vault_service=vault_service,
        semantic_search_service=semantic_search_service,
        semantic_indexer=semantic_indexer,
    )
    return TestClient(app)


def auth():
    return {"Authorization": "Bearer test-secret"}


class RecordingIndexer:
    def __init__(self):
        self.paths = []

    def enqueue(self, path):
        self.paths.append(path)
        return True


class RaisingIndexer(RecordingIndexer):
    def enqueue(self, path):
        self.paths.append(path)
        raise RuntimeError("semantic submission failed")


def test_application_metadata():
    assert main.app.title == "VaultBridge"
    assert main.app.version == "1.0.0"
    assert main.app.description == "Self-hosted REST and semantic search API for an Obsidian vault."
    assert main.app.docs_url is None
    assert main.app.redoc_url is None
    assert main.app.openapi_url is None


def test_router_registration_preserves_public_contract():
    expected = {
        ("GET", "/health", "healthCheck"),
        ("GET", "/health/live", "livenessCheck"),
        ("GET", "/health/ready", "readinessCheck"),
        ("POST", "/notes", "createNote"),
        ("POST", "/notes/append", "appendNote"),
        ("GET", "/notes/read", "readNote"),
        ("POST", "/notes/search", "searchNotes"),
        ("POST", "/notes/related", "findRelatedNotes"),
        ("GET", "/notes/list", "listNotes"),
        ("POST", "/api/v1/notes", "createNoteV1"),
        ("POST", "/api/v1/notes/append", "appendNoteV1"),
        ("GET", "/api/v1/notes/read", "readNoteV1"),
        ("POST", "/api/v1/notes/search", "searchNotesV1"),
        ("POST", "/api/v1/notes/related", "findRelatedNotesV1"),
        ("GET", "/api/v1/notes/list", "listNotesV1"),
    }
    schema = main.app.openapi()
    actual = {
        (method.upper(), path, operation["operationId"])
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
    }

    assert actual == expected


def test_auth_required(tmp_path):
    client = client_for(tmp_path)
    response = client.get("/notes/list")
    assert response.status_code == 401


def test_missing_api_key_is_rejected(tmp_path):
    client = client_for(tmp_path, api_key="")
    response = client.get("/notes/list")
    assert response.status_code == 500
    assert response.json() == {"detail": "Server API_KEY is not configured"}


def test_vault_errors_keep_existing_http_contract(tmp_path):
    client = client_for(tmp_path)

    response = client.get("/notes/read", headers=auth(), params={"path": "Missing.md"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found"}

    response = client.get("/notes/read", headers=auth(), params={"path": "Note.txt"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Only .md files are allowed"}

    payload = {"title": "Conflict", "folder": "Inbox", "content": "First", "tags": []}
    assert client.post("/notes", headers=auth(), json=payload).status_code == 200
    response = client.post("/notes", headers=auth(), json={**payload, "content": "Second"})
    assert response.status_code == 409
    assert response.json() == {
        "detail": "A note with this title already exists. Use appendNote or choose another title."
    }

    small_client = client_for(tmp_path / "small", max_note_bytes=60)
    response = small_client.post(
        "/notes",
        headers=auth(),
        json={"title": "Large", "folder": "Inbox", "content": "content", "tags": []},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Generated note is too large"}


def test_create_note_enqueues_only_a_created_note(tmp_path):
    indexer = RecordingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)
    payload = {"title": "Queued", "folder": "Inbox", "content": "Body", "tags": []}

    response = client.post("/notes", headers=auth(), json=payload)
    duplicate = client.post("/notes", headers=auth(), json=payload)

    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "unchanged"
    assert indexer.paths == [response.json()["path"]]


def test_append_note_enqueues_only_a_modified_note(tmp_path):
    indexer = RecordingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)
    created = client.post(
        "/notes",
        headers=auth(),
        json={"title": "Append queue", "folder": "Inbox", "content": "Body", "tags": []},
    )
    path = created.json()["path"]
    indexer.paths.clear()
    payload = {"path": path, "content": "Addition", "dedupe_key": "same-write"}

    appended = client.post("/notes/append", headers=auth(), json=payload)
    duplicate = client.post("/notes/append", headers=auth(), json=payload)

    assert appended.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_applied"
    assert indexer.paths == [path]


def test_failed_note_writes_do_not_enqueue_semantic_work(tmp_path):
    indexer = RecordingIndexer()
    client = client_for(tmp_path, max_note_bytes=100, semantic_indexer=indexer)

    missing = client.post(
        "/notes/append",
        headers=auth(),
        json={"path": "Missing.md", "content": "Addition"},
    )
    oversized = client.post(
        "/notes",
        headers=auth(),
        json={"title": "Large", "folder": "Inbox", "content": "x" * 100, "tags": []},
    )

    assert missing.status_code == 404
    assert oversized.status_code == 413
    assert indexer.paths == []


def test_create_note_remains_successful_when_enqueue_raises(tmp_path):
    indexer = RaisingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)

    response = client.post(
        "/notes",
        headers=auth(),
        json={"title": "Durable create", "folder": "Inbox", "content": "Unique body", "tags": []},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "created"
    note = tmp_path / "Inbox" / "Durable create.md"
    assert note.read_text(encoding="utf-8").count("Unique body") == 1
    assert indexer.paths == [response.json()["path"]]


def test_append_note_remains_successful_when_enqueue_raises(tmp_path):
    note = tmp_path / "Append target.md"
    note.write_text("Start", encoding="utf-8")
    indexer = RaisingIndexer()
    client = client_for(tmp_path, semantic_indexer=indexer)

    response = client.post(
        "/notes/append",
        headers=auth(),
        json={"path": "Append target.md", "content": "One committed addition."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "appended"
    assert note.read_text(encoding="utf-8").count("One committed addition.") == 1
    assert indexer.paths == [response.json()["path"]]


def test_create_read_search_append(tmp_path):
    client = client_for(tmp_path)

    response = client.post(
        "/notes",
        headers=auth(),
        json={
            "title": "APEX limit validation",
            "folder": "Technical Notes/Example Project",
            "content": "Use NVL for limit_qty.",
            "tags": ["oracle-apex", "example-project"],
        },
    )
    assert response.status_code == 200
    path = response.json()["path"]
    assert Path(path) == Path("Oracle APEX") / "Example Project" / "APEX limit validation.md"

    response = client.get("/notes/read", headers=auth(), params={"path": path})
    assert response.status_code == 200
    assert "Use NVL" in response.json()["content"]

    response = client.post("/notes/search", headers=auth(), json={"query": "limit_qty"})
    assert response.status_code == 200
    assert response.json()["results"][0]["path"] == path

    payload = {"path": path, "content": "More detail.", "dedupe_key": "abc123"}
    response = client.post("/notes/append", headers=auth(), json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "appended"

    response = client.post("/notes/append", headers=auth(), json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "already_applied"


def test_targeted_reindex_updates_semantic_results_after_append(tmp_path):
    home_server = tmp_path / "Infrastructure Notes"
    oracle_apex = tmp_path / "Oracle APEX"
    home_server.mkdir()
    oracle_apex.mkdir()
    (home_server / "TrueNAS backup strategy.md").write_text(
        "My NAS stores backups and Docker application data on mirrored storage.",
        encoding="utf-8",
    )
    oracle_note = oracle_apex / "Oracle APEX REST API.md"
    oracle_note.write_text(
        "ORDS exposes REST endpoints backed by the Oracle database.",
        encoding="utf-8",
    )
    client = client_for(tmp_path)

    with client:
        assert client.app.state.semantic_indexer.wait(timeout=2) == {
            "indexed": 2,
            "unchanged": 0,
            "removed": 0,
        }
        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "network storage and server backups", "limit": 5, "min_score": 0.0},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["path"] == "Infrastructure Notes/TrueNAS backup strategy.md"

        response = client.post(
            "/notes/append",
            headers=auth(),
            json={"path": "Technical Notes/Oracle APEX REST API.md", "content": "Navidrome audio music note."},
        )
        assert response.status_code == 200
        assert client.app.state.semantic_indexer.wait(timeout=2) == {
            "indexed": 1,
            "unchanged": 0,
            "removed": 0,
        }

        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "music audio library", "limit": 1, "min_score": 0.5},
        )
        assert response.status_code == 200
        assert response.json()["results"][0]["path"] == "Technical Notes/Oracle APEX REST API.md"


def test_note_write_response_does_not_wait_for_targeted_embedding(tmp_path):
    class BlockingWriteEmbedder(FakeEmbedder):
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def embed(self, texts):
            if any("blocking semantic marker" in text for text in texts):
                self.started.set()
                if not self.release.wait(timeout=5):
                    raise RuntimeError("test targeted embedding was not released")
            return super().embed(texts)

    embedder = BlockingWriteEmbedder()
    client = client_for(tmp_path, embedder=embedder)
    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        response = client.post(
            "/notes",
            headers=auth(),
            json={
                "title": "Non blocking",
                "folder": "Inbox",
                "content": "blocking semantic marker",
                "tags": [],
            },
        )
        try:
            assert response.status_code == 200
            assert embedder.started.wait(timeout=2)
            assert client.app.state.semantic_indexer.is_running is True
        finally:
            embedder.release.set()
        client.app.state.semantic_indexer.wait(timeout=2)


def test_related_notes_folder_filter(tmp_path):
    (tmp_path / "Infrastructure Notes").mkdir()
    (tmp_path / "Oracle APEX").mkdir()
    (tmp_path / "Infrastructure Notes" / "NAS.md").write_text("TrueNAS storage backup server", encoding="utf-8")
    (tmp_path / "Oracle APEX" / "Database.md").write_text("Oracle APEX database ORDS", encoding="utf-8")
    client = client_for(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "server storage", "folder": "Oracle APEX", "min_score": 0.0},
        )
        assert response.status_code == 200
        assert [item["path"] for item in response.json()["results"]] == ["Technical Notes/Database.md"]


def test_health_reports_semantic_state(tmp_path):
    client = client_for(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "vault_exists": True,
        "semantic_index_ready": False,
        "semantic_index_state": "uninitialized",
        "semantic_search_available": False,
        "semantic_indexer_running": False,
        "full_sync_required": False,
        "indexed_notes": 0,
        "semantic_chunks": 0,
        "vault_notes": 0,
        "last_successful_sync": None,
    }


def test_health_reports_ready_after_successful_empty_vault_index(tmp_path):
    client = client_for(tmp_path)

    with client:
        assert client.app.state.semantic_indexer.wait(timeout=2) == {
            "indexed": 0,
            "unchanged": 0,
            "removed": 0,
        }
        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "nothing indexed yet"},
        )

        assert response.status_code == 200
        assert response.json() == {"text": "nothing indexed yet", "results": []}
        assert client.get("/health").json()["semantic_index_ready"] is True


def test_related_notes_returns_503_after_initial_background_index_failure(tmp_path):
    class FailingEmbedder:
        def embed(self, texts):
            raise RuntimeError("background embedding failure")

    (tmp_path / "note.md").write_text("Semantic content.", encoding="utf-8")
    client = client_for(tmp_path, embedder=FailingEmbedder())

    with client:
        with pytest.raises(RuntimeError, match="background embedding failure"):
            client.app.state.semantic_indexer.wait(timeout=2)
        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "semantic content"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Semantic search unavailable: Semantic index is unavailable"
    }


def test_path_traversal_is_blocked(tmp_path):
    client = client_for(tmp_path)
    response = client.get("/notes/read", headers=auth(), params={"path": "../secret.md"})
    assert response.status_code == 400

    response = client.post(
        "/notes/related",
        headers=auth(),
        json={"text": "anything", "folder": "../"},
    )
    assert response.status_code == 400


def test_legacy_and_v1_literal_routes_block_external_symlink_escape(tmp_path):
    vault = tmp_path / "vault"
    scoped = vault / "Scoped"
    scoped.mkdir(parents=True)
    (scoped / "safe.md").write_text("Contained route marker.", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("Unique external route marker.", encoding="utf-8")
    link = scoped / "external-link.md"
    try:
        link.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    client = client_for(vault, semantic_indexer=RecordingIndexer())
    for prefix in ("", "/api/v1"):
        search = client.post(
            f"{prefix}/notes/search",
            headers=auth(),
            json={"query": "Unique external route marker", "folder": "Scoped"},
        )
        listing = client.get(
            f"{prefix}/notes/list",
            headers=auth(),
            params={"folder": "Scoped"},
        )
        direct_read = client.get(
            f"{prefix}/notes/read",
            headers=auth(),
            params={"path": "Scoped/external-link.md"},
        )

        assert search.status_code == 200
        assert search.json()["results"] == []
        assert listing.status_code == 200
        assert [note["path"] for note in listing.json()["notes"]] == [
            str(Path("Scoped") / "safe.md")
        ]
        assert direct_read.status_code == 400
        assert direct_read.json() == {"detail": "Path escapes the vault"}
        assert str(secret.resolve()) not in search.text
        assert str(secret.resolve()) not in listing.text
        assert str(secret.resolve()) not in direct_read.text
