from __future__ import annotations

from pathlib import Path

import pytest

from app.services.markdown_links import MarkdownLink, MarkdownLinkResolver
from app.services.vault import VaultService


def resolver_for(vault: Path) -> MarkdownLinkResolver:
    return MarkdownLinkResolver(
        VaultService(vault_root=vault, max_note_bytes=1_000_000)
    )


def create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")


def test_parse_supported_links_preserves_written_metadata_order_and_duplicates():
    markdown = (
        "[Root](Note.md) [Folder](Folder/Note.md) [Parent](../Note.md) "
        "[Heading](Note.md#Exact-Heading) [Spaced](<My Note.md>) "
        "[Unicode ž](<Priečinok/Životný plán.md#Časť>) [Root](Note.md)"
    )

    assert MarkdownLinkResolver.parse(markdown) == (
        MarkdownLink("Note.md", None, "Root"),
        MarkdownLink("Folder/Note.md", None, "Folder"),
        MarkdownLink("../Note.md", None, "Parent"),
        MarkdownLink("Note.md", "Exact-Heading", "Heading"),
        MarkdownLink("My Note.md", None, "Spaced"),
        MarkdownLink("Priečinok/Životný plán.md", "Časť", "Unicode ž"),
        MarkdownLink("Note.md", None, "Root"),
    )


def test_parse_ignores_fenced_and_inline_code_but_keeps_surrounding_links():
    markdown = """[Before](Before.md) `code [Inline](Inline.md)` [After](After.md)
```python
[Fenced](Fenced.md)
```
~~~
[Also fenced](Other.md)
~~~
``double [code](Code.md)`` [Last](Last.md)
"""

    assert MarkdownLinkResolver.parse(markdown) == (
        MarkdownLink("Before.md", None, "Before"),
        MarkdownLink("After.md", None, "After"),
        MarkdownLink("Last.md", None, "Last"),
    )


def test_parse_ignores_excluded_markdown_and_html_syntax():
    markdown = (
        "![Image](Image.md) [Reference][note] [note]: Note.md "
        "<Note.md> <a href=\"Note.md\">HTML</a> [[Wiki]] "
        "[^footnote] [Title](Note.md \"Optional title\") "
        "[Angle title](<Note.md> \"Optional title\")"
    )

    assert MarkdownLinkResolver.parse(markdown) == ()


@pytest.mark.parametrize(
    "markdown",
    [
        "[Incomplete](Note.md",
        "[Incomplete(Note.md)",
        "[Nested [label]](Note.md)",
        "[NUL\x00label](Note.md)",
        "[NUL](Note\x00.md)",
        "[Whitespace](My Note.md)",
        "[Empty]()",
        "[Fragment](#Heading)",
    ],
)
def test_parse_ignores_malformed_or_unsupported_forms(markdown):
    assert MarkdownLinkResolver.parse(markdown) == ()


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.com/Note.md",
        "http://example.com/Note.md",
        "mailto:person@example.md",
        "custom:Note.md",
        "//host/Note.md",
        "/absolute/Note.md",
        "C:/absolute/Note.md",
        "\\\\host\\share\\Note.md",
        "Note.txt",
        "Folder/",
    ],
)
def test_parse_excludes_non_local_or_non_markdown_destinations(destination):
    assert MarkdownLinkResolver.parse(f"[Label](<{destination}>)") == ()


def test_parse_keeps_percent_escapes_literal():
    assert MarkdownLinkResolver.parse("[Encoded](My%20Note.md#Part%201)") == (
        MarkdownLink("My%20Note.md", "Part%201", "Encoded"),
    )


def test_resolve_uses_verified_source_directory_and_preserves_fragments(tmp_path):
    folder = tmp_path / "Folder"
    folder.mkdir()
    (folder / "Source.md").write_text("source", encoding="utf-8")
    (folder / "Sibling.md").write_text("sibling", encoding="utf-8")
    (tmp_path / "Root.md").write_text("root", encoding="utf-8")
    resolver = resolver_for(tmp_path)

    assert resolver.resolve_markdown(
        "[Sibling](Sibling.md) [Root](../Root.md#Missing-heading)",
        source_path="Folder/Source.md",
    ) == (
        MarkdownLink("Sibling.md", None, "Sibling", "Folder/Sibling.md"),
        MarkdownLink("../Root.md", "Missing-heading", "Root", "Root.md"),
    )


def test_missing_unsafe_directory_and_case_mismatched_targets_are_unresolved(tmp_path):
    folder = tmp_path / "Folder"
    folder.mkdir()
    (folder / "Source.md").write_text("source", encoding="utf-8")
    (folder / "Target.md").write_text("target", encoding="utf-8")
    (folder / "Directory.md").mkdir()
    resolver = resolver_for(tmp_path)

    assert resolver.resolve_markdown(
        "[Missing](Missing.md) [Escape](../../Outside.md) "
        "[Directory](Directory.md) [Case](target.md)",
        source_path="Folder/Source.md",
    ) == (
        MarkdownLink("Missing.md", None, "Missing"),
        MarkdownLink("../../Outside.md", None, "Escape"),
        MarkdownLink("Directory.md", None, "Directory"),
        MarkdownLink("target.md", None, "Case"),
    )


def test_resolution_does_not_percent_decode_or_use_alias_title_fallback(tmp_path):
    (tmp_path / "My Note.md").write_text("target", encoding="utf-8")
    (tmp_path / "Canonical.md").write_text(
        "---\naliases: [Alias.md]\n---\n# Title.md\n",
        encoding="utf-8",
    )
    (tmp_path / "Source.md").write_text("source", encoding="utf-8")
    resolver = resolver_for(tmp_path)

    assert resolver.resolve_markdown(
        "[Encoded](My%20Note.md) [Alias](Alias.md) [Title](Title.md)",
        source_path="Source.md",
    ) == (
        MarkdownLink("My%20Note.md", None, "Encoded"),
        MarkdownLink("Alias.md", None, "Alias"),
        MarkdownLink("Title.md", None, "Title"),
    )


def test_internal_symlink_canonicalizes_and_external_or_broken_symlinks_do_not(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "Folder"
    canonical_folder = vault / "Canonical"
    folder.mkdir(parents=True)
    canonical_folder.mkdir()
    (folder / "Source.md").write_text("source", encoding="utf-8")
    canonical = canonical_folder / "Target.md"
    canonical.write_text("target", encoding="utf-8")
    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    create_symlink_or_skip(folder / "Internal.md", canonical)
    create_symlink_or_skip(folder / "External.md", outside)
    create_symlink_or_skip(folder / "Broken.md", tmp_path / "Missing.md")
    resolver = resolver_for(vault)

    assert resolver.resolve_markdown(
        "[Internal](Internal.md) [External](External.md) [Broken](Broken.md)",
        source_path="Folder/Source.md",
    ) == (
        MarkdownLink("Internal.md", None, "Internal", "Canonical/Target.md"),
        MarkdownLink("External.md", None, "External"),
        MarkdownLink("Broken.md", None, "Broken"),
    )
