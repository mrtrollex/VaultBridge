from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.repositories.semantic import SemanticRepository, StoredChunk, StoredNote
from app.services.duplicate_candidates import DuplicateCandidateService
from app.services.knowledge_hygiene import (
    KnowledgeHygieneError,
    KnowledgeHygieneRequest,
    KnowledgeHygieneService,
)
from app.services.relationships import RelationshipService
from app.services.semantic_search import SemanticSearchService
from app.services.vault import (
    BoundedMarkdownPathFact,
    BoundedMarkdownSnapshot,
    BoundedMarkdownSpellingFact,
    MarkdownPathVerification,
    NoteReadResult,
    NoteUnavailableError,
    VaultService,
)


class SemanticStub:
    def __init__(self, status="missing"):
        self.status = status
        self.calls = 0

    def inspect_hygiene_index(self):
        self.calls += 1
        return self.status


def service_for(root: Path, *, semantic=None):
    vault = VaultService(vault_root=root, max_note_bytes=1_000_000)
    semantic = semantic or SemanticStub()
    duplicate = DuplicateCandidateService(
        vault_service=vault, semantic_search_service=semantic,
    )
    return KnowledgeHygieneService(
        vault_service=vault,
        relationship_service=RelationshipService(vault),
        duplicate_candidate_service=duplicate,
        semantic_search_service=semantic,
    ), vault, duplicate


def write(root: Path, path: str, body: str):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


@pytest.mark.parametrize("kwargs", [
    {"groups": {"frontmatter"}},
    {"groups": frozenset({"unknown"})},
    {"finding_limit": True},
    {"finding_limit": 0},
    {"finding_limit": 501},
    {"duplicate_source_limit": False},
    {"duplicate_source_limit": 21},
    {"semantic_candidates": True},
    {"semantic_candidates": 1, "duplicate_source_limit": 1},
    {"inspect_derived_index": 1},
])
def test_invalid_request_fails_before_owner_work(tmp_path, monkeypatch, kwargs):
    service, vault, _ = service_for(tmp_path)
    monkeypatch.setattr(vault, "bounded_markdown_snapshot", lambda: pytest.fail("enumerated"))
    with pytest.raises(KnowledgeHygieneError) as error:
        service.scan(KnowledgeHygieneRequest(**kwargs))
    assert error.value.reason == "invalid_request"
    assert str(error.value) == "invalid_request"


def test_relationship_isolation_and_capture_classification(tmp_path):
    write(tmp_path, "A.md", "[[B]] [[Missing]] [bad](../outside.md) [[Twin]]")
    write(tmp_path, "B.md", "body")
    write(tmp_path, "Twin.md", "body")
    write(tmp_path, "Folder/Twin.md", "body")
    write(tmp_path, "Self.md", "[[Self]]")
    write(tmp_path, "Unresolved.md", "[[NoSuch]]")
    write(tmp_path, "Inbox.md", "---\ncapture_state: inbox\n---\nbody")
    write(tmp_path, "Draft.md", "---\ncapture_state: draft\n---\nbody")
    write(tmp_path, "Unknown.md", "---\ncapture_state: unknown\n---\nbody")
    service, _, _ = service_for(tmp_path)
    first = service.scan()
    assert service.scan() == first
    assert first.scan.state == "complete"
    def by_kind(kind):
        return [finding for finding in first.findings if finding.kind == kind]

    assert any(f.primary_path == "A.md" and f.evidence.source_order == 1
               for f in by_kind("missing_relationship_target"))
    assert any(f.primary_path == "A.md" and f.evidence.source_order == 2
               for f in by_kind("unsafe_relationship_target"))
    assert any(f.primary_path == "A.md" and f.evidence.source_order == 3
               for f in by_kind("ambiguous_relationship_target"))
    assert {f.primary_path for f in by_kind("isolated_note")} == {
        "Folder/Twin.md", "Twin.md", "Unresolved.md",
    }
    assert all(not hasattr(f.evidence, "written_target") for f in by_kind("missing_relationship_target"))


@pytest.mark.skipif(os.name != "nt", reason="Windows Markdown glob matching")
def test_uppercase_markdown_extension_is_a_resolved_hygiene_node_on_windows(tmp_path):
    write(tmp_path, "Source.md", "[[Target.MD]]")
    write(tmp_path, "Target.MD", "body")
    service, vault, _ = service_for(tmp_path)
    assert vault.live_markdown_paths() == ["Source.md", "Target.MD"]
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete and snapshot.resolution_complete
    assert snapshot.paths == ("Source.md", "Target.MD")
    assert {candidate.discovered_path for candidate in snapshot.candidates.live_candidates} == set(snapshot.paths)
    owner_links = service._relationships.normalized_outgoing_relationships("Source.md")
    assert [(link.resolution, link.resolved_path) for link in owner_links] == [
        ("resolved", "Target.MD"),
    ]

    result = service.scan(KnowledgeHygieneRequest(
        groups=frozenset({"relationships", "isolation"}), duplicate_source_limit=1,
    ))
    assert result.scan.state == "complete"
    assert result.scan.eligible_paths == result.scan.inspected_notes == 2
    assert result.candidates.state == "complete"
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)


