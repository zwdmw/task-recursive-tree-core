from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any

from .harness_safety import (
    BASE_ROTATION_SAMPLE,
    BASE_TRANSLATION_SAMPLE,
)
from .paths import import_harness_module
from .route_validation import validate_se2_route


POST_PLACEMENT_CONTINUATION_MODEL_VERSION = (
    "post_placement_continuation/1.1"
)


@dataclass(frozen=True)
class ContinuationRouteContext:
    anchor_pose: tuple[float, float, float]
    goal_poses: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class PostPlacementContinuationAssessment:
    ok: bool
    start_pose: tuple[float, float, float]
    goal_poses: tuple[tuple[float, float, float], ...]
    selected_goal_pose: tuple[float, float, float] | None
    route_attempt_count: int
    blocking_entity_ids: tuple[str, ...]
    destination_is_floor: bool
    hypothetical_obstacle: Mapping[str, Any] | None
    failure_mode: str | None
    rejections: tuple[Mapping[str, Any], ...]
    acceptance_mode: str = "reachable_goal"
    baseline_assessment: Mapping[str, Any] | None = None
    introduced_blocking_entity_ids: tuple[str, ...] = ()
    model_version: str = POST_PLACEMENT_CONTINUATION_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "start_pose": list(self.start_pose),
            "goal_poses": [list(pose) for pose in self.goal_poses],
            "selected_goal_pose": (
                list(self.selected_goal_pose)
                if self.selected_goal_pose is not None
                else None
            ),
            "route_attempt_count": self.route_attempt_count,
            "blocking_entity_ids": list(self.blocking_entity_ids),
            "destination_is_floor": self.destination_is_floor,
            "hypothetical_obstacle": copy.deepcopy(
                dict(self.hypothetical_obstacle)
                if self.hypothetical_obstacle is not None
                else None
            ),
            "failure_mode": self.failure_mode,
            "rejections": [
                copy.deepcopy(dict(value)) for value in self.rejections
            ],
            "acceptance_mode": self.acceptance_mode,
            "baseline_assessment": copy.deepcopy(
                dict(self.baseline_assessment)
                if self.baseline_assessment is not None
                else None
            ),
            "introduced_blocking_entity_ids": list(
                self.introduced_blocking_entity_ids
            ),
            "model_version": self.model_version,
        }


@dataclass(frozen=True)
class _ContinuationRouteSweep:
    selected_goal_pose: tuple[float, float, float] | None
    route_attempt_count: int
    blocking_entity_ids: tuple[str, ...]
    failure_mode: str | None
    rejections: tuple[Mapping[str, Any], ...]

    @property
    def ok(self) -> bool:
        return self.selected_goal_pose is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "selected_goal_pose": (
                list(self.selected_goal_pose)
                if self.selected_goal_pose is not None
                else None
            ),
            "route_attempt_count": self.route_attempt_count,
            "blocking_entity_ids": list(self.blocking_entity_ids),
            "failure_mode": self.failure_mode,
            "rejections": [
                copy.deepcopy(dict(value)) for value in self.rejections
            ],
        }


def continuation_route_context(
    params: Mapping[str, Any],
) -> ContinuationRouteContext | None:
    anchor_raw = params.get("continuation_anchor_pose")
    goals_raw = params.get("continuation_goal_poses")
    if anchor_raw is None and goals_raw is None:
        return None
    if anchor_raw is None or goals_raw is None:
        raise ValueError(
            "post-placement continuation requires both "
            "continuation_anchor_pose and continuation_goal_poses"
        )

    anchor = _pose3(anchor_raw, field_name="continuation_anchor_pose")
    if not isinstance(goals_raw, (list, tuple)):
        raise ValueError("continuation_goal_poses must be a sequence")
    goals: list[tuple[float, float, float]] = []
    for index, raw_goal in enumerate(goals_raw):
        goal = _pose3(
            raw_goal,
            field_name=f"continuation_goal_poses[{index}]",
        )
        if goal not in goals:
            goals.append(goal)
    if not goals:
        raise ValueError("continuation_goal_poses must not be empty")
    return ContinuationRouteContext(
        anchor_pose=anchor,
        goal_poses=tuple(goals),
    )


