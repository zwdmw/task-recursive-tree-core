from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

from .harness_safety import (
    BASE_ROTATION_SAMPLE,
    BASE_TRANSLATION_SAMPLE,
    HARNESS_SAFETY_MODEL_VERSION,
    HELD_DEPARTURE_CANDIDATE_STEP,
    HELD_DEPARTURE_MAX_DISTANCE,
    safety_policy_artifact,
)
from .continuation_routes import (
    assess_post_placement_continuation,
    continuation_route_context,
)
from .paths import import_harness_module
from .relocatability import relocatable_by_grasp
from .region_space import PROTECTED_RELOCATION_ENTITY_IDS_KEY
from .route_validation import (
    ROUTE_VALIDATION_MODEL_VERSION,
    validate_se2_route,
)


ModuleLoader = Callable[..., Any]
ENDPOINT_CLEARANCE_BUFFER = 0.005
START_EGRESS_DISTANCES = (0.04, 0.08, 0.12, 0.16, 0.24, 0.32, 0.40)
START_EGRESS_ROUTE_ATTEMPTS = 12
COUNTERFACTUAL_BLOCKER_VERIFICATION_MODEL_VERSION = (
    "task_recursive_tree_counterfactual_route_blocker/2.0"
)
COUNTERFACTUAL_BLOCKER_MAX_CANDIDATES = 6
COUNTERFACTUAL_BLOCKER_MAX_SET_SIZE = 2
SOURCE_SUPPORT_STANDOFF_RETRY_MODEL_VERSION = (
    "source_support_standoff_retry/1.1"
)
SOURCE_SUPPORT_ANGULAR_RESAMPLING_MODEL_VERSION = (
    "source_support_angular_resampling/1.0"
)
SOURCE_SUPPORT_ANGULAR_RESAMPLING_STEP_DEGREES = 15.0
SOURCE_SUPPORT_ANGULAR_RESAMPLING_MAX_CANDIDATES = 24


@dataclass(frozen=True)
class InteractionRouteFailure:
    code: str
    message: str
    details: dict[str, Any]
    retryable: bool = False


@dataclass(frozen=True)
class InteractionRoutePlan:
    artifact: dict[str, Any]
    result: dict[str, Any]
    dependency_entity_ids: tuple[str, ...]


