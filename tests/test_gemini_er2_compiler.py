from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2 import (
    decomposition as decomposition_module,
)
from task_recursive_tree.integrations.gemini_er2.compiler import (
    KernelCompilerBridge,
)
from task_recursive_tree.integrations.gemini_er2.program_normalization import (
    ProgramNormalizationError,
    normalize_model_task_program,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)
from task_recursive_tree.integrations.gemini_er2.placement_coordination import (
    coordinate_sequence_placement_layouts,
)
from task_recursive_tree.integrations.gemini_er2.planner import (
    HarnessTaskProgramPlannerAdapter,
)
from task_recursive_tree.integrations.gemini_er2.decomposition import (
    HarnessBuiltinTaskDecomposerAdapter,
)
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.model import (
    ControlKind,
    ExecutionPolicy,
    GraphDelta,
    NodeOrigin,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)


def _kernel_spec(
    node_id: str,
    task_type: str = "place",
) -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id=node_id,
        task_type=task_type,
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        parameters={
            "object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "participants": {
                "manipuland": ["apple_1"],
                "destination": ["plate_1"],
            },
        },
    )


def test_compiler_uses_harness_grounding_then_returns_kernel_tree() -> None:
    runtime = object()
    harness_tree = SimpleNamespace(
        program_ref="program/demo",
        world_revision=17,
        metadata={"schema": "task_tree/1.0"},
    )
    harness_task = SimpleNamespace(id="task-7", artifacts={"binding": {}})
    calls: dict[str, object] = {}

    class FakeHarnessCompiler:
        def __init__(self, *, runtime):
            calls["runtime"] = runtime

        def compile(
            self,
            program,
            *,
            task_id,
            instruction,
            commit,
        ):
            calls["compile"] = {
                "program": program,
                "task_id": task_id,
                "instruction": instruction,
                "commit": commit,
            }
            return SimpleNamespace(
                program=program,
                task=harness_task,
                tree=harness_tree,
                collection_snapshots={"collection://1": {"ids": ["apple_1"]}},
                binding_artifacts={"binding://1": {"entity_id": "apple_1"}},
                warnings=["grounded"],
            )

    root = _kernel_spec("program/demo")
    definition = TaskTreeDefinition(
        root_id=root.node_id,
        delta=GraphDelta.from_specs((root,), root_id=root.node_id),
    )

    def translate_tree(tree):
        calls["translated_tree"] = tree
        return definition

    bridge = KernelCompilerBridge(
        runtime=runtime,
        compiler_factory=FakeHarnessCompiler,
        tree_translator=translate_tree,
    )
    program = SimpleNamespace(program_id="demo")
    compiled = bridge.compile(
        program,
        task_id="task-7",
        instruction="把苹果放到盘子里",
    )

    assert calls["runtime"] is runtime
    assert calls["compile"] == {
        "program": program,
        "task_id": "task-7",
        "instruction": "把苹果放到盘子里",
        "commit": True,
    }
    assert calls["translated_tree"] is harness_tree
    assert compiled.task is harness_task
    assert compiled.tree.definition is definition
    assert compiled.tree.root_id == "program/demo"
    assert list(compiled.tree.nodes) == ["program/demo"]
    assert compiled.tree.to_dict()["task_id"] == "task-7"
    assert compiled.collection_snapshots["collection://1"]["ids"] == [
        "apple_1"
    ]
    assert compiled.binding_artifacts["binding://1"]["entity_id"] == "apple_1"
    assert compiled.warnings == ("grounded",)


def _place_spec(
    node_id: str,
    object_id: str,
    *,
    destination_id: str = "plate_1",
    region_ref: str = "plate_1/interior",
) -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id=node_id,
        task_type="place",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        parameters={
            "object_ids": [object_id],
            "destination_ids": [destination_id],
            "participants": {
                "manipuland": [object_id],
                "destination": [destination_id],
            },
            "relation": "inside_support_region",
            "region_ref": region_ref,
            "target_selector": "interior",
        },
    )


def test_sequence_places_share_one_compiled_layout_producer() -> None:
    root = TaskNodeSpec(
        node_id="program/red-apples",
        task_type="sequence",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        parameters={
            "__gemini_er2__": {
                "children": ["place/apple-1", "place/apple-2"],
                "preexpanded": True,
            }
        },
    )
    first = _place_spec("place/apple-1", "apple_1")
    second = _place_spec("place/apple-2", "apple_2")
    definition = TaskTreeDefinition(
        root_id=root.node_id,
        delta=GraphDelta.from_specs(
            (root, first, second),
            (
                TaskEdge(root.node_id, first.node_id, order=0),
                TaskEdge(root.node_id, second.node_id, order=1),
            ),
            root_id=root.node_id,
        ),
    )

    coordinated = coordinate_sequence_placement_layouts(definition)
    specs = {
        command.spec.node_id: command.spec
        for command in coordinated.delta.nodes
    }
    children = [
        command.edge.child_id
        for command in coordinated.delta.edges
        if command.edge.parent_id == root.node_id
    ]

    assert len(children) == 3
    producer = specs[children[0]]
    assert producer.task_type == "select_placement_space"
    assert producer.operation_kind is OperationKind.SYSTEM
    assert producer.execution_policy is ExecutionPolicy.REQUIRE_EXECUTION
    assert producer.parameters["batch_object_ids"] == [
        "apple_1",
        "apple_2",
    ]
    assert producer.parameters["layout_continuation_scope_id"] == root.node_id
    contract = producer.parameters["__gemini_er2__"]["metadata"][
        "artifact_produces"
    ][0]
    assert contract["producer_node_id"] == producer.node_id
    assert contract["continuation_node_id"] == root.node_id

    first_params = specs[first.node_id].parameters
    second_params = specs[second.node_id].parameters
    assert first_params["target_ref"] == second_params["target_ref"]
    assert (
        first_params["layout_producer_node_id"]
        == second_params["layout_producer_node_id"]
        == producer.node_id
    )
    assert (
        first_params["reservation_group"]
        == second_params["reservation_group"]
    )
    assert first_params["reservation_index"] == 0
    assert second_params["reservation_index"] == 1
    assert specs[root.node_id].parameters["__gemini_er2__"][
        "children"
    ] == children