def assess_post_placement_continuation(
    *,
    runtime: Any,
    object_id: str,
    destination_id: str,
    placement_point: Sequence[Any],
    start_pose: Sequence[Any],
    goal_poses: Sequence[Sequence[Any]],
    navigation: Any | None = None,
    control: Any | None = None,
    harness_root: str | None = None,
    module_loader: Callable[..., Any] = import_harness_module,
) -> PostPlacementContinuationAssessment:
    start = _pose3(start_pose, field_name="start_pose")
    goals = tuple(
        _pose3(value, field_name=f"goal_poses[{index}]")
        for index, value in enumerate(goal_poses)
    )
    if not goals:
        raise ValueError("goal_poses must not be empty")
    point = _point_xy(placement_point, field_name="placement_point")

    navigation = navigation or module_loader(
        "er2sim.navigation_capabilities",
        harness_root=harness_root,
    )
    control = control or module_loader(
        "er2sim.control",
        harness_root=harness_root,
    )
    scene = getattr(runtime, "scene", None)
    perception = getattr(runtime, "perception", None)
    world = getattr(runtime, "world", None)
    position_ok = getattr(scene, "base_position_ok", None)
    if perception is None or not callable(position_ok):
        raise RuntimeError(
            "post-placement continuation requires scene and perception"
        )

    excluded = {str(object_id)}
    navigation_obstacles = tuple(
        navigation.build_navigation_obstacles(
            perception,
            world=world,
            exclude_entity_ids=excluded,
        )
    )
    route_obstacles = list(
        navigation.build_base_route_obstacles(
            perception,
            world=world,
            exclude_entity_ids=excluded,
        )
    )
    destination_is_floor = _entity_category(
        perception,
        destination_id,
    ) == "floor"
    hypothetical = None
    if destination_is_floor:
        prototype = next(
            (
                obstacle
                for obstacle in navigation.build_base_route_obstacles(
                    perception,
                    world=world,
                )
                if str(getattr(obstacle, "entity_id", ""))
                == str(object_id)
            ),
            None,
        )
        if prototype is None or not hasattr(
            prototype,
            "__dataclass_fields__",
        ):
            return _failed_assessment(
                start=start,
                goals=goals,
                destination_is_floor=True,
                failure_mode="HYPOTHETICAL_OBSTACLE_UNAVAILABLE",
                rejections=(
                    {
                        "phase": "hypothetical_obstacle",
                        "code": "PERCEPTION_INSUFFICIENT",
                        "failure_mode": (
                            "HYPOTHETICAL_OBSTACLE_UNAVAILABLE"
                        ),
                        "object_id": str(object_id),
                    },
                ),
            )
        center_x = (
            float(prototype.min_x) + float(prototype.max_x)
        ) / 2.0
        center_y = (
            float(prototype.min_y) + float(prototype.max_y)
        ) / 2.0
        delta_x = point[0] - center_x
        delta_y = point[1] - center_y
        hypothetical = replace(
            prototype,
            min_x=float(prototype.min_x) + delta_x,
            max_x=float(prototype.max_x) + delta_x,
            min_y=float(prototype.min_y) + delta_y,
            max_y=float(prototype.max_y) + delta_y,
        )
        route_obstacles.append(hypothetical)

    terminal_position_tolerance = float(
        getattr(
            control,
            "INTERACTION_PATH_POSITION_TOLERANCE",
            0.008,
        )
    )
    terminal_yaw_tolerance = float(
        getattr(
            control,
            "INTERACTION_PATH_YAW_TOLERANCE",
            0.02,
        )
    )
    obstacle_snapshot = (
        _obstacle_dict(hypothetical)
        if hypothetical is not None
        else None
    )
    post_placement = _assess_route_sweep(
        start=start,
        goals=goals,
        navigation=navigation,
        position_ok=position_ok,
        navigation_obstacles=navigation_obstacles,
        route_obstacles=tuple(route_obstacles),
        terminal_position_tolerance=terminal_position_tolerance,
        terminal_yaw_tolerance=terminal_yaw_tolerance,
    )
    if post_placement.ok:
        return PostPlacementContinuationAssessment(
            ok=True,
            start_pose=start,
            goal_poses=goals,
            selected_goal_pose=post_placement.selected_goal_pose,
            route_attempt_count=post_placement.route_attempt_count,
            blocking_entity_ids=(),
            destination_is_floor=destination_is_floor,
            hypothetical_obstacle=obstacle_snapshot,
            failure_mode=None,
            rejections=post_placement.rejections,
        )

    explicit_introduced_blockers = tuple(
        dict.fromkeys(
            (
                *(
                    value
                    for value in post_placement.blocking_entity_ids
                    if value == str(object_id)
                ),
                *_hypothetical_pose_blocking_ids(
                    navigation=navigation,
                    hypothetical=hypothetical,
                    start=start,
                    goals=goals,
                ),
            )
        )
    )
    if (
        post_placement.failure_mode
        == "POST_PLACEMENT_START_POSE_BLOCKED"
    ):
        return _failed_assessment(
            start=start,
            goals=goals,
            route_attempt_count=post_placement.route_attempt_count,
            destination_is_floor=destination_is_floor,
            hypothetical_obstacle=obstacle_snapshot,
            blocking_entity_ids=post_placement.blocking_entity_ids,
            failure_mode=post_placement.failure_mode,
            rejections=post_placement.rejections,
            introduced_blocking_entity_ids=(
                explicit_introduced_blockers
            ),
        )

    baseline = _assess_route_sweep(
        start=start,
        goals=goals,
        navigation=navigation,
        position_ok=position_ok,
        navigation_obstacles=navigation_obstacles,
        route_obstacles=tuple(
            obstacle
            for obstacle in route_obstacles
            if obstacle is not hypothetical
        ),
        terminal_position_tolerance=terminal_position_tolerance,
        terminal_yaw_tolerance=terminal_yaw_tolerance,
    )
    baseline_payload = baseline.to_dict()
    if not baseline.ok and not explicit_introduced_blockers:
        return PostPlacementContinuationAssessment(
            ok=True,
            start_pose=start,
            goal_poses=goals,
            selected_goal_pose=None,
            route_attempt_count=post_placement.route_attempt_count,
            blocking_entity_ids=(),
            destination_is_floor=destination_is_floor,
            hypothetical_obstacle=obstacle_snapshot,
            failure_mode=None,
            rejections=post_placement.rejections,
            acceptance_mode="non_degrading",
            baseline_assessment=baseline_payload,
        )

    introduced_blockers = (
        explicit_introduced_blockers
        or ((str(object_id),) if hypothetical is not None else ())
    )
    return _failed_assessment(
        start=start,
        goals=goals,
        route_attempt_count=post_placement.route_attempt_count,
        destination_is_floor=destination_is_floor,
        hypothetical_obstacle=obstacle_snapshot,
        blocking_entity_ids=post_placement.blocking_entity_ids,
        failure_mode="POST_PLACEMENT_CONTINUATION_UNREACHABLE",
        rejections=post_placement.rejections,
        baseline_assessment=baseline_payload,
        introduced_blocking_entity_ids=introduced_blockers,
    )


