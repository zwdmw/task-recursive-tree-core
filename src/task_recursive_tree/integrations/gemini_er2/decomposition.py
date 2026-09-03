from __future__ import annotations

import copy
import hashlib
import importlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from task_recursive_tree.task.model import GraphDelta, TaskNodeSpec

from .paths import import_harness_module
from .relocatability import (
    relocatable_by_grasp,
    relocatable_entity_ids,
)
from .region_space import (
    PROTECTED_RELOCATION_ENTITY_IDS_KEY,
    RELOCATION_CONTINUATION_CONTEXT_KEYS,
    RELOCATION_LAYOUT_CONSTRAINT_KEYS,
    HarnessRegionSpaceAdapter,
)
from .translation import GEMINI_ER2_METADATA_KEY


SubtreeTranslator = Callable[[TaskNodeSpec, Any], GraphDelta]


@dataclass
class _HarnessSpecProxy:
    node_id: str
    parent_id: str | None
    root_id: str
    node_kind: str
    task_type: str
    params: dict[str, Any]
    preconditions: dict[str, Any] = field(default_factory=dict)
    goal: dict[str, Any] = field(default_factory=dict)
    obligations: list[dict[str, Any]] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    child_policy: str = "sequence"
    object_ref: Any = None
    actor_ref: Any = None
    from_state: Any = None
    to_state: Any = None
    goal_scope: str = "world"
    decomposer_ref: str | None = None
    tool_ref: str | None = None
    action_ref: str | None = None
    retry_policy: dict[str, Any] = field(default_factory=dict)
    resource_policy: dict[str, Any] = field(default_factory=dict)
    origin: str = "kernel_bridge"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_atomic(self) -> bool:
        return bool(self.action_ref) and not self.children


@dataclass
class _HarnessNodeProxy:
    spec: _HarnessSpecProxy
    runtime: Any = field(
        default_factory=lambda: SimpleNamespace(
            status="PENDING",
            phase="enter",
            failure=None,
            last_result=None,
        )
    )

    @property
    def node_id(self) -> str:
        return self.spec.node_id

    @property
    def status(self) -> str:
        return str(self.runtime.status)


class HarnessBuiltinTaskDecomposerAdapter:
    """Adapt Harness decomposition without admitting its executor or tree."""

    def __init__(
        self,
        *,
        runtime: Any,
        task_id: str | None = None,
        tree: Any = None,
        config: Mapping[str, Any] | None = None,
        harness_root: str | None = None,
        decomposer: Any = None,
        subtree_translator: SubtreeTranslator | None = None,
        region_space: HarnessRegionSpaceAdapter | None = None,
    ) -> None:
        self.runtime = runtime
        self.task_id = task_id
        self.tree = tree
        self.config = dict(config or {})
        self.harness_root = harness_root
        self._decomposer = decomposer
        self._subtree_translator = subtree_translator
        self.region_space = (
            region_space
            or HarnessRegionSpaceAdapter(
                runtime=runtime,
                harness_root=harness_root,
            )
        )

    def expand(self, node: TaskNodeSpec, context: Any) -> GraphDelta:
        del context
        decomposer = self._decomposer or self._load_decomposer()
        harness_node = to_harness_node(node)
        execution_context = SimpleNamespace(
            runtime=self.runtime,
            tree=self.tree,
            task_id=self._task_id(node),
            config=dict(self.config),
            variables={},
            repair_scope_id=None,
            problem_signature=None,
            semantic_state_fingerprint=None,
            repair_excluded_rule_refs=set(),
        )
        if not decomposer.supports(harness_node, execution_context):
            raise ValueError(
                f"Harness BuiltinTaskDecomposer does not support "
                f"{harness_node.spec.task_type}"
            )

        expansion = decomposer.expand(harness_node, execution_context)
        expansion = _without_non_relocatable_region_branches(
            harness_node,
            expansion,
            self.runtime,
        )
        expansion = _with_explicit_region_blockers(
            harness_node,
            expansion,
            self.runtime,
            self.harness_root,
        )
        expansion = _with_explicit_grasp_preparation(
            harness_node,
            expansion,
        )
        expansion = _with_explicit_placement_layout(
            harness_node,
            expansion,
            self.region_space,
        )
        expansion = _with_explicit_floor_placement_preparation(
            harness_node,
            expansion,
            self.runtime,
        )
        expansion = _with_batch_region_layout(
            harness_node,
            expansion,
            self.region_space,
        )
        expansion = _with_inherited_layout_context(
            harness_node,
            expansion,
        )
        expansion = _with_inherited_relocation_continuation_context(
            harness_node,
            expansion,
        )
        expansion = _with_explicit_place_hold_precondition(expansion)
        deterministic = _determinize_expansion(node, expansion)
        _declare_placement_layout_contracts(
            harness_node,
            deterministic,
        )
        _declare_batch_layout_contracts(deterministic)
        _declare_adjacent_staging_layout_contracts(deterministic)
        _declare_inherited_layout_contracts(
            harness_node,
            deterministic,
        )
        _declare_supported_decompositions(
            deterministic,
            decomposer,
            execution_context,
        )
        translator = (
            self._subtree_translator or self._load_subtree_translator()
        )
        delta = translator(node, deterministic)
        if not isinstance(delta, GraphDelta):
            raise TypeError(
                "translate_harness_subtree must return GraphDelta"
            )
        if delta.root_id is not None:
            raise ValueError(
                "translate_harness_subtree must not replace the tree root"
            )
        return delta

    def _task_id(self, node: TaskNodeSpec) -> str:
        if self.task_id:
            return self.task_id
        configured = node.parameters.get("task_id")
        if configured:
            return str(configured)
        current = getattr(self.runtime, "_current_task_id", None)
        return str(current or "kernel-task")

    def _load_decomposer(self) -> Any:
        module = import_harness_module(
            "er2sim.task_decomposer",
            harness_root=self.harness_root,
        )
        return module.BuiltinTaskDecomposer()

    @staticmethod
    def _load_subtree_translator() -> SubtreeTranslator:
        module = importlib.import_module(
            "task_recursive_tree.integrations.gemini_er2.translation"
        )
        return module.translate_harness_subtree


