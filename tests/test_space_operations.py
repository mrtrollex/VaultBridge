from __future__ import annotations

import dataclasses
import json
import os
import pickle
import sqlite3
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.repositories.semantic import SemanticRepository
from app.services.capture import CaptureService
from app.services.knowledge_hygiene import KnowledgeHygieneRequest
from app.services.knowledge_query import KnowledgeQuery, KnowledgeQueryService
from app.services.knowledge_spaces import (
    DEFAULT_SPACE_ID,
    AuthorizedSpaceBinding,
    Capability,
    Dialect,
    QualifiedNoteIdentity,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceId,
    SpacePolicyResolver,
    SpaceRegistry,
    WriteScope,
)
from app.services.promotion import PromotionService
from app.services.semantic_search import FastEmbedder, SemanticSearchService, SemanticSearchUnavailableError
from app.services.space_operations import SpaceOperations
from app.services.space_owners import compose_space_registry
from app.services.vault import NoteNotFoundError, VaultService, VaultValidationError

RESEARCH = SpaceId("research")


def setup_spaces(tmp_path, *, default_indexing="enabled", **research_policy):
    records = []
    for name in ("default", "research"):
        root = tmp_path / name
        root.mkdir()
        records.append(dict(
            space_id=name, root=str(root), semantic_data_path=str(tmp_path / f"{name}-data"),
            label="private operator label", read="allow", write="allow", indexing="enabled",
            dialects=[item.value for item in Dialect], capabilities=[item.value for item in Capability],
        ))
    records[1].update(research_policy)
    records[0]["indexing"] = default_indexing
    settings = Settings(
        vault_path=records[0]["root"], semantic_data_path=records[0]["semantic_data_path"],
        knowledge_spaces_json=json.dumps(dict(version=1, spaces=records)),
    )
    return settings, compose_space_registry(settings)


def operations(registry):
    return SpaceOperations(SpacePolicyResolver(registry))


def note(tmp_path, space, path, content):
    target = tmp_path / space / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def category(expected, callback):
    with pytest.raises(SpaceError) as caught:
        callback()
    assert str(caught.value) == expected


def test_fixed_bundles_use_exact_local_owners(tmp_path):
    _, registry = setup_spaces(tmp_path)
    a, b = (registry._owners[key] for key in (DEFAULT_SPACE_ID, RESEARCH))
    assert a is not b and a.vault is not b.vault
    assert a.vault.vault_root == tmp_path / "default"
    assert b.vault.vault_root == tmp_path / "research"
    for bundle in (a, b):
        assert isinstance(bundle.vault, VaultService)
        assert isinstance(bundle.query, KnowledgeQueryService)
        assert isinstance(bundle.capture, CaptureService)
        assert isinstance(bundle.promotion, PromotionService)
        assert bundle.relationships._vault_service is bundle.vault
        assert bundle.query._vault is bundle.vault
        assert bundle.query._relationships is bundle.relationships
        assert bundle.duplicates.vault_service is bundle.vault
        assert bundle.capture.vault_service is bundle.vault
        assert bundle.promotion.vault_service is bundle.vault
        assert bundle.promotion.candidates is bundle.duplicates
        assert bundle.hygiene._vault is bundle.vault
        assert bundle.hygiene._relationships is bundle.relationships
        assert bundle.hygiene._duplicates is bundle.duplicates
        assert bundle.hygiene._semantic is bundle.semantic
        assert bundle.capture.enqueue is None and bundle.promotion.enqueue is None
        assert bundle.scheduler is not None
        assert not hasattr(bundle.scheduler, "enqueue")
    assert not (tmp_path / "default-data").exists()
    assert not (tmp_path / "research-data").exists()