def _assess_route_sweep(
    *,
    start: tuple[float, float, float],
    goals: tuple[tuple[float, float, float], ...],
    navigation: Any,
    position_ok: Callable[[float, float], bool],
    navigation_obstacles: Sequence[Any],
    route_obstacles: Sequence[Any],
    terminal_position_tolerance: float,
    terminal_yaw_tolerance: float,
) -> _ContinuationRouteSweep:
    pose_clear = getattr(navigation, "route_pose_clear", None)
    if not callable(pose_clear):
        raise RuntimeError(
            "navigation module has no route_pose_clear validator"
        )
    start_clear, start_blocker = pose_clear(
        start,
        route_obstacles,
        footprint=navigation.DEFAULT_BASE_FOOTPRINT,
    )
    if not start_clear:
        blocker_ids = _ids(start_blocker)
        return _ContinuationRouteSweep(
            selected_goal_pose=None,
            route_attempt_count=0,
            blocking_entity_ids=blocker_ids,
            failure_mode="POST_PLACEMENT_START_POSE_BLOCKED",
            rejections=(
                {
                    "phase": "continuation_start",
                    "code": "INTERACTION_POSE_BLOCKED",
                    "failure_mode": "POST_PLACEMENT_START_POSE_BLOCKED",
                    "blocking_entity_ids": list(blocker_ids),
                },
            ),
        )

    rejections: list[dict[str, Any]] = []
    route_attempts = 0
    for goal in goals:
        goal_clear, goal_blocker = pose_clear(
            goal,
            route_obstacles,
            footprint=navigation.DEFAULT_BASE_FOOTPRINT,
        )
        if not goal_clear:
            rejections.append(
                {
                    "goal": list(goal),
                    "phase": "continuation_goal",
                    "code": "GOAL_POSE_IN_COLLISION",
                    "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
                    "blocking_entity_ids": list(_ids(goal_blocker)),
                }
            )
            continue
        route_attempts += 1
        try:
            plan = dict(
                navigation.plan_base_route(
                    start,
                    goal,
                    route_obstacles,
                    position_ok=position_ok,
                )
            )
            poses = tuple(
                _pose3(
                    value,
                    field_name="planned continuation route pose",
                )
                for value in (plan.get("poses_world") or ())
            )
        except Exception as error:
            rejections.append(
                {
                    "goal": list(goal),
                    "phase": "continuation_route",
                    "code": "SYSTEM_OPERATION_ERROR",
                    "failure_mode": "CONTINUATION_ROUTE_VALIDATION_FAILED",
                    "exception_type": type(error).__name__,
                    "message": str(error),
                }
            )
            continue
        if not poses:
            rejections.append(
                {
                    "goal": list(goal),
                    "phase": "continuation_route",
                    "code": str(
                        plan.get("reason_code") or "NO_SAFE_DETOUR"
                    ),
                    "failure_mode": "NO_COLLISION_FREE_ROUTE",
                    "blocking_entity_ids": [
                        str(value)
                        for value in plan.get(
                            "direct_blocking_entity_ids",
                            (),
                        )
                    ],
                    "expanded_states": int(
                        plan.get("expanded_states") or 0
                    ),
                }
            )
            continue
        validation = validate_se2_route(
            navigation=navigation,
            position_ok=position_ok,
            poses=poses,
            expected_start=start,
            expected_goal=goal,
            navigation_obstacles=navigation_obstacles,
            route_obstacles=route_obstacles,
            endpoint_position_tolerance=terminal_position_tolerance,
            endpoint_yaw_tolerance=terminal_yaw_tolerance,
            translation_sample=BASE_TRANSLATION_SAMPLE,
            rotation_sample=BASE_ROTATION_SAMPLE,
        )
        if not validation.ok:
            rejections.append(
                {
                    "goal": list(goal),
                    "phase": "continuation_route",
                    "code": validation.reason_code or "PATH_BLOCKED",
                    "failure_mode": (
                        validation.failure_mode
                        or "NO_COLLISION_FREE_ROUTE"
                    ),
                    "blocking_entity_ids": list(
                        validation.blocking_entity_ids
                    ),
                    "route_validation": validation.to_dict(),
                }
            )
            continue
        return _ContinuationRouteSweep(
            selected_goal_pose=goal,
            route_attempt_count=route_attempts,
            blocking_entity_ids=(),
            failure_mode=None,
            rejections=tuple(rejections),
        )

    blockers = tuple(
        dict.fromkeys(
            str(value)
            for rejection in rejections
            for value in rejection.get("blocking_entity_ids", ())
            if str(value)
        )
    )
    return _ContinuationRouteSweep(
        selected_goal_pose=None,
        route_attempt_count=route_attempts,
        blocking_entity_ids=blockers,
        failure_mode="POST_PLACEMENT_CONTINUATION_UNREACHABLE",
        rejections=tuple(rejections),
    )


