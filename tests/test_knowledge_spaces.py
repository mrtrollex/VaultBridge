from __future__ import annotations

import dataclasses
import json
import os
import pickle
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.knowledge_spaces import (
    DEFAULT_SPACE_ID,
    AuthorizedSpaceBinding,
    Capability,
    Dialect,
    KnowledgeSpacePolicy,
    QualifiedNoteIdentity,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceId,
    SpacePolicyResolver,
    SpaceRegistry,
    WriteScope,
    _ordinary_missing_data_component,
    parse_knowledge_spaces,
)


def record(tmp_path, space_id="default", **changes):
    value = dict(
        space_id=space_id, root=str(tmp_path / str(space_id)),
        semantic_data_path=str(tmp_path / (str(space_id) + "-data")),
        read="allow", write="allow", indexing="enabled",
        dialects=[item.value for item in Dialect], capabilities=[item.value for item in Capability],
    )
    value.update(changes)
    return value


def encoded(*records):
    return json.dumps(dict(version=1, spaces=list(records)))


def settings_for(*records):
    default = next(item for item in records if item["space_id"] == "default")
    return Settings(vault_path=default["root"], semantic_data_path=default["semantic_data_path"],
                    knowledge_spaces_json=encoded(*records))


def registry_for(tmp_path, **changes):
    records = [record(tmp_path), record(tmp_path, "research", **changes)]
    for item in records:
        Path(item["root"]).mkdir()
    return SpaceRegistry.from_settings(settings_for(*records))


def assert_category(category, call):
    with pytest.raises(SpaceError) as caught:
        call()
    assert caught.value.category == category
    assert str(caught.value) == category
    return caught.value


@pytest.mark.parametrize("raw", [
    "", " \t\n", " " * 32769, "é" * 16385, "\ufeff{}", "{", "{} {}", "// comment\n{}",
    '{"version":NaN,"spaces":[]}', '{"version":Infinity,"spaces":[]}',
    '{"version":1,"version":1,"spaces":[]}',
    '{"version":1,"spaces":[{"space_id":"default","space_id":"default"}]}',
    '{"version":1,"spaces":[{"x":{"a":1,"a":2}}]}',
    '{"version":1,"spaces":[{"x":[[]]}]}',
    '{"version":true,"spaces":[]}', '{"version":2,"spaces":[]}',
    '{"version":1.0,"spaces":[]}', '{"version":"1","spaces":[]}',
    '{"version":1,"spaces":[]}', '{"version":1,"spaces":{},"extra":0}', "[]", "null", "\ud800",
], ids=lambda raw: f"length-{len(raw)}-{ascii(raw[:16])}")
def test_strict_json_envelope(raw):
    assert_category("invalid_configuration", lambda: parse_knowledge_spaces(raw))


@pytest.mark.parametrize("changes", [
    {"space_id": "Default"}, {"space_id": "défault"}, {"space_id": " default"},
    {"space_id": "a" * 65}, {"space_id": 1}, {"space_id": "*"}, {"space_id": ""},
    {"capabilities": ["unknown"]}, {"capabilities": ["read_note"] * 2},
    {"capabilities": ["read_note"] * 13}, {"capabilities": "read_note"}, {"capabilities": [True]},
    {"dialects": ["unknown"]}, {"dialects": ["markdown_link"] * 2},
    {"dialects": ["markdown_link"] * 3}, {"dialects": None},
    {"read": True}, {"write": "ALLOW"}, {"indexing": False},
    {"read": "deny", "write": "allow", "indexing": "disabled"},
    {"read": "deny", "write": "deny", "indexing": "enabled"},
    {"label": ""}, {"label": "é" * 65}, {"label": "a\x00b"}, {"label": "\ud800"},
    {"label": None}, {"policy": {}}, {"extra": 1},
])
def test_record_schema(tmp_path, changes):
    assert_category("invalid_configuration", lambda: parse_knowledge_spaces(encoded(record(tmp_path, **changes))))


