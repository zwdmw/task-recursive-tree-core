from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

from task_recursive_tree.integrations.gemini_er2.semantics import (
    GeminiER2NodeSemantics,
)
from task_recursive_tree.task.contracts import SemanticState
from task_recursive_tree.task.model import (
    ControlKind,
    NodeOrigin,
    OperationKind,
    TaskNodeRuntime,
    TaskNodeSpec,
)


class FakeTruth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class FakePredicateResult:
    def __init__(self, value: FakeTruth) -> None:
        self.value = value
        self.confidence = 0.8
        self.evidence = ["evidence://formula"]
        self.derivation = "fake_predicate/1.0"
        self.details = {"observed": value.value}

    def to_dict(self):
        return {
            "value": self.value.value,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence),
            "derivation": self.derivation,
            "details": dict(self.details),
        }


class FakePredicates:
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls: list[tuple[object, ...]] = []

    def evaluate_formula(
        self,
        formula,
        world,
        scene,
        perception,
        slot_bindings,
    ):
        self.calls.append(
            (formula, world, scene, perception, slot_bindings)
        )
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _node(
    *,
    goal=None,
    preconditions=None,
    parameters=None,
    operation_kind=OperationKind.PHYSICAL,
) -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id="node/place",
        task_type="place",
        operation_kind=operation_kind,
        control_kind=(
            ControlKind.SEQUENCE
            if operation_kind is OperationKind.DECOMPOSER
            else ControlKind.LEAF
        ),
        origin=NodeOrigin.COMPILER,
        parameters={
            **dict(parameters or {}),
            "__gemini_er2__": {
                "goal": goal,
                "preconditions": preconditions,
                "tree": {"task_id": "task-7"},
            }
        },
    )


def _runtime(results):
    predicates = FakePredicates(results)
    slot = SimpleNamespace(bound_entity_ids=["apple_1"])
    task = SimpleNamespace(slots={"source-slot": slot})
    world = SimpleNamespace(tasks={"task-7": task})
    runtime = SimpleNamespace(
        predicates=predicates,
        world=world,
        scene=object(),
        perception=object(),
        recent_outcomes=[],
        _current_task_id="task-7",
    )
    return runtime, predicates


def test_semantics_maps_harness_true_false_and_unknown() -> None:
    runtime, predicates = _runtime(
        [
            FakePredicateResult(FakeTruth.TRUE),
            FakePredicateResult(FakeTruth.FALSE),
            FakePredicateResult(FakeTruth.UNKNOWN),
        ]
    )
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    node = _node(
        goal={"predicate": "inside_support_region"},
        preconditions={"predicate": "end_effector_available"},
    )
    node_runtime = TaskNodeRuntime()

    goal = semantics.evaluate_goal(node, node_runtime)
    preconditions = semantics.evaluate_preconditions(node, node_runtime)
    postconditions = semantics.evaluate_postconditions(node, node_runtime)

    assert goal is not None
    assert goal.state is SemanticState.SATISFIED
    assert preconditions is not None
    assert preconditions.state is SemanticState.UNSATISFIED
    assert postconditions is not None
    assert postconditions.state is SemanticState.UNKNOWN
    assert goal.evidence["evidence_refs"] == ["evidence://formula"]
    assert all(
        call[4] == {"source-slot": ["apple_1"]}
        for call in predicates.calls
    )
    assert predicates.calls[0][1:] == (
        runtime.world,
        runtime.scene,
        runtime.perception,
        {"source-slot": ["apple_1"]},
    )


def test_semantics_returns_none_for_undeclared_formulae() -> None:
    runtime, predicates = _runtime([])
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    node_runtime = TaskNodeRuntime()

    assert semantics.evaluate_goal(_node(goal={}), node_runtime) is None
    assert (
        semantics.evaluate_preconditions(
            _node(preconditions=None),
            node_runtime,
        )
        is None
    )
    assert predicates.calls == []


def test_semantics_reports_structural_node_goal_truthfully() -> None:
    runtime, predicates = _runtime(
        [
            FakePredicateResult(FakeTruth.TRUE),
            FakePredicateResult(FakeTruth.TRUE),
        ]
    )
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    node = _node(
        goal={"predicate": "interaction_pose_achieved"},
        parameters={"require_structural_execution": True},
        operation_kind=OperationKind.DECOMPOSER,
    )

    entry = semantics.evaluate_goal(node, TaskNodeRuntime(expanded=False))
    postcondition = semantics.evaluate_postconditions(
        node,
        TaskNodeRuntime(expanded=True),
    )

    assert entry is not None
    assert entry.state is SemanticState.SATISFIED
    assert predicates.calls == [
        (
            {"predicate": "interaction_pose_achieved"},
            runtime.world,
            runtime.scene,
            runtime.perception,
            {"source-slot": ["apple_1"]},
        ),
        (
            {"predicate": "interaction_pose_achieved"},
            runtime.world,
            runtime.scene,
            runtime.perception,
            {"source-slot": ["apple_1"]},
        ),
    ]
    assert postcondition is not None
    assert postcondition.state is SemanticState.SATISFIED


