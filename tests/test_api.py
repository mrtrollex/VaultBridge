import hmac
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.semantic_search import SemanticResult, SemanticSearchService
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
    previous_api_key: str = "",
    max_note_bytes: int = 1_000_000,
    embedder=None,
    semantic_indexer=None,
) -> TestClient:
    settings = Settings(
        api_key=api_key,
        previous_api_key=previous_api_key,
        vault_path=tmp_path,
        max_note_bytes=max_note_bytes,
    )
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


def replace_with_symlink_or_skip(link: Path, target: Path) -> None:
    link.unlink()
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


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
    assert response.json() == {"detail": "Invalid API key"}


@pytest.mark.parametrize("path", ["/notes/list", "/api/v1/notes/list"])
@pytest.mark.parametrize("api_key", ["test-secret", "test-previous-secret"])
def test_current_and_previous_api_keys_authenticate_legacy_and_v1_routes(
    tmp_path,
    path,
    api_key,
):
    client = client_for(tmp_path, previous_api_key="test-previous-secret")

    response = client.get(path, headers={"Authorization": f"Bearer {api_key}"})

    assert response.status_code == 200


def test_authentication_checks_both_configured_keys_with_constant_time_comparison(
    tmp_path,
    monkeypatch,
):
    comparisons = []
    compare_digest = hmac.compare_digest

    def record_comparison(presented, expected):
        comparisons.append((presented, expected))
        return compare_digest(presented, expected)

    monkeypatch.setattr("app.api.dependencies.hmac.compare_digest", record_comparison)
    client = client_for(tmp_path, previous_api_key="test-previous-secret")

    response = client.get("/notes/list", headers=auth())

    assert response.status_code == 200
    assert comparisons == [
        (b"Bearer test-secret", b"Bearer test-secret"),
        (b"Bearer test-secret", b"Bearer test-previous-secret"),
    ]


def test_previous_api_key_is_rejected_when_not_configured(tmp_path):
    client = client_for(tmp_path)

    response = client.get(
        "/api/v1/notes/list",
        headers={"Authorization": "Bearer test-previous-secret"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"Authorization": "Basic test-secret"},
        {"Authorization": "Bearer"},
        {"Authorization": "bearer test-secret"},
        {"Authorization": "Bearer invalid-key"},
    ],
)
def test_missing_malformed_and_invalid_authorization_are_rejected(tmp_path, headers):
    client = client_for(tmp_path, previous_api_key="test-previous-secret")

    response = client.get("/api/v1/notes/list", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


def test_missing_api_key_is_rejected(tmp_path):
    client = client_for(tmp_path, api_key="")
    response = client.get("/notes/list")
    assert response.status_code == 500
    assert response.json() == {"detail": "Server API_KEY is not configured"}


def test_previous_api_key_cannot_replace_missing_current_key(tmp_path):
    client = client_for(tmp_path, api_key="", previous_api_key="test-previous-secret")

    response = client.get(
        "/api/v1/notes/list",
        headers={"Authorization": "Bearer test-previous-secret"},
    )

    assert response.status_code == 500
    assert response.json() == {"detail": "Server API_KEY is not configured"}


@pytest.mark.parametrize(
    ("path", "expected_status"),
    [
        ("/health", 200),
        ("/health/live", 200),
        ("/health/ready", 503),
    ],
)
def test_public_health_routes_remain_unauthenticated_during_key_rotation(
    tmp_path,
    path,
    expected_status,
):
    client = client_for(tmp_path, api_key="", previous_api_key="test-previous-secret")

    response = client.get(path)

    assert response.status_code == expected_status


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
    assert Path(path) == Path("Technical Notes") / "Example Project" / "APEX limit validation.md"

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
    oracle_apex = tmp_path / "Technical Notes"
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
    (tmp_path / "Technical Notes").mkdir()
    (tmp_path / "Infrastructure Notes" / "NAS.md").write_text("TrueNAS storage backup server", encoding="utf-8")
    (tmp_path / "Technical Notes" / "Database.md").write_text("Oracle APEX database ORDS", encoding="utf-8")
    client = client_for(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "server storage", "folder": "Technical Notes", "min_score": 0.0},
        )
        assert response.status_code == 200
        assert [item["path"] for item in response.json()["results"]] == ["Technical Notes/Database.md"]