@pytest.mark.parametrize("field", ["root", "semantic_data_path"])
@pytest.mark.parametrize("path", [
    "", "relative", "~/vault", "/vault/../other", "/vault/./data", " /vault", "/vault ",
    "/vault/$HOME", "/vault/${name}", "/vault/%name%", "/vault/`name`", "/vault/\x00x",
    "/vault/\nx", "C:relative", "\\\\?\\C:\\vault", "\\\\.\\C:\\vault", "/" + "é" * 2048,
    "/vault/\ud800",
])
def test_path_lexical_rejections(tmp_path, field, path):
    assert_category("invalid_configuration", lambda: parse_knowledge_spaces(encoded(record(tmp_path, **{field: path}))))


def test_configuration_counts_required_fields_and_default(tmp_path):
    item = record(tmp_path)
    for records in ([], [item] * 9, [item] * 2, [record(tmp_path, "other")]):
        assert_category("invalid_configuration", lambda: parse_knowledge_spaces(encoded(*records)))
    for field in item:
        incomplete = {key: value for key, value in item.items() if key != field}
        assert_category("invalid_configuration", lambda: parse_knowledge_spaces(encoded(incomplete)))


def test_parser_is_nonprobing_and_raw_byte_bound_is_inclusive(tmp_path, monkeypatch):
    raw = encoded(record(tmp_path, label="a [{\\\" b", capabilities=[], dialects=[]))
    padded = raw + " " * (32768 - len(raw.encode()))
    def forbidden(*args, **kwargs):
        pytest.fail("parser performed filesystem work")
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    assert len(parse_knowledge_spaces(padded)) == 1
    assert_category("invalid_configuration", lambda: parse_knowledge_spaces(padded + " "))


@pytest.mark.parametrize("field", ["KNOWLEDGE_SPACES_JSON", "knowledge_spaces_json"])
def test_settings_alias_and_strict_input(tmp_path, field):
    raw = encoded(record(tmp_path))
    settings = Settings(**{field: raw})
    assert settings.knowledge_spaces_json == raw
    assert len(settings._knowledge_space_definitions) == 1
    assert raw not in repr(settings)
    assert field not in settings.model_dump()
    for invalid in ("", " ", 1, b"{}", {}, []):
        assert_category("invalid_configuration", lambda: Settings(**{field: invalid}))
    assert Settings(**{field: None}).knowledge_spaces_json is None
    assert_category("invalid_configuration", lambda: Settings(KNOWLEDGE_SPACES_JSON=raw, knowledge_spaces_json=raw))
    assert Settings.from_env({"KNOWLEDGE_SPACES_JSON": raw}).knowledge_spaces_json == raw
    assert Settings.from_env({"knowledge_spaces_json": raw}).knowledge_spaces_json is None


def test_legacy_implicit_definition_does_no_new_filesystem_work(tmp_path, monkeypatch):
    settings = Settings(vault_path=tmp_path / "missing", semantic_data_path=Path("relative-data"))
    def forbidden(*args, **kwargs):
        pytest.fail("legacy registry performed filesystem work")
    for name in ("exists", "resolve", "mkdir", "is_dir", "stat", "open"):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    registry = SpaceRegistry.from_settings(settings)
    definition, = registry._definitions
    assert definition.space_id == DEFAULT_SPACE_ID
    assert definition.root_binding == settings.vault_path
    assert definition.semantic_data_binding == settings.semantic_data_path
    assert definition.policy == KnowledgeSpacePolicy("allow", "allow", "enabled", frozenset(Dialect),
                                                   frozenset(Capability))
    settings.require_legacy_composition()


def test_named_registry_is_sorted_and_default_does_not_broaden(tmp_path):
    registry = registry_for(tmp_path)
    assert [item.space_id.value for item in registry._definitions] == ["default", "research"]
    resolver = SpacePolicyResolver(registry)
    assert [item.space_id for item in resolver.authorize_read(None, Capability.LIST_NOTES)] == [DEFAULT_SPACE_ID]
    assert resolver.authorize_write(None, Capability.CAPTURE, legacy=True).space_id == DEFAULT_SPACE_ID


@pytest.mark.parametrize("field", ["vault_path", "semantic_data_path"])
def test_named_default_must_match_legacy_settings(tmp_path, field):
    item = record(tmp_path)
    Path(item["root"]).mkdir()
    other = tmp_path / "other"
    other.mkdir()
    values = dict(vault_path=item["root"], semantic_data_path=item["semantic_data_path"],
                  knowledge_spaces_json=encoded(item))
    values[field] = other
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(Settings(**values)))


