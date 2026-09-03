from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from .harness_safety import (
    BASE_ROTATION_SAMPLE,
    BASE_TRANSLATION_SAMPLE,
    DEFAULT_HARNESS_SAFETY_POLICY,
)


ROUTE_VALIDATION_MODEL_VERSION = (
    "task_recursive_tree_se2_route_validation/1.0"
)


@dataclass(frozen=True)
class RouteValidationEvidence:
    ok: bool
    reason_code: str | None
    failure_mode: str | None
    blocking_entity_ids: tuple[str, ...]
    start_position_error: float
    start_yaw_error: float
    goal_position_error: float
    goal_yaw_error: float
    route_pose_count: int
    dense_pose_count: int
    translation_sample: float
    rotation_sample: float
    model_version: str = ROUTE_VALIDATION_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason_code": self.reason_code,
            "failure_mode": self.failure_mode,
            "blocking_entity_ids": list(self.blocking_entity_ids),
            "start_position_error": self.start_position_error,
            "start_yaw_error": self.start_yaw_error,
            "goal_position_error": self.goal_position_error,
            "goal_yaw_error": self.goal_yaw_error,
            "route_pose_count": self.route_pose_count,
            "dense_pose_count": self.dense_pose_count,
            "translation_sample": self.translation_sample,
            "rotation_sample": self.rotation_sample,
            "model_version": self.model_version,
            "safety_policy_fingerprint": (
                DEFAULT_HARNESS_SAFETY_POLICY.fingerprint
            ),
        }


def validate_se2_route(
    *,
    navigation: Any,
    position_ok: Callable[[float, float], bool],
    poses: Sequence[Sequence[float]],
    expected_start: Sequence[float],
    expected_goal: Sequence[float],
    navigation_obstacles: Sequence[Any],
    route_obstacles: Sequence[Any],
    endpoint_position_tolerance: float,
    endpoint_yaw_tolerance: float,
    translation_sample: float = BASE_TRANSLATION_SAMPLE,
    rotation_sample: float = BASE_ROTATION_SAMPLE,
) -> RouteValidationEvidence:
    path = tuple(_pose3(pose) for pose in poses)
    start = _pose3(expected_start)
    goal = _pose3(expected_goal)
    position_tolerance = _positive(
        endpoint_position_tolerance,
        "endpoint_position_tolerance",
    )
    yaw_tolerance = _positive(
        endpoint_yaw_tolerance,
        "endpoint_yaw_tolerance",
    )
    translation_step = _positive(
        translation_sample,
        "translation_sample",
    )
    rotation_step = _positive(
        rotation_sample,
        "rotation_sample",
    )
    if not path:
        return _evidence(
            ok=False,
            reason_code="PATH_MALFORMED",
            failure_mode="EMPTY_ROUTE",
            path=path,
            dense=(),
            start_errors=(math.inf, math.inf),
            goal_errors=(math.inf, math.inf),
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )

    start_errors = _pose_errors(path[0], start)
    goal_errors = _pose_errors(path[-1], goal)
    if (
        start_errors[0] > position_tolerance
        or start_errors[1] > yaw_tolerance
    ):
        return _evidence(
            ok=False,
            reason_code="ROUTE_START_MISMATCH",
            failure_mode="ROUTE_ENDPOINT_MISMATCH",
            path=path,
            dense=(),
            start_errors=start_errors,
            goal_errors=goal_errors,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )
    if (
        goal_errors[0] > position_tolerance
        or goal_errors[1] > yaw_tolerance
    ):
        return _evidence(
            ok=False,
            reason_code="ROUTE_GOAL_MISMATCH",
            failure_mode="ROUTE_ENDPOINT_MISMATCH",
            path=path,
            dense=(),
            start_errors=start_errors,
            goal_errors=goal_errors,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )

    route_clear, route_blocker = navigation.route_pose_path_clear(
        path,
        route_obstacles,
        footprint=navigation.DEFAULT_BASE_FOOTPRINT,
        translation_sample=translation_step,
        rotation_sample=rotation_step,
    )
    if not route_clear:
        blockers = (
            (str(route_blocker),)
            if route_blocker is not None
            else ()
        )
        return _evidence(
            ok=False,
            reason_code="PATH_BLOCKED",
            failure_mode="ROUTE_OBSTACLE_BLOCKS_PATH",
            blockers=blockers,
            path=path,
            dense=(),
            start_errors=start_errors,
            goal_errors=goal_errors,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )

    points_xy = [(pose[0], pose[1]) for pose in path]
    residual = navigation.residual_navigation_obstacles(
        navigation_obstacles,
        route_obstacles,
    )
    residual_clear, residual_blocker = navigation.path_clear(
        points_xy,
        residual,
        clearance=navigation.DEFAULT_BASE_CLEARANCE,
    )
    if not residual_clear:
        blockers = (
            (str(residual_blocker),)
            if residual_blocker is not None
            else ()
        )
        return _evidence(
            ok=False,
            reason_code="PATH_BLOCKED",
            failure_mode="RESIDUAL_OBSTACLE_BLOCKS_PATH",
            blockers=blockers,
            path=path,
            dense=(),
            start_errors=start_errors,
            goal_errors=goal_errors,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )

    dense = tuple(
        densify_se2_path(
            path,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )
    )
    if not all(position_ok(pose[0], pose[1]) for pose in dense):
        return _evidence(
            ok=False,
            reason_code="NO_REACHABLE_POSE",
            failure_mode="BASE_PATH_OUTSIDE_WORKSPACE",
            path=path,
            dense=dense,
            start_errors=start_errors,
            goal_errors=goal_errors,
            translation_sample=translation_step,
            rotation_sample=rotation_step,
        )
    return _evidence(
        ok=True,
        reason_code=None,
        failure_mode=None,
        path=path,
        dense=dense,
        start_errors=start_errors,
        goal_errors=goal_errors,
        translation_sample=translation_step,
        rotation_sample=rotation_step,
    )


