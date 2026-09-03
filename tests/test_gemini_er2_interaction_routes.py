from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2 import interaction_routes
from task_recursive_tree.integrations.gemini_er2.harness_safety import (
    BASE_ROTATION_SAMPLE,
    BASE_TRANSLATION_SAMPLE,
)
from task_recursive_tree.integrations.gemini_er2.interaction_routes import (
    HarnessInteractionRoutePlanner,
    InteractionRouteFailure,
    InteractionRoutePlan,
)


@dataclass
class FakeObstacle:
    entity_id: str
    entity_revision: int = 1
    geometry_fingerprint: str = "geometry"
    interaction_capabilities: dict[str, str] = field(
        default_factory=lambda: {
            "grasp": "unavailable",
            "push": "unavailable",
        }
    )


@dataclass
class FakeTarget:
    kind: str
    reference_id: str
    point: tuple[float, float, float]
    placement_object_id: str | None = None
    destination_id: str | None = None
    placement_point: tuple[float, float, float] | None = None
    target_ref: str | None = None
    relation: str | None = None
    side: str | None = None
    corner: str | None = None

    @property
    def entity_ids(self) -> list[str]:
        return list(
            dict.fromkeys(
                value
                for value in (
                    self.reference_id,
                    self.placement_object_id,
                    self.destination_id,
                )
                if value is not None
            )
        )

    def descriptor(self) -> dict:
        return {
            "kind": self.kind,
            "reference_id": self.reference_id,
            "point": list(self.point),
        }


class FakeFootprint:
    model_version = "footprint/1"
    fingerprint = "footprint-fingerprint"

    @staticmethod
    def to_dict() -> dict:
        return {
            "model_version": "footprint/1",
            "fingerprint": "footprint-fingerprint",
        }


@dataclass(frozen=True)
class RobustFootprint:
    safety_margin: float = 0.015
    max_radius: float = 0.30
    model_version: str = "robust-footprint/1"
    fingerprint: str = "robust-footprint-fingerprint"

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "safety_margin": self.safety_margin,
            "fingerprint": self.fingerprint,
        }


class FakeScene:
    def __init__(self) -> None:
        self.position_checks: list[tuple[float, float]] = []

    @staticmethod
    def base_pose():
        return ((-1.0, 0.0, 0.0), 0.0)

    def base_position_ok(self, x: float, y: float) -> bool:
        self.position_checks.append((float(x), float(y)))
        return True


class FakePerception:
    def __init__(self) -> None:
        self.catalog = {
            "apple_1": {
                "kind": "physical_object",
                "geometry": {"grasp_mode": "suction"},
            },
            "plate_1": {
                "kind": "physical_object",
                "geometry": {},
            },
            "table_1": {
                "kind": "physical_object",
                "geometry": {},
            },
        }

    @staticmethod
    def support_of(entity_id: str) -> str | None:
        return "table_1" if entity_id in {"apple_1", "plate_1"} else None


class FakeNavigation:
    BASE_ROUTE_ARTIFACT_SCHEMA_VERSION = "detour_path/2.0"
    NAVIGATION_MODEL_VERSION = "navigation/1"
    BASE_FOOTPRINT_MODEL_VERSION = "footprint/1"
    DEFAULT_BASE_FOOTPRINT = FakeFootprint()
    DEFAULT_FOOTPRINT_TRANSLATION_SAMPLE = 0.02
    DEFAULT_FOOTPRINT_ROTATION_SAMPLE = 0.03
    DEFAULT_BASE_CLEARANCE = 0.30
    DEFAULT_ROUTE_CLEARANCE = 0.0

    def __init__(
        self,
        *,
        fail_all: bool = False,
        navigation_obstacles: list[FakeObstacle] | None = None,
    ) -> None:
        self.fail_all = fail_all
        self.calls: list[dict] = []
        self.route_checks: list[dict] = []
        self.navigation_exclusions: list[set[str]] = []
        self.route_exclusions: list[set[str]] = []
        self.route_obstacle = FakeObstacle("table_1")
        self.navigation_obstacles = list(navigation_obstacles or ())

    def build_navigation_obstacles(self, *_args, **kwargs):
        self.navigation_exclusions.append(set(
            kwargs.get("exclude_entity_ids") or ()
        ))
        return list(self.navigation_obstacles)

    def build_base_route_obstacles(self, *_args, **kwargs):
        self.route_exclusions.append(set(
            kwargs.get("exclude_entity_ids") or ()
        ))
        return [self.route_obstacle]

    def plan_base_route(
        self,
        start,
        goal,
        obstacles,
        *,
        position_ok=None,
    ):
        self.calls.append(
            {
                "start": tuple(start),
                "goal": tuple(goal),
                "obstacles": list(obstacles),
                "position_ok": position_ok,
            }
        )
        blocked = self.fail_all or float(goal[0]) == 0.0
        return {
            "selected": None if blocked else [start[:2], goal[:2]],
            "poses_world": [] if blocked else [list(start), list(goal)],
            "planner": "se2_lattice_astar",
            "planner_model_version": "astar/1",
            "reason_code": (
                "GOAL_POSE_IN_COLLISION" if blocked else None
            ),
            "message": "blocked" if blocked else "planned",
            "direct_blocking_entity_ids": (
                ["table_1"] if blocked else []
            ),
            "expanded_states": 0 if blocked else 3,
            "generated_states": 0 if blocked else 5,
            "path_cost": None if blocked else 2.0,
            "resolution": 0.08,
            "heading_bins": 24,
            "base_footprint": self.DEFAULT_BASE_FOOTPRINT.to_dict(),
            "base_footprint_model_version": "footprint/1",
            "base_footprint_fingerprint": "footprint-fingerprint",
        }

    def route_pose_path_clear(self, *_args, **kwargs):
        self.route_checks.append(dict(kwargs))
        return True, None

    @staticmethod
    def residual_navigation_obstacles(
        navigation_obstacles,
        route_obstacles,
    ):
        modeled = {item.entity_id for item in route_obstacles}
        return [
            item
            for item in navigation_obstacles
            if item.entity_id not in modeled
        ]

    @staticmethod
    def path_clear(*_args, **_kwargs):
        return True, None

    @staticmethod
    def obstacle_revision_map(_obstacles):
        return {}

    @staticmethod
    def obstacle_geometry_map(_obstacles):
        return {}

    @staticmethod
    def route_obstacle_revision_map(obstacles):
        return {
            item.entity_id: item.entity_revision
            for item in obstacles
        }

    @staticmethod
    def route_obstacle_geometry_map(obstacles):
        return {
            item.entity_id: item.geometry_fingerprint
            for item in obstacles
        }


class NoSafeDetourNavigation(FakeNavigation):
    DEFAULT_BASE_FOOTPRINT = RobustFootprint()

    def __init__(
        self,
        *,
        candidate_id: str,
        route_obstacle_ids: tuple[str, ...],
        direct_blocking_entity_ids: tuple[str, ...],
        restored_route: str = "valid",
        relocatable_ids: tuple[str, ...] | None = None,
        restoring_sets: tuple[tuple[str, ...], ...] | None = None,
        navigation_obstacle_ids: tuple[str, ...] = (),
    ) -> None:
        self.relocatable_ids = set(relocatable_ids or (candidate_id,))
        super().__init__(
            navigation_obstacles=[
                FakeObstacle(entity_id)
                for entity_id in navigation_obstacle_ids
            ]
        )
        self.candidate_id = candidate_id
        self.restoring_sets = tuple(
            frozenset(values)
            for values in (
                restoring_sets
                if restoring_sets is not None
                else ((candidate_id,),)
            )
        )
        self.route_obstacles = [
            FakeObstacle(
                entity_id,
                interaction_capabilities=(
                    {"grasp": "available", "push": "unavailable"}
                    if entity_id in self.relocatable_ids
                    else {
                        "grasp": "unavailable",
                        "push": "unavailable",
                    }
                ),
            )
            for entity_id in route_obstacle_ids
        ]
        self.direct_blocking_entity_ids = list(
            direct_blocking_entity_ids
        )
        self.restored_route = restored_route

    def build_base_route_obstacles(self, *_args, **kwargs):
        self.route_exclusions.append(set(
            kwargs.get("exclude_entity_ids") or ()
        ))
        return list(self.route_obstacles)

    def plan_base_route(
        self,
        start,
        goal,
        obstacles,
        *,
        position_ok=None,
    ):
        obstacle_ids = [
            str(obstacle.entity_id) for obstacle in obstacles
        ]
        self.calls.append(
            {
                "start": tuple(start),
                "goal": tuple(goal),
                "obstacles": list(obstacles),
                "position_ok": position_ok,
            }
        )
        route_restored = (
            any(
                restoring_set.isdisjoint(obstacle_ids)
                for restoring_set in self.restoring_sets
            )
            and self.restored_route != "missing"
        )
        poses = [list(start), list(goal)] if route_restored else []
        if route_restored and self.restored_route == "wrong_endpoint":
            poses[-1][0] += 0.1
        if route_restored and self.restored_route == "malformed":
            poses = [["not-a-pose"]]
        return {
            "selected": (
                [start[:2], goal[:2]] if route_restored else None
            ),
            "poses_world": poses,
            "planner": "se2_lattice_astar",
            "planner_model_version": "astar/1",
            "reason_code": None if route_restored else "NO_SAFE_DETOUR",
            "message": "planned" if route_restored else "blocked",
            "direct_blocking_entity_ids": (
                [] if route_restored
                else list(self.direct_blocking_entity_ids)
            ),
            "expanded_states": 3 if route_restored else 52,
            "generated_states": 5 if route_restored else 52,
            "path_cost": 2.0 if route_restored else None,
            "resolution": 0.08,
            "heading_bins": 24,
            "base_footprint": self.DEFAULT_BASE_FOOTPRINT.to_dict(),
            "base_footprint_model_version": "footprint/1",
            "base_footprint_fingerprint": "footprint-fingerprint",
        }

    @staticmethod
    def route_pose_clear(_pose, _obstacles, *, footprint):
        assert footprint.safety_margin >= 0.015
        return True, None


