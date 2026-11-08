"""Private v2 orchestration; portable facts never act as policy selectors."""

import json
from dataclasses import dataclass, field, replace

from app.services._space_writes import enqueue
from app.services.knowledge_spaces import Capability, SpaceError, SpaceId, WriteScope
from app.services.promotion import PromotionError, PromotionReview, PromotionService
from app.services.space_operations import OneSpaceResult


@dataclass(frozen=True, slots=True)
class ScopedPromotionReview:
    space_id: SpaceId
    provenance_version: int
    review: PromotionReview = field(repr=False)
    destination: str
    _binding: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ScopedPromotionDecision:
    space_id: SpaceId
    provenance_version: int
    decision: object = field(repr=False)
    _request_json: str = field(repr=False)
    _binding: object = field(repr=False, compare=False)


def _pair(ops, source, destination):
    source, destination = ops._path(source), ops._path(destination)
    if source.space_id != destination.space_id:
        raise SpaceError("invalid_scope")
    return source, destination


def _context(ops, wrapper, kind, *, write=False):
    if (type(wrapper) is not kind or type(wrapper.provenance_version) is not int
            or wrapper.provenance_version != 2
            or wrapper.space_id != wrapper._binding.space_id
            or not ops._resolver._registry._accepts(wrapper._binding)):
        raise SpaceError("invalid_scope")
    return (ops._resolver.authorize_write(WriteScope(wrapper.space_id), Capability.PROMOTION)
            if write else ops._read(wrapper.space_id, Capability.PROMOTION))


class _LocalAdvice:
    def __init__(self, owner):
        self.owner = owner

    def find_candidates(self, **kwargs):
        return self.owner.find_candidates(**kwargs, semantic_candidates=False)


def _service(owners, sid):
    # The existing owner and its exact VaultService remain responsible for execution.
    owner = owners.promotion
    return PromotionService(owner.vault_service, candidates=_LocalAdvice(owners.duplicates), _space_id=sid)


def _enqueue(binding, result):
    if result.category not in ("created", "appended") or not result.committed:
        return result
    relative = result.path if hasattr(result, "path") else result.destination
    pending = enqueue(binding, relative)
    return replace(result, index_state="index_pending" if pending else "index_unavailable")


def capture(ops, scope, request, *, cancel=None):
    binding = ops._resolver.authorize_write(scope, Capability.CAPTURE)
    if ops._admission is None:
        raise SpaceError("unavailable_space")
    with ops._admit(cancel) as token:
        owners = ops._owners(binding)
        if token is not None:
            token.check()
        result = owners.capture.capture(request, _space_id=binding.space_id)
        if token is not None and (result.committed or result.category == "commit_unknown"):
            token._write_completed = True
        return OneSpaceResult(binding.space_id, _enqueue(binding, result))


def review(ops, source, capture_id, destination, candidate_limit, *, cancel=None):
    source, destination = _pair(ops, source, destination)
    binding = ops._read(source.space_id, Capability.PROMOTION)
    if candidate_limit is not None:
        ops._read(source.space_id, Capability.DUPLICATE_CANDIDATES)
    with ops._admit(cancel):
        owners = ops._owners(binding)
        service = _service(owners, binding.space_id)
        snapshot = service.review(source.relative_path, capture_id, candidate_limit=candidate_limit)
        return ScopedPromotionReview(binding.space_id, 2, snapshot, destination.relative_path, binding)


def inspect_destination(ops, destination, *, cancel=None, missing_ok=False):
    destination = ops._path(destination)
    binding = ops._read(destination.space_id, Capability.PROMOTION)
    with ops._admit(cancel):
        result = _service(ops._owners(binding), binding.space_id).inspect_destination(
            destination.relative_path, missing_ok=missing_ok)
        return OneSpaceResult(binding.space_id, result)


def decide(ops, snapshot, request, *, cancel=None):
    binding = _context(ops, snapshot, ScopedPromotionReview)
    if type(request) is not dict or request.get("destination") != snapshot.destination:
        raise SpaceError("invalid_scope")
    with ops._admit(cancel):
        service = _service(ops._owners(binding), binding.space_id)
        live = service.review(snapshot.review.source_path, snapshot.review.capture_id)
        if live.source_sha256 != snapshot.review.source_sha256:
            raise PromotionError("source_changed")
        decision = service.decide(request, live)
        return ScopedPromotionDecision(binding.space_id, 2, decision,
                                       json.dumps(request, allow_nan=False), binding)


def apply(ops, decision, *, cancel=None):
    binding = _context(ops, decision, ScopedPromotionDecision, write=True)
    if ops._admission is None:
        raise SpaceError("unavailable_space")
    with ops._admit(cancel) as token:
        owners = ops._owners(binding)
        service = _service(owners, binding.space_id)
        request = json.loads(decision._request_json)
        live = service.review(decision.decision.source_path, decision.decision.capture_id)
        if service.decide(request, live) != decision.decision:
            raise PromotionError("source_changed")
        if token is not None:
            token.check()
        result = service.apply(request)
        if token is not None and (result.committed or result.category == "commit_unknown"):
            token._write_completed = True
        return OneSpaceResult(binding.space_id, _enqueue(binding, result))