@pytest.mark.skipif(os.name == "nt", reason="POSIX case-sensitive Markdown discovery")
def test_alias_only_canonical_target_is_inspected_and_connected_on_posix(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "Source.md", "[[Alias]]")
    write(tmp_path, "Target.MD", "[[Alias]]")
    try:
        (tmp_path / "Alias.md").symlink_to(tmp_path / "Target.MD")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    service, vault, _ = service_for(tmp_path)
    assert vault.live_markdown_paths() == ["Source.md", "Target.MD"]
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete and snapshot.paths == ("Source.md", "Target.MD")
    assert {(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates} == {
        ("Alias.md", "Target.MD"), ("Source.md", "Source.md"),
    }
    assert service._relationships.normalized_outgoing_relationships("Source.md")[0].resolved_path == (
        "Target.MD"
    )

    original_scandir = vault_module.os.scandir
    original_read = vault.read_verified_markdown_snapshot
    calls = {"scandir": 0, "read": 0}

    def scandir(directory):
        calls["scandir"] += 1
        return original_scandir(directory)

    def read(path, **kwargs):
        calls["read"] += 1
        return original_read(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(vault, "read_verified_markdown_snapshot", read)
        result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=1))
    assert calls == {"scandir": 1, "read": 2}
    assert result.scan.state == "complete"
    assert result.scan.eligible_paths == result.scan.inspected_notes == 2
    assert result.candidates.state == "complete"
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink spelling races")
@pytest.mark.parametrize("replacement", ["escaping", "deleted", "retargeted", "regular", "renamed"])
def test_unqualified_snapshot_rechecks_alias_spelling_before_resolution(
    tmp_path, monkeypatch, replacement,
):
    from app.services import vault as vault_module

    write(tmp_path, "Target.md", "body")
    write(tmp_path, "Other.md", "body")
    write(tmp_path, "Source.md", "[[Alias]]")
    alias = tmp_path / "aliases/Alias.md"
    alias.parent.mkdir()
    alias.symlink_to(tmp_path / "Target.md")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside", encoding="utf-8")
    service, vault, _ = service_for(tmp_path)
    bounded = vault.bounded_markdown_snapshot()
    assert bounded.complete and bounded.resolution_complete
    resolution = service._relationships.normalized_resolution_snapshot(bounded.candidates)
    facts = {fact.path: fact for fact in bounded.path_facts}
    assert vault.verify_bounded_markdown_path(facts["Source.md"])
    assert vault.verify_bounded_markdown_path(facts["Target.md"])

    alias.unlink()
    if replacement == "escaping":
        alias.symlink_to(outside)
    elif replacement == "retargeted":
        alias.symlink_to(tmp_path / "Other.md")
    elif replacement == "regular":
        alias.write_text("replacement", encoding="utf-8")
    elif replacement == "renamed":
        (tmp_path / "aliases/alias.md").symlink_to(tmp_path / "Target.md")

    assert vault.read_verified_markdown_snapshot("Source.md", fact=facts["Source.md"]).content == (
        "[[Alias]]"
    )
    assert vault.read_verified_markdown_snapshot("Target.md", fact=facts["Target.md"]).content == (
        "body"
    )

    original_scandir = vault_module.os.scandir
    scans = 0

    def scandir(directory):
        nonlocal scans
        scans += 1
        return original_scandir(directory)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(vault_module.os, "scandir", scandir)
            with pytest.raises(NoteUnavailableError, match="relationship_unavailable"):
                service._relationships.normalized_relationships_from_content(
                    "[[Alias]]", source_path="Source.md", snapshot=resolution,
                )
        assert scans == 0
        assert vault.verify_bounded_markdown_path(facts["Source.md"])
        assert vault.verify_bounded_markdown_path(facts["Target.md"])
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX escaping alias swap")
def test_hygiene_escaping_alias_swap_withholds_stale_connectivity(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "Target.md", "body")
    write(tmp_path, "Source.md", "[[Alias]]")
    alias = tmp_path / "aliases/Alias.md"
    alias.parent.mkdir()
    alias.symlink_to(tmp_path / "Target.md")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside", encoding="utf-8")
    service, vault, _ = service_for(tmp_path)
    original_snapshot = vault.bounded_markdown_snapshot
    original_read = vault.read_verified_markdown_snapshot
    original_derive = service._relationships.normalized_relationships_from_content
    original_spelling = vault.verify_bounded_markdown_spelling
    original_scandir = vault_module.os.scandir
    reads = []
    derived = []
    spellings = []
    scans = {}

    def snapshot():
        observed = original_snapshot()
        assert observed.complete and observed.resolution_complete
        alias.unlink()
        alias.symlink_to(outside)
        facts = {fact.path: fact for fact in observed.path_facts}
        assert vault.verify_bounded_markdown_path(facts["Source.md"])
        assert vault.verify_bounded_markdown_path(facts["Target.md"])
        return observed

    def read(path, **kwargs):
        result = original_read(path, **kwargs)
        reads.append(result.path)
        return result

    def derive(content, *, source_path, snapshot):
        derived.append(source_path)
        return original_derive(content, source_path=source_path, snapshot=snapshot)

    def verify_spelling(fact):
        result = original_spelling(fact)
        spellings.append((fact.path, result))
        return result

    def scandir(directory):
        path = (vault_module.os.readlink(f"/proc/self/fd/{directory}")
                if isinstance(directory, int) else str(directory))
        scans[path] = scans.get(path, 0) + 1
        return original_scandir(directory)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(vault, "bounded_markdown_snapshot", snapshot)
            patch.setattr(vault, "read_verified_markdown_snapshot", read)
            patch.setattr(service._relationships, "normalized_relationships_from_content", derive)
            patch.setattr(vault, "verify_bounded_markdown_spelling", verify_spelling)
            patch.setattr(vault_module.os, "scandir", scandir)
            result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
        assert result.scan.state == "partial"
        assert result.scan.reasons == ("relationship_unavailable",)
        assert reads == ["Source.md", "Target.md"]
        assert "Source.md" in derived
        assert ("aliases/Alias.md", False) in spellings
        assert scans == {str(tmp_path): 1, str(tmp_path / "aliases"): 1}
        assert not any(finding.kind in {"isolated_note", "missing_relationship_target"}
                       for finding in result.findings)
        assert str(outside) not in repr(result)
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlink spelling")
def test_qualified_directory_alias_resolves_both_dialects_without_rescans(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    content = "[[AliasFolder/Live]] [Live](AliasFolder/Live.md) [[AliasFolder/Live]]"
    write(tmp_path, "Source.md", content)
    write(tmp_path, "Canonical/Live.md", "body")
    (tmp_path / "AliasFolder").symlink_to(tmp_path / "Canonical", target_is_directory=True)
    service, vault, _ = service_for(tmp_path)
    original_snapshot = vault.bounded_markdown_snapshot
    original_scandir = vault_module.os.scandir
    snapshots = []
    scans = {}

    def snapshot():
        result = original_snapshot()
        snapshots.append(result)
        return result

    def scandir(directory):
        path = (vault_module.os.readlink(f"/proc/self/fd/{directory}")
                if isinstance(directory, int) else str(directory))
        scans[path] = scans.get(path, 0) + 1
        return original_scandir(directory)

    with monkeypatch.context() as patch:
        patch.setattr(vault, "bounded_markdown_snapshot", snapshot)
        patch.setattr(vault_module.os, "scandir", scandir)
        result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
        resolution = service._relationships.normalized_resolution_snapshot(snapshots[0].candidates)
        relationships = service._relationships.normalized_relationships_from_content(
            content, source_path="Source.md", snapshot=resolution,
        )

    assert snapshots[0].paths == ("Canonical/Live.md", "Source.md")
    assert tuple(fact.path for fact in snapshots[0].candidates.directory_alias_facts) == (
        "AliasFolder",
    )
    assert result.scan.state == "complete" and result.scan.reasons == ()
    assert [(edge.origin, edge.resolved_path, edge.resolution) for edge in relationships] == [
        ("obsidian_wikilink", "Canonical/Live.md", "resolved"),
        ("markdown_link", "Canonical/Live.md", "resolved"),
        ("obsidian_wikilink", "Canonical/Live.md", "resolved"),
    ]
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)
    assert scans == {str(tmp_path): 1, str(tmp_path / "Canonical"): 1}


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlink spelling")
def test_qualified_directory_alias_wrong_case_remains_missing(tmp_path):
    write(tmp_path, "Source.md", "[[aliasfolder/Live]] [[AliasFolder/live]]")
    write(tmp_path, "Canonical/Live.md", "body")
    (tmp_path / "AliasFolder").symlink_to(tmp_path / "Canonical", target_is_directory=True)
    service, _, _ = service_for(tmp_path)
    result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
    assert result.scan.state == "complete" and result.scan.reasons == ()
    assert [(finding.primary_path, finding.kind) for finding in result.findings
            if finding.kind == "missing_relationship_target"] == [
        ("Source.md", "missing_relationship_target"),
        ("Source.md", "missing_relationship_target"),
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlink spelling")
def test_qualified_directory_alias_outside_selected_universe_is_unavailable(tmp_path):
    write(tmp_path, "Source.md", "[[AliasFolder/Hidden]]")
    write(tmp_path, ".git/Hidden.md", "excluded")
    (tmp_path / "AliasFolder").symlink_to(tmp_path / ".git", target_is_directory=True)
    service, vault, _ = service_for(tmp_path)
    assert vault.bounded_markdown_snapshot().paths == ("Source.md",)
    result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("relationship_unavailable",)
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory alias leaf race")
@pytest.mark.parametrize("replacement", ["contained", "deleted", "escaping", "regular"])
def test_qualified_missing_leaf_rechecks_raced_directory_alias(
    tmp_path, monkeypatch, replacement,
):
    from app.services import vault as vault_module

    write(tmp_path, "Canonical/Live.md", "body")
    (tmp_path / "Empty").mkdir()
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    alias = aliases / "AliasFolder"
    alias.symlink_to(tmp_path / "Canonical", target_is_directory=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    snapshot = vault.bounded_markdown_snapshot()
    facts = {fact.path: fact for fact in snapshot.candidates.directory_alias_facts}
    original_lstat = vault_module.os.lstat
    original_verify = vault.verify_bounded_directory_alias_fact
    checks = []
    leaf = alias / "Live.md"
    raced = False

    def lstat(path, *args, **kwargs):
        nonlocal raced
        if Path(path) == leaf and not raced:
            raced = True
            alias.unlink()
            if replacement == "contained":
                alias.symlink_to(tmp_path / "Empty", target_is_directory=True)
            elif replacement == "escaping":
                alias.symlink_to(outside, target_is_directory=True)
            elif replacement == "regular":
                alias.write_text("replacement", encoding="utf-8")
        return original_lstat(path, *args, **kwargs)

    def verify(fact):
        result = original_verify(fact)
        checks.append(result)
        return result

    try:
        with monkeypatch.context() as patch:
            patch.setattr(vault_module.os, "lstat", lstat)
            patch.setattr(vault, "verify_bounded_directory_alias_fact", verify)
            with pytest.raises(NoteUnavailableError, match="relationship_unavailable"):
                vault.verify_snapshot_qualified_markdown_path(
                    "aliases/AliasFolder/Live.md", facts, frozenset(snapshot.paths),
                )
        assert raced and checks[0] is True
        if replacement != "regular":
            assert checks[-1] is False and len(checks) >= 2
    finally:
        outside.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory alias leaf race")
@pytest.mark.parametrize("content", [
    "[[aliases/AliasFolder/Live]]", "[Live](aliases/AliasFolder/Live.md)",
])
def test_hygiene_qualified_leaf_race_withholds_absence_for_both_dialects(
    tmp_path, monkeypatch, content,
):
    from app.services import vault as vault_module

    write(tmp_path, "Source.md", content)
    write(tmp_path, "Canonical/Live.md", "body")
    (tmp_path / "Empty").mkdir()
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    alias = aliases / "AliasFolder"
    alias.symlink_to(tmp_path / "Canonical", target_is_directory=True)
    service, vault, _ = service_for(tmp_path)
    original_lstat = vault_module.os.lstat
    original_read = vault.read_verified_markdown_snapshot
    original_verify = vault.verify_bounded_directory_alias_fact
    leaf = alias / "Live.md"
    reads = []
    checks = []
    raced = False

    def lstat(path, *args, **kwargs):
        nonlocal raced
        if Path(path) == leaf and not raced:
            raced = True
            alias.unlink()
            alias.symlink_to(tmp_path / "Empty", target_is_directory=True)
        return original_lstat(path, *args, **kwargs)

    def read(path, **kwargs):
        result = original_read(path, **kwargs)
        reads.append(result.path)
        return result

    def verify(fact):
        result = original_verify(fact)
        checks.append(result)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "lstat", lstat)
        patch.setattr(vault, "read_verified_markdown_snapshot", read)
        patch.setattr(vault, "verify_bounded_directory_alias_fact", verify)
        result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
    assert raced and checks[0] is True and checks[-1] is False
    assert reads == ["Canonical/Live.md", "Source.md"]
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("relationship_unavailable",)
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory alias stable absence")
def test_hygiene_qualified_directory_alias_stable_absence_is_missing(tmp_path):
    write(tmp_path, "Source.md", (
        "[[aliases/AliasFolder/Missing]] [Missing](aliases/AliasFolder/Missing.md)"
    ))
    write(tmp_path, "Canonical/Live.md", "body")
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    (aliases / "AliasFolder").symlink_to(tmp_path / "Canonical", target_is_directory=True)
    service, _, _ = service_for(tmp_path)
    result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
    assert result.scan.state == "complete" and result.scan.reasons == ()
    assert [(finding.primary_path, finding.kind) for finding in result.findings
            if finding.kind == "missing_relationship_target"] == [
        ("Source.md", "missing_relationship_target"),
        ("Source.md", "missing_relationship_target"),
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlink race")
def test_qualified_directory_alias_escape_after_discovery_is_unavailable(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "Source.md", "[[aliases/AliasFolder/Live]]")
    write(tmp_path, "Canonical/Live.md", "body")
    aliases = tmp_path / "aliases"
    aliases.mkdir()
    alias = aliases / "AliasFolder"
    alias.symlink_to(tmp_path / "Canonical", target_is_directory=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    write(outside, "Live.md", "outside")
    service, vault, _ = service_for(tmp_path)
    original_snapshot = vault.bounded_markdown_snapshot
    original_read = vault.read_verified_markdown_snapshot
    original_scandir = vault_module.os.scandir
    reads = []
    scans = {}

    def snapshot():
        observed = original_snapshot()
        assert observed.complete and observed.resolution_complete
        alias.unlink()
        alias.symlink_to(outside, target_is_directory=True)
        return observed

    def read(path, **kwargs):
        result = original_read(path, **kwargs)
        reads.append(result.path)
        return result

    def scandir(directory):
        path = (vault_module.os.readlink(f"/proc/self/fd/{directory}")
                if isinstance(directory, int) else str(directory))
        scans[path] = scans.get(path, 0) + 1
        return original_scandir(directory)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(vault, "bounded_markdown_snapshot", snapshot)
            patch.setattr(vault, "read_verified_markdown_snapshot", read)
            patch.setattr(vault_module.os, "scandir", scandir)
            result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=0))
        assert result.scan.reasons == ("relationship_unavailable",)
        assert result.scan.state == "partial"
        assert reads == ["Canonical/Live.md", "Source.md"]
        assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                       for finding in result.findings)
        assert scans == {
            str(tmp_path): 1, str(tmp_path / "Canonical"): 1, str(aliases): 1,
        }
    finally:
        (outside / "Live.md").unlink(missing_ok=True)
        outside.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX case-sensitive Markdown discovery")