class FakeSupervisor:
    calls: list[dict] = []

    def __init__(self, scene) -> None:
        self.scene = scene

    def plan_reposition_base_candidates_for_grasp(
        self,
        target,
        entity_id,
        **kwargs,
    ):
        self.calls.append(
            {
                "kind": "grasp",
                "target": tuple(target),
                "entity_id": entity_id,
                **kwargs,
            }
        )
        return (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        )

    def plan_reposition_base_candidates(self, target, **kwargs):
        self.calls.append(
            {
                "kind": "placement",
                "target": tuple(target),
                **kwargs,
            }
        )
        return ((1.0, 0.0, 0.0),)


def make_runtime():
    return SimpleNamespace(
        scene=FakeScene(),
        perception=FakePerception(),
        world=SimpleNamespace(
            revision=7,
            entities={
                "apple_1": SimpleNamespace(revision=2),
                "plate_1": SimpleNamespace(revision=3),
                "table_1": SimpleNamespace(revision=4),
            },
        ),
    )


def add_relocatable_entities(runtime, *entity_ids: str) -> None:
    for index, entity_id in enumerate(entity_ids, start=10):
        runtime.perception.catalog.setdefault(
            entity_id,
            {"kind": "physical_object"},
        ).update(
            {
                "movable": True,
                "interaction_capabilities": {"grasp": "available"},
            }
        )
        runtime.world.entities[entity_id] = SimpleNamespace(revision=index)


def make_loader(
    *,
    target: FakeTarget,
    navigation: FakeNavigation,
    placement=None,
    supervisor_type=FakeSupervisor,
    control_overrides: dict | None = None,
):
    targets = SimpleNamespace(
        resolve_interaction_target=(
            lambda _runtime, _reference_id, params: target
        ),
        resolve_grasp_execution_config=(
            lambda _perception, _reference_id, _params: SimpleNamespace(
                source_contact_entity_ids=(),
                suction_standoff=0.0,
            )
        ),
    )
    control_values = {
        "MotionSupervisor": supervisor_type,
        "INTERACTION_REACH_MARGIN": 0.015,
        "INTERACTION_PATH_POSITION_TOLERANCE": 0.008,
        "INTERACTION_PATH_YAW_TOLERANCE": 0.02,
    }
    control_values.update(control_overrides or {})
    modules = {
        "er2sim.interaction_targets": targets,
        "er2sim.navigation_capabilities": navigation,
        "er2sim.control": SimpleNamespace(**control_values),
        "er2sim.scene": SimpleNamespace(PLACE_APPROACH_DIST=0.62),
        "er2sim.placement_capabilities": placement,
        "er2sim.placement_simulator": SimpleNamespace(
            assess_robot_placement_clearance=lambda *_args, **_kwargs: {
                "resolved": True,
                "blocks": False,
            }
        ),
    }
    return lambda name, **_kwargs: modules[name]


def test_planner_skips_colliding_stance_and_selects_next_candidate() -> None:
    FakeSupervisor.calls.clear()
    navigation = FakeNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan(
        "apple_1",
        {"participants": {"reference": ["apple_1"]}},
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert decision.artifact["goal_pose_world"] == [1.0, 0.0, 0.0]
    assert decision.artifact["candidate_rejections"][0]["code"] == \
        "GOAL_POSE_IN_COLLISION"
    assert decision.artifact["candidate_rejections"][0][
        "blocking_entity_ids"
    ] == ["table_1"]
    assert len(navigation.calls) == 2
    assert all(call["position_ok"] is not None for call in navigation.calls)
    assert decision.artifact["se2_footprint_validated"] is True
    assert navigation.route_checks == [
        {
            "footprint": navigation.DEFAULT_BASE_FOOTPRINT,
            "translation_sample": BASE_TRANSLATION_SAMPLE,
            "rotation_sample": BASE_ROTATION_SAMPLE,
        }
    ]
    assert decision.artifact["footprint_translation_sample"] == \
        BASE_TRANSLATION_SAMPLE
    assert decision.artifact["footprint_rotation_sample"] == \
        BASE_ROTATION_SAMPLE
    assert decision.artifact[
        "route_validation_policy_fingerprint"
    ]
    assert navigation.route_exclusions == [set()]
    assert decision.artifact["route_excluded_entity_ids"] == []


def test_planner_retries_source_support_collision_with_larger_standoff(
) -> None:
    class SourceSupportRetrySupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            if kwargs.get("distance") == pytest.approx(0.62):
                return ((1.0, 0.0, 0.0),)
            return ((0.0, 0.0, 0.0),)

    SourceSupportRetrySupervisor.calls.clear()
    navigation = FakeNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            supervisor_type=SourceSupportRetrySupervisor,
        ),
    )

    decision = planner.plan(
        "apple_1",
        {
            "grasp_policy": "confined_source_region",
            "source_region_id": "table_1",
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert decision.artifact["goal_pose_world"] == [1.0, 0.0, 0.0]
    assert len(SourceSupportRetrySupervisor.calls) == 2
    assert "distance" not in SourceSupportRetrySupervisor.calls[0]
    assert SourceSupportRetrySupervisor.calls[1]["distance"] == \
        pytest.approx(0.62)
    assert decision.artifact["candidate_count"] == 2
    assert decision.artifact["route_attempt_count"] == 2
    assert decision.artifact["candidate_rejections"][0]["code"] == \
        "GOAL_POSE_IN_COLLISION"
    assert decision.artifact["route_excluded_entity_ids"] == []
    assert navigation.route_exclusions == [set()]
    retry = decision.artifact["source_support_standoff_retry"]
    assert retry["source_support_id"] == "table_1"
    assert retry["initial_candidate_count"] == 1
    assert retry["generated_candidate_count"] == 1
    assert retry["distinct_candidate_count"] == 1


def test_planner_retries_source_support_execution_tolerance_collision(
) -> None:
    class SourceSupportRetrySupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            if kwargs.get("distance") == pytest.approx(0.62):
                return ((2.0, 0.0, 0.0),)
            return ((1.0, 0.0, 0.0),)

    class ExecutionToleranceNavigation(FakeNavigation):
        DEFAULT_BASE_FOOTPRINT = RobustFootprint()

        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            return {
                "selected": [start[:2], goal[:2]],
                "poses_world": [list(start), list(goal)],
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": None,
                "message": "planned",
                "direct_blocking_entity_ids": [],
                "expanded_states": 3,
                "generated_states": 5,
                "path_cost": 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": self.DEFAULT_BASE_FOOTPRINT.to_dict(),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": "footprint-fingerprint",
            }

        @staticmethod
        def route_pose_clear(pose, _obstacles, *, footprint):
            if float(pose[0]) == 1.0 and footprint.safety_margin > 0.015:
                return False, "table_1"
            return True, None

    SourceSupportRetrySupervisor.calls.clear()
    navigation = ExecutionToleranceNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            supervisor_type=SourceSupportRetrySupervisor,
        ),
    )

    decision = planner.plan(
        "apple_1",
        {
            "grasp_policy": "confined_source_region",
            "source_region_id": "table_1",
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert decision.artifact["goal_pose_world"] == [2.0, 0.0, 0.0]
    assert len(SourceSupportRetrySupervisor.calls) == 2
    assert "distance" not in SourceSupportRetrySupervisor.calls[0]
    assert SourceSupportRetrySupervisor.calls[1]["distance"] == \
        pytest.approx(0.62)
    rejection = decision.artifact["candidate_rejections"][0]
    assert rejection["code"] == "GOAL_POSE_UNCERTAIN_COLLISION"
    assert rejection["failure_mode"] == "EXECUTION_TOLERANCE_COLLISION"
    assert rejection["blocking_entity_ids"] == ["table_1"]
    assert decision.artifact["source_support_standoff_retry"][
        "source_support_id"
    ] == "table_1"


def test_planner_resamples_grasp_angles_after_standoff_retry_exhausted(
) -> None:
    class AngularResamplingSupervisor(FakeSupervisor):
        angular_validation_inputs: list[
            tuple[tuple[float, float, float], ...]
        ] = []

        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            if len(self.calls) == 1:
                return ((0.0, 0.0, 0.0),)
            if len(self.calls) == 2:
                return ((0.1, 0.0, 0.0),)
            candidates = tuple(
                self.plan_reposition_base_candidates(
                    target,
                    distance=kwargs.get("distance"),
                    obstacle_discs=kwargs.get("obstacle_discs", ()),
                )
            )
            self.angular_validation_inputs.append(candidates)
            return candidates

    class AngularResamplingNavigation(FakeNavigation):
        DEFAULT_BASE_FOOTPRINT = RobustFootprint()

        def __init__(self) -> None:
            super().__init__()
            self.endpoint_checks: list[
                tuple[tuple[float, float, float], float]
            ] = []

        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            blocked = float(goal[1]) <= 0.1
            return {
                "selected": (
                    None if blocked else [start[:2], goal[:2]]
                ),
                "poses_world": (
                    [] if blocked else [list(start), list(goal)]
                ),
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": (
                    "GOAL_POSE_IN_COLLISION" if blocked else None
                ),
                "message": "blocked" if blocked else "planned",
                "direct_blocking_entity_ids": (
                    ["table_1"] if blocked else []
                ),
                "expanded_states": 0 if blocked else 3,
                "generated_states": 0 if blocked else 5,
                "path_cost": None if blocked else 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": (
                    self.DEFAULT_BASE_FOOTPRINT.to_dict()
                ),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": (
                    "footprint-fingerprint"
                ),
            }

        def route_pose_clear(self, pose, _obstacles, *, footprint):
            resolved = tuple(float(value) for value in pose)
            self.endpoint_checks.append(
                (resolved, float(footprint.safety_margin))
            )
            north_stance = (
                abs(resolved[0]) <= 1e-6
                and resolved[1] >= 0.619
            )
            return (
                (True, None)
                if north_stance
                else (False, "table_1")
            )

    gripper_target_yaws: list[float] = []

    def gripper_target_for_grasp(
        scene,
        grasp_point,
        grasp_mode,
        *,
        base_yaw,
        suction_standoff,
    ):
        assert scene is runtime.scene
        assert grasp_mode == "suction"
        assert suction_standoff == pytest.approx(0.0)
        gripper_target_yaws.append(float(base_yaw))
        return tuple(grasp_point)

    AngularResamplingSupervisor.calls.clear()
    AngularResamplingSupervisor.angular_validation_inputs.clear()
    navigation = AngularResamplingNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    runtime = make_runtime()
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            supervisor_type=AngularResamplingSupervisor,
            control_overrides={
                "gripper_target_for_grasp": (
                    gripper_target_for_grasp
                ),
            },
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRoutePlan)
    assert decision.artifact["goal_pose_world"][:2] == pytest.approx(
        [0.0, 0.62]
    )
    assert len(AngularResamplingSupervisor.calls) == 3
    assert len(
        AngularResamplingSupervisor.angular_validation_inputs
    ) == 1
    assert len(
        AngularResamplingSupervisor.angular_validation_inputs[0]
    ) == 1
    assert len(gripper_target_yaws) == 24
    assert len(navigation.calls) == 3
    angular = decision.artifact["source_support_standoff_retry"][
        "angular_resampling"
    ]
    assert angular["sampled_candidate_count"] == 24
    assert angular["max_candidate_count"] == 24
    assert angular["angle_step_degrees"] == pytest.approx(15.0)
    assert angular["endpoint_clear_candidate_count"] == 1
    assert angular["grasp_input_candidate_count"] == 1
    assert angular["grasp_validator_invoked"] is True
    assert angular["grasp_validated_candidate_count"] == 1
    assert angular["distinct_candidate_count"] == 1


