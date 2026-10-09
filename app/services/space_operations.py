"""Qualified local operations and bounded Markdown-only read federation.

Semantic execution waits for D/E. Writes and scoped
capture/promotion wait for their bound lifecycle/provenance implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
from typing import Generic, TypeVar

from app.services.knowledge_hygiene import KnowledgeHygieneRequest, KnowledgeHygieneService
from app.services.knowledge_spaces import (
    AuthorizedSpaceBinding,
    Capability,
    Dialect,
    QualifiedNoteIdentity,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceId,
    SpaceOwners,
    SpacePolicyResolver,
)
from app.services.vault import NoteReadResult, NoteUnavailableError, VaultValidationError

T = TypeVar("T")


def _safe_local_errors(method):
    @wraps(method)
    def execute(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except VaultValidationError:
            raise VaultValidationError("Unsafe path") from None
        except UnicodeError:
            raise NoteUnavailableError("Note unavailable") from None
        except OSError:
            raise SpaceError("unavailable_space") from None
    return execute


@dataclass(frozen=True, slots=True)
class OneSpaceResult(Generic[T]):
    """All paths in the unchanged local payload inherit this authorized ID."""

    space_id: SpaceId
    result: T


@dataclass(frozen=True, slots=True)
class QualifiedNoteRead:
    identity: QualifiedNoteIdentity
    note: NoteReadResult


@dataclass(frozen=True, slots=True)
class SpaceOperations:
    _resolver: SpacePolicyResolver = field(repr=False)

    def scoped_list(self, scope=None, *, folder=None, limit=50, cancel=None, limits=None):
        from app.services._scoped_federation import federate

        return federate(self, mode="list", scope=scope, folder=folder, limit=limit,
                        cancel=cancel, limits=limits)

    def scoped_literal_search(self, text, scope=None, *, folder=None, limit=10, cancel=None, limits=None):
        from app.services._scoped_federation import federate

        return federate(self, mode="literal", text=text, scope=scope, folder=folder, limit=limit,
                        cancel=cancel, limits=limits)

    def scoped_query(self, request, *, cancel=None, limits=None):
        from app.services._scoped_federation import federate

        return federate(self, mode="query", request=request, cancel=cancel, limits=limits)

    def _read(self, space_id, operation, dialects=frozenset()) -> AuthorizedSpaceBinding:
        if space_id is not None and type(space_id) is not SpaceId:
            raise SpaceError("invalid_scope")
        scope = None if space_id is None else ReadScope((space_id,))
        binding, = self._resolver.authorize_read(scope, operation, dialects)
        return binding

    def _owners(self, binding: AuthorizedSpaceBinding) -> SpaceOwners:
        # Even trusted internal callers cannot consume another startup's grant.
        if not self._resolver._registry._accepts(binding):
            raise SpaceError("invalid_scope")
        if type(binding._owners) is not SpaceOwners:
            raise SpaceError("unavailable_space")
        owners = binding._owners
        if owners._root_identity is not None and owners.vault.root_binding_identity() != owners._root_identity:
            raise SpaceError("unavailable_space")
        return owners

    @staticmethod
    def _path(path):
        if type(path) is not QualifiedPath:
            raise SpaceError("invalid_scope")
        return path

    @staticmethod
    def _origin(origin):
        if origin is not None and type(origin) is not Dialect:
            raise SpaceError("invalid_scope")
        return frozenset(Dialect) if origin is None else frozenset((origin,))

    @staticmethod
    def _identity(binding, canonical):
        # Only call after the local VaultService has verified canonical identity.
        canonical = canonical.replace("\\", "/")
        if not 1 <= len(canonical.encode("utf-8")) <= 1024:
            # These operations have no partial-success contract.
            raise SpaceError("unavailable_space")
        return QualifiedNoteIdentity(binding.space_id, canonical)

    @_safe_local_errors
    def read_note(self, path: QualifiedPath) -> QualifiedNoteRead:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.READ_NOTE)
        note = self._owners(binding).vault.read_note(path.relative_path)
        return QualifiedNoteRead(self._identity(binding, note.path), note)

    @_safe_local_errors
    def outgoing(self, path: QualifiedPath, *, origin: Dialect | None = None) -> OneSpaceResult:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.RELATIONSHIPS, self._origin(origin))
        relationships = self._owners(binding).relationships
        if origin is Dialect.OBSIDIAN_WIKILINK:
            result = relationships.outgoing_relationships(path.relative_path)
        elif origin is Dialect.MARKDOWN_LINK:
            result = relationships.outgoing_markdown_relationships(path.relative_path)
        else:
            result = relationships.normalized_outgoing_relationships(path.relative_path)
        return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def backlinks(self, path: QualifiedPath, *, origin: Dialect | None = None) -> OneSpaceResult:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.RELATIONSHIPS, self._origin(origin))
        relationships = self._owners(binding).relationships
        if origin is Dialect.OBSIDIAN_WIKILINK:
            result = relationships.backlinks(path.relative_path)
        elif origin is Dialect.MARKDOWN_LINK:
            result = relationships.markdown_backlinks(path.relative_path)
        else:
            result = relationships.normalized_backlinks(path.relative_path)
        return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def duplicates(self, space_id: SpaceId | None = None, *, title: str, text: str = "",
                   folder: str = "", limit: int = 5) -> OneSpaceResult:
        binding = self._read(space_id, Capability.DUPLICATE_CANDIDATES)
        # Optional semantic advice is unavailable; do not call search at all.
        result = self._owners(binding).duplicates.find_candidates(
            title=title, text=text, folder=folder, limit=limit, semantic_candidates=False,
        )
        return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def hygiene(self, space_id: SpaceId | None = None,
                request: KnowledgeHygieneRequest = KnowledgeHygieneRequest()) -> OneSpaceResult:
        binding = self._read(space_id, Capability.HYGIENE)
        # Pure owner validation precedes requirement projection, never discovery.
        KnowledgeHygieneService._validate(request)
        scope = ReadScope((binding.space_id,))
        if request.groups & {"relationships", "isolation"}:
            self._resolver.authorize_read(scope, Capability.RELATIONSHIPS, frozenset(Dialect))
        if request.duplicate_source_limit:
            self._resolver.authorize_read(scope, Capability.DUPLICATE_CANDIDATES)
        # Existing bounded hygiene candidates never execute semantic search, and
        # preserve semantic_unavailable advisory coverage (ADR 0008).
        result = self._owners(binding).hygiene.scan(request)
        return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def inspect_index(self, space_id: SpaceId | None = None) -> OneSpaceResult[str]:
        binding = self._read(space_id, Capability.HYGIENE)
        return OneSpaceResult(binding.space_id, self._owners(binding).semantic.inspect_hygiene_index())