def test_default_semantic_path_resolves_under_startup_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    item = record(tmp_path)
    Path(item["root"]).mkdir()
    settings = Settings(vault_path=item["root"], semantic_data_path="default-data", knowledge_spaces_json=encoded(item))
    assert SpaceRegistry.from_settings(settings)._definitions[0].semantic_data_binding == tmp_path / "default-data"


@pytest.mark.parametrize("problem", ["same", "child", "parent", "missing", "file"])
def test_root_rejections(tmp_path, problem):
    first = record(tmp_path)
    root = Path(first["root"])
    root.mkdir()
    candidate = {"same": root, "child": root / "child", "parent": tmp_path,
                 "missing": tmp_path / "missing", "file": tmp_path / "file"}[problem]
    if problem == "child":
        candidate.mkdir()
    if problem == "file":
        candidate.write_text("fixture")
    second = record(tmp_path, "research", root=str(candidate))
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings_for(first, second)))


@pytest.mark.parametrize("problem", ["same", "child", "parent", "root", "contains_root", "other_root", "file",
                                      "own_unexcluded", "excluded_component_itself", "missing_excluded_component"])
def test_semantic_binding_rejections(tmp_path, problem):
    first, second = record(tmp_path), record(tmp_path, "research")
    for item in (first, second):
        Path(item["root"]).mkdir()
    data = Path(first["semantic_data_path"])
    data.mkdir()
    excluded = Path(second["root"]) / ".obsidian"
    excluded.mkdir()
    candidate = {
        "same": data, "child": data / "child", "parent": tmp_path,
        "root": Path(second["root"]), "contains_root": tmp_path,
        "other_root": Path(first["root"]) / ".obsidian" / "data",
        "file": tmp_path / "file", "own_unexcluded": Path(second["root"]) / "data",
        "excluded_component_itself": excluded,
        "missing_excluded_component": Path(second["root"]) / ".git" / "data",
    }[problem]
    if problem == "file":
        candidate.write_text("fixture")
    second["semantic_data_path"] = str(candidate)
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings_for(first, second)))


def test_own_excluded_storage_and_missing_external_storage_are_nonmutating(tmp_path, monkeypatch):
    first, second = record(tmp_path), record(tmp_path, "research", indexing="disabled", write="deny")
    for item in (first, second):
        Path(item["root"]).mkdir()
    excluded = Path(first["root"]) / ".obsidian"
    excluded.mkdir()
    first["semantic_data_path"] = str(excluded / "derived" / "data")
    before = set(tmp_path.rglob("*"))
    settings = settings_for(first, second)
    def forbidden(*args, **kwargs):
        pytest.fail("startup attempted write/recursive scan/index initialization")
    for name in ("mkdir", "write_text", "write_bytes", "open", "rglob"):
        monkeypatch.setattr(Path, name, forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    registry = SpaceRegistry.from_settings(settings)
    assert len(registry._definitions) == 2
    monkeypatch.undo()
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("failure", ["scandir", "access", "resolve"])
def test_inaccessible_named_root_sanitizes_errors(tmp_path, monkeypatch, failure):
    item = record(tmp_path)
    Path(item["root"]).mkdir()
    settings = settings_for(item)
    def denied(*args, **kwargs):
        raise PermissionError("private host path and OS details")
    if failure == "access":
        monkeypatch.setattr(os, "access", lambda *args: False)
    elif failure == "resolve":
        monkeypatch.setattr(Path, "resolve", denied)
    else:
        monkeypatch.setattr(os, "scandir", denied)
    error = assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings))
    assert error.__suppress_context__ or error.__context__ is None


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        pytest.skip(f"Native symlink unavailable: {type(exc).__name__}")