class HarnessInteractionRoutePlanner:
    """Select and validate a reachable base stance for one interaction."""

    def __init__(
        self,
        *,
        runtime: Any,
        harness_root: str | None = None,
        module_loader: ModuleLoader = import_harness_module,
    ) -> None:
        self.runtime = runtime
        self.harness_root = harness_root
        self._module_loader = module_loader

    def plan(
        self,
        reference_id: str,
        params: Mapping[str, Any],
    ) -> InteractionRoutePlan | InteractionRouteFailure:
        params = copy.deepcopy(dict(params))
        try:
            continuation_context = continuation_route_context(params)
        except ValueError as error:
            return InteractionRouteFailure(
                code="INVALID_REQUEST",
                message=str(error),
                details={
                    "failure_mode": "MALFORMED_CONTINUATION_CONTEXT",
                    "params": copy.deepcopy(params),
                },
            )
        targets = self._module("er2sim.interaction_targets")
        navigation = self._module("er2sim.navigation_capabilities")
        control = self._module("er2sim.control")
        scene_constants = self._module("er2sim.scene")
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

        target = targets.resolve_interaction_target(
            self.runtime,
            str(reference_id),
            params=params,
        )
        if target is None:
            placement_target = (
                str(params.get("interaction_target_kind") or "").casefold()
                == "placement_pose"
            )
            code = (
                "TARGET_REGION_CHANGED"
                if placement_target
                else "TARGET_NOT_FOUND"
            )
            details = {
                "params": copy.deepcopy(params),
                "reference_id": str(reference_id),
            }
            target_ref = params.get("target_ref")
            if placement_target and isinstance(target_ref, str) and target_ref:
                details.update({
                    "artifact_kind": "layout_targets",
                    "artifact_ref": target_ref,
                    "affected_refs": [target_ref],
                })
            return InteractionRouteFailure(
                code=code,
                message=f"Cannot resolve route target for {reference_id}",
                details=details,
                retryable=placement_target,
            )

        try:
            grasp_config = targets.resolve_grasp_execution_config(
                self.runtime.perception,
                str(reference_id),
                params,
            )
        except Exception as error:
            return self._failure_from_error(
                error,
                target=target,
                params=params,
            )

        scene = self.runtime.scene
        perception = self.runtime.perception
        world = self.runtime.world
        base, start_yaw = scene.base_pose()
        start_pose = (
            float(base[0]),
            float(base[1]),
            float(start_yaw),
        )
        baseline_pose = _baseline_pose(
            params.get("baseline_pose"),
            fallback=start_pose,
        )
        minimum_reposition_distance = _nonnegative_float(
            params.get("minimum_reposition_distance"),
            default=0.0,
        )

        source_support_id = _support_of(perception, str(reference_id))
        exclude = {
            str(reference_id),
            source_support_id,
        }
        if target.destination_id:
            exclude.add(str(target.destination_id))
        if target.placement_object_id:
            exclude.add(str(target.placement_object_id))
        obstacles = navigation.build_navigation_obstacles(
            perception,
            world=world,
            exclude_entity_ids=exclude,
        )

        route_exclude = set()
        if target.placement_object_id:
            route_exclude.add(str(target.placement_object_id))
        if str(target.kind) != "entity_grasp":
            route_exclude.add(str(reference_id))
        route_obstacles = navigation.build_base_route_obstacles(
            perception,
            world=world,
            exclude_entity_ids=route_exclude,
        )
        candidate_obstacles = tuple(
            navigation.residual_navigation_obstacles(
                obstacles,
                route_obstacles,
            )
        )

        supervisor = control.MotionSupervisor(scene)
        candidate_kwargs: dict[str, Any] = {
            # Structured obstacles are owned by the SE(2) A* endpoint and
            # route checks. Reapplying their coarse circle envelopes here can
            # erase valid narrow-passage stances before A* sees them.
            "obstacle_discs": candidate_obstacles,
        }
        if params.get("stand_off") is not None:
            candidate_kwargs["distance"] = _positive_float(
                params["stand_off"],
                name="stand_off",
            )
        elif target.placement_object_id is not None:
            candidate_kwargs["distance"] = float(
                scene_constants.PLACE_APPROACH_DIST
            )

        grasp_candidate_kwargs: dict[str, Any] | None = None
        try:
            if str(target.kind) == "entity_grasp":
                geometry = (
                    perception.catalog.get(str(reference_id), {})
                    .get("geometry", {})
                ) or {}
                grasp_candidate_kwargs = {
                    "grasp_mode": str(
                        geometry.get("grasp_mode", "suction")
                    ),
                    "source_contact_entity_ids": (
                        grasp_config.source_contact_entity_ids
                    ),
                    "suction_standoff": float(
                        grasp_config.suction_standoff
                    ),
                    **candidate_kwargs,
                }
                candidate_goals = (
                    supervisor.plan_reposition_base_candidates_for_grasp(
                        target.point,
                        str(reference_id),
                        **grasp_candidate_kwargs,
                    )
                )
            else:
                candidate_goals = (
                    supervisor.plan_reposition_base_candidates(
                        target.point,
                        **candidate_kwargs,
                    )
                )
        except Exception as error:
            return self._failure_from_error(
                error,
                target=target,
                params=params,
            )
        candidate_goals = tuple(candidate_goals)
        protected_relocation_entity_ids = tuple(
            dict.fromkeys(
                [
                    *(
                        str(value)
                        for value in getattr(target, "entity_ids", ())
                        if str(value)
                    ),
                    *_entity_id_values(
                        params.get(PROTECTED_RELOCATION_ENTITY_IDS_KEY)
                    ),
                    *_entity_id_values(params.get("exclude_entity_ids")),
                    *_entity_id_values(params.get("excluded_entity_ids")),
                ]
            )
        )
        verify_relocatable_route_blockers = (
            str(target.kind).casefold() == "entity_grasp"
        )

        require_held_load = (
            target.placement_object_id is not None
            and bool(params.get("require_held_load", True))
        )
        placement = (
            self._module("er2sim.placement_capabilities")
            if target.placement_object_id is not None
            else None
        )
        placement_simulator = (
            self._module("er2sim.placement_simulator")
            if target.placement_object_id is not None
            else None
        )

        candidate_rejections: list[dict[str, Any]] = []
        route_attempts = 0
        selected_goal: tuple[float, float, float] | None = None
        selected_path: list[tuple[float, float, float]] | None = None
        selected_route: dict[str, Any] | None = None
        selected_displacement: float | None = None
        selected_departure_prefix: dict[str, Any] | None = None
        held_path_assessment: dict[str, Any] | None = None
        placement_assessment: dict[str, Any] | None = None
        robot_clearance_assessment: dict[str, Any] | None = None
        post_placement_continuation_assessment: (
            dict[str, Any] | None
        ) = None

        candidate_queue: list[tuple[bool, Any]] = [
            (False, raw_goal) for raw_goal in candidate_goals
        ]
        source_support_standoff_retry_distance = (
            _source_support_standoff_retry_distance(
                target_kind=str(target.kind),
                explicit_stand_off=params.get("stand_off"),
                source_support_id=source_support_id,
                scene_constants=scene_constants,
            )
        )
        source_support_standoff_retry_attempted = False
        source_support_angular_resampling_attempted = False
        source_support_standoff_retry_audit: dict[str, Any] | None = None
        deferred_held_departures: list[
            tuple[
                tuple[float, float, float],
                float,
                dict[str, Any],
            ]
        ] = []
        while True:
            if not candidate_queue:
                if deferred_held_departures:
                    deferred_held_departures.sort(
                        key=lambda item: _absolute_yaw_delta(
                            item[0][2],
                            start_pose[2],
                        )
                    )
                    candidate_queue.extend(
                        (True, item)
                        for item in deferred_held_departures
                    )
                    deferred_held_departures.clear()
                elif (
                    not source_support_standoff_retry_attempted
                    and source_support_standoff_retry_distance is not None
                    and grasp_candidate_kwargs is not None
                    and _all_grasp_stances_collide_with_source_support(
                        candidate_rejections,
                        source_support_id=source_support_id,
                    )
                ):
                    source_support_standoff_retry_attempted = True
                    retry_distance = (
                        source_support_standoff_retry_distance
                    )
                    retry_kwargs = {
                        **grasp_candidate_kwargs,
                        "distance": retry_distance,
                    }
                    generation_error: dict[str, Any] | None = None
                    try:
                        regenerated_goals = tuple(
                            supervisor
                            .plan_reposition_base_candidates_for_grasp(
                                target.point,
                                str(reference_id),
                                **retry_kwargs,
                            )
                        )
                    except Exception as error:
                        regenerated_goals = ()
                        generation_error = {
                            "code": str(
                                getattr(error, "code", "")
                                or type(error).__name__
                            ),
                            "message": str(
                                getattr(error, "message", "")
                                or str(error)
                            ),
                        }
                    distinct_goals = _distinct_pose_candidates(
                        regenerated_goals,
                        existing=candidate_goals,
                    )
                    source_support_standoff_retry_audit = {
                        "model_version": (
                            SOURCE_SUPPORT_STANDOFF_RETRY_MODEL_VERSION
                        ),
                        "trigger": (
                            "all_default_grasp_stances_collide_with_"
                            "source_support"
                        ),
                        "source_support_id": source_support_id,
                        "distance": retry_distance,
                        "initial_candidate_count": len(candidate_goals),
                        "generated_candidate_count": len(
                            regenerated_goals
                        ),
                        "distinct_candidate_count": len(distinct_goals),
                        **(
                            {"generation_error": generation_error}
                            if generation_error is not None
                            else {}
                        ),
                    }
                    if distinct_goals:
                        candidate_goals = tuple(
                            [*candidate_goals, *distinct_goals]
                        )
                        candidate_queue.extend(
                            (False, raw_goal)
                            for raw_goal in distinct_goals
                        )
                    continue
                elif (
                    source_support_standoff_retry_attempted
                    and not source_support_angular_resampling_attempted
                    and source_support_standoff_retry_distance is not None
                    and source_support_standoff_retry_audit is not None
                    and grasp_candidate_kwargs is not None
                ):
                    source_support_angular_resampling_attempted = True
                    (
                        angular_goals,
                        angular_audit,
                    ) = self._resample_source_support_grasp_stances(
                        control=control,
                        navigation=navigation,
                        supervisor=supervisor,
                        grasp_point=target.point,
                        entity_id=str(reference_id),
                        grasp_candidate_kwargs=grasp_candidate_kwargs,
                        distance=source_support_standoff_retry_distance,
                        start_pose=start_pose,
                        route_obstacles=route_obstacles,
                        endpoint_position_tolerance=(
                            terminal_position_tolerance
                        ),
                        endpoint_yaw_tolerance=terminal_yaw_tolerance,
                        existing_candidates=candidate_goals,
                    )
                    source_support_standoff_retry_audit[
                        "angular_resampling"
                    ] = angular_audit
                    if angular_goals:
                        candidate_goals = tuple(
                            [*candidate_goals, *angular_goals]
                        )
                        candidate_queue.extend(
                            (False, raw_goal)
                            for raw_goal in angular_goals
                        )
                    continue
                else:
                    break

            repair_phase, candidate_input = candidate_queue.pop(0)
            if repair_phase:
                candidate_goal, displacement, route = candidate_input
            else:
                raw_goal = candidate_input
                try:
                    candidate_goal = _pose3(raw_goal)
                except (TypeError, ValueError):
                    candidate_rejections.append(
                        {
                            "goal": _safe_list(raw_goal),
                            "phase": "candidate_generation",
                            "code": "INVALID_REQUEST",
                            "failure_mode": "MALFORMED_BASE_STANCE",
                        }
                    )
                    continue

                displacement = math.hypot(
                    candidate_goal[0] - baseline_pose[0],
                    candidate_goal[1] - baseline_pose[1],
                )
                if displacement + 1e-9 < minimum_reposition_distance:
                    candidate_rejections.append(
                        {
                            "goal": list(candidate_goal),
                            "phase": "semantic_state_change",
                            "code": "INSUFFICIENT_STATE_CHANGE",
                            "baseline_pose": list(baseline_pose),
                            "displacement": displacement,
                            "minimum_reposition_distance": (
                                minimum_reposition_distance
                            ),
                        }
                    )
                    continue

                route = self._plan_route(
                    navigation,
                    start_pose,
                    candidate_goal,
                    obstacles,
                    route_obstacles,
                    endpoint_position_tolerance=(
                        terminal_position_tolerance
                    ),
                    endpoint_yaw_tolerance=terminal_yaw_tolerance,
                    excluded_relocation_entity_ids=(
                        protected_relocation_entity_ids
                    ),
                    verify_relocatable_blockers=(
                        verify_relocatable_route_blockers
                    ),
                )
                route_attempts += 1
            candidate_departure_prefix = None
            candidate_held_path = None
            candidate_placement = None
            candidate_robot_clearance = None
            candidate_continuation = None
            candidate_path = route.get("selected")
            if candidate_path is None:
                recoverable_route_failure = str(
                    route.get("reason_code") or ""
                ) in {
                    "NO_SAFE_DETOUR",
                    "ASTAR_BUDGET_EXHAUSTED",
                }
                if require_held_load and recoverable_route_failure:
                    if not repair_phase:
                        deferred_held_departures.append(
                            (candidate_goal, displacement, route)
                        )
                        continue
                    assert placement is not None
                    repaired = self._repair_held_departure(
                        navigation=navigation,
                        placement=placement,
                        object_id=str(target.placement_object_id),
                        interaction_point=target.point,
                        start_pose=start_pose,
                        candidate_goal=candidate_goal,
                        candidate_path=[start_pose, candidate_goal],
                        obstacles=obstacles,
                        route_obstacles=route_obstacles,
                        endpoint_position_tolerance=(
                            terminal_position_tolerance
                        ),
                        endpoint_yaw_tolerance=(
                            terminal_yaw_tolerance
                        ),
                        target_yaw_source=(
                            "candidate_goal_without_initial_route"
                        ),
                        initial_route=route,
                        excluded_relocation_entity_ids=(
                            protected_relocation_entity_ids
                        ),
                        verify_relocatable_blockers=(
                            verify_relocatable_route_blockers
                        ),
                    )
                    route_attempts += int(
                        repaired.get("route_attempts", 0)
                    )
                    rejection = repaired.get("rejection")
                    if rejection is not None:
                        candidate_rejections.append(rejection)
                        continue
                    candidate_path = repaired["path"]
                    route = repaired["route"]
                    candidate_held_path = repaired[
                        "held_path_assessment"
                    ]
                    candidate_departure_prefix = repaired["prefix"]
                else:
                    candidate_rejections.append(
                        self._route_rejection(candidate_goal, route)
                    )
                    continue
            candidate_execution_goal = _pose3(candidate_path[-1])

            if target.placement_object_id is not None:
                object_id = str(target.placement_object_id)
                destination_id = str(
                    target.destination_id or reference_id
                )
                if require_held_load:
                    assert placement is not None
                    if candidate_held_path is None:
                        candidate_held_path = (
                            placement.assess_held_base_path(
                                scene,
                                object_id,
                                candidate_path,
                                target.point,
                            )
                        )
                    if not candidate_held_path.get("ok"):
                        held_reasons = set(
                            candidate_held_path.get("reason_codes") or ()
                        )
                        if "HOLD_LOST" in held_reasons:
                            candidate_rejections.append(
                                {
                                    "goal": list(candidate_goal),
                                    "phase": "held_precondition",
                                    "code": "GRASP_FAILED",
                                    "failure_mode": (
                                        "EXPECTED_HELD_OBJECT_MISSING"
                                    ),
                                    "assessment": candidate_held_path,
                                }
                            )
                            break
                        if (
                            candidate_departure_prefix is None
                            and _held_collision_before_departure(
                            candidate_held_path,
                            candidate_path,
                            start_pose,
                            )
                        ):
                            repaired = self._repair_held_departure(
                                navigation=navigation,
                                placement=placement,
                                object_id=object_id,
                                interaction_point=target.point,
                                start_pose=start_pose,
                                candidate_goal=candidate_goal,
                                candidate_path=candidate_path,
                                obstacles=obstacles,
                                route_obstacles=route_obstacles,
                                endpoint_position_tolerance=(
                                    terminal_position_tolerance
                                ),
                                endpoint_yaw_tolerance=(
                                    terminal_yaw_tolerance
                                ),
                                initial_route=route,
                                excluded_relocation_entity_ids=(
                                    protected_relocation_entity_ids
                                ),
                                verify_relocatable_blockers=(
                                    verify_relocatable_route_blockers
                                ),
                            )
                            route_attempts += int(
                                repaired.get("route_attempts", 0)
                            )
                            rejection = repaired.get("rejection")
                            if rejection is not None:
                                candidate_rejections.append(rejection)
                                continue
                            candidate_path = repaired["path"]
                            route = repaired["route"]
                            candidate_held_path = repaired[
                                "held_path_assessment"
                            ]
                            candidate_departure_prefix = repaired["prefix"]
                        if not candidate_held_path.get("ok"):
                            candidate_rejections.append(
                                {
                                    "goal": list(candidate_goal),
                                    "phase": (
                                        "combined_held_path"
                                        if candidate_departure_prefix
                                        is not None
                                        else "held_base_path"
                                    ),
                                    "code": "PATH_BLOCKED",
                                    "failure_mode": (
                                        "HELD_PATH_COLLISION_AFTER_DEPARTURE"
                                        if candidate_departure_prefix
                                        is not None
                                        else "HELD_PATH_COLLISION"
                                    ),
                                    "assessment": candidate_held_path,
                                }
                            )
                            continue

                    candidate_placement = (
                        placement.assess_placement_execution_ready(
                            scene,
                            perception,
                            object_id,
                            destination_id,
                            relation=(
                                target.relation
                                or "inside_support_region"
                            ),
                            corner=target.corner,
                            side=target.side or "left",
                            base_pose=candidate_execution_goal,
                            candidate_point=target.placement_point,
                        )
                    )
                    if not candidate_placement.get("ok"):
                        planning_error = (
                            candidate_placement.get("planning_error")
                            or {}
                        )
                        candidate_rejections.append(
                            {
                                "goal": list(candidate_goal),
                                "phase": "placement_approach",
                                "code": str(
                                    planning_error.get("code")
                                    or "NO_REACHABLE_POSE"
                                ),
                                "assessment": candidate_placement,
                            }
                        )
                        continue
                else:
                    assert placement_simulator is not None
                    candidate_robot_clearance = (
                        placement_simulator
                        .assess_robot_placement_clearance(
                            scene,
                            perception,
                            object_id,
                            destination_id,
                            relation=(
                                target.relation
                                or "inside_support_region"
                            ),
                            corner=target.corner,
                            side=target.side or "left",
                            base_pose=candidate_execution_goal,
                            candidate_point=target.placement_point,
                        )
                    )
                    if (
                        not candidate_robot_clearance.get("resolved")
                        or candidate_robot_clearance.get("blocks")
                    ):
                        candidate_rejections.append(
                            {
                                "goal": list(candidate_goal),
                                "phase": "vacate_clearance",
                                "code": "NO_REACHABLE_POSE",
                                "blocking_entity_ids": ["robot_1"],
                                "assessment": (
                                    candidate_robot_clearance
                                ),
                            }
                        )
                        continue

                if continuation_context is not None:
                    if target.placement_point is None:
                        candidate_rejections.append(
                            {
                                "goal": list(candidate_goal),
                                "phase": "post_placement_continuation",
                                "code": "INVALID_REQUEST",
                                "failure_mode": (
                                    "PLACEMENT_POINT_UNAVAILABLE"
                                ),
                            }
                        )
                        continue
                    try:
                        continuation_assessment = (
                            assess_post_placement_continuation(
                                runtime=self.runtime,
                                object_id=object_id,
                                destination_id=destination_id,
                                placement_point=target.placement_point,
                                start_pose=candidate_execution_goal,
                                goal_poses=(
                                    continuation_context.goal_poses
                                ),
                                navigation=navigation,
                                control=control,
                                harness_root=self.harness_root,
                                module_loader=self._module_loader,
                            )
                        )
                    except Exception as error:
                        candidate_rejections.append(
                            {
                                "goal": list(candidate_goal),
                                "phase": "post_placement_continuation",
                                "code": "PERCEPTION_INSUFFICIENT",
                                "failure_mode": (
                                    "CONTINUATION_VALIDATION_UNAVAILABLE"
                                ),
                                "exception_type": type(error).__name__,
                                "message": str(error),
                            }
                        )
                        continue
                    candidate_continuation = (
                        continuation_assessment.to_dict()
                    )
                    if not continuation_assessment.ok:
                        validation_unavailable = (
                            continuation_assessment.failure_mode
                            == "HYPOTHETICAL_OBSTACLE_UNAVAILABLE"
                        )
                        candidate_rejections.append(
                            {
                                "goal": list(candidate_goal),
                                "phase": "post_placement_continuation",
                                "code": (
                                    "PERCEPTION_INSUFFICIENT"
                                    if validation_unavailable
                                    else "INTERACTION_POSE_BLOCKED"
                                ),
                                "failure_mode": (
                                    continuation_assessment.failure_mode
                                    if validation_unavailable
                                    else (
                                        "POST_PLACEMENT_CONTINUATION_"
                                        "UNREACHABLE"
                                    )
                                ),
                                "recovery_kind": (
                                    None
                                    if validation_unavailable
                                    else "replan_placement_layout"
                                ),
                                "blocking_entity_ids": list(
                                    continuation_assessment
                                    .blocking_entity_ids
                                ),
                                "assessment": candidate_continuation,
                            }
                        )
                        continue

            selected_goal = candidate_execution_goal
            selected_path = list(candidate_path)
            selected_route = route
            selected_displacement = math.hypot(
                candidate_execution_goal[0] - baseline_pose[0],
                candidate_execution_goal[1] - baseline_pose[1],
            )
            selected_departure_prefix = candidate_departure_prefix
            held_path_assessment = candidate_held_path
            placement_assessment = candidate_placement
            robot_clearance_assessment = candidate_robot_clearance
            post_placement_continuation_assessment = (
                candidate_continuation
            )
            break

        if (
            selected_goal is None
            or selected_path is None
            or selected_route is None
            or selected_displacement is None
        ):
            return self._failure_from_rejections(
                target=target,
                params=params,
                baseline_pose=baseline_pose,
                minimum_reposition_distance=minimum_reposition_distance,
                candidate_rejections=candidate_rejections,
                route_attempts=route_attempts,
                source_support_standoff_retry=(
                    source_support_standoff_retry_audit
                ),
            )

        obstacle_ids = [str(item.entity_id) for item in obstacles]
        route_obstacle_ids = [
            str(item.entity_id) for item in route_obstacles
        ]
        dependency_ids = list(
            dict.fromkeys(
                [
                    *[str(value) for value in target.entity_ids],
                    *obstacle_ids,
                    *route_obstacle_ids,
                ]
            )
        )
        if target.placement_object_id is not None:
            dependency_ids.extend(
                str(entity_id)
                for entity_id, metadata in perception.catalog.items()
                if isinstance(metadata, Mapping)
                and metadata.get("kind") == "physical_object"
            )
            dependency_ids = list(dict.fromkeys(dependency_ids))

        footprint = selected_route.get("base_footprint")
        if footprint is None:
            footprint = navigation.DEFAULT_BASE_FOOTPRINT.to_dict()
        route_validation = copy.deepcopy(
            selected_route.get("route_validation") or {}
        )
        safety_policy = safety_policy_artifact()
        artifact = {
            "path_schema_version": getattr(
                navigation,
                "BASE_ROUTE_ARTIFACT_SCHEMA_VERSION",
                "detour_path/2.0",
            ),
            "shape": "polyline",
            "navigation_model_version": getattr(
                navigation,
                "NAVIGATION_MODEL_VERSION",
                "",
            ),
            "collision_model_version": HARNESS_SAFETY_MODEL_VERSION,
            "route_validation_model_version": (
                ROUTE_VALIDATION_MODEL_VERSION
            ),
            "route_validation_policy": safety_policy,
            "route_validation_policy_fingerprint": (
                safety_policy["fingerprint"]
            ),
            "route_validation": route_validation,
            "endpoint_clearance_assessment": copy.deepcopy(
                selected_route.get("endpoint_clearance_assessment")
            ),
            "start_egress": copy.deepcopy(
                selected_route.get("start_egress")
            ),
            "target_object_id": str(reference_id),
            "interaction_target_kind": str(target.kind),
            "interaction_target_point": [
                float(value) for value in target.point
            ],
            "source_contact_entity_ids": list(
                grasp_config.source_contact_entity_ids
            ),
            "suction_standoff": float(grasp_config.suction_standoff),
            "poses_world": [
                [float(value) for value in pose]
                for pose in selected_path
            ],
            "waypoints_world": [
                [float(pose[0]), float(pose[1])]
                for pose in selected_path
            ],
            "explicit_execution_route": True,
            "planner": selected_route.get("planner"),
            "planner_model_version": selected_route.get(
                "planner_model_version"
            ),
            "base_footprint": copy.deepcopy(footprint),
            "base_footprint_model_version": selected_route.get(
                "base_footprint_model_version",
                getattr(
                    navigation,
                    "BASE_FOOTPRINT_MODEL_VERSION",
                    "",
                ),
            ),
            "base_footprint_fingerprint": selected_route.get(
                "base_footprint_fingerprint",
                getattr(
                    navigation.DEFAULT_BASE_FOOTPRINT,
                    "fingerprint",
                    "",
                ),
            ),
            "base_footprint_frame": "base_api_yaw_local",
            "footprint_translation_sample": BASE_TRANSLATION_SAMPLE,
            "footprint_rotation_sample": BASE_ROTATION_SAMPLE,
            "se2_footprint_validated": bool(
                route_validation.get("ok")
            ),
            "validated_pose_count": int(
                route_validation.get(
                    "route_pose_count",
                    len(selected_path),
                )
            ),
            "validated_edge_count": max(0, len(selected_path) - 1),
            "validated_dense_pose_count": int(
                route_validation.get("dense_pose_count", 0)
            ),
            "expanded_states": int(
                selected_route.get("expanded_states") or 0
            ),
            "generated_states": int(
                selected_route.get("generated_states") or 0
            ),
            "path_cost": selected_route.get("path_cost"),
            "grid_resolution": float(
                selected_route.get("resolution") or 0.08
            ),
            "heading_bins": int(
                selected_route.get("heading_bins") or 24
            ),
            "start_world": [start_pose[0], start_pose[1]],
            "start_pose_world": list(start_pose),
            "baseline_pose": list(baseline_pose),
            "minimum_reposition_distance": minimum_reposition_distance,
            "planned_reposition_distance": selected_displacement,
            "goal_world": [selected_goal[0], selected_goal[1]],
            "goal_pose_world": list(selected_goal),
            "interaction_reach_margin": float(
                getattr(control, "INTERACTION_REACH_MARGIN", 0.015)
            ),
            "terminal_position_tolerance": (
                terminal_position_tolerance
            ),
            "terminal_yaw_tolerance": terminal_yaw_tolerance,
            "base_path_controller_model_version": str(
                getattr(
                    control,
                    "BASE_PATH_CONTROLLER_MODEL_VERSION",
                    "continuous_pose_tracker/1.0",
                )
            ),
            "base_clearance": float(
                getattr(navigation, "DEFAULT_BASE_CLEARANCE", 0.30)
            ),
            "route_clearance": float(
                getattr(navigation, "DEFAULT_ROUTE_CLEARANCE", 0.0)
            ),
            "obstacle_ids": obstacle_ids,
            "obstacle_revisions": navigation.obstacle_revision_map(
                obstacles
            ),
            "obstacle_geometry_fingerprints": (
                navigation.obstacle_geometry_map(obstacles)
            ),
            "obstacle_interaction_capabilities": {
                str(item.entity_id): dict(item.interaction_capabilities)
                for item in obstacles
            },
            "navigation_obstacle_ids": obstacle_ids,
            "stance_candidate_obstacle_ids": [
                str(item.entity_id) for item in candidate_obstacles
            ],
            "stance_collision_owner": "se2_lattice_astar_route_geometry",
            "excluded_entity_ids": sorted(
                str(value) for value in exclude if value is not None
            ),
            "route_obstacle_ids": route_obstacle_ids,
            "route_obstacle_revisions": (
                navigation.route_obstacle_revision_map(route_obstacles)
            ),
            "route_obstacle_geometry_fingerprints": (
                navigation.route_obstacle_geometry_map(route_obstacles)
            ),
            "route_obstacle_interaction_capabilities": {
                str(item.entity_id): dict(item.interaction_capabilities)
                for item in route_obstacles
            },
            "route_excluded_entity_ids": sorted(
                str(value) for value in route_exclude if value is not None
            ),
            "candidate_rejections": copy.deepcopy(candidate_rejections),
            "candidate_count": len(candidate_goals),
            "route_attempt_count": route_attempts,
            "target_revision": _entity_revision(
                world,
                str(reference_id),
            ),
            "requires_revalidation": True,
        }
        if source_support_standoff_retry_audit is not None:
            artifact["source_support_standoff_retry"] = copy.deepcopy(
                source_support_standoff_retry_audit
            )
        if params.get("grasp_policy") is not None:
            artifact["grasp_policy"] = copy.deepcopy(
                params["grasp_policy"]
            )
        if params.get("source_region_id") is not None:
            artifact["source_region_id"] = copy.deepcopy(
                params["source_region_id"]
            )
        if target.placement_object_id is not None:
            artifact.update(
                {
                    "placement_object_id": str(
                        target.placement_object_id
                    ),
                    "destination_id": (
                        str(target.destination_id)
                        if target.destination_id is not None
                        else None
                    ),
                    "require_held_load": require_held_load,
                    "relation": target.relation,
                    "side": target.side,
                    "corner": target.corner,
                    "placement_target_point": (
                        [
                            float(value)
                            for value in target.placement_point
                        ]
                        if target.placement_point is not None
                        else None
                    ),
                    "target_ref": target.target_ref,
                    "held_departure_prefix": (
                        copy.deepcopy(selected_departure_prefix)
                    ),
                    "posture_transition": copy.deepcopy(
                        (
                            selected_departure_prefix.get(
                                "posture_transition"
                            )
                            if selected_departure_prefix is not None
                            else None
                        )
                    ),
                    "held_path_assessment": copy.deepcopy(
                        held_path_assessment
                    ),
                    "placement_approach_assessment": copy.deepcopy(
                        placement_assessment
                    ),
                    "robot_clearance_assessment": copy.deepcopy(
                        robot_clearance_assessment
                    ),
                    "post_placement_continuation_assessment": (
                        copy.deepcopy(
                            post_placement_continuation_assessment
                        )
                    ),
                }
            )

        result = {
            "kind": "obstacle_map",
            "blocking_entity_ids": list(
                selected_route.get("direct_blocking_entity_ids") or ()
            ),
            "waypoint_count": len(
                {
                    (
                        round(float(pose[0]), 9),
                        round(float(pose[1]), 9),
                    )
                    for pose in selected_path
                }
            ),
            "pose_count": len(selected_path),
            "candidate_count": len(candidate_goals),
            "route_attempt_count": route_attempts,
            "baseline_pose": list(baseline_pose),
            "minimum_reposition_distance": minimum_reposition_distance,
            "planned_reposition_distance": selected_displacement,
            "held_departure_distance": (
                selected_departure_prefix.get("distance")
                if selected_departure_prefix is not None
                else None
            ),
            "message": "interaction route compiled",
        }
        return InteractionRoutePlan(
            artifact=artifact,
            result=result,
            dependency_entity_ids=tuple(dependency_ids),
        )

    def _module(self, name: str) -> Any:
        return self._module_loader(
            name,
            harness_root=self.harness_root,
        )

    def _plan_route(
        self,
        navigation: Any,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        obstacles: Sequence[Any],
        route_obstacles: Sequence[Any],
        *,
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
        excluded_relocation_entity_ids: Sequence[Any] = (),
        verify_relocatable_blockers: bool = False,
    ) -> dict[str, Any]:
        plan = dict(
            navigation.plan_base_route(
                start,
                goal,
                route_obstacles,
                position_ok=self.runtime.scene.base_position_ok,
            )
        )
        start_egress: dict[str, Any] | None = None
        if (
            not plan.get("poses_world")
            and str(plan.get("reason_code") or "")
            == "START_POSE_IN_COLLISION"
        ):
            repaired = self._plan_start_egress(
                navigation=navigation,
                start=start,
                goal=goal,
                route_obstacles=route_obstacles,
            )
            start_egress = repaired["evidence"]
            repaired_plan = repaired.get("plan")
            if isinstance(repaired_plan, Mapping):
                plan = dict(repaired_plan)
                plan["start_egress"] = copy.deepcopy(start_egress)
            else:
                plan["start_egress"] = copy.deepcopy(start_egress)
        poses = [
            _pose3(pose)
            for pose in (plan.get("poses_world") or ())
        ]
        direct_blocking_ids = [
            str(value)
            for value in plan.get("direct_blocking_entity_ids", ())
        ]
        reason_code = plan.get("reason_code")
        failure_mode = {
            "START_POSE_IN_COLLISION": "ROUTE_ENDPOINT_IN_COLLISION",
            "GOAL_POSE_IN_COLLISION": "ROUTE_ENDPOINT_IN_COLLISION",
            "NO_SAFE_DETOUR": "NO_COLLISION_FREE_ROUTE",
            "ASTAR_BUDGET_EXHAUSTED": "PLANNING_BUDGET_EXHAUSTED",
            "START_POSE_INVALID": "BASE_STANCE_OUTSIDE_WORKSPACE",
            "GOAL_POSE_INVALID": "BASE_STANCE_OUTSIDE_WORKSPACE",
        }.get(str(reason_code or ""))
        route_blocking_ids: list[str] = []
        if failure_mode == "ROUTE_ENDPOINT_IN_COLLISION":
            route_blocking_ids.extend(direct_blocking_ids)
        counterfactual_blocker_verification: dict[str, Any] | None = None
        if (
            verify_relocatable_blockers
            and str(reason_code or "") == "NO_SAFE_DETOUR"
        ):
            (
                verified_blocker_ids,
                counterfactual_blocker_verification,
            ) = self._verify_no_safe_detour_blockers(
                navigation=navigation,
                start=start,
                goal=goal,
                obstacles=obstacles,
                route_obstacles=route_obstacles,
                direct_blocking_entity_ids=direct_blocking_ids,
                excluded_entity_ids=excluded_relocation_entity_ids,
                endpoint_position_tolerance=endpoint_position_tolerance,
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
            )
            if verified_blocker_ids:
                route_blocking_ids.extend(verified_blocker_ids)
                failure_mode = "ROUTE_OBSTACLE_BLOCKS_PATH"

        selected: list[tuple[float, float, float]] | None = (
            poses if poses else None
        )
        route_validation: dict[str, Any] | None = None
        endpoint_clearance: dict[str, Any] | None = None
        if selected is not None:
            validation = validate_se2_route(
                navigation=navigation,
                position_ok=self.runtime.scene.base_position_ok,
                poses=selected,
                expected_start=start,
                expected_goal=goal,
                navigation_obstacles=obstacles,
                route_obstacles=route_obstacles,
                endpoint_position_tolerance=(
                    endpoint_position_tolerance
                ),
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
                translation_sample=BASE_TRANSLATION_SAMPLE,
                rotation_sample=BASE_ROTATION_SAMPLE,
            )
            route_validation = validation.to_dict()
            if not validation.ok:
                selected = None
                reason_code = (
                    validation.reason_code or "PATH_BLOCKED"
                )
                failure_mode = (
                    validation.failure_mode
                    or "NO_COLLISION_FREE_ROUTE"
                )
                route_blocking_ids.extend(
                    validation.blocking_entity_ids
                )
        if selected is not None:
            endpoint_clearance = self._assess_endpoint_clearance(
                navigation=navigation,
                goal=goal,
                route_obstacles=route_obstacles,
                endpoint_position_tolerance=(
                    endpoint_position_tolerance
                ),
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
            )
            if not endpoint_clearance.get("ok"):
                selected = None
                reason_code = "GOAL_POSE_UNCERTAIN_COLLISION"
                failure_mode = "EXECUTION_TOLERANCE_COLLISION"
                blocker = endpoint_clearance.get("blocking_entity_id")
                if blocker is not None:
                    route_blocking_ids.append(str(blocker))

        obstacle_by_id = {
            str(item.entity_id): item
            for item in [*route_obstacles, *obstacles]
        }
        route_blocking_ids = list(dict.fromkeys(route_blocking_ids))
        blocker_capabilities = {
            entity_id: dict(
                obstacle_by_id[entity_id].interaction_capabilities
            )
            for entity_id in route_blocking_ids
            if entity_id in obstacle_by_id
        }
        return {
            **plan,
            "selected": selected,
            "reason_code": reason_code,
            "failure_mode": failure_mode,
            "direct_blocking_entity_ids": direct_blocking_ids,
            "route_blocking_entity_ids": route_blocking_ids,
            "route_validation": route_validation,
            "endpoint_clearance_assessment": endpoint_clearance,
            "counterfactual_blocker_verification": copy.deepcopy(
                counterfactual_blocker_verification
            ),
            "verified_minimal_blocker_sets": copy.deepcopy(
                (
                    counterfactual_blocker_verification.get(
                        "verified_minimal_blocker_sets",
                        [],
                    )
                    if counterfactual_blocker_verification is not None
                    else []
                )
            ),
            "selected_minimal_blocker_set": copy.deepcopy(
                (
                    counterfactual_blocker_verification.get(
                        "selected_minimal_blocker_set",
                        [],
                    )
                    if counterfactual_blocker_verification is not None
                    else []
                )
            ),
            "selected_sufficient_blocker_set": copy.deepcopy(
                (
                    counterfactual_blocker_verification.get(
                        "selected_sufficient_blocker_set",
                        [],
                    )
                    if counterfactual_blocker_verification is not None
                    else []
                )
            ),
            "minimality_proven": bool(
                counterfactual_blocker_verification
                and counterfactual_blocker_verification.get(
                    "minimality_proven"
                )
            ),
            "minimal_set_enumeration_complete": bool(
                counterfactual_blocker_verification
                and counterfactual_blocker_verification.get(
                    "minimal_set_enumeration_complete"
                )
            ),
            "start_egress": copy.deepcopy(
                plan.get("start_egress") or start_egress
            ),
            "route_blocker_interaction_capabilities": (
                blocker_capabilities
            ),
        }

    def _verify_no_safe_detour_blockers(
        self,
        *,
        navigation: Any,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        obstacles: Sequence[Any],
        route_obstacles: Sequence[Any],
        direct_blocking_entity_ids: Sequence[Any],
        excluded_entity_ids: Sequence[Any],
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
    ) -> tuple[list[str], dict[str, Any]]:
        direct_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in direct_blocking_entity_ids
                if str(value)
            )
        )
        direct_id_set = set(direct_ids)
        route_obstacle_ids = tuple(
            dict.fromkeys(
                str(getattr(obstacle, "entity_id", ""))
                for obstacle in route_obstacles
                if str(getattr(obstacle, "entity_id", ""))
            )
        )
        candidate_ids = sorted(
            {*direct_ids, *route_obstacle_ids},
            key=lambda entity_id: (
                entity_id not in direct_id_set,
                entity_id,
            ),
        )
        excluded_ids = {
            "robot_1",
            *(
                str(value)
                for value in excluded_entity_ids
                if str(value)
            ),
        }
        world_entities = getattr(
            getattr(self.runtime, "world", None),
            "entities",
            {},
        )
        perception_catalog = getattr(
            getattr(self.runtime, "perception", None),
            "catalog",
            {},
        )
        known_entity_ids = {
            str(value)
            for source in (world_entities, perception_catalog)
            if isinstance(source, Mapping)
            for value in source
        }
        route_obstacle_id_set = set(route_obstacle_ids)
        attempts: list[dict[str, Any]] = []
        eligible_candidate_ids: list[str] = []
        for entity_id in candidate_ids:
            attempt: dict[str, Any] = {
                "entity_id": entity_id,
                "entity_ids": [entity_id],
                "set_size": 1,
                "candidate_source": (
                    "direct_blocking_entity_ids"
                    if entity_id in direct_id_set
                    else "route_obstacles"
                ),
            }
            if entity_id in excluded_ids:
                attempt["outcome"] = "EXCLUDED_ENTITY"
                attempt["conclusive"] = True
                attempts.append(attempt)
                continue
            if entity_id not in known_entity_ids:
                attempt["outcome"] = "UNKNOWN_ENTITY"
                attempt["conclusive"] = True
                attempts.append(attempt)
                continue
            if entity_id not in route_obstacle_id_set:
                attempt["outcome"] = "NOT_IN_ROUTE_OBSTACLE_MAP"
                attempt["conclusive"] = True
                attempts.append(attempt)
                continue
            if not relocatable_by_grasp(self.runtime, entity_id):
                attempt["outcome"] = "NOT_RELOCATABLE_BY_GRASP"
                attempt["conclusive"] = True
                attempts.append(attempt)
                continue
            eligible_candidate_ids.append(entity_id)

        omitted_eligible_candidate_ids = eligible_candidate_ids[
            COUNTERFACTUAL_BLOCKER_MAX_CANDIDATES:
        ]
        searched_candidate_ids = eligible_candidate_ids[
            :COUNTERFACTUAL_BLOCKER_MAX_CANDIDATES
        ]
        sufficient_sets: list[list[str]] = []
        singleton_search_conclusive = True
        search_stopped_after_success = False

        for set_size in range(
            1,
            min(
                COUNTERFACTUAL_BLOCKER_MAX_SET_SIZE,
                len(searched_candidate_ids),
            )
            + 1,
        ):
            entity_id_sets = list(
                combinations(searched_candidate_ids, set_size)
            )
            if set_size == 2:
                singleton_results = {
                    str(attempt.get("entity_id")): attempt
                    for attempt in attempts
                    if int(attempt.get("set_size", 0)) == 1
                    and attempt.get("entity_id")
                }

                def pair_rank(values: tuple[str, ...]) -> tuple[Any, ...]:
                    left, right = values
                    left_direct = set(
                        (
                            singleton_results.get(left, {})
                            .get("planner_result", {})
                            .get("direct_blocking_entity_ids")
                        )
                        or ()
                    )
                    right_direct = set(
                        (
                            singleton_results.get(right, {})
                            .get("planner_result", {})
                            .get("direct_blocking_entity_ids")
                        )
                        or ()
                    )
                    frontier_linked = (
                        right in left_direct or left in right_direct
                    )
                    return (
                        not frontier_linked,
                        -sum(
                            entity_id in direct_id_set
                            for entity_id in values
                        ),
                        tuple(
                            searched_candidate_ids.index(entity_id)
                            for entity_id in values
                        ),
                    )

                entity_id_sets.sort(key=pair_rank)
            for entity_id_set in entity_id_sets:
                attempt = self._counterfactual_blocker_set_attempt(
                    navigation=navigation,
                    start=start,
                    goal=goal,
                    obstacles=obstacles,
                    route_obstacles=route_obstacles,
                    entity_ids=entity_id_set,
                    direct_blocking_entity_ids=direct_ids,
                    endpoint_position_tolerance=endpoint_position_tolerance,
                    endpoint_yaw_tolerance=endpoint_yaw_tolerance,
                )
                attempts.append(attempt)
                if set_size == 1 and not attempt.get("conclusive", False):
                    singleton_search_conclusive = False
                if attempt.get("outcome") != (
                    "ROUTE_RESTORED_AND_VALIDATED"
                ):
                    continue
                sufficient_sets.append(list(entity_id_set))
                if set_size > 1:
                    search_stopped_after_success = True
                    break
            if sufficient_sets:
                break

        selected_sufficient_set = (
            min(
                sufficient_sets,
                key=lambda values: (len(values), tuple(values)),
            )
            if sufficient_sets
            else []
        )
        selected_size = len(selected_sufficient_set)
        minimality_proven = bool(selected_sufficient_set) and (
            selected_size == 1
            or (
                selected_size == 2
                and singleton_search_conclusive
                and not omitted_eligible_candidate_ids
            )
        )
        verified_minimal_sets = (
            sorted(
                sufficient_sets,
                key=lambda values: (len(values), tuple(values)),
            )
            if minimality_proven
            else []
        )
        selected_minimal_set = (
            list(selected_sufficient_set)
            if minimality_proven
            else []
        )
        candidate_rank = {
            entity_id: index
            for index, entity_id in enumerate(candidate_ids)
        }
        singleton_attempts = [
            attempt
            for attempt in attempts
            if int(attempt.get("set_size", 0)) == 1
        ]
        singleton_attempts.sort(
            key=lambda attempt: candidate_rank.get(
                str(attempt.get("entity_id") or ""),
                len(candidate_rank),
            )
        )
        attempts = singleton_attempts + [
            attempt
            for attempt in attempts
            if int(attempt.get("set_size", 0)) != 1
        ]
        return selected_minimal_set, {
            "method": "counterfactual_astar_minimal_blocker_sets",
            "model_version": (
                COUNTERFACTUAL_BLOCKER_VERIFICATION_MODEL_VERSION
            ),
            "original_reason_code": "NO_SAFE_DETOUR",
            "direct_blocking_entity_ids": list(direct_ids),
            "route_obstacle_entity_ids": list(route_obstacle_ids),
            "candidate_entity_ids": candidate_ids,
            "eligible_candidate_ids": list(eligible_candidate_ids),
            "searched_candidate_ids": list(searched_candidate_ids),
            "omitted_eligible_candidate_ids": list(
                omitted_eligible_candidate_ids
            ),
            "excluded_entity_ids": sorted(excluded_ids),
            "max_candidate_count": (
                COUNTERFACTUAL_BLOCKER_MAX_CANDIDATES
            ),
            "max_blocker_set_size": (
                COUNTERFACTUAL_BLOCKER_MAX_SET_SIZE
            ),
            "attempts": attempts,
            "attempt_count": sum(
                1
                for attempt in attempts
                if "planner_result" in attempt
                or attempt.get("outcome")
                == "COUNTERFACTUAL_PLANNER_ERROR"
            ),
            "verified_sufficient_blocker_sets": copy.deepcopy(
                sufficient_sets
            ),
            "selected_sufficient_blocker_set": list(
                selected_sufficient_set
            ),
            "verified_minimal_blocker_sets": copy.deepcopy(
                verified_minimal_sets
            ),
            "selected_minimal_blocker_set": list(selected_minimal_set),
            "minimality_proven": minimality_proven,
            "singleton_search_conclusive": singleton_search_conclusive,
            "candidate_limit_reached": bool(
                omitted_eligible_candidate_ids
            ),
            "minimal_set_enumeration_complete": (
                not search_stopped_after_success
            ),
            "verified_entity_ids": list(selected_minimal_set),
        }

    def _counterfactual_blocker_set_attempt(
        self,
        *,
        navigation: Any,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        obstacles: Sequence[Any],
        route_obstacles: Sequence[Any],
        entity_ids: Sequence[str],
        direct_blocking_entity_ids: Sequence[str],
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
    ) -> dict[str, Any]:
        normalized_ids = tuple(dict.fromkeys(str(value) for value in entity_ids))
        removed_ids = set(normalized_ids)
        attempt: dict[str, Any] = {
            "entity_ids": list(normalized_ids),
            "set_size": len(normalized_ids),
            "candidate_source": (
                "direct_blocking_entity_ids"
                if len(normalized_ids) == 1
                and normalized_ids[0] in set(direct_blocking_entity_ids)
                else "route_obstacles"
                if len(normalized_ids) == 1
                else "route_obstacle_combinations"
            ),
        }
        if len(normalized_ids) == 1:
            attempt["entity_id"] = normalized_ids[0]

        reduced_route_obstacles = tuple(
            obstacle
            for obstacle in route_obstacles
            if str(getattr(obstacle, "entity_id", "")) not in removed_ids
        )
        reduced_obstacles = tuple(
            obstacle
            for obstacle in obstacles
            if str(getattr(obstacle, "entity_id", "")) not in removed_ids
        )
        attempt["removed_route_obstacle_count"] = (
            len(route_obstacles) - len(reduced_route_obstacles)
        )
        attempt["removed_navigation_obstacle_count"] = (
            len(obstacles) - len(reduced_obstacles)
        )
        try:
            restored_plan = dict(
                navigation.plan_base_route(
                    start,
                    goal,
                    reduced_route_obstacles,
                    position_ok=self.runtime.scene.base_position_ok,
                )
            )
        except Exception as error:
            attempt.update(
                {
                    "outcome": "COUNTERFACTUAL_PLANNER_ERROR",
                    "conclusive": False,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                }
            )
            return attempt

        attempt["planner_result"] = {
            key: copy.deepcopy(restored_plan.get(key))
            for key in (
                "planner",
                "planner_model_version",
                "reason_code",
                "direct_blocking_entity_ids",
                "expanded_states",
                "generated_states",
                "path_cost",
            )
        }
        try:
            restored_poses = [
                _pose3(pose)
                for pose in (restored_plan.get("poses_world") or ())
            ]
        except (TypeError, ValueError, OverflowError) as error:
            attempt.update(
                {
                    "outcome": "RESTORED_ROUTE_MALFORMED",
                    "conclusive": False,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                }
            )
            return attempt
        attempt["restored_pose_count"] = len(restored_poses)
        restored_reason = str(restored_plan.get("reason_code") or "")
        if restored_reason:
            attempt["outcome"] = (
                "RESTORED_ROUTE_REPORTED_FAILURE"
                if restored_poses
                else "ROUTE_NOT_RESTORED"
            )
            attempt["conclusive"] = (
                not restored_poses
                and restored_reason != "ASTAR_BUDGET_EXHAUSTED"
            )
            return attempt
        if not restored_poses:
            attempt["outcome"] = "ROUTE_NOT_RESTORED_WITHOUT_REASON"
            attempt["conclusive"] = False
            return attempt

        try:
            validation = validate_se2_route(
                navigation=navigation,
                position_ok=self.runtime.scene.base_position_ok,
                poses=restored_poses,
                expected_start=start,
                expected_goal=goal,
                navigation_obstacles=reduced_obstacles,
                route_obstacles=reduced_route_obstacles,
                endpoint_position_tolerance=endpoint_position_tolerance,
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
                translation_sample=BASE_TRANSLATION_SAMPLE,
                rotation_sample=BASE_ROTATION_SAMPLE,
            )
            attempt["route_validation"] = validation.to_dict()
        except Exception as error:
            attempt.update(
                {
                    "outcome": (
                        "COUNTERFACTUAL_ROUTE_VALIDATION_ERROR"
                    ),
                    "conclusive": False,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                }
            )
            return attempt
        if not validation.ok:
            attempt["outcome"] = "RESTORED_ROUTE_INVALID"
            attempt["conclusive"] = validation.failure_mode not in {
                "ROUTE_ENDPOINT_MISMATCH",
                "EMPTY_ROUTE",
            }
            return attempt

        try:
            endpoint_clearance = self._assess_endpoint_clearance(
                navigation=navigation,
                goal=goal,
                route_obstacles=reduced_route_obstacles,
                endpoint_position_tolerance=endpoint_position_tolerance,
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
            )
            attempt["endpoint_clearance_assessment"] = copy.deepcopy(
                endpoint_clearance
            )
        except Exception as error:
            attempt.update(
                {
                    "outcome": (
                        "COUNTERFACTUAL_ENDPOINT_VALIDATION_ERROR"
                    ),
                    "conclusive": False,
                    "exception_type": type(error).__name__,
                    "message": str(error),
                }
            )
            return attempt
        if endpoint_clearance.get("validated") is not True:
            attempt["outcome"] = (
                "COUNTERFACTUAL_ENDPOINT_VALIDATION_ERROR"
                if endpoint_clearance.get("validation_error")
                else "RESTORED_ENDPOINT_UNVERIFIED"
            )
            attempt["conclusive"] = False
            return attempt
        if not endpoint_clearance.get("ok"):
            attempt["outcome"] = "RESTORED_ENDPOINT_UNSAFE"
            attempt["conclusive"] = True
            return attempt

        attempt["outcome"] = "ROUTE_RESTORED_AND_VALIDATED"
        attempt["conclusive"] = True
        return attempt

    def _plan_start_egress(
        self,
        *,
        navigation: Any,
        start: tuple[float, float, float],
        goal: tuple[float, float, float],
        route_obstacles: Sequence[Any],
    ) -> dict[str, Any]:
        pose_clear = getattr(navigation, "route_pose_clear", None)
        path_clear = getattr(navigation, "route_pose_path_clear", None)
        evidence: dict[str, Any] = {
            "attempted": True,
            "initial_reason_code": "START_POSE_IN_COLLISION",
            "exact_start_clear": None,
            "candidate_count": 0,
            "route_attempt_count": 0,
            "selected_escape_pose": None,
            "candidate_rejections": [],
        }
        if not callable(pose_clear) or not callable(path_clear):
            evidence["failure_mode"] = "EGRESS_VALIDATOR_UNAVAILABLE"
            return {"plan": None, "evidence": evidence}

        try:
            exact_clear, exact_blocker = pose_clear(
                start,
                route_obstacles,
                footprint=navigation.DEFAULT_BASE_FOOTPRINT,
            )
        except Exception as error:
            evidence.update(
                {
                    "failure_mode": "EGRESS_VALIDATION_FAILED",
                    "validation_error": str(error),
                }
            )
            return {"plan": None, "evidence": evidence}
        evidence["exact_start_clear"] = bool(exact_clear)
        evidence["exact_start_blocking_entity_id"] = exact_blocker
        if not exact_clear:
            evidence["failure_mode"] = "EXACT_START_POSE_IN_COLLISION"
            return {"plan": None, "evidence": evidence}

        route_attempts = 0
        rejections: list[dict[str, Any]] = []
        for escape_pose in _start_egress_candidates(start, goal):
            evidence["candidate_count"] += 1
            if not self.runtime.scene.base_position_ok(
                escape_pose[0],
                escape_pose[1],
            ):
                rejections.append(
                    {
                        "escape_pose": list(escape_pose),
                        "reason": "BASE_STANCE_OUTSIDE_WORKSPACE",
                    }
                )
                continue
            prefix = [start, escape_pose]
            prefix_clear, prefix_blocker = path_clear(
                prefix,
                route_obstacles,
                footprint=navigation.DEFAULT_BASE_FOOTPRINT,
                translation_sample=BASE_TRANSLATION_SAMPLE,
                rotation_sample=BASE_ROTATION_SAMPLE,
            )
            if not prefix_clear:
                rejections.append(
                    {
                        "escape_pose": list(escape_pose),
                        "reason": "EGRESS_PREFIX_BLOCKED",
                        "blocking_entity_id": prefix_blocker,
                    }
                )
                continue
            if route_attempts >= START_EGRESS_ROUTE_ATTEMPTS:
                break
            route_attempts += 1
            reroute = dict(
                navigation.plan_base_route(
                    escape_pose,
                    goal,
                    route_obstacles,
                    position_ok=self.runtime.scene.base_position_ok,
                )
            )
            reroute_poses = [
                _pose3(pose)
                for pose in (reroute.get("poses_world") or ())
            ]
            if not reroute_poses:
                rejections.append(
                    {
                        "escape_pose": list(escape_pose),
                        "reason": str(
                            reroute.get("reason_code")
                            or "NO_ROUTE_AFTER_EGRESS"
                        ),
                        "blocking_entity_ids": list(
                            reroute.get(
                                "direct_blocking_entity_ids",
                                (),
                            )
                        ),
                    }
                )
                continue
            combined = _merge_pose_paths(prefix, reroute_poses)
            evidence.update(
                {
                    "route_attempt_count": route_attempts,
                    "selected_escape_pose": list(escape_pose),
                    "distance": math.hypot(
                        escape_pose[0] - start[0],
                        escape_pose[1] - start[1],
                    ),
                    "failure_mode": None,
                    "candidate_rejections": rejections,
                }
            )
            reroute.update(
                {
                    "poses_world": [list(pose) for pose in combined],
                    "selected": [
                        [float(pose[0]), float(pose[1])]
                        for pose in combined
                    ],
                }
            )
            return {"plan": reroute, "evidence": evidence}

        evidence.update(
            {
                "route_attempt_count": route_attempts,
                "failure_mode": "NO_COLLISION_FREE_START_EGRESS",
                "candidate_rejections": rejections,
            }
        )
        return {"plan": None, "evidence": evidence}

    def _resample_source_support_grasp_stances(
        self,
        *,
        control: Any,
        navigation: Any,
        supervisor: Any,
        grasp_point: Sequence[Any],
        entity_id: str,
        grasp_candidate_kwargs: Mapping[str, Any],
        distance: float,
        start_pose: tuple[float, float, float],
        route_obstacles: Sequence[Any],
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
        existing_candidates: Sequence[Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        audit: dict[str, Any] = {
            "model_version": (
                SOURCE_SUPPORT_ANGULAR_RESAMPLING_MODEL_VERSION
            ),
            "trigger": "standoff_retry_exhausted_without_safe_route",
            "distance": float(distance),
            "angle_step_degrees": (
                SOURCE_SUPPORT_ANGULAR_RESAMPLING_STEP_DEGREES
            ),
            "max_candidate_count": (
                SOURCE_SUPPORT_ANGULAR_RESAMPLING_MAX_CANDIDATES
            ),
            "sampled_candidate_count": 0,
            "reach_target_resolved_count": 0,
            "base_position_clear_count": 0,
            "endpoint_clear_candidate_count": 0,
            "endpoint_validation_unavailable_count": 0,
            "endpoint_blocking_entity_ids": [],
            "grasp_input_candidate_count": 0,
            "grasp_validator_invoked": False,
            "grasp_validator_returned_count": 0,
            "grasp_validated_candidate_count": 0,
            "distinct_candidate_count": 0,
        }
        gripper_target_for_grasp = getattr(
            control,
            "gripper_target_for_grasp",
            None,
        )
        base_position_ok = getattr(
            self.runtime.scene,
            "base_position_ok",
            None,
        )
        grasp_validator = getattr(
            supervisor,
            "plan_reposition_base_candidates_for_grasp",
            None,
        )
        missing_validators = [
            name
            for name, value in (
                ("gripper_target_for_grasp", gripper_target_for_grasp),
                ("base_position_ok", base_position_ok),
                (
                    "plan_reposition_base_candidates_for_grasp",
                    grasp_validator,
                ),
            )
            if not callable(value)
        ]
        if missing_validators:
            audit["generation_error"] = {
                "code": "ANGULAR_RESAMPLING_VALIDATOR_UNAVAILABLE",
                "message": (
                    "required angular-resampling validator unavailable: "
                    + ", ".join(missing_validators)
                ),
            }
            return (), audit

        grasp_mode = str(
            grasp_candidate_kwargs.get("grasp_mode", "suction")
        )
        suction_standoff = float(
            grasp_candidate_kwargs.get("suction_standoff", 0.0)
        )
        endpoint_clear: list[
            tuple[
                tuple[float, float, float],
                float,
            ]
        ] = []
        endpoint_blockers: list[str] = []
        candidate_error_count = 0
        first_candidate_error: dict[str, str] | None = None
        angle_step_radians = (
            math.tau
            / SOURCE_SUPPORT_ANGULAR_RESAMPLING_MAX_CANDIDATES
        )
        for index in range(
            SOURCE_SUPPORT_ANGULAR_RESAMPLING_MAX_CANDIDATES
        ):
            outward_angle = index * angle_step_radians
            outward_x = math.cos(outward_angle)
            outward_y = math.sin(outward_angle)
            yaw = math.atan2(-outward_y, -outward_x)
            audit["sampled_candidate_count"] += 1
            try:
                raw_reach_target = gripper_target_for_grasp(
                    self.runtime.scene,
                    grasp_point,
                    grasp_mode,
                    base_yaw=yaw,
                    suction_standoff=suction_standoff,
                )
                reach_target = tuple(
                    float(raw_reach_target[axis])
                    for axis in range(3)
                )
                if not all(
                    math.isfinite(value) for value in reach_target
                ):
                    raise ValueError("non-finite reach target")
            except Exception as error:
                candidate_error_count += 1
                if first_candidate_error is None:
                    first_candidate_error = {
                        "code": type(error).__name__,
                        "message": str(error),
                    }
                continue
            audit["reach_target_resolved_count"] += 1
            candidate = (
                reach_target[0] + float(distance) * outward_x,
                reach_target[1] + float(distance) * outward_y,
                yaw,
            )
            try:
                if not bool(
                    base_position_ok(candidate[0], candidate[1])
                ):
                    continue
            except Exception as error:
                candidate_error_count += 1
                if first_candidate_error is None:
                    first_candidate_error = {
                        "code": type(error).__name__,
                        "message": str(error),
                    }
                continue
            audit["base_position_clear_count"] += 1
            clearance = self._assess_endpoint_clearance(
                navigation=navigation,
                goal=candidate,
                route_obstacles=route_obstacles,
                endpoint_position_tolerance=(
                    endpoint_position_tolerance
                ),
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
            )
            if not clearance.get("validated"):
                audit["endpoint_validation_unavailable_count"] += 1
                continue
            if not clearance.get("ok"):
                blocker = clearance.get("blocking_entity_id")
                if blocker is not None:
                    endpoint_blockers.append(str(blocker))
                continue
            endpoint_clear.append((candidate, outward_angle))

        audit["candidate_generation_error_count"] = candidate_error_count
        if first_candidate_error is not None:
            audit["first_candidate_generation_error"] = (
                first_candidate_error
            )
        audit["endpoint_blocking_entity_ids"] = list(
            dict.fromkeys(endpoint_blockers)
        )
        audit["endpoint_clear_candidate_count"] = len(endpoint_clear)
        endpoint_clear.sort(
            key=lambda item: (
                math.hypot(
                    item[0][0] - start_pose[0],
                    item[0][1] - start_pose[1],
                ),
                _absolute_yaw_delta(item[0][2], start_pose[2]),
                item[1],
            )
        )
        prefiltered_candidates = _distinct_pose_candidates(
            [item[0] for item in endpoint_clear],
            existing=existing_candidates,
        )
        audit["grasp_input_candidate_count"] = len(
            prefiltered_candidates
        )
        if not prefiltered_candidates:
            return (), audit

        generator_name = "plan_reposition_base_candidates"
        instance_attributes = getattr(supervisor, "__dict__", None)
        no_instance_override = object()
        previous_instance_override: Any = no_instance_override
        if isinstance(instance_attributes, dict):
            previous_instance_override = instance_attributes.get(
                generator_name,
                no_instance_override,
            )

        def fixed_candidate_generator(
            *_args: Any,
            **_kwargs: Any,
        ) -> tuple[Any, ...]:
            return tuple(prefiltered_candidates)

        validated_candidates: tuple[Any, ...] = ()
        restoration_error: Exception | None = None
        try:
            setattr(
                supervisor,
                generator_name,
                fixed_candidate_generator,
            )
            audit["grasp_validator_invoked"] = True
            validated_candidates = tuple(
                grasp_validator(
                    grasp_point,
                    str(entity_id),
                    **dict(grasp_candidate_kwargs),
                )
            )
        except Exception as error:
            audit["generation_error"] = {
                "code": str(
                    getattr(error, "code", "")
                    or type(error).__name__
                ),
                "message": str(
                    getattr(error, "message", "")
                    or str(error)
                ),
            }
        finally:
            try:
                if previous_instance_override is no_instance_override:
                    delattr(supervisor, generator_name)
                else:
                    setattr(
                        supervisor,
                        generator_name,
                        previous_instance_override,
                    )
            except Exception as error:
                restoration_error = error

        if restoration_error is not None:
            audit["generation_error"] = {
                "code": "ANGULAR_RESAMPLING_RESTORE_FAILED",
                "message": str(restoration_error),
            }
            return (), audit
        audit["grasp_validator_returned_count"] = len(
            validated_candidates
        )
        supplied_by_pose = {
            tuple(round(value, 9) for value in _pose3(candidate)): (
                candidate
            )
            for candidate in prefiltered_candidates
        }
        validated_supplied: list[Any] = []
        unmatched_count = 0
        for raw_candidate in validated_candidates:
            try:
                key = tuple(
                    round(value, 9)
                    for value in _pose3(raw_candidate)
                )
            except (TypeError, ValueError):
                unmatched_count += 1
                continue
            supplied = supplied_by_pose.get(key)
            if supplied is None:
                unmatched_count += 1
                continue
            validated_supplied.append(supplied)
        distinct_candidates = _distinct_pose_candidates(
            validated_supplied,
            existing=existing_candidates,
        )
        audit["validator_unmatched_candidate_count"] = unmatched_count
        audit["grasp_validated_candidate_count"] = len(
            validated_supplied
        )
        audit["distinct_candidate_count"] = len(distinct_candidates)
        return distinct_candidates, audit

    @staticmethod
    def _assess_endpoint_clearance(
        *,
        navigation: Any,
        goal: tuple[float, float, float],
        route_obstacles: Sequence[Any],
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
    ) -> dict[str, Any]:
        pose_clear = getattr(navigation, "route_pose_clear", None)
        footprint = getattr(navigation, "DEFAULT_BASE_FOOTPRINT", None)
        if (
            not callable(pose_clear)
            or footprint is None
            or not hasattr(footprint, "__dataclass_fields__")
            or not hasattr(footprint, "max_radius")
            or not hasattr(footprint, "safety_margin")
        ):
            return {
                "ok": True,
                "validated": False,
                "reason": "ROBUST_FOOTPRINT_UNAVAILABLE",
            }
        uncertainty_margin = (
            float(endpoint_position_tolerance)
            + float(footprint.max_radius)
            * float(endpoint_yaw_tolerance)
            + ENDPOINT_CLEARANCE_BUFFER
        )
        try:
            robust_footprint = replace(
                footprint,
                safety_margin=(
                    float(footprint.safety_margin)
                    + uncertainty_margin
                ),
            )
            clear, blocker = pose_clear(
                goal,
                route_obstacles,
                footprint=robust_footprint,
            )
        except Exception as error:
            return {
                "ok": False,
                "validated": False,
                "reason": "ROBUST_ENDPOINT_VALIDATION_FAILED",
                "validation_error": str(error),
                "uncertainty_margin": uncertainty_margin,
            }
        return {
            "ok": bool(clear),
            "validated": True,
            "blocking_entity_id": blocker,
            "uncertainty_margin": uncertainty_margin,
            "nominal_safety_margin": float(footprint.safety_margin),
            "robust_safety_margin": float(
                robust_footprint.safety_margin
            ),
            "footprint": robust_footprint.to_dict(),
        }

    @staticmethod
    def _route_rejection(
        candidate_goal: tuple[float, float, float],
        route: Mapping[str, Any],
    ) -> dict[str, Any]:
        selected_minimal_set = list(
            route.get("selected_minimal_blocker_set") or ()
        )
        selected_sufficient_set = list(
            route.get("selected_sufficient_blocker_set") or ()
        )
        selected_counterfactual_set = (
            selected_minimal_set or selected_sufficient_set
        )
        verified_route_blockers = (
            []
            if len(selected_counterfactual_set) > 1
            else list(route.get("route_blocking_entity_ids") or ())
        )
        return {
            "goal": list(candidate_goal),
            "phase": "base_path",
            "code": str(
                route.get("reason_code") or "PATH_BLOCKED"
            ),
            "failure_mode": route.get("failure_mode"),
            "blocking_entity_ids": list(
                route.get("route_blocking_entity_ids") or ()
            ),
            "route_blocker_interaction_capabilities": copy.deepcopy(
                route.get("route_blocker_interaction_capabilities")
                or {}
            ),
            "direct_blocking_entity_ids": list(
                route.get("direct_blocking_entity_ids") or ()
            ),
            "verified_route_blocking_entity_ids": list(
                verified_route_blockers
            )
            if route.get("failure_mode")
            in {
                "ROUTE_OBSTACLE_BLOCKS_PATH",
                "ROUTE_ENDPOINT_IN_COLLISION",
                "EXECUTION_TOLERANCE_COLLISION",
            }
            else [],
            "counterfactual_blocker_verification": copy.deepcopy(
                route.get("counterfactual_blocker_verification")
            ),
            "verified_minimal_blocker_sets": copy.deepcopy(
                route.get("verified_minimal_blocker_sets") or []
            ),
            "selected_minimal_blocker_set": list(
                selected_minimal_set
            ),
            "selected_sufficient_blocker_set": list(
                selected_sufficient_set
            ),
            "minimality_proven": bool(route.get("minimality_proven")),
            "minimal_set_enumeration_complete": bool(
                route.get("minimal_set_enumeration_complete")
            ),
            "planner": route.get("planner"),
            "planner_model_version": route.get(
                "planner_model_version"
            ),
            "expanded_states": route.get("expanded_states"),
            "generated_states": route.get("generated_states"),
            "path_cost": route.get("path_cost"),
            "route_validation": copy.deepcopy(
                route.get("route_validation")
            ),
            "endpoint_clearance_assessment": copy.deepcopy(
                route.get("endpoint_clearance_assessment")
            ),
            "start_egress": copy.deepcopy(
                route.get("start_egress")
            ),
        }

    def _repair_held_departure(
        self,
        *,
        navigation: Any,
        placement: Any,
        object_id: str,
        interaction_point: Sequence[float],
        start_pose: tuple[float, float, float],
        candidate_goal: tuple[float, float, float],
        candidate_path: list[tuple[float, float, float]],
        obstacles: Sequence[Any],
        route_obstacles: Sequence[Any],
        endpoint_position_tolerance: float,
        endpoint_yaw_tolerance: float,
        target_yaw_source: str = (
            "provisional_astar_first_translation"
        ),
        initial_route: Mapping[str, Any] | None = None,
        excluded_relocation_entity_ids: Sequence[Any] = (),
        verify_relocatable_blockers: bool = False,
    ) -> dict[str, Any]:
        departure_yaw = _departure_yaw_for_path(
            candidate_path,
            start_pose,
            candidate_goal,
        )
        initial_route_failure = (
            self._route_rejection(candidate_goal, initial_route)
            if (
                initial_route is not None
                and initial_route.get("selected") is None
            )
            else None
        )
        route_attempts = 0
        minimum_distance = 0.0
        seen_distances: set[float] = set()
        departure_candidate_rejections: list[dict[str, Any]] = []

        while minimum_distance <= HELD_DEPARTURE_MAX_DISTANCE + 1e-9:
            prefix = placement.held_departure_prefix(
                self.runtime.scene,
                object_id,
                departure_yaw,
                minimum_distance=minimum_distance,
            )
            if not prefix.get("ok"):
                assessment = prefix.get("assessment") or prefix
                issue = (
                    assessment.get("issue") or {}
                    if isinstance(assessment, Mapping)
                    else {}
                )
                collision_pair = list(
                    issue.get("collision_pair") or ()
                )
                prefix_rejection = {
                    "goal": list(candidate_goal),
                    "phase": "held_departure_prefix",
                    "code": "PATH_BLOCKED",
                    "failure_mode": (
                        "HELD_DEPARTURE_PREFIX_UNAVAILABLE"
                    ),
                    "object_id": object_id,
                    "start_pose": list(start_pose),
                    "target_yaw": departure_yaw,
                    "target_yaw_source": target_yaw_source,
                    "minimum_distance": minimum_distance,
                    "reason_codes": list(
                        prefix.get("reason_codes") or ()
                    ),
                    "assessment": copy.deepcopy(assessment),
                    "collision_pair": collision_pair,
                    "blocking_entity_ids": collision_pair,
                    **(
                        {
                            "initial_route_failure": (
                                initial_route_failure
                            )
                        }
                        if initial_route_failure is not None
                        else {}
                    ),
                }
                if not departure_candidate_rejections:
                    return {
                        "route_attempts": route_attempts,
                        "rejection": prefix_rejection,
                    }
                rejection = copy.deepcopy(
                    departure_candidate_rejections[-1]
                )
                rejection["departure_candidate_rejections"] = (
                    copy.deepcopy(departure_candidate_rejections)
                )
                rejection["departure_search_exhaustion"] = (
                    prefix_rejection
                )
                return {
                    "route_attempts": route_attempts,
                    "rejection": rejection,
                }

            try:
                escape_pose = _pose3(prefix["escape_pose"])
                prefix_poses = [
                    _pose3(pose)
                    for pose in prefix.get("poses", ())
                ]
                departure_distance = float(prefix["distance"])
            except (KeyError, TypeError, ValueError):
                return {
                    "route_attempts": route_attempts,
                    "rejection": {
                        "goal": list(candidate_goal),
                        "phase": "held_departure_prefix",
                        "code": "INVALID_REQUEST",
                        "failure_mode": (
                            "HELD_DEPARTURE_PREFIX_MALFORMED"
                        ),
                        "assessment": copy.deepcopy(prefix),
                        "departure_candidate_rejections": (
                            copy.deepcopy(
                                departure_candidate_rejections
                            )
                        ),
                        **(
                            {
                                "initial_route_failure": (
                                    initial_route_failure
                                )
                            }
                            if initial_route_failure is not None
                            else {}
                        ),
                    },
                }
            distance_key = round(departure_distance, 9)
            if (
                not math.isfinite(departure_distance)
                or departure_distance + 1e-9 < minimum_distance
                or departure_distance
                > HELD_DEPARTURE_MAX_DISTANCE + 1e-9
                or distance_key in seen_distances
            ):
                return {
                    "route_attempts": route_attempts,
                    "rejection": {
                        "goal": list(candidate_goal),
                        "phase": "held_departure_prefix",
                        "code": "INVALID_REQUEST",
                        "failure_mode": (
                            "HELD_DEPARTURE_PREFIX_MALFORMED"
                        ),
                        "minimum_distance": minimum_distance,
                        "departure_distance": departure_distance,
                        "assessment": copy.deepcopy(prefix),
                        "departure_candidate_rejections": (
                            copy.deepcopy(
                                departure_candidate_rejections
                            )
                        ),
                        **(
                            {
                                "initial_route_failure": (
                                    initial_route_failure
                                )
                            }
                            if initial_route_failure is not None
                            else {}
                        ),
                    },
                }
            seen_distances.add(distance_key)

            rotated_escape = (
                escape_pose[0],
                escape_pose[1],
                departure_yaw,
            )
            prefix_poses.append(rotated_escape)
            reroute = self._plan_route(
                navigation,
                rotated_escape,
                candidate_goal,
                obstacles,
                route_obstacles,
                endpoint_position_tolerance=(
                    endpoint_position_tolerance
                ),
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
                excluded_relocation_entity_ids=(
                    excluded_relocation_entity_ids
                ),
                verify_relocatable_blockers=(
                    verify_relocatable_blockers
                ),
            )
            route_attempts += 1
            reroute_path = reroute.get("selected")
            if reroute_path is None:
                rejection = self._route_rejection(
                    candidate_goal,
                    reroute,
                )
                rejection.update(
                    {
                        "phase": "astar_after_held_departure",
                        "failure_mode": (
                            reroute.get("failure_mode")
                            or "NO_ROUTE_AFTER_HELD_DEPARTURE"
                        ),
                        "escape_pose": list(rotated_escape),
                        "departure_distance": departure_distance,
                        "target_yaw": departure_yaw,
                        "target_yaw_source": target_yaw_source,
                        **(
                            {
                                "initial_route_failure": (
                                    initial_route_failure
                                )
                            }
                            if initial_route_failure is not None
                            else {}
                        ),
                    }
                )
                departure_candidate_rejections.append(rejection)
                minimum_distance = (
                    departure_distance
                    + HELD_DEPARTURE_CANDIDATE_STEP
                )
                continue

            combined = _merge_pose_paths(
                prefix_poses,
                list(reroute_path),
            )
            combined_validation = validate_se2_route(
                navigation=navigation,
                position_ok=self.runtime.scene.base_position_ok,
                poses=combined,
                expected_start=start_pose,
                expected_goal=candidate_goal,
                navigation_obstacles=obstacles,
                route_obstacles=route_obstacles,
                endpoint_position_tolerance=(
                    endpoint_position_tolerance
                ),
                endpoint_yaw_tolerance=endpoint_yaw_tolerance,
                translation_sample=BASE_TRANSLATION_SAMPLE,
                rotation_sample=BASE_ROTATION_SAMPLE,
            )
            if not combined_validation.ok:
                departure_candidate_rejections.append(
                    {
                        "goal": list(candidate_goal),
                        "phase": "combined_held_path",
                        "code": str(
                            combined_validation.reason_code
                            or "PATH_BLOCKED"
                        ),
                        "failure_mode": str(
                            combined_validation.failure_mode
                            or "HELD_DEPARTURE_PATH_BLOCKED"
                        ),
                        "escape_pose": list(rotated_escape),
                        "departure_distance": departure_distance,
                        "target_yaw": departure_yaw,
                        "target_yaw_source": target_yaw_source,
                        "blocking_entity_ids": list(
                            combined_validation.blocking_entity_ids
                        ),
                        "route_validation": (
                            combined_validation.to_dict()
                        ),
                        **(
                            {
                                "initial_route_failure": (
                                    initial_route_failure
                                )
                            }
                            if initial_route_failure is not None
                            else {}
                        ),
                    }
                )
                minimum_distance = (
                    departure_distance
                    + HELD_DEPARTURE_CANDIDATE_STEP
                )
                continue

            held_assessment = placement.assess_held_base_path(
                self.runtime.scene,
                object_id,
                combined,
                interaction_point,
                **_held_posture_assessment_kwargs(prefix),
            )
            if not held_assessment.get("ok"):
                issue = held_assessment.get("issue") or {}
                collision_pair = list(
                    issue.get("collision_pair") or ()
                )
                held_rejection = {
                    "goal": list(candidate_goal),
                    "phase": "combined_held_path",
                    "code": (
                        "GRASP_FAILED"
                        if "HOLD_LOST" in set(
                            held_assessment.get("reason_codes") or ()
                        )
                        else "PATH_BLOCKED"
                    ),
                    "failure_mode": (
                        "EXPECTED_HELD_OBJECT_MISSING"
                        if "HOLD_LOST" in set(
                            held_assessment.get("reason_codes") or ()
                        )
                        else "HELD_PATH_COLLISION_AFTER_DEPARTURE"
                    ),
                    "escape_pose": list(rotated_escape),
                    "departure_distance": departure_distance,
                    "target_yaw": departure_yaw,
                    "target_yaw_source": target_yaw_source,
                    "assessment": copy.deepcopy(held_assessment),
                    "collision_pair": collision_pair,
                    "blocking_entity_ids": collision_pair,
                    **(
                        {
                            "initial_route_failure": (
                                initial_route_failure
                            )
                        }
                        if initial_route_failure is not None
                        else {}
                    ),
                }
                departure_candidate_rejections.append(held_rejection)
                if held_rejection["code"] == "GRASP_FAILED":
                    held_rejection[
                        "departure_candidate_rejections"
                    ] = copy.deepcopy(
                        departure_candidate_rejections
                    )
                    return {
                        "route_attempts": route_attempts,
                        "rejection": held_rejection,
                    }
                minimum_distance = (
                    departure_distance
                    + HELD_DEPARTURE_CANDIDATE_STEP
                )
                continue

            stored_prefix = copy.deepcopy(prefix)
            stored_prefix.update(
                {
                    "target_yaw_source": target_yaw_source,
                    "rotated_escape_pose": list(rotated_escape),
                    "execution_poses": [
                        list(pose) for pose in prefix_poses
                    ],
                    "departure_candidate_rejections": (
                        copy.deepcopy(
                            departure_candidate_rejections
                        )
                    ),
                    "provisional_planner": (
                        initial_route or reroute
                    ).get("planner"),
                    "provisional_planner_model_version": (
                        initial_route or reroute
                    ).get(
                        "planner_model_version"
                    ),
                    "provisional_expanded_states": (
                        initial_route or reroute
                    ).get("expanded_states"),
                    "provisional_generated_states": (
                        initial_route or reroute
                    ).get("generated_states"),
                    **(
                        {
                            "initial_route_failure": (
                                initial_route_failure
                            )
                        }
                        if initial_route_failure is not None
                        else {}
                    ),
                }
            )
            return {
                "route_attempts": route_attempts,
                "path": combined,
                "route": {
                    **reroute,
                    "route_validation": (
                        combined_validation.to_dict()
                    ),
                },
                "held_path_assessment": held_assessment,
                "prefix": stored_prefix,
            }

        if departure_candidate_rejections:
            rejection = copy.deepcopy(
                departure_candidate_rejections[-1]
            )
            rejection["departure_candidate_rejections"] = (
                copy.deepcopy(departure_candidate_rejections)
            )
            rejection["departure_search_exhaustion"] = {
                "reason_codes": [
                    "HELD_DEPARTURE_SEARCH_EXHAUSTED"
                ],
                "minimum_distance": minimum_distance,
                "max_distance": HELD_DEPARTURE_MAX_DISTANCE,
            }
            return {
                "route_attempts": route_attempts,
                "rejection": rejection,
            }
        return {
            "route_attempts": route_attempts,
            "rejection": {
                "goal": list(candidate_goal),
                "phase": "held_departure_prefix",
                "code": "PATH_BLOCKED",
                "failure_mode": "HELD_DEPARTURE_PREFIX_UNAVAILABLE",
                "object_id": object_id,
                "start_pose": list(start_pose),
                "target_yaw": departure_yaw,
                "target_yaw_source": target_yaw_source,
                "reason_codes": [
                    "HELD_DEPARTURE_SEARCH_EXHAUSTED"
                ],
                **(
                    {
                        "initial_route_failure": (
                            initial_route_failure
                        )
                    }
                    if initial_route_failure is not None
                    else {}
                ),
            },
        }

    def _failure_from_error(
        self,
        error: Exception,
        *,
        target: Any,
        params: Mapping[str, Any],
    ) -> InteractionRouteFailure:
        code = str(
            getattr(error, "code", "")
            or "SYSTEM_OPERATION_ERROR"
        )
        message = str(
            getattr(error, "message", "")
            or str(error)
            or "interaction route planning failed"
        )
        details = getattr(error, "details", {})
        details = (
            copy.deepcopy(dict(details))
            if isinstance(details, Mapping)
            else {}
        )
        relocatable_blockers = (
            _relocatable_candidate_collision_entity_ids(
                self.runtime,
                details,
                excluded_entity_ids=getattr(target, "entity_ids", ()),
            )
            if code.upper() == "NO_REACHABLE_POSE"
            and str(getattr(target, "kind", "")).casefold()
            == "entity_grasp"
            else ()
        )
        details.update(
            {
                "raw_failure_code": code,
                "interaction_target": target.descriptor(),
                "params": copy.deepcopy(dict(params)),
            }
        )
        if relocatable_blockers:
            code = "PATH_BLOCKED"
            details.update(
                {
                    "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
                    "blocking_entity_ids": list(relocatable_blockers),
                    "verified_route_blocking_entity_ids": list(
                        relocatable_blockers
                    ),
                    "recovery_kind": "relocate_interaction_blocker",
                }
            )
        return InteractionRouteFailure(
            code=code,
            message=message,
            details=details,
            retryable=bool(getattr(error, "retryable", False)),
        )

    def _failure_from_rejections(
        self,
        *,
        target: Any,
        params: Mapping[str, Any],
        baseline_pose: tuple[float, float, float],
        minimum_reposition_distance: float,
        candidate_rejections: list[dict[str, Any]],
        route_attempts: int,
        source_support_standoff_retry: Mapping[str, Any] | None = None,
    ) -> InteractionRouteFailure:
        saw_grasp_failure = any(
            item.get("code") == "GRASP_FAILED"
            for item in candidate_rejections
        )
        saw_malformed_departure = any(
            item.get("failure_mode")
            == "HELD_DEPARTURE_PREFIX_MALFORMED"
            for item in candidate_rejections
        )
        saw_insufficient_change = any(
            item.get("code") == "INSUFFICIENT_STATE_CHANGE"
            for item in candidate_rejections
        )
        saw_continuation_failure = any(
            item.get("failure_mode")
            == "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
            for item in candidate_rejections
        )
        saw_continuation_validation_unavailable = any(
            item.get("phase") == "post_placement_continuation"
            and item.get("code") == "PERCEPTION_INSUFFICIENT"
            for item in candidate_rejections
        )
        reachability_codes = {
            "START_POSE_INVALID",
            "GOAL_POSE_INVALID",
            "NO_REACHABLE_POSE",
            "ASTAR_BUDGET_EXHAUSTED",
        }
        saw_path_failure = any(
            item.get("phase")
            in {
                "base_path",
                "astar_after_held_departure",
                "held_base_path",
                "held_departure_prefix",
                "combined_held_path",
            }
            and str(item.get("code") or "") not in reachability_codes
            for item in candidate_rejections
        )
        saw_reachability_failure = any(
            str(item.get("code") or "") in reachability_codes
            for item in candidate_rejections
        )
        code = (
            "GRASP_FAILED"
            if saw_grasp_failure
            else "INVALID_REQUEST"
            if saw_malformed_departure
            else "PERCEPTION_INSUFFICIENT"
            if saw_continuation_validation_unavailable
            else "INTERACTION_POSE_BLOCKED"
            if saw_continuation_failure
            else "PATH_BLOCKED"
            if saw_path_failure
            else "INSUFFICIENT_STATE_CHANGE"
            if saw_insufficient_change
            else "NO_REACHABLE_POSE"
        )

        verified_failure_modes = {
            "ROUTE_OBSTACLE_BLOCKS_PATH",
            "ROUTE_ENDPOINT_IN_COLLISION",
            "EXECUTION_TOLERANCE_COLLISION",
        }
        blocker_witnesses: list[dict[str, Any]] = []
        candidate_goal_minimal_sets: list[tuple[str, ...]] = []
        counterfactual_evidence: list[Mapping[str, Any]] = []
        for index, item in enumerate(candidate_rejections):
            evidence = item.get("counterfactual_blocker_verification")
            if isinstance(evidence, Mapping):
                counterfactual_evidence.append(evidence)

            selected_minimal = _normalized_entity_id_tuple(
                item.get("selected_minimal_blocker_set")
            )
            selected_sufficient = _normalized_entity_id_tuple(
                item.get("selected_sufficient_blocker_set")
            )
            selected_counterfactual = (
                selected_minimal or selected_sufficient
            )
            values = (
                item.get("verified_route_blocking_entity_ids")
                if "verified_route_blocking_entity_ids" in item
                else item.get("blocking_entity_ids", ())
            )
            verified_route_set = _normalized_entity_id_tuple(values)
            witness_set = (
                selected_counterfactual
                or (
                    verified_route_set
                    if item.get("failure_mode") in verified_failure_modes
                    else ()
                )
            )
            if witness_set:
                blocker_witnesses.append(
                    {
                        "candidate_index": index,
                        "entity_ids": witness_set,
                        "item": item,
                        "counterfactual": bool(
                            selected_counterfactual
                        ),
                        "local_minimality_proven": bool(
                            selected_minimal
                            and item.get("minimality_proven")
                        ),
                    }
                )
            for raw_set in item.get(
                "verified_minimal_blocker_sets",
                (),
            ):
                normalized_set = _normalized_entity_id_tuple(raw_set)
                if (
                    normalized_set
                    and normalized_set not in candidate_goal_minimal_sets
                ):
                    candidate_goal_minimal_sets.append(normalized_set)

        blocker_witnesses.sort(
            key=lambda witness: (
                len(witness["entity_ids"]),
                not witness["local_minimality_proven"],
                witness["entity_ids"],
                witness["candidate_index"],
            )
        )
        selected_witness = (
            blocker_witnesses[0] if blocker_witnesses else None
        )
        selected_blocker_set = list(
            selected_witness["entity_ids"]
            if selected_witness is not None
            else ()
        )
        selected_is_counterfactual = bool(
            selected_witness
            and selected_witness["counterfactual"]
        )
        selected_local_minimality = bool(
            selected_witness
            and selected_witness["local_minimality_proven"]
        )
        all_goal_singletons_conclusive = all(
            evidence.get("singleton_search_conclusive") is True
            and not evidence.get("candidate_limit_reached")
            for evidence in counterfactual_evidence
        )
        global_minimality_proven = (
            selected_is_counterfactual
            and selected_local_minimality
            and (
                len(selected_blocker_set) == 1
                or (
                    len(selected_blocker_set) == 2
                    and all_goal_singletons_conclusive
                )
            )
        )
        candidate_goal_minimal_sets.sort(
            key=lambda values: (len(values), values)
        )
        verified_minimal_sets = (
            [
                values
                for values in candidate_goal_minimal_sets
                if len(values) == len(selected_blocker_set)
            ]
            if global_minimality_proven
            else []
        )
        selected_minimal_blocker_set = (
            list(selected_blocker_set)
            if global_minimality_proven
            else []
        )
        selected_sufficient_blocker_set = (
            list(selected_blocker_set)
            if selected_is_counterfactual
            else []
        )
        verified_blockers = (
            list(selected_blocker_set)
            if selected_blocker_set
            and (
                not selected_is_counterfactual
                or len(selected_blocker_set) == 1
            )
            else []
        )
        blocking_ids = _blocking_entity_ids(candidate_rejections)
        blocking_ids = list(
            dict.fromkeys([*selected_blocker_set, *blocking_ids])
        )
        target_entity_ids = {
            str(value) for value in getattr(target, "entity_ids", ())
        }
        known_entities = (
            set(getattr(self.runtime.world, "entities", {}))
            | set(getattr(self.runtime.perception, "catalog", {}))
            | {"robot_1"}
        )
        if known_entities:
            verified_blockers = [
                value
                for value in verified_blockers
                if value in known_entities
                and value not in target_entity_ids
            ]
            selected_blocker_set = [
                value
                for value in selected_blocker_set
                if value in known_entities
                and value not in target_entity_ids
            ]
            blocking_ids = [
                value
                for value in blocking_ids
                if value in known_entities
                and value not in target_entity_ids
            ]
        selected_blocker_set_relocatable = (
            bool(selected_blocker_set)
            and all(
                entity_id != "robot_1"
                and relocatable_by_grasp(self.runtime, entity_id)
                for entity_id in selected_blocker_set
            )
        )
        relocation_repair_available = (
            code == "PATH_BLOCKED"
            and str(getattr(target, "kind", "")).casefold()
            == "entity_grasp"
            and selected_blocker_set_relocatable
        )
        failure_mode = (
            "CONTINUATION_VALIDATION_UNAVAILABLE"
            if saw_continuation_validation_unavailable
            else "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
            if saw_continuation_failure
            else "ROUTE_OBSTACLE_BLOCKS_PATH"
            if selected_blocker_set
            else "NO_COLLISION_FREE_ROUTE"
            if saw_path_failure
            else "NO_REACHABLE_BASE_STANCE"
            if saw_reachability_failure
            else None
        )
        placement_object_id = target.placement_object_id
        destination_id = target.destination_id
        cause = (
            "the expected carried object is no longer held"
            if saw_grasp_failure
            else "the held departure path is malformed"
            if saw_malformed_departure
            else (
                "post-placement continuation validation is unavailable"
            )
            if saw_continuation_validation_unavailable
            else (
                "every feasible placement stance would make the original "
                "task continuation unreachable after release"
            )
            if saw_continuation_failure
            else (
                "no candidate base stance changes the failed "
                "interaction state by the required displacement"
            )
            if saw_insufficient_change and not saw_path_failure
            else (
                "no candidate grasp stance is reachable through a "
                "collision-free current-scene route"
            )
            if placement_object_id is None
            else (
                "no candidate base stance has both a collision-free "
                "held path and a feasible placement approach"
            )
        )
        capabilities: dict[str, dict[str, str]] = {}
        selected_item = (
            selected_witness["item"]
            if selected_witness is not None
            else {}
        )
        raw_capabilities = (
            selected_item.get(
                "route_blocker_interaction_capabilities"
            )
            if isinstance(selected_item, Mapping)
            else {}
        )
        if isinstance(raw_capabilities, Mapping):
            for entity_id, values in raw_capabilities.items():
                if (
                    str(entity_id) in selected_blocker_set
                    and isinstance(values, Mapping)
                ):
                    capabilities[str(entity_id)] = {
                        str(key): str(value)
                        for key, value in values.items()
                    }
        raw_route_failure_codes = list(
            dict.fromkeys(
                str(item.get("code"))
                for item in candidate_rejections
                if item.get("phase") == "base_path"
                and item.get("code")
            )
        )
        selected_raw_failure_code = (
            str(selected_item.get("code"))
            if isinstance(selected_item, Mapping)
            and selected_item.get("code")
            else None
        )
        recovery_kind = (
            (
                "relocate_interaction_blocker_set"
                if len(selected_blocker_set) > 1
                else "relocate_interaction_blocker"
            )
            if relocation_repair_available
            else "replan_placement_layout"
            if saw_continuation_failure
            else None
        )

        return InteractionRouteFailure(
            code=code,
            message=cause,
            details={
                "failure_mode": failure_mode,
                "blocking_entity_ids": blocking_ids,
                "params": {
                    **copy.deepcopy(dict(params)),
                    "object_id": (
                        str(placement_object_id)
                        if placement_object_id is not None
                        else str(target.reference_id)
                    ),
                    "target_id": str(target.reference_id),
                    "placement_object_id": (
                        str(placement_object_id)
                        if placement_object_id is not None
                        else None
                    ),
                    "destination_id": (
                        str(destination_id)
                        if destination_id is not None
                        else None
                    ),
                    "placement_destination_id": (
                        str(destination_id)
                        if destination_id is not None
                        else None
                    ),
                    "target_role": (
                        "destination"
                        if placement_object_id is not None
                        else "object"
                    ),
                    "relation": target.relation,
                    "side": target.side,
                    "corner": target.corner,
                    "target_ref": target.target_ref,
                },
                "candidate_count": len(candidate_rejections),
                "route_attempt_count": route_attempts,
                "candidate_rejections": copy.deepcopy(
                    candidate_rejections
                ),
                "verified_route_blocking_entity_ids": (
                    verified_blockers
                ),
                "verified_route_blocker_interaction_capabilities": (
                    capabilities
                ),
                **(
                    {
                        "candidate_goal_minimal_blocker_sets": [
                            list(values)
                            for values in candidate_goal_minimal_sets
                        ],
                        "verified_minimal_blocker_sets": [
                            list(values)
                            for values in verified_minimal_sets
                        ],
                        "selected_minimal_blocker_set": (
                            selected_minimal_blocker_set
                        ),
                        "minimality_proven": bool(
                            global_minimality_proven
                        ),
                        "minimality_scope": (
                            "interaction_candidate_goals"
                        ),
                        "selected_sufficient_blocker_set": (
                            selected_sufficient_blocker_set
                        ),
                        "minimal_set_enumeration_complete": all(
                            evidence.get(
                                "minimal_set_enumeration_complete"
                            )
                            is True
                            for evidence in counterfactual_evidence
                        ),
                        "selected_blocker_set_witness": {
                            "candidate_index": (
                                selected_witness["candidate_index"]
                                if selected_witness is not None
                                else None
                            ),
                            "goal": copy.deepcopy(
                                selected_item.get("goal")
                                if isinstance(selected_item, Mapping)
                                else None
                            ),
                            "code": selected_raw_failure_code,
                            "failure_mode": (
                                selected_item.get("failure_mode")
                                if isinstance(selected_item, Mapping)
                                else None
                            ),
                        },
                    }
                    if counterfactual_evidence
                    else {}
                ),
                **(
                    {
                        "raw_failure_code": selected_raw_failure_code,
                        "raw_failure_codes": raw_route_failure_codes,
                    }
                    if relocation_repair_available
                    and selected_raw_failure_code is not None
                    else {
                        "raw_failure_codes": raw_route_failure_codes,
                    }
                    if relocation_repair_available
                    and raw_route_failure_codes
                    else {}
                ),
                "baseline_pose": list(baseline_pose),
                "minimum_reposition_distance": (
                    minimum_reposition_distance
                ),
                **(
                    {
                        "source_support_standoff_retry": copy.deepcopy(
                            dict(source_support_standoff_retry)
                        )
                    }
                    if source_support_standoff_retry is not None
                    else {}
                ),
                **(
                    {"recovery_kind": recovery_kind}
                    if recovery_kind is not None
                    else {}
                ),
            },
        )


def _source_support_standoff_retry_distance(
    *,
    target_kind: str,
    explicit_stand_off: Any,
    source_support_id: str | None,
    scene_constants: Any,
) -> float | None:
    if (
        str(target_kind).casefold() != "entity_grasp"
        or explicit_stand_off is not None
        or source_support_id is None
    ):
        return None
    try:
        default_distance = float(
            getattr(scene_constants, "APPROACH_DIST", 0.58)
        )
        retry_distance = float(
            getattr(
                scene_constants,
                "PLACE_APPROACH_DIST",
                default_distance,
            )
        )
    except (TypeError, ValueError):
        return None
    if (
        not math.isfinite(default_distance)
        or not math.isfinite(retry_distance)
        or default_distance <= 0.0
        or retry_distance <= default_distance + 1e-9
    ):
        return None
    return retry_distance


def _all_grasp_stances_collide_with_source_support(
    candidate_rejections: Sequence[Mapping[str, Any]],
    *,
    source_support_id: str | None,
) -> bool:
    if source_support_id is None or not candidate_rejections:
        return False
    support_collision_signatures = {
        (
            "GOAL_POSE_IN_COLLISION",
            "ROUTE_ENDPOINT_IN_COLLISION",
        ),
        (
            "GOAL_POSE_UNCERTAIN_COLLISION",
            "EXECUTION_TOLERANCE_COLLISION",
        ),
    }
    return all(
        item.get("phase") == "base_path"
        and (
            str(item.get("code") or ""),
            str(item.get("failure_mode") or ""),
        )
        in support_collision_signatures
        and source_support_id
        in _normalized_entity_id_tuple(
            item.get("direct_blocking_entity_ids")
            or item.get("blocking_entity_ids")
        )
        for item in candidate_rejections
    )


def _distinct_pose_candidates(
    candidates: Sequence[Any],
    *,
    existing: Sequence[Any],
) -> tuple[Any, ...]:
    seen: set[tuple[float, float, float]] = set()
    for value in existing:
        try:
            pose = _pose3(value)
        except (TypeError, ValueError):
            continue
        seen.add(tuple(round(component, 9) for component in pose))

    distinct: list[Any] = []
    for value in candidates:
        try:
            pose = _pose3(value)
        except (TypeError, ValueError):
            distinct.append(value)
            continue
        key = tuple(round(component, 9) for component in pose)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(value)
    return tuple(distinct)


def _normalized_entity_id_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return ()
    return tuple(
        dict.fromkeys(
            str(item) for item in values if str(item)
        )
    )


def _pose3(value: Sequence[Any]) -> tuple[float, float, float]:
    if len(value) < 3:
        raise ValueError("SE(2) pose requires x, y, and yaw")
    pose = (float(value[0]), float(value[1]), float(value[2]))
    if not all(math.isfinite(component) for component in pose):
        raise ValueError("SE(2) pose values must be finite")
    return pose


def _baseline_pose(
    value: Any,
    *,
    fallback: tuple[float, float, float],
) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return fallback
    try:
        result = (
            float(value[0]),
            float(value[1]),
            float(value[2]) if len(value) >= 3 else fallback[2],
        )
    except (TypeError, ValueError):
        return fallback
    return result if all(math.isfinite(item) for item in result) else fallback


def _nonnegative_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return max(0.0, number)


def _positive_float(value: Any, *, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


def _support_of(perception: Any, entity_id: str) -> str | None:
    try:
        value = perception.support_of(str(entity_id))
    except Exception:
        return None
    return str(value) if value is not None else None


def _entity_revision(world: Any, entity_id: str) -> int:
    entity = getattr(world, "entities", {}).get(str(entity_id))
    return int(getattr(entity, "revision", 0))


def _absolute_yaw_delta(yaw: float, start_yaw: float) -> float:
    return abs(
        (
            float(yaw) - float(start_yaw) + math.pi
        ) % (2.0 * math.pi) - math.pi
    )


def _start_egress_candidates(
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    yaw = float(start[2])
    directions = [
        (-math.cos(yaw), -math.sin(yaw)),
        (start[0] - goal[0], start[1] - goal[1]),
        (math.cos(yaw), math.sin(yaw)),
        (-math.sin(yaw), math.cos(yaw)),
        (math.sin(yaw), -math.cos(yaw)),
    ]
    directions.extend(
        (math.cos(index * math.pi / 4.0), math.sin(index * math.pi / 4.0))
        for index in range(8)
    )
    normalized: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for raw_x, raw_y in directions:
        length = math.hypot(raw_x, raw_y)
        if length <= 1e-9:
            continue
        direction = (raw_x / length, raw_y / length)
        key = (
            int(round(direction[0] * 1000.0)),
            int(round(direction[1] * 1000.0)),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(direction)
    return [
        (
            start[0] + distance * direction[0],
            start[1] + distance * direction[1],
            yaw,
        )
        for distance in START_EGRESS_DISTANCES
        for direction in normalized
    ]


def _departure_yaw_for_path(
    path: Sequence[Sequence[float]],
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
) -> float:
    for pose in path[1:]:
        if math.hypot(
            float(pose[0]) - start[0],
            float(pose[1]) - start[1],
        ) > 1e-6:
            return float(pose[2])
    for pose in path[1:]:
        delta = (
            float(pose[2]) - start[2] + math.pi
        ) % (2.0 * math.pi) - math.pi
        if abs(delta) > 1e-6:
            return float(pose[2])
    return float(goal[2])


def _held_collision_before_departure(
    assessment: Mapping[str, Any],
    path: Sequence[Sequence[float]],
    start: tuple[float, float, float],
) -> bool:
    if "HELD_PATH_COLLISION" not in set(
        assessment.get("reason_codes") or ()
    ):
        return False
    if str(assessment.get("phase") or "") not in {
        "pose_rotation",
        "segment_rotation",
    }:
        return False
    try:
        collision_index = int(assessment["segment_index"])
    except (KeyError, TypeError, ValueError):
        return False
    first_translation = next(
        (
            index
            for index, pose in enumerate(path)
            if math.hypot(
                float(pose[0]) - start[0],
                float(pose[1]) - start[1],
            )
            > 1e-6
        ),
        len(path),
    )
    return collision_index <= first_translation


def _held_posture_assessment_kwargs(
    prefix: Mapping[str, Any],
) -> dict[str, Any]:
    transition = prefix.get("posture_transition")
    if not isinstance(transition, Mapping):
        return {}
    target = transition.get("target_configuration")
    if not isinstance(target, Mapping):
        return {}
    try:
        configuration = {
            name: float(target[name])
            for name in ("lift", "arm_extend", "wrist_yaw")
        }
    except (KeyError, TypeError, ValueError):
        return {}
    if not all(math.isfinite(value) for value in configuration.values()):
        return {}
    return {"arm_configuration": configuration}


def _merge_pose_paths(
    *paths: Sequence[Sequence[float]],
) -> list[tuple[float, float, float]]:
    merged: list[tuple[float, float, float]] = []
    for path in paths:
        for raw_pose in path:
            pose = _pose3(raw_pose)
            if merged:
                previous = merged[-1]
                same_xy = math.hypot(
                    pose[0] - previous[0],
                    pose[1] - previous[1],
                ) <= 1e-9
                yaw_delta = (
                    pose[2] - previous[2] + math.pi
                ) % (2.0 * math.pi) - math.pi
                if same_xy and abs(yaw_delta) <= 1e-9:
                    continue
            merged.append(pose)
    return merged


def _blocking_entity_ids(
    rejections: Sequence[Mapping[str, Any]],
) -> list[str]:
    values: list[str] = []
    for item in rejections:
        values.extend(
            str(value)
            for value in item.get("blocking_entity_ids", ())
        )
        assessment = item.get("assessment")
        if not isinstance(assessment, Mapping):
            continue
        issue = assessment.get("issue")
        if isinstance(issue, Mapping):
            values.extend(
                str(value)
                for value in issue.get("collision_pair", ())
            )
        planning_error = assessment.get("planning_error")
        if isinstance(planning_error, Mapping):
            values.extend(
                str(value)
                for value in planning_error.get(
                    "collision_pair",
                    (),
                )
            )
    normalized = {
        "base_link": "robot_1",
        "base_link_collision": "robot_1",
        "link_mast": "robot_1",
    }
    return list(
        dict.fromkeys(normalized.get(value, value) for value in values)
    )


def _relocatable_candidate_collision_entity_ids(
    runtime: Any,
    details: Mapping[str, Any],
    *,
    excluded_entity_ids: Sequence[Any],
) -> tuple[str, ...]:
    raw_failures = details.get("candidate_failures")
    if isinstance(raw_failures, Mapping):
        failures = (raw_failures,)
    elif isinstance(raw_failures, (list, tuple)):
        failures = tuple(
            value for value in raw_failures if isinstance(value, Mapping)
        )
    else:
        failures = ()

    world_entities = getattr(getattr(runtime, "world", None), "entities", {})
    perception_catalog = getattr(
        getattr(runtime, "perception", None),
        "catalog",
        {},
    )
    known_entity_ids = {
        str(value)
        for source in (world_entities, perception_catalog)
        if isinstance(source, Mapping)
        for value in source
    }
    excluded = {
        "robot_1",
        *(str(value) for value in excluded_entity_ids),
    }
    candidates: list[str] = []
    for failure in failures:
        pair = failure.get("collision_pair")
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        candidates.extend(
            value
            for value in pair
            if isinstance(value, str) and value
        )
    return tuple(
        entity_id
        for entity_id in dict.fromkeys(candidates)
        if entity_id in known_entity_ids
        and entity_id not in excluded
        and relocatable_by_grasp(runtime, entity_id)
    )


def _safe_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _entity_id_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_values = (
        value
        if isinstance(value, (list, tuple, set, frozenset))
        else (value,)
    )
    return tuple(
        dict.fromkeys(
            str(item)
            for item in raw_values
            if item is not None and str(item)
        )
    )


__all__ = [
    "HarnessInteractionRoutePlanner",
    "InteractionRouteFailure",
    "InteractionRoutePlan",
]