def to_harness_node(node: TaskNodeSpec) -> _HarnessNodeProxy:
    source = copy.deepcopy(
        dict(
            node.parameters.get(
                GEMINI_ER2_METADATA_KEY, {}
            )
        )
    )
    task_type = str(
        source.get("task_type")
        or {
            "Place": "place",
            "Pick": "grasp",
            "TransferHeld": "transport",
        }.get(node.task_type, node.task_type)
    )
    operation_kind = node.operation_kind.value
    node_kind = str(
        source.get("node_kind")
        or {
            "decomposer": "task",
            "system": "planning",
            "physical": "physical",
        }.get(operation_kind, operation_kind)
    )
    params = {
        key: copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    spec = _HarnessSpecProxy(
        node_id=node.node_id,
        parent_id=source.get("parent_id"),
        root_id=str(source.get("root_id") or node.node_id),
        node_kind=node_kind,
        task_type=task_type,
        params=params,
        preconditions=copy.deepcopy(source.get("preconditions") or {}),
        goal=copy.deepcopy(source.get("goal") or {}),
        obligations=copy.deepcopy(source.get("obligations") or []),
        children=list(source.get("children") or []),
        child_policy=str(source.get("child_policy") or "sequence"),
        object_ref=copy.deepcopy(source.get("object_ref")),
        actor_ref=copy.deepcopy(source.get("actor_ref")),
        from_state=copy.deepcopy(source.get("from_state")),
        to_state=copy.deepcopy(source.get("to_state")),
        goal_scope=str(source.get("goal_scope") or "world"),
        decomposer_ref=source.get("decomposer_ref"),
        tool_ref=source.get("tool_ref"),
        action_ref=source.get("action_ref"),
        retry_policy=copy.deepcopy(
            source.get("retry_policy")
            or {
                "max_attempts": node.max_attempts,
                "max_repairs": node.max_repairs,
            }
        ),
        resource_policy=copy.deepcopy(
            source.get("resource_policy") or {}
        ),
        origin=str(source.get("origin") or node.origin.value),
        metadata={
            **copy.deepcopy(source.get("metadata") or {}),
            "source_operation_kind": operation_kind,
        },
    )
    return _HarnessNodeProxy(spec)


def _with_explicit_grasp_preparation(
    parent: _HarnessNodeProxy,
    expansion: Any,
) -> Any:
    """Make mobile-manipulator grasp preparation a normal task subtree."""

    if str(parent.spec.task_type).casefold() != "grasp":
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    if any(
        _spec_task_type(child) == "reposition_for_interaction"
        for child in children
    ):
        return result

    object_id = _first_entity_id(parent.spec.params)
    if object_id is None:
        for child in children:
            object_id = _first_entity_id(
                getattr(_child_spec(child), "params", {}) or {}
            )
            if object_id is not None:
                break
    if object_id is None:
        return result

    grasp_context: dict[str, Any] = {}
    parameter_sources = [
        parent.spec.params,
        *[
            getattr(_child_spec(child), "params", {}) or {}
            for child in children
        ],
    ]
    for key in (
        "source_region_id",
        "clear_region_owner_id",
        "grasp_policy",
    ):
        for parameters in parameter_sources:
            if parameters.get(key) is not None:
                grasp_context[key] = copy.deepcopy(parameters[key])
                break

    prepare = _HarnessSpecProxy(
        node_id=f"{parent.node_id}:prepare-grasp-interaction",
        parent_id=parent.node_id,
        root_id=parent.spec.root_id,
        node_kind="interaction",
        task_type="reposition_for_interaction",
        object_ref={"entity_id": object_id},
        params={
            "object_ids": [object_id],
            "reference_ids": [object_id],
            "participants": {"reference": [object_id]},
            "purpose": "prepare_grasp_execution",
            "require_structural_execution": True,
            **grasp_context,
        },
        goal={
            "predicate": "interaction_pose_achieved",
            "participants": {
                "object": [object_id],
                "robot": ["robot_1"],
            },
            "desired_value": "true",
        },
        decomposer_ref="builtin:reposition_for_interaction",
        retry_policy={"max_attempts": 1, "max_repairs": 3},
        origin="kernel_adapter",
        metadata={
            "adapter_injected": True,
            "role": "grasp_interaction_preparation",
        },
    )
    insertion_index = next(
        (
            index
            for index, child in enumerate(children)
            if _spec_task_type(child) in {
                "assess_interaction",
                "pick_object",
            }
        ),
        len(children),
    )
    children.insert(insertion_index, prepare)
    result.children = children
    rationale = str(getattr(result, "rationale", "") or "").strip()
    suffix = (
        "grasp preparation explicitly selects and executes a validated "
        "interaction stance before assessment and contact"
    )
    result.rationale = f"{rationale}; {suffix}" if rationale else suffix
    return result


def _with_explicit_placement_layout(
    parent: _HarnessNodeProxy,
    expansion: Any,
    region_space: HarnessRegionSpaceAdapter,
) -> Any:
    """Select and reserve an exact placement point before assessment."""

    if str(parent.spec.task_type).casefold() != "place":
        return expansion
    parent_params = parent.spec.params
    if (
        isinstance(parent_params.get("target_ref"), str)
        and parent_params.get("layout_producer_node_id")
    ):
        return expansion

    relation = str(
        parent_params.get("relation") or "inside_support_region"
    ).casefold()
    selector_by_relation = {
        "inside_support_region": "interior",
        "on_support": "support",
    }
    selector = (
        _first_entity_value(parent_params.get("target_selector"))
        or selector_by_relation.get(relation)
    )
    if selector is None:
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    assess_index = next(
        (
            index
            for index, child in enumerate(children)
            if _spec_task_type(child) == "assess_placeability"
        ),
        None,
    )
    has_place_action = any(
        _spec_task_type(child) == "place_object" for child in children
    )
    if assess_index is None or not has_place_action:
        return result
    if any(
        str(
            (getattr(_child_spec(child), "params", {}) or {}).get(
                "system_check",
                "",
            )
        ).casefold()
        == "plan_region_layout"
        for child in children
    ):
        return result

    object_id = _first_entity_id(parent_params)
    destination_id = _destination_entity_id(parent_params)
    if object_id is None or destination_id is None:
        return result

    requested_region_ref = _first_entity_value(
        parent_params.get("region_ref")
    )
    try:
        region_ref = region_space.placement_region_ref(
            destination_id,
            relation,
            requested_region_ref=requested_region_ref,
            selector=selector,
        )
    except Exception:
        region_ref = None
    if region_ref is None:
        region_ref = requested_region_ref or f"{destination_id}/{selector}"

    reservation_group = _placement_reservation_group(
        parent.node_id,
        destination_id,
        object_id,
    )
    planner = _HarnessSpecProxy(
        node_id=f"{parent.node_id}:select-placement-space",
        parent_id=parent.node_id,
        root_id=parent.spec.root_id,
        node_kind="planning",
        task_type="select_placement_space",
        params={
            "system_check": "plan_region_layout",
            "object_ids": [object_id],
            "batch_object_ids": [object_id],
            "destination_ids": [destination_id],
            "participants": {
                "object": [object_id],
                "destination": [destination_id],
            },
            "relation": relation,
            "region_ref": region_ref,
            "target_selector": selector,
            "reservation_group": reservation_group,
            "lock_region_selection": True,
            **{
                key: copy.deepcopy(parent_params[key])
                for key in RELOCATION_LAYOUT_CONSTRAINT_KEYS
                if parent_params.get(key) is not None
            },
        },
        goal={},
        origin="kernel_adapter",
        metadata={
            "adapter_injected": True,
            "role": "explicit_placement_space_selection",
        },
    )
    children.insert(assess_index, planner)
    result.children = children
    rationale = str(getattr(result, "rationale", "") or "").strip()
    suffix = (
        "placement space is explicitly selected and reserved before "
        "placeability assessment"
    )
    result.rationale = f"{rationale}; {suffix}" if rationale else suffix
    return result


def _with_explicit_floor_placement_preparation(
    parent: _HarnessNodeProxy,
    expansion: Any,
    runtime: Any,
) -> Any:
    """Prepare a fixed compiled floor target before the atomic release."""

    if str(parent.spec.task_type).casefold() != "place":
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    place_index = next(
        (
            index
            for index, child in enumerate(children)
            if _spec_task_type(child) == "place_object"
        ),
        None,
    )
    if place_index is None:
        return result

    place = _child_spec(children[place_index])
    place_params = getattr(place, "params", {}) or {}
    destination_id = _destination_entity_id(place_params)
    if (
        destination_id is None
        or _runtime_entity_category(runtime, destination_id) != "floor"
    ):
        return result

    object_id = _first_entity_id(place_params)
    if object_id is None:
        return result

    prepare = next(
        (
            _child_spec(child)
            for child in children
            if _spec_task_type(child) == "prepare_placement"
        ),
        None,
    )
    if prepare is None:
        relation = str(
            place_params.get("relation")
            or parent.spec.params.get("relation")
            or "inside_support_region"
        )
        prepare_params = {
            "object_ids": [object_id],
            "reference_ids": [destination_id],
            "placement_object_ids": [object_id],
            "destination_ids": [destination_id],
            "participants": {
                "manipuland": [object_id],
                "reference": [destination_id],
                "placement_object": [object_id],
                "destination": [destination_id],
            },
            "interaction_target_kind": "placement_pose",
            "purpose": "placement_execution_preparation",
            "require_structural_execution": True,
            "relation": relation,
        }
        for key in (
            "corner",
            "side",
            "target_ref",
            "layout_producer_node_id",
            "layout_continuation_scope_id",
            "reservation_group",
            "reservation_index",
        ):
            value = place_params.get(key)
            if value is None:
                value = parent.spec.params.get(key)
            if value is not None:
                prepare_params[key] = copy.deepcopy(value)

        placement_goal = {
            "predicate": "placement_execution_ready",
            "participants": {
                "object": [object_id],
                "destination": [destination_id],
                "robot": ["robot_1"],
            },
            "parameters": {
                "relation": relation,
            },
            "desired_value": "true",
        }
        for key in ("corner", "side", "target_ref"):
            if prepare_params.get(key) is not None:
                placement_goal["parameters"][key] = copy.deepcopy(
                    prepare_params[key]
                )
        prepare = _HarnessSpecProxy(
            node_id=f"{parent.node_id}:prepare-floor-placement",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="interaction",
            task_type="prepare_placement",
            object_ref={"entity_id": destination_id},
            params=prepare_params,
            goal={
                "op": "and",
                "args": [
                    placement_goal,
                    {
                        "predicate": "attached_to_any_end_effector",
                        "participants": {"object": [object_id]},
                        "desired_value": "true",
                    },
                ],
            },
            decomposer_ref="builtin:prepare_placement",
            origin="kernel_adapter",
            metadata={
                "adapter_injected": True,
                "role": "floor_placement_execution_preparation",
            },
        )
        children.insert(place_index, prepare)

    place_params["placement_prepared"] = True
    place_params["base_prepositioned"] = True
    place.params = place_params
    result.children = children
    rationale = str(getattr(result, "rationale", "") or "").strip()
    suffix = (
        "fixed floor layout is prepared by an explicit held-load stance "
        "and A* route before atomic placement"
    )
    result.rationale = f"{rationale}; {suffix}" if rationale else suffix
    return result


def _with_explicit_region_blockers(
    parent: _HarnessNodeProxy,
    expansion: Any,
    runtime: Any,
    harness_root: str | None,
) -> Any:
    """Ensure every diagnosed region blocker gets an executable move branch."""

    if str(parent.spec.task_type).casefold() not in {
        "clear_support_region",
        "clear_placement_region",
    }:
        return expansion
    include_ids = relocatable_entity_ids(
        runtime,
        parent.spec.params.get("include_entity_ids") or (),
    )
    if not include_ids:
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    existing = {
        entity_id
        for child in children
        if _spec_task_type(child) == "transport"
        for entity_id in (
            _first_entity_id(
                getattr(_child_spec(child), "params", {}) or {}
            ),
        )
        if entity_id is not None
    }
    missing = tuple(
        entity_id for entity_id in include_ids if entity_id not in existing
    )
    if not missing:
        return result

    owner_id = (
        _first_entity_value(parent.spec.params.get("region_owner_id"))
        or _first_entity_value(parent.spec.params.get("destination_ids"))
        or _first_entity_value(
            (
                parent.spec.params.get("participants") or {}
            ).get("destination")
            if isinstance(
                parent.spec.params.get("participants"),
                Mapping,
            )
            else None
        )
    )
    if owner_id is None:
        return result

    ensure_entity = getattr(
        import_harness_module(
            "er2sim.task_decomposer",
            harness_root=harness_root,
        ),
        "_ensure_world_entity",
        None,
    )
    insert_at = next(
        (
            index
            for index, child in enumerate(children)
            if _spec_task_type(child) == "verify"
        ),
        len(children),
    )
    injected: list[_HarnessSpecProxy] = []
    for index, entity_id in enumerate(missing):
        if callable(ensure_entity):
            ensure_entity(runtime, entity_id)
        support_id = None
        support_of = getattr(runtime.perception, "support_of", None)
        if callable(support_of):
            try:
                support_id = support_of(entity_id)
            except Exception:
                support_id = None
        grasp_policy = (
            "confined_source_region"
            if str(support_id or "") == owner_id
            else "auto"
        )
        shared = {
            "object_ids": [entity_id],
            "source_region_id": owner_id,
            "clear_region_owner_id": owner_id,
            "grasp_policy": grasp_policy,
            "required_policy": "clear_support_region",
            "placement_policy": "clear_of_workspace",
            "adapter_injected_blocker": True,
        }
        select = _HarnessSpecProxy(
            node_id=f"{parent.node_id}:select-explicit-blocker-{index}",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="planning",
            task_type="select_staging_region",
            params={
                **copy.deepcopy(shared),
                "system_check": "select_staging",
                "participants": {"object": [entity_id]},
            },
            goal={},
            origin="kernel_adapter",
            metadata={
                "adapter_injected": True,
                "role": "diagnosed_region_blocker_selection",
            },
        )
        transport = _HarnessSpecProxy(
            node_id=f"{parent.node_id}:transport-explicit-blocker-{index}",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="repair",
            task_type="transport",
            object_ref={"entity_id": entity_id},
            params={
                **copy.deepcopy(shared),
                "participants": {"manipuland": [entity_id]},
                "staging_source_id": owner_id,
            },
            goal={
                "predicate": "occupies_support_region",
                "participants": {
                    "subject": [entity_id],
                    "region_owner": [owner_id],
                },
                "desired_value": "false",
            },
            decomposer_ref="builtin:transport",
            origin="kernel_adapter",
            metadata={
                "adapter_injected": True,
                "role": "diagnosed_region_blocker_transport",
            },
        )
        injected.extend((select, transport))

    children[insert_at:insert_at] = injected
    result.children = children
    rationale = str(getattr(result, "rationale", "") or "").strip()
    suffix = f"added executable branches for diagnosed blockers {list(missing)}"
    result.rationale = f"{rationale}; {suffix}" if rationale else suffix
    return result


def _without_non_relocatable_region_branches(
    parent: _HarnessNodeProxy,
    expansion: Any,
    runtime: Any,
) -> Any:
    """Drop external clear-region branches that try to manipulate anchors."""

    if str(parent.spec.task_type).casefold() not in {
        "clear_support_region",
        "clear_placement_region",
    }:
        return expansion

    result = copy.deepcopy(expansion)
    rejected: list[str] = []

    def filter_group(group: Any) -> list[Any]:
        filtered: list[Any] = []
        for child in list(group or ()):
            task_type = _spec_task_type(child)
            entity_id = _first_entity_id(
                getattr(_child_spec(child), "params", {}) or {}
            )
            if (
                task_type in {"select_staging_region", "transport"}
                and entity_id is not None
                and not relocatable_by_grasp(runtime, entity_id)
            ):
                rejected.append(entity_id)
                continue
            filtered.append(child)
        return filtered

    result.children = filter_group(getattr(result, "children", ()))
    alternatives = getattr(result, "alternatives", None)
    if isinstance(alternatives, (list, tuple)):
        result.alternatives = [
            filter_group(branch) for branch in alternatives
        ]
    rejected = list(dict.fromkeys(rejected))
    if rejected:
        rationale = str(
            getattr(result, "rationale", "") or ""
        ).strip()
        suffix = (
            "discarded non-relocatable clear-region branches for "
            f"{rejected}"
        )
        result.rationale = (
            f"{rationale}; {suffix}" if rationale else suffix
        )
    return result


def _with_batch_region_layout(
    parent: _HarnessNodeProxy,
    expansion: Any,
    region_space: HarnessRegionSpaceAdapter,
) -> Any:
    """Turn clear-region staging into an explicit batch allocation protocol."""

    if str(parent.spec.task_type).casefold() not in {
        "clear_support_region",
        "clear_placement_region",
    }:
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    pairs: list[tuple[Any, Any]] = []
    for index, child in enumerate(children[:-1]):
        child_spec = _child_spec(child)
        if _spec_task_type(child) != "select_staging_region":
            continue
        if str(
            (getattr(child_spec, "params", {}) or {}).get(
                "system_check",
                "",
            )
        ).casefold() != "select_staging":
            continue
        transport = children[index + 1]
        if _spec_task_type(transport) != "transport":
            continue
        pairs.append((child_spec, _child_spec(transport)))
    if not pairs:
        return result

    object_ids = tuple(
        dict.fromkeys(
            entity_id
            for _select, transport in pairs
            for entity_id in (
                _first_entity_id(
                    getattr(transport, "params", {}) or {}
                ),
            )
            if entity_id is not None
        )
    )
    if not object_ids:
        return result

    owner_id = (
        _first_entity_value(
            parent.spec.params.get("region_owner_id")
        )
        or _first_entity_value(
            parent.spec.params.get("destination_ids")
        )
        or _first_entity_value(
            (
                parent.spec.params.get("participants") or {}
            ).get("destination")
            if isinstance(
                parent.spec.params.get("participants"),
                Mapping,
            )
            else None
        )
    )
    reservation_group = _batch_reservation_group(
        parent.node_id,
        owner_id,
        object_ids,
    )
    planning_params = copy.deepcopy(
        getattr(pairs[0][0], "params", {}) or {}
    )
    candidate_refs: tuple[str, ...] = ()
    decision = None
    try:
        candidate_refs = region_space.candidate_refs(planning_params)
        planning_params["candidate_region_refs"] = list(candidate_refs)
        decision = region_space.select_layout(
            object_ids,
            planning_params,
            reservation_group=reservation_group,
        )
    except Exception as exc:
        preflight_failure = {
            "code": "PERCEPTION_INSUFFICIENT",
            "message": str(exc),
            "exception_type": type(exc).__name__,
        }
    else:
        preflight_failure = (
            decision.failure.to_dict()
            if decision is not None and decision.failure is not None
            else None
        )

    selected_owner = None
    selected_region_ref = None
    selected_relation = None
    if (
        decision is not None
        and decision.feasible
        and decision.snapshot is not None
    ):
        definition = decision.snapshot.definition
        selected_owner = definition.owner_id
        selected_region_ref = definition.region_ref
        selected_relation = (
            definition.allowed_relations[0]
            if definition.allowed_relations
            else "on_support"
        )
        candidate_refs = tuple(
            dict.fromkeys((selected_region_ref, *candidate_refs))
        )

    for index, (select, transport) in enumerate(pairs):
        remaining = list(object_ids[index:])
        shared = {
            "batch_object_ids": remaining,
            "reservation_group": reservation_group,
            "reservation_index": index,
            "candidate_region_refs": list(candidate_refs),
            "layout_preflight_failure": copy.deepcopy(
                preflight_failure
            ),
        }
        select.params.update(copy.deepcopy(shared))
        transport.params.update(copy.deepcopy(shared))
        if selected_owner is None or selected_region_ref is None:
            continue
        old_destination = _first_entity_value(
            transport.params.get("destination_ids")
        )
        for params in (select.params, transport.params):
            _set_destination(params, selected_owner)
            params["staging_id"] = selected_owner
            params["staging_region_ref"] = selected_region_ref
            params["region_ref"] = selected_region_ref
            params["relation"] = selected_relation
            params["lock_region_selection"] = True
        if old_destination and old_destination != selected_owner:
            transport.goal = _rewrite_references(
                transport.goal,
                {old_destination: selected_owner},
            )
    result.children = children
    return result


def _declare_placement_layout_contracts(
    parent: _HarnessNodeProxy,
    expansion: Any,
) -> None:
    children = list(getattr(expansion, "children", ()) or ())
    producer = next(
        (
            _child_spec(child)
            for child in children
            if str(
                (
                    getattr(_child_spec(child), "metadata", {}) or {}
                ).get("role")
                or ""
            )
            == "explicit_placement_space_selection"
        ),
        None,
    )
    if producer is None:
        return

    consumers = [
        _child_spec(child)
        for child in children
        if _spec_task_type(child)
        in {
            "assess_placeability",
            "place_object",
            "prepare_placement",
        }
    ]
    if not consumers:
        return

    group = str(
        producer.params.get("reservation_group") or "placement-layout"
    )
    target_ref = _layout_target_ref(producer.node_id, group)
    scope_id = parent.node_id
    producer.params["target_ref"] = target_ref
    producer.params["layout_producer_node_id"] = producer.node_id
    producer.params["layout_continuation_scope_id"] = scope_id
    producer_contract = _artifact_contract(
        "layout_targets",
        producer.node_id,
        scope_id,
        ref_key="target_ref",
    )
    producer.metadata = _append_artifact_contract(
        getattr(producer, "metadata", {}) or {},
        producer_contract,
        direction="produces",
    )

    for consumer in consumers:
        consumer.params["target_ref"] = target_ref
        consumer.params["layout_producer_node_id"] = producer.node_id
        consumer.params["layout_continuation_scope_id"] = scope_id
        contract = _artifact_contract(
            "layout_targets",
            producer.node_id,
            consumer.node_id,
            ref_key="target_ref",
        )
        consumer.metadata = _append_artifact_contract(
            getattr(consumer, "metadata", {}) or {},
            contract,
            direction="consumes",
        )


def _with_inherited_layout_context(
    parent: _HarnessNodeProxy,
    expansion: Any,
) -> Any:
    """Propagate a layout target only down the placement branch."""

    parent_params = parent.spec.params
    target_ref = parent_params.get("target_ref")
    producer_id = parent_params.get("layout_producer_node_id")
    if not isinstance(target_ref, str) or not target_ref or not producer_id:
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    parent_type = str(parent.spec.task_type).casefold()
    context_keys = (
        "target_ref",
        "layout_producer_node_id",
        "layout_continuation_scope_id",
        "reservation_group",
        "reservation_index",
        "batch_object_ids",
    )
    for child in children:
        spec = _child_spec(child)
        child_type = _spec_task_type(child)
        params = getattr(spec, "params", {}) or {}
        should_receive = (
            parent_type == "transport" and child_type == "place"
        ) or (
            parent_type == "place"
            and child_type
            in {
                "assess_placeability",
                "prepare_placement",
                "place_object",
            }
        ) or "target_ref" in params
        if not should_receive:
            continue
        for key in context_keys:
            if parent_params.get(key) is not None:
                params[key] = copy.deepcopy(parent_params[key])
        spec.params = params
    result.children = children
    return result


def _with_inherited_relocation_continuation_context(
    parent: _HarnessNodeProxy,
    expansion: Any,
) -> Any:
    parent_params = parent.spec.params
    inherited_keys: list[str] = []
    if all(
        parent_params.get(key) is not None
        for key in RELOCATION_CONTINUATION_CONTEXT_KEYS
    ):
        inherited_keys.extend(RELOCATION_CONTINUATION_CONTEXT_KEYS)
    if parent_params.get(PROTECTED_RELOCATION_ENTITY_IDS_KEY):
        inherited_keys.append(PROTECTED_RELOCATION_ENTITY_IDS_KEY)
    if not inherited_keys:
        return expansion

    allowed_children = {
        "relocate_blocker": {"transport"},
        "transport": {"place"},
        "place": {
            "select_placement_space",
            "assess_placeability",
            "prepare_placement",
        },
        "prepare_placement": {"reposition_for_interaction"},
        "vacate_placement_region": {
            "plan_detour",
            "reposition_for_interaction",
        },
        "reposition_for_interaction": {"plan_detour"},
        "plan_detour": {"build_obstacle_map"},
    }
    child_types = allowed_children.get(
        str(parent.spec.task_type).casefold()
    )
    if not child_types:
        return expansion

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    for child in children:
        spec = _child_spec(child)
        if _spec_task_type(spec) not in child_types:
            continue
        params = getattr(spec, "params", {}) or {}
        for key in inherited_keys:
            params[key] = copy.deepcopy(parent_params[key])
        spec.params = params
    result.children = children
    return result


def _with_explicit_place_hold_precondition(expansion: Any) -> Any:
    """Require a live grasp before a physical placement can dispatch."""

    result = copy.deepcopy(expansion)
    children = list(getattr(result, "children", ()) or ())
    for child in children:
        spec = _child_spec(child)
        if _spec_task_type(spec) != "place_object":
            continue
        object_id = _first_entity_id(
            getattr(spec, "params", {}) or {}
        )
        if object_id is None:
            continue
        existing = copy.deepcopy(
            getattr(spec, "preconditions", {}) or {}
        )
        if _formula_contains_predicate(
            existing,
            "attached_to_any_end_effector",
        ):
            continue
        hold = {
            "predicate": "attached_to_any_end_effector",
            "participants": {"object": [object_id]},
            "parameters": {
                "semantic_role": "execution_resource",
                "repair_policy": "recursive_repair",
            },
            "desired_value": "true",
        }
        if not existing:
            spec.preconditions = hold
        elif (
            isinstance(existing, Mapping)
            and str(existing.get("op") or "").casefold() == "and"
            and isinstance(existing.get("args"), list)
        ):
            existing["args"].insert(0, hold)
            spec.preconditions = existing
        else:
            spec.preconditions = {
                "op": "and",
                "args": [hold, existing],
            }
    result.children = children
    return result


def _formula_contains_predicate(
    formula: Any,
    predicate: str,
) -> bool:
    if not isinstance(formula, Mapping):
        return False
    if str(formula.get("predicate") or "") == str(predicate):
        return True
    return any(
        _formula_contains_predicate(argument, predicate)
        for argument in (formula.get("args") or ())
    )


def _declare_batch_layout_contracts(expansion: Any) -> None:
    children = list(getattr(expansion, "children", ()) or ())
    by_reservation_index: dict[int, dict[str, Any]] = {}
    for child in children:
        spec = _child_spec(child)
        params = getattr(spec, "params", {}) or {}
        raw_index = params.get("reservation_index")
        if raw_index is None:
            continue
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        item = by_reservation_index.setdefault(index, {})
        if (
            _spec_task_type(child) == "select_staging_region"
            and str(params.get("system_check") or "").casefold()
            == "select_staging"
        ):
            item["producer"] = spec
        elif _spec_task_type(child) == "transport":
            item["continuation"] = spec

    for item in by_reservation_index.values():
        producer = item.get("producer")
        continuation = item.get("continuation")
        if producer is None or continuation is None:
            continue
        group = str(
            producer.params.get("reservation_group") or "region-layout"
        )
        target_ref = _layout_target_ref(producer.node_id, group)
        for spec in (producer, continuation):
            spec.params["target_ref"] = target_ref
            spec.params["layout_producer_node_id"] = producer.node_id
            spec.params[
                "layout_continuation_scope_id"
            ] = continuation.node_id
        contract = _artifact_contract(
            "layout_targets",
            producer.node_id,
            continuation.node_id,
            ref_key="target_ref",
        )
        producer.metadata = _append_artifact_contract(
            producer.metadata,
            contract,
            direction="produces",
        )
        continuation.metadata = _append_artifact_contract(
            continuation.metadata,
            contract,
            direction="consumes",
        )


def _declare_adjacent_staging_layout_contracts(expansion: Any) -> None:
    """Bind unindexed staging selection to its immediate continuation."""

    children = list(getattr(expansion, "children", ()) or ())
    for index, child in enumerate(children[:-1]):
        producer = _child_spec(child)
        producer_params = getattr(producer, "params", {}) or {}
        if (
            _spec_task_type(producer) != "select_staging_region"
            or str(
                producer_params.get("system_check") or ""
            ).casefold()
            != "select_staging"
            or producer_params.get("reservation_index") is not None
        ):
            continue

        continuation = _child_spec(children[index + 1])
        if _spec_task_type(continuation) not in {"place", "transport"}:
            continue
        continuation_params = getattr(continuation, "params", {}) or {}
        group = str(
            producer_params.get("reservation_group")
            or continuation_params.get("reservation_group")
            or f"staging-layout:{producer.node_id}"
        )
        target_ref = str(
            producer_params.get("target_ref")
            or _layout_target_ref(producer.node_id, group)
        )
        scope_id = continuation.node_id
        for spec, params in (
            (producer, producer_params),
            (continuation, continuation_params),
        ):
            params["target_ref"] = target_ref
            params["layout_producer_node_id"] = producer.node_id
            params["layout_continuation_scope_id"] = scope_id
            params["reservation_group"] = group
            spec.params = params

        contract = _artifact_contract(
            "layout_targets",
            producer.node_id,
            scope_id,
            ref_key="target_ref",
        )
        producer.metadata = _append_artifact_contract(
            getattr(producer, "metadata", {}) or {},
            contract,
            direction="produces",
        )
        continuation.metadata = _append_artifact_contract(
            getattr(continuation, "metadata", {}) or {},
            contract,
            direction="consumes",
        )


def _declare_inherited_layout_contracts(
    parent: _HarnessNodeProxy,
    expansion: Any,
) -> None:
    producer_id = parent.spec.params.get("layout_producer_node_id")
    if not producer_id:
        return
    for child in list(getattr(expansion, "children", ()) or ()):
        spec = _child_spec(child)
        params = getattr(spec, "params", {}) or {}
        if not isinstance(params.get("target_ref"), str):
            continue
        contract = _artifact_contract(
            "layout_targets",
            str(producer_id),
            spec.node_id,
            ref_key="target_ref",
        )
        spec.metadata = _append_artifact_contract(
            getattr(spec, "metadata", {}) or {},
            contract,
            direction="consumes",
        )


def _child_spec(child: Any) -> Any:
    return getattr(child, "spec", child)


def _spec_task_type(child: Any) -> str:
    return str(
        getattr(_child_spec(child), "task_type", "") or ""
    ).casefold()


def _first_entity_id(params: Mapping[str, Any]) -> str | None:
    for key in ("object_id", "object_ids", "reference_id", "reference_ids"):
        entity_id = _first_entity_value(params.get(key))
        if entity_id is not None:
            return entity_id

    participants = params.get("participants")
    if not isinstance(participants, Mapping):
        return None
    for role in ("manipuland", "object", "reference"):
        entity_id = _first_entity_value(participants.get(role))
        if entity_id is not None:
            return entity_id
    return None


def _destination_entity_id(params: Mapping[str, Any]) -> str | None:
    for key in ("destination_id", "destination_ids", "region_owner_id"):
        entity_id = _first_entity_value(params.get(key))
        if entity_id is not None:
            return entity_id
    participants = params.get("participants")
    if not isinstance(participants, Mapping):
        return None
    for role in ("destination", "region_owner", "reference"):
        entity_id = _first_entity_value(participants.get(role))
        if entity_id is not None:
            return entity_id
    return None


def _runtime_entity_category(runtime: Any, entity_id: str) -> str:
    perception = getattr(runtime, "perception", None)
    catalog = getattr(perception, "catalog", None)
    entry = catalog.get(str(entity_id)) if isinstance(catalog, Mapping) else None
    if not isinstance(entry, Mapping):
        return ""
    return str(entry.get("category") or "").casefold()


def _first_entity_value(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = (
            value.get("entity_ids")
            or value.get("entity_id")
            or value.get("ids")
        )
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            text = str(item)
            if text.strip():
                return text
    return None


def _determinize_expansion(parent: TaskNodeSpec, expansion: Any) -> Any:
    result = copy.deepcopy(expansion)
    groups: list[tuple[str, list[Any]]] = [
        ("child", list(getattr(result, "children", ()) or ()))
    ]
    for branch_index, branch in enumerate(
        getattr(result, "alternatives", ()) or ()
    ):
        groups.append((f"alternative-{branch_index}", list(branch or ())))

    old_to_new: dict[str, str] = {}
    located: list[tuple[str, int, Any]] = []
    for group_name, specs in groups:
        for index, spec in enumerate(specs):
            old_id = str(spec.node_id)
            new_id = _deterministic_node_id(
                parent.node_id,
                group_name,
                index,
                str(spec.task_type),
            )
            old_to_new[old_id] = new_id
            located.append((group_name, index, spec))

    parent_source = parent.parameters.get(
        GEMINI_ER2_METADATA_KEY, {}
    )
    root_id = (
        str(parent_source.get("root_id"))
        if isinstance(parent_source, Mapping)
        and parent_source.get("root_id") is not None
        else parent.node_id
    )
    for _group_name, _index, spec in located:
        old_parent = getattr(spec, "parent_id", None)
        spec.node_id = old_to_new[str(spec.node_id)]
        spec.parent_id = old_to_new.get(
            str(old_parent),
            parent.node_id,
        )
        if hasattr(spec, "root_id"):
            spec.root_id = root_id
        if hasattr(spec, "children"):
            spec.children = [
                old_to_new.get(str(child_id), str(child_id))
                for child_id in list(spec.children or ())
            ]
        for attribute in (
            "params",
            "preconditions",
            "goal",
            "obligations",
            "metadata",
            "object_ref",
            "actor_ref",
            "from_state",
            "to_state",
        ):
            if hasattr(spec, attribute):
                setattr(
                    spec,
                    attribute,
                    _rewrite_references(
                        getattr(spec, attribute),
                        old_to_new,
                    ),
                )
    return result


def _declare_supported_decompositions(
    expansion: Any,
    decomposer: Any,
    context: Any,
) -> None:
    """Make Harness' dynamic ``supports`` decision explicit for the kernel."""

    specs = list(getattr(expansion, "children", ()) or ())
    specs.extend(
        spec
        for branch in (getattr(expansion, "alternatives", ()) or ())
        for spec in (branch or ())
    )
    supports = getattr(decomposer, "supports", None)
    if not callable(supports):
        return

    name = str(getattr(decomposer, "name", "harness_decomposer"))
    version = str(getattr(decomposer, "version", "") or "")
    for spec in specs:
        params = getattr(spec, "params", {}) or {}
        if (
            getattr(spec, "children", None)
            or getattr(spec, "action_ref", None)
            or getattr(spec, "tool_ref", None)
            or (
                isinstance(params, Mapping)
                and params.get("system_check")
            )
        ):
            continue
        try:
            supported = bool(
                supports(_HarnessNodeProxy(spec=spec), context)
            )
        except Exception:
            supported = False
        if not supported:
            continue
        task_type = str(getattr(spec, "task_type", "task"))
        suffix = f"@{version}" if version else ""
        spec.decomposer_ref = (
            getattr(spec, "decomposer_ref", None)
            or f"{name}:{task_type}{suffix}"
        )


def _deterministic_node_id(
    parent_id: str,
    group_name: str,
    index: int,
    task_type: str,
) -> str:
    semantic_path = (
        f"{parent_id}:{group_name}:{index}:{task_type.casefold()}"
    )
    digest = hashlib.sha256(semantic_path.encode("utf-8")).hexdigest()[:16]
    return f"{parent_id}/harness/{digest}"


def _rewrite_references(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, Mapping):
        return {
            str(key): _rewrite_references(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_references(item, replacements)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _rewrite_references(item, replacements)
            for item in value
        )
    if isinstance(value, set):
        return {
            _rewrite_references(item, replacements)
            for item in value
        }
    return copy.deepcopy(value)


def _set_destination(params: dict[str, Any], owner_id: str) -> None:
    params["destination_ids"] = [str(owner_id)]
    participants = params.get("participants")
    if not isinstance(participants, dict):
        participants = {}
        params["participants"] = participants
    participants["destination"] = [str(owner_id)]


def _batch_reservation_group(
    parent_id: str,
    owner_id: str | None,
    object_ids: tuple[str, ...],
) -> str:
    payload = ":".join(
        (str(parent_id), str(owner_id or ""), *object_ids)
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"clear-region-{digest}"


def _placement_reservation_group(
    parent_id: str,
    destination_id: str,
    object_id: str,
) -> str:
    payload = ":".join(
        (str(parent_id), str(destination_id), str(object_id))
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"place-region-{digest}"


def _layout_target_ref(producer_node_id: str, group: str) -> str:
    digest = hashlib.sha256(
        f"{producer_node_id}:{group}:layout_targets".encode("utf-8")
    ).hexdigest()[:24]
    return f"layout-targets-{digest}"


def _artifact_contract(
    artifact_kind: str,
    producer_node_id: str,
    continuation_node_id: str,
    *,
    ref_key: str,
) -> dict[str, Any]:
    return {
        "schema": "task_artifact/1.0",
        "artifact_kind": str(artifact_kind),
        "producer_node_id": str(producer_node_id),
        "continuation_node_id": str(continuation_node_id),
        "ref_key": str(ref_key),
        "required": True,
    }


def _append_artifact_contract(
    metadata: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    direction: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(metadata))
    key = f"artifact_{direction}"
    values = result.get(key)
    if isinstance(values, Mapping):
        values = [copy.deepcopy(dict(values))]
    elif isinstance(values, list):
        values = copy.deepcopy(values)
    else:
        values = []
    normalized = copy.deepcopy(dict(contract))
    identity = (
        normalized.get("artifact_kind"),
        normalized.get("producer_node_id"),
        normalized.get("continuation_node_id"),
        normalized.get("ref_key"),
    )
    values = [
        value
        for value in values
        if not (
            isinstance(value, Mapping)
            and (
                value.get("artifact_kind"),
                value.get("producer_node_id"),
                value.get("continuation_node_id"),
                value.get("ref_key"),
            )
            == identity
        )
    ]
    values.append(normalized)
    result[key] = values
    if direction == "consumes":
        result["artifact_contract"] = normalized
    return result


__all__ = [
    "HarnessBuiltinTaskDecomposerAdapter",
    "to_harness_node",
]
