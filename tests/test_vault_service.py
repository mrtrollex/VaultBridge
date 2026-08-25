from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.vault import (
    NoteConflictError,
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultValidationError,
)


def service_for(tmp_path: Path, *, max_note_bytes: int = 1_000_000) -> VaultService:
    return VaultService(vault_root=tmp_path, max_note_bytes=max_note_bytes)


def create_symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


def test_vault_availability_requires_an_existing_directory(tmp_path):
    directory = tmp_path / "vault"
    directory.mkdir()
    missing = tmp_path / "missing"
    regular_file = tmp_path / "vault-file"
    regular_file.write_text("not a directory", encoding="utf-8")

    assert service_for(directory).vault_available() is True
    assert service_for(missing).vault_available() is False
    assert service_for(regular_file).vault_available() is False


@pytest.mark.parametrize(
    "error",
    [PermissionError("vault metadata denied"), OSError("vault metadata unavailable")],
)
def test_vault_availability_handles_expected_directory_metadata_errors(tmp_path, monkeypatch, error):
    service = service_for(tmp_path)

    def unavailable(_path):
        raise error

    monkeypatch.setattr(Path, "is_dir", unavailable)

    assert service.vault_available() is False


def test_vault_availability_handles_directory_open_errors(tmp_path, monkeypatch):
    service = service_for(tmp_path)

    def unavailable(_path):
        raise PermissionError("vault directory cannot be inspected")

    monkeypatch.setattr("app.services.vault.os.scandir", unavailable)

    assert service.vault_available() is False


def test_vault_availability_does_not_scan_or_read_notes(tmp_path, monkeypatch):
    service = service_for(tmp_path)

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("vault availability scanned or read notes")

    monkeypatch.setattr(Path, "rglob", unexpected_call)
    monkeypatch.setattr(Path, "read_text", unexpected_call)

    assert service.vault_available() is True


def test_create_and_read_note_preserves_markdown_format(tmp_path):
    service = service_for(tmp_path)

    created = service.create_note(
        title="Životný plán",
        folder="Learning",
        content="First line.\n",
        tags=["plán", "learning"],
    )
    read = service.read_note(created.path)

    assert created.status == "created"
    assert created.path == str(Path("Learning") / "Životný plán.md")
    assert read.path == created.path
    assert read.content.startswith("---\ncreated: ")
    assert '\ntags: ["plán", "learning"]\nsource: chatgpt\n---\n\n' in read.content
    assert read.content.endswith("First line.\n")


def test_existing_note_is_unchanged_or_conflicts(tmp_path):
    service = service_for(tmp_path)
    request = {
        "title": "Existing note",
        "folder": "Inbox",
        "content": "Stable content",
        "tags": ["test"],
    }

    assert service.create_note(**request).status == "created"
    assert service.create_note(**request).status == "unchanged"

    with pytest.raises(
        NoteConflictError,
        match="A note with this title already exists. Use appendNote or choose another title.",
    ):
        service.create_note(**{**request, "content": "Different content"})


def test_append_and_dedupe_preserve_marker_behavior(tmp_path):
    service = service_for(tmp_path)
    path = service.create_note(title="Append target", folder="Inbox", content="Start", tags=[]).path

    appended = service.append_note(path=path, content=" More detail. ", dedupe_key="task key/1")
    duplicate = service.append_note(path=path, content=" More detail. ", dedupe_key="task key/1")
    content = service.read_note(path).content

    assert appended.status == "appended"
    assert duplicate.status == "already_applied"
    assert content.count("More detail.") == 1
    assert content.count("<!-- chatgpt-append:task_key_1 -->") == 1


def test_literal_search_covers_content_title_folder_and_limit(tmp_path):
    service = service_for(tmp_path)
    service.create_note(title="Oracle overview", folder="Work", content="General database notes", tags=[])
    service.create_note(title="Other", folder="Work", content="An Oracle identifier appears here", tags=[])
    service.create_note(title="Oracle outside", folder="Personal", content="Outside folder", tags=[])

    results = service.search_notes(query="oracle", folder="Work", limit=2)

    assert {result.title for result in results} == {"Oracle overview", "Other"}
    assert all(result.path.startswith(f"Work{os.sep}") for result in results)
    assert service.search_notes(query="oracle", folder="Missing", limit=10) == []


def test_list_notes_is_newest_first_and_honors_limit(tmp_path):
    service = service_for(tmp_path)
    older = tmp_path / "Older.md"
    newer = tmp_path / "Newer.md"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    notes = service.list_notes(limit=1)

    assert len(notes) == 1
    assert notes[0].title == "Newer"
    assert notes[0].modified == "2023-11-14T22:15:00+00:00"


@pytest.mark.parametrize("path", ["../outside.md", "folder/../../outside.md"])
def test_path_traversal_is_rejected(tmp_path, path):
    service = service_for(tmp_path)

    with pytest.raises(VaultValidationError, match="Path escapes the vault"):
        service.read_note(path)