@pytest.mark.parametrize("kind", ["root", "data", "data_child", "sidecar", "broken_data"])
def test_symlink_aliases(tmp_path, kind):
    first, second = record(tmp_path), record(tmp_path, "research")
    for item in (first, second):
        Path(item["root"]).mkdir()
        Path(item["semantic_data_path"]).mkdir()
    alias = tmp_path / "alias"
    if kind == "root":
        symlink_or_skip(alias, Path(first["root"]))
        second["root"] = str(alias)
    elif kind in ("data", "data_child"):
        symlink_or_skip(alias, Path(first["semantic_data_path"]))
        second["semantic_data_path"] = str(alias / "child" if kind == "data_child" else alias)
    elif kind == "broken_data":
        symlink_or_skip(alias, tmp_path / "absent")
        second["semantic_data_path"] = str(alias)
    else:
        target = Path(first["semantic_data_path"]) / "semantic-index.sqlite3"
        target.write_bytes(b"fixture, never opened")
        symlink_or_skip(Path(second["semantic_data_path"]) / "semantic-index.sqlite3-wal", target)
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings_for(first, second)))


def test_hardlinked_sidecar_namespace_collision(tmp_path):
    first, second = record(tmp_path), record(tmp_path, "research", indexing="disabled")
    for item in (first, second):
        Path(item["root"]).mkdir()
        Path(item["semantic_data_path"]).mkdir()
    source = Path(first["semantic_data_path"]) / "semantic-index.sqlite3"
    source.write_bytes(b"fixture")
    try:
        (Path(second["semantic_data_path"]) / "semantic-index.sqlite3-shm").hardlink_to(source)
    except OSError:
        pytest.skip("Native hardlinks unavailable")
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings_for(first, second)))


@pytest.mark.parametrize("value", ["Default", "é", "a" * 65, " default", "*", "", [], None, True])
def test_space_id_caller_validator(value):
    assert_category("invalid_scope", lambda: SpaceId(value))


def test_immutable_identity_and_scope_semantics():
    a, z = SpaceId("a"), SpaceId("z")
    assert a < z and a == SpaceId("a") and len({a, SpaceId("a")}) == 1
    assert ReadScope((z, a, z)).space_ids == (a, z)
    assert ReadScope((a,) * 8) == ReadScope((a,))
    for bad in ((), (a,) * 9, [a], ("a",), ((a,),)):
        assert_category("invalid_scope", lambda: ReadScope(bad))
    for bad in ((a, z), [a], "a", None):
        assert_category("invalid_scope", lambda: WriteScope(bad))
    identities = [QualifiedNoteIdentity(a, path) for path in ("b.md", "a.md", "A.md")]
    assert [item.canonical_relative_path for item in sorted(identities)] == ["A.md", "a.md", "b.md"]
    assert QualifiedNoteIdentity(a, "a.md") != QualifiedNoteIdentity(z, "a.md")
    assert QualifiedPath(a, "../unverified.md").relative_path == "../unverified.md"
    assert QualifiedPath(a, "é" * 512).relative_path == "é" * 512
    for bad in ("", "é" * 513, "\ud800", None):
        assert_category("invalid_scope", lambda: QualifiedPath(a, bad))
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.value = "z"


@pytest.mark.parametrize("changes, operation, dialects, category", [
    ({"capabilities": []}, Capability.READ_NOTE, frozenset(), "unsupported_capability"),
    ({"dialects": []}, Capability.RELATIONSHIPS, frozenset(Dialect), "unsupported_dialect"),
    ({"indexing": "disabled"}, Capability.SEMANTIC_RETRIEVAL, frozenset(), "indexing_disabled"),
    ({"read": "deny", "write": "deny", "indexing": "disabled"}, Capability.READ_NOTE,
     frozenset(), "unknown_or_denied_space"),
])
def test_resolver_policy_denials(tmp_path, changes, operation, dialects, category):
    resolver = SpacePolicyResolver(registry_for(tmp_path, **changes))
    assert_category(category, lambda: resolver.authorize_read(ReadScope((SpaceId("research"),)), operation, dialects))


