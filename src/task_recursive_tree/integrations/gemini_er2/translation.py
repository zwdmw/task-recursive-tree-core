from __future__ import annotations

import copy
from dataclasses import fields, is_dataclass
from enum import Enum
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
from task_recursive_tree.world.regions import (
    RegionRequirement,
    RegionRequirementKind,
    RequirementPhase,
)


GEMINI_ER2_METADATA_KEY = "__gemini_er2__"


class HarnessTreeTranslationError(ValueError):
    """Raised when a Harness tree DTO cannot be represented deterministically."""


def translate_harness_tree(tree: Any) -> TaskTreeDefinition:
    """Convert a Harness TaskTree-shaped DTO into a kernel tree definition.

    The adapter intentionally uses structural access instead of importing
    GeminiER2Harness. Both its dataclasses and their ``to_dict`` payloads are
    accepted.
    """

    root_id = _required_text(_field(tree, "root_id"), "tree.root_id")
    indexed = _index_nodes(_field(tree, "nodes"))
    if root_id not in indexed:
        raise HarnessTreeTranslationError(
            f"Harness tree root is missing from nodes: {root_id}"
        )

    children_by_id: dict[str, tuple[str, ...]] = {}
    declared_parent_by_id: dict[str, str | None] = {}
    for node_id, node in indexed.items():
        spec = _node_spec(node)
        children = tuple(
            _required_text(value, f"{node_id}.spec.children")
            for value in (_field(spec, "children", ()) or ())
        )
        if len(set(children)) != len(children):
            raise HarnessTreeTranslationError(
                f"Harness node declares duplicate children: {node_id}"
            )
        missing = [child_id for child_id in children if child_id not in indexed]
        if missing:
            raise HarnessTreeTranslationError(
                f"Harness node {node_id} references missing children: {missing}"
            )
        parent_id = _field(spec, "parent_id")
        declared_parent_by_id[node_id] = (
            str(parent_id) if parent_id is not None else None
        )
        children_by_id[node_id] = children

    if declared_parent_by_id[root_id] is not None:
        raise HarnessTreeTranslationError(
            "Harness tree root must not declare a parent"
        )

    parent_by_id: dict[str, str] = {}
    for parent_id, children in children_by_id.items():
        for child_id in children:
            previous = parent_by_id.get(child_id)
            if previous is not None and previous != parent_id:
                raise HarnessTreeTranslationError(
                    f"Harness node has multiple parents: {child_id}"
                )
            declared = declared_parent_by_id[child_id]
            if declared is not None and declared != parent_id:
                raise HarnessTreeTranslationError(
                    f"Harness parent mismatch for {child_id}: "
                    f"{declared} != {parent_id}"
                )
            parent_by_id[child_id] = parent_id

    for node_id, declared_parent in declared_parent_by_id.items():
        if node_id == root_id:
            continue
        if declared_parent is not None and parent_by_id.get(node_id) != declared_parent:
            raise HarnessTreeTranslationError(
                f"Harness parent {declared_parent} does not list child {node_id}"
            )

    ordered_ids = _preorder(root_id, children_by_id)
    unreachable = sorted(set(indexed).difference(ordered_ids))
    if unreachable:
        raise HarnessTreeTranslationError(
            f"Harness tree contains unreachable nodes: {unreachable}"
        )

    tree_metadata = {
        "task_id": _plain_value(_field(tree, "task_id")),
        "program_ref": _plain_value(_field(tree, "program_ref")),
        "world_revision": _plain_value(_field(tree, "world_revision")),
        "metadata": _plain_mapping(_field(tree, "metadata", {}), "tree.metadata"),
    }
    specs = tuple(
        _translate_node(
            indexed[node_id],
            root_id=root_id,
            tree_metadata=tree_metadata if node_id == root_id else None,
        )
        for node_id in ordered_ids
    )
    edges = tuple(
        TaskEdge(
            parent_id=parent_id,
            child_id=child_id,
            kind=_edge_kind(indexed[child_id]),
            order=order,
        )
        for parent_id in ordered_ids
        for order, child_id in enumerate(children_by_id[parent_id])
    )
    return TaskTreeDefinition(
        root_id=root_id,
        delta=GraphDelta.from_specs(specs, edges, root_id=root_id),
        task_id=(
            str(tree_metadata["task_id"])
            if tree_metadata["task_id"] is not None
            else None
        ),
    )


