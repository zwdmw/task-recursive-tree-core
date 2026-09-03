from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Mapping

from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    ExecutionPolicy,
    GraphDelta,
    NodeOrigin,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)

from .translation import GEMINI_ER2_METADATA_KEY


@dataclass(frozen=True)
class _PlacementStep:
    spec: TaskNodeSpec
    object_id: str
    destination_id: str
    region_ref: str
    relation: str
    target_selector: str

    @property
    def group_key(self) -> tuple[str, str, str, str]:
        return (
            self.destination_id,
            self.region_ref,
            self.relation,
            self.target_selector,
        )


def coordinate_sequence_placement_layouts(
    definition: TaskTreeDefinition,
) -> TaskTreeDefinition:
    """Insert one shared layout producer for each compatible place run."""

    specs = {
        command.spec.node_id: command.spec
        for command in definition.delta.nodes
    }
    child_ids_by_parent: dict[str, list[str]] = {}
    edge_orders: dict[tuple[str, str], int] = {}
    for command in definition.delta.edges:
        edge = command.edge
        if edge.kind is not EdgeKind.CHILD:
            continue
        child_ids_by_parent.setdefault(edge.parent_id, []).append(
            edge.child_id
        )
        edge_orders[(edge.parent_id, edge.child_id)] = edge.order
    for parent_id, child_ids in child_ids_by_parent.items():
        child_ids.sort(
            key=lambda child_id: edge_orders[(parent_id, child_id)]
        )

    replacements: dict[str, TaskNodeSpec] = {}
    planners_before: dict[str, list[TaskNodeSpec]] = {}
    rewritten_children: dict[str, tuple[str, ...]] = {}

    for parent in tuple(specs.values()):
        if parent.control_kind is not ControlKind.SEQUENCE:
            continue
        child_ids = child_ids_by_parent.get(parent.node_id, [])
        if not child_ids:
            continue

        new_child_ids: list[str] = []
        changed = False
        index = 0
        while index < len(child_ids):
            first = _placement_step(specs[child_ids[index]])
            if first is None:
                new_child_ids.append(child_ids[index])
                index += 1
                continue

            run = [first]
            end = index + 1
            while end < len(child_ids):
                candidate = _placement_step(specs[child_ids[end]])
                if candidate is None or candidate.group_key != first.group_key:
                    break
                run.append(candidate)
                end += 1

            object_ids = tuple(step.object_id for step in run)
            if len(run) < 2 or len(set(object_ids)) != len(object_ids):
                new_child_ids.extend(child_ids[index:end])
                index = end
                continue

            planner, updated_places = _shared_layout_group(
                parent_id=parent.node_id,
                root_id=definition.root_id,
                steps=tuple(run),
            )
            if planner.node_id in specs:
                new_child_ids.extend(child_ids[index:end])
                index = end
                continue

            planners_before.setdefault(run[0].spec.node_id, []).append(
                planner
            )
            replacements.update(
                {place.node_id: place for place in updated_places}
            )
            new_child_ids.append(planner.node_id)
            new_child_ids.extend(step.spec.node_id for step in run)
            changed = True
            index = end

        if changed:
            rewritten_children[parent.node_id] = tuple(new_child_ids)
            replacements[parent.node_id] = _with_source_children(
                parent,
                new_child_ids,
            )

    if not rewritten_children:
        return definition

    ordered_specs: list[TaskNodeSpec] = []
    for command in definition.delta.nodes:
        ordered_specs.extend(
            planners_before.get(command.spec.node_id, ())
        )
        ordered_specs.append(
            replacements.get(command.spec.node_id, command.spec)
        )

    rewritten_edges: list[TaskEdge] = []
    emitted_parents: set[str] = set()
    for command in definition.delta.edges:
        edge = command.edge
        if (
            edge.kind is EdgeKind.CHILD
            and edge.parent_id in rewritten_children
        ):
            if edge.parent_id not in emitted_parents:
                rewritten_edges.extend(
                    TaskEdge(
                        parent_id=edge.parent_id,
                        child_id=child_id,
                        kind=EdgeKind.CHILD,
                        order=order,
                    )
                    for order, child_id in enumerate(
                        rewritten_children[edge.parent_id]
                    )
                )
                emitted_parents.add(edge.parent_id)
            continue
        rewritten_edges.append(edge)

    return replace(
        definition,
        delta=GraphDelta.from_specs(
            tuple(ordered_specs),
            tuple(rewritten_edges),
            root_id=definition.delta.root_id,
        ),
    )


def _placement_step(spec: TaskNodeSpec) -> _PlacementStep | None:
    if (
        spec.task_type.casefold() != "place"
        or spec.operation_kind is not OperationKind.DECOMPOSER
    ):
        return None
    params = spec.parameters
    if any(
        params.get(key) is not None
        for key in (
            "target_ref",
            "layout_producer_node_id",
            "batch_object_ids",
            "reservation_group",
            "reservation_index",
        )
    ):
        return None

    object_ids = _entity_ids(
        params,
        direct_keys=("object_ids", "object_id"),
        roles=("manipuland", "object", "subject"),
    )
    destination_ids = _entity_ids(
        params,
        direct_keys=(
            "destination_ids",
            "destination_id",
            "region_owner_id",
        ),
        roles=("destination", "region_owner"),
    )
    region_ref = _single_text(params.get("region_ref"))
    relation = _single_text(params.get("relation"))
    target_selector = _single_text(params.get("target_selector"))
    if (
        len(object_ids) != 1
        or len(destination_ids) != 1
        or region_ref is None
        or relation is None
        or target_selector is None
    ):
        return None

    return _PlacementStep(
        spec=spec,
        object_id=object_ids[0],
        destination_id=destination_ids[0],
        region_ref=region_ref,
        relation=relation.casefold(),
        target_selector=target_selector.casefold(),
    )