def test_multiple_md_aliases_are_one_canonical_note_on_posix(tmp_path, monkeypatch):
    write(tmp_path, "Source.md", "[[A]] [[B]]")
    write(tmp_path, "Target.MD", "[[A]]")
    try:
        (tmp_path / "A.md").symlink_to(tmp_path / "Target.MD")
        (tmp_path / "B.md").symlink_to(tmp_path / "Target.MD")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    service, vault, _ = service_for(tmp_path)
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete and snapshot.resolution_complete
    assert snapshot.paths == ("Source.md", "Target.MD")
    assert [(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates] == [
        ("A.md", "Target.MD"),
        ("B.md", "Target.MD"),
        ("Source.md", "Source.md"),
    ]
    original_read = vault.read_verified_markdown_snapshot
    reads: list[str] = []

    def read(path, **kwargs):
        reads.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(vault, "read_verified_markdown_snapshot", read)
    first = service.scan()
    assert reads == ["Source.md", "Target.MD"]
    assert service.scan() == first
    assert first.scan.state == "complete"
    assert not any(finding.kind in {"ambiguous_relationship_target", "isolated_note"}
                   for finding in first.findings)


def test_frontmatter_aliases_body_and_privacy(tmp_path):
    write(tmp_path, "A.md", "---\naliases: [same, same, other, '  ']\ntags: [ok, ' ']\n---\n")
    write(tmp_path, "B.md", "---\naliases: [other, same]\n---\nbody")
    write(tmp_path, "Invalid.md", "---\na: 1\na: 2\n---\n")
    write(tmp_path, "Fields.md", "---\naliases: [ok, 3]\ntags: 3\n---\nbody")
    write(tmp_path, "Bare.md", "\ufeff \n\t")
    service, _, _ = service_for(tmp_path)
    result = service.scan()
    kinds = [finding.kind for finding in result.findings]
    assert kinds.count("invalid_frontmatter") == 1
    assert kinds.count("invalid_portable_field") == 2
    assert kinds.count("empty_portable_field_value") == 2
    assert kinds.count("empty_authored_body") == 2
    assert kinds.count("duplicate_alias_in_note") == 1
    assert kinds.count("colliding_alias") == 4
    repeated = next(f for f in result.findings if f.kind == "duplicate_alias_in_note")
    assert repeated.evidence.source_indices == (0, 1)
    a_collisions = [f for f in result.findings if f.kind == "colliding_alias" and f.primary_path == "A.md"]
    assert sorted(f.evidence.source_index for f in a_collisions) == [0, 2]
    assert all(f.evidence.peer_count == 1 for f in a_collisions)
    assert "same" not in repr(result) and "other" not in repr(result)
    assert all(f.primary_path != "Invalid.md" or f.kind != "empty_authored_body" for f in result.findings)
    with pytest.raises(FrozenInstanceError):
        result.scan.state = "partial"


def test_partial_scan_withholds_isolation_and_cross_note_aliases(tmp_path, monkeypatch):
    for path in ("A.md", "B.md"):
        write(tmp_path, path, "---\naliases: [x]\n---\nbody")
    service, vault, _ = service_for(tmp_path)
    actual = vault.bounded_markdown_snapshot
    monkeypatch.setattr(vault, "bounded_markdown_snapshot", lambda: BoundedMarkdownSnapshot(
        actual().paths, actual().candidates, False, path_facts=actual().path_facts,
    ))
    result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=2, semantic_candidates=True))
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("path_ceiling",)
    assert result.candidates.state == "partial"
    assert result.candidates.reasons == ("scan_partial", "semantic_unavailable")
    assert not any(f.kind in {"isolated_note", "colliding_alias", "near_duplicate_candidate"} for f in result.findings)


