"""Write orchestration over the exact registry-bound local owners."""

from app.services.knowledge_spaces import Capability, QualifiedPath, SpaceError
from app.services.space_operations import OneSpaceResult


def completed(token, result):
    if hasattr(token, "_write_completed") and (getattr(result, "status", None) in {
            "created", "appended", "unchanged", "already_applied"}
            or getattr(result, "committed", False)
            or getattr(result, "category", None) == "commit_unknown"):
        token._write_completed = True


def enqueue(binding, path, *, legacy=False):
    """A derived callback must never turn a durable commit into a retryable failure."""
    if binding.policy.indexing != "enabled" or binding._owners.scheduler is None:
        return False
    try:
        scheduler = binding._owners.scheduler
        callback = getattr(scheduler, "enqueue_legacy", scheduler.enqueue) if legacy else scheduler.enqueue
        return bool(callback(QualifiedPath(binding.space_id, path)))
    except Exception:
        return False


def note(ops, operation, scope, request, *, cancel=None, legacy=False, canonical_enqueue=False,
         _legacy_on_commit=None):
    capability = {"create": Capability.CREATE_NOTE, "append": Capability.APPEND_NOTE}[operation]
    binding = ops._resolver.authorize_write(scope, capability, legacy=legacy)
    if not legacy and ops._admission is None:
        raise SpaceError("unavailable_space")
    with ops._admit(cancel) as token:
        owners = ops._owners(binding)
        if token is not None:
            token.check()
        if not legacy:
            owners.vault.validate_scoped_write_path(
                (request.get("folder") or "Inbox") if operation == "create" else request["path"])
        result = getattr(owners.vault, operation + "_note")(**request)
        completed(token, result)
        if result.status in {"created", "appended"}:
            if legacy and owners._root_identity is None and _legacy_on_commit is not None:
                try:
                    _legacy_on_commit(result)
                except Exception:
                    pass  # Observability cannot erase an authoritative write.
            path = result.path.replace("\\", "/") if (
                not legacy or owners._root_identity is not None or canonical_enqueue) else result.path
            enqueue(binding, path, legacy=legacy and owners._root_identity is None)
        if not legacy:
            from dataclasses import replace

            result = replace(result, path=result.path.replace("\\", "/"))
        return result if legacy else OneSpaceResult(binding.space_id, result)


def compatibility(ops, operation, request, arguments, *, cancel=None):
    if operation in {"create", "append"}:
        return note(ops, operation, None, request, cancel=cancel, legacy=True, **arguments)
    if operation not in {"capture", "promotion_review", "promotion_destination", "promotion_apply"}:
        raise SpaceError("unsupported_capability")
    capability = Capability.CAPTURE if operation == "capture" else Capability.PROMOTION
    writing = operation in {"capture", "promotion_apply"}
    binding = (ops._resolver.authorize_write(None, capability, legacy=True) if writing
               else ops._read(None, capability))
    semantic_advice = True
    if operation == "promotion_review" and arguments.get("candidate_limit") is not None:
        ops._read(None, Capability.DUPLICATE_CANDIDATES)
        try:
            ops._read(None, Capability.SEMANTIC_RETRIEVAL)
        except SpaceError as exc:
            if exc.category not in {"unsupported_capability", "indexing_disabled"}:
                raise
            semantic_advice = False
    with ops._admit(cancel) as token:
        owners = ops._owners(binding)
        service = arguments.pop("_legacy_service", None)
        # Legacy injection seams may replace only an implicit legacy owner.
        if service is None or owners._root_identity is not None:
            service = owners.capture if operation == "capture" else owners.promotion
        if not semantic_advice and owners._root_identity is not None:
            from app.services._scoped_promotion import _LocalAdvice
            from app.services.promotion import PromotionService

            service = PromotionService(owners.promotion.vault_service, candidates=_LocalAdvice(owners.duplicates))
        if token is not None:
            token.check()
        if operation == "capture":
            result = service.capture(request)
        elif operation == "promotion_apply":
            result = service.apply(request)
        elif operation == "promotion_destination":
            return service.inspect_destination(**arguments)
        else:
            return service.review(**arguments)
        completed(token, result)
        if owners._root_identity is not None:
            from dataclasses import replace

            if result.committed and result.category in {"created", "appended"}:
                path = result.path if operation == "capture" else result.destination
                result = replace(result, index_state="index_pending" if enqueue(binding, path)
                                 else "index_unavailable")
        return result