@pytest.mark.parametrize(
    "children",
    [
        (
            _place_spec("place/apple-1", "apple_1"),
            _place_spec(
                "place/apple-2",
                "apple_2",
                destination_id="basket_1",
                region_ref="basket_1/interior",
            ),
        ),
        (
            _place_spec("place/apple-1", "apple_1"),
            TaskNodeSpec(
                node_id="inspect",
                task_type="inspect",
                operation_kind=OperationKind.SYSTEM,
                control_kind=ControlKind.LEAF,
                origin=NodeOrigin.COMPILER,
            ),
            _place_spec("place/apple-2", "apple_2"),
        ),
    ],
)
def test_incompatible_or_noncontiguous_places_are_not_grouped(
    children,
) -> None:
    root = TaskNodeSpec(
        node_id="program/not-a-batch",
        task_type="sequence",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
    )
    definition = TaskTreeDefinition(
        root_id=root.node_id,
        delta=GraphDelta.from_specs(
            (root, *children),
            tuple(
                TaskEdge(root.node_id, child.node_id, order=index)
                for index, child in enumerate(children)
            ),
            root_id=root.node_id,
        ),
    )

    coordinated = coordinate_sequence_placement_layouts(definition)

    assert coordinated is definition


def _verified_model_program(
    *,
    quantifier: str = "all",
) -> dict[str, object]:
    return {
        "schema": "task_program/1.0",
        "program_id": "native_model_translator_regression",
        "source_instruction": "把圆柱都放在篮子里",
        "root": {
            "kind": "sequence",
            "steps": [
                {
                    "kind": "task",
                    "task_type": "place",
                    "object": {
                        "entity_ref": "can_1",
                        "selection_basis": {
                            "category": "can",
                            "attributes": {"shape": "cylinder"},
                            "scope": {"quantifier": quantifier},
                        },
                    },
                    "destination": {
                        "region_ref": "box_1/interior",
                        "selection_basis": {
                            "category": "box",
                            "attributes": {},
                        },
                    },
                    "relation": "inside_support_region",
                    "params": {},
                    "goal": {
                        "op": "and",
                        "args": [
                            {
                                "predicate": "inside_support_region",
                                "participants": {
                                    "subject": ["$object"],
                                    "region_owner": ["$destination"],
                                },
                                "desired_value": "true",
                            },
                            {
                                "predicate": (
                                    "attached_to_any_end_effector"
                                ),
                                "participants": {
                                    "object": ["$object"],
                                },
                                "desired_value": "false",
                            },
                        ],
                    },
                },
                {
                    "kind": "task",
                    "task_type": "place",
                    "object": {
                        "entity_ref": "cup_1",
                        "selection_basis": {
                            "category": "cup",
                            "attributes": {"shape": "cylinder"},
                            "scope": {
                                "quantifier": quantifier,
                                "relation": "on",
                                "object_ref": "table_1",
                            },
                        },
                    },
                    "destination": {
                        "region_ref": "box_1/interior",
                        "selection_basis": {
                            "category": "box",
                            "attributes": {},
                        },
                    },
                    "relation": "inside_support_region",
                    "params": {},
                    "goal": {
                        "op": "and",
                        "args": [
                            {
                                "predicate": "inside_support_region",
                                "participants": {
                                    "subject": ["$object"],
                                    "region_owner": ["$destination"],
                                },
                                "desired_value": "true",
                            },
                            {
                                "predicate": (
                                    "attached_to_any_end_effector"
                                ),
                                "participants": {
                                    "object": ["$object"],
                                },
                                "desired_value": "false",
                            },
                        ],
                    },
                },
            ],
        },
        "translation_metadata": {
            "source": "native_model_translator",
            "translator": "grounded_model_task_program/1.0",
            "semantic_constraints_verified": True,
        },
    }


def test_verified_model_universal_scope_is_normalized_without_reexpansion(
) -> None:
    original = _verified_model_program()

    result = normalize_model_task_program(original)

    assert result.program is not original
    root = result.program["root"]
    assert root["kind"] == "sequence"
    assert len(root["steps"]) == 2
    assert all(step["kind"] == "task" for step in root["steps"])
    first_basis = root["steps"][0]["object"]["selection_basis"]
    second_basis = root["steps"][1]["object"]["selection_basis"]
    assert "scope" not in first_basis
    assert second_basis["scope"] == {
        "relation": "on",
        "object_ref": "table_1",
    }
    assert original["root"]["steps"][0]["object"]["selection_basis"][
        "scope"
    ] == {"quantifier": "all"}
    assert result.changed_paths == (
        "root.steps[0].object.selection_basis.scope.quantifier",
        "root.steps[1].object.selection_basis.scope.quantifier",
    )
    assert result.warnings


