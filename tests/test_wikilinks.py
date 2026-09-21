from __future__ import annotations

from pathlib import Path

import pytest

from app.services.vault import VaultService
from app.services.wikilinks import Wikilink, WikilinkResolver


def resolver_for(vault: Path) -> WikilinkResolver:
    return WikilinkResolver(VaultService(vault_root=vault, max_note_bytes=1_000_000))


def create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


def test_parse_supported_forms_preserves_metadata_and_source_order():
    markdown = (
        "[[Note]] [[Folder/Note]] [[Note|Display alias]]\n"
        "[[Note#Heading]] [[Note#Heading|Heading alias]] [[Životný plán]]"
    )

    assert WikilinkResolver.parse(markdown) == (
        Wikilink(target="Note"),
        Wikilink(target="Folder/Note"),
        Wikilink(target="Note", alias="Display alias"),
        Wikilink(target="Note", heading="Heading"),
        Wikilink(target="Note", heading="Heading", alias="Heading alias"),
        Wikilink(target="Životný plán"),
    )


def test_parse_preserves_repeated_links_deterministically():
    markdown = "[[First]] [[Repeated]] [[Repeated]] [[Last]]"
    expected = (
        Wikilink(target="First"),
        Wikilink(target="Repeated"),
        Wikilink(target="Repeated"),
        Wikilink(target="Last"),
    )

    assert WikilinkResolver.parse(markdown) == expected
    assert WikilinkResolver.parse(markdown) == expected


def test_parse_ignores_backtick_and_tilde_fenced_code():
    markdown = """[[Before]]
```markdown
[[Inside backticks]]
````
[[Between]]
~~~text
[[Inside tildes]]
~~~
[[After]]
"""

    assert WikilinkResolver.parse(markdown) == (
        Wikilink(target="Before"),
        Wikilink(target="Between"),
        Wikilink(target="After"),
    )


def test_parse_ignores_malformed_or_incomplete_links():
    markdown = (
        "[[]] [[ ]] [[#Heading]] [[Note#]] [[Note|]] [[Note|Alias|Extra]] "
        "[[Outer [[Inner]] [[Unclosed"
    )

    assert WikilinkResolver.parse(markdown) == ()


def test_resolve_markdown_handles_exact_paths_extensions_and_unicode(tmp_path):
    (tmp_path / "Root.md").write_text("root", encoding="utf-8")
    folder = tmp_path / "Priečinok"
    folder.mkdir()
    (folder / "Životný plán.md").write_text("unicode", encoding="utf-8")
    (folder / "Unique.md").write_text("unique nested note", encoding="utf-8")
    resolver = resolver_for(tmp_path)

    resolved = resolver.resolve_markdown(
        "[[Root]] [[Unique]] [[Priečinok/Životný plán]] "
        "[[Priečinok/Životný plán.md|Plán]]"
    )

    assert resolved == (
        Wikilink(target="Root", resolved_path="Root.md"),
        Wikilink(target="Unique", resolved_path="Priečinok/Unique.md"),
        Wikilink(
            target="Priečinok/Životný plán",
            resolved_path="Priečinok/Životný plán.md",
        ),
        Wikilink(
            target="Priečinok/Životný plán.md",
            alias="Plán",
            resolved_path="Priečinok/Životný plán.md",
        ),
    )


def test_unqualified_ambiguous_exact_name_is_unresolved(tmp_path):
    for folder_name in ("First", "Second"):
        folder = tmp_path / folder_name
        folder.mkdir()
        (folder / "Duplicate.md").write_text(folder_name, encoding="utf-8")
    resolver = resolver_for(tmp_path)

    assert resolver.resolve(Wikilink(target="Duplicate")) == Wikilink(target="Duplicate")


def test_resolution_requires_exact_filename_and_folder_case(tmp_path):
    (tmp_path / "Note.md").write_text("root note", encoding="utf-8")
    folder = tmp_path / "CaseFolder"
    folder.mkdir()
    (folder / "FolderNote.md").write_text("folder note", encoding="utf-8")
    resolver = resolver_for(tmp_path)

    assert resolver.resolve_markdown(
        "[[note]] [[casefolder/FolderNote]] [[CaseFolder/foldernote]] "
        "[[Note]] [[CaseFolder/FolderNote]]"
    ) == (
        Wikilink(target="note"),
        Wikilink(target="casefolder/FolderNote"),
        Wikilink(target="CaseFolder/foldernote"),
        Wikilink(target="Note", resolved_path="Note.md"),
        Wikilink(target="CaseFolder/FolderNote", resolved_path="CaseFolder/FolderNote.md"),
    )