def test_postconditions_use_confirmed_transaction_effect() -> None:
    runtime, predicates = _runtime([])
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    goal = {
        "predicate": "placement_execution_ready",
        "participants": {
            "object": ["cup_1"],
            "destination": ["plate_1"],
            "robot": ["robot_1"],
        },
        "parameters": {
            "relation": "inside_support_region",
            "target_ref": "layout-targets-1",
        },
        "desired_value": "true",
    }
    node_runtime = TaskNodeRuntime(
        adapter_state={
            "last_request_id": "request-1",
            "last_result": {
                "request_id": "request-1",
                "transaction_id": "transaction-1",
                "termination": "succeeded",
                "effect_state": "confirmed",
                "verification": "true",
                "effects": [
                    {
                        "effect_id": "effect-placement-ready",
                        "predicate": "placement_execution_ready",
                        "participants": {
                            "object": ["cup_1"],
                            "destination": ["plate_1"],
                        },
                        "parameters": {
                            "relation": "inside_support_region",
                            "target_ref": "layout-targets-1",
                        },
                        "state": "confirmed",
                        "required": True,
                        "evidence_refs": ["sim_placement_approach_plan"],
                    }
                ],
            },
        }
    )

    check = semantics.evaluate_postconditions(
        _node(goal=goal),
        node_runtime,
    )

    assert check is not None
    assert check.state is SemanticState.SATISFIED
    assert check.evidence["derivation"] == (
        "confirmed_physical_transaction_effect"
    )
    assert check.evidence["matched_effect_ids"] == [
        "effect-placement-ready"
    ]
    assert predicates.calls == []


def test_postconditions_only_recheck_unconfirmed_conjuncts() -> None:
    runtime, predicates = _runtime(
        [
            FakePredicateResult(FakeTruth.TRUE),
            FakePredicateResult(FakeTruth.TRUE),
        ]
    )
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    held_formula = {
        "predicate": "attached_to_any_end_effector",
        "participants": {"object": ["cup_1"]},
        "desired_value": "true",
    }
    goal = {
        "op": "and",
        "args": [
            {
                "predicate": "placement_execution_ready",
                "participants": {
                    "object": ["cup_1"],
                    "destination": ["plate_1"],
                    "robot": ["robot_1"],
                },
                "parameters": {
                    "relation": "inside_support_region",
                    "target_ref": "layout-targets-1",
                },
                "desired_value": "true",
            },
            held_formula,
        ],
    }
    node_runtime = TaskNodeRuntime(
        adapter_state={
            "last_request_id": "request-1",
            "last_result": {
                "request_id": "request-1",
                "transaction_id": "transaction-1",
                "termination": "succeeded",
                "effect_state": "confirmed",
                "verification": "true",
                "effects": [
                    {
                        "effect_id": "effect-placement-ready",
                        "predicate": "placement_execution_ready",
                        "participants": {
                            "object": ["cup_1"],
                            "destination": ["plate_1"],
                        },
                        "parameters": {
                            "relation": "inside_support_region",
                            "target_ref": "layout-targets-1",
                        },
                        "state": "confirmed",
                        "required": True,
                    }
                ],
            },
        }
    )

    check = semantics.evaluate_postconditions(
        _node(goal=goal),
        node_runtime,
    )
    runtime.recent_outcomes.append(
        node_runtime.adapter_state["last_result"]
    )
    sibling_check = semantics.evaluate_goal(
        _node(goal=goal),
        TaskNodeRuntime(),
    )

    assert check is not None
    assert check.state is SemanticState.SATISFIED
    assert check.evidence["derivation"] == (
        "confirmed_physical_transaction_effect_plus_live"
    )
    assert check.evidence["residual_formula"] == held_formula
    assert sibling_check is not None
    assert sibling_check.state is SemanticState.SATISFIED
    assert sibling_check.evidence["transaction_effects"]["source"] == (
        "latest_task_transaction"
    )
    assert len(predicates.calls) == 2
    assert predicates.calls[0][0] == held_formula
    assert predicates.calls[1][0] == held_formula


