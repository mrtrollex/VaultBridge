"""Qualified local operations and bounded Markdown-only read federation.

Named semantic activation and scoped v2 writes use the explicit private runtime.
Public named-serving adapters remain gated on VB-152.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Generic, TypeVar

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
    WriteScope,
)
from app.services.vault import NoteReadResult, NoteUnavailableError, VaultValidationError, _owned_note_reads

if TYPE_CHECKING:
    from app.services.space_admission import ScopedOperationAdmission

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
    _admission: ScopedOperationAdmission | None = field(default=None, repr=False)

    def compatibility_read(self, operation, *, cancel=None, **arguments):
        """Default-only legacy dispatch, without federation's new bounds."""
        from app.services._space_compatibility import read

        return read(self, operation, arguments, cancel=cancel)

    def compatibility_write(self, operation, *, request=None, cancel=None, **arguments):
        """Authorize default before legacy writes/reviews, retaining their owner contract."""
        from app.services._space_writes import compatibility

        return compatibility(self, operation, request, arguments, cancel=cancel)

    @_safe_local_errors
    def scoped_create_note(self, scope: WriteScope, request, *, cancel=None):
        from app.services._space_writes import note

        return note(self, "create", scope, request, cancel=cancel)

    @_safe_local_errors
    def scoped_append_note(self, scope: WriteScope, request, *, cancel=None):
        from app.services._space_writes import note

        return note(self, "append", scope, request, cancel=cancel)

    def authorize_read_requirements(self, operation, scope, *, request=None):
        """Pure profile preflight, before composing any operational owner.

        No grant escapes this check: execution reauthorizes against the finalized
        owner map. The full registry, policies and default binding are retained.
        """
        capability = {"list": Capability.LIST_NOTES, "literal": Capability.LITERAL_SEARCH,
                      "related": Capability.SEMANTIC_RETRIEVAL, "query": Capability.KNOWLEDGE_QUERY,
                      "hygiene": Capability.HYGIENE}[operation]
        bindings = self._resolver.authorize_read(scope, capability)
        if operation == "query":
            from app.services._scoped_federation import validate_scoped_query
            validate_scoped_query(request)
            selected_ids = tuple(binding.space_id for binding in bindings)
            if any(path.space_id not in selected_ids for path in request.paths) or any(
                    predicate.other.space_id not in selected_ids for predicate in request.relationships):
                raise SpaceError("invalid_scope")
            if request.relationships:
                dialects = frozenset(origin for predicate in request.relationships for origin in (
                    (predicate.origin,) if predicate.origin else tuple(Dialect)))
                self._resolver.authorize_read(scope, Capability.RELATIONSHIPS, dialects)
            if request.query.semantic_text is not None:
                self._resolver.authorize_read(scope, Capability.SEMANTIC_RETRIEVAL)
        if operation == "hygiene":
            KnowledgeHygieneService._validate(request)
            if request.groups & {"relationships", "isolation"}:
                self._resolver.authorize_read(scope, Capability.RELATIONSHIPS, frozenset(Dialect))
            if request.duplicate_source_limit:
                self._resolver.authorize_read(scope, Capability.DUPLICATE_CANDIDATES)
        return tuple(binding.space_id for binding in bindings)

    def probe_default_readiness(self):
        try:
            binding = self._read(None, Capability.SEMANTIC_RETRIEVAL)
            with self._admit():
                owners = self._owners(binding)
                return owners.vault.vault_available() and owners.semantic.probe_search_availability()
        except (OSError, UnicodeError, NoteUnavailableError):
            return False
        except SpaceError as exc:
            if exc.category in {"unknown_or_denied_space", "unsupported_capability", "unsupported_dialect",
                                "indexing_disabled", "unavailable_space", "operation_cancelled"}:
                return False
            raise

    @contextmanager
    def _admit(self, cancel=None):
        if cancel is None:
            from app.read_execution import current_read_cancellation
            cancel = current_read_cancellation.get()
        # Legacy/directly constructed operations preserve their existing behavior.
        if self._admission is None:
            yield cancel
        else:
            with self._admission.operation(cancel) as token, _owned_note_reads():
                yield token

    @_safe_local_errors
    def scoped_capture(self, scope: WriteScope, request, *, cancel=None):
        from app.services._scoped_promotion import capture
        return capture(self, scope, request, cancel=cancel)

    @_safe_local_errors
    def scoped_promotion_review(self, source: QualifiedPath, capture_id, *,
                                destination: QualifiedPath, candidate_limit=None, cancel=None):
        from app.services._scoped_promotion import review
        return review(self, source, capture_id, destination, candidate_limit, cancel=cancel)

    @_safe_local_errors
    def scoped_promotion_destination(self, destination: QualifiedPath, *, cancel=None, missing_ok=False):
        from app.services._scoped_promotion import inspect_destination
        return inspect_destination(self, destination, cancel=cancel, missing_ok=missing_ok)

    @_safe_local_errors
    def scoped_promotion_decide(self, snapshot, request, *, cancel=None):
        from app.services._scoped_promotion import decide
        return decide(self, snapshot, request, cancel=cancel)

    @_safe_local_errors
    def scoped_promotion_apply(self, decision, *, cancel=None):
        from app.services._scoped_promotion import apply
        return apply(self, decision, cancel=cancel)

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

    def scoped_semantic_search(self, text, scope=None, *, folder=None, limit=5,
                               min_score=0.28, cancel=None, limits=None):
        from app.services._scoped_federation import federate_semantic

        return federate_semantic(self, text, scope=scope, folder=folder, limit=limit,
                                 min_score=min_score, cancel=cancel, limits=limits)

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
        if owners._root_identity is not None:
            try:
                identity = owners.vault.root_binding_identity()
            except (OSError, NoteUnavailableError):
                # Named binding validation is an operational availability check.
                # Do not translate legacy owner errors or arbitrary failures.
                raise SpaceError("unavailable_space") from None
            if identity != owners._root_identity:
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
    def read_note(self, path: QualifiedPath, *, cancel=None) -> QualifiedNoteRead:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.READ_NOTE)
        with self._admit(cancel):
            note = self._owners(binding).vault.read_note(path.relative_path)
            return QualifiedNoteRead(self._identity(binding, note.path), note)

    @_safe_local_errors
    def outgoing(self, path: QualifiedPath, *, origin: Dialect | None = None, cancel=None) -> OneSpaceResult:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.RELATIONSHIPS, self._origin(origin))
        with self._admit(cancel):
            relationships = self._owners(binding).relationships
            if origin is Dialect.OBSIDIAN_WIKILINK:
                result = relationships.outgoing_relationships(path.relative_path)
            elif origin is Dialect.MARKDOWN_LINK:
                result = relationships.outgoing_markdown_relationships(path.relative_path)
            else:
                result = relationships.normalized_outgoing_relationships(path.relative_path)
            return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def backlinks(self, path: QualifiedPath, *, origin: Dialect | None = None, cancel=None) -> OneSpaceResult:
        path = self._path(path)
        binding = self._read(path.space_id, Capability.RELATIONSHIPS, self._origin(origin))
        with self._admit(cancel):
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
                   folder: str = "", limit: int = 5, cancel=None) -> OneSpaceResult:
        binding = self._read(space_id, Capability.DUPLICATE_CANDIDATES)
        with self._admit(cancel):
            # Optional semantic advice is unavailable; do not call search at all.
            result = self._owners(binding).duplicates.find_candidates(
                title=title, text=text, folder=folder, limit=limit, semantic_candidates=False,
            )
            return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def hygiene(self, space_id: SpaceId | None = None,
                request: KnowledgeHygieneRequest = KnowledgeHygieneRequest(), *, cancel=None) -> OneSpaceResult:
        binding = self._read(space_id, Capability.HYGIENE)
        # Pure owner validation precedes requirement projection, never discovery.
        KnowledgeHygieneService._validate(request)
        scope = ReadScope((binding.space_id,))
        if request.groups & {"relationships", "isolation"}:
            self._resolver.authorize_read(scope, Capability.RELATIONSHIPS, frozenset(Dialect))
        if request.duplicate_source_limit:
            self._resolver.authorize_read(scope, Capability.DUPLICATE_CANDIDATES)
        with self._admit(cancel):
            # Existing bounded hygiene candidates never execute semantic search, and
            # preserve semantic_unavailable advisory coverage (ADR 0008).
            result = self._owners(binding).hygiene.scan(request)
            return OneSpaceResult(binding.space_id, result)

    @_safe_local_errors
    def inspect_index(self, space_id: SpaceId | None = None, *, cancel=None) -> OneSpaceResult[str]:
        binding = self._read(space_id, Capability.HYGIENE)
        with self._admit(cancel):
            return OneSpaceResult(binding.space_id, self._owners(binding).semantic.inspect_hygiene_index())
