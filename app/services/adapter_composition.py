"""Bind legacy injection seams once; private named adapters accept operations only."""

from types import MappingProxyType

from app.services.knowledge_spaces import SpaceError, SpaceOwners, SpacePolicyResolver, SpaceRegistry
from app.services.space_operations import SpaceOperations


def legacy_operations(settings, *, vault, semantic=None, relationships=None, duplicates=None,
                      hygiene=None, query=None):
    settings.require_legacy_composition()
    registry = SpaceRegistry.from_settings(settings)
    definition, = registry._definitions
    owners = SpaceOwners(
        definition=definition, root_identity=None, vault=vault, semantic=semantic,
        relationships=relationships, duplicates=duplicates, hygiene=hygiene, query=query,
        capture=None, promotion=None, scheduler=None,
    )
    object.__setattr__(registry, "_owners", MappingProxyType({definition.space_id: owners}))
    return SpaceOperations(SpacePolicyResolver(registry))


def validate_private_operations(operations, *, settings=None, **raw_owners):
    if type(operations) is not SpaceOperations or any(value is not None for value in raw_owners.values()):
        raise SpaceError("invalid_configuration", reason="named_serving_unsupported")
    registry = operations._resolver._registry
    if any(type(registry._owners.get(d.space_id)) is not SpaceOwners for d in registry._definitions):
        raise SpaceError("invalid_configuration", reason="named_serving_unsupported")
    if settings is not None and settings.knowledge_spaces_json is not None:
        from app.services.space_admission import ScopedOperationAdmission
        configured = {definition.space_id: definition for definition in settings._knowledge_space_definitions}
        if (type(operations._admission) is not ScopedOperationAdmission
                or len(configured) != len(registry._definitions)
                or any(configured.get(definition.space_id) != definition for definition in registry._definitions)
                or any(owner._root_identity is None for owner in registry._owners.values())):
            raise SpaceError("invalid_configuration", reason="named_serving_unsupported")
    return operations


def validate_legacy_operations(settings, operations, **owners):
    settings.require_legacy_composition()
    validate_private_operations(operations)
    registry = operations._resolver._registry
    if len(registry._definitions) != 1:
        raise SpaceError("invalid_configuration")
    definition, = registry._definitions
    bundle = registry._owners[definition.space_id]
    if (definition.space_id.value != "default" or definition.root_binding != settings.vault_path
            or any(getattr(bundle, name) is not owner for name, owner in owners.items())):
        raise SpaceError("invalid_configuration")
    return operations