def translate_harness_subtree(
    parent: TaskNodeSpec,
    expansion: Any,
) -> GraphDelta:
    """Translate one deterministic Harness expansion below ``parent``."""

    direct_children = list(_field(expansion, "children", ()) or ())
    alternatives = list(_field(expansion, "alternatives", ()) or ())
    if alternatives:
        direct_children.extend(
            child
            for branch in alternatives
            for child in (branch or ())
        )
    if not direct_children:
        return GraphDelta()

    indexed: dict[str, Any] = {}
    for child in direct_children:
        spec = _node_spec(child)
        node_id = _required_text(
            _field(spec, "node_id"), "expansion.child.node_id"
        )
        if node_id in indexed:
            raise HarnessTreeTranslationError(
                f"Harness expansion contains duplicate node: {node_id}"
            )
        indexed[node_id] = child

    parent_source = _plain_mapping(
        parent.parameters.get(GEMINI_ER2_METADATA_KEY, {}),
        f"{parent.node_id}.parameters.{GEMINI_ER2_METADATA_KEY}",
    )
    root_id = str(parent_source.get("root_id") or parent.node_id)
    specs = tuple(
        _translate_node(
            child,
            root_id=root_id,
            tree_metadata=None,
        )
        for child in direct_children
    )

    edges: list[TaskEdge] = []
    direct_ids = {spec.node_id for spec in specs}
    for order, child in enumerate(direct_children):
        child_spec = _node_spec(child)
        child_id = str(_field(child_spec, "node_id"))
        declared_parent = _field(child_spec, "parent_id")
        parent_id = (
            str(declared_parent)
            if declared_parent is not None
            else parent.node_id
        )
        if parent_id not in direct_ids:
            parent_id = parent.node_id
        edges.append(
            TaskEdge(
                parent_id=parent_id,
                child_id=child_id,
                kind=_edge_kind(child),
                order=(
                    order
                    if parent_id == parent.node_id
                    else sum(
                        1
                        for edge in edges
                        if edge.parent_id == parent_id
                    )
                ),
            )
        )
    return GraphDelta.from_specs(specs, tuple(edges))


def translate_harness_spec(
    spec: Any,
    *,
    root_id: str,
    node_id: str | None = None,
    parent_id: str | None = None,
) -> TaskNodeSpec:
    """Translate one detached Harness node spec for a kernel-owned mount."""

    value = copy.deepcopy(spec)
    if node_id is not None:
        _set_field(value, "node_id", str(node_id))
    _set_field(value, "root_id", str(root_id))
    _set_field(value, "parent_id", parent_id)
    return _translate_node(
        value,
        root_id=str(root_id),
        tree_metadata=None,
    )


