from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.integrations.gemini_er2 import (
    HarnessTreeTranslationError,
    KernelTreeProjection,
    to_harness_status,
    translate_harness_tree,
)
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    ExecutionFrame,
    FramePhase,
    GraphDelta,
    NodeOrigin,
    NodeStatus,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)
from task_recursive_tree.task.store import TaskTreeStore


@dataclass
class HarnessSpec:
    node_id: str
    parent_id: str | None = None
    root_id: str | None = None
    node_kind: str = "interaction"
    task_type: str = "task"
    object_ref: object = None
    actor_ref: object = None
    from_state: object = None
    to_state: object = None
    preconditions: dict = field(default_factory=dict)
    goal: dict = field(default_factory=dict)
    goal_scope: str = "world"
    obligations: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    children: list[str] = field(default_factory=list)
    child_policy: str = "sequence"
    decomposer_ref: str | None = None
    tool_ref: str | None = None
    action_ref: str | None = None
    retry_policy: dict = field(default_factory=dict)
    resource_policy: dict = field(default_factory=dict)
    origin: str = "system_decomposer"
    metadata: dict = field(default_factory=dict)


@dataclass
class HarnessNode:
    spec: HarnessSpec


def harness_tree(*, reversed_nodes: bool = False):
    root = HarnessNode(
        HarnessSpec(
            node_id="root",
            root_id="root",
            node_kind="task",
            task_type="sequence",
            children=["pick", "place"],
            child_policy="sequence",
            origin="task_compiler",
            metadata={"label": "Place"},
        )
    )
    pick = HarnessNode(
        HarnessSpec(
            node_id="pick",
            parent_id="root",
            root_id="root",
            node_kind="physical",
            task_type="pick",
            object_ref={"entity_id": "apple_1"},
            params={
                "object_ids": ["apple_1"],
                "participants": {"manipuland": ["apple_1"]},
            },
            action_ref="pick_object",
            retry_policy={"max_attempts": 3, "max_repairs": 2},
            resource_policy={"claims": ["gripper"], "exclusive": True},
        )
    )
    place = HarnessNode(
        HarnessSpec(
            node_id="place",
            parent_id="root",
            root_id="root",
            node_kind="physical",
            task_type="place",
            params={
                "object_ids": ["apple_1"],
                "destination_ids": ["plate_1"],
                "participants": {
                    "manipuland": ["apple_1"],
                    "destination": ["plate_1"],
                },
            },
            goal={"predicate": "object_at", "desired_value": "true"},
            action_ref="place_object",
        )
    )
    items = [("root", root), ("pick", pick), ("place", place)]
    if reversed_nodes:
        items.reverse()
    return SimpleNamespace(
        root_id="root",
        nodes=dict(items),
        event_seq=4,
        world_revision=17,
        program_ref="program/place",
        task_id="task-001",
        metadata={"instruction": "put the apple on the plate"},
    )


def initialized_store(tree=None):
    definition = translate_harness_tree(tree or harness_tree())
    store = TaskTreeStore()
    writer = store.claim_writer()
    store.apply_delta(definition.delta, writer=writer)
    return store, writer, definition


def test_translation_is_deterministic_and_preserves_harness_spec() -> None:
    first = translate_harness_tree(harness_tree())
    second = translate_harness_tree(harness_tree(reversed_nodes=True))

    assert first == second
    assert [item.spec.node_id for item in first.delta.nodes] == [
        "root",
        "pick",
        "place",
    ]
    assert [
        (item.edge.parent_id, item.edge.child_id, item.edge.order)
        for item in first.delta.edges
    ] == [
        ("root", "pick", 0),
        ("root", "place", 1),
    ]

    root = first.delta.nodes[0].spec
    pick = first.delta.nodes[1].spec
    assert root.operation_kind is OperationKind.DECOMPOSER
    assert root.control_kind is ControlKind.SEQUENCE
    assert root.origin is NodeOrigin.COMPILER
    assert pick.operation_kind is OperationKind.PHYSICAL
    assert pick.control_kind is ControlKind.LEAF
    assert pick.max_attempts == 3
    assert pick.max_repairs == 2
    assert pick.parameters["object_ids"] == ["apple_1"]


def test_translation_accepts_to_dict_shape_and_rejects_ambiguous_graphs() -> None:
    raw = {
        "root_id": "root",
        "task_id": "task-dict",
        "nodes": {
            "root": {
                "spec": {
                    "node_id": "root",
                    "task_type": "sequence",
                    "children": ["leaf"],
                }
            },
            "leaf": {
                "spec": {
                    "node_id": "leaf",
                    "parent_id": "root",
                    "task_type": "verify",
                    "tool_ref": "predicate",
                }
            },
        },
    }
    translated = translate_harness_tree(raw)
    assert translated.root_id == "root"
    assert translated.delta.nodes[1].spec.operation_kind is OperationKind.SYSTEM

    raw["nodes"]["leaf"]["spec"]["parent_id"] = "someone-else"
    with pytest.raises(HarnessTreeTranslationError, match="parent mismatch"):
        translate_harness_tree(raw)