def _hypothetical_pose_blocking_ids(
    *,
    navigation: Any,
    hypothetical: Any | None,
    start: tuple[float, float, float],
    goals: tuple[tuple[float, float, float], ...],
) -> tuple[str, ...]:
    if hypothetical is None:
        return ()
    pose_clear = getattr(navigation, "route_pose_clear", None)
    if not callable(pose_clear):
        return ()
    obstacle_id = str(getattr(hypothetical, "entity_id", ""))
    blockers: list[str] = []
    for pose in (start, *goals):
        clear, blocker = pose_clear(
            pose,
            (hypothetical,),
            footprint=navigation.DEFAULT_BASE_FOOTPRINT,
        )
        if clear:
            continue
        blocker_ids = _ids(blocker)
        if blocker_ids:
            blockers.extend(blocker_ids)
        elif obstacle_id:
            blockers.append(obstacle_id)
    return tuple(dict.fromkeys(blockers))


def _failed_assessment(
    *,
    start: tuple[float, float, float],
    goals: tuple[tuple[float, float, float], ...],
    destination_is_floor: bool,
    failure_mode: str,
    route_attempt_count: int = 0,
    hypothetical_obstacle: Mapping[str, Any] | None = None,
    blocking_entity_ids: tuple[str, ...] = (),
    rejections: tuple[Mapping[str, Any], ...] = (),
    baseline_assessment: Mapping[str, Any] | None = None,
    introduced_blocking_entity_ids: tuple[str, ...] = (),
) -> PostPlacementContinuationAssessment:
    return PostPlacementContinuationAssessment(
        ok=False,
        start_pose=start,
        goal_poses=goals,
        selected_goal_pose=None,
        route_attempt_count=route_attempt_count,
        blocking_entity_ids=blocking_entity_ids,
        destination_is_floor=destination_is_floor,
        hypothetical_obstacle=hypothetical_obstacle,
        failure_mode=failure_mode,
        rejections=rejections,
        acceptance_mode="rejected",
        baseline_assessment=baseline_assessment,
        introduced_blocking_entity_ids=(
            introduced_blocking_entity_ids
        ),
    )