@pytest.mark.parametrize("selected", ["unknown", "research"])
def test_whole_scope_denial_before_any_work(tmp_path, monkeypatch, selected):
    registry = registry_for(tmp_path, read="deny", write="deny", indexing="disabled")
    class NoWork:
        def __getattribute__(self, name):
            pytest.fail("owner/index/model/candidate discovery accessed")
    owners = {item.space_id: NoWork() for item in registry._definitions}
    settings = settings_for(
        record(tmp_path), record(tmp_path, "research", read="deny", write="deny", indexing="disabled"))
    resolver = SpacePolicyResolver(SpaceRegistry.from_settings(settings, owners=owners))
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("work")
        pytest.fail("authorization performed filesystem/index work")
    scope = ReadScope((DEFAULT_SPACE_ID, SpaceId(selected)))
    with monkeypatch.context() as patches:
        for name in ("exists", "is_dir", "is_file", "stat", "lstat", "resolve", "open", "read_text"):
            patches.setattr(Path, name, forbidden)
        for name in ("scandir", "stat", "lstat", "readlink", "open", "access"):
            patches.setattr(os, name, forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        assert_category("unknown_or_denied_space", lambda: resolver.authorize_read(scope, Capability.READ_NOTE))
    assert calls == []


def test_resolver_authorized_bindings_and_write_index_rules(tmp_path):
    registry = registry_for(tmp_path, write="deny", indexing="disabled")
    resolver = SpacePolicyResolver(registry)
    research = SpaceId("research")
    bindings = resolver.authorize_read(ReadScope((research, DEFAULT_SPACE_ID)), Capability.LIST_NOTES)
    assert tuple(item.space_id for item in bindings) == (DEFAULT_SPACE_ID, research)
    assert all(registry._accepts(item) for item in bindings)
    assert_category("unknown_or_denied_space",
                    lambda: resolver.authorize_write(WriteScope(research), Capability.CAPTURE))
    assert_category("invalid_scope", lambda: resolver.authorize_write(None, Capability.CAPTURE))
    assert_category("invalid_scope", lambda: resolver.authorize_write(ReadScope((research,)), Capability.CAPTURE))
    assert_category("invalid_scope", lambda: resolver.authorize_read([], Capability.READ_NOTE))
    assert_category("indexing_disabled", lambda: resolver.authorize_index(WriteScope(research)))
    assert resolver.authorize_read(ReadScope((research,)), Capability.HYGIENE)
    assert resolver.authorize_index(WriteScope(DEFAULT_SPACE_ID)).space_id == DEFAULT_SPACE_ID
    assert_category("unsupported_capability", lambda: resolver.authorize_read(None, "read_note"))
    assert_category("unsupported_dialect",
                    lambda: resolver.authorize_read(None, Capability.READ_NOTE, {"markdown_link"}))


def test_index_maintenance_does_not_require_note_write_or_capabilities(tmp_path):
    resolver = SpacePolicyResolver(registry_for(tmp_path, write="deny", capabilities=[]))
    assert resolver.authorize_index(WriteScope(SpaceId("research"))).space_id == SpaceId("research")


@pytest.mark.parametrize("operation, dialects, changes, category", [
    (Capability.READ_NOTE, frozenset(), {"capabilities": []}, "unsupported_capability"),
    (Capability.RELATIONSHIPS, frozenset(Dialect), {"dialects": []}, "unsupported_dialect"),
    (Capability.SEMANTIC_RETRIEVAL, frozenset(), {"indexing": "disabled"}, "indexing_disabled"),
])
def test_semantic_and_capability_denials_do_not_probe_storage(
    tmp_path, monkeypatch, operation, dialects, changes, category,
):
    resolver = SpacePolicyResolver(registry_for(tmp_path, **changes))
    scope = ReadScope((SpaceId("research"),))
    def forbidden(*args, **kwargs):
        pytest.fail("policy denial performed work")
    with monkeypatch.context() as patches:
        for name in ("exists", "resolve", "stat", "open", "is_file", "is_dir"):
            patches.setattr(Path, name, forbidden)
        patches.setattr(os, "scandir", forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        assert_category(category, lambda: resolver.authorize_read(scope, operation, dialects))


@pytest.mark.skipif(os.name != "nt", reason="Windows native aliases")
@pytest.mark.parametrize("alias_kind", ["case", "junction", "short_name"])
def test_windows_native_root_aliases(tmp_path, alias_kind):
    first = record(tmp_path)
    root = Path(first["root"])
    root.mkdir()
    if alias_kind == "case":
        alias = Path(str(root).upper())
    elif alias_kind == "junction":
        alias = tmp_path / "junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(root)],
                                capture_output=True, check=False)
        if result.returncode:
            pytest.skip("Native junction creation unavailable")
    else:
        import ctypes
        buffer = ctypes.create_unicode_buffer(4096)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(root), buffer, len(buffer))
        if not length or length >= len(buffer) or buffer.value.casefold() == str(root).casefold():
            pytest.skip("Filesystem has no distinct 8.3 alias")
        alias = Path(buffer.value)
    second = record(tmp_path, "research", root=str(alias))
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings_for(first, second)))