def test_qualified_identity_only_after_local_canonical_read(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    for space in ("default", "research"):
        note(tmp_path, space, "folder/same.md", space)
    ops = operations(registry)
    results = [ops.read_note(QualifiedPath(key, "folder/../folder/same.md")) for key in (DEFAULT_SPACE_ID, RESEARCH)]
    assert len({row.identity for row in results}) == 2
    assert [row.identity.canonical_relative_path for row in results] == ["folder/same.md"] * 2
    assert [row.note.content for row in results] == ["default", "research"]
    assert all(type(row.identity) is QualifiedNoteIdentity for row in results)
    created = []
    monkeypatch.setattr("app.services.space_operations.QualifiedNoteIdentity", lambda *args: created.append(args))
    with pytest.raises(VaultValidationError):
        ops.read_note(QualifiedPath(RESEARCH, "../default/folder/same.md"))
    with pytest.raises(NoteNotFoundError):
        ops.read_note(QualifiedPath(RESEARCH, "absent.md"))
    assert created == []


def test_registry_token_and_exact_owner_binding(tmp_path):
    settings, registry = setup_spaces(tmp_path)
    other = compose_space_registry(settings)
    resolver = SpacePolicyResolver(registry)
    binding, = resolver.authorize_read(ReadScope((DEFAULT_SPACE_ID,)), Capability.READ_NOTE)
    assert binding._owners is registry._owners[DEFAULT_SPACE_ID]
    assert binding._owners is not registry._owners[RESEARCH]
    category("invalid_scope", lambda: operations(other)._owners(binding))
    forged = AuthorizedSpaceBinding._issue(
        registry._by_id[DEFAULT_SPACE_ID], registry._owners[RESEARCH], registry._token,
    )
    category("invalid_scope", lambda: operations(registry)._owners(forged))
    category("invalid_configuration", lambda: SpaceRegistry.from_settings(
        settings, owners={DEFAULT_SPACE_ID: registry._owners[RESEARCH]},
    ))


@pytest.mark.parametrize("kind", ["unknown", "denied", "capability", "dialect", "hygiene_relationships",
                                      "hygiene_duplicates", "write", "invalid"])
def test_denial_performs_zero_owner_or_filesystem_work(tmp_path, monkeypatch, kind):
    changes = {}
    if kind == "denied":
        changes = dict(read="deny", write="deny", indexing="disabled")
    elif kind == "write":
        changes = dict(write="deny")
    elif kind in {"capability", "hygiene_duplicates"}:
        changes = dict(capabilities=[Capability.HYGIENE.value])
    elif kind in {"dialect", "hygiene_relationships"}:
        changes = dict(dialects=[])
    _, registry = setup_spaces(tmp_path, **changes)
    ops = operations(registry)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("denial touched an owner/filesystem/index/model")
    selected = SpaceId("unknown") if kind == "unknown" else RESEARCH
    actions = {
        "unknown": ("unknown_or_denied_space", lambda: ops.read_note(QualifiedPath(selected, "same.md"))),
        "denied": ("unknown_or_denied_space", lambda: ops.read_note(QualifiedPath(selected, "same.md"))),
        "capability": ("unsupported_capability", lambda: ops.read_note(QualifiedPath(selected, "same.md"))),
        "dialect": ("unsupported_dialect", lambda: ops.outgoing(QualifiedPath(selected, "same.md"))),
        "hygiene_relationships": ("unsupported_dialect", lambda: ops.hygiene(selected)),
        "hygiene_duplicates": ("unsupported_capability", lambda: ops.hygiene(
            selected, KnowledgeHygieneRequest(groups=frozenset(), duplicate_source_limit=1))),
        "write": ("unknown_or_denied_space", lambda: ops._resolver.authorize_write(
            WriteScope(selected), Capability.CAPTURE)),
        "invalid": ("invalid_scope", lambda: ops.inspect_index(ReadScope((DEFAULT_SPACE_ID, RESEARCH)))),
    }
    with monkeypatch.context() as patches:
        patches.setattr(SpaceOperations, "_owners", forbidden)
        for name in ("resolve", "stat", "lstat", "open", "exists", "is_dir", "is_file"):
            patches.setattr(Path, name, forbidden)
        patches.setattr(os, "scandir", forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        patches.setattr(FastEmbedder, "_get_model", forbidden)
        expected, action = actions[kind]
        category(expected, action)
    assert calls == []


def test_authorization_precedes_owner_access_for_every_operation(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    ops = operations(registry)
    events = []
    original = SpacePolicyResolver.authorize_read
    def authorize(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        events.append("authorized")
        return result
    def owner(self, binding):
        assert events and events[-1] == "authorized"
        events.append("owner")
        raise RuntimeError("stop at owner boundary")
    monkeypatch.setattr(SpacePolicyResolver, "authorize_read", authorize)
    monkeypatch.setattr(SpaceOperations, "_owners", owner)
    path = QualifiedPath(RESEARCH, "same.md")
    for action in (lambda: ops.read_note(path), lambda: ops.outgoing(path), lambda: ops.backlinks(path),
                   lambda: ops.duplicates(RESEARCH, title="same"), lambda: ops.hygiene(RESEARCH),
                   lambda: ops.inspect_index(RESEARCH)):
        events.clear()
        with pytest.raises(RuntimeError, match="stop at owner boundary"):
            action()
        assert events[-1] == "owner"


@pytest.mark.parametrize("origin", [None, Dialect.OBSIDIAN_WIKILINK, Dialect.MARKDOWN_LINK])
def test_relationships_do_not_resolve_or_scan_other_space(tmp_path, origin):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "same.md", "[[OnlyResearch]]\n[local](OnlyResearch.md)")
    note(tmp_path, "research", "OnlyResearch.md", "target")
    note(tmp_path, "research", "same.md", "[[OnlyResearch]]\n[local](OnlyResearch.md)")
    ops = operations(registry)
    local = ops.outgoing(QualifiedPath(DEFAULT_SPACE_ID, "same.md"), origin=origin)
    remote = ops.outgoing(QualifiedPath(RESEARCH, "same.md"), origin=origin)
    assert local.space_id == DEFAULT_SPACE_ID
    assert all(row.resolved_path is None for row in local.result)
    assert all(row.resolved_path == "OnlyResearch.md" for row in remote.result)
    back = ops.backlinks(QualifiedPath(RESEARCH, "OnlyResearch.md"), origin=origin)
    assert back.space_id == RESEARCH
    assert len(back.result) == (2 if origin is None else 1)
    assert {row.source_path for row in back.result} == {"same.md"}


def test_hygiene_and_duplicate_evidence_is_local(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "default", "same.md", "---\naliases: [shared]\n---\nbody")
    note(tmp_path, "research", "same.md", "---\naliases: [shared]\n---\nbody")
    note(tmp_path, "research", "nested/same.md", "---\naliases: [shared]\n---\nbody")
    ops = operations(registry)
    a = ops.duplicates(DEFAULT_SPACE_ID, title="same")
    b = ops.duplicates(RESEARCH, title="same")
    assert [row.path for row in a.result] == ["same.md"]
    assert {row.path for row in b.result} == {"same.md", "nested/same.md"}
    request = KnowledgeHygieneRequest(groups=frozenset({"aliases"}), duplicate_source_limit=2)
    first, second = (ops.hygiene(key, request) for key in (DEFAULT_SPACE_ID, RESEARCH))
    assert not any(row.kind in {"colliding_alias", "duplicate_candidate"} for row in first.result.findings)
    assert any(row.kind == "colliding_alias" for row in second.result.findings)
    assert any(row.kind == "duplicate_candidate" for row in second.result.findings)


@pytest.mark.parametrize("indexing", ["enabled", "disabled"])
@pytest.mark.parametrize("storage", ["missing", "existing", "wal"])
def test_inspection_and_construction_never_initialize_search_or_load_model(tmp_path, monkeypatch, indexing, storage):
    def forbidden(*args, **kwargs):
        pytest.fail("semantic initialization/search/model/worker work")
    from app.services.indexer import BackgroundSemanticIndexer

    monkeypatch.setattr(FastEmbedder, "__init__", forbidden)
    monkeypatch.setattr(BackgroundSemanticIndexer, "__init__", forbidden)
    monkeypatch.setattr(SemanticRepository, "_connect", forbidden)
    monkeypatch.setattr(SemanticSearchService, "search", forbidden)
    monkeypatch.setattr(SemanticSearchService, "sync", forbidden)
    settings, registry = setup_spaces(tmp_path, indexing=indexing)
    data = tmp_path / "research-data"
    if storage != "missing":
        data.mkdir()
        connection = sqlite3.connect(data / "semantic-index.sqlite3")
        connection.execute("CREATE TABLE fixture (value TEXT)")
        connection.commit()
        connection.close()
        if storage == "wal":
            (data / "semantic-index.sqlite3-wal").write_bytes(b"old sidecar")
    def snapshot():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in data.iterdir()} if data.exists() else {}
    before = snapshot()
    connections = []
    connect = sqlite3.connect
    def immutable_only(database, *args, **kwargs):
        assert isinstance(database, str) and "mode=ro&immutable=1" in database and kwargs.get("uri") is True
        connections.append(database)
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", immutable_only)
    result = operations(registry).inspect_index(RESEARCH)
    expected = {"missing": "missing", "existing": "invalid_metadata", "wal": "inspection_unavailable"}
    assert result.result == expected[storage]
    assert bool(connections) == (storage == "existing")
    scan = operations(registry).hygiene(RESEARCH, KnowledgeHygieneRequest(inspect_derived_index=True))
    assert scan.result.derived_index.status == expected[storage]
    bundle = registry._owners[RESEARCH]
    assert (bundle.scheduler is None) == (indexing == "disabled")
    for name in ("search", "sync", "rebuild", "initialize", "repository", "embedder", "reconfigure"):
        assert not hasattr(bundle.semantic, name)
    with pytest.raises(SemanticSearchUnavailableError):
        bundle.duplicates.semantic_search_service.search("same")
    after = snapshot()
    assert before == after
    assert not (data / "models").exists()
    # Recomposition remains equally cheap with existing derived data.
    assert compose_space_registry(settings)


def test_legacy_explicit_composition_keeps_default_binding_and_local_semantics(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "same.md").write_text("legacy", encoding="utf-8")
    settings = Settings(vault_path=root, semantic_data_path=tmp_path / "legacy-data")
    registry = compose_space_registry(settings)
    bundle = registry._owners[DEFAULT_SPACE_ID]
    assert bundle.vault.vault_root == settings.vault_path
    assert bundle.semantic._inspect.__self__.cache_dir == settings.semantic_data_path / "models"
    read = operations(registry).read_note(QualifiedPath(DEFAULT_SPACE_ID, "same.md"))
    assert read.note == bundle.vault.read_note("same.md")
    assert operations(registry).inspect_index().space_id == DEFAULT_SPACE_ID
    assert bundle.query.query(KnowledgeQuery(literal_text="legacy")).matches[0].canonical_path == "same.md"
    assert not settings.semantic_data_path.exists()
    # Explicit domain composition does not retrofit named startup checks into legacy.
    missing = Settings(vault_path=tmp_path / "missing", semantic_data_path=tmp_path / "missing-data")
    assert compose_space_registry(missing)


def test_private_bundle_binding_facade_and_errors_are_safe(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path)
    resolver = SpacePolicyResolver(registry)
    binding, = resolver.authorize_read(None, Capability.READ_NOTE)
    bundle = binding._owners
    for private in (bundle, binding, registry, bundle.semantic, bundle.scheduler, resolver, operations(registry)):
        assert str(tmp_path) not in str(private) and "private operator label" not in repr(private)
        with pytest.raises((TypeError, AttributeError)):
            dataclasses.asdict(private)
        with pytest.raises(TypeError):
            pickle.dumps(private)
    with pytest.raises(AttributeError):
        bundle.vault = registry._owners[RESEARCH].vault
    def denied(*args, **kwargs):
        raise PermissionError(f"{tmp_path} private operator label")
    monkeypatch.setattr(bundle.vault, "read_note", denied)
    category("unavailable_space", lambda: operations(registry).read_note(QualifiedPath(DEFAULT_SPACE_ID, "same.md")))


def test_actual_containment_rejects_cross_space_symlink(tmp_path):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "research", "secret.md", "private")
    link = tmp_path / "default" / "alias.md"
    try:
        link.symlink_to(tmp_path / "research" / "secret.md")
    except OSError:
        pytest.skip("Native symlink privilege unavailable; requires actual POSIX coverage")
    with pytest.raises(VaultValidationError):
        operations(registry).read_note(QualifiedPath(DEFAULT_SPACE_ID, "alias.md"))


def test_no_federation_or_scoped_write_surface(tmp_path):
    _, registry = setup_spaces(tmp_path)
    ops = operations(registry)
    for name in ("query", "list_notes", "search", "semantic_search", "capture", "promotion",
                 "create_note", "append_note"):
        assert not hasattr(ops, name)
    for selected in (ReadScope((RESEARCH,)), ReadScope((DEFAULT_SPACE_ID, RESEARCH)), "research", [RESEARCH]):
        category("invalid_scope", lambda: ops.hygiene(selected))


@pytest.mark.parametrize("change", ["missing", "replaced"])
def test_named_root_loss_or_replacement_never_falls_back(tmp_path, change):
    _, registry = setup_spaces(tmp_path)
    note(tmp_path, "research", "same.md", "must never fall back")
    root = tmp_path / "default"
    root.rename(tmp_path / "old-default")
    if change == "replaced":
        note(tmp_path, "default", "same.md", "must never adopt new namespace")
    category("unavailable_space", lambda: operations(registry).read_note(
        QualifiedPath(DEFAULT_SPACE_ID, "same.md")))


def test_all_configuration_validated_before_constructing_any_owner(tmp_path, monkeypatch):
    settings, _ = setup_spaces(tmp_path)
    raw = json.loads(settings.knowledge_spaces_json)
    raw["spaces"][1]["root"] = raw["spaces"][0]["root"]
    invalid = Settings(vault_path=settings.vault_path, semantic_data_path=settings.semantic_data_path,
                       knowledge_spaces_json=json.dumps(raw))
    def forbidden(*args, **kwargs):
        pytest.fail("constructed owner before all registry definitions validated")
    monkeypatch.setattr(VaultService, "__init__", forbidden)
    category("invalid_configuration", lambda: compose_space_registry(invalid))


def test_nonsemantic_advice_does_not_call_search(tmp_path, monkeypatch):
    _, registry = setup_spaces(tmp_path, indexing="disabled", capabilities=["duplicate_candidates"])
    note(tmp_path, "research", "same.md", "body")
    advice = registry._owners[RESEARCH].duplicates.semantic_search_service
    def forbidden(*args, **kwargs):
        pytest.fail("nonsemantic advice called search")
    monkeypatch.setattr(type(advice), "search", forbidden)
    assert operations(registry).duplicates(RESEARCH, title="same").result[0].path == "same.md"
    with pytest.raises(SemanticSearchUnavailableError):
        operations(registry).duplicates(RESEARCH, title="absent")


@pytest.mark.parametrize("operation", ["read_note", "outgoing", "backlinks", "duplicates", "hygiene", "inspect_index"])
@pytest.mark.parametrize("selected", ["unknown", "research"])
def test_every_operation_denies_before_owner_access(tmp_path, monkeypatch, operation, selected):
    _, registry = setup_spaces(tmp_path, read="deny", write="deny", indexing="disabled")
    def forbidden(*args, **kwargs):
        pytest.fail("owner access on denied request")
    monkeypatch.setattr(SpaceOperations, "_owners", forbidden)
    ops = operations(registry)
    key = SpaceId(selected)
    def action():
        if operation in {"read_note", "outgoing", "backlinks"}:
            return getattr(ops, operation)(QualifiedPath(key, "same.md"))
        if operation == "duplicates":
            return ops.duplicates(key, title="same")
        return getattr(ops, operation)(key)
    category("unknown_or_denied_space", action)


@pytest.mark.parametrize("kind", ["unknown", "denied", "capability", "invalid"])
def test_write_binding_authorization_does_no_owner_work(tmp_path, monkeypatch, kind):
    policy = dict(write="deny") if kind == "denied" else dict(capabilities=[])
    _, registry = setup_spaces(tmp_path, **policy)
    def forbidden(*args, **kwargs):
        pytest.fail("write authorization touched local owners")
    monkeypatch.setattr(VaultService, "root_binding_identity", forbidden)
    monkeypatch.setattr(CaptureService, "capture", forbidden)
    monkeypatch.setattr(PromotionService, "apply", forbidden)
    scope = WriteScope(SpaceId("unknown") if kind == "unknown" else RESEARCH)
    if kind == "invalid":
        scope = ReadScope((RESEARCH,))
    expected = {
        "unknown": "unknown_or_denied_space", "denied": "unknown_or_denied_space",
        "capability": "unsupported_capability", "invalid": "invalid_scope",
    }
    category(expected[kind], lambda: SpacePolicyResolver(registry).authorize_write(scope, Capability.CAPTURE))


def redirect_storage_path(link, target, *, directory):
    if os.name == "nt" and directory:
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, check=False)
        if result.returncode:
            pytest.skip("Native Windows junction creation unavailable")
        assert link.is_junction()
    else:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError:
            pytest.skip("Native file/directory symlink creation unavailable")