def test_absolute_path_and_non_markdown_file_are_rejected(tmp_path):
    service = service_for(tmp_path)

    with pytest.raises(VaultValidationError, match="Path must be vault-relative"):
        service.read_note(str((tmp_path / "Note.md").resolve()))

    with pytest.raises(VaultValidationError, match="Only .md files are allowed"):
        service.read_note("Note.txt")


def test_symlink_escape_is_rejected(tmp_path):
    service = service_for(tmp_path / "vault")
    service.vault_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = service.vault_root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    with pytest.raises(VaultValidationError, match="Path escapes the vault"):
        service.read_note("linked/Secret.md")


def test_note_count_ignores_symlink_escape_without_reading_contents(tmp_path):
    service = service_for(tmp_path / "vault")
    service.vault_root.mkdir()
    (service.vault_root / "inside.md").write_text("Inside.", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("Outside.", encoding="utf-8")
    link = service.vault_root / "outside.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    assert service.count_notes() == 1


def test_search_and_list_skip_external_file_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    safe = vault / "safe.md"
    safe.write_text("Contained search marker.", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("External unique secret marker.", encoding="utf-8")
    create_symlink_or_skip(vault / "external-link.md", outside)
    service = service_for(vault)

    assert [result.path for result in service.search_notes(query="Contained search marker")] == [
        str(Path("safe.md"))
    ]
    assert service.search_notes(query="External unique secret marker") == []
    assert [note.path for note in service.list_notes()] == [str(Path("safe.md"))]


def test_internal_file_symlink_is_canonical_and_deduplicated(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    canonical = vault / "canonical.md"
    canonical.write_text("Internal alias marker.", encoding="utf-8")
    create_symlink_or_skip(vault / "alias.md", canonical)
    non_markdown = vault / "not-a-note.txt"
    non_markdown.write_text("Internal non-Markdown marker.", encoding="utf-8")
    create_symlink_or_skip(vault / "text-alias.md", non_markdown)
    service = service_for(vault)

    search_results = service.search_notes(query="Internal alias marker")
    list_results = service.list_notes()

    assert [result.path for result in search_results] == [str(Path("canonical.md"))]
    assert [note.path for note in list_results] == [str(Path("canonical.md"))]
    assert service.search_notes(query="Internal non-Markdown marker") == []


def test_broken_and_external_directory_symlinks_are_skipped(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "secret.md").write_text(
        "External directory secret marker.",
        encoding="utf-8",
    )
    create_symlink_or_skip(vault / "broken.md", tmp_path / "missing.md")
    create_symlink_or_skip(
        vault / "external-directory",
        outside_directory,
        target_is_directory=True,
    )
    service = service_for(vault)

    assert service.search_notes(query="External directory secret marker") == []
    assert service.list_notes() == []


def test_search_size_encoding_and_folder_scoped_containment_are_preserved(tmp_path):
    vault = tmp_path / "vault"
    scoped = vault / "Scoped"
    scoped.mkdir(parents=True)
    safe = scoped / "safe.md"
    safe.write_text("Scoped safe marker.", encoding="utf-8")
    large = scoped / "large.md"
    large.write_text("Large external-style marker.", encoding="utf-8")
    invalid = scoped / "invalid.md"
    invalid.write_bytes(b"\xff\xfe invalid marker")
    outside = tmp_path / "outside.md"
    outside.write_text("Scoped external marker.", encoding="utf-8")
    create_symlink_or_skip(scoped / "external-link.md", outside)
    service = service_for(vault, max_note_bytes=20)

    assert [result.path for result in service.search_notes(query="Scoped safe", folder="Scoped")] == [
        str(Path("Scoped") / "safe.md")
    ]
    assert service.search_notes(query="Large external-style", folder="Scoped") == []
    assert service.search_notes(query="invalid marker", folder="Scoped") == []
    assert service.search_notes(query="Scoped external", folder="Scoped") == []
    assert {note.path for note in service.list_notes(folder="Scoped")} == {
        str(Path("Scoped") / "safe.md"),
        str(Path("Scoped") / "large.md"),
        str(Path("Scoped") / "invalid.md"),
    }


def test_missing_and_oversized_note_reads_are_rejected(tmp_path):
    service = service_for(tmp_path, max_note_bytes=10)

    with pytest.raises(NoteNotFoundError, match="Note not found"):
        service.read_note("Missing.md")

    (tmp_path / "Large.md").write_text("x" * 11, encoding="utf-8")
    with pytest.raises(NoteTooLargeError, match="Note is too large"):
        service.read_note("Large.md")


def test_create_and_append_size_limits_are_enforced(tmp_path):
    small_service = service_for(tmp_path / "small", max_note_bytes=60)
    with pytest.raises(NoteTooLargeError, match="Generated note is too large"):
        small_service.create_note(title="Large", folder="Inbox", content="content", tags=[])

    service = service_for(tmp_path / "append", max_note_bytes=110)
    path = service.create_note(title="Target", folder="Inbox", content="Start", tags=[]).path
    with pytest.raises(NoteTooLargeError, match="Resulting note would be too large"):
        service.append_note(path=path, content="x" * 80)