def test_exact_title_batch_uses_one_enumeration_and_one_content_read(tmp_path, monkeypatch):
    for path in ("A/Same.md", "B/Same.md", "C/Same.md"):
        write(tmp_path, path, "body")
    service, vault, duplicate = service_for(tmp_path)
    calls = {"enumerate": 0, "read": 0, "batch": 0}
    original_enumerate = vault.bounded_markdown_snapshot
    original_read = vault.read_verified_markdown_snapshot
    original_batch = duplicate.find_bounded_batch
    from app.services import duplicate_candidates as duplicate_module
    original_normalize = duplicate_module.normalize_note_title
    calls["normalize"] = 0

    def enumerate_once():
        calls["enumerate"] += 1
        return original_enumerate()

    def read_once(path, **kwargs):
        calls["read"] += 1
        return original_read(path, **kwargs)

    def batch_once(**kwargs):
        calls["batch"] += 1
        return original_batch(**kwargs)

    def normalize_once(title):
        calls["normalize"] += 1
        return original_normalize(title)

    monkeypatch.setattr(vault, "bounded_markdown_snapshot", enumerate_once)
    monkeypatch.setattr(vault, "read_verified_markdown_snapshot", read_once)
    monkeypatch.setattr(duplicate, "find_bounded_batch", batch_once)
    monkeypatch.setattr(duplicate_module, "normalize_note_title", normalize_once)
    monkeypatch.setattr(vault, "live_markdown_paths", lambda **_kwargs: pytest.fail("unbounded enumeration"))
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset(), duplicate_source_limit=3))
    assert calls == {"enumerate": 1, "read": 3, "batch": 1, "normalize": 6}
    assert result.candidates.state == "complete"
    assert len(result.findings) == 3
    assert all(f.kind == "duplicate_candidate" and len(f.related_paths) == 1 for f in result.findings)
    assert [(f.primary_path, f.related_paths) for f in result.findings] == [
        ("A/Same.md", ("B/Same.md",)),
        ("A/Same.md", ("C/Same.md",)),
        ("B/Same.md", ("C/Same.md",)),
    ]


def test_alias_peer_and_finding_bounds(tmp_path):
    for index in range(12):
        write(tmp_path, f"N{index:02}.md", "---\naliases: [shared]\n---\nbody")
    service, _, _ = service_for(tmp_path)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset({"aliases"}), finding_limit=1))
    assert len(result.findings) == 1 and result.findings_truncated
    finding = result.findings[0]
    assert finding.kind == "colliding_alias"
    assert finding.evidence.peer_count == 11
    assert finding.evidence.related_paths_truncated
    assert len(finding.related_paths) == 10


def test_bounded_snapshot_10000_complete_and_10001_partial(tmp_path, monkeypatch):
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    names = [f"N{i:05}.md" for i in range(10_001)]
    def first_discoveries():
        yield from ((name, False) for name in names[:10_000])

    monkeypatch.setattr(vault, "_sorted_markdown_discoveries", first_discoveries)
    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", lambda path, **_kwargs: type(
        "Result", (), {"resolved_path": path, "resolution": "resolved"},
    )())
    monkeypatch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
        path, (0, 0, 0, 0), (),
    ))
    monkeypatch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
        path, (0, 0, 0, 0), (),
    ))
    assert vault.bounded_markdown_snapshot().complete
    def all_discoveries():
        yield from ((name, False) for name in names)

    monkeypatch.setattr(vault, "_sorted_markdown_discoveries", all_discoveries)
    result = vault.bounded_markdown_snapshot()
    assert not result.complete and len(result.paths) == 10_000


@pytest.mark.parametrize("file_count,complete", [
    (10_000, True), (10_001, False), (20_000, False),
])
def test_large_directory_is_listed_once_with_bounded_sorted_result(
    tmp_path, monkeypatch, file_count, complete,
):
    from app.services import vault as vault_module

    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    names = [f"N{index:05}.md" for index in reversed(range(file_count))]
    calls = 0
    observed = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def is_dir(self, *, follow_symlinks):
            return False

        def is_symlink(self):
            return False

    class Listing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            nonlocal observed
            for name in names:
                observed += 1
                yield Entry(name)

    def scandir(_directory):
        nonlocal calls
        calls += 1
        return Listing()

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(
            vault, "verify_existing_markdown_path_result",
            lambda path, **_kwargs: MarkdownPathVerification("resolved", path),
        )
        patch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
            path, (0, 0, 0, 0), (),
        ))
        patch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
            path, (0, 0, 0, 0), (),
        ))
        snapshot = vault.bounded_markdown_snapshot()
    assert calls == 1
    assert observed == file_count
    assert snapshot.complete is complete
    assert len(snapshot.paths) == 10_000
    assert snapshot.paths == tuple(f"N{index:05}.md" for index in range(10_000))


def test_early_alias_does_not_displace_canonical_first_10000(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    names = ["A-Alias.md", *(f"N{index:05}.md" for index in range(10_000)), "Z-Target.md"]
    calls = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def is_dir(self, *, follow_symlinks):
            return False

        def is_symlink(self):
            return self.name == "A-Alias.md"

    class Listing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return (Entry(name) for name in reversed(names))

    def scandir(_directory):
        nonlocal calls
        calls += 1
        return Listing()

    monkeypatch.setattr(vault_module.os, "scandir", scandir)
    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", lambda path, **_kwargs:
                        MarkdownPathVerification(
                            "resolved", "Z-Target.md" if path == "A-Alias.md" else path,
                        ))
    monkeypatch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
        path, (0, 0, 0, 0), (),
    ))
    monkeypatch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
        path, (0, 0, 0, 0), (), path == "A-Alias.md",
    ))
    snapshot = vault.bounded_markdown_snapshot()
    assert calls == 1
    assert not snapshot.complete and not snapshot.enumeration_unavailable
    assert snapshot.paths == tuple(f"N{index:05}.md" for index in range(10_000))
    assert tuple(fact.path for fact in snapshot.path_facts) == snapshot.paths
    assert all(candidate.canonical_path != "Z-Target.md"
               for candidate in snapshot.candidates.live_candidates)
    assert "Z-Target.md" not in snapshot.paths


@pytest.mark.skipif(os.name == "nt", reason="POSIX alias-only canonical target")
def test_alias_only_canonical_identity_displaces_later_note_at_ceiling(tmp_path, monkeypatch):
    ordinary = tuple(f"N{index:05}.md" for index in range(10_000))
    alias = "z/Alias.md"
    target = "A/Target.MD"
    discoveries = (*((path, False) for path in ordinary), (alias, True))
    service, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: target if path == alias else None,
        lambda _path: "body",
    )
    snapshot = vault.bounded_markdown_snapshot()
    expected = (target, *ordinary[:-1])
    assert not snapshot.complete and not snapshot.enumeration_unavailable
    assert snapshot.paths == expected
    assert len(snapshot.paths) == 10_000
    assert tuple(fact.path for fact in snapshot.path_facts) == expected
    assert [(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == alias] == [(alias, target)]
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("path_ceiling",)
    assert result.scan.eligible_paths == result.scan.inspected_notes == 10_000


def _model_bounded_hygiene(monkeypatch, root, discoveries, alias_target, content_for):
    service, vault, _ = service_for(root)
    def walk():
        yield from discoveries

    monkeypatch.setattr(vault, "_sorted_markdown_discoveries", walk)
    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", lambda path, **_kwargs:
                        MarkdownPathVerification("resolved", alias_target(path) or path))
    monkeypatch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
        path, (0, 0, 0, 0), (),
    ))
    monkeypatch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
        path, (0, 0, 0, 0), (), alias_target(path) is not None,
    ))
    monkeypatch.setattr(vault, "verify_bounded_markdown_path", lambda _fact: True)
    monkeypatch.setattr(vault, "read_verified_markdown_snapshot", lambda path, **_kwargs:
                        NoteReadResult(path, content_for(path)))
    return service, vault


