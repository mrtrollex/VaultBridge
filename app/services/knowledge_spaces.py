"""Private, startup-owned knowledge-space configuration and policy boundary.

Registry validation is independent of owner construction. Domain composition is
explicit and remains inaccessible to today's direct-owner public adapters.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import total_ordering
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from app.services.vault import SEMANTIC_EXCLUDED_DIRECTORIES

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.capture import CaptureService
    from app.services.duplicate_candidates import DuplicateCandidateService
    from app.services.knowledge_hygiene import KnowledgeHygieneService
    from app.services.knowledge_query import KnowledgeQueryService
    from app.services.promotion import PromotionService
    from app.services.relationships import RelationshipService
    from app.services.space_owners import DeferredSpaceIndexLifecycle, ImmutableSpaceInspection
    from app.services.vault import VaultService


class SpaceError(Exception):
    """Stable private domain category; never includes supplied values or I/O text."""

    def __init__(
        self,
        category: Literal[
            "invalid_configuration", "invalid_scope", "unknown_or_denied_space",
            "unsupported_capability", "unsupported_dialect", "indexing_disabled", "unavailable_space",
        ],
        *,
        reason: Literal["named_serving_unsupported"] | None = None,
    ):
        self.category = category
        self.reason = reason
        super().__init__(category)


def _invalid_configuration() -> SpaceError:
    return SpaceError("invalid_configuration")


class Capability(StrEnum):
    READ_NOTE = "read_note"
    LIST_NOTES = "list_notes"
    LITERAL_SEARCH = "literal_search"
    RELATIONSHIPS = "relationships"
    SEMANTIC_RETRIEVAL = "semantic_retrieval"
    KNOWLEDGE_QUERY = "knowledge_query"
    DUPLICATE_CANDIDATES = "duplicate_candidates"
    CAPTURE = "capture"
    PROMOTION = "promotion"
    CREATE_NOTE = "create_note"
    APPEND_NOTE = "append_note"
    HYGIENE = "hygiene"


class Dialect(StrEnum):
    OBSIDIAN_WIKILINK = "obsidian_wikilink"
    MARKDOWN_LINK = "markdown_link"


def validate_space_id(value: object, *, category: str = "invalid_scope") -> str:
    if type(value) is not str or not value.isascii() or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise SpaceError(category)
    return value


@dataclass(frozen=True, slots=True, order=True)
class SpaceId:
    value: str

    def __post_init__(self):
        validate_space_id(self.value)


DEFAULT_SPACE_ID = SpaceId("default")


def _require_id(value: object) -> None:
    if type(value) is not SpaceId:
        raise SpaceError("invalid_scope")


def _utf8(value: object, maximum: int, category: str) -> str:
    try:
        if type(value) is str and 1 <= len(value.encode("utf-8", errors="strict")) <= maximum:
            return value
    except UnicodeError:
        pass
    raise SpaceError(category)


@total_ordering
@dataclass(frozen=True, slots=True)
class QualifiedNoteIdentity:
    """Path authority is supplied by the containment owner, never this value type."""

    space_id: SpaceId
    canonical_relative_path: str

    def __post_init__(self):
        _require_id(self.space_id)
        if type(self.canonical_relative_path) is not str:
            raise SpaceError("invalid_scope")

    def __lt__(self, other):
        if not isinstance(other, QualifiedNoteIdentity):
            return NotImplemented
        return self._key() < other._key()

    def _key(self):
        return self.space_id, self.canonical_relative_path.casefold(), self.canonical_relative_path


@dataclass(frozen=True, slots=True)
class QualifiedPath:
    """Unverified input; does not establish canonical note identity."""

    space_id: SpaceId
    relative_path: str

    def __post_init__(self):
        _require_id(self.space_id)
        _utf8(self.relative_path, 1024, "invalid_scope")


@dataclass(frozen=True, slots=True)
class KnowledgeSpacePolicy:
    read: Literal["allow", "deny"]
    write: Literal["allow", "deny"]
    indexing: Literal["enabled", "disabled"]
    dialects: frozenset[Dialect]
    capabilities: frozenset[Capability]

    def __post_init__(self):
        if (
            type(self.read) is not str
            or self.read not in ("allow", "deny")
            or type(self.write) is not str
            or self.write not in ("allow", "deny")
            or type(self.indexing) is not str
            or self.indexing not in ("enabled", "disabled")
            or type(self.dialects) is not frozenset
            or any(type(item) is not Dialect for item in self.dialects)
            or type(self.capabilities) is not frozenset
            or any(type(item) is not Capability for item in self.capabilities)
            or (self.read == "deny" and (self.write == "allow" or self.indexing == "enabled"))
        ):
            raise _invalid_configuration()


class KnowledgeSpaceDefinition:
    """Immutable private configuration; intentionally no generic serialization."""

    __slots__ = ("space_id", "label", "policy", "root_binding", "semantic_data_binding")

    def __init__(
        self,
        space_id: SpaceId,
        label: str | None,
        policy: KnowledgeSpacePolicy,
        root_binding: Path,
        semantic_data_binding: Path,
    ):
        if type(space_id) is not SpaceId or type(policy) is not KnowledgeSpacePolicy:
            raise _invalid_configuration()
        if not isinstance(root_binding, Path) or not isinstance(semantic_data_binding, Path):
            raise _invalid_configuration()
        if label is not None:
            _label(label)
        for name, value in (
            ("space_id", space_id),
            ("label", label),
            ("policy", policy),
            ("root_binding", root_binding),
            ("semantic_data_binding", semantic_data_binding),
        ):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("immutable definition")

    def __delattr__(self, name):
        raise AttributeError("immutable definition")

    def __repr__(self):
        return f"KnowledgeSpaceDefinition(space_id={self.space_id!r}, policy={self.policy!r})"

    def _values(self):
        return self.space_id, self.label, self.policy, self.root_binding, self.semantic_data_binding

    def __eq__(self, other):
        if type(other) is not KnowledgeSpaceDefinition:
            return NotImplemented
        return self._values() == other._values()

    def __hash__(self):
        return hash(self._values())

    def __reduce_ex__(self, protocol):
        raise TypeError("private configuration is not serializable")


@dataclass(frozen=True, slots=True)
class ReadScope:
    space_ids: tuple[SpaceId, ...]

    def __post_init__(self):
        if type(self.space_ids) is not tuple or not 1 <= len(self.space_ids) <= 8:
            raise SpaceError("invalid_scope")
        for item in self.space_ids:
            _require_id(item)
        object.__setattr__(self, "space_ids", tuple(sorted(set(self.space_ids))))


@dataclass(frozen=True, slots=True)
class WriteScope:
    space_id: SpaceId

    def __post_init__(self):
        _require_id(self.space_id)


class _PrivateImmutable:
    __slots__ = ()

    def __setattr__(self, name, value):
        raise AttributeError("immutable private binding")

    def __delattr__(self, name):
        raise AttributeError("immutable private binding")

    def __reduce_ex__(self, protocol):
        raise TypeError("private binding is not serializable")


class SpaceOwners(_PrivateImmutable):
    """Fixed private local references; no generic serialization or value equality.

    Construction belongs to space_owners; registry authorization never touches a
    reference. Capture/promotion are local owners only, not scoped v2 entry points.
    """

    __slots__ = ("vault", "relationships", "query", "duplicates", "capture", "promotion",
                 "hygiene", "semantic", "scheduler", "_definition", "_root_identity")

    vault: VaultService
    relationships: RelationshipService
    query: KnowledgeQueryService
    duplicates: DuplicateCandidateService
    capture: CaptureService
    promotion: PromotionService
    hygiene: KnowledgeHygieneService
    semantic: ImmutableSpaceInspection
    scheduler: DeferredSpaceIndexLifecycle | None

    def __init__(self, *, definition, root_identity, vault, relationships, query, duplicates, capture, promotion,
                 hygiene, semantic, scheduler):
        for name, value in (("_definition", definition), ("_root_identity", root_identity), ("vault", vault),
                            ("relationships", relationships), ("query", query),
                            ("duplicates", duplicates), ("capture", capture), ("promotion", promotion),
                            ("hygiene", hygiene), ("semantic", semantic), ("scheduler", scheduler)):
            object.__setattr__(self, name, value)

    def __repr__(self):
        return "SpaceOwners()"


class AuthorizedSpaceBinding(_PrivateImmutable):
    __slots__ = ("space_id", "policy", "_owners", "_registry_token")

    space_id: SpaceId
    policy: KnowledgeSpacePolicy
    _owners: SpaceOwners
    _registry_token: object

    def __new__(cls):
        raise TypeError("bindings are issued by the policy owner")

    def __repr__(self):
        return f"AuthorizedSpaceBinding(space_id={self.space_id!r}, policy={self.policy!r})"

    @classmethod
    def _issue(cls, definition, owners, token):
        result = object.__new__(cls)
        object.__setattr__(result, "space_id", definition.space_id)
        object.__setattr__(result, "policy", definition.policy)
        object.__setattr__(result, "_owners", owners)
        object.__setattr__(result, "_registry_token", token)
        return result


class SpaceRegistry(_PrivateImmutable):
    __slots__ = ("_definitions", "_owners", "_token", "_by_id")

    def __new__(cls):
        raise TypeError("registries are constructed from settings")

    def __repr__(self):
        return "SpaceRegistry()"

    @classmethod
    def from_settings(cls, settings: Settings, *, owners: Mapping[SpaceId, SpaceOwners] | None = None):
        definitions = settings._knowledge_space_definitions
        if definitions is None:
            # No path probes, storage preparation, or new fail-fast legacy checks.
            definitions = (
                KnowledgeSpaceDefinition(
                    DEFAULT_SPACE_ID,
                    None,
                    KnowledgeSpacePolicy("allow", "allow", "enabled", frozenset(Dialect), frozenset(Capability)),
                    settings.vault_path,
                    settings.semantic_data_path,
                ),
            )
        else:
            definitions = _validate_named_bindings(definitions, settings)
        result = object.__new__(cls)
        definitions = tuple(sorted(definitions, key=lambda item: item.space_id))
        owner_refs = {} if owners is None else dict(owners)
        by_id = {item.space_id: item for item in definitions}
        if any(type(key) is not SpaceId or key not in by_id for key in owner_refs):
            raise _invalid_configuration()
        if any(type(owner) is SpaceOwners and owner._definition != by_id[key] for key, owner in owner_refs.items()):
            raise _invalid_configuration()
        object.__setattr__(result, "_definitions", definitions)
        object.__setattr__(result, "_by_id", MappingProxyType(by_id))
        object.__setattr__(result, "_owners", MappingProxyType(owner_refs))
        object.__setattr__(result, "_token", object())
        return result

    def _binding(self, space_id):
        return AuthorizedSpaceBinding._issue(self._by_id[space_id], self._owners.get(space_id), self._token)

    def _accepts(self, binding: AuthorizedSpaceBinding) -> bool:
        return (
            type(binding) is AuthorizedSpaceBinding
            and binding._registry_token is self._token
            and binding.space_id in self._by_id
            and binding.policy is self._by_id[binding.space_id].policy
            and binding._owners is self._owners.get(binding.space_id)
        )


@dataclass(frozen=True, slots=True)
class SpacePolicyResolver:
    _registry: SpaceRegistry = field(repr=False)

    def _select(self, ids, *, write=False):
        selected = tuple(self._registry._by_id.get(item) for item in ids)
        if any(
            item is None or item.policy.read != "allow" or (write and item.policy.write != "allow") for item in selected
        ):
            raise SpaceError("unknown_or_denied_space")
        return selected

    @staticmethod
    def _requirements(selected, operation, required_dialects):
        if type(operation) is not Capability:
            raise SpaceError("unsupported_capability")
        if any(operation not in item.policy.capabilities for item in selected):
            raise SpaceError("unsupported_capability")
        if (
            type(required_dialects) is not frozenset
            or any(type(item) is not Dialect for item in required_dialects)
            or any(not required_dialects <= item.policy.dialects for item in selected)
        ):
            raise SpaceError("unsupported_dialect")
        if operation is Capability.SEMANTIC_RETRIEVAL:
            SpacePolicyResolver._indexing(selected)

    @staticmethod
    def _indexing(selected):
        if any(item.policy.indexing != "enabled" for item in selected):
            raise SpaceError("indexing_disabled")

    def authorize_read(
        self, scope: ReadScope | None, operation: Capability, required_dialects: frozenset[Dialect] = frozenset()
    ) -> tuple[AuthorizedSpaceBinding, ...]:
        if scope is not None and type(scope) is not ReadScope:
            raise SpaceError("invalid_scope")
        ids = (DEFAULT_SPACE_ID,) if scope is None else scope.space_ids
        selected = self._select(ids)
        self._requirements(selected, operation, required_dialects)
        return tuple(self._registry._binding(item) for item in ids)

    def authorize_write(
        self, scope: WriteScope | None, operation: Capability, legacy: bool = False
    ) -> AuthorizedSpaceBinding:
        if (
            type(legacy) is not bool
            or (scope is None and not legacy)
            or (scope is not None and type(scope) is not WriteScope)
        ):
            raise SpaceError("invalid_scope")
        space_id = DEFAULT_SPACE_ID if scope is None else scope.space_id
        selected = self._select((space_id,), write=True)
        self._requirements(selected, operation, frozenset())
        return self._registry._binding(space_id)

    def authorize_index(self, scope: WriteScope) -> AuthorizedSpaceBinding:
        """Internal maintenance needs read+indexing, never Markdown write permission."""
        if type(scope) is not WriteScope:
            raise SpaceError("invalid_scope")
        selected = self._select((scope.space_id,))
        self._indexing(selected)
        return self._registry._binding(scope.space_id)


def _control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _label(value):
    value = _utf8(value, 128, "invalid_configuration")
    if _control(value):
        raise _invalid_configuration()
    return value


def _native_path(value):
    value = _utf8(value, 4096, "invalid_configuration")
    # Check raw components before pathlib erases '.'; no shell interpretation.
    parts = re.split(r"[\\/]" if os.name == "nt" else r"/", value)
    path = Path(value)
    if (
        value != value.strip()
        or _control(value)
        or any(char in value for char in "~$%`")
        or any(part in (".", "..") for part in parts)
        or not path.is_absolute()
        or value.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./"))
    ):
        raise _invalid_configuration()
    if os.name == "nt":
        # Reject alternate streams, Win32 device names and trailing-dot/space aliases.
        for part in path.parts[1:]:
            if (
                ":" in part
                or part.endswith((".", " "))
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
            ):
                raise _invalid_configuration()
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid_configuration()
        result[key] = value
    return result


def _no_constant(_value):
    raise _invalid_configuration()


def _json_depth(raw):
    depth, in_string, escaped = 0, False, False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > 4:
                raise _invalid_configuration()
        elif char in "]}":
            depth -= 1


def _finite_set(value, enum, maximum):
    if type(value) is not list or len(value) > maximum:
        raise _invalid_configuration()
    if any(type(item) is not str for item in value) or len(set(value)) != len(value):
        raise _invalid_configuration()
    try:
        return frozenset(enum(item) for item in value)
    except ValueError:
        raise _invalid_configuration() from None


def parse_knowledge_spaces(raw: str) -> tuple[KnowledgeSpaceDefinition, ...]:
    """The single strict JSON/schema owner; performs no filesystem work."""
    raw = _utf8(raw, 32768, "invalid_configuration")
    if not raw.strip() or raw.startswith("\ufeff"):
        raise _invalid_configuration()
    _json_depth(raw)
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise _invalid_configuration() from None
    if (
        type(document) is not dict
        or set(document) != {"version", "spaces"}
        or type(document["version"]) is not int
        or document["version"] != 1
        or type(document["spaces"]) is not list
        or not 1 <= len(document["spaces"]) <= 8
    ):
        raise _invalid_configuration()
    required = {"space_id", "root", "semantic_data_path", "read", "write", "indexing", "dialects", "capabilities"}
    definitions = []
    for record in document["spaces"]:
        if type(record) is not dict or not required <= record.keys() or record.keys() - required - {"label"}:
            raise _invalid_configuration()
        space_id = SpaceId(validate_space_id(record["space_id"], category="invalid_configuration"))
        policy = KnowledgeSpacePolicy(
            record["read"],
            record["write"],
            record["indexing"],
            _finite_set(record["dialects"], Dialect, 2),
            _finite_set(record["capabilities"], Capability, 12),
        )
        label = _label(record["label"]) if "label" in record else None
        definitions.append(
            KnowledgeSpaceDefinition(
                space_id, label, policy, _native_path(record["root"]), _native_path(record["semantic_data_path"])
            )
        )
    ids = [item.space_id for item in definitions]
    if len(set(ids)) != len(ids) or ids.count(DEFAULT_SPACE_ID) != 1:
        raise _invalid_configuration()
    return tuple(sorted(definitions, key=lambda item: item.space_id))


def _directory_access(path: Path, *, writable: bool = False):
    if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK | (os.W_OK if writable else 0)):
        raise _invalid_configuration()
    with os.scandir(path) as entries:
        next(entries, None)


def _ordinary_missing_data_component(path: Path) -> bool:
    """Prove lexical absence without following a final symlink/reparse point.

    Windows dangling junctions fail exists() and are not symlinks, but lstat()
    still observes their directory entry. Any observed object is not a missing
    component, regardless of its reparse tag. Other I/O failures fail startup.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _resolve_data(path: Path, enabled: bool) -> Path:
    ancestor = path
    missing = []
    while not ancestor.exists():
        # An unresolved binding may contain only genuinely absent components.
        if not _ordinary_missing_data_component(ancestor) or ancestor.parent == ancestor:
            raise _invalid_configuration()
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    resolved = ancestor.resolve(strict=True)
    if not resolved.is_dir():
        raise _invalid_configuration()
    if enabled or missing:
        _directory_access(resolved, writable=enabled)
    # Disabled storage is structural only, including unreadable existing storage.
    return resolved.joinpath(*reversed(missing))