def test_compiler_normalizes_verified_model_scope_before_harness_grounding(
) -> None:
    runtime = object()
    program = _verified_model_program()
    harness_tree = SimpleNamespace(
        program_ref="native_model_translator_regression",
        world_revision=1,
        metadata={"schema": "task_tree/1.0"},
    )
    harness_task = SimpleNamespace(id="task-quantifier")
    calls: dict[str, object] = {}

    class FakeHarnessCompiler:
        def __init__(self, *, runtime):
            calls["runtime"] = runtime

        def compile(
            self,
            value,
            *,
            task_id,
            instruction,
            commit,
        ):
            calls["program"] = value
            calls["task_id"] = task_id
            calls["instruction"] = instruction
            calls["commit"] = commit
            return SimpleNamespace(
                program=value,
                task=harness_task,
                tree=harness_tree,
                collection_snapshots={},
                binding_artifacts={},
                warnings=[],
            )

    root = _kernel_spec("native_model_translator_regression")
    definition = TaskTreeDefinition(
        root_id=root.node_id,
        delta=GraphDelta.from_specs((root,), root_id=root.node_id),
    )
    bridge = KernelCompilerBridge(
        runtime=runtime,
        compiler_factory=FakeHarnessCompiler,
        tree_translator=lambda _tree: definition,
    )

    compiled = bridge.compile(
        program,
        task_id="task-quantifier",
        instruction="把圆柱都放在篮子里",
    )

    normalized = calls["program"]
    steps = normalized["root"]["steps"]
    assert len(steps) == 2
    assert "scope" not in (
        steps[0]["object"]["selection_basis"]
    )
    assert steps[1]["object"]["selection_basis"]["scope"] == {
        "relation": "on",
        "object_ref": "table_1",
    }
    assert compiled.program is normalized
    assert compiled.tree.metadata["program_normalizations"] == [
        "root.steps[0].object.selection_basis.scope.quantifier",
        "root.steps[1].object.selection_basis.scope.quantifier",
    ]
    assert compiled.warnings[0].startswith(
        "Canonicalized task-program quantifiers"
    )


def test_normalized_quantifier_program_compiles_with_installed_harness(
    tmp_path,
) -> None:
    scene_module = import_harness_module("er2sim.scene")
    world_module = import_harness_module("er2sim.world_state")
    perception_module = import_harness_module("er2sim.perception")
    predicates_module = import_harness_module("er2sim.predicates")
    runtime_module = import_harness_module("er2sim.runtime")
    logging_module = import_harness_module("er2sim.logging_events")
    task_ir_module = import_harness_module("er2sim.task_ir")
    harness_root = Path(scene_module.__file__).resolve().parents[1]

    scene = scene_module.SimScene(headless=True)
    world = world_module.WorldStateService(now=0.0, ttl=30.0)
    perception = perception_module.OraclePerceptionService(
        scene,
        media_dir=str(tmp_path / "media"),
    )
    predicates = predicates_module.build_default_engine()
    tools = runtime_module.ToolRegistry(
        str(harness_root / "config" / "tools" / "registry.json")
    )
    logger = logging_module.EventLogger(
        str(tmp_path / "events"),
        "quantifier_regression",
    )
    runtime = runtime_module.HarnessRuntime(
        world=world,
        scene=scene,
        perception=perception,
        predicates=predicates,
        tools=tools,
        logger=logger,
        cfg={
            "brain": "rule",
            "dispatch_registry_path": str(
                tmp_path / "dispatch_registry.json"
            ),
        },
    )
    source_program = task_ir_module.TaskProgram.from_dict(
        _verified_model_program()
    )

    class StaticPlanner:
        parse_report = {}

        @staticmethod
        def parse_task_program(*_args, **_kwargs):
            return source_program

    planner = HarnessTaskProgramPlannerAdapter(StaticPlanner())
    program = planner.parse_task_program(
        "把圆柱都放在篮子里",
        perception,
    )

    try:
        compiled = KernelCompilerBridge(
            runtime=runtime,
            harness_root=str(harness_root),
        ).compile(
            program,
            task_id="task-quantifier-live",
            instruction="把圆柱都放在篮子里",
        )
    finally:
        scene.close()

    assert compiled.task.id == "task-quantifier-live"
    assert compiled.tree.metadata["program_normalizations"] == [
        "root.steps[0].object.selection_basis.scope.quantifier",
        "root.steps[1].object.selection_basis.scope.quantifier",
    ]
    normalized_steps = compiled.program.to_dict()["root"]["steps"]
    assert "scope" not in (
        normalized_steps[0]["object"]["selection_basis"]
    )
    assert normalized_steps[1]["object"]["selection_basis"]["scope"] == {
        "relation": "on",
        "object_ref": "table_1",
    }


def test_unverified_or_nonuniversal_scope_fails_closed() -> None:
    unsupported = _verified_model_program(quantifier="some")
    with pytest.raises(
        ProgramNormalizationError,
        match="unsupported quantifier",
    ):
        normalize_model_task_program(unsupported)

    unverified = _verified_model_program()
    unverified["translation_metadata"][
        "semantic_constraints_verified"
    ] = False
    with pytest.raises(
        ProgramNormalizationError,
        match="must use for_each",
    ):
        normalize_model_task_program(unverified)


@pytest.mark.parametrize(
    "quantifier",
    ["all", "ALL", " each ", "every", "all-matching"],
)
def test_universal_quantifier_aliases_share_one_canonical_form(
    quantifier,
) -> None:
    result = normalize_model_task_program(
        _verified_model_program(quantifier=quantifier)
    )

    assert result.applied is True
    assert result.changed_paths == (
        "root.steps[0].object.selection_basis.scope.quantifier",
        "root.steps[1].object.selection_basis.scope.quantifier",
    )


