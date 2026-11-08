"""Legacy bounds and local owners behind the shared default policy boundary."""

from app.services.knowledge_hygiene import KnowledgeHygieneService
from app.services.knowledge_spaces import Capability, Dialect, ReadScope, SpaceError
from app.services.vault import VaultValidationError


class LegacyReadUnavailable(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__("Legacy read unavailable")


class LegacySemanticFailure(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__("Legacy semantic failure")


def _cli_folder(vault, folder):
    if not vault.vault_available():
        raise LegacyReadUnavailable("vault")
    if not folder.strip():
        return ""
    path = vault.resolve_path(folder)
    if not path.is_dir():
        raise VaultValidationError("Folder is unavailable")
    relative = path.relative_to(vault.vault_root).as_posix()
    return "" if relative == "." else relative

_OPERATIONS = {
    "read": Capability.READ_NOTE, "list": Capability.LIST_NOTES,
    "literal": Capability.LITERAL_SEARCH, "related": Capability.SEMANTIC_RETRIEVAL,
    "duplicates": Capability.DUPLICATE_CANDIDATES, "links": Capability.RELATIONSHIPS,
    "backlinks": Capability.RELATIONSHIPS, "hygiene": Capability.HYGIENE,
    "query": Capability.KNOWLEDGE_QUERY,
}


def authorize(operations, operation, arguments):
    """Pure compatibility preflight, also used before CLI owner composition."""
    capability = _OPERATIONS.get(operation)
    if capability is None:
        raise SpaceError("unsupported_capability")
    dialects = frozenset((Dialect.OBSIDIAN_WIKILINK,)) if operation in {"links", "backlinks"} else frozenset()
    binding = operations._read(None, capability, dialects)
    scope = ReadScope((binding.space_id,))
    if operation == "duplicates":
        try:
            operations._resolver.authorize_read(scope, Capability.SEMANTIC_RETRIEVAL)
        except SpaceError as exc:
            if exc.category not in {"unsupported_capability", "indexing_disabled"}:
                raise
            # Semantic advice is optional in the domain operation matrix. A
            # policy exclusion means unavailable evidence, never a raw search
            # or readiness probe. Implicit legacy policy still allows it all.
            arguments["semantic_candidates"] = False
            arguments.pop("prepare", None)
    if operation == "hygiene":
        request = arguments["request"]
        KnowledgeHygieneService._validate(request)
        if request.groups & {"relationships", "isolation"}:
            operations._resolver.authorize_read(scope, Capability.RELATIONSHIPS, frozenset(Dialect))
        if request.duplicate_source_limit:
            operations._resolver.authorize_read(scope, Capability.DUPLICATE_CANDIDATES)
    if operation == "query" and arguments["request"].semantic_text is not None:
        operations._resolver.authorize_read(scope, Capability.SEMANTIC_RETRIEVAL)
    return binding


def read(operations, operation, arguments, *, cancel):
    binding = authorize(operations, operation, arguments)
    with operations._admit(cancel):
        owners = operations._owners(binding)
        if operation == "read":
            return owners.vault.read_note(**arguments)
        if operation == "list":
            return owners.vault.list_notes(**arguments)
        if operation == "literal":
            if arguments.pop("cli", False):
                arguments["folder"] = _cli_folder(owners.vault, arguments["folder"])
            return owners.vault.search_notes(**arguments)
        if operation == "related":
            return _related(owners, **arguments)
        if operation == "duplicates":
            prepare = arguments.pop("prepare", None)
            if prepare is not None:
                prepare(owners.semantic)
            # Private runtime advice is intentionally nonsemantic; legacy owner
            # composition retains the existing optional semantic advice.
            return owners.duplicates.find_candidates(**arguments)
        if operation == "links":
            return owners.relationships.outgoing_relationships(arguments["path"])
        if operation == "backlinks":
            return owners.relationships.backlinks(arguments["path"])
        if operation == "hygiene":
            service = arguments.get("_legacy_service")
            if service is not None and owners._root_identity is None:
                return service.scan(arguments["request"])
            return owners.hygiene.scan(arguments["request"])
        return owners.query.query(arguments["request"])


def _related(owners, *, text, folder, limit, min_score, prepare=None, settings=None, cli=False):
    folder = (_cli_folder(owners.vault, folder) if cli else
              owners.vault.existing_relative_path(folder) if folder else "")
    if folder is None:
        return []
    if settings is not None and owners._root_identity is None:
        owners.semantic.reconfigure(vault_root=settings.vault_path, max_note_bytes=settings.max_note_bytes)
    if prepare is not None:
        prepare(owners.semantic)
    try:
        results = owners.semantic.search(text, folder=folder, limit=min(50, limit * 3), min_score=min_score)
    except Exception as exc:
        if settings is not None:
            raise LegacySemanticFailure(exc) from exc
        raise
    verified = []
    for result in results:
        path = owners.vault.verify_existing_markdown_path(result.path, folder=folder)
        if path is not None:
            verified.append((path, result))
            if len(verified) >= limit:
                break
    return verified