@pytest.mark.skipif(os.name == "nt", reason="POSIX late alias-only canonical target")
def test_late_alias_replaces_canonical_cutoff_after_10001_ordinary_notes(tmp_path, monkeypatch):
    ordinary = tuple(f"N{index:05}.md" for index in range(10_001))
    target = "A/Target.MD"
    alias = "z/Alias.md"
    discoveries = (*((path, False) for path in ordinary), (alias, True))
    reads = []
    service, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: target if path == alias else None,
        lambda path: reads.append(path) or "body",
    )
    snapshot = vault.bounded_markdown_snapshot()
    expected = (target, *ordinary[:9_999])
    assert snapshot.paths == expected
    assert tuple(fact.path for fact in snapshot.path_facts) == expected
    assert len(snapshot.paths) == len(set(snapshot.paths)) == 10_000
    assert {candidate.canonical_path for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == candidate.canonical_path} == set(expected) - {target}
    assert not snapshot.complete and not snapshot.enumeration_unavailable
    assert [(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == alias] == [(alias, target)]

    result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("path_ceiling",)
    assert len(reads) == len(set(reads)) == 10_000
    assert set(reads) == set(expected)


@pytest.mark.skipif(os.name == "nt", reason="POSIX late duplicate aliases")
def test_late_duplicate_aliases_and_evicted_identity_do_not_reenter(tmp_path, monkeypatch):
    ordinary = tuple(f"N{index:05}.md" for index in range(10_001))
    target = "A/Target.MD"
    aliases = ("z/Alias1.md", "z/Alias2.md", "z/Alias3.md")
    evicted_alias = "z/Evicted.md"
    discoveries = (
        *((path, False) for path in ordinary),
        *((path, True) for path in aliases),
        (evicted_alias, True),
    )
    service, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: target if path in aliases else ordinary[9_999] if path == evicted_alias else None,
        lambda _path: "body",
    )
    first = vault.bounded_markdown_snapshot()
    second = vault.bounded_markdown_snapshot()
    expected = (target, *ordinary[:9_999])
    assert first == second
    assert first.paths == expected
    assert tuple(fact.path for fact in first.path_facts) == expected
    assert len(set(first.paths)) == 10_000
    assert ordinary[9_999] not in first.paths
    assert {candidate.canonical_path for candidate in first.candidates.live_candidates
            if candidate.discovered_path == candidate.canonical_path} == set(expected) - {target}
    assert len(first.candidates.live_candidates) <= 20_000
    assert [(candidate.discovered_path, candidate.canonical_path)
            for candidate in first.candidates.live_candidates
            if candidate.discovered_path in aliases] == [
        (alias, target) for alias in aliases
    ]
    assert not first.complete and not first.enumeration_unavailable
    assert service.scan(KnowledgeHygieneRequest(groups=frozenset())).scan.reasons == ("path_ceiling",)


@pytest.mark.skipif(os.name == "nt", reason="POSIX real alias-only target")
def test_real_late_directory_alias_enters_global_top_k_and_full_scan(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    ordinary = tuple(f"N{index:05}.md" for index in range(10_001))
    for name in ordinary:
        (tmp_path / name).touch()
    write(tmp_path, "A/Target.MD", "[[Alias]]")
    alias = tmp_path / "z/Alias.md"
    alias.parent.mkdir()
    alias.symlink_to(tmp_path / "A/Target.MD")
    service, vault, _ = service_for(tmp_path)
    original_snapshot = vault.bounded_markdown_snapshot
    original_scandir = vault_module.os.scandir
    original_read = vault.read_verified_markdown_snapshot
    snapshots = []
    scans = {}
    reads = []

    def snapshot():
        result = original_snapshot()
        snapshots.append(result)
        return result

    def scandir(directory):
        path = (vault_module.os.readlink(f"/proc/self/fd/{directory}")
                if isinstance(directory, int) else str(directory))
        scans[path] = scans.get(path, 0) + 1
        return original_scandir(directory)

    def read(path, **kwargs):
        reads.append(path)
        return original_read(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(vault, "bounded_markdown_snapshot", snapshot)
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(vault, "read_verified_markdown_snapshot", read)
        result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))

    expected = ("A/Target.MD", *ordinary[:9_999])
    selected = snapshots[0]
    assert selected.paths == expected
    assert tuple(fact.path for fact in selected.path_facts) == expected
    assert not selected.complete and not selected.enumeration_unavailable
    assert ("z/Alias.md", "A/Target.MD") in {
        (candidate.discovered_path, candidate.canonical_path)
        for candidate in selected.candidates.live_candidates
    }
    resolution = service._relationships.normalized_resolution_snapshot(selected.candidates)
    occurrences = service._relationships.normalized_relationships_from_content(
        "[[Alias]]", source_path="A/Target.MD", snapshot=resolution,
    )
    assert len(occurrences) == 1
    assert occurrences[0].resolved_path == "A/Target.MD"
    assert scans == {str(tmp_path): 1, str(tmp_path / "A"): 1, str(tmp_path / "z"): 1}
    assert len(reads) == len(set(reads)) == 10_000
    assert set(reads) == set(expected)
    assert result.scan.state == "partial" and result.scan.reasons == ("path_ceiling",)
    assert result.scan.eligible_paths == result.scan.inspected_notes == 10_000
    assert not any(finding.kind in {"isolated_note", "missing_relationship_target"}
                   for finding in result.findings)


def test_alias_overflow_preserves_late_selected_mandatory_spelling(tmp_path, monkeypatch):
    aliases = tuple(f"Alias{index:05}.md" for index in range(10_002))
    late = "z/Late.md"
    target_a, target_b = "A/Target.MD", "B/Target.MD"
    _, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path,
        (*((path, True) for path in reversed(aliases)), (late, True)),
        lambda path: target_b if path == late else target_a,
        lambda _path: "body",
    )
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.paths == (target_a, target_b)
    assert snapshot.complete and not snapshot.resolution_complete
    assert tuple(fact.path for fact in snapshot.path_facts) == snapshot.paths
    candidates = snapshot.candidates.live_candidates
    assert (aliases[0], target_a) in {
        (candidate.discovered_path, candidate.canonical_path) for candidate in candidates
    }
    assert (late, target_b) in {
        (candidate.discovered_path, candidate.canonical_path) for candidate in candidates
    }
    assert len(candidates) == 10_002  # two mandatory plus 10,000 extra aliases
    assert sum(candidate.canonical_path == target_a for candidate in candidates) == 10_001


def test_eviction_removes_alias_only_mandatory_and_extra_evidence(tmp_path, monkeypatch):
    aliases = ("x/Alias1.md", "x/Alias2.md")
    late = "z/Late.md"
    target_x, target_y = "Z/Target.MD", "0/Target.MD"
    discoveries = (
        *((path, True) for path in aliases),
        ("A.md", False), ("B.md", False), (late, True),
    )
    _, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: target_x if path in aliases else target_y if path == late else None,
        lambda _path: "body",
    )
    snapshot = vault.bounded_markdown_snapshot(limit=2)
    assert snapshot.paths == (target_y, "A.md")
    assert tuple(fact.path for fact in snapshot.path_facts) == snapshot.paths
    assert {(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates} == {
        (late, target_y), ("A.md", "A.md"),
    }
    assert not snapshot.complete and not snapshot.enumeration_unavailable


def test_exact_10000_notes_with_early_alias_keep_complete_resolution(tmp_path, monkeypatch):
    canonical = [
        "A/ANote.md", "A/aNote.md",
        *(f"A/N{index:05}.md" for index in range(9_997)),
        "z/Late.md",
    ]
    canonical.sort(key=lambda path: (path.casefold(), path))
    alias = "A/0Alias.md"
    discoveries = ((alias, True), *((path, False) for path in canonical))
    service, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: "z/Late.md" if path == alias else None,
        lambda path: (
            "[[Missing]]" if path == "A/ANote.md" else
            "body" if path == "A/aNote.md" else
            "[[0Alias]]" if path == "z/Late.md" else
            f"[[{Path(path).stem}]]"
        ),
    )
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete and snapshot.resolution_complete
    assert snapshot.paths == tuple(canonical)
    assert len(snapshot.paths) == 10_000
    own_spellings = {
        candidate.discovered_path for candidate in snapshot.candidates.live_candidates
        if candidate.discovered_path == candidate.canonical_path
    }
    assert own_spellings == set(canonical)
    assert [(candidate.discovered_path, candidate.canonical_path)
            for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == alias] == [(alias, "z/Late.md")]

    result = service.scan()
    assert result.scan.state == "complete"
    assert result.scan.eligible_paths == result.scan.inspected_notes == 10_000
    assert "relationship_unavailable" not in result.scan.reasons
    assert [(finding.primary_path, finding.kind) for finding in result.findings] == [
        ("A/ANote.md", "isolated_note"),
        ("A/ANote.md", "missing_relationship_target"),
        ("A/aNote.md", "isolated_note"),
    ]