def test_for_each_quantifier_aliases_are_canonicalized() -> None:
    program = {
        "schema": "task_program/1.0",
        "program_id": "for-each-quantifier",
        "root": {
            "kind": "for_each",
            "item_variable": "item",
            "aggregate": " every ",
            "collection": {
                "category": "can",
                "attributes": {},
                "scope": {"quantifier": "all_matching"},
            },
            "body": {
                "kind": "task",
                "task_type": "grasp",
                "object": "$item",
                "params": {},
            },
        },
    }

    first = normalize_model_task_program(program)
    second = normalize_model_task_program(first.program)

    assert first.program["root"]["aggregate"] == "all"
    assert "scope" not in first.program["root"]["collection"]
    assert first.changed_paths == (
        "root.aggregate",
        "root.collection.scope.quantifier",
    )
    assert second.program is first.program
    assert second.changed_paths == first.changed_paths
    assert second.applied is False


def test_destination_and_misplaced_quantifiers_fail_closed() -> None:
    destination = _verified_model_program()
    destination["root"]["steps"][0]["destination"][
        "selection_basis"
    ]["scope"] = {"quantifier": "all"}
    with pytest.raises(
        ProgramNormalizationError,
        match="destination quantifiers",
    ):
        normalize_model_task_program(destination)

    misplaced = _verified_model_program()
    misplaced["root"]["steps"][0]["object"]["quantifier"] = "all"
    with pytest.raises(
        ProgramNormalizationError,
        match="must not be a query field",
    ):
        normalize_model_task_program(misplaced)


def test_collection_quantifier_conflict_fails_closed() -> None:
    program = {
        "schema": "task_program/1.0",
        "program_id": "for-each-conflict",
        "root": {
            "kind": "for_each",
            "item_variable": "item",
            "aggregate": "any",
            "collection": {
                "category": "can",
                "attributes": {},
                "scope": {"quantifier": "all"},
            },
            "body": {
                "kind": "task",
                "task_type": "grasp",
                "object": "$item",
                "params": {},
            },
        },
    }

    with pytest.raises(
        ProgramNormalizationError,
        match="conflicts with aggregate='any'",
    ):
        normalize_model_task_program(program)


@dataclass
class FakeHarnessSpec:
    node_id: str
    parent_id: str
    root_id: str
    task_type: str
    node_kind: str = "physical"
    action_ref: str | None = "pick_object"
    tool_ref: str | None = None
    decomposer_ref: str | None = None
    params: dict[str, object] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)


class RandomHarnessDecomposer:
    def __init__(self) -> None:
        self.generation = 0
        self.seen_task_types: list[str] = []

    def supports(self, node, context) -> bool:
        self.seen_task_types.append(node.spec.task_type)
        return context.runtime is not None

    def expand(self, node, context):
        self.generation += 1
        random_id = f"random-{self.generation}"
        child = FakeHarnessSpec(
            node_id=random_id,
            parent_id=node.node_id,
            root_id=node.spec.root_id,
            task_type="pick_object",
            params={"participants": node.spec.params["participants"]},
        )
        return SimpleNamespace(
            children=[child],
            child_policy="sequence",
            alternatives=[],
            required_artifacts=[],
            rationale="fake expansion",
        )


def test_clear_region_decomposition_drops_static_transport_branches() -> None:
    parent = SimpleNamespace(
        spec=SimpleNamespace(task_type="clear_support_region")
    )
    expansion = SimpleNamespace(
        children=[
            FakeHarnessSpec(
                node_id="select-wall",
                parent_id="clear-floor",
                root_id="clear-floor",
                task_type="select_staging_region",
                params={"object_ids": ["aisle_left"]},
            ),
            FakeHarnessSpec(
                node_id="transport-wall",
                parent_id="clear-floor",
                root_id="clear-floor",
                task_type="transport",
                params={"object_ids": ["aisle_left"]},
            ),
            FakeHarnessSpec(
                node_id="select-apple",
                parent_id="clear-floor",
                root_id="clear-floor",
                task_type="select_staging_region",
                params={"object_ids": ["apple_3"]},
            ),
            FakeHarnessSpec(
                node_id="transport-apple",
                parent_id="clear-floor",
                root_id="clear-floor",
                task_type="transport",
                params={"object_ids": ["apple_3"]},
            ),
            FakeHarnessSpec(
                node_id="verify",
                parent_id="clear-floor",
                root_id="clear-floor",
                task_type="verify",
                params={},
            ),
        ],
        alternatives=[],
        rationale="external clear-region expansion",
    )
    runtime = SimpleNamespace(
        world=SimpleNamespace(entities={}),
        perception=SimpleNamespace(
            catalog={
                "aisle_left": {
                    "movable": False,
                    "attributes": {"fixed": True},
                    "interaction_capabilities": {
                        "grasp": "unavailable",
                    },
                },
                "apple_3": {
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "available",
                    },
                },
            }
        ),
    )

    filtered = decomposition_module._without_non_relocatable_region_branches(
        parent,
        expansion,
        runtime,
    )

    assert [child.node_id for child in filtered.children] == [
        "select-apple",
        "transport-apple",
        "verify",
    ]
    assert "aisle_left" in filtered.rationale


def test_decomposition_determinizes_harness_nodes_before_translation() -> None:
    decomposer = RandomHarnessDecomposer()
    translated_ids: list[str] = []

    def translate_subtree(parent, expansion):
        child = expansion.children[0]
        translated_ids.append(child.node_id)
        spec = TaskNodeSpec(
            node_id=child.node_id,
            task_type=child.task_type,
            operation_kind=OperationKind.PHYSICAL,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.DECOMPOSER,
            parameters=child.params,
        )
        return GraphDelta.from_specs(
            (spec,),
            edges=(TaskEdge(parent.node_id, spec.node_id),),
        )

    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=SimpleNamespace(_current_task_id="task-7"),
        decomposer=decomposer,
        subtree_translator=translate_subtree,
    )
    parent = _kernel_spec("program/demo", task_type="Place")

    first = adapter.expand(parent, context=object())
    second = adapter.expand(parent, context=object())

    first_id = first.nodes[0].spec.node_id
    second_id = second.nodes[0].spec.node_id
    assert first_id == second_id
    assert translated_ids == [first_id, second_id]
    assert first.edges[0].edge.parent_id == parent.node_id
    assert first.edges[0].edge.child_id == first_id
    assert not first_id.startswith("random-")
    assert decomposer.seen_task_types == ["place", "place"]


