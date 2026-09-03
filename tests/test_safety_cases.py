from __future__ import annotations

from dataclasses import replace

from task_recursive_tree.artifacts import ArtifactStore
from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.demo import demo_world
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.contracts import RepairContext
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    NodeOrigin,
    NodeStatus,
    OperationKind,
    TaskNodeSpec,
)
from task_recursive_tree.task.repair import DefaultRepairResolver
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.model import WorldModel
from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    Observation,
    RobotState,
)


def place_program(program_id: str = "safety") -> TaskProgram:
    return TaskProgram.place(
        SpatialSelector.exact("cup-red", "object"),
        SpatialSelector.exact("drop-zone", "region"),
        program_id=program_id,
    )


def test_unreachable_height_fails_before_physical_execution() -> None:
    grid, observation = demo_world()
    entities = tuple(
        replace(entity, pose=replace(entity.pose, z=10.0))
        if entity.entity_id == "drop-zone"
        else entity
        for entity in observation.entities
    )
    application = build_application(
        grid, replace(observation, entities=entities)
    )

    assert application.execute(place_program("high-target")) is NodeStatus.FAILED
    assert application.transaction_records() == ()
    assert application.world.snapshot().entity("cup-red").pose.z == 0.0


def test_static_wall_does_not_create_false_blocker_witness() -> None:
    grid = GridMap(
        width=8,
        height=6,
        static_occupied=frozenset((4, y) for y in range(6)),
    )
    crate_pose = Pose(3.5, 4.5)
    observation = Observation(
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0)),
        entities=(
            EntityState(
                "cup-red",
                "object",
                Pose(1.5, 1.5),
                properties={"movable": True},
            ),
            EntityState(
                "drop-zone",
                "region",
                Pose(6.5, 1.5),
                radius=0.4,
                properties={"acceptance_radius": 0.3},
            ),
            EntityState(
                "unrelated-crate",
                "object",
                crate_pose,
                radius=0.25,
                properties={"movable": True, "nav_obstacle": True},
            ),
            EntityState(
                "parking",
                "region",
                Pose(1.5, 4.5),
                tags=frozenset({"parking"}),
            ),
        ),
    )
    application = build_application(grid, observation)

    assert application.execute(place_program("false-blocker")) is NodeStatus.FAILED
    assert application.store.edges(kind=EdgeKind.REPAIR) == ()
    assert (
        application.world.snapshot().entity("unrelated-crate").pose
        == crate_pose
    )
    assert application.transaction_records() == ()


def test_held_route_blockage_proposes_replan_not_nested_place() -> None:
    grid = GridMap(5, 5)
    observation = Observation(
        robot=RobotState(
            Pose(0.5, 0.5),
            (0.0, 0.0),
            gripper_open=False,
            held_object_id="cup-red",
        ),
        entities=(
            EntityState("cup-red", "object", Pose(1.0, 0.5)),
            EntityState(
                "blocker",
                "object",
                Pose(2.5, 0.5),
                properties={"movable": True, "nav_obstacle": True},
            ),
        ),
    )
    context = RepairContext(
        world=WorldModel(grid, observation),
        artifacts=ArtifactStore(),
    )
    node = TaskNodeSpec(
        node_id="navigate-held",
        task_type="NavigateHeld",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={"scope": "held", "repair_depth": 0},
    )
    proposal = DefaultRepairResolver().propose(
        node,
        Diagnostic(
            "ROUTE_BLOCKED",
            "dynamic obstacle",
            details={"blocker_id": "blocker"},
            retryable=True,
        ),
        context,
        0,
    )

    assert proposal is not None
    entry = proposal.delta.nodes[0].spec
    assert entry.task_type == "RepairTransferPlan"
    assert entry.task_type != "Place"