def test_alias_budget_overflow_keeps_canonical_evidence_and_withholds_absence(tmp_path, monkeypatch):
    aliases = tuple(f"A/0Alias{index:05}.md" for index in range(10_001))
    alias_set = frozenset(aliases)
    canonical = ("A/Source.md", "z/Target.md")
    discoveries = (*((path, True) for path in aliases), *((path, False) for path in canonical))
    service, vault = _model_bounded_hygiene(
        monkeypatch, tmp_path, discoveries,
        lambda path: "z/Target.md" if path in alias_set else None,
        lambda path: "[[Missing]]" if path == "A/Source.md" else "body",
    )
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete and not snapshot.resolution_complete
    assert snapshot.paths == canonical
    assert len(snapshot.candidates.live_candidates) == 10_002
    assert {candidate.discovered_path for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == candidate.canonical_path} == set(canonical)
    assert sum(candidate.discovered_path in alias_set
               for candidate in snapshot.candidates.live_candidates) == 10_000

    result = service.scan()
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("relationship_unavailable",)
    assert not any(finding.kind in {"missing_relationship_target", "isolated_note"}
                   for finding in result.findings)


def test_directory_alias_overflow_does_not_interrupt_canonical_walk(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    names = [*(f"A{index:05}.md" for index in range(10_002)), "Z1.md", "Z2.md"]
    calls = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def is_dir(self, *, follow_symlinks):
            return False

        def is_symlink(self):
            return self.name.startswith("A")

    class Listing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return (Entry(name) for name in reversed(names))

    def scandir(_directory):
        nonlocal calls
        calls += 1
        return Listing()

    monkeypatch.setattr(vault_module.os, "scandir", scandir)
    monkeypatch.setattr(vault, "verify_existing_markdown_path_result", lambda path, **_kwargs:
                        MarkdownPathVerification(
                            "resolved", "Z2.md" if path.startswith("A") else path,
                        ))
    monkeypatch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
        path, (0, 0, 0, 0), (),
    ))
    monkeypatch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
        path, (0, 0, 0, 0), (), path.startswith("A"),
    ))
    snapshot = vault.bounded_markdown_snapshot()
    assert calls == 1
    assert snapshot.complete and not snapshot.resolution_complete
    assert not snapshot.enumeration_unavailable
    assert snapshot.paths == ("Z1.md", "Z2.md")
    assert len(snapshot.candidates.live_candidates) == 10_002
    assert {candidate.discovered_path for candidate in snapshot.candidates.live_candidates
            if candidate.discovered_path == candidate.canonical_path} == set(snapshot.paths)


def test_full_scan_10000_notes_lists_main_directory_once(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    service, vault, _ = service_for(tmp_path)
    names = [f"N{index:05}.md" for index in reversed(range(10_000))]
    listings = 0
    reads = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def is_dir(self, *, follow_symlinks):
            return False

        def is_symlink(self):
            return False

    class Listing:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return (Entry(name) for name in names)

    def scandir(directory):
        nonlocal listings
        listings += 1
        return Listing()

    def read(path, *, fact):
        nonlocal reads
        assert fact.path == path
        reads += 1
        return NoteReadResult(path, "body")

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(vault, "verify_existing_markdown_path_result", lambda path, **_kwargs:
                      MarkdownPathVerification("resolved", path))
        patch.setattr(vault, "_bounded_path_fact", lambda path: BoundedMarkdownPathFact(
            path, (0, 0, 0, 0), (),
        ))
        patch.setattr(vault, "_bounded_spelling_fact", lambda path: BoundedMarkdownSpellingFact(
            path, (0, 0, 0, 0), (),
        ))
        patch.setattr(vault, "verify_bounded_markdown_path", lambda _fact: True)
        patch.setattr(vault, "read_verified_markdown_snapshot", read)
        patch.setattr(vault, "verify_existing_markdown_path", lambda *_args, **_kwargs:
                      pytest.fail("legacy sibling enumeration"))
        result = service.scan(KnowledgeHygieneRequest(
            groups=frozenset(), duplicate_source_limit=20,
        ))
    assert listings == 1
    assert reads == 10_000
    assert result.scan.state == "complete"
    assert result.scan.eligible_paths == result.scan.inspected_notes == 10_000
    assert result.candidates.source_notes == 20


def test_full_nested_scan_does_not_rescan_parent_directories(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "A.md", (
        "[[Folder/Target]] [target](Folder/Target.md) "
        "[relative](Folder/Deep/../Target.md)"
    ))
    write(tmp_path, "Folder/Target.md", "body")
    for index in range(24):
        write(tmp_path, f"Folder/Deep/N{index:02}.md", "body")
    service, vault, _ = service_for(tmp_path)
    original_scandir = vault_module.os.scandir
    original_read = vault.read_verified_markdown_snapshot
    scans: dict[str, int] = {}
    reads = 0

    def scandir(directory):
        key = (
            vault_module.os.readlink(f"/proc/self/fd/{directory}")
            if isinstance(directory, int) else str(directory)
        )
        scans[key] = scans.get(key, 0) + 1
        return original_scandir(directory)

    def read(path, **kwargs):
        nonlocal reads
        reads += 1
        return original_read(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(vault, "read_verified_markdown_snapshot", read)
        result = service.scan(KnowledgeHygieneRequest(duplicate_source_limit=20))
    assert scans == {
        str(tmp_path): 1,
        str(tmp_path / "Folder"): 1,
        str(tmp_path / "Folder/Deep"): 1,
    }
    assert reads == result.scan.eligible_paths == 26
    assert result.scan.state == "complete"
    assert result.candidates.source_notes == 20
    assert not any(f.kind == "missing_relationship_target" for f in result.findings)


def test_candidate_mode_verifies_matches_without_sibling_rescans(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    for index in range(8):
        write(tmp_path, f"F{index}/Same.md", "body")
    service, _, _ = service_for(tmp_path)
    original_scandir = vault_module.os.scandir
    scans: dict[str, int] = {}

    def scandir(directory):
        key = (
            vault_module.os.readlink(f"/proc/self/fd/{directory}")
            if isinstance(directory, int) else str(directory)
        )
        scans[key] = scans.get(key, 0) + 1
        return original_scandir(directory)

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        result = service.scan(KnowledgeHygieneRequest(
            groups=frozenset(), duplicate_source_limit=1,
        ))
    assert scans == {str(tmp_path): 1, **{
        str(tmp_path / f"F{index}"): 1 for index in range(8)
    }}
    assert result.candidates.state == "complete"
    assert result.candidates.source_notes == 1
    assert len(result.findings) == 5
    assert all(finding.kind == "duplicate_candidate" for finding in result.findings)


def test_nested_tree_lists_each_directory_once_in_sorted_order(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "z.md", "body")
    write(tmp_path, "a.md", "body")
    write(tmp_path, "a/child.md", "body")
    write(tmp_path, "B/other.md", "body")
    write(tmp_path, ".obsidian/hidden.md", "body")
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    original = vault_module.os.scandir
    scans: dict[str, int] = {}

    def scandir(directory):
        if isinstance(directory, int):
            key = str(Path(vault_module.os.readlink(f"/proc/self/fd/{directory}")))
        else:
            key = str(directory)
        scans[key] = scans.get(key, 0) + 1
        return original(directory)

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete
    assert snapshot.paths == ("a.md", "a/child.md", "B/other.md", "z.md")
    assert scans == {
        str(tmp_path): 1,
        str(tmp_path / "a"): 1,
        str(tmp_path / "B"): 1,
    }


def test_partial_zero_findings_and_note_unavailable(tmp_path, monkeypatch):
    write(tmp_path, "Gone.md", "body")
    service, vault, _ = service_for(tmp_path)
    monkeypatch.setattr(
        vault, "read_verified_markdown_snapshot",
        lambda _path, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert not result.findings
    assert result.scan.state == "partial"
    assert result.scan.unavailable_notes == 1
    assert result.scan.reasons == ("note_unavailable",)


@pytest.mark.parametrize("status,kind", [
    ("compatible_ready", None),
    ("compatible_previous_refresh", "previous_compatible_index"),
    ("compatible_previous_error", "previous_compatible_index"),
    ("missing", "derived_index_unavailable"),
    ("incompatible", "derived_index_unavailable"),
    ("invalid_metadata", "derived_index_unavailable"),
    ("corrupt_storage", "derived_index_unavailable"),
    ("storage_unavailable", "derived_index_unavailable"),
    ("inspection_unavailable", "derived_index_unavailable"),
])
def test_derived_index_mapping(tmp_path, status, kind):
    semantic = SemanticStub(status)
    service, _, _ = service_for(tmp_path, semantic=semantic)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset(), inspect_derived_index=True))
    assert result.derived_index.status == status
    assert [f.kind for f in result.findings] == ([] if kind is None else [kind])
    assert all(f.primary_path is None and not f.related_paths for f in result.findings)
    assert semantic.calls == 1


def test_immutable_inspection_bytes_and_sidecars(tmp_path):
    db = tmp_path / "data" / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    assert semantic.inspect_hygiene_index() == "missing"
    assert not db.parent.exists()
    db.parent.mkdir()
    db.write_bytes(b"not a database")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "corrupt_storage"
    assert db.read_bytes() == before
    db.unlink()
    semantic.repository.prepare_index(semantic.index_signature)
    semantic.repository.set_metadata("index_state", "ready")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "compatible_ready"
    assert db.read_bytes() == before
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"existing sidecar")
    before_wal = wal.read_bytes()
    assert semantic.inspect_hygiene_index() == "inspection_unavailable"
    assert db.read_bytes() == before and wal.read_bytes() == before_wal
    assert not Path(str(db) + "-shm").exists()


def test_persisted_previous_states_do_not_infer_current_lifecycle_from_stale_flags(tmp_path):
    db = tmp_path / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    semantic.repository.prepare_index(semantic.index_signature)
    with semantic.repository.transaction() as session:
        session.replace_note(
            StoredNote("A.md", 1, 4, "hash", "then"),
            [StoredChunk("A.md", 0, None, "body", b"0000", 1)],
        )
    semantic.repository.set_metadata("index_state", "indexing")
    assert semantic.inspect_hygiene_index() == "inspection_unavailable"
    semantic._state_initialized = True
    semantic._search_available = True
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "inspection_unavailable"
    assert db.read_bytes() == before
    semantic.repository.set_metadata("index_state", "error")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "inspection_unavailable"
    assert db.read_bytes() == before
    service, _, _ = service_for(tmp_path, semantic=semantic)
    result = service.scan(KnowledgeHygieneRequest(
        groups=frozenset(), inspect_derived_index=True,
    ))
    assert result.derived_index.status == "inspection_unavailable"
    assert [finding.kind for finding in result.findings] == ["derived_index_unavailable"]


def test_external_persisted_transition_cannot_claim_previous_index(tmp_path):
    db = tmp_path / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    semantic.repository.prepare_index(semantic.index_signature)
    with semantic.repository.transaction() as session:
        session.replace_note(
            StoredNote("A.md", 1, 4, "hash", "then"),
            [StoredChunk("A.md", 0, None, "body", b"0000", 1)],
        )
    semantic.repository.set_metadata("index_state", "ready")
    semantic._state_initialized = True
    semantic._search_available = True
    assert semantic.inspect_hygiene_index() == "compatible_ready"
    external_repository = SemanticRepository(db)
    for external_state in ("indexing", "error"):
        external_repository.set_metadata("index_state", external_state)
        before = db.read_bytes()
        assert semantic.inspect_hygiene_index() == "inspection_unavailable"
        assert db.read_bytes() == before


def test_invalid_metadata_and_incompatible_storage_are_immutable(tmp_path):
    db = tmp_path / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    semantic.repository.prepare_index(semantic.index_signature)
    semantic.repository.set_metadata("index_state", "unknown")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "invalid_metadata"
    assert db.read_bytes() == before
    semantic.repository.set_metadata("index_signature", "different")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "invalid_metadata"
    assert db.read_bytes() == before
    semantic.repository.set_metadata("index_signature", "semantic-index-v2:{broken")
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "invalid_metadata"
    assert db.read_bytes() == before
    semantic.repository.set_metadata(
        "index_signature", semantic.index_signature.replace("\"model\":", "\"model\":\"other\",\"ignored\":"),
    )
    before = db.read_bytes()
    assert semantic.inspect_hygiene_index() == "incompatible"
    assert db.read_bytes() == before


def test_full_hygiene_scan_has_no_storage_or_markdown_writes(tmp_path, monkeypatch):
    write(tmp_path, "A.md", "---\naliases: [one, one]\n---\n[[Missing]]")
    db = tmp_path / "data" / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    service, _, _ = service_for(tmp_path, semantic=semantic)

    def snapshot():
        return {
            path.relative_to(tmp_path).as_posix(): (
                "directory" if path.is_dir() else path.read_bytes()
            )
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    monkeypatch.setattr(semantic.repository, "_connect", lambda: pytest.fail("write connection"))
    monkeypatch.setattr(semantic.repository, "set_metadata", lambda *_args: pytest.fail("metadata write"))
    monkeypatch.setattr(semantic, "_persist_state", lambda *_args: pytest.fail("lifecycle write"))
    monkeypatch.setattr(semantic, "_resolve_index_signature", lambda **_kwargs: pytest.fail("cache mutation"))
    monkeypatch.setattr(semantic, "_set_search_available", lambda *_args, **_kwargs: pytest.fail("state mutation"))
    result = service.scan(KnowledgeHygieneRequest(
        duplicate_source_limit=1, semantic_candidates=True, inspect_derived_index=True,
    ))
    assert result.scan.state == "complete"
    assert result.candidates.reasons == ("semantic_unavailable",)
    assert result.derived_index.status == "missing"
    assert snapshot() == before


def test_candidate_race_is_partial_and_five_per_source(tmp_path, monkeypatch):
    for index in range(8):
        write(tmp_path, f"F{index}/Same.md", "body")
    service, _, duplicate = service_for(tmp_path)
    original = duplicate.find_bounded_batch

    def candidate_races(**kwargs):
        (tmp_path / "F1/Same.md").unlink()
        return original(**kwargs)

    monkeypatch.setattr(duplicate, "find_bounded_batch", candidate_races)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset(), duplicate_source_limit=1))
    assert result.candidates.state == "partial"
    assert result.candidates.reasons == ("scan_partial", "candidate_unavailable")
    assert result.scan.reasons == ("note_unavailable",)
    assert len(result.findings) == 5
    assert "F1/Same.md" not in {f.related_paths[0] for f in result.findings}