def test_planner_angular_resampling_requires_robust_endpoint_validator(
) -> None:
    class AngularResamplingSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            if len(self.calls) == 1:
                return ((0.0, 0.0, 0.0),)
            if len(self.calls) == 2:
                return ((0.1, 0.0, 0.0),)
            raise AssertionError(
                "grasp validator must not run without robust clearance"
            )

    AngularResamplingSupervisor.calls.clear()
    navigation = FakeNavigation(fail_all=True)
    runtime = make_runtime()
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=FakeTarget(
                kind="entity_grasp",
                reference_id="apple_1",
                point=(0.0, 0.0, 0.8),
            ),
            navigation=navigation,
            supervisor_type=AngularResamplingSupervisor,
            control_overrides={
                "gripper_target_for_grasp": (
                    lambda _scene, point, _mode, **_kwargs: tuple(
                        point
                    )
                ),
            },
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert len(AngularResamplingSupervisor.calls) == 2
    assert len(navigation.calls) == 2
    angular = decision.details["source_support_standoff_retry"][
        "angular_resampling"
    ]
    assert angular["sampled_candidate_count"] == 24
    assert angular["endpoint_clear_candidate_count"] == 0
    assert angular["endpoint_validation_unavailable_count"] == 24
    assert angular["grasp_validator_invoked"] is False
    assert angular["distinct_candidate_count"] == 0


def test_planner_angular_resampling_fails_closed_on_grasp_validator_error(
) -> None:
    class RaisingAngularSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            if len(self.calls) == 1:
                return ((0.0, 0.0, 0.0),)
            if len(self.calls) == 2:
                return ((0.1, 0.0, 0.0),)
            candidates = tuple(
                self.plan_reposition_base_candidates(target)
            )
            assert len(candidates) == 24
            raise RuntimeError("synthetic grasp validation failed")

    class RobustFailingNavigation(FakeNavigation):
        DEFAULT_BASE_FOOTPRINT = RobustFootprint()

        @staticmethod
        def route_pose_clear(_pose, _obstacles, *, footprint):
            assert footprint.safety_margin > 0.015
            return True, None

    RaisingAngularSupervisor.calls.clear()
    navigation = RobustFailingNavigation(fail_all=True)
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=FakeTarget(
                kind="entity_grasp",
                reference_id="apple_1",
                point=(0.0, 0.0, 0.8),
            ),
            navigation=navigation,
            supervisor_type=RaisingAngularSupervisor,
            control_overrides={
                "gripper_target_for_grasp": (
                    lambda _scene, point, _mode, **_kwargs: tuple(
                        point
                    )
                ),
            },
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert len(RaisingAngularSupervisor.calls) == 3
    assert len(navigation.calls) == 2
    angular = decision.details["source_support_standoff_retry"][
        "angular_resampling"
    ]
    assert angular["sampled_candidate_count"] == 24
    assert angular["grasp_input_candidate_count"] == 24
    assert angular["grasp_validator_invoked"] is True
    assert angular["generation_error"]["code"] == "RuntimeError"
    assert angular["distinct_candidate_count"] == 0


def test_planner_does_not_override_explicit_grasp_standoff() -> None:
    class ExplicitStandoffSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            self.calls.append(
                {
                    "kind": "grasp",
                    "target": tuple(target),
                    "entity_id": entity_id,
                    **kwargs,
                }
            )
            return ((0.0, 0.0, 0.0),)

    ExplicitStandoffSupervisor.calls.clear()
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=FakeTarget(
                kind="entity_grasp",
                reference_id="apple_1",
                point=(0.0, 0.0, 0.8),
            ),
            navigation=FakeNavigation(),
            supervisor_type=ExplicitStandoffSupervisor,
        ),
    )

    decision = planner.plan(
        "apple_1",
        {
            "stand_off": 0.58,
            "grasp_policy": "confined_source_region",
            "source_region_id": "table_1",
        },
    )

    assert isinstance(decision, InteractionRouteFailure)
    assert len(ExplicitStandoffSupervisor.calls) == 1
    assert ExplicitStandoffSupervisor.calls[0]["distance"] == \
        pytest.approx(0.58)
    assert "source_support_standoff_retry" not in decision.details


def test_planner_uses_local_egress_when_only_lattice_entry_is_blocked() -> None:
    FakeSupervisor.calls.clear()

    class SingleGoalSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            return ((1.0, 0.0, 0.0),)

    class StartBlockedNavigation(FakeNavigation):
        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            blocked = tuple(start) == (-1.0, 0.0, 0.0)
            return {
                "selected": None if blocked else [start[:2], goal[:2]],
                "poses_world": (
                    [] if blocked else [list(start), list(goal)]
                ),
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": (
                    "START_POSE_IN_COLLISION" if blocked else None
                ),
                "message": "blocked" if blocked else "planned",
                "direct_blocking_entity_ids": (
                    ["table_1"] if blocked else []
                ),
                "expanded_states": 0 if blocked else 3,
                "generated_states": 0 if blocked else 5,
                "path_cost": None if blocked else 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": self.DEFAULT_BASE_FOOTPRINT.to_dict(),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": "footprint-fingerprint",
            }

        @staticmethod
        def route_pose_clear(*_args, **_kwargs):
            return True, None

    navigation = StartBlockedNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            supervisor_type=SingleGoalSupervisor,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRoutePlan)
    assert len(navigation.calls) == 2
    assert navigation.calls[0]["start"] == (-1.0, 0.0, 0.0)
    assert navigation.calls[1]["start"][0] < -1.0
    assert decision.artifact["poses_world"][0] == [-1.0, 0.0, 0.0]
    assert decision.artifact["poses_world"][1][0] < -1.0
    assert decision.artifact["start_egress"]["exact_start_clear"] is True
    assert decision.artifact["start_egress"][
        "selected_escape_pose"
    ] == decision.artifact["poses_world"][1]