@pytest.mark.parametrize("indexing", ["enabled", "disabled"])
@pytest.mark.parametrize("redirect", ["directory", "database"])
def test_postconstruction_storage_redirect_never_reads_other_repository(tmp_path, monkeypatch, indexing, redirect):
    a, b = (tmp_path / name for name in ("default-data", "research-data"))
    for data in (a, b):
        data.mkdir()
        (data / "semantic-index.sqlite3").write_bytes(b"read-only probe fixture")
    _, registry = setup_spaces(tmp_path, indexing=indexing, default_indexing=indexing)
    link = a if redirect == "directory" else a / "semantic-index.sqlite3"
    target = b if redirect == "directory" else b / "semantic-index.sqlite3"
    link.rename(tmp_path / "original-storage")
    redirect_storage_path(link, target, directory=redirect == "directory")
    reads = []
    def probe(self, uri):
        reads.append(uri)
        return self._missing_status()
    monkeypatch.setattr(SemanticRepository, "_read_status_uri", probe)
    inspected = operations(registry).inspect_index(DEFAULT_SPACE_ID)
    scan = operations(registry).hygiene(DEFAULT_SPACE_ID, KnowledgeHygieneRequest(inspect_derived_index=True))
    assert reads == [], "A opened a redirected repository before reporting availability"
    assert inspected.result == "inspection_unavailable"
    assert scan.result.derived_index.status == "inspection_unavailable"
    assert all(f.primary_path is None and not f.related_paths for f in scan.result.findings)