def test_projection_is_dynamic_read_only_and_harness_shaped() -> None:
    store, writer, _definition = initialized_store()
    details = {
        "place": {
            "phase": "goal_check",
            "runtime_request_id": "task-001/place/1",
            "transaction_id": "txn-9",
            "last_result": {
                "transaction_id": "txn-9",
                "termination": "canceled",
                "effect_state": "refuted",
                "verification": "false",
                "residual_state": {
                    "safe_checkpoint": "before_move",
                    "phase": "base_motion",
                },
            },
        }
    }
    revision = {"value": 21}
    projection = KernelTreeProjection(
        store,
        world_revision=lambda: revision["value"],
        runtime_details=details,
    )
    nodes = projection.nodes
    place = projection.node("place")

    assert projection.task_id == "task-001"
    assert projection.program_ref == "program/place"
    assert projection.world_revision == 21
    assert place.status == "PENDING"
    assert place.spec.parent_id == "root"
    assert place.spec.children == []
    assert place.spec.is_atomic is True
    assert place.spec.action_ref == "place_object"
    assert place.spec.params["participants"]["destination"] == ["plate_1"]
    assert place.runtime.phase == "goal_check"
    assert place.runtime.transaction_id == "txn-9"
    assert place.runtime.last_result["termination"] == "canceled"

    store.set_runtime(
        "place",
        store.runtime("place").evolve(
            status=NodeStatus.RUNNING,
            attempts=1,
        ),
        writer=writer,
    )
    store.push_frame(
        ExecutionFrame("place", phase=FramePhase.ENTER),
        writer=writer,
    )
    details["place"]["phase"] = "base_motion"
    revision["value"] = 22

    assert place.status == "EXECUTING"
    assert place.runtime.phase == "base_motion"
    assert projection.execution_stack[-1].node_id == "place"
    assert projection.world_revision == 22

    repair = TaskNodeSpec(
        node_id="place/repair",
        task_type="RecoverPlace",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
    )
    store.apply_delta(
        GraphDelta.from_specs(
            (repair,),
            (
                TaskEdge(
                    "place",
                    repair.node_id,
                    kind=EdgeKind.REPAIR,
                    order=0,
                ),
            ),
        ),
        writer=writer,
    )
    assert "place/repair" in nodes
    assert place.spec.children == ["place/repair"]
    assert projection.node("place/repair").runtime.to_dict()["repair_depth"] == 1

    params = place.spec.params
    params["participants"]["destination"].append("mutated")
    assert place.spec.params["participants"]["destination"] == ["plate_1"]
    with pytest.raises(TypeError):
        nodes["other"] = place
    with pytest.raises(AttributeError):
        place.status = "FAILED"

    snapshot = projection.to_dict()
    assert snapshot["schema"] == "task_tree/1.0"
    assert snapshot["nodes"]["place"]["runtime"]["status"] == "EXECUTING"
    assert snapshot["nodes"]["place"]["runtime"]["transaction_id"] == "txn-9"
    assert snapshot["execution_stack"][-1]["node_id"] == "place"


def test_projection_maps_diagnostics_and_both_status_spellings() -> None:
    store, writer, _definition = initialized_store()
    store.set_runtime(
        "pick",
        store.runtime("pick").evolve(
            status=NodeStatus.FAILED,
            attempts=2,
            last_diagnostic=Diagnostic(
                code="IK_FAILED",
                message="No feasible grasp pose",
                details={"destination_id": "plate_1"},
                retryable=False,
            ),
        ),
        writer=writer,
    )
    projection = KernelTreeProjection(store)
    runtime = projection.node("pick").runtime

    assert runtime.status == "FAILED"
    assert runtime.failure == {
        "source_node_id": "pick",
        "code": "IK_FAILED",
        "message": "No feasible grasp pose",
        "details": {"destination_id": "plate_1"},
        "retryable": False,
        "repairable": False,
        "destination_id": "plate_1",
    }
    assert runtime.to_dict()["diagnostics"] == ["No feasible grasp pose"]
    assert to_harness_status("SUCCEEDED") == "SUCCEEDED"
    assert to_harness_status("succeeded") == "SUCCEEDED"
    assert to_harness_status(NodeStatus.CANCELLED) == "CANCELLED"
    assert to_harness_status("canceled") == "CANCELLED"