def test_recent_effects_from_before_executor_start_are_ignored() -> None:
    old_result = {
        "request_id": "old-request",
        "termination": "succeeded",
        "effect_state": "confirmed",
        "verification": "true",
        "effects": [
            {
                "effect_id": "old-effect",
                "predicate": "placement_execution_ready",
                "participants": {
                    "object": ["cup_1"],
                    "destination": ["plate_1"],
                },
                "state": "confirmed",
                "required": True,
            }
        ],
    }
    runtime, predicates = _runtime(
        [FakePredicateResult(FakeTruth.TRUE)]
    )
    runtime.recent_outcomes.append(old_result)
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    goal = {
        "predicate": "placement_execution_ready",
        "participants": {
            "object": ["cup_1"],
            "destination": ["plate_1"],
        },
        "desired_value": "true",
    }

    check = semantics.evaluate_goal(
        _node(goal=goal),
        TaskNodeRuntime(),
    )

    assert check is not None
    assert check.state is SemanticState.SATISFIED
    assert len(predicates.calls) == 1
    assert predicates.calls[0][0] == goal


def test_postconditions_recheck_when_transaction_effect_mismatches() -> None:
    runtime, predicates = _runtime(
        [FakePredicateResult(FakeTruth.TRUE)]
    )
    semantics = GeminiER2NodeSemantics(runtime=runtime)
    goal = {
        "predicate": "placement_execution_ready",
        "participants": {
            "object": ["cup_1"],
            "destination": ["plate_1"],
        },
        "parameters": {"target_ref": "layout-targets-current"},
        "desired_value": "true",
    }
    node_runtime = TaskNodeRuntime(
        adapter_state={
            "last_request_id": "request-2",
            "last_result": {
                "request_id": "request-2",
                "termination": "succeeded",
                "effect_state": "confirmed",
                "verification": "true",
                "effects": [
                    {
                        "effect_id": "effect-stale-placement-ready",
                        "predicate": "placement_execution_ready",
                        "participants": {
                            "object": ["cup_1"],
                            "destination": ["plate_1"],
                        },
                        "parameters": {
                            "target_ref": "layout-targets-stale"
                        },
                        "state": "confirmed",
                        "required": True,
                    }
                ],
            },
        }
    )

    check = semantics.evaluate_postconditions(
        _node(goal=goal),
        node_runtime,
    )

    assert check is not None
    assert check.state is SemanticState.SATISFIED
    assert len(predicates.calls) == 1


def test_semantics_treats_evaluator_exception_as_unknown() -> None:
    runtime, _predicates = _runtime([RuntimeError("sensor unavailable")])
    semantics = GeminiER2NodeSemantics(runtime=runtime)

    check = semantics.evaluate_goal(
        _node(goal={"predicate": "visible"}),
        TaskNodeRuntime(),
    )

    assert check is not None
    assert check.state is SemanticState.UNKNOWN
    assert check.diagnostic is not None
    assert (
        check.diagnostic.code
        == "GEMINI_ER2_SEMANTIC_EVALUATION_ERROR"
    )
    assert check.evidence["exception_type"] == "RuntimeError"


def test_semantics_treats_malformed_formula_as_unknown() -> None:
    runtime, predicates = _runtime([])
    semantics = GeminiER2NodeSemantics(runtime=runtime)

    check = semantics.evaluate_goal(
        _node(goal=["not", "a", "formula"]),
        TaskNodeRuntime(),
    )

    assert check is not None
    assert check.state is SemanticState.UNKNOWN
    assert check.diagnostic is not None
    assert check.diagnostic.code == "GEMINI_ER2_FORMULA_INVALID"
    assert predicates.calls == []


def test_support_region_diagnostic_excludes_fixed_and_ungraspable_occupants(
) -> None:
    runtime, _predicates = _runtime(
        [FakePredicateResult(FakeTruth.FALSE)]
    )
    support = {
        "aisle_left": "floor_1",
        "apple_2": "floor_1",
        "apple_3": "floor_1",
    }
    runtime.perception = SimpleNamespace(
        catalog={
            "floor_1": {"category": "floor"},
            "aisle_left": {
                "movable": False,
                "attributes": {"fixed": True},
                "interaction_capabilities": {
                    "grasp": "unavailable",
                },
            },
            "apple_2": {
                "movable": True,
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
        },
        support_of=lambda entity_id: support.get(entity_id),
        scene=SimpleNamespace(body={}),
    )
    semantics = GeminiER2NodeSemantics(runtime=runtime)

    check = semantics.evaluate_goal(
        _node(
            goal={
                "predicate": "support_region_empty",
                "participants": {"region_owner": ["floor_1"]},
                "desired_value": "true",
            }
        ),
        TaskNodeRuntime(),
    )

    assert check is not None
    assert check.state is SemanticState.UNSATISFIED
    assert check.diagnostic is not None
    assert check.diagnostic.code == "REGION_OCCUPIED"
    assert check.diagnostic.details["blocking_entity_ids"] == [
        "apple_3"
    ]