@pytest.mark.parametrize("indexing", ["enabled", "disabled"])
@pytest.mark.parametrize("entry_kind", ["symlink", "reparse"])
def test_database_redirect_guard_precedes_resolve_and_reader(tmp_path, monkeypatch, indexing, entry_kind):
    _, registry = setup_spaces(tmp_path, default_indexing=indexing)
    data = tmp_path / "default-data"
    data.mkdir()
    database = data / "semantic-index.sqlite3"
    database.write_bytes(b"fixture")
    original = Path.lstat
    def redirected_entry(path, *args, **kwargs):
        if path == database:
            return SimpleNamespace(st_mode=stat.S_IFLNK if entry_kind == "symlink" else stat.S_IFREG,
                                   st_file_attributes=0x400 if entry_kind == "reparse" else 0)
        return original(path, *args, **kwargs)
    resolve = Path.resolve
    def no_redirect_resolution(path, *args, **kwargs):
        assert path != database, "redirected database was followed before rejecting it"
        return resolve(path, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("redirected database reached SQLite reader")
    monkeypatch.setattr(Path, "lstat", redirected_entry)
    monkeypatch.setattr(Path, "resolve", no_redirect_resolution)
    monkeypatch.setattr(SemanticRepository, "_read_status_uri", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    ops = operations(registry)
    assert ops.inspect_index().result == "inspection_unavailable"
    assert ops.hygiene(request=KnowledgeHygieneRequest(inspect_derived_index=True)).result.derived_index.status \
        == "inspection_unavailable"


@pytest.mark.parametrize("change", ["directory_identity", "non_directory", "inaccessible", "parent_reparse"])
def test_storage_binding_failure_never_reaches_reader(tmp_path, monkeypatch, change):
    data = tmp_path / "default-data"
    data.mkdir()
    (data / "semantic-index.sqlite3").write_bytes(b"fixture")
    _, registry = setup_spaces(tmp_path)
    if change in {"directory_identity", "non_directory"}:
        data.rename(tmp_path / "original-data")
        if change == "directory_identity":
            data.mkdir()
            (data / "semantic-index.sqlite3").write_bytes(b"unrelated new repository")
        else:
            data.write_bytes(b"not a directory")
    elif change == "inaccessible":
        access = os.access
        monkeypatch.setattr(os, "access", lambda path, mode: False if path == data else access(path, mode))
    else:
        lstat = Path.lstat
        def reparse_parent(path, *args, **kwargs):
            if path == data.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            assert path != data, "probed child through redirected parent"
            return lstat(path, *args, **kwargs)
        monkeypatch.setattr(Path, "lstat", reparse_parent)
    def forbidden(*args, **kwargs):
        pytest.fail("unsafe data binding reached reader")
    monkeypatch.setattr(SemanticRepository, "_read_status_uri", forbidden)
    assert operations(registry).inspect_index().result == "inspection_unavailable"


@pytest.mark.parametrize("indexing", ["enabled", "disabled"])
def test_unchanged_validated_database_reaches_reader_without_second_resolve(tmp_path, monkeypatch, indexing):
    data = tmp_path / "default-data"
    data.mkdir()
    database = data / "semantic-index.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE fixture(value TEXT)")
    _, registry = setup_spaces(tmp_path, default_indexing=indexing)
    before = database.read_bytes(), database.stat().st_mtime_ns
    repository_read = SemanticRepository._read_immutable_status
    reads = []
    def consume(self, *, validated_path=None):
        assert validated_path == database and self.db_path == validated_path
        def forbidden(*args, **kwargs):
            pytest.fail("immutable repository independently re-resolved validated target")
        with monkeypatch.context() as patches:
            patches.setattr(Path, "resolve", forbidden)
            return repository_read(self, validated_path=validated_path)
    connect = sqlite3.connect
    def immutable_only(database_uri, *args, **kwargs):
        reads.append(database_uri)
        assert database_uri == database.as_uri() + "?mode=ro&immutable=1"
        return connect(database_uri, *args, **kwargs)
    monkeypatch.setattr(SemanticRepository, "_read_immutable_status", consume)
    monkeypatch.setattr(sqlite3, "connect", immutable_only)
    ops = operations(registry)
    assert ops.inspect_index().result == "invalid_metadata"
    assert ops.hygiene(request=KnowledgeHygieneRequest(inspect_derived_index=True)).result.derived_index.status \
        == "invalid_metadata"
    assert len(reads) == 2
    assert before == (database.read_bytes(), database.stat().st_mtime_ns)
    assert [p.name for p in data.iterdir()] == [database.name]


@pytest.mark.parametrize("storage", ["directory_absent", "database_absent"])
def test_missing_named_storage_remains_missing_without_reader_or_creation(tmp_path, monkeypatch, storage):
    data = tmp_path / "default-data"
    if storage == "database_absent":
        data.mkdir()
    _, registry = setup_spaces(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("missing storage caused creation or SQLite read")
    with monkeypatch.context() as patches:
        patches.setattr(Path, "mkdir", forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        patches.setattr(SemanticRepository, "_read_status_uri", forbidden)
        assert operations(registry).inspect_index().result == "missing"
    assert data.exists() == (storage == "database_absent")
    assert not (data / "semantic-index.sqlite3").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_redirected_sidecar_is_unavailable_before_any_sqlite_read(tmp_path, monkeypatch, suffix):
    data = tmp_path / "default-data"
    data.mkdir()
    database = data / "semantic-index.sqlite3"
    database.write_bytes(b"fixture")
    _, registry = setup_spaces(tmp_path)
    sidecar = database.with_name(database.name + suffix)
    lstat = Path.lstat
    def redirected_entry(path, *args, **kwargs):
        if path == sidecar:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
        return lstat(path, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail("redirected sidecar reached repository reader")
    monkeypatch.setattr(Path, "lstat", redirected_entry)
    monkeypatch.setattr(SemanticRepository, "_read_status_uri", forbidden)
    assert operations(registry).inspect_index().result == "inspection_unavailable"