def densify_se2_path(
    poses: Sequence[Sequence[float]],
    *,
    translation_sample: float = BASE_TRANSLATION_SAMPLE,
    rotation_sample: float = BASE_ROTATION_SAMPLE,
) -> list[tuple[float, float, float]]:
    path = [_pose3(pose) for pose in poses]
    if not path:
        return []
    translation_step = _positive(
        translation_sample,
        "translation_sample",
    )
    rotation_step = _positive(
        rotation_sample,
        "rotation_sample",
    )
    dense = [path[0]]
    current = path[0]
    for target in path[1:]:
        yaw_delta = _angle_delta(current[2], target[2])
        rotation_steps = max(
            1,
            int(math.ceil(abs(yaw_delta) / rotation_step)),
        )
        for index in range(1, rotation_steps + 1):
            dense.append(
                (
                    current[0],
                    current[1],
                    current[2]
                    + yaw_delta * (index / rotation_steps),
                )
            )
        distance = math.hypot(
            target[0] - current[0],
            target[1] - current[1],
        )
        translation_steps = max(
            1,
            int(math.ceil(distance / translation_step)),
        )
        for index in range(1, translation_steps + 1):
            alpha = index / translation_steps
            dense.append(
                (
                    current[0] + (target[0] - current[0]) * alpha,
                    current[1] + (target[1] - current[1]) * alpha,
                    target[2],
                )
            )
        current = target
    return dense


def _evidence(
    *,
    ok: bool,
    reason_code: str | None,
    failure_mode: str | None,
    path: Sequence[Sequence[float]],
    dense: Sequence[Sequence[float]],
    start_errors: tuple[float, float],
    goal_errors: tuple[float, float],
    translation_sample: float,
    rotation_sample: float,
    blockers: tuple[str, ...] = (),
) -> RouteValidationEvidence:
    return RouteValidationEvidence(
        ok=ok,
        reason_code=reason_code,
        failure_mode=failure_mode,
        blocking_entity_ids=tuple(dict.fromkeys(blockers)),
        start_position_error=start_errors[0],
        start_yaw_error=start_errors[1],
        goal_position_error=goal_errors[0],
        goal_yaw_error=goal_errors[1],
        route_pose_count=len(path),
        dense_pose_count=len(dense),
        translation_sample=translation_sample,
        rotation_sample=rotation_sample,
    )


def _pose_errors(
    actual: Sequence[float],
    expected: Sequence[float],
) -> tuple[float, float]:
    return (
        math.hypot(
            float(actual[0]) - float(expected[0]),
            float(actual[1]) - float(expected[1]),
        ),
        abs(_angle_delta(float(actual[2]), float(expected[2]))),
    )


def _pose3(raw: Sequence[float]) -> tuple[float, float, float]:
    if len(raw) < 3:
        raise ValueError("route pose must contain x, y, and yaw")
    pose = (float(raw[0]), float(raw[1]), float(raw[2]))
    if not all(math.isfinite(value) for value in pose):
        raise ValueError("route pose must be finite")
    return pose


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _angle_delta(start: float, end: float) -> float:
    return (float(end) - float(start) + math.pi) % (
        2.0 * math.pi
    ) - math.pi


__all__ = [
    "ROUTE_VALIDATION_MODEL_VERSION",
    "RouteValidationEvidence",
    "densify_se2_path",
    "validate_se2_route",
]