def _translate_node(
    node: Any,
    *,
    root_id: str,
    tree_metadata: Mapping[str, Any] | None,
) -> TaskNodeSpec:
    spec = _node_spec(node)
    node_id = _required_text(_field(spec, "node_id"), "node.spec.node_id")
    task_type = _required_text(
        _field(spec, "task_type", "task"), f"{node_id}.spec.task_type"
    )
    children = tuple(str(value) for value in (_field(spec, "children", ()) or ()))
    params = _plain_mapping(
        _field(spec, "params", {}), f"{node_id}.spec.params"
    )
    if GEMINI_ER2_METADATA_KEY in params:
        raise HarnessTreeTranslationError(
            f"Harness params use reserved key {GEMINI_ER2_METADATA_KEY}: {node_id}"
        )

    retry_policy = _plain_mapping(
        _field(spec, "retry_policy", {}), f"{node_id}.spec.retry_policy"
    )
    resource_policy = _plain_mapping(
        _field(spec, "resource_policy", {}), f"{node_id}.spec.resource_policy"
    )
    source = {
        "schema": "gemini_er2_node/1.0",
        "root_id": root_id,
        "parent_id": _plain_value(_field(spec, "parent_id")),
        "node_kind": str(_field(spec, "node_kind", "interaction")),
        "task_type": task_type,
        "object_ref": _plain_value(_field(spec, "object_ref")),
        "actor_ref": _plain_value(_field(spec, "actor_ref")),
        "from_state": _plain_value(_field(spec, "from_state")),
        "to_state": _plain_value(_field(spec, "to_state")),
        "preconditions": _plain_value(_field(spec, "preconditions", {})),
        "goal": _plain_value(_field(spec, "goal", {})),
        "goal_scope": str(_field(spec, "goal_scope", "world")),
        "obligations": _plain_value(_field(spec, "obligations", ())),
        "children": list(children),
        "child_policy": str(_field(spec, "child_policy", "sequence")),
        "decomposer_ref": _plain_value(_field(spec, "decomposer_ref")),
        "tool_ref": _plain_value(_field(spec, "tool_ref")),
        "action_ref": _plain_value(_field(spec, "action_ref")),
        "retry_policy": retry_policy,
        "resource_policy": resource_policy,
        "origin": str(_field(spec, "origin", "system_decomposer")),
        "metadata": _plain_mapping(
            _field(spec, "metadata", {}), f"{node_id}.spec.metadata"
        ),
        "preexpanded": bool(children),
    }
    source["region_requirements"] = _region_requirements(
        source["preconditions"],
        source["obligations"],
    )
    if tree_metadata is not None:
        source["tree"] = _plain_value(tree_metadata)
    params[GEMINI_ER2_METADATA_KEY] = source

    return TaskNodeSpec(
        node_id=node_id,
        task_type=task_type,
        operation_kind=_operation_kind(spec, children),
        control_kind=_control_kind(spec, children),
        origin=_node_origin(_field(spec, "origin", "system_decomposer")),
        execution_policy=_execution_policy(params),
        parameters=params,
        max_attempts=max(1, int(retry_policy.get("max_attempts", 1))),
        max_repairs=max(0, int(retry_policy.get("max_repairs", 0))),
    )


def _index_nodes(raw_nodes: Any) -> dict[str, Any]:
    if isinstance(raw_nodes, Mapping):
        items = raw_nodes.items()
    elif raw_nodes is not None:
        items = ((None, node) for node in raw_nodes)
    else:
        raise HarnessTreeTranslationError("tree.nodes must be a mapping or iterable")

    indexed: dict[str, Any] = {}
    for key, node in items:
        spec = _node_spec(node)
        node_id = _required_text(_field(spec, "node_id"), "node.spec.node_id")
        if key is not None and str(key) != node_id:
            raise HarnessTreeTranslationError(
                f"Harness node key does not match spec.node_id: {key} != {node_id}"
            )
        if node_id in indexed:
            raise HarnessTreeTranslationError(
                f"Harness tree contains duplicate node: {node_id}"
            )
        indexed[node_id] = node
    return indexed