def test_enabled_missing_storage_needs_create_capable_parent(tmp_path, monkeypatch):
    first = record(tmp_path)
    Path(first["root"]).mkdir()
    settings = settings_for(first)
    original = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: False if path == tmp_path else original(path, mode))
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings))


@pytest.mark.skipif(os.name != "nt", reason="Windows dangling junction semantics")
@pytest.mark.parametrize("suffix", [(), ("future", "data")])
def test_dangling_junction_semantic_data_is_rejected(tmp_path, monkeypatch, suffix):
    first = record(tmp_path)
    Path(first["root"]).mkdir()
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)],
                            capture_output=True, check=False)
    if result.returncode:
        pytest.skip("Native mklink /J unavailable for disposable test junction")
    target.rmdir()
    assert not junction.exists() and not junction.is_symlink()
    assert junction.is_junction()
    assert junction.lstat().st_file_attributes & 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
    first["semantic_data_path"] = str(junction.joinpath(*suffix))
    settings = settings_for(first)
    before = tuple(sorted(path.name for path in tmp_path.iterdir()))
    def forbidden(*args, **kwargs):
        pytest.fail("validation created or wrote storage")
    with monkeypatch.context() as patches:
        for name in ("mkdir", "open", "write_text", "write_bytes"):
            patches.setattr(Path, name, forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings))
    assert tuple(sorted(path.name for path in tmp_path.iterdir())) == before
    assert not target.exists()
    assert not junction.joinpath(*suffix).exists()
    assert junction.is_junction()


@pytest.mark.parametrize("tag", [0xA0000003, 0xA000000C, 0x80000000])
def test_missing_component_helper_rejects_any_observed_reparse_entry(tmp_path, monkeypatch, tag):
    # Junction/mount-point, symlink and an arbitrary reparse tag: none is absent.
    path = tmp_path / "entry"
    info = SimpleNamespace(st_file_attributes=0x400, st_reparse_tag=tag)
    calls = []
    def nofollow(candidate):
        calls.append(candidate)
        return info
    def forbidden(*args, **kwargs):
        pytest.fail("missing-component proof followed an entry or used link-kind guesses")
    with monkeypatch.context() as patches:
        patches.setattr(Path, "lstat", nofollow)
        for name in ("exists", "is_symlink", "is_junction", "resolve"):
            patches.setattr(Path, name, forbidden)
        assert not _ordinary_missing_data_component(path)
    assert calls == [path]


def test_missing_component_helper_accepts_only_not_found(tmp_path, monkeypatch):
    path = tmp_path / "genuinely-absent"
    assert _ordinary_missing_data_component(path)
    def denied(*args, **kwargs):
        raise PermissionError("private filesystem diagnostic")
    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(PermissionError):
        _ordinary_missing_data_component(path)


def test_missing_data_metadata_failure_is_sanitized(tmp_path, monkeypatch):
    first = record(tmp_path)
    Path(first["root"]).mkdir()
    settings = settings_for(first)
    data = Path(first["semantic_data_path"])
    original = Path.lstat
    def unavailable(path):
        if path == data:
            raise PermissionError("private host path and diagnostic")
        return original(path)
    monkeypatch.setattr(Path, "lstat", unavailable)
    assert_category("invalid_configuration", lambda: SpaceRegistry.from_settings(settings))