def test_500_distinct_findings_are_sorted_before_truncation(tmp_path):
    write(tmp_path, "A.md", " ".join(f"[[Missing{i:03}]]" for i in range(501)))
    service, _, _ = service_for(tmp_path)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset({"relationships"})))
    assert len(result.findings) == 500 and result.findings_truncated
    assert [f.evidence.source_order for f in result.findings] == list(range(500))


def test_contained_symlink_alias_is_one_canonical_note(tmp_path):
    write(tmp_path, "Target.md", "body")
    try:
        (tmp_path / "Alias.md").symlink_to(tmp_path / "Target.md")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    service, vault, _ = service_for(tmp_path)
    assert vault.bounded_markdown_snapshot().paths == ("Target.md",)
    result = service.scan()
    assert result.scan.eligible_paths == result.scan.inspected_notes == 1
    assert [f.primary_path for f in result.findings if f.kind == "isolated_note"] == ["Target.md"]


def test_symlink_spellings_resolve_one_self_link_and_preserve_unsafe_alias(tmp_path, monkeypatch):
    write(tmp_path, "Target.md", "[[Target]] [[Unsafe]]")
    (tmp_path / "Folder").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("outside", encoding="utf-8")
    try:
        (tmp_path / "Folder/Target.md").symlink_to(tmp_path / "Target.md")
        (tmp_path / "Unsafe.md").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    try:
        service, vault, _ = service_for(tmp_path)
        from app.services import vault as vault_module

        original_scandir = vault_module.os.scandir
        scans: dict[str, int] = {}

        def scandir(directory):
            key = (
                vault_module.os.readlink(f"/proc/self/fd/{directory}")
                if isinstance(directory, int) else str(directory)
            )
            scans[key] = scans.get(key, 0) + 1
            return original_scandir(directory)

        with monkeypatch.context() as patch:
            patch.setattr(vault_module.os, "scandir", scandir)
            result = service.scan()
        assert scans == {str(tmp_path): 1, str(tmp_path / "Folder"): 1}
        bounded = vault.bounded_markdown_snapshot()
        assert bounded.paths == ("Target.md",)
        assert bounded.complete and bounded.resolution_complete
        assert vault.bounded_markdown_snapshot() == bounded
    finally:
        outside.unlink(missing_ok=True)
    assert result.scan.state == "complete"
    assert not any(f.kind == "ambiguous_relationship_target" for f in result.findings)
    assert not any(f.kind == "isolated_note" and f.primary_path == "Target.md" for f in result.findings)
    assert [(f.kind, f.evidence.source_order) for f in result.findings
            if f.primary_path == "Target.md"] == [("unsafe_relationship_target", 1)]