def _preorder(
    root_id: str, children_by_id: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise HarnessTreeTranslationError(
                f"Harness tree contains a cycle at: {node_id}"
            )
        if node_id in visited:
            return
        visiting.add(node_id)
        ordered.append(node_id)
        for child_id in children_by_id[node_id]:
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    visit(root_id)
    return tuple(ordered)


def _operation_kind(spec: Any, children: tuple[str, ...]) -> OperationKind:
    if children or _field(spec, "decomposer_ref"):
        return OperationKind.DECOMPOSER
    if _field(spec, "action_ref"):
        return OperationKind.PHYSICAL
    return OperationKind.SYSTEM


def _control_kind(spec: Any, children: tuple[str, ...]) -> ControlKind:
    policy = str(_field(spec, "child_policy", "")).lower()
    task_type = str(_field(spec, "task_type", "")).lower()
    if policy == "selector" or task_type == "selector":
        return ControlKind.SELECTOR
    if children:
        return ControlKind.SEQUENCE
    return ControlKind.LEAF


def _execution_policy(params: Mapping[str, Any]) -> ExecutionPolicy:
    required = params.get("require_structural_execution", False)
    if isinstance(required, str):
        required = required.strip().lower() in {"1", "true", "yes", "on"}
    return (
        ExecutionPolicy.REQUIRE_EXECUTION
        if bool(required)
        else ExecutionPolicy.SKIP_IF_GOAL_SATISFIED
    )


def _node_origin(value: Any) -> NodeOrigin:
    text = str(_enum_value(value)).lower()
    if "repair" in text or "recovery" in text:
        return NodeOrigin.REPAIR
    if "program" in text:
        return NodeOrigin.PROGRAM
    if "compiler" in text:
        return NodeOrigin.COMPILER
    return NodeOrigin.DECOMPOSER


def _edge_kind(node: Any) -> EdgeKind:
    spec = _node_spec(node)
    node_kind = str(_field(spec, "node_kind", "")).lower()
    metadata = _plain_mapping(
        _field(spec, "metadata", {}), "node.spec.metadata"
    )
    if node_kind == "repair" and bool(metadata.get("runtime_only")):
        return EdgeKind.REPAIR
    return EdgeKind.CHILD


def _node_spec(node: Any) -> Any:
    spec = _field(node, "spec")
    return node if spec is None else spec


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _set_field(value: Any, name: str, item: Any) -> None:
    if isinstance(value, dict):
        value[name] = item
        return
    setattr(value, name, item)


def _required_text(value: Any, path: str) -> str:
    if value is None or not str(value).strip():
        raise HarnessTreeTranslationError(f"{path} must not be empty")
    return str(value)


def _plain_mapping(value: Any, path: str) -> dict[str, Any]:
    plain = _plain_value(value)
    if plain is None:
        return {}
    if not isinstance(plain, dict):
        raise HarnessTreeTranslationError(f"{path} must be a mapping")
    return plain


def _plain_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _plain_value(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in value.items()
        }
    if is_dataclass(value):
        return {
            item.name: _plain_value(getattr(value, item.name))
            for item in fields(value)
        }
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain_value(to_dict())
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain_value(item) for item in value), key=repr)
    return copy.deepcopy(value)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _region_requirements(
    preconditions: Any,
    obligations: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = (
        ("preconditions", preconditions),
        ("obligations", obligations),
    )
    for formula_kind, value in sources:
        formulae = (
            value
            if isinstance(value, (list, tuple))
            else (value,)
        )
        for formula in formulae:
            for leaf in _formula_leaves(formula):
                if str(leaf.get("predicate") or "") != (
                    "support_region_empty"
                ):
                    continue
                desired = str(
                    leaf.get("desired_value", "true")
                ).casefold()
                if desired not in {"true", "1", "confirmed"}:
                    continue
                participants = leaf.get("participants")
                owners = (
                    participants.get("region_owner", ())
                    if isinstance(participants, Mapping)
                    else ()
                )
                if isinstance(owners, str):
                    owners = (owners,)
                if not isinstance(owners, (list, tuple)):
                    owners = ()
                parameters = (
                    dict(leaf.get("parameters") or {})
                    if isinstance(leaf.get("parameters"), Mapping)
                    else {}
                )
                semantic_role = str(
                    parameters.get("semantic_role") or ""
                )
                phase = (
                    RequirementPhase.AT_FINAL
                    if formula_kind == "obligations"
                    else RequirementPhase.BEFORE_PICK
                    if semantic_role == "source_state"
                    else RequirementPhase.BEFORE_BATCH
                )
                for owner in owners:
                    owner_id = str(owner)
                    requirement = RegionRequirement(
                        kind=RegionRequirementKind.EMPTY,
                        phase=phase,
                        owner_id=owner_id,
                        region_ref=f"{owner_id}/support",
                        repair_policy=str(
                            parameters.get("repair_policy")
                            or "recursive_repair"
                        ),
                    ).to_dict()
                    key = repr(sorted(requirement.items()))
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append(requirement)
    return result


def _formula_leaves(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Mapping):
        return ()
    args = value.get("args")
    if isinstance(args, (list, tuple)):
        return tuple(
            leaf
            for child in args
            for leaf in _formula_leaves(child)
        )
    return (value,)