def test_decomposition_makes_dynamic_child_support_explicit() -> None:
    class NestedHarnessDecomposer:
        name = "nested"
        version = "2.0"

        def supports(self, node, context) -> bool:
            del context
            return node.spec.task_type in {"place", "grasp"}

        def expand(self, node, context):
            del context
            return SimpleNamespace(
                children=[
                    FakeHarnessSpec(
                        node_id="runtime-grasp",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="grasp",
                        node_kind="interaction",
                        action_ref=None,
                    ),
                    FakeHarnessSpec(
                        node_id="runtime-assess",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="assess_interaction",
                        node_kind="planning",
                        action_ref=None,
                        params={"system_check": "assess_interaction"},
                    ),
                ],
                alternatives=[],
            )

    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=SimpleNamespace(_current_task_id="task-7"),
        decomposer=NestedHarnessDecomposer(),
    )

    delta = adapter.expand(_kernel_spec("program/demo"), context=object())
    specs = {
        command.spec.task_type: command.spec
        for command in delta.nodes
    }

    assert specs["grasp"].operation_kind is OperationKind.DECOMPOSER
    assert specs["grasp"].parameters["__gemini_er2__"][
        "decomposer_ref"
    ] == "nested:grasp@2.0"
    assert specs["assess_interaction"].operation_kind is \
        OperationKind.SYSTEM


def test_grasp_decomposition_injects_explicit_interaction_preparation() -> None:
    class GraspHarnessDecomposer:
        name = "grasp"
        version = "1.0"

        def supports(self, node, context) -> bool:
            del context
            return node.spec.task_type == "grasp"

        def expand(self, node, context):
            del context
            common = {
                "object_ids": ["apple_1"],
                "participants": {"manipuland": ["apple_1"]},
                "grasp_policy": "auto",
            }
            return SimpleNamespace(
                children=[
                    FakeHarnessSpec(
                        node_id="inspect",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="inspect",
                        node_kind="observation",
                        action_ref=None,
                        tool_ref="inspect_entities",
                    ),
                    FakeHarnessSpec(
                        node_id="assess",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="assess_interaction",
                        node_kind="planning",
                        action_ref=None,
                        params={
                            **common,
                            "system_check": "assess_interaction",
                        },
                    ),
                    FakeHarnessSpec(
                        node_id="pick",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="pick_object",
                        params=common,
                    ),
                    FakeHarnessSpec(
                        node_id="verify",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="verify",
                        node_kind="verification",
                        action_ref=None,
                        params={"system_check": "verify"},
                    ),
                ],
                alternatives=[],
                rationale="original grasp expansion",
            )

    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=SimpleNamespace(_current_task_id="task-grasp"),
        decomposer=GraspHarnessDecomposer(),
    )

    delta = adapter.expand(
        _kernel_spec("program/grasp", task_type="grasp"),
        context=object(),
    )
    specs = [command.spec for command in delta.nodes]

    assert [spec.task_type for spec in specs] == [
        "inspect",
        "reposition_for_interaction",
        "assess_interaction",
        "pick_object",
        "verify",
    ]
    preparation = specs[1]
    assert preparation.operation_kind is OperationKind.DECOMPOSER
    assert preparation.parameters["participants"] == {
        "reference": ["apple_1"]
    }
    assert preparation.parameters["purpose"] == "prepare_grasp_execution"
    assert preparation.parameters["require_structural_execution"] is True
    assert (
        preparation.execution_policy
        is ExecutionPolicy.REQUIRE_EXECUTION
    )
    assert preparation.parameters["grasp_policy"] == "auto"
    assert preparation.parameters["__gemini_er2__"]["metadata"] == {
        "adapter_injected": True,
        "role": "grasp_interaction_preparation",
    }


def test_reposition_decomposition_preserves_layout_alongside_detour() -> None:
    parent_id = "program/place/repair-reposition"
    layout_producer_id = "program/place/select-space"
    target_ref = "layout/place/cup"
    parent = TaskNodeSpec(
        node_id=parent_id,
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.REPAIR,
        parameters={
            "object_ids": ["plate_1"],
            "reference_ids": ["plate_1"],
            "placement_object_ids": ["cup_1"],
            "destination_ids": ["plate_1"],
            "participants": {
                "reference": ["plate_1"],
                "placement_object": ["cup_1"],
            },
            "interaction_target_kind": "placement_pose",
            "purpose": "repair_placement_reachability",
            "relation": "inside_support_region",
            "target_ref": target_ref,
            "layout_producer_node_id": layout_producer_id,
            "layout_continuation_scope_id": "program/place",
            "reservation_group": "place-region-cup",
            "__gemini_er2__": {
                "root_id": "program/place",
                "parent_id": "program/place/place-object",
                "node_kind": "repair",
                "task_type": "reposition_for_interaction",
                "decomposer_ref": "builtin:reposition_for_interaction",
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": layout_producer_id,
                            "continuation_node_id": parent_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                },
            },
        },
    )
    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=SimpleNamespace(_current_task_id="task-place-repair"),
    )

    delta = adapter.expand(parent, context=object())
    specs = [command.spec for command in delta.nodes]
    plan = next(
        spec for spec in specs if spec.task_type == "plan_detour"
    )
    action = next(
        spec
        for spec in specs
        if (
            spec.task_type == "reposition_for_interaction"
            and spec.operation_kind is OperationKind.PHYSICAL
        )
    )

    for consumer in (plan, action):
        assert consumer.parameters["target_ref"] == target_ref
        assert consumer.parameters["layout_producer_node_id"] == (
            layout_producer_id
        )
        assert consumer.parameters["layout_continuation_scope_id"] == (
            "program/place"
        )
        layout_contract = {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": layout_producer_id,
            "continuation_node_id": consumer.node_id,
            "ref_key": "target_ref",
            "required": True,
        }
        metadata = consumer.parameters["__gemini_er2__"]["metadata"]
        assert layout_contract in metadata["artifact_consumes"]

    plan_metadata = plan.parameters["__gemini_er2__"]["metadata"]
    action_metadata = action.parameters["__gemini_er2__"]["metadata"]
    detour_contract = plan_metadata["artifact_produces"][0]
    assert detour_contract["artifact_kind"] == "detour_path"
    assert detour_contract["continuation_node_id"] == action.node_id
    assert detour_contract in action_metadata["artifact_consumes"]


