"""Private stopped-service CLI profiles using the existing runtime lifecycle."""

from app.services.knowledge_spaces import Capability, ReadScope, SpacePolicyResolver, SpaceRegistry
from app.services.persisted_space_reads import compose_persisted_space_reads
from app.services.space_owners import compose_space_registry
from app.services.space_runtime import SpaceRuntime, compose_space_runtime


def compose_write_profile(settings, operation, scope, *, candidate_limit=None):
    resolver = SpacePolicyResolver(SpaceRegistry.from_settings(settings))
    if operation == "review":
        read_scope = None if scope is None else ReadScope((scope.space_id,))
        resolver.authorize_read(read_scope, Capability.PROMOTION)
        if candidate_limit is not None:
            resolver.authorize_read(read_scope, Capability.DUPLICATE_CANDIDATES)
            if scope is None:
                # V1 optional semantic advice keeps the old persisted-read behavior.
                # Execution still reauthorizes its optional semantic requirement.
                return compose_persisted_space_reads(settings, promotion_review=True)
        return SpaceRuntime(compose_space_registry(settings), None, None, None)
    resolver.authorize_write(scope, Capability.CAPTURE if operation == "capture" else Capability.PROMOTION,
                             legacy=scope is None)
    return compose_space_runtime(settings)
