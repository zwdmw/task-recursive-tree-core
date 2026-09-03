from __future__ import annotations

import argparse
from pathlib import Path

from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import NodeStatus
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    Observation,
    RobotState,
)


def demo_world(*, blocked: bool = False) -> tuple[GridMap, Observation]:
    static_occupied = {
        (4, 0),
        (4, 1),
        (4, 2),
        (4, 4),
        (4, 5),
        (4, 6),
        (4, 7),
    }
    entities = [
        EntityState(
            entity_id="cup-red",
            kind="object",
            pose=Pose(2.5, 2.5),
            radius=0.16,
            tags=frozenset({"red", "cup"}),
            properties={"movable": True},
        ),
        EntityState(
            entity_id="drop-zone",
            kind="region",
            pose=Pose(7.5, 5.5),
            radius=0.45,
            tags=frozenset({"destination"}),
            properties={
                "acceptance_radius": 0.3,
                "placement_offsets": ((0.0, 0.0),),
            },
        ),
        EntityState(
            entity_id="parking-zone",
            kind="region",
            pose=Pose(1.5, 6.5),
            radius=0.45,
            tags=frozenset({"parking"}),
            properties={"acceptance_radius": 0.3},
        ),
    ]
    if blocked:
        entities.append(
            EntityState(
                entity_id="movable-crate",
                kind="object",
                pose=Pose(4.5, 3.5),
                radius=0.35,
                tags=frozenset({"crate"}),
                properties={
                    "movable": True,
                    "nav_obstacle": True,
                },
            )
        )
    grid = GridMap(
        width=10,
        height=8,
        static_occupied=frozenset(static_occupied),
    )
    observation = Observation(
        robot=RobotState(
            base_pose=Pose(1.5, 1.5),
            joints=(0.0, 0.0),
        ),
        entities=tuple(entities),
    )
    return grid, observation


def run_demo(
    *,
    blocked: bool = False,
    tree_output: Path | None = None,
) -> int:
    grid, observation = demo_world(blocked=blocked)
    application = build_application(grid, observation)
    program = TaskProgram.place(
        SpatialSelector.exact("cup-red", "object"),
        SpatialSelector.exact("drop-zone", "region"),
        program_id="demo-place",
    )
    status = application.execute(program)
    print(application.inspector.render_ascii())
    snapshot = application.world.snapshot()
    cup = snapshot.entity("cup-red")
    destination = snapshot.entity("drop-zone")
    print()
    print(f"status={status.value}")
    print(
        "cup_pose="
        f"({cup.pose.x:.2f}, {cup.pose.y:.2f}) "
        "destination="
        f"({destination.pose.x:.2f}, {destination.pose.y:.2f})"
    )
    print(f"world_snapshot={snapshot.snapshot_ref}")
    print(f"transactions={len(application.transaction_records())}")
    if tree_output:
        saved = application.inspector.save(tree_output)
        print(f"tree_snapshot={saved}")
    return 0 if status is NodeStatus.SUCCEEDED else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the recursive mobile-manipulation task-tree demo."
    )
    parser.add_argument(
        "--blocked",
        action="store_true",
        help="Insert a movable blocker and exercise recursive repair.",
    )
    parser.add_argument(
        "--tree-output",
        type=Path,
        help="Write the final tree, event log, and execution stack as JSON.",
    )
    arguments = parser.parse_args()
    raise SystemExit(
        run_demo(
            blocked=arguments.blocked,
            tree_output=arguments.tree_output,
        )
    )


if __name__ == "__main__":
    main()
