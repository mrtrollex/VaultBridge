from __future__ import annotations

from pathlib import Path

import pytest

from app.services.relationships import Backlink, OutgoingRelationship, RelationshipService
from app.services.vault import (
    NoteNotFoundError,
    NoteTooLargeError,
    VaultService,
    VaultValidationError,
)


def service_for(
    vault: Path,
    *,
    max_note_bytes: int = 1_000_000,
) -> RelationshipService:
    return RelationshipService(
        VaultService(vault_root=vault, max_note_bytes=max_note_bytes)
    )


def create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


def test_outgoing_relationships_preserve_resolved_unresolved_metadata_and_order(tmp_path):
    folder = tmp_path / "Folder"
    folder.mkdir()
    (folder / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Životný plán.md").write_text("unicode", encoding="utf-8")
    (tmp_path / "Source.md").write_text(
        "[[Missing|Unknown]] [[Folder/Target#Section|Display]] [[Životný plán]]",
        encoding="utf-8",
    )

    relationships = service_for(tmp_path).outgoing_relationships("Source.md")

    assert relationships == (
        OutgoingRelationship(
            target="Missing",
            heading=None,
            alias="Unknown",
            resolved_path=None,
        ),
        OutgoingRelationship(
            target="Folder/Target",
            heading="Section",
            alias="Display",
            resolved_path="Folder/Target.md",
        ),
        OutgoingRelationship(
            target="Životný plán",
            heading=None,
            alias=None,
            resolved_path="Životný plán.md",
        ),
    )
    assert [relationship.state for relationship in relationships] == [
        "unresolved",
        "resolved",
        "resolved",
    ]


def test_outgoing_relationships_preserve_duplicate_occurrences_without_sorting(tmp_path):
    (tmp_path / "First.md").write_text("first", encoding="utf-8")
    (tmp_path / "Repeated.md").write_text("repeated", encoding="utf-8")
    (tmp_path / "Source.md").write_text(
        "[[Repeated]] [[Missing]] [[Repeated]] "
        "[[Repeated#One|First alias]] [[Repeated#Two|Second alias]] [[First]]",
        encoding="utf-8",
    )

    relationships = service_for(tmp_path).outgoing_relationships("Source.md")

    assert [relationship.target for relationship in relationships] == [
        "Repeated",
        "Missing",
        "Repeated",
        "Repeated",
        "Repeated",
        "First",
    ]
    assert relationships[0] == relationships[2]
    assert [(item.heading, item.alias) for item in relationships[3:5]] == [
        ("One", "First alias"),
        ("Two", "Second alias"),
    ]


@pytest.mark.parametrize(
    ("source_path", "expected_error"),
    [
        ("Missing.md", NoteNotFoundError),
        ("../Outside.md", VaultValidationError),
        ("Source.txt", VaultValidationError),
        ("Directory.md", NoteNotFoundError),
    ],
)
def test_outgoing_relationships_reuse_source_note_validation(
    tmp_path,
    source_path,
    expected_error,
):
    (tmp_path / "Directory.md").mkdir()

    with pytest.raises(expected_error):
        service_for(tmp_path).outgoing_relationships(source_path)


def test_outgoing_relationships_reject_absolute_source_path(tmp_path):
    source = tmp_path / "Source.md"
    source.write_text("[[Target]]", encoding="utf-8")

    with pytest.raises(VaultValidationError, match="Path must be vault-relative"):
        service_for(tmp_path).outgoing_relationships(str(source.resolve()))


def test_outgoing_relationships_reject_external_and_broken_source_symlinks(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "Outside.md"
    outside.write_text("[[Secret]]", encoding="utf-8")
    create_symlink_or_skip(vault / "External.md", outside)
    create_symlink_or_skip(vault / "Broken.md", vault / "Missing.md")
    service = service_for(vault)

    with pytest.raises(VaultValidationError, match="Path escapes the vault"):
        service.outgoing_relationships("External.md")
    with pytest.raises(NoteNotFoundError, match="Note not found"):
        service.outgoing_relationships("Broken.md")


def test_outgoing_relationships_accept_internal_source_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    canonical = vault / "Canonical.md"
    canonical.write_text("[[Target]]", encoding="utf-8")
    (vault / "Target.md").write_text("target", encoding="utf-8")
    create_symlink_or_skip(vault / "Alias.md", canonical)

    assert service_for(vault).outgoing_relationships("Alias.md") == (
        OutgoingRelationship(
            target="Target",
            heading=None,
            alias=None,
            resolved_path="Target.md",
        ),
    )


def test_outgoing_relationships_inherit_unsafe_target_resolution(tmp_path):
    outside = tmp_path.parent / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "Source.md").write_text(
        "[[../Outside]] [[C:/Outside]]",
        encoding="utf-8",
    )

    assert service_for(tmp_path).outgoing_relationships("Source.md") == (
        OutgoingRelationship(
            target="../Outside",
            heading=None,
            alias=None,
            resolved_path=None,
        ),
        OutgoingRelationship(
            target="C:/Outside",
            heading=None,
            alias=None,
            resolved_path=None,
        ),
    )


def test_outgoing_relationships_propagate_source_size_and_encoding_failures(tmp_path):
    (tmp_path / "Large.md").write_text("x" * 11, encoding="utf-8")
    (tmp_path / "Invalid.md").write_bytes(b"\xff\xfe")
    service = service_for(tmp_path, max_note_bytes=10)

    with pytest.raises(NoteTooLargeError, match="Note is too large"):
        service.outgoing_relationships("Large.md")
    with pytest.raises(UnicodeDecodeError):
        service.outgoing_relationships("Invalid.md")


def test_outgoing_relationships_propagate_source_read_failure(tmp_path, monkeypatch):
    source = tmp_path / "Source.md"
    source.write_text("[[Target]]", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_source_read(path: Path, *args, **kwargs):
        if path == source:
            raise OSError("source read failed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_source_read)

    with pytest.raises(OSError, match="source read failed"):
        service_for(tmp_path).outgoing_relationships("Source.md")


def test_backlinks_return_verified_sources_and_preserve_relationship_metadata(tmp_path):
    folder = tmp_path / "Folder"
    folder.mkdir()
    (folder / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Alpha.md").write_text(
        "[[Folder/Target#Overview|Read this]] [[Missing Target]]",
        encoding="utf-8",
    )
    (tmp_path / "Beta.md").write_text("[[Folder/Target]]", encoding="utf-8")
    (tmp_path / "Plain text.md").write_text("Folder/Target", encoding="utf-8")

    assert service_for(tmp_path).backlinks("Folder/Target.md") == (
        Backlink(
            source_path="Alpha.md",
            target="Folder/Target",
            heading="Overview",
            alias="Read this",
        ),
        Backlink(
            source_path="Beta.md",
            target="Folder/Target",
            heading=None,
            alias=None,
        ),
    )


def test_backlinks_support_unicode_paths_and_return_empty_result(tmp_path):
    folder = tmp_path / "Priečinok"
    folder.mkdir()
    target = folder / "Životný plán.md"
    target.write_text("target", encoding="utf-8")
    (tmp_path / "Zdroj.md").write_text("[[Priečinok/Životný plán|Plán]]", encoding="utf-8")
    (tmp_path / "Unrelated.md").write_text("no links", encoding="utf-8")
    service = service_for(tmp_path)

    assert service.backlinks("Priečinok/Životný plán.md") == (
        Backlink(
            source_path="Zdroj.md",
            target="Priečinok/Životný plán",
            heading=None,
            alias="Plán",
        ),
    )
    assert service.backlinks("Unrelated.md") == ()


@pytest.mark.parametrize(
    ("target_path", "expected_error"),
    [
        ("Missing.md", NoteNotFoundError),
        ("../Outside.md", VaultValidationError),
        ("Target.txt", VaultValidationError),
        ("Directory.md", NoteNotFoundError),
        ("target.md", NoteNotFoundError),
    ],
)
def test_backlinks_reuse_exact_target_validation(tmp_path, target_path, expected_error):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Target.txt").write_text("text", encoding="utf-8")
    (tmp_path / "Directory.md").mkdir()

    with pytest.raises(expected_error):
        service_for(tmp_path).backlinks(target_path)


def test_backlinks_reject_absolute_target_path(tmp_path):
    target = tmp_path / "Target.md"
    target.write_text("target", encoding="utf-8")

    with pytest.raises(VaultValidationError, match="Path must be vault-relative"):
        service_for(tmp_path).backlinks(str(target.resolve()))


def test_backlinks_reject_external_and_broken_target_symlinks(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(vault / "External.md", outside)
    create_symlink_or_skip(vault / "Broken.md", vault / "Missing.md")
    service = service_for(vault)

    with pytest.raises(VaultValidationError, match="Path escapes the vault"):
        service.backlinks("External.md")
    with pytest.raises(NoteNotFoundError, match="Note not found"):
        service.backlinks("Broken.md")


def test_backlinks_canonicalize_internal_target_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    canonical = vault / "Canonical.md"
    canonical.write_text("target", encoding="utf-8")
    create_symlink_or_skip(vault / "Alias.md", canonical)
    (vault / "Source.md").write_text("[[Alias#Section|Alias display]]", encoding="utf-8")

    assert service_for(vault).backlinks("Alias.md") == (
        Backlink(
            source_path="Source.md",
            target="Alias",
            heading="Section",
            alias="Alias display",
        ),
    )


def test_backlinks_require_canonical_resolution_not_ambiguous_or_case_mismatched_text(tmp_path):
    first = tmp_path / "First"
    second = tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    (first / "Target.md").write_text("first", encoding="utf-8")
    (second / "Target.md").write_text("second", encoding="utf-8")
    (tmp_path / "Ambiguous.md").write_text("[[Target]]", encoding="utf-8")
    (tmp_path / "Wrong case.md").write_text("[[first/Target]]", encoding="utf-8")
    (tmp_path / "Verified.md").write_text("[[First/Target]]", encoding="utf-8")

    assert service_for(tmp_path).backlinks("First/Target.md") == (
        Backlink(
            source_path="Verified.md",
            target="First/Target",
            heading=None,
            alias=None,
        ),
    )


def test_backlinks_ignore_relationships_through_external_symlinks(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Target.md").write_text("target", encoding="utf-8")
    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(vault / "External.md", outside)
    (vault / "Source.md").write_text("[[External]]", encoding="utf-8")

    assert service_for(vault).backlinks("Target.md") == ()


def test_backlinks_deduplicate_exact_links_but_preserve_distinct_metadata(tmp_path):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Source.md").write_text(
        "[[Target]] [[Target]] [[Target#One]] [[Target#One|Alias]] [[Target.md]]",
        encoding="utf-8",
    )

    assert service_for(tmp_path).backlinks("Target.md") == (
        Backlink("Source.md", "Target", None, None),
        Backlink("Source.md", "Target", "One", None),
        Backlink("Source.md", "Target", "One", "Alias"),
        Backlink("Source.md", "Target.md", None, None),
    )


def test_backlinks_sort_sources_independently_of_enumeration_order(tmp_path, monkeypatch):
    for path in ("zeta.md", "Alpha.md", "beta.md"):
        (tmp_path / path).write_text("[[Target]]", encoding="utf-8")
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    vault_service = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    discovered = vault_service.live_markdown_paths()
    monkeypatch.setattr(vault_service, "live_markdown_paths", lambda: list(reversed(discovered)))
    service = RelationshipService(vault_service)

    expected = (
        Backlink("Alpha.md", "Target", None, None),
        Backlink("beta.md", "Target", None, None),
        Backlink("zeta.md", "Target", None, None),
    )
    assert service.backlinks("Target.md") == expected
    assert service.backlinks("Target.md") == expected


def test_backlinks_do_not_duplicate_canonical_source_reached_by_symlink(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    source = vault / "Canonical source.md"
    source.write_text("[[Target]]", encoding="utf-8")
    (vault / "Target.md").write_text("target", encoding="utf-8")
    create_symlink_or_skip(vault / "Alias source.md", source)

    assert service_for(vault).backlinks("Target.md") == (
        Backlink("Canonical source.md", "Target", None, None),
    )


def test_backlinks_reuse_one_live_candidate_snapshot(tmp_path, monkeypatch):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    for index in range(3):
        (tmp_path / f"Source {index}.md").write_text("[[Target]]", encoding="utf-8")
    vault_service = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    original_candidates = vault_service.live_markdown_path_candidates
    calls = 0

    def counted_candidates():
        nonlocal calls
        calls += 1
        return original_candidates()

    monkeypatch.setattr(vault_service, "live_markdown_path_candidates", counted_candidates)

    assert len(RelationshipService(vault_service).backlinks("Target.md")) == 3
    assert calls == 1


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [(b"x" * 11, NoteTooLargeError), (b"\xff\xfe", UnicodeDecodeError)],
)
def test_backlinks_propagate_source_size_and_encoding_failures(
    tmp_path,
    content,
    expected_error,
):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Broken source.md").write_bytes(content)

    with pytest.raises(expected_error):
        service_for(tmp_path, max_note_bytes=10).backlinks("Target.md")


def test_backlinks_propagate_unexpected_source_read_failure(tmp_path, monkeypatch):
    source = tmp_path / "Source.md"
    source.write_text("[[Target]]", encoding="utf-8")
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_source_read(path: Path, *args, **kwargs):
        if path == source:
            raise OSError("source read failed")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_source_read)

    with pytest.raises(OSError, match="source read failed"):
        service_for(tmp_path).backlinks("Target.md")
