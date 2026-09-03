from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


_AVAILABLE_STATES = frozenset(
    {"available", "enabled", "true", "yes", "1"}
)
_UNAVAILABLE_STATES = frozenset(
    {"unavailable", "disabled", "false", "no", "0", "unknown", "uncertain"}
)


def relocatable_by_grasp(runtime: Any, entity_id: str) -> bool:
    """Return whether recursive recovery may pick and relocate an entity."""

    metadata = _catalog_metadata(runtime, entity_id)
    if not metadata or not bool(metadata.get("movable", False)):
        return False
    entity = _world_entity(runtime, entity_id)
    if _is_fixed(metadata, entity):
        return False
    return _grasp_available(metadata, entity)


def relocatable_entity_ids(
    runtime: Any,
    entity_ids: Iterable[Any],
) -> tuple[str, ...]:
    return tuple(
        entity_id
        for entity_id in dict.fromkeys(
            str(value) for value in entity_ids if str(value)
        )
        if relocatable_by_grasp(runtime, entity_id)
    )


def non_relocatable_entity_ids(
    runtime: Any,
    entity_ids: Iterable[Any],
) -> tuple[str, ...]:
    return tuple(
        entity_id
        for entity_id in dict.fromkeys(
            str(value) for value in entity_ids if str(value)
        )
        if not relocatable_by_grasp(runtime, entity_id)
    )


def _catalog_metadata(runtime: Any, entity_id: str) -> Mapping[str, Any]:
    perception = getattr(runtime, "perception", None)
    catalog = getattr(perception, "catalog", None)
    if not isinstance(catalog, Mapping):
        return {}
    value = catalog.get(str(entity_id))
    return value if isinstance(value, Mapping) else {}


def _world_entity(runtime: Any, entity_id: str) -> Any:
    world = getattr(runtime, "world", None)
    entities = getattr(world, "entities", None)
    if not isinstance(entities, Mapping):
        return None
    return entities.get(str(entity_id))


def _is_fixed(metadata: Mapping[str, Any], entity: Any) -> bool:
    attributes = metadata.get("attributes")
    if isinstance(attributes, Mapping) and bool(attributes.get("fixed")):
        return True
    if bool(metadata.get("fixed")):
        return True
    entity_attributes = getattr(entity, "attributes", None)
    if (
        isinstance(entity_attributes, Mapping)
        and bool(entity_attributes.get("fixed"))
    ):
        return True
    semantic = getattr(entity, "semantic", None)
    semantic_attributes = getattr(semantic, "attributes", None)
    return bool(
        isinstance(semantic_attributes, Mapping)
        and semantic_attributes.get("fixed")
    )


def _grasp_available(metadata: Mapping[str, Any], entity: Any) -> bool:
    explicit = getattr(entity, "interaction_capabilities", None)
    affordances = getattr(entity, "affordances", ())
    if not isinstance(explicit, Mapping) or not explicit:
        explicit = metadata.get("interaction_capabilities")
        affordances = metadata.get("affordances") or ()
    if isinstance(explicit, Mapping):
        for key in ("grasp", "graspable"):
            if key in explicit:
                return _capability_available(explicit[key])
    return "graspable" in {str(value) for value in affordances or ()}


def _capability_available(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().casefold()
    if normalized in _AVAILABLE_STATES:
        return True
    if normalized in _UNAVAILABLE_STATES:
        return False
    return False


__all__ = [
    "non_relocatable_entity_ids",
    "relocatable_by_grasp",
    "relocatable_entity_ids",
]