@pytest.mark.parametrize(
    ("parent_type", "child_type"),
    (
        ("relocate_blocker", "transport"),
        ("transport", "place"),
        ("place", "select_placement_space"),
        ("place", "assess_placeability"),
        ("place", "prepare_placement"),
        ("prepare_placement", "reposition_for_interaction"),
        ("vacate_placement_region", "plan_detour"),
        ("vacate_placement_region", "reposition_for_interaction"),
        ("reposition_for_interaction", "plan_detour"),
        ("plan_detour", "build_obstacle_map"),
    ),
)
def test_relocation_continuation_context_crosses_declared_edges(
    parent_type,
    child_type,
) -> None:
    context = {
        "continuation_anchor_pose": [1.9, 1.2, -0.4],
        "continuation_goal_poses": [
            [0.5, -0.4, 0.7],
            [1.4, 0.2, -2.6],
        ],
        "protected_relocation_entity_ids": [
            "apple_1",
            "cup_1",
        ],
    }
    parent = SimpleNamespace(
        spec=SimpleNamespace(
            task_type=parent_type,
            params=context,
        )
    )
    expansion = SimpleNamespace(
        children=[
            SimpleNamespace(
                task_type=child_type,
                params={"preserved": True},
            ),
            SimpleNamespace(
                task_type="verify",
                params={},
            ),
        ]
    )

    result = (
        decomposition_module
        ._with_inherited_relocation_continuation_context(
            parent,
            expansion,
        )
    )

    assert result.children[0].params == {
        "preserved": True,
        **context,
    }
    assert result.children[1].params == {}
    assert expansion.children[0].params == {"preserved": True}


def test_relocation_protection_context_propagates_without_continuation(
) -> None:
    parent = SimpleNamespace(
        spec=SimpleNamespace(
            task_type="reposition_for_interaction",
            params={
                "protected_relocation_entity_ids": [
                    "apple_1",
                    "cup_1",
                ],
            },
        )
    )
    expansion = SimpleNamespace(
        children=[
            SimpleNamespace(
                task_type="plan_detour",
                params={"preserved": True},
            ),
            SimpleNamespace(task_type="verify", params={}),
        ]
    )

    result = (
        decomposition_module
        ._with_inherited_relocation_continuation_context(
            parent,
            expansion,
        )
    )

    assert result.children[0].params == {
        "preserved": True,
        "protected_relocation_entity_ids": [
            "apple_1",
            "cup_1",
        ],
    }
    assert result.children[1].params == {}


def test_place_decomposition_selects_space_and_propagates_layout_contract() -> None:
    class StaticRegionSpace:
        def placement_region_ref(
            self,
            destination_id,
            relation,
            *,
            requested_region_ref=None,
            selector=None,
        ):
            assert destination_id == "plate_1"
            assert relation == "inside_support_region"
            assert requested_region_ref is None
            assert selector == "interior"
            return "plate_1/interior"

    runtime = SimpleNamespace(
        _current_task_id="task-place",
        perception=SimpleNamespace(
            catalog={"plate_1": {"category": "plate"}}
        ),
    )
    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=runtime,
        region_space=StaticRegionSpace(),
    )
    constraints = {
        "baseline_pose": [0.60, -1.00, 0.0],
        "minimum_relocation_distance": 0.39,
        "path_segments": [
            [[0.20, -1.00], [1.20, -1.00]],
        ],
        "object_radius": 0.17,
        "required_clearance": 0.30,
        "minimum_improvement": 0.05,
    }
    parent = _kernel_spec("program/place", task_type="place")
    parent = replace(
        parent,
        parameters={
            **dict(parent.parameters),
            **constraints,
        },
    )

    delta = adapter.expand(
        parent,
        context=object(),
    )
    specs = [command.spec for command in delta.nodes]

    assert specs[0].task_type == "select_placement_space"
    planner = specs[0]
    assert planner.operation_kind is OperationKind.SYSTEM
    assert planner.parameters["system_check"] == "plan_region_layout"
    assert planner.parameters["region_ref"] == "plate_1/interior"
    assert planner.parameters["batch_object_ids"] == ["apple_1"]
    for key, value in constraints.items():
        assert planner.parameters[key] == value

    target_ref = planner.parameters["target_ref"]
    producer_id = planner.node_id
    producer_metadata = planner.parameters["__gemini_er2__"]["metadata"]
    assert producer_metadata["artifact_produces"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": producer_id,
            "continuation_node_id": "program/place",
            "ref_key": "target_ref",
            "required": True,
        }
    ]

    consumers = {
        spec.task_type: spec
        for spec in specs
        if spec.task_type
        in {"assess_placeability", "prepare_placement", "place_object"}
    }
    assert set(consumers) == {
        "assess_placeability",
        "prepare_placement",
        "place_object",
    }
    for consumer in consumers.values():
        assert consumer.parameters["target_ref"] == target_ref
        assert consumer.parameters["layout_producer_node_id"] == producer_id
        metadata = consumer.parameters["__gemini_er2__"]["metadata"]
        assert metadata["artifact_consumes"][0] == {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": producer_id,
            "continuation_node_id": consumer.node_id,
            "ref_key": "target_ref",
            "required": True,
        }
    place_preconditions = consumers["place_object"].parameters[
        "__gemini_er2__"
    ]["preconditions"]
    assert place_preconditions == {
        "predicate": "attached_to_any_end_effector",
        "participants": {"object": ["apple_1"]},
        "parameters": {
            "semantic_role": "execution_resource",
            "repair_policy": "recursive_repair",
        },
        "desired_value": "true",
    }