def test_planner_rejects_goal_without_execution_error_clearance() -> None:
    FakeSupervisor.calls.clear()

    class TwoGoalSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            return (
                (1.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
            )

    class RobustNavigation(FakeNavigation):
        DEFAULT_BASE_FOOTPRINT = RobustFootprint()

        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            return {
                "selected": [start[:2], goal[:2]],
                "poses_world": [list(start), list(goal)],
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": None,
                "message": "planned",
                "direct_blocking_entity_ids": [],
                "expanded_states": 3,
                "generated_states": 5,
                "path_cost": 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": self.DEFAULT_BASE_FOOTPRINT.to_dict(),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": "footprint-fingerprint",
            }

        @staticmethod
        def route_pose_clear(pose, _obstacles, *, footprint):
            if float(pose[0]) == 1.0 and footprint.safety_margin > 0.015:
                return False, "table_1"
            return True, None

    navigation = RobustNavigation()
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            supervisor_type=TwoGoalSupervisor,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRoutePlan)
    assert decision.artifact["goal_pose_world"] == [2.0, 0.0, 0.0]
    rejection = decision.artifact["candidate_rejections"][0]
    assert rejection["code"] == "GOAL_POSE_UNCERTAIN_COLLISION"
    assert rejection["failure_mode"] == "EXECUTION_TOLERANCE_COLLISION"
    assessment = rejection["endpoint_clearance_assessment"]
    assert assessment["validated"] is True
    assert assessment["blocking_entity_id"] == "table_1"
    assert abs(assessment["uncertainty_margin"] - 0.019) < 1e-12


def test_modeled_obstacles_are_deferred_to_astar_stance_validation() -> None:
    FakeSupervisor.calls.clear()
    wall = FakeObstacle("aisle_left")
    navigation = FakeNavigation(navigation_obstacles=[wall])
    navigation.route_obstacle = wall
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRoutePlan)
    assert FakeSupervisor.calls[0]["obstacle_discs"] == ()
    assert decision.artifact["obstacle_ids"] == ["aisle_left"]
    assert decision.artifact["route_obstacle_ids"] == ["aisle_left"]
    assert decision.artifact["stance_candidate_obstacle_ids"] == []
    assert decision.artifact["stance_collision_owner"] == \
        "se2_lattice_astar_route_geometry"
    assert navigation.calls
    assert navigation.calls[0]["obstacles"] == [wall]


