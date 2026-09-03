from __future__ import annotations

import json

from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.demo import demo_world
from task_recursive_tree.artifacts import (
    BindingArtifact,
    NavigationPlan,
    PickPlan,
    TransferPlan,
)
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import EdgeKind, NodeOrigin, NodeStatus
from task_recursive_tree.world.geometry import distance


def program(program_id: str) -> TaskProgram:
    return TaskProgram.place(
        SpatialSelector.exact("cup-red", "object"),
        SpatialSelector.exact("drop-zone", "region"),
        program_id=program_id,
    )


def test_place_workflow_is_end_to_end_and_explicit(tmp_path) -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    status = application.execute(program("normal"))

    assert status is NodeStatus.SUCCEEDED
    snapshot = application.world.snapshot()
    assert distance(
        snapshot.entity("cup-red").pose,
        snapshot.entity("drop-zone").pose,
    ) < 1e-9
    assert snapshot.robot.held_object_id is None
    assert len(application.transaction_records()) == 5
    assert application.store.stack() == ()

    task_types = {
        spec.task_type for spec in application.store.specs().values()
    }
    assert {"AStar", "IK", "RRT"}.isdisjoint(task_types)
    assert {
        "Place",
        "PlanPick",
        "PlanTransfer",
        "ExecuteGrasp",
        "ExecuteRelease",
    }.issubset(task_types)

    artifact_types = {
        type(artifact) for artifact in application.artifacts.all()
    }
    assert artifact_types == {
        BindingArtifact,
        NavigationPlan,
        PickPlan,
        TransferPlan,
    }

    target = application.inspector.save(tmp_path / "tree.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["root_id"] == "program/normal"
    assert payload["execution_stack"] == []


def test_blocked_route_mounts_and_executes_recursive_repair() -> None:
    grid, observation = demo_world(blocked=True)
    application = build_application(grid, observation)
    status = application.execute(program("blocked"))

    assert status is NodeStatus.SUCCEEDED
    repair_edges = application.store.edges(kind=EdgeKind.REPAIR)
    assert repair_edges
    repair_specs = list(application.store.specs().values())
    assert any(
        spec.task_type == "Place" and spec.origin is NodeOrigin.REPAIR
        for spec in repair_specs
    )
    snapshot = application.world.snapshot()
    assert distance(
        snapshot.entity("cup-red").pose,
        snapshot.entity("drop-zone").pose,
    ) < 1e-9
    assert distance(
        snapshot.entity("movable-crate").pose,
        snapshot.entity("parking-zone").pose,
    ) < 1e-9