def test_grouped_place_consumes_shared_layout_without_local_planner() -> None:
    class UnexpectedRegionSpace:
        @staticmethod
        def placement_region_ref(*_args, **_kwargs):
            raise AssertionError("shared layout must skip local selection")

    producer_id = "program/shared-layout"
    target_ref = "layout/shared"
    parent = TaskNodeSpec(
        node_id="program/place/apple-1",
        task_type="place",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        parameters={
            "object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "participants": {
                "manipuland": ["apple_1"],
                "destination": ["plate_1"],
            },
            "relation": "inside_support_region",
            "region_ref": "plate_1/interior",
            "target_selector": "interior",
            "batch_object_ids": ["apple_1", "apple_2"],
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": "program/sequence",
            "reservation_group": "place-sequence-red-apples",
            "reservation_index": 0,
        },
    )
    runtime = SimpleNamespace(
        _current_task_id="task-shared-layout",
        perception=SimpleNamespace(
            catalog={"plate_1": {"category": "plate"}}
        ),
    )
    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=runtime,
        region_space=UnexpectedRegionSpace(),
    )

    delta = adapter.expand(parent, context=object())
    specs = [command.spec for command in delta.nodes]

    assert all(
        spec.task_type != "select_placement_space" for spec in specs
    )
    consumers = [
        spec
        for spec in specs
        if spec.task_type
        in {"assess_placeability", "prepare_placement", "place_object"}
    ]
    assert {spec.task_type for spec in consumers} == {
        "assess_placeability",
        "prepare_placement",
        "place_object",
    }
    for consumer in consumers:
        assert consumer.parameters["target_ref"] == target_ref
        assert (
            consumer.parameters["layout_producer_node_id"]
            == producer_id
        )
        assert consumer.parameters["batch_object_ids"] == [
            "apple_1",
            "apple_2",
        ]
        contracts = consumer.parameters["__gemini_er2__"]["metadata"][
            "artifact_consumes"
        ]
        assert any(
            contract["producer_node_id"] == producer_id
            and contract["continuation_node_id"] == consumer.node_id
            for contract in contracts
        )


def test_semantic_target_selector_still_produces_explicit_layout() -> None:
    calls = []

    class StaticRegionSpace:
        def placement_region_ref(
            self,
            destination_id,
            relation,
            *,
            requested_region_ref=None,
            selector=None,
        ):
            calls.append(
                (
                    destination_id,
                    relation,
                    requested_region_ref,
                    selector,
                )
            )
            return "floor_1/support"

    runtime = SimpleNamespace(
        _current_task_id="task-floor-layout",
        perception=SimpleNamespace(
            catalog={"floor_1": {"category": "floor"}}
        ),
    )
    parent = TaskNodeSpec(
        node_id="program/place-floor-layout",
        task_type="place",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        parameters={
            "object_ids": ["plate_1"],
            "destination_ids": ["floor_1"],
            "participants": {
                "manipuland": ["plate_1"],
                "destination": ["floor_1"],
            },
            "relation": "on_support",
            "region_ref": "floor_1/support",
            "target_selector": "support",
        },
    )
    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=runtime,
        region_space=StaticRegionSpace(),
    )

    delta = adapter.expand(parent, context=object())
    specs = [command.spec for command in delta.nodes]
    planner = next(
        spec for spec in specs if spec.task_type == "select_placement_space"
    )

    assert calls == [
        ("floor_1", "on_support", "floor_1/support", "support")
    ]
    assert planner.parameters["region_ref"] == "floor_1/support"
    assert planner.parameters["target_selector"] == "support"
    assert planner.parameters["system_check"] == "plan_region_layout"
    target_ref = planner.parameters["target_ref"]

    consumers = [
        spec
        for spec in specs
        if spec.task_type
        in {"assess_placeability", "prepare_placement", "place_object"}
    ]
    assert {consumer.task_type for consumer in consumers} == {
        "assess_placeability",
        "prepare_placement",
        "place_object",
    }
    for consumer in consumers:
        assert consumer.parameters["target_ref"] == target_ref
        assert consumer.parameters["layout_producer_node_id"] == planner.node_id
        contracts = consumer.parameters["__gemini_er2__"]["metadata"][
            "artifact_consumes"
        ]
        assert any(
            contract["artifact_kind"] == "layout_targets"
            and contract["producer_node_id"] == planner.node_id
            and contract["continuation_node_id"] == consumer.node_id
            for contract in contracts
        )