@pytest.mark.parametrize("indexing", ["enabled", "disabled"])
def test_ordinary_missing_semantic_suffix_remains_valid(tmp_path, monkeypatch, indexing):
    first = record(tmp_path, indexing=indexing, semantic_data_path=str(tmp_path / "future" / "data"))
    Path(first["root"]).mkdir()
    settings = settings_for(first)
    def forbidden(*args, **kwargs):
        pytest.fail("validation created ordinary missing storage")
    with monkeypatch.context() as patches:
        patches.setattr(Path, "mkdir", forbidden)
        patches.setattr(Path, "open", forbidden)
        patches.setattr(sqlite3, "connect", forbidden)
        registry = SpaceRegistry.from_settings(settings)
    assert registry._definitions[0].semantic_data_binding == tmp_path / "future" / "data"
    assert not (tmp_path / "future").exists()


def test_disabled_existing_storage_needs_no_read_access(tmp_path, monkeypatch):
    first = record(tmp_path, indexing="disabled")
    Path(first["root"]).mkdir()
    data = Path(first["semantic_data_path"])
    data.mkdir()
    settings = settings_for(first)
    original = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: False if path == data else original(path, mode))
    assert SpaceRegistry.from_settings(settings)


def test_private_objects_and_binding_generation(tmp_path):
    item = record(tmp_path, label="private label")
    Path(item["root"]).mkdir()
    settings = settings_for(item)
    owner = object()
    registry = SpaceRegistry.from_settings(settings, owners={DEFAULT_SPACE_ID: owner})
    other = SpaceRegistry.from_settings(settings, owners={DEFAULT_SPACE_ID: owner})
    binding, = SpacePolicyResolver(registry).authorize_read(None, Capability.READ_NOTE)
    assert binding._owners is owner
    assert not other._accepts(binding)
    assert binding != SpacePolicyResolver(registry).authorize_read(None, Capability.READ_NOTE)[0]
    for private in (registry, registry._definitions[0], binding, SpacePolicyResolver(registry)):
        assert str(tmp_path) not in repr(private) and "private label" not in repr(private)
        with pytest.raises((TypeError, AttributeError)):
            dataclasses.asdict(private)
        with pytest.raises(TypeError):
            pickle.dumps(private)
    with pytest.raises(AttributeError):
        registry._definitions = ()
    with pytest.raises(TypeError):
        registry._by_id[SpaceId("x")] = registry._definitions[0]
    with pytest.raises(TypeError):
        AuthorizedSpaceBinding()


def test_public_composition_guards_run_before_owners(tmp_path, monkeypatch):
    from app import cli, main, mcp_http, mcp_server
    settings = settings_for(record(tmp_path))  # Missing root: guard precedes startup probes too.
    def forbidden(*args, **kwargs):
        pytest.fail("direct-owner composition was reached")
    for module in (cli, main, mcp_server):
        monkeypatch.setattr(module, "VaultService", forbidden)
        monkeypatch.setattr(module, "semantic_search_service_from_settings", forbidden)
    assert_category("invalid_configuration", lambda: main.create_app(settings=settings))
    assert_category("invalid_configuration", lambda: mcp_server.create_mcp_server(settings=settings))
    assert_category("invalid_configuration", lambda: mcp_http.create_mcp_http_transport(
        settings=settings, vault_service=None, semantic_search_service=None, duplicate_candidate_service=None,
        relationship_service=None, knowledge_hygiene_service=None, semantic_indexer=None, rate_limiter=None))
    for run in (cli.run_status, cli.run_index_check, cli.run_index, cli.run_index_rebuild, cli.run_reindex,
                cli.run_capture, cli.run_promote_apply, cli.run_hygiene_scan):
        assert_category("invalid_configuration", lambda: run(settings))
    assert_category("invalid_configuration", lambda: cli.run_search(settings, "text"))
    assert_category("invalid_configuration", lambda: cli.run_related(settings, "text"))
    assert_category("invalid_configuration", lambda: cli.run_knowledge_query(settings))
    assert_category("invalid_configuration",
                    lambda: cli.run_promote_review(settings, source_path="a.md", capture_id="x"))
    assert cli.main(["capture"], settings=settings) == 2
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    assert mcp_server.main() == 2


def test_legacy_application_still_accepts_missing_root(tmp_path):
    from app.main import create_app
    settings = Settings(vault_path=tmp_path / "missing", semantic_data_path=tmp_path / "absent-data")
    app = create_app(settings=settings)
    assert app.state.settings is settings
    assert not settings.vault_path.exists() and not settings.semantic_data_path.exists()