def test_distinct_canonical_targets_remain_ambiguous_with_symlink_alias(tmp_path):
    write(tmp_path, "First/Target.md", "body")
    write(tmp_path, "Second/Target.md", "body")
    write(tmp_path, "Source.md", "[[Target]]")
    (tmp_path / "Alias").mkdir()
    try:
        (tmp_path / "Alias/Target.md").symlink_to(tmp_path / "First/Target.md")
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    service, _, _ = service_for(tmp_path)
    result = service.scan()
    assert [(f.kind, f.primary_path) for f in result.findings
            if f.kind == "ambiguous_relationship_target"] == [
        ("ambiguous_relationship_target", "Source.md"),
    ]


def test_sorted_directory_windows_are_complete(tmp_path):
    for index in range(1_030):
        (tmp_path / f"N{index:04}.md").touch()
    vault = VaultService(vault_root=tmp_path, max_note_bytes=1_000_000)
    snapshot = vault.bounded_markdown_snapshot()
    assert snapshot.complete
    assert len(snapshot.paths) == 1_030
    assert snapshot.paths[0] == "N0000.md"
    assert snapshot.paths[-1] == "N1029.md"


@pytest.mark.parametrize("scenario,expected", [
    ("missing", "missing"),
    ("corrupt", "corrupt_storage"),
    ("invalid", "invalid_metadata"),
    ("ready", "compatible_ready"),
    ("sidecar", "inspection_unavailable"),
])
def test_derived_hygiene_scan_does_not_mutate_storage(tmp_path, monkeypatch, scenario, expected):
    write(tmp_path, "A.md", "body")
    db = tmp_path / "data" / "semantic.db"
    semantic = SemanticSearchService(
        vault_root=tmp_path, repository=SemanticRepository(db),
        embedding_fingerprint="embedding-v1:" + "a" * 64,
    )
    if scenario == "corrupt":
        db.parent.mkdir()
        db.write_bytes(b"not a database")
    elif scenario in {"invalid", "ready", "sidecar"}:
        semantic.repository.prepare_index(semantic.index_signature)
        semantic.repository.set_metadata(
            "index_state", "unknown" if scenario == "invalid" else "ready",
        )
        if scenario == "sidecar":
            Path(str(db) + "-wal").write_bytes(b"live sidecar")
    service, _, _ = service_for(tmp_path, semantic=semantic)

    def snapshot():
        return {
            path.relative_to(tmp_path).as_posix(): (
                "directory" if path.is_dir() else path.read_bytes()
            )
            for path in tmp_path.rglob("*")
        }

    before = snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(semantic.repository, "_connect", lambda: pytest.fail("write connection"))
        patch.setattr(semantic, "_persist_state", lambda *_args: pytest.fail("state write"))
        patch.setattr(semantic, "_resolve_index_signature", lambda **_kwargs: pytest.fail("cache mutation"))
        patch.setattr(semantic, "_set_search_available", lambda *_args, **_kwargs: pytest.fail("state mutation"))
        patch.setattr(semantic, "sync", lambda *_args, **_kwargs: pytest.fail("index sync"))
        patch.setattr(Path, "mkdir", lambda *_args, **_kwargs: pytest.fail("directory creation"))
        result = service.scan(KnowledgeHygieneRequest(
            groups=frozenset(), inspect_derived_index=True,
        ))
    assert result.derived_index.status == expected
    assert snapshot() == before


def test_deleted_note_is_partial_without_exception_text(tmp_path, monkeypatch):
    write(tmp_path, "A.md", "private body")
    service, vault, _ = service_for(tmp_path)
    original = vault.read_verified_markdown_snapshot

    def delete_before_read(path, **kwargs):
        (tmp_path / path).unlink()
        return original(path, **kwargs)

    monkeypatch.setattr(vault, "read_verified_markdown_snapshot", delete_before_read)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert result.scan.state == "partial"
    assert result.scan.reasons == ("note_unavailable",)
    assert not result.findings


def test_replaced_note_is_withheld_without_sibling_rescan(tmp_path, monkeypatch):
    from app.services import vault as vault_module

    write(tmp_path, "A.md", "[[Missing]]")
    service, vault, _ = service_for(tmp_path)
    original_read = vault.read_verified_markdown_snapshot
    original_scandir = vault_module.os.scandir
    calls = 0

    def scandir(directory):
        nonlocal calls
        calls += 1
        return original_scandir(directory)

    def replace_before_read(path, **kwargs):
        target = tmp_path / path
        target.unlink()
        target.write_text("replacement", encoding="utf-8")
        return original_read(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(vault_module.os, "scandir", scandir)
        patch.setattr(vault, "read_verified_markdown_snapshot", replace_before_read)
        result = service.scan()
    assert calls == 1
    assert result.scan.reasons == ("note_unavailable",)
    assert result.scan.inspected_notes == 0
    assert not result.findings


def test_relationship_owner_unavailable_withholds_isolation(tmp_path, monkeypatch):
    write(tmp_path, "A.md", "body")
    service, _, _ = service_for(tmp_path)
    monkeypatch.setattr(
        service._relationships, "normalized_relationships_from_content",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    result = service.scan()
    assert result.scan.reasons == ("relationship_unavailable",)
    assert not any(f.kind == "isolated_note" for f in result.findings)


def test_alias_comparison_is_exact_unicode_and_not_casefolded(tmp_path):
    write(tmp_path, "A.md", "---\naliases: [Cafe, Caf\u00e9]\n---\nbody")
    write(tmp_path, "B.md", "---\naliases: [cafe, Cafe\u0301]\n---\nbody")
    service, _, _ = service_for(tmp_path)
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset({"aliases"})))
    assert not result.findings


def test_candidate_sources_stop_at_twenty(tmp_path, monkeypatch):
    for index in range(21):
        write(tmp_path, f"N{index:02}.md", "body")
    service, _, duplicate = service_for(tmp_path)
    original = duplicate.find_bounded_batch
    counts = []

    def record(**kwargs):
        counts.append(len(kwargs["sources"]))
        return original(**kwargs)

    monkeypatch.setattr(duplicate, "find_bounded_batch", record)
    result = service.scan(KnowledgeHygieneRequest(
        groups=frozenset(), duplicate_source_limit=20,
    ))
    assert counts == [20]
    assert result.candidates.source_notes == 20


def test_oversize_and_invalid_utf8_notes_make_scan_partial(tmp_path):
    (tmp_path / "Large.md").write_bytes(b"large body")
    (tmp_path / "Invalid.md").write_bytes(b"\xff")
    service, vault, _ = service_for(tmp_path)
    vault.max_note_bytes = 2
    result = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert result.scan.state == "partial"
    assert result.scan.unavailable_notes == 2
    assert result.scan.reasons == ("note_unavailable",)


def test_unavailable_note_outside_selected_candidate_sources(tmp_path, monkeypatch):
    write(tmp_path, "A.md", "body")
    write(tmp_path, "Z.md", "body")
    service, vault, _ = service_for(tmp_path)
    original = vault.read_verified_markdown_snapshot

    def read(path, **kwargs):
        if path == "Z.md":
            raise OSError("unreadable")
        return original(path, **kwargs)

    monkeypatch.setattr(vault, "read_verified_markdown_snapshot", read)
    result = service.scan(KnowledgeHygieneRequest(
        groups=frozenset(), duplicate_source_limit=1,
    ))
    assert result.scan.reasons == ("note_unavailable",)
    assert result.candidates.state == "partial"
    assert result.candidates.source_notes == 1
    assert result.candidates.reasons == ("scan_partial",)
    no_candidates = service.scan(KnowledgeHygieneRequest(groups=frozenset()))
    assert no_candidates.candidates.state == "not_requested"
    assert no_candidates.candidates.reasons == ()


def test_selected_candidate_source_race_has_source_unavailable(tmp_path, monkeypatch):
    write(tmp_path, "A.md", "body")
    service, vault, _ = service_for(tmp_path)
    original = vault.verify_bounded_markdown_path
    calls = 0

    def source_races(fact):
        nonlocal calls
        calls += 1
        return calls < 4 and original(fact)

    monkeypatch.setattr(vault, "verify_bounded_markdown_path", source_races)
    result = service.scan(KnowledgeHygieneRequest(
        groups=frozenset(), duplicate_source_limit=1,
    ))
    assert result.candidates.state == "partial"
    assert result.candidates.reasons == ("scan_partial", "source_unavailable")