def test_placement_stance_uses_place_distance_and_validates_held_path() -> None:
    FakeSupervisor.calls.clear()
    navigation = FakeNavigation()
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    calls: list[tuple[str, tuple]] = []
    placement = SimpleNamespace(
        assess_held_base_path=lambda *args: (
            calls.append(("held", args[2])),
            {"ok": True, "reason_codes": []},
        )[1],
        assess_placement_execution_ready=lambda *args, **kwargs: (
            calls.append(("placement", kwargs["base_pose"])),
            {"ok": True, "reason_codes": []},
        )[1],
        held_departure_prefix=lambda *_args, **_kwargs: {
            "ok": False,
        },
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            placement=placement,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert FakeSupervisor.calls[0]["kind"] == "placement"
    assert FakeSupervisor.calls[0]["distance"] == 0.62
    assert [item[0] for item in calls] == ["held", "placement"]
    assert decision.artifact["held_path_assessment"]["ok"] is True
    assert decision.artifact[
        "placement_approach_assessment"
    ]["ok"] is True
    assert navigation.route_exclusions == [{"apple_1", "plate_1"}]
    assert decision.artifact["route_excluded_entity_ids"] == [
        "apple_1",
        "plate_1",
    ]


def test_placement_skips_stance_that_blocks_task_continuation(
    monkeypatch,
) -> None:
    FakeSupervisor.calls.clear()

    class TwoStanceSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates(self, target, **kwargs):
            self.calls.append(
                {
                    "kind": "placement",
                    "target": tuple(target),
                    **kwargs,
                }
            )
            return (
                (1.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
            )

    continuation_calls: list[tuple[float, float, float]] = []

    def assess_continuation(**kwargs):
        start = tuple(kwargs["start_pose"])
        continuation_calls.append(start)
        ok = start[0] == 2.0
        data = {
            "ok": ok,
            "start_pose": list(start),
            "blocking_entity_ids": [] if ok else ["box_1"],
            "failure_mode": (
                None
                if ok
                else "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
            ),
        }
        return SimpleNamespace(
            ok=ok,
            failure_mode=data["failure_mode"],
            blocking_entity_ids=tuple(data["blocking_entity_ids"]),
            to_dict=lambda: dict(data),
        )

    monkeypatch.setattr(
        interaction_routes,
        "assess_post_placement_continuation",
        assess_continuation,
    )
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    placement = SimpleNamespace(
        assess_held_base_path=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        assess_placement_execution_ready=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        held_departure_prefix=lambda *_args, **_kwargs: {"ok": False},
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
            placement=placement,
            supervisor_type=TwoStanceSupervisor,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
            "continuation_anchor_pose": [-1.0, 0.0, 0.0],
            "continuation_goal_poses": [[0.5, 0.0, 0.0]],
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert continuation_calls == [
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
    ]
    assert decision.artifact["goal_pose_world"] == [2.0, 0.0, 0.0]
    rejection = decision.artifact["candidate_rejections"][0]
    assert rejection["phase"] == "post_placement_continuation"
    assert rejection["failure_mode"] == \
        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
    assert decision.artifact[
        "post_placement_continuation_assessment"
    ]["ok"] is True


def test_all_continuation_blocking_stances_request_layout_replan(
    monkeypatch,
) -> None:
    FakeSupervisor.calls.clear()

    class TwoStanceSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates(self, target, **kwargs):
            return (
                (1.0, 0.0, 0.0),
                (2.0, 0.0, 0.0),
            )

    def assess_continuation(**kwargs):
        start = tuple(kwargs["start_pose"])
        data = {
            "ok": False,
            "start_pose": list(start),
            "blocking_entity_ids": ["box_1"],
            "failure_mode": (
                "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
            ),
        }
        return SimpleNamespace(
            ok=False,
            failure_mode=data["failure_mode"],
            blocking_entity_ids=("box_1",),
            to_dict=lambda: dict(data),
        )

    monkeypatch.setattr(
        interaction_routes,
        "assess_post_placement_continuation",
        assess_continuation,
    )
    runtime = make_runtime()
    runtime.perception.catalog["box_1"] = {
        "kind": "physical_object",
        "geometry": {},
    }
    runtime.world.entities["box_1"] = SimpleNamespace(revision=5)
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    placement = SimpleNamespace(
        assess_held_base_path=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        assess_placement_execution_ready=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        held_departure_prefix=lambda *_args, **_kwargs: {"ok": False},
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
            placement=placement,
            supervisor_type=TwoStanceSupervisor,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
            "continuation_anchor_pose": [-1.0, 0.0, 0.0],
            "continuation_goal_poses": [[0.5, 0.0, 0.0]],
        },
    )

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "INTERACTION_POSE_BLOCKED"
    assert decision.details["failure_mode"] == \
        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
    assert decision.details["recovery_kind"] == \
        "replan_placement_layout"
    assert decision.details["blocking_entity_ids"] == ["box_1"]
    assert len(decision.details["candidate_rejections"]) == 2


def test_held_departure_repairs_prioritize_smallest_yaw_change() -> None:
    FakeSupervisor.calls.clear()

    class OrderedSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates(self, target, **kwargs):
            self.calls.append(
                {
                    "kind": "placement",
                    "target": tuple(target),
                    **kwargs,
                }
            )
            return (
                (1.0, 0.0, 3.0),
                (1.0, 0.0, 0.2),
            )

    class DeferredRepairNavigation(FakeNavigation):
        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            blocked = tuple(start) == (-1.0, 0.0, 0.0)
            return {
                "selected": (
                    None if blocked else [start[:2], goal[:2]]
                ),
                "poses_world": (
                    [] if blocked else [list(start), list(goal)]
                ),
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": (
                    "NO_SAFE_DETOUR" if blocked else None
                ),
                "message": "blocked" if blocked else "planned",
                "direct_blocking_entity_ids": (
                    ["aisle_left"] if blocked else []
                ),
                "expanded_states": 52 if blocked else 3,
                "generated_states": 52 if blocked else 5,
                "path_cost": None if blocked else 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": (
                    self.DEFAULT_BASE_FOOTPRINT.to_dict()
                ),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": (
                    "footprint-fingerprint"
                ),
            }

    navigation = DeferredRepairNavigation()
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    prefix_yaws: list[float] = []
    placement = SimpleNamespace(
        assess_held_base_path=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        assess_placement_execution_ready=(
            lambda *_args, **_kwargs: {
                "ok": True,
                "reason_codes": [],
            }
        ),
        held_departure_prefix=lambda *_args, **_kwargs: (
            prefix_yaws.append(float(_args[2])),
            {
                "ok": True,
                "poses": [
                    [-1.0, 0.0, 0.0],
                    [-1.08, 0.0, 0.0],
                ],
                "escape_pose": [-1.08, 0.0, 0.0],
                "target_yaw": float(_args[2]),
                "distance": 0.08,
                "assessment": {"ok": True, "reason_codes": []},
                "reason_codes": [],
            },
        )[1],
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            placement=placement,
            supervisor_type=OrderedSupervisor,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert [call["goal"] for call in navigation.calls] == [
        (1.0, 0.0, 3.0),
        (1.0, 0.0, 0.2),
        (1.0, 0.0, 0.2),
    ]
    assert prefix_yaws == [0.2]
    assert decision.artifact["goal_pose_world"] == [1.0, 0.0, 0.2]


def test_repaired_held_route_uses_and_exposes_posture_transition() -> None:
    FakeSupervisor.calls.clear()
    navigation = FakeNavigation()
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    transition = {
        "model_version": "held_departure_posture/1.0",
        "initial_configuration": {
            "lift": 0.26,
            "arm_extend": 0.04,
            "wrist_yaw": 0.18,
        },
        "target_configuration": {
            "lift": 0.34,
            "arm_extend": 0.02,
            "wrist_yaw": 0.0,
        },
        "stages": [
            {
                "name": "raise_clearance",
                "target_configuration": {
                    "lift": 0.34,
                    "arm_extend": 0.04,
                    "wrist_yaw": 0.18,
                },
                "allowed_held_contact_entity_ids": ["table_1"],
            },
            {
                "name": "retract_arm",
                "target_configuration": {
                    "lift": 0.34,
                    "arm_extend": 0.02,
                    "wrist_yaw": 0.18,
                },
                "allowed_held_contact_entity_ids": [],
            },
            {
                "name": "align_wrist",
                "target_configuration": {
                    "lift": 0.34,
                    "arm_extend": 0.02,
                    "wrist_yaw": 0.0,
                },
                "allowed_held_contact_entity_ids": [],
            },
        ],
    }
    assessment_calls: list[dict] = []

    def assess_held(*_args, **kwargs):
        assessment_calls.append(dict(kwargs))
        if kwargs.get("arm_configuration") is not None:
            return {"ok": True, "reason_codes": []}
        return {
            "ok": False,
            "reason_codes": ["HELD_PATH_COLLISION"],
            "phase": "pose_rotation",
            "segment_index": 0,
            "issue": {
                "code": "COLLISION",
                "collision_pair": ["apple_1", "table_1"],
            },
        }

    placement = SimpleNamespace(
        assess_held_base_path=assess_held,
        assess_placement_execution_ready=(
            lambda *_args, **_kwargs: {
                "ok": True,
                "reason_codes": [],
            }
        ),
        held_departure_prefix=lambda *_args, **kwargs: {
            "ok": True,
            "poses": [
                [-1.0, 0.0, 0.0],
                [-1.2, 0.0, 0.0],
            ],
            "escape_pose": [-1.2, 0.0, 0.0],
            "target_yaw": float(_args[2]),
            "distance": 0.2,
            "reason_codes": [],
            "posture_transition": transition,
        },
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            placement=placement,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert len(assessment_calls) == 2
    assert assessment_calls[0] == {}
    assert assessment_calls[1]["arm_configuration"] == (
        transition["target_configuration"]
    )
    assert decision.artifact["posture_transition"] == transition
    assert decision.artifact["held_departure_prefix"][
        "posture_transition"
    ] == transition


def test_initial_astar_failure_replans_after_held_departure() -> None:
    FakeSupervisor.calls.clear()

    class InitiallyTrappedNavigation(FakeNavigation):
        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            blocked = len(self.calls) == 1
            return {
                "selected": (
                    None if blocked else [start[:2], goal[:2]]
                ),
                "poses_world": (
                    [] if blocked else [list(start), list(goal)]
                ),
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": (
                    "NO_SAFE_DETOUR" if blocked else None
                ),
                "message": "blocked" if blocked else "planned",
                "direct_blocking_entity_ids": (
                    ["aisle_left"] if blocked else []
                ),
                "expanded_states": 52 if blocked else 3,
                "generated_states": 52 if blocked else 5,
                "path_cost": None if blocked else 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": (
                    self.DEFAULT_BASE_FOOTPRINT.to_dict()
                ),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": (
                    "footprint-fingerprint"
                ),
            }

    navigation = InitiallyTrappedNavigation()
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    held_paths: list[tuple] = []
    placement = SimpleNamespace(
        assess_held_base_path=lambda *_args, **_kwargs: (
            held_paths.append(tuple(_args[2])),
            {"ok": True, "reason_codes": []},
        )[1],
        assess_placement_execution_ready=(
            lambda *_args, **_kwargs: {
                "ok": True,
                "reason_codes": [],
            }
        ),
        held_departure_prefix=lambda *_args, **_kwargs: {
            "ok": True,
            "poses": [
                [-1.0, 0.0, 0.0],
                [-1.08, 0.0, 0.0],
            ],
            "escape_pose": [-1.08, 0.0, 0.0],
            "target_yaw": float(_args[2]),
            "distance": 0.08,
            "assessment": {"ok": True, "reason_codes": []},
            "reason_codes": [],
        },
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            placement=placement,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert [call["start"] for call in navigation.calls] == [
        (-1.0, 0.0, 0.0),
        (-1.08, 0.0, 0.0),
    ]
    assert decision.artifact["route_attempt_count"] == 2
    prefix = decision.artifact["held_departure_prefix"]
    assert prefix["target_yaw_source"] == \
        "candidate_goal_without_initial_route"
    assert prefix["initial_route_failure"]["code"] == \
        "NO_SAFE_DETOUR"
    assert prefix["initial_route_failure"][
        "direct_blocking_entity_ids"
    ] == ["aisle_left"]
    assert decision.artifact["poses_world"] == [
        [-1.0, 0.0, 0.0],
        [-1.08, 0.0, 0.0],
        [1.0, 0.0, 0.0],
    ]
    assert len(held_paths) == 1


def test_held_departure_tries_farther_prefix_after_reroute_failure() -> None:
    FakeSupervisor.calls.clear()

    class DistanceSensitiveNavigation(FakeNavigation):
        def plan_base_route(
            self,
            start,
            goal,
            obstacles,
            *,
            position_ok=None,
        ):
            self.calls.append(
                {
                    "start": tuple(start),
                    "goal": tuple(goal),
                    "obstacles": list(obstacles),
                    "position_ok": position_ok,
                }
            )
            blocked = float(start[0]) > -1.20
            return {
                "selected": (
                    None if blocked else [start[:2], goal[:2]]
                ),
                "poses_world": (
                    [] if blocked else [list(start), list(goal)]
                ),
                "planner": "se2_lattice_astar",
                "planner_model_version": "astar/1",
                "reason_code": (
                    "NO_SAFE_DETOUR" if blocked else None
                ),
                "message": "blocked" if blocked else "planned",
                "direct_blocking_entity_ids": (
                    ["aisle_left"] if blocked else []
                ),
                "expanded_states": 52 if blocked else 3,
                "generated_states": 52 if blocked else 5,
                "path_cost": None if blocked else 2.0,
                "resolution": 0.08,
                "heading_bins": 24,
                "base_footprint": (
                    self.DEFAULT_BASE_FOOTPRINT.to_dict()
                ),
                "base_footprint_model_version": "footprint/1",
                "base_footprint_fingerprint": (
                    "footprint-fingerprint"
                ),
            }

    prefix_calls = []

    def held_departure_prefix(
        scene,
        _object_id,
        target_yaw,
        *,
        minimum_distance=0.0,
        **_kwargs,
    ):
        prefix_calls.append(float(minimum_distance))
        distance = 0.08 if minimum_distance <= 0.08 else 0.24
        (base, current_yaw) = scene.base_pose()
        start = [float(base[0]), float(base[1]), float(current_yaw)]
        escape = [
            start[0] - distance,
            start[1],
            start[2],
        ]
        return {
            "ok": True,
            "poses": [start, escape],
            "escape_pose": escape,
            "target_yaw": float(target_yaw),
            "minimum_distance": float(minimum_distance),
            "distance": distance,
            "assessment": {"ok": True, "reason_codes": []},
            "reason_codes": [],
        }

    navigation = DistanceSensitiveNavigation()
    target = FakeTarget(
        kind="placement_pose",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.9),
        placement_object_id="apple_1",
        destination_id="plate_1",
        placement_point=(0.0, 0.0, 0.8),
        relation="inside_support_region",
    )
    placement = SimpleNamespace(
        assess_held_base_path=lambda *_args, **_kwargs: {
            "ok": True,
            "reason_codes": [],
        },
        assess_placement_execution_ready=(
            lambda *_args, **_kwargs: {
                "ok": True,
                "reason_codes": [],
            }
        ),
        held_departure_prefix=held_departure_prefix,
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
            placement=placement,
        ),
    )

    decision = planner.plan(
        "plate_1",
        {
            "placement_object_ids": ["apple_1"],
            "destination_ids": ["plate_1"],
            "require_held_load": True,
        },
    )

    assert isinstance(decision, InteractionRoutePlan)
    assert [call["start"] for call in navigation.calls] == [
        (-1.0, 0.0, 0.0),
        (-1.08, 0.0, 0.0),
        (-1.24, 0.0, 0.0),
    ]
    assert prefix_calls == [0.0, 0.12]
    assert decision.artifact["route_attempt_count"] == 3
    prefix = decision.artifact["held_departure_prefix"]
    assert prefix["distance"] == 0.24
    assert prefix["target_yaw_source"] == \
        "candidate_goal_without_initial_route"
    assert prefix["initial_route_failure"]["code"] == \
        "NO_SAFE_DETOUR"
    rejected = prefix["departure_candidate_rejections"]
    assert len(rejected) == 1
    assert rejected[0]["phase"] == "astar_after_held_departure"
    assert rejected[0]["departure_distance"] == 0.08
    assert decision.artifact["poses_world"] == [
        [-1.0, 0.0, 0.0],
        [-1.24, 0.0, 0.0],
        [1.0, 0.0, 0.0],
    ]