def _entity_category(perception: Any, entity_id: str) -> str:
    catalog = getattr(perception, "catalog", None)
    metadata = (
        catalog.get(str(entity_id))
        if isinstance(catalog, Mapping)
        else None
    )
    return (
        str(metadata.get("category") or "").casefold()
        if isinstance(metadata, Mapping)
        else ""
    )


def _obstacle_dict(obstacle: Any) -> dict[str, Any]:
    serializer = getattr(obstacle, "to_dict", None)
    if callable(serializer):
        value = serializer()
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    return {
        "entity_id": str(getattr(obstacle, "entity_id", "")),
        "bounds": [
            float(obstacle.min_x),
            float(obstacle.max_x),
            float(obstacle.min_y),
            float(obstacle.max_y),
        ],
    }


def _pose3(
    value: Any,
    *,
    field_name: str,
) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ) or len(value) < 3:
        raise ValueError(f"{field_name} requires x, y, and yaw")
    pose = (float(value[0]), float(value[1]), float(value[2]))
    if not all(isfinite(component) for component in pose):
        raise ValueError(f"{field_name} values must be finite")
    return pose


def _point_xy(
    value: Any,
    *,
    field_name: str,
) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ) or len(value) < 2:
        raise ValueError(f"{field_name} requires x and y")
    point = (float(value[0]), float(value[1]))
    if not all(isfinite(component) for component in point):
        raise ValueError(f"{field_name} values must be finite")
    return point


def _ids(value: Any) -> tuple[str, ...]:
    return (str(value),) if value is not None and str(value) else ()


__all__ = [
    "ContinuationRouteContext",
    "POST_PLACEMENT_CONTINUATION_MODEL_VERSION",
    "PostPlacementContinuationAssessment",
    "assess_post_placement_continuation",
    "continuation_route_context",
]
