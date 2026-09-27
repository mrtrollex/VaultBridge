from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.services.relationships import RelationshipOccurrence, RelationshipService
from app.services.vault import VaultService
from app.services.wikilinks import Wikilink, WikilinkResolver


def service_for(vault: Path) -> RelationshipService:
    return RelationshipService(
        VaultService(vault_root=vault, max_note_bytes=1_000_000)
    )


def create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


def test_normalized_outgoing_maps_both_dialects_in_true_source_order(tmp_path):
    folder = tmp_path / "Priečinok"
    folder.mkdir()
    (folder / "Cieľ.md").write_text("target", encoding="utf-8")
    (folder / "Source.md").write_text(
        "[[Priečinok/Cieľ#Časť|Wiki štítok]] "
        "[Markdown štítok](Cieľ.md#Oddiel) [[Priečinok/Cieľ]]\n"
        "[Markdown štítok](Cieľ.md#Oddiel)",
        encoding="utf-8",
    )

    service = service_for(tmp_path)
    expected = (
        RelationshipOccurrence(
            source_path="Priečinok/Source.md",
            written_target="Priečinok/Cieľ",
            resolved_path="Priečinok/Cieľ.md",
            resolution="resolved",
            origin="obsidian_wikilink",
            relationship_type="note_link",
            fragment="Časť",
            label="Wiki štítok",
            source_order=0,
        ),
        RelationshipOccurrence(
            source_path="Priečinok/Source.md",
            written_target="Cieľ.md",
            resolved_path="Priečinok/Cieľ.md",
            resolution="resolved",
            origin="markdown_link",
            relationship_type="note_link",
            fragment="Oddiel",
            label="Markdown štítok",
            source_order=1,
        ),
        RelationshipOccurrence(
            source_path="Priečinok/Source.md",
            written_target="Priečinok/Cieľ",
            resolved_path="Priečinok/Cieľ.md",
            resolution="resolved",
            origin="obsidian_wikilink",
            relationship_type="note_link",
            fragment=None,
            label=None,
            source_order=2,
        ),
        RelationshipOccurrence(
            source_path="Priečinok/Source.md",
            written_target="Cieľ.md",
            resolved_path="Priečinok/Cieľ.md",
            resolution="resolved",
            origin="markdown_link",
            relationship_type="note_link",
            fragment="Oddiel",
            label="Markdown štítok",
            source_order=3,
        ),
    )

    assert service.normalized_outgoing_relationships("Priečinok/Source.md") == expected
    assert service.normalized_outgoing_relationships("Priečinok/Source.md") == expected
    assert tuple(item.source_order for item in expected) == (0, 1, 2, 3)


def test_normalized_occurrence_and_origin_metadata_are_immutable():
    occurrence = RelationshipOccurrence(
        source_path="Source.md",
        written_target="Target",
        resolved_path=None,
        resolution="missing",
        origin="obsidian_wikilink",
        relationship_type="note_link",
        fragment=None,
        label=None,
        source_order=0,
    )

    assert occurrence.origin_metadata == ()
    with pytest.raises(FrozenInstanceError):
        occurrence.resolution = "unsafe"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        occurrence.origin_metadata += (("private", "value"),)  # type: ignore[misc]


def test_normalized_resolution_distinguishes_bounded_outcomes(tmp_path):
    folder = tmp_path / "Folder"
    folder.mkdir()
    for duplicate_folder in (tmp_path / "First", tmp_path / "Second"):
        duplicate_folder.mkdir()
        (duplicate_folder / "Duplicate.md").write_text("duplicate", encoding="utf-8")
    (folder / "Source.md").write_text(
        "[[Missing]] [[Duplicate]] [[../Escape]] "
        "[Missing](Missing.md) [Escape](../../Outside.md)",
        encoding="utf-8",
    )

    relationships = service_for(tmp_path).normalized_outgoing_relationships(
        "Folder/Source.md"
    )

    assert tuple(item.resolution for item in relationships) == (
        "missing",
        "ambiguous",
        "unsafe",
        "missing",
        "unsafe",
    )
    assert all(item.resolved_path is None for item in relationships)
    assert set(item.resolution for item in relationships) <= {
        "resolved",
        "missing",
        "ambiguous",
        "unsafe",
    }


def test_normalized_resolution_does_not_use_alias_title_or_tags(tmp_path):
    (tmp_path / "Canonical.md").write_text(
        "---\ntitle: Display title\naliases: [Alias name]\ntags: [TargetTag]\n---\n",
        encoding="utf-8",
    )
    (tmp_path / "Source.md").write_text(
        "[[Alias name]] [[Display title]] [Tag](TargetTag.md)",
        encoding="utf-8",
    )

    relationships = service_for(tmp_path).normalized_outgoing_relationships("Source.md")

    assert tuple(item.resolution for item in relationships) == (
        "missing",
        "missing",
        "missing",
    )