def test_missing_placement_target_reports_repairable_region_change() -> None:
    navigation = FakeNavigation()
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=None,
            navigation=navigation,
        ),
    )
    params = {
        "interaction_target_kind": "placement_pose",
        "placement_object_ids": ["apple_1"],
        "destination_ids": ["plate_1"],
        "participants": {
            "reference": ["plate_1"],
            "placement_object": ["apple_1"],
        },
        "relation": "inside_support_region",
        "target_ref": "layout/plate/apple",
        "layout_producer_node_id": "program/place/select-space",
        "layout_continuation_scope_id": "program/place",
    }

    decision = planner.plan("plate_1", params)

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "TARGET_REGION_CHANGED"
    assert decision.retryable is True
    assert decision.details["params"] == params
    assert decision.details["artifact_kind"] == "layout_targets"
    assert decision.details["artifact_ref"] == "layout/plate/apple"
    assert decision.details["affected_refs"] == [
        "layout/plate/apple"
    ]


def test_grasp_candidate_collision_promotes_relocatable_blocker() -> None:
    candidate_failures = [
        {
            "code": "COLLISION",
            "collision_pair": [
                "link_gripper_finger_right",
                "apple_1",
            ],
        },
        {
            "code": "COLLISION",
            "collision_pair": ["base_link", "aisle_left"],
        },
        {
            "code": "COLLISION",
            "collision_pair": [
                "link_gripper_finger_right",
                "apple_1",
            ],
        },
    ]

    class CandidateError(Exception):
        code = "NO_REACHABLE_POSE"
        message = "no collision-free grasp approach"
        details = {
            "candidate_failures": candidate_failures,
            "collision_pair": ["base_link", "aisle_left"],
        }

    class BlockedGraspSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            del target, entity_id, kwargs
            raise CandidateError

    runtime = make_runtime()
    runtime.perception.catalog["apple_1"].update(
        {
            "movable": True,
            "interaction_capabilities": {"grasp": "available"},
        }
    )
    runtime.perception.catalog["aisle_left"] = {
        "movable": False,
        "attributes": {"fixed": True},
        "interaction_capabilities": {"grasp": "unavailable"},
    }
    runtime.world.entities["aisle_left"] = SimpleNamespace(revision=5)
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
            supervisor_type=BlockedGraspSupervisor,
        ),
    )

    decision = planner.plan("plate_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "PATH_BLOCKED"
    assert decision.message == "no collision-free grasp approach"
    assert decision.details["raw_failure_code"] == "NO_REACHABLE_POSE"
    assert decision.details["failure_mode"] == \
        "ROUTE_ENDPOINT_IN_COLLISION"
    assert decision.details["recovery_kind"] == \
        "relocate_interaction_blocker"
    assert decision.details["blocking_entity_ids"] == ["apple_1"]
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == ["apple_1"]
    assert decision.details["candidate_failures"] == candidate_failures


def test_grasp_candidate_collision_without_relocatable_blocker_fails_closed(
) -> None:
    class CandidateError(Exception):
        code = "NO_REACHABLE_POSE"
        message = "no collision-free grasp approach"
        details = {
            "candidate_failures": [
                {
                    "code": "COLLISION",
                    "collision_pair": ["base_link", "aisle_left"],
                },
                {
                    "code": "COLLISION",
                    "collision_pair": [
                        "link_gripper_finger_right",
                        "robot_1",
                    ],
                },
                {
                    "code": "COLLISION",
                    "collision_pair": [
                        "link_gripper_finger_right",
                        "plate_1",
                    ],
                },
                {
                    "code": "COLLISION",
                    "collision_pair": "apple_1",
                },
                {
                    "code": "COLLISION",
                    "collision_pair": ["apple_1"],
                },
            ],
        }

    class BlockedGraspSupervisor(FakeSupervisor):
        def plan_reposition_base_candidates_for_grasp(
            self,
            target,
            entity_id,
            **kwargs,
        ):
            del target, entity_id, kwargs
            raise CandidateError

    runtime = make_runtime()
    runtime.perception.catalog["apple_1"].update(
        {
            "movable": True,
            "interaction_capabilities": {"grasp": "available"},
        }
    )
    runtime.perception.catalog["robot_1"] = {
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.perception.catalog["aisle_left"] = {
        "movable": False,
        "attributes": {"fixed": True},
        "interaction_capabilities": {"grasp": "unavailable"},
    }
    runtime.world.entities.update(
        {
            "robot_1": SimpleNamespace(revision=5),
            "aisle_left": SimpleNamespace(revision=6),
        }
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="plate_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
            supervisor_type=BlockedGraspSupervisor,
        ),
    )

    decision = planner.plan("plate_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "NO_REACHABLE_POSE"
    assert decision.details["raw_failure_code"] == "NO_REACHABLE_POSE"
    assert "recovery_kind" not in decision.details
    assert "verified_route_blocking_entity_ids" not in decision.details


def test_all_route_candidates_preserve_blocker_diagnostics() -> None:
    navigation = FakeNavigation(fail_all=True)
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "PATH_BLOCKED"
    assert decision.details["failure_mode"] == \
        "ROUTE_OBSTACLE_BLOCKS_PATH"
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == ["table_1"]
    assert len(decision.details["candidate_rejections"]) == 2


def test_no_safe_detour_verifies_relocatable_blocker_counterfactually(
) -> None:
    runtime = make_runtime()
    runtime.perception.catalog["cup_1"] = {
        "kind": "physical_object",
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.perception.catalog["aisle_left"] = {
        "kind": "physical_object",
        "movable": False,
        "attributes": {"fixed": True},
        "interaction_capabilities": {"grasp": "unavailable"},
    }
    runtime.world.entities.update(
        {
            "cup_1": SimpleNamespace(revision=5),
            "aisle_left": SimpleNamespace(revision=6),
        }
    )
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("aisle_left", "cup_1", "table_1"),
        direct_blocking_entity_ids=(
            "aisle_left",
            "cup_1",
            "table_1",
        ),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.code == "PATH_BLOCKED"
    assert decision.details["failure_mode"] == \
        "ROUTE_OBSTACLE_BLOCKS_PATH"
    assert decision.details["recovery_kind"] == \
        "relocate_interaction_blocker"
    assert decision.details["raw_failure_code"] == "NO_SAFE_DETOUR"
    assert decision.details["blocking_entity_ids"] == ["cup_1"]
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == ["cup_1"]
    assert len(navigation.calls) == 4
    for rejection in decision.details["candidate_rejections"]:
        evidence = rejection["counterfactual_blocker_verification"]
        assert evidence["method"] == \
            "counterfactual_astar_minimal_blocker_sets"
        assert evidence["verified_entity_ids"] == ["cup_1"]
        assert evidence["verified_minimal_blocker_sets"] == [["cup_1"]]
        assert evidence["selected_minimal_blocker_set"] == ["cup_1"]
        assert evidence["minimality_proven"] is True
        attempts = {
            item["entity_id"]: item for item in evidence["attempts"]
        }
        assert attempts["cup_1"]["outcome"] == \
            "ROUTE_RESTORED_AND_VALIDATED"
        assert attempts["table_1"]["outcome"] == \
            "NOT_RELOCATABLE_BY_GRASP"
        assert attempts["aisle_left"]["outcome"] == \
            "NOT_RELOCATABLE_BY_GRASP"


def test_no_safe_detour_fails_closed_when_removal_does_not_restore_route(
) -> None:
    runtime = make_runtime()
    runtime.perception.catalog["cup_1"] = {
        "kind": "physical_object",
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.world.entities["cup_1"] = SimpleNamespace(revision=5)
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "table_1"),
        direct_blocking_entity_ids=("cup_1", "table_1"),
        restored_route="missing",
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details["failure_mode"] == \
        "NO_COLLISION_FREE_ROUTE"
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    for rejection in decision.details["candidate_rejections"]:
        attempts = rejection[
            "counterfactual_blocker_verification"
        ]["attempts"]
        cup_attempt = next(
            item for item in attempts if item["entity_id"] == "cup_1"
        )
        assert cup_attempt["outcome"] == "ROUTE_NOT_RESTORED"


def test_no_safe_detour_does_not_replan_for_fixed_only_blockers() -> None:
    navigation = NoSafeDetourNavigation(
        candidate_id="table_1",
        route_obstacle_ids=("table_1",),
        direct_blocking_entity_ids=("table_1",),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    assert len(navigation.calls) == 2
    attempts = decision.details["candidate_rejections"][0][
        "counterfactual_blocker_verification"
    ]["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["entity_id"] == "table_1"
    assert attempts[0]["candidate_source"] == \
        "direct_blocking_entity_ids"
    assert attempts[0]["outcome"] == "NOT_RELOCATABLE_BY_GRASP"
    assert attempts[0]["conclusive"] is True


def test_no_safe_detour_excludes_current_and_protected_targets() -> None:
    runtime = make_runtime()
    runtime.perception.catalog["apple_1"].update(
        {
            "movable": True,
            "interaction_capabilities": {"grasp": "available"},
        }
    )
    runtime.perception.catalog["plate_1"].update(
        {
            "movable": True,
            "interaction_capabilities": {"grasp": "available"},
        }
    )
    runtime.perception.catalog["cup_1"] = {
        "kind": "physical_object",
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.world.entities["cup_1"] = SimpleNamespace(revision=5)
    navigation = NoSafeDetourNavigation(
        candidate_id="apple_1",
        route_obstacle_ids=("apple_1", "plate_1", "cup_1"),
        direct_blocking_entity_ids=("apple_1", "plate_1", "cup_1"),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan(
        "apple_1",
        {
            "protected_relocation_entity_ids": ["plate_1"],
            "exclude_entity_ids": ["cup_1"],
        },
    )

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    assert len(navigation.calls) == 2
    attempts = decision.details["candidate_rejections"][0][
        "counterfactual_blocker_verification"
    ]["attempts"]
    assert {
        item["entity_id"]: item["outcome"] for item in attempts
    } == {
        "apple_1": "EXCLUDED_ENTITY",
        "plate_1": "EXCLUDED_ENTITY",
        "cup_1": "EXCLUDED_ENTITY",
    }


@pytest.mark.parametrize("restored_route", ["wrong_endpoint", "malformed"])
def test_no_safe_detour_rejects_invalid_counterfactual_route(
    restored_route,
) -> None:
    runtime = make_runtime()
    runtime.perception.catalog["cup_1"] = {
        "kind": "physical_object",
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.world.entities["cup_1"] = SimpleNamespace(revision=5)
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "table_1"),
        direct_blocking_entity_ids=("cup_1",),
        restored_route=restored_route,
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    attempts = decision.details["candidate_rejections"][0][
        "counterfactual_blocker_verification"
    ]["attempts"]
    cup_attempt = next(
        item for item in attempts if item["entity_id"] == "cup_1"
    )
    assert cup_attempt["outcome"] in {
        "RESTORED_ROUTE_INVALID",
        "RESTORED_ROUTE_MALFORMED",
    }
    assert cup_attempt["conclusive"] is False


def raise_endpoint_validation_error(
    _pose,
    _obstacles,
    *,
    footprint,
):
    del footprint
    raise RuntimeError("endpoint validation failed")


@pytest.mark.parametrize(
    ("endpoint_validator", "expected_outcome"),
    (
        (None, "RESTORED_ENDPOINT_UNVERIFIED"),
        (
            lambda _pose, _obstacles, *, footprint: (
                False,
                "table_1",
            ),
            "RESTORED_ENDPOINT_UNSAFE",
        ),
        (
            raise_endpoint_validation_error,
            "COUNTERFACTUAL_ENDPOINT_VALIDATION_ERROR",
        ),
    ),
)
def test_no_safe_detour_requires_verified_safe_counterfactual_endpoint(
    endpoint_validator,
    expected_outcome,
) -> None:
    runtime = make_runtime()
    runtime.perception.catalog["cup_1"] = {
        "kind": "physical_object",
        "movable": True,
        "interaction_capabilities": {"grasp": "available"},
    }
    runtime.world.entities["cup_1"] = SimpleNamespace(revision=5)
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "table_1"),
        direct_blocking_entity_ids=("cup_1",),
    )
    navigation.route_pose_clear = endpoint_validator
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    attempts = decision.details["candidate_rejections"][0][
        "counterfactual_blocker_verification"
    ]["attempts"]
    attempt = next(
        item for item in attempts if item.get("entity_id") == "cup_1"
    )
    assert attempt["outcome"] == expected_outcome
    if expected_outcome == "COUNTERFACTUAL_ENDPOINT_VALIDATION_ERROR":
        assert attempt["conclusive"] is False


def test_no_safe_detour_finds_non_direct_route_obstacle_singleton() -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1", "box_1")
    navigation = NoSafeDetourNavigation(
        candidate_id="box_1",
        route_obstacle_ids=("cup_1", "box_1", "table_1"),
        direct_blocking_entity_ids=("cup_1", "table_1"),
        relocatable_ids=("cup_1", "box_1"),
        restoring_sets=(("box_1",),),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == ["box_1"]
    assert decision.details["selected_minimal_blocker_set"] == ["box_1"]
    assert decision.details["minimality_proven"] is True
    for rejection in decision.details["candidate_rejections"]:
        evidence = rejection["counterfactual_blocker_verification"]
        assert evidence["verified_entity_ids"] == ["box_1"]
        box_attempt = next(
            item
            for item in evidence["attempts"]
            if item.get("entity_id") == "box_1"
        )
        assert box_attempt["candidate_source"] == "route_obstacles"
        assert box_attempt["outcome"] == \
            "ROUTE_RESTORED_AND_VALIDATED"


def test_no_safe_detour_keeps_alternative_singletons_as_separate_sets(
) -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1", "box_1")
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "box_1", "table_1"),
        direct_blocking_entity_ids=("cup_1", "table_1"),
        relocatable_ids=("cup_1", "box_1"),
        restoring_sets=(("cup_1",), ("box_1",)),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details["verified_minimal_blocker_sets"] == [
        ["box_1"],
        ["cup_1"],
    ]
    assert decision.details["selected_minimal_blocker_set"] == ["box_1"]
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == ["box_1"]
    for rejection in decision.details["candidate_rejections"]:
        evidence = rejection["counterfactual_blocker_verification"]
        assert evidence["verified_minimal_blocker_sets"] == [
            ["box_1"],
            ["cup_1"],
        ]
        assert evidence["verified_entity_ids"] == ["box_1"]


def test_no_safe_detour_verifies_minimal_pair_and_removes_both_maps(
) -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1", "box_1")
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "box_1", "table_1"),
        direct_blocking_entity_ids=("cup_1", "table_1"),
        relocatable_ids=("cup_1", "box_1"),
        restoring_sets=(("cup_1", "box_1"),),
        navigation_obstacle_ids=("cup_1", "box_1"),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details["failure_mode"] == \
        "ROUTE_OBSTACLE_BLOCKS_PATH"
    assert decision.details["recovery_kind"] == \
        "relocate_interaction_blocker_set"
    assert decision.details["blocking_entity_ids"] == [
        "cup_1",
        "box_1",
    ]
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert decision.details["verified_minimal_blocker_sets"] == [
        ["cup_1", "box_1"]
    ]
    assert decision.details["selected_minimal_blocker_set"] == [
        "cup_1",
        "box_1",
    ]
    assert decision.details["selected_sufficient_blocker_set"] == [
        "cup_1",
        "box_1",
    ]
    assert decision.details["minimality_proven"] is True
    for rejection in decision.details["candidate_rejections"]:
        evidence = rejection["counterfactual_blocker_verification"]
        pair_attempt = next(
            item
            for item in evidence["attempts"]
            if item.get("entity_ids") == ["cup_1", "box_1"]
        )
        assert pair_attempt["outcome"] == \
            "ROUTE_RESTORED_AND_VALIDATED"
        assert pair_attempt["removed_route_obstacle_count"] == 2
        assert pair_attempt["removed_navigation_obstacle_count"] == 2


@pytest.mark.parametrize(
    ("validation_stage", "expected_outcome"),
    (
        ("route", "COUNTERFACTUAL_ROUTE_VALIDATION_ERROR"),
        ("endpoint", "COUNTERFACTUAL_ENDPOINT_VALIDATION_ERROR"),
    ),
)
def test_no_safe_detour_validation_errors_fail_closed(
    monkeypatch,
    validation_stage,
    expected_outcome,
) -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1")
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "table_1"),
        direct_blocking_entity_ids=("cup_1",),
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    def raise_validation_error(*_args, **_kwargs):
        raise RuntimeError(f"{validation_stage} validator failed")

    if validation_stage == "route":
        monkeypatch.setattr(
            interaction_routes,
            "validate_se2_route",
            raise_validation_error,
        )
    else:
        monkeypatch.setattr(
            planner,
            "_assess_endpoint_clearance",
            raise_validation_error,
        )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert decision.details["minimality_proven"] is False
    assert "recovery_kind" not in decision.details
    for rejection in decision.details["candidate_rejections"]:
        attempt = next(
            item
            for item in rejection[
                "counterfactual_blocker_verification"
            ]["attempts"]
            if item.get("entity_id") == "cup_1"
        )
        assert attempt["outcome"] == expected_outcome
        assert attempt["conclusive"] is False


def test_no_safe_detour_rejects_poses_with_failure_reason() -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1")
    navigation = NoSafeDetourNavigation(
        candidate_id="cup_1",
        route_obstacle_ids=("cup_1", "table_1"),
        direct_blocking_entity_ids=("cup_1",),
    )
    original_plan = navigation.plan_base_route

    def inconsistent_plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        if result["poses_world"]:
            result["reason_code"] = "NO_SAFE_DETOUR"
        return result

    navigation.plan_base_route = inconsistent_plan
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    assert decision.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert "recovery_kind" not in decision.details
    for rejection in decision.details["candidate_rejections"]:
        attempt = next(
            item
            for item in rejection[
                "counterfactual_blocker_verification"
            ]["attempts"]
            if item.get("entity_id") == "cup_1"
        )
        assert attempt["outcome"] == \
            "RESTORED_ROUTE_REPORTED_FAILURE"
        assert attempt["conclusive"] is False


def test_explicit_empty_verified_blockers_do_not_fall_back() -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1")
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
        ),
    )

    failure = planner._failure_from_rejections(
        target=target,
        params={},
        baseline_pose=(-1.0, 0.0, 0.0),
        minimum_reposition_distance=0.1,
        candidate_rejections=[
            {
                "goal": [0.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "PATH_BLOCKED",
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["cup_1"],
                "verified_route_blocking_entity_ids": [],
            }
        ],
        route_attempts=1,
    )

    assert failure.details["blocking_entity_ids"] == ["cup_1"]
    assert failure.details["verified_route_blocking_entity_ids"] == []
    assert failure.details["failure_mode"] == "NO_COLLISION_FREE_ROUTE"
    assert "recovery_kind" not in failure.details


def test_failure_aggregation_selects_one_complete_goal_witness() -> None:
    runtime = make_runtime()
    add_relocatable_entities(
        runtime,
        "cup_1",
        "box_1",
        "can_1",
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
        ),
    )
    pair_evidence = {
        "singleton_search_conclusive": True,
        "candidate_limit_reached": False,
        "minimal_set_enumeration_complete": False,
    }

    failure = planner._failure_from_rejections(
        target=target,
        params={},
        baseline_pose=(-1.0, 0.0, 0.0),
        minimum_reposition_distance=0.1,
        candidate_rejections=[
            {
                "goal": [0.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "NO_SAFE_DETOUR",
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["cup_1", "box_1"],
                "verified_route_blocking_entity_ids": [],
                "verified_minimal_blocker_sets": [
                    ["cup_1", "box_1"]
                ],
                "selected_minimal_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "selected_sufficient_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "minimality_proven": True,
                "counterfactual_blocker_verification": pair_evidence,
            },
            {
                "goal": [1.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "NO_SAFE_DETOUR",
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["can_1"],
                "verified_route_blocking_entity_ids": ["can_1"],
                "verified_minimal_blocker_sets": [["can_1"]],
                "selected_minimal_blocker_set": ["can_1"],
                "selected_sufficient_blocker_set": ["can_1"],
                "minimality_proven": True,
                "counterfactual_blocker_verification": {
                    **pair_evidence,
                    "minimal_set_enumeration_complete": True,
                },
            },
        ],
        route_attempts=2,
    )

    assert failure.details[
        "verified_route_blocking_entity_ids"
    ] == ["can_1"]
    assert failure.details["selected_minimal_blocker_set"] == ["can_1"]
    assert failure.details["verified_minimal_blocker_sets"] == [
        ["can_1"]
    ]
    assert failure.details["minimality_proven"] is True
    assert failure.details["recovery_kind"] == \
        "relocate_interaction_blocker"
    assert failure.details["raw_failure_code"] == "NO_SAFE_DETOUR"
    assert failure.details["selected_blocker_set_witness"][
        "candidate_index"
    ] == 1


def test_pair_stays_sufficient_when_another_goal_is_inconclusive() -> None:
    runtime = make_runtime()
    add_relocatable_entities(runtime, "cup_1", "box_1")
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
        ),
    )

    failure = planner._failure_from_rejections(
        target=target,
        params={},
        baseline_pose=(-1.0, 0.0, 0.0),
        minimum_reposition_distance=0.1,
        candidate_rejections=[
            {
                "goal": [0.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "NO_SAFE_DETOUR",
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["cup_1", "box_1"],
                "verified_route_blocking_entity_ids": [],
                "verified_minimal_blocker_sets": [
                    ["cup_1", "box_1"]
                ],
                "selected_minimal_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "selected_sufficient_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "minimality_proven": True,
                "counterfactual_blocker_verification": {
                    "singleton_search_conclusive": True,
                    "candidate_limit_reached": False,
                    "minimal_set_enumeration_complete": False,
                },
            },
            {
                "goal": [1.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "NO_SAFE_DETOUR",
                "failure_mode": "NO_COLLISION_FREE_ROUTE",
                "blocking_entity_ids": [],
                "verified_route_blocking_entity_ids": [],
                "verified_minimal_blocker_sets": [],
                "selected_minimal_blocker_set": [],
                "selected_sufficient_blocker_set": [],
                "minimality_proven": False,
                "counterfactual_blocker_verification": {
                    "singleton_search_conclusive": False,
                    "candidate_limit_reached": False,
                    "minimal_set_enumeration_complete": True,
                },
            },
        ],
        route_attempts=2,
    )

    assert failure.details["blocking_entity_ids"][:2] == [
        "cup_1",
        "box_1",
    ]
    assert failure.details[
        "verified_route_blocking_entity_ids"
    ] == []
    assert failure.details["selected_sufficient_blocker_set"] == [
        "cup_1",
        "box_1",
    ]
    assert failure.details["selected_minimal_blocker_set"] == []
    assert failure.details["minimality_proven"] is False
    assert failure.details["recovery_kind"] == \
        "relocate_interaction_blocker_set"


def test_endpoint_witness_does_not_mix_with_counterfactual_pair() -> None:
    runtime = make_runtime()
    add_relocatable_entities(
        runtime,
        "can_1",
        "cup_1",
        "box_1",
    )
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=runtime,
        module_loader=make_loader(
            target=target,
            navigation=FakeNavigation(),
        ),
    )

    failure = planner._failure_from_rejections(
        target=target,
        params={},
        baseline_pose=(-1.0, 0.0, 0.0),
        minimum_reposition_distance=0.1,
        candidate_rejections=[
            {
                "goal": [0.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "NO_SAFE_DETOUR",
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["cup_1", "box_1"],
                "verified_route_blocking_entity_ids": [],
                "verified_minimal_blocker_sets": [
                    ["cup_1", "box_1"]
                ],
                "selected_minimal_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "selected_sufficient_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
                "minimality_proven": True,
                "counterfactual_blocker_verification": {
                    "singleton_search_conclusive": True,
                    "candidate_limit_reached": False,
                    "minimal_set_enumeration_complete": False,
                },
            },
            {
                "goal": [1.0, 0.0, 0.0],
                "phase": "base_path",
                "code": "GOAL_POSE_IN_COLLISION",
                "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
                "blocking_entity_ids": ["can_1"],
                "verified_route_blocking_entity_ids": ["can_1"],
                "counterfactual_blocker_verification": None,
            },
        ],
        route_attempts=2,
    )

    assert failure.details[
        "verified_route_blocking_entity_ids"
    ] == ["can_1"]
    assert failure.details["recovery_kind"] == \
        "relocate_interaction_blocker"
    assert failure.details["raw_failure_code"] == \
        "GOAL_POSE_IN_COLLISION"
    assert failure.details["selected_blocker_set_witness"] == {
        "candidate_index": 1,
        "goal": [1.0, 0.0, 0.0],
        "code": "GOAL_POSE_IN_COLLISION",
        "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
    }


def test_route_with_wrong_astar_endpoint_is_rejected() -> None:
    navigation = FakeNavigation()
    original_plan = navigation.plan_base_route

    def wrong_endpoint_plan(*args, **kwargs):
        result = original_plan(*args, **kwargs)
        if result["poses_world"]:
            result["poses_world"][-1][0] += 0.1
        return result

    navigation.plan_base_route = wrong_endpoint_plan
    target = FakeTarget(
        kind="entity_grasp",
        reference_id="apple_1",
        point=(0.0, 0.0, 0.8),
    )
    planner = HarnessInteractionRoutePlanner(
        runtime=make_runtime(),
        module_loader=make_loader(
            target=target,
            navigation=navigation,
        ),
    )

    decision = planner.plan("apple_1", {})

    assert isinstance(decision, InteractionRouteFailure)
    rejection = decision.details["candidate_rejections"][-1]
    assert rejection["code"] == "ROUTE_GOAL_MISMATCH"
    assert rejection["failure_mode"] == "ROUTE_ENDPOINT_MISMATCH"
    assert rejection["route_validation"]["ok"] is False


def test_route_planner_source_has_no_legacy_executor_dependency() -> None:
    source = Path(__file__).parents[1] / (
        "src/task_recursive_tree/integrations/gemini_er2/"
        "interaction_routes.py"
    )
    text = source.read_text(encoding="utf-8").lower()
    forbidden = ("tree" + "executor", "tree_" + "executor")
    assert all(token not in text for token in forbidden)