@pytest.mark.parametrize(
    "target",
    [
        "Missing",
        "../Outside",
        "Folder/../../Outside",
        "/absolute/Outside.md",
        "C:/absolute/Outside.md",
        "Data.txt",
        "Directory.md",
    ],
)
def test_missing_unsafe_non_markdown_and_directory_targets_are_unresolved(tmp_path, target):
    (tmp_path / "Data.txt").write_text("not Markdown", encoding="utf-8")
    (tmp_path / "Directory.md").mkdir()
    resolver = resolver_for(tmp_path)

    assert resolver.resolve(Wikilink(target=target)) == Wikilink(target=target)


def test_internal_symlink_resolves_canonically_and_external_or_broken_symlinks_do_not(tmp_path):
    vault = tmp_path / "vault"
    aliases = vault / "Aliases"
    canonical_folder = vault / "Canonical"
    aliases.mkdir(parents=True)
    canonical_folder.mkdir()
    canonical = canonical_folder / "Live.md"
    canonical.write_text("canonical", encoding="utf-8")
    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(aliases / "Internal.md", canonical)
    create_symlink_or_skip(aliases / "External.md", outside)
    create_symlink_or_skip(aliases / "Broken.md", tmp_path / "Missing.md")
    resolver = resolver_for(vault)

    assert resolver.resolve_markdown(
        "[[Aliases/Internal]] [[Aliases/External]] [[Aliases/Broken]]"
    ) == (
        Wikilink(target="Aliases/Internal", resolved_path="Canonical/Live.md"),
        Wikilink(target="Aliases/External"),
        Wikilink(target="Aliases/Broken"),
    )


def test_internal_directory_symlink_requires_exact_alias_spelling(tmp_path):
    vault = tmp_path / "vault"
    canonical_folder = vault / "Canonical"
    canonical_folder.mkdir(parents=True)
    (canonical_folder / "Live.md").write_text("canonical", encoding="utf-8")
    create_symlink_or_skip(vault / "AliasFolder", canonical_folder)
    resolver = resolver_for(vault)

    assert resolver.resolve_markdown(
        "[[AliasFolder/Live]] [[aliasfolder/Live]] [[AliasFolder/live]]"
    ) == (
        Wikilink(target="AliasFolder/Live", resolved_path="Canonical/Live.md"),
        Wikilink(target="aliasfolder/Live"),
        Wikilink(target="AliasFolder/live"),
    )


def test_unqualified_internal_alias_and_distinct_same_name_note_are_ambiguous(tmp_path):
    vault = tmp_path / "vault"
    alias_folder = vault / "A"
    distinct_folder = vault / "B"
    canonical_folder = vault / "Canonical"
    alias_folder.mkdir(parents=True)
    distinct_folder.mkdir()
    canonical_folder.mkdir()
    canonical = canonical_folder / "Live.md"
    canonical.write_text("canonical", encoding="utf-8")
    create_symlink_or_skip(alias_folder / "Name.md", canonical)
    (distinct_folder / "Name.md").write_text("distinct", encoding="utf-8")
    resolver = resolver_for(vault)

    assert resolver.resolve(Wikilink(target="Name")) == Wikilink(target="Name")


def test_resolution_is_read_only_and_deterministic(tmp_path):
    note = tmp_path / "Note.md"
    note.write_text("unchanged", encoding="utf-8")
    resolver = resolver_for(tmp_path)
    markdown = "[[Note#Heading|Alias]] [[Missing]] [[Note]]"

    first = resolver.resolve_markdown(markdown)
    second = resolver.resolve_markdown(markdown)

    assert first == second
    assert first == (
        Wikilink(
            target="Note",
            heading="Heading",
            alias="Alias",
            resolved_path="Note.md",
        ),
        Wikilink(target="Missing"),
        Wikilink(target="Note", resolved_path="Note.md"),
    )
    assert note.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Note.md"]
