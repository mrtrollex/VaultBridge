from __future__ import annotations

from tests.test_api import auth, client_for


def test_note_links_preserve_occurrences_metadata_and_resolution(tmp_path):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Source.md").write_text(
        "[[Target#Section|Display]] [[Missing]] [[Target#Section|Display]]",
        encoding="utf-8",
    )
    client = client_for(tmp_path)

    response = client.get(
        "/api/v1/notes/links",
        params={"path": "Source.md"},
        headers=auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "links": [
            {
                "target": "Target",
                "heading": "Section",
                "alias": "Display",
                "state": "resolved",
                "resolved_path": "Target.md",
            },
            {
                "target": "Missing",
                "heading": None,
                "alias": None,
                "state": "unresolved",
                "resolved_path": None,
            },
            {
                "target": "Target",
                "heading": "Section",
                "alias": "Display",
                "state": "resolved",
                "resolved_path": "Target.md",
            },
        ]
    }


def test_note_backlinks_preserve_deterministic_distinct_metadata_and_empty_result(tmp_path):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "A.md").write_text(
        "[[Target#One|First]] [[Target#One|First]] [[Target#Two]]",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text("[[Target]]", encoding="utf-8")
    (tmp_path / "Empty.md").write_text("empty", encoding="utf-8")
    client = client_for(tmp_path)

    response = client.get(
        "/api/v1/notes/backlinks",
        params={"path": "Target.md"},
        headers=auth(),
    )
    empty = client.get(
        "/api/v1/notes/backlinks",
        params={"path": "Empty.md"},
        headers=auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "backlinks": [
            {"source_path": "A.md", "target": "Target", "heading": "One", "alias": "First"},
            {"source_path": "A.md", "target": "Target", "heading": "Two", "alias": None},
            {"source_path": "b.md", "target": "Target", "heading": None, "alias": None},
        ]
    }
    assert empty.json() == {"backlinks": []}


def test_relationship_routes_use_existing_validation_auth_and_error_mapping(tmp_path):
    client = client_for(tmp_path)

    for route in ("links", "backlinks"):
        path = f"/api/v1/notes/{route}"
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get(path, headers=auth()).status_code == 422
        assert client.get(path, params={"path": "../Secret.md"}, headers=auth()).status_code == 400
        assert client.get(path, params={"path": "Missing.md"}, headers=auth()).status_code == 404
        assert client.get(path, params={"path": "Note.txt"}, headers=auth()).status_code == 400


def test_relationship_routes_share_protected_peer_rate_limit(tmp_path):
    (tmp_path / "Note.md").write_text("note", encoding="utf-8")
    client = client_for(tmp_path, rate_limit_requests=1)

    first = client.get(
        "/api/v1/notes/links",
        params={"path": "Note.md"},
        headers=auth(),
    )
    limited = client.get(
        "/api/v1/notes/backlinks",
        params={"path": "Note.md"},
        headers=auth(),
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Rate limit exceeded"}


def test_relationship_openapi_contract_is_versioned_only_and_unique():
    from app.main import app

    schema = app.openapi()
    assert schema["paths"]["/api/v1/notes/links"]["get"]["operationId"] == "listNoteLinksV1"
    assert schema["paths"]["/api/v1/notes/backlinks"]["get"]["operationId"] == (
        "listNoteBacklinksV1"
    )
    assert "/notes/links" not in schema["paths"]
    assert "/notes/backlinks" not in schema["paths"]
    operation_ids = [
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
    ]
    assert len(operation_ids) == len(set(operation_ids))
