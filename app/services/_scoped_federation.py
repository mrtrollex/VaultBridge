"""Sequential Markdown-only orchestration after whole-selection authorization."""

import stat
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import PurePosixPath

from app.services._scoped_vault import ScopedVaultSnapshot, validate_path
from app.services._vault_writes import UnsafeWritePathError
from app.services.knowledge_query import (
    InvalidKnowledgeQueryError,
    KnowledgeQuery,
    KnowledgeQueryService,
    RelationshipPredicate,
    UnsafeKnowledgeQueryScopeError,
    _bounded_text,
)
from app.services.knowledge_spaces import (
    Capability,
    Dialect,
    QualifiedNoteIdentity,
    QualifiedPath,
    ReadScope,
    SpaceError,
    SpaceOwners,
)
from app.services.scoped_budget import BudgetExhausted, CancellationToken, ScopedBudget
from app.services.scoped_reads import (
    QualifiedRelationshipPredicate,
    ScopedKnowledgeQuery,
    ScopedNoteMatch,
    ScopedReadResult,
    SpaceCoverage,
)
from app.services.vault import NoteUnavailableError


def _path(raw, *, folder=False):
    try:
        return validate_path(raw, folder=folder)
    except (ValueError, UnicodeError):
        raise UnsafeKnowledgeQueryScopeError() from None


def validate_scoped_query(request):
    if type(request) is not ScopedKnowledgeQuery or type(request.query) is not KnowledgeQuery:
        raise InvalidKnowledgeQueryError()
    query = request.query
    if query.paths or query.relationships:
        raise InvalidKnowledgeQueryError()
    if type(request.paths) is not tuple or len(request.paths) > 64:
        raise InvalidKnowledgeQueryError()
    if type(request.relationships) is not tuple or len(request.relationships) > 16:
        raise InvalidKnowledgeQueryError()
    for path in request.paths:
        if type(path) is not QualifiedPath:
            raise InvalidKnowledgeQueryError()
    for predicate in request.relationships:
        if (type(predicate) is not QualifiedRelationshipPredicate
                or type(predicate.direction) is not str
                or predicate.direction not in {"incoming", "outgoing"}
                or type(predicate.other) is not QualifiedPath
                or (predicate.origin is not None and type(predicate.origin) is not Dialect)):
            raise InvalidKnowledgeQueryError()
    # Reuse ADR 0006 validation verbatim, including qualified-only empty requests.
    projected = replace(query,
                        paths=tuple(path.relative_path for path in request.paths),
                        relationships=tuple(RelationshipPredicate(p.direction, p.other.relative_path,
                                                                 p.origin.value if p.origin else None)
                                            for p in request.relationships))
    validated = KnowledgeQueryService._validate_request(projected)
    for path in request.paths:
        _path(path.relative_path)
    for predicate in request.relationships:
        _path(predicate.other.relative_path)
    if query.folder is not None:
        _path(query.folder, folder=True)
    return validated


