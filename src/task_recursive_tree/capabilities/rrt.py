from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite, sqrt
from random import Random
from typing import Callable

from task_recursive_tree.world.state import JointConfiguration


class RRTPathNotFound(RuntimeError):
    pass


DEFAULT_EDGE_RESOLUTION = 0.02


@dataclass(frozen=True)
class RRTResult:
    path: tuple[JointConfiguration, ...]
    iterations: int


def rrt_plan(
    start: JointConfiguration,
    goal: JointConfiguration,
    *,
    joint_limits: tuple[tuple[float, float], ...],
    collision_free: Callable[[JointConfiguration], bool],
    edge_collision_free: Callable[
        [JointConfiguration, JointConfiguration], bool
    ] | None = None,
    seed: int = 0,
    step_size: float = 0.22,
    edge_resolution: float = DEFAULT_EDGE_RESOLUTION,
    goal_bias: float = 0.2,
    max_iterations: int = 2500,
) -> RRTResult:
    if len(start) != len(goal) or len(start) != len(joint_limits):
        raise ValueError("Joint configuration dimensions do not match")
    if not isfinite(step_size) or step_size <= 0.0:
        raise ValueError("RRT step_size must be finite and positive")
    if not isfinite(edge_resolution) or edge_resolution <= 0.0:
        raise ValueError("RRT edge_resolution must be finite and positive")
    if not collision_free(start):
        raise RRTPathNotFound("RRT start configuration is in collision")
    if not collision_free(goal):
        raise RRTPathNotFound("RRT goal configuration is in collision")
    direct = _segment(
        start,
        goal,
        collision_free,
        edge_resolution,
        edge_collision_free=edge_collision_free,
        waypoint_resolution=step_size,
    )
    if direct is not None:
        return RRTResult(path=direct, iterations=0)

    random = Random(seed)
    nodes: list[JointConfiguration] = [start]
    parents: list[int | None] = [None]

    for iteration in range(1, max_iterations + 1):
        if random.random() < goal_bias:
            sample = goal
        else:
            sample = tuple(
                random.uniform(lower, upper)
                for lower, upper in joint_limits
            )
        nearest_index = min(
            range(len(nodes)),
            key=lambda index: _distance(nodes[index], sample),
        )
        candidate = _steer(nodes[nearest_index], sample, step_size)
        if not collision_free(candidate):
            continue
        if _segment(
            nodes[nearest_index],
            candidate,
            collision_free,
            edge_resolution,
            edge_collision_free=edge_collision_free,
            waypoint_resolution=step_size,
        ) is None:
            continue
        nodes.append(candidate)
        parents.append(nearest_index)
        candidate_index = len(nodes) - 1
        if _distance(candidate, goal) <= step_size:
            connection = _segment(
                candidate,
                goal,
                collision_free,
                edge_resolution,
                edge_collision_free=edge_collision_free,
                waypoint_resolution=step_size,
            )
            if connection is None:
                continue
            prefix = _reconstruct(nodes, parents, candidate_index)
            return RRTResult(
                path=prefix + connection[1:],
                iterations=iteration,
            )
    raise RRTPathNotFound(
        f"RRT did not find a path after {max_iterations} iterations"
    )


def _segment(
    start: JointConfiguration,
    end: JointConfiguration,
    collision_free: Callable[[JointConfiguration], bool],
    resolution: float,
    *,
    edge_collision_free: Callable[
        [JointConfiguration, JointConfiguration], bool
    ] | None = None,
    waypoint_resolution: float | None = None,
) -> tuple[JointConfiguration, ...] | None:
    if edge_collision_free is not None and not edge_collision_free(
        start,
        end,
    ):
        return None
    samples = sample_joint_segment(start, end, resolution=resolution)
    for point in samples:
        if not collision_free(point):
            return None
    return sample_joint_segment(
        start,
        end,
        resolution=(
            resolution
            if waypoint_resolution is None
            else waypoint_resolution
        ),
    )


def sample_joint_segment(
    start: JointConfiguration,
    end: JointConfiguration,
    *,
    resolution: float = DEFAULT_EDGE_RESOLUTION,
) -> tuple[JointConfiguration, ...]:
    if len(start) != len(end):
        raise ValueError("Joint edge dimensions do not match")
    if not isfinite(resolution) or resolution <= 0.0:
        raise ValueError("Joint edge resolution must be finite and positive")
    span = _distance(start, end)
    steps = max(1, int(ceil(span / resolution)))
    return tuple(
        tuple(
            a + (index / steps) * (b - a)
            for a, b in zip(start, end)
        )
        for index in range(steps + 1)
    )


def _steer(
    start: JointConfiguration,
    target: JointConfiguration,
    step_size: float,
) -> JointConfiguration:
    span = _distance(start, target)
    if span <= step_size:
        return target
    scale = step_size / span
    return tuple(
        value + scale * (target_value - value)
        for value, target_value in zip(start, target)
    )


def _distance(a: JointConfiguration, b: JointConfiguration) -> float:
    return sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def _reconstruct(
    nodes: list[JointConfiguration],
    parents: list[int | None],
    index: int,
) -> tuple[JointConfiguration, ...]:
    path: list[JointConfiguration] = []
    current: int | None = index
    while current is not None:
        path.append(nodes[current])
        current = parents[current]
    path.reverse()
    return tuple(path)