def test_related_notes_verify_live_paths_backfill_limit_and_preserve_legacy_v1_contract(
    tmp_path,
    monkeypatch,
):
    scoped = tmp_path / "Scoped"
    other = tmp_path / "Other"
    scoped.mkdir()
    other.mkdir()
    for name in ("B.md", "C.md", "D.md"):
        (scoped / name).write_text(f"Live {name}.", encoding="utf-8")
    (other / "Outside.md").write_text("Outside folder.", encoding="utf-8")
    client = client_for(tmp_path, semantic_indexer=RecordingIndexer())
    candidates = [
        SemanticResult("Scoped/Stale.md", "Stale", 0.99, 0.99, 0.90, "stale", "Old"),
        SemanticResult("Scoped/B.md", "Indexed B", 0.90, 0.91, 0.80, "snippet B", "Heading B"),
        SemanticResult("Scoped/C.md", "Indexed C", 0.80, 0.81, 0.70, "snippet C", None),
        SemanticResult("Scoped/D.md", "Indexed D", 0.70, 0.71, 0.60, "snippet D", "Heading D"),
        SemanticResult("Other/Outside.md", "Outside", 0.60, 0.61, 0.50, "outside", None),
    ]
    search_calls = []

    def fake_search(text, *, folder, limit, min_score):
        search_calls.append((text, folder, limit, min_score))
        return candidates[:limit]

    monkeypatch.setattr(client.app.state.semantic_search_service, "search", fake_search)
    payload = {"text": "related concept", "folder": "Scoped", "limit": 3, "min_score": 0.1}

    legacy = client.post("/notes/related", headers=auth(), json=payload)
    versioned = client.post("/api/v1/notes/related", headers=auth(), json=payload)
    maximum_limit = client.post(
        "/api/v1/notes/related",
        headers=auth(),
        json={**payload, "limit": 20},
    )

    assert legacy.status_code == versioned.status_code == 200
    assert maximum_limit.status_code == 200
    assert legacy.json() == versioned.json()
    assert [item["path"] for item in legacy.json()["results"]] == [
        "Scoped/B.md",
        "Scoped/C.md",
        "Scoped/D.md",
    ]
    assert legacy.json()["results"][0] == {
        "path": "Scoped/B.md",
        "title": "B",
        "score": 0.90,
        "semantic_score": 0.91,
        "lexical_score": 0.80,
        "snippet": "snippet B",
        "heading": "Heading B",
    }
    assert search_calls == [
        ("related concept", "Scoped", 9, 0.1),
        ("related concept", "Scoped", 9, 0.1),
        ("related concept", "Scoped", 50, 0.1),
    ]


def test_related_notes_filter_deleted_stale_index_without_mutating_semantic_storage(tmp_path):
    stale = tmp_path / "TrueNAS backup server.md"
    live = tmp_path / "Storage recovery.md"
    stale.write_text("TrueNAS NAS storage backup server recovery.", encoding="utf-8")
    live.write_text("TrueNAS NAS storage backup server recovery guide.", encoding="utf-8")
    client = client_for(tmp_path)
    query = "TrueNAS storage backup server"

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        service = client.app.state.semantic_search_service
        indexed_paths = [
            result.path for result in service.search(query, limit=10, min_score=0.0)
        ]
        stored_before = service.repository.load_chunks()
        assert stale.name in indexed_paths
        stale.unlink()

        response = client.post(
            "/api/v1/notes/related",
            headers=auth(),
            json={"text": query, "limit": 5, "min_score": 0.0},
        )

        assert response.status_code == 200
        returned_paths = [item["path"] for item in response.json()["results"]]
        assert stale.name not in returned_paths
        assert live.name in returned_paths
        assert service.repository.load_chunks() == stored_before
        assert stale.name in {chunk.path for chunk in stored_before}


def test_related_notes_filter_candidate_replaced_by_directory(tmp_path):
    replaced = tmp_path / "Directory.md"
    live = tmp_path / "Live.md"
    replaced.write_text("TrueNAS storage backup server.", encoding="utf-8")
    live.write_text("TrueNAS storage backup server live.", encoding="utf-8")
    client = client_for(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        replaced.unlink()
        replaced.mkdir()

        response = client.post(
            "/notes/related",
            headers=auth(),
            json={"text": "TrueNAS storage backup server", "limit": 5, "min_score": 0.0},
        )

    assert response.status_code == 200
    assert [item["path"] for item in response.json()["results"]] == [live.name]


def test_related_notes_filter_broken_non_markdown_and_external_symlink_candidates(tmp_path):
    candidate_names = ("Broken.md", "Non-Markdown.md", "External.md", "Live.md")
    for name in candidate_names:
        (tmp_path / name).write_text("TrueNAS storage backup server.", encoding="utf-8")
    client = client_for(tmp_path)

    with client:
        client.app.state.semantic_indexer.wait(timeout=2)
        internal_text = tmp_path / "replacement.txt"
        internal_text.write_text("Not Markdown.", encoding="utf-8")
        external = tmp_path.parent / f"{tmp_path.name}-external-related-note.md"
        external.write_text("External Markdown.", encoding="utf-8")
        replace_with_symlink_or_skip(tmp_path / "Broken.md", tmp_path / "missing.md")
        replace_with_symlink_or_skip(tmp_path / "Non-Markdown.md", internal_text)
        replace_with_symlink_or_skip(tmp_path / "External.md", external)

        response = client.post(
            "/api/v1/notes/related",
            headers=auth(),
            json={"text": "TrueNAS storage backup server", "limit": 10, "min_score": 0.0},
        )

    assert response.status_code == 200
    assert [item["path"] for item in response.json()["results"]] == ["Live.md"]


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