def federate(operations, *, mode, scope=None, limit=None, folder=None, text=None,
             request=None, cancel=None, limits=None):
    validated = None
    if mode == "query":
        validated = validate_scoped_query(request)
        scope, limit, folder = request.scope, request.query.limit, request.query.folder
    else:
        limit = (50 if mode == "list" else 10) if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidKnowledgeQueryError()
        if folder is not None:
            folder = _path(folder, folder=True)
        if mode == "literal":
            _bounded_text(text, 8192, allow_empty=False)
    # No root, owner, index, or model work before every policy is authorized.
    capability = {"list": Capability.LIST_NOTES, "literal": Capability.LITERAL_SEARCH,
                  "query": Capability.KNOWLEDGE_QUERY}[mode]
    bindings = operations._resolver.authorize_read(scope, capability)
    selected_ids = tuple(binding.space_id for binding in bindings)
    predicates = () if request is None else request.relationships
    paths = () if request is None else request.paths
    if any(path.space_id not in selected_ids for path in paths) or any(
            predicate.other.space_id not in selected_ids for predicate in predicates):
        raise SpaceError("invalid_scope")
    if predicates:
        dialects = frozenset(origin for predicate in predicates for origin in (
            (predicate.origin,) if predicate.origin else tuple(Dialect)))
        operations._resolver.authorize_read(ReadScope(selected_ids), Capability.RELATIONSHIPS, dialects)
    if request is not None and request.query.semantic_text is not None:
        raise SpaceError("unsupported_capability")
    for binding in bindings:
        if (not operations._resolver._registry._accepts(binding)
                or type(binding._owners) is not SpaceOwners):
            raise SpaceError("invalid_scope")
    cancel = CancellationToken() if cancel is None else cancel
    cancel.check()
    budget = ScopedBudget(selected_ids, limits=limits)
    buffered = []
    modification_times = {}
    coverage = []
    for binding in bindings:
        cancel.check()
        sid, owners = binding.space_id, binding._owners
        session = owners.vault.scoped_session(space_id=sid, budget=budget, cancel=cancel,
                                             root_identity=owners._root_identity)
        reasons, local = set(), []
        evaluated = enumerated = 0
        available = True
        try:
            session.__enter__()
            local_folder = folder
            folder_missing = False
            if folder is not None:
                try:
                    local_folder, folder_info = session.resolve(folder)
                except FileNotFoundError:
                    folder_missing = True
                except UnsafeWritePathError:
                    raise UnsafeKnowledgeQueryScopeError() from None
                else:
                    folder_missing = not stat.S_ISDIR(folder_info.st_mode)
                    local_folder = local_folder or "."
            empty_projection = mode == "query" and (
                (bool(paths) and not any(path.space_id == sid for path in paths))
                or any(predicate.other.space_id != sid for predicate in predicates)
            )
            snapshot = (ScopedVaultSnapshot((), {}, frozenset(), {}, ())
                        if empty_projection or folder_missing else session.discover())
            reasons.update(snapshot.reasons)
            if not predicates:
                reasons.discard("alias_limit")
            enumerated = len(snapshot.facts)
            if "discovery_limit" in reasons:
                available = False
            elif folder_missing or empty_projection:
                pass
            elif mode == "query":
                local_predicates = tuple(dict.fromkeys(predicates))
                if any(predicate.other.space_id != sid for predicate in local_predicates):
                    matches = ()
                else:
                    local_paths = (tuple(dict.fromkeys(_path(p.relative_path) for p in paths if p.space_id == sid))
                                   if paths else None)
                    matches, evaluated, available = owners.query.evaluate_supplied_nonsemantic(
                        replace(request.query, folder=local_folder),
                        snapshot=snapshot, session=session, paths=local_paths,
                        relationships=local_predicates, budget=budget, cancel=cancel,
                        reasons=reasons, validated=validated,
                    )
                local = [ScopedNoteMatch(QualifiedNoteIdentity(sid, path), PurePosixPath(path).stem)
                         for path in matches]
            else:
                for fact in snapshot.facts:
                    cancel.check()
                    if local_folder is not None and PurePosixPath(local_folder) not in PurePosixPath(fact.path).parents:
                        continue
                    try:
                        if mode == "list":
                            session.verify(fact)
                            snippet = None
                        else:
                            note = session.read(fact)
                            needle = text.casefold()
                            position = note.content.casefold().find(needle)
                            if position < 0 and needle not in PurePosixPath(fact.path).stem.casefold():
                                evaluated += 1
                                continue
                            snippet = (note.content[max(0, position - 120):position + len(text) + 220]
                                       if position >= 0 else note.content[:320]).replace("\n", " ").strip()[:400]
                        evaluated += 1
                        modified = datetime.fromtimestamp(
                            fact.modified_ns / 1e9, timezone.utc,
                        ).isoformat(timespec="seconds")
                        identity = QualifiedNoteIdentity(sid, fact.path)
                        modification_times[identity] = fact.modified_ns
                        local.append(ScopedNoteMatch(identity,
                                                     PurePosixPath(fact.path).stem, snippet,
                                                     modified if mode == "list" else None))
                    except (OSError, UnicodeError, NoteUnavailableError, UnsafeWritePathError):
                        reasons.add("note_unavailable")
                    except BudgetExhausted as exc:
                        reasons.add("content_limit" if exc.resource == "bytes" else "discovery_limit")
                        if exc.resource != "bytes":
                            local, available = [], False
                        break
            # One final verification pass; root loss invalidates the buffered space.
            facts = {fact.path: fact for fact in snapshot.facts}
            verified = []
            for item in local:
                cancel.check()
                try:
                    session.verify(facts[item.identity.canonical_relative_path])
                except (OSError, NoteUnavailableError, UnsafeWritePathError):
                    reasons.add("note_unavailable")
                else:
                    verified.append(item)
            local = verified
            session.verify_root()
        except (OSError, NoteUnavailableError, UnsafeWritePathError):
            local, available = [], False
            reasons.add("root_unavailable")
        except BudgetExhausted:
            local, available = [], False
            reasons.add("discovery_limit")
        finally:
            session.__exit__(None, None, None)
        if predicates and not available and "root_unavailable" not in reasons:
            reasons.add("relationship_limit")
        if not available:
            local = []
        state = "unavailable" if not available else "partial" if reasons else "complete"
        coverage.append(SpaceCoverage(sid, state, tuple(sorted(reasons)), "none",
                                      enumerated, evaluated, len(local)))
        buffered.extend(local)
    cancel.check()
    if all(row.state == "unavailable" for row in coverage):
        raise SpaceError("unavailable_space")
    def canonical(item):
        path = item.identity.canonical_relative_path
        return item.identity.space_id.value, path.casefold(), path
    if mode == "list":
        buffered.sort(key=lambda item: (-modification_times[item.identity], *canonical(item)))
    else:
        buffered.sort(key=canonical)
    return ScopedReadResult(tuple(buffered[:limit]), tuple(coverage),
                            "partial" if any(row.state != "complete" for row in coverage) else "complete",
                            "modified" if mode == "list" else "canonical_path", len(buffered) > limit)