def _shared_layout_group(
    *,
    parent_id: str,
    root_id: str,
    steps: tuple[_PlacementStep, ...],
) -> tuple[TaskNodeSpec, tuple[TaskNodeSpec, ...]]:
    first = steps[0]
    object_ids = tuple(step.object_id for step in steps)
    payload = {
        "parent_id": parent_id,
        "destination_id": first.destination_id,
        "region_ref": first.region_ref,
        "relation": first.relation,
        "target_selector": first.target_selector,
        "object_ids": list(object_ids),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    producer_id = (
        f"{parent_id}/compiler/shared-placement-layout-{digest}"
    )
    reservation_group = f"place-sequence-{digest}"
    target_ref = _layout_target_ref(producer_id, reservation_group)
    contract = _artifact_contract(producer_id, parent_id)
    planner_params = {
        "system_check": "plan_region_layout",
        "object_ids": list(object_ids),
        "batch_object_ids": list(object_ids),
        "destination_ids": [first.destination_id],
        "participants": {
            "object": list(object_ids),
            "destination": [first.destination_id],
        },
        "relation": first.relation,
        "region_ref": first.region_ref,
        "target_selector": first.target_selector,
        "reservation_group": reservation_group,
        "lock_region_selection": True,
        "target_ref": target_ref,
        "layout_producer_node_id": producer_id,
        "layout_continuation_scope_id": parent_id,
        GEMINI_ER2_METADATA_KEY: {
            "schema": "gemini_er2_node/1.0",
            "root_id": root_id,
            "parent_id": parent_id,
            "node_kind": "planning",
            "task_type": "select_placement_space",
            "object_ref": None,
            "actor_ref": None,
            "from_state": None,
            "to_state": None,
            "preconditions": {},
            "goal": {},
            "goal_scope": "world",
            "obligations": [],
            "children": [],
            "child_policy": "sequence",
            "decomposer_ref": None,
            "tool_ref": None,
            "action_ref": None,
            "retry_policy": {
                "max_attempts": 1,
                "max_repairs": 0,
            },
            "resource_policy": {},
            "origin": "kernel_compiler",
            "metadata": {
                "adapter_injected": True,
                "role": "shared_sequence_placement_space_selection",
                "artifact_produces": [contract],
            },
            "preexpanded": False,
        },
    }
    planner = TaskNodeSpec(
        node_id=producer_id,
        task_type="select_placement_space",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        execution_policy=ExecutionPolicy.REQUIRE_EXECUTION,
        parameters=planner_params,
        max_attempts=1,
        max_repairs=0,
    )

    updated_places = tuple(
        _with_shared_layout(
            step.spec,
            object_ids=object_ids,
            producer_id=producer_id,
            target_ref=target_ref,
            continuation_scope_id=parent_id,
            reservation_group=reservation_group,
            reservation_index=index,
        )
        for index, step in enumerate(steps)
    )
    return planner, updated_places


def _with_shared_layout(
    spec: TaskNodeSpec,
    *,
    object_ids: tuple[str, ...],
    producer_id: str,
    target_ref: str,
    continuation_scope_id: str,
    reservation_group: str,
    reservation_index: int,
) -> TaskNodeSpec:
    params = copy.deepcopy(dict(spec.parameters))
    params.update(
        {
            "batch_object_ids": list(object_ids),
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": continuation_scope_id,
            "reservation_group": reservation_group,
            "reservation_index": reservation_index,
        }
    )
    return replace(spec, parameters=params)


def _with_source_children(
    spec: TaskNodeSpec,
    child_ids: list[str],
) -> TaskNodeSpec:
    params = copy.deepcopy(dict(spec.parameters))
    source = params.get(GEMINI_ER2_METADATA_KEY)
    if isinstance(source, Mapping):
        source = copy.deepcopy(dict(source))
        source["children"] = list(child_ids)
        source["preexpanded"] = True
        params[GEMINI_ER2_METADATA_KEY] = source
    return replace(spec, parameters=params)


def _entity_ids(
    params: Mapping[str, Any],
    *,
    direct_keys: tuple[str, ...],
    roles: tuple[str, ...],
) -> tuple[str, ...]:
    for key in direct_keys:
        values = _text_values(params.get(key))
        if values:
            return values
    participants = params.get("participants")
    if not isinstance(participants, Mapping):
        return ()
    for role in roles:
        values = _text_values(participants.get(role))
        if values:
            return values
    return ()


def _text_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        value = (
            value.get("entity_ids")
            or value.get("entity_id")
            or value.get("ids")
        )
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        dict.fromkeys(
            str(item)
            for item in value
            if item is not None and str(item).strip()
        )
    )


def _single_text(value: Any) -> str | None:
    values = _text_values(value)
    return values[0] if len(values) == 1 else None


def _layout_target_ref(producer_node_id: str, group: str) -> str:
    digest = hashlib.sha256(
        f"{producer_node_id}:{group}:layout_targets".encode("utf-8")
    ).hexdigest()[:24]
    return f"layout-targets-{digest}"


def _artifact_contract(
    producer_node_id: str,
    continuation_node_id: str,
) -> dict[str, Any]:
    return {
        "schema": "task_artifact/1.0",
        "artifact_kind": "layout_targets",
        "producer_node_id": producer_node_id,
        "continuation_node_id": continuation_node_id,
        "ref_key": "target_ref",
        "required": True,
    }


__all__ = ["coordinate_sequence_placement_layouts"]