def test_normalized_symlink_resolution_is_safe_and_privacy_bounded(tmp_path):
    vault = tmp_path / "vault"
    aliases = vault / "Aliases"
    canonical_folder = vault / "Canonical"
    aliases.mkdir(parents=True)
    canonical_folder.mkdir()
    canonical = canonical_folder / "Live.md"
    canonical.write_text("target", encoding="utf-8")
    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(aliases / "Internal.md", canonical)
    create_symlink_or_skip(aliases / "External.md", outside)
    create_symlink_or_skip(aliases / "Broken.md", tmp_path / "Missing.md")
    (vault / "Source.md").write_text(
        "[[Aliases/Internal]] [Internal](Aliases/Internal.md) "
        "[[Aliases/External]] [External](Aliases/External.md) "
        "[[Aliases/Broken]] [Broken](Aliases/Broken.md)",
        encoding="utf-8",
    )

    relationships = service_for(vault).normalized_outgoing_relationships("Source.md")

    assert tuple(
        (item.origin, item.resolution, item.resolved_path) for item in relationships
    ) == (
        ("obsidian_wikilink", "resolved", "Canonical/Live.md"),
        ("markdown_link", "resolved", "Canonical/Live.md"),
        ("obsidian_wikilink", "unsafe", None),
        ("markdown_link", "unsafe", None),
        ("obsidian_wikilink", "unsafe", None),
        ("markdown_link", "unsafe", None),
    )
    assert all(str(tmp_path) not in item.resolution for item in relationships)


def test_unqualified_wikilinks_classify_unsafe_missing_and_ambiguous(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "External.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(vault / "External.md", outside)
    create_symlink_or_skip(vault / "Broken.md", tmp_path / "Absent.md")
    nested = vault / "Nested"
    nested.mkdir()
    create_symlink_or_skip(nested / "NestedExternal.md", outside)
    create_symlink_or_skip(nested / "NestedBroken.md", tmp_path / "NestedAbsent.md")
    live = vault / "Live"
    live.mkdir()
    (live / "Resolved.md").write_text("resolved", encoding="utf-8")
    unsafe = vault / "Unsafe"
    unsafe.mkdir()
    create_symlink_or_skip(unsafe / "Resolved.md", outside)
    create_symlink_or_skip(unsafe / "Ambiguous.md", outside)
    for folder_name in ("First", "Second"):
        folder = vault / folder_name
        folder.mkdir()
        (folder / "Ambiguous.md").write_text(folder_name, encoding="utf-8")
    (vault / "Source.md").write_text(
        "[[External]] [[Broken]] [[NestedExternal]] [[NestedBroken]] "
        "[[Missing]] [[Resolved]] [[Ambiguous]]",
        encoding="utf-8",
    )

    service = service_for(vault)
    relationships = service.normalized_outgoing_relationships("Source.md")

    assert tuple(
        (item.written_target, item.resolution, item.resolved_path)
        for item in relationships
    ) == (
        ("External", "unsafe", None),
        ("Broken", "unsafe", None),
        ("NestedExternal", "unsafe", None),
        ("NestedBroken", "unsafe", None),
        ("Missing", "missing", None),
        ("Resolved", "resolved", "Live/Resolved.md"),
        ("Ambiguous", "ambiguous", None),
    )
    resolver = WikilinkResolver(
        VaultService(vault_root=vault, max_note_bytes=1_000_000)
    )
    assert resolver.resolve_markdown(
        "[[External]] [[Broken]] [[NestedExternal]] [[NestedBroken]] "
        "[[Missing]] [[Resolved]] [[Ambiguous]]"
    ) == (
        Wikilink(target="External"),
        Wikilink(target="Broken"),
        Wikilink(target="NestedExternal"),
        Wikilink(target="NestedBroken"),
        Wikilink(target="Missing"),
        Wikilink(target="Resolved", resolved_path="Live/Resolved.md"),
        Wikilink(target="Ambiguous"),
    )


def test_normalized_backlinks_use_verified_paths_origin_and_exact_deduplication(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "Target.md").write_text("target", encoding="utf-8")
    (tmp_path / "Alpha.md").write_text(
        "[[Target]] [[Target]] [Markdown](Target.md) [Markdown](Target.md)",
        encoding="utf-8",
    )
    (tmp_path / "beta.md").write_text(
        "Target.md [Other](Target.md#Part) [[Missing]]",
        encoding="utf-8",
    )
    vault_service = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    discovered = vault_service.live_markdown_paths()
    monkeypatch.setattr(
        vault_service,
        "live_markdown_paths",
        lambda: list(reversed(discovered)),
    )
    service = RelationshipService(vault_service)

    expected = (
        RelationshipOccurrence(
            "Alpha.md",
            "Target",
            "Target.md",
            "resolved",
            "obsidian_wikilink",
            "note_link",
            None,
            None,
            0,
        ),
        RelationshipOccurrence(
            "Alpha.md",
            "Target.md",
            "Target.md",
            "resolved",
            "markdown_link",
            "note_link",
            None,
            "Markdown",
            2,
        ),
        RelationshipOccurrence(
            "beta.md",
            "Target.md",
            "Target.md",
            "resolved",
            "markdown_link",
            "note_link",
            "Part",
            "Other",
            0,
        ),
    )
    assert service.normalized_backlinks("Target.md") == expected
    assert service.normalized_backlinks("Target.md") == expected