def _overlap(left: Path, right: Path) -> bool:
    if left == right or left in right.parents or right in left.parents:
        return True
    return left.exists() and right.exists() and left.samefile(right)


def _validate_named_bindings(definitions, settings):
    try:
        resolved = []
        for definition in definitions:
            root = definition.root_binding.resolve(strict=True)
            _directory_access(root, writable=definition.policy.write == "allow")
            data = _resolve_data(definition.semantic_data_binding, definition.policy.indexing == "enabled")
            resolved.append(
                KnowledgeSpaceDefinition(definition.space_id, definition.label, definition.policy, root, data)
            )
        default = next(item for item in resolved if item.space_id == DEFAULT_SPACE_ID)
        if default.root_binding != settings.vault_path.resolve(
            strict=True
        ) or default.semantic_data_binding != _resolve_data(settings.semantic_data_path, False):
            raise _invalid_configuration()
        for index, item in enumerate(resolved):
            root, data = item.root_binding, item.semantic_data_binding
            for other in resolved[index + 1 :]:
                if _overlap(root, other.root_binding) or _overlap(data, other.semantic_data_binding):
                    raise _invalid_configuration()
            for other in resolved:
                other_root = other.root_binding
                if data == other_root or data in other_root.parents:
                    raise _invalid_configuration()
                if other_root in data.parents:
                    if other is not item:
                        raise _invalid_configuration()
                    relative = data.relative_to(root)
                    if not any(
                        part in SEMANTIC_EXCLUDED_DIRECTORIES and root.joinpath(*relative.parts[: offset + 1]).is_dir()
                        for offset, part in enumerate(relative.parts[:-1])
                    ):
                        raise _invalid_configuration()
        # Existing DB/WAL/journal/SHM aliases can collide despite distinct directories.
        namespaces = []
        for item in resolved:
            for name in (
                "semantic-index.sqlite3",
                "semantic-index.sqlite3-wal",
                "semantic-index.sqlite3-shm",
                "semantic-index.sqlite3-journal",
            ):
                path = item.semantic_data_binding / name
                if path.exists() or path.is_symlink():
                    target = path.resolve(strict=True)
                    if not target.is_file() or target.parent != item.semantic_data_binding:
                        raise _invalid_configuration()
                    if any(target.samefile(previous) for previous in namespaces):
                        raise _invalid_configuration()
                    namespaces.append(target)
        return tuple(resolved)
    except (OSError, RuntimeError, ValueError):
        raise _invalid_configuration() from None
