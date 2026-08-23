from pathlib import Path

import numpy as np
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
        embedder=FakeEmbedder(),
    )
    app = main.create_app(
        settings=settings,
        vault_service=vault_service,
        semantic_search_service=semantic_search_service,
    )
    return TestClient(app)


def auth():
    return {"Authorization": "Bearer test-secret"}


def test_application_metadata():
    assert main.app.title == "VaultBridge"
    assert main.app.version == "0.1.0"
    assert main.app.description == "Self-hosted REST and semantic search API for an Obsidian vault."
    assert main.app.docs_url is None
    assert main.app.redoc_url is None
    assert main.app.openapi_url is None


def test_router_registration_preserves_public_contract():
    expected = {
        ("GET", "/health", "healthCheck"),
        ("POST", "/notes", "createNote"),
        ("POST", "/notes/append", "appendNote"),
        ("GET", "/notes/read", "readNote"),
        ("POST", "/notes/search", "searchNotes"),
        ("POST", "/notes/related", "findRelatedNotes"),
        ("GET", "/notes/list", "listNotes"),
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
    assert path == "Technical Notes/Example Project/APEX limit validation.md"

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


def test_find_related_notes_and_incremental_refresh(tmp_path):
    client = client_for(tmp_path)

    for note in [
        {
            "title": "TrueNAS backup strategy",
            "folder": "Infrastructure Notes",
            "content": "My NAS stores backups and Docker application data on mirrored storage.",
            "tags": ["truenas"],
        },
        {
            "title": "Oracle APEX REST API",
            "folder": "Oracle APEX",
            "content": "ORDS exposes REST endpoints backed by the Oracle database.",
            "tags": ["oracle-apex"],
        },
    ]:
        assert client.post("/notes", headers=auth(), json=note).status_code == 200

    response = client.post(
        "/notes/related",
        headers=auth(),
        json={"text": "network storage and server backups", "limit": 5, "min_score": 0.0},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["path"] == "Infrastructure Notes/TrueNAS backup strategy.md"

    # The next semantic request must notice an appended note and re-index it.
    response = client.post(
        "/notes/append",
        headers=auth(),
        json={"path": "Technical Notes/Oracle APEX REST API.md", "content": "Navidrome audio music note."},
    )
    assert response.status_code == 200

    response = client.post(
        "/notes/related",
        headers=auth(),
        json={"text": "music audio library", "limit": 1, "min_score": 0.0},
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["path"] == "Technical Notes/Oracle APEX REST API.md"


def test_related_notes_folder_filter(tmp_path):
    client = client_for(tmp_path)
    (tmp_path / "Infrastructure Notes").mkdir()
    (tmp_path / "Oracle APEX").mkdir()
    (tmp_path / "Infrastructure Notes" / "NAS.md").write_text("TrueNAS storage backup server", encoding="utf-8")
    (tmp_path / "Oracle APEX" / "Database.md").write_text("Oracle APEX database ORDS", encoding="utf-8")

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
    assert response.json() == {"ok": True, "vault_exists": True, "semantic_index_ready": False}


def test_health_reports_ready_after_successful_empty_vault_index(tmp_path):
    client = client_for(tmp_path)

    response = client.post(
        "/notes/related",
        headers=auth(),
        json={"text": "nothing indexed yet"},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "nothing indexed yet", "results": []}
    assert client.get("/health").json()["semantic_index_ready"] is True


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