def test_floor_place_prepares_compiled_stance_before_atomic_release() -> None:
    class StaticRegionSpace:
        def placement_region_ref(
            self,
            destination_id,
            relation,
            *,
            requested_region_ref=None,
            selector=None,
        ):
            assert destination_id == "floor_1"
            assert relation == "on_support"
            assert requested_region_ref is None
            assert selector == "support"
            return "floor_1/support"

    runtime = SimpleNamespace(
        _current_task_id="task-floor-place",
        perception=SimpleNamespace(
            catalog={"floor_1": {"category": "floor"}}
        ),
    )
    parent = TaskNodeSpec(
        node_id="program/place-floor",
        task_type="place",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        parameters={
            "object_ids": ["plate_1"],
            "destination_ids": ["floor_1"],
            "participants": {
                "manipuland": ["plate_1"],
                "destination": ["floor_1"],
            },
            "relation": "on_support",
        },
    )
    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=runtime,
        region_space=StaticRegionSpace(),
    )

    delta = adapter.expand(parent, context=object())
    specs = [command.spec for command in delta.nodes]
    task_types = [spec.task_type for spec in specs]
    prepare = next(
        spec for spec in specs if spec.task_type == "prepare_placement"
    )
    place = next(spec for spec in specs if spec.task_type == "place_object")
    planner = next(
        spec for spec in specs if spec.task_type == "select_placement_space"
    )

    assert task_types.index("select_placement_space") < task_types.index(
        "prepare_placement"
    )
    assert task_types.index("prepare_placement") < task_types.index(
        "place_object"
    )
    assert prepare.operation_kind is OperationKind.DECOMPOSER
    assert prepare.execution_policy is ExecutionPolicy.REQUIRE_EXECUTION
    assert prepare.parameters["participants"] == {
        "manipuland": ["plate_1"],
        "reference": ["floor_1"],
        "placement_object": ["plate_1"],
        "destination": ["floor_1"],
    }
    assert prepare.parameters["purpose"] == (
        "placement_execution_preparation"
    )
    assert prepare.parameters["relation"] == "on_support"
    assert place.parameters["placement_prepared"] is True
    assert place.parameters["base_prepositioned"] is True
    assert prepare.parameters["target_ref"] == planner.parameters["target_ref"]
    assert place.parameters["target_ref"] == planner.parameters["target_ref"]
    assert prepare.parameters["__gemini_er2__"]["metadata"]["role"] == (
        "floor_placement_execution_preparation"
    )
    for consumer in (prepare, place):
        contracts = consumer.parameters["__gemini_er2__"]["metadata"][
            "artifact_consumes"
        ]
        assert any(
            contract["artifact_kind"] == "layout_targets"
            and contract["producer_node_id"] == planner.node_id
            and contract["continuation_node_id"] == consumer.node_id
            for contract in contracts
        )


def test_recovery_staging_declares_layout_contract_for_place() -> None:
    class RecoveryHarnessDecomposer:
        name = "recovery"
        version = "1.0"

        def supports(self, node, context) -> bool:
            del context
            return node.spec.task_type in {"clear_end_effector", "place"}

        def expand(self, node, context):
            del context
            shared = {
                "object_ids": ["cup_1"],
                "destination_ids": ["table_1"],
                "relation": "on_support",
            }
            return SimpleNamespace(
                children=[
                    FakeHarnessSpec(
                        node_id="select-staging",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="select_staging_region",
                        node_kind="planning",
                        action_ref=None,
                        params={
                            **shared,
                            "system_check": "select_staging",
                        },
                    ),
                    FakeHarnessSpec(
                        node_id="place-held",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="place",
                        node_kind="repair",
                        action_ref=None,
                        params={
                            **shared,
                            "participants": {
                                "manipuland": ["cup_1"],
                                "destination": ["table_1"],
                            },
                        },
                    ),
                    FakeHarnessSpec(
                        node_id="verify-clear",
                        parent_id=node.node_id,
                        root_id=node.spec.root_id,
                        task_type="verify",
                        node_kind="verification",
                        action_ref=None,
                        params={"system_check": "verify"},
                    ),
                ],
                alternatives=[],
            )

    adapter = HarnessBuiltinTaskDecomposerAdapter(
        runtime=SimpleNamespace(_current_task_id="task-recovery"),
        decomposer=RecoveryHarnessDecomposer(),
    )

    delta = adapter.expand(
        _kernel_spec(
            "program/recovery",
            task_type="clear_end_effector",
        ),
        context=object(),
    )
    specs = {command.spec.task_type: command.spec for command in delta.nodes}
    producer = specs["select_staging_region"]
    continuation = specs["place"]

    target_ref = producer.parameters["target_ref"]
    assert producer.parameters.get("reservation_index") is None
    assert continuation.parameters["target_ref"] == target_ref
    assert producer.parameters["layout_producer_node_id"] == producer.node_id
    assert continuation.parameters["layout_producer_node_id"] == (
        producer.node_id
    )
    assert producer.parameters["layout_continuation_scope_id"] == (
        continuation.node_id
    )
    assert continuation.parameters["layout_continuation_scope_id"] == (
        continuation.node_id
    )
    producer_metadata = producer.parameters["__gemini_er2__"]["metadata"]
    consumer_metadata = continuation.parameters["__gemini_er2__"]["metadata"]
    contract = {
        "schema": "task_artifact/1.0",
        "artifact_kind": "layout_targets",
        "producer_node_id": producer.node_id,
        "continuation_node_id": continuation.node_id,
        "ref_key": "target_ref",
        "required": True,
    }
    assert producer_metadata["artifact_produces"] == [contract]
    assert consumer_metadata["artifact_consumes"] == [contract]


def test_decomposition_adapter_does_not_depend_on_tree_executor() -> None:
    import task_recursive_tree.integrations.gemini_er2.decomposition as module

    source = inspect.getsource(module)
    assert "TreeExecutor" not in source
    assert "tree_executor" not in source
