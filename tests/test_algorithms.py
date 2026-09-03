from __future__ import annotations

from math import hypot

import pytest

from task_recursive_tree.capabilities.astar import astar_grid
from task_recursive_tree.capabilities.ik import (
    forward_planar,
    solve_planar_2link,
)
from task_recursive_tree.capabilities.rrt import (
    RRTPathNotFound,
    rrt_plan,
)
from task_recursive_tree.robot.controllers import (
    ControllerFailure,
    ReferenceJointTrajectoryController,
)
from task_recursive_tree.robot.model import RobotModel


def test_astar_routes_through_gap() -> None:
    occupied = {(2, y) for y in range(5) if y != 3}
    result = astar_grid(
        (0, 1),
        (4, 1),
        occupied=occupied,
        width=5,
        height=5,
    )
    assert (2, 3) in result.cells
    assert result.cells[0] == (0, 1)
    assert result.cells[-1] == (4, 1)


def test_planar_ik_reaches_target() -> None:
    candidates = solve_planar_2link(
        1.0,
        0.25,
        (0.8, 0.8),
        ((-3.14, 3.14), (-2.8, 2.8)),
    )
    assert candidates
    reached = forward_planar(candidates[0], (0.8, 0.8))
    assert hypot(reached[0] - 1.0, reached[1] - 0.25) < 1e-8


def test_rrt_detours_around_joint_space_obstacle() -> None:
    def collision_free(configuration: tuple[float, ...]) -> bool:
        x, y = configuration
        return hypot(x, y) > 0.35

    result = rrt_plan(
        (-1.0, 0.0),
        (1.0, 0.0),
        joint_limits=((-1.5, 1.5), (-1.5, 1.5)),
        collision_free=collision_free,
        seed=9,
        max_iterations=5000,
    )
    assert result.iterations > 0
    assert result.path[0] == (-1.0, 0.0)
    assert result.path[-1] == (1.0, 0.0)
    assert all(collision_free(point) for point in result.path)


def test_rrt_rejects_collision_between_safe_endpoints() -> None:
    def collision_free(configuration: tuple[float, ...]) -> bool:
        return not 0.09 <= configuration[0] <= 0.11

    with pytest.raises(RRTPathNotFound):
        rrt_plan(
            (0.0,),
            (0.2,),
            joint_limits=((0.0, 0.2),),
            collision_free=collision_free,
            seed=3,
            max_iterations=80,
        )


def test_controller_revalidates_sparse_joint_edges() -> None:
    controller = ReferenceJointTrajectoryController()
    model = RobotModel()

    def collision_free(configuration: tuple[float, ...]) -> bool:
        return not 0.09 <= configuration[0] <= 0.11

    with pytest.raises(ControllerFailure) as caught:
        controller.validate(
            current=(0.0, 0.0),
            path=((0.0, 0.0), (0.2, 0.0)),
            model=model,
            collision_free=collision_free,
        )

    assert caught.value.diagnostic.code == "ARM_PATH_BLOCKED"
    assert caught.value.diagnostic.details["blocked_sample"][0] == \
        pytest.approx(0.1)
