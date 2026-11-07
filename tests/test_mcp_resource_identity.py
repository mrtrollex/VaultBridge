"""Qualified resource locators use the verified identity on both native transports."""

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from app.core.config import Settings
from app.services.knowledge_spaces import QualifiedNoteIdentity, QualifiedPath, SpaceId
from app.services.space_operations import QualifiedNoteRead
from app.services.space_runtime import compose_space_runtime
from app.space_read_transport import parse_resource_uri
from tests.test_mcp_read_input_security import invoke
from tests.test_policy_read_adapters import local_backend as local_backend
from tests.test_policy_read_adapters import runtime as runtime
from tests.test_policy_read_adapters import settings as settings
from tests.test_policy_read_repairs import read_body, rpc

PATHS = [
    ("folder/note.md", "folder%2Fnote.md"),
    ("folder/sub/café #%.md", "folder%2Fsub%2Fcaf%C3%A9%20%23%25.md"),
]


def nested_notes(settings, path):
    for space in ("default", "space-1"):
        target = settings.vault_path.parent / space / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"Nested content for {space}", encoding="utf-8")


def resource_body(uri):
    packet = json.loads(read_body("qualified_resource"))
    packet["params"]["uri"] = uri
    return json.dumps(packet).encode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("space", ["default", "space-1"])
@pytest.mark.parametrize("path,component", PATHS)
@pytest.mark.parametrize("selection", ["direct", "generated"])
def test_nested_qualified_round_trip(
    settings, runtime, monkeypatch, tmp_path, transport, space, path, component, selection
):
    nested_notes(settings, path)
    owned = runtime.operations.read_note(QualifiedPath(SpaceId(space), path))
    assert type(owned) is QualifiedNoteRead
    assert type(owned.identity) is QualifiedNoteIdentity
    assert owned.identity.canonical_relative_path == path
    assert owned.note.path == str(Path(path))
    expected = f"vaultbridge://space/{space}/note/{component}"
    uri = expected
    if selection == "generated":
        payload, calls, _ = invoke(
            transport, rpc(json.dumps({"space": space, "path": path})), settings, runtime, monkeypatch, tmp_path
        )
        result = payload["result"]["structuredContent"]
        assert result["space_id"] == space
        # Keep the inherited platform-native payload; only the locator is canonical.
        assert result["result"]["path"] == owned.note.path
        assert result["result"]["content"] == owned.note.content
        assert result["result"]["mime_type"] == "text/markdown"
        uri = result["result"]["resource_uri"]
        assert uri == expected
        assert "read_note" in calls and "authorize_read" in calls and "operation" in calls
    assert "%2F" in uri and "%5C" not in uri and "%2f" not in uri
    assert parse_resource_uri(uri) == QualifiedPath(SpaceId(space), path)
    payload, calls, _ = invoke(transport, resource_body(uri), settings, runtime, monkeypatch, tmp_path)
    assert "result" in payload, (payload, calls)
    assert payload["result"]["contents"] == [
        {"uri": expected, "mimeType": "text/markdown", "text": f"Nested content for {space}"}
    ]
    assert "authorize_read" in calls and "operation" in calls and "read_note" in calls


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("path,component", PATHS)
def test_nested_legacy_default_resource(settings, runtime, monkeypatch, tmp_path, transport, path, component):
    nested_notes(settings, path)
    uri = "vaultbridge://note/" + component
    assert component == quote(path, safe="")
    payload, calls, _ = invoke(transport, resource_body(uri), settings, runtime, monkeypatch, tmp_path)
    assert payload["result"]["contents"] == [
        {"uri": uri, "mimeType": "text/markdown", "text": "Nested content for default"}
    ]
    assert "read_note" in calls and "authorize_read" in calls


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("component", [
    "folder%2fnote.md", "folder%2F%6Eote.md", "folder%252Fnote.md", "folder%zznote.md", "folder%5Cnote.md",
    "..%2Fdefault%2Ffolder%2Fnote.md", "%2Fforeign-root%2Fnote.md",
])
def test_nested_uri_aliases_and_escape_rejected(settings, runtime, monkeypatch, tmp_path, transport, component):
    nested_notes(settings, "folder/note.md")
    payload, _, stderr = invoke(
        transport, resource_body("vaultbridge://space/space-1/note/" + component),
        settings, runtime, monkeypatch, tmp_path,
    )
    assert payload["error"]["message"] == "note_resource_unavailable: Note resource is invalid or unavailable."
    assert "Nested content" not in json.dumps(payload) + stderr.decode()


@pytest.mark.parametrize("transport", ["http", "stdio"])
@pytest.mark.parametrize("space", ["space-1", "unknown"])
def test_nested_resource_does_not_grant_access(settings, backend, monkeypatch, tmp_path, transport, space):
    nested_notes(settings, "folder/note.md")
    config = json.loads(settings.knowledge_spaces_json)
    for entry in config["spaces"]:
        if entry["space_id"] == "space-1":
            entry.update(read="deny", indexing="disabled")
    denied = Settings.model_validate({**settings.model_dump(), "knowledge_spaces_json": json.dumps(config)})
    with compose_space_runtime(denied) as owner_runtime:
        owner_runtime.scheduler.wait(20)
        payload, calls, stderr = invoke(
            transport, resource_body(f"vaultbridge://space/{space}/note/folder%2Fnote.md"),
            denied, owner_runtime, monkeypatch, tmp_path,
        )
    assert payload["error"]["message"] == "note_resource_unavailable: Note resource is invalid or unavailable."
    assert "authorize_read" in calls
    assert not {"operation", "read_note", "scoped_session"}.intersection(calls)
    assert "Nested content" not in json.dumps(payload) + stderr.decode()
