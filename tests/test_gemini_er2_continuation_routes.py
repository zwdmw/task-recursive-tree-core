from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2.continuation_routes import (
    assess_post_placement_continuation,
)


@dataclass(frozen=True)
class FakeRouteObstacle:
    entity_id: str
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    bottom_z: float = 0.0
    top_z: float = 0.2

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "bounds": [
                self.min_x,
                self.max_x,
                self.min_y,
                self.max_y,
            ],
        }


class FakeNavigation:
    DEFAULT_BASE_FOOTPRINT = object()
    DEFAULT_BASE_CLEARANCE = 0.30

    def __init__(
        self,
        *,
        prototype: FakeRouteObstacle | None,
        blocked_plan_goals: tuple[tuple[float, float, float], ...] = (),
        existing_obstacles: tuple[FakeRouteObstacle, ...] = (),
        pose_blockers: dict[
            tuple[float, float, float],
            str,
        ] | None = None,
    ) -> None:
        self.prototype = prototype
        self.blocked_plan_goals = set(blocked_plan_goals)
        self.existing_obstacles = existing_obstacles
        self.pose_blockers = dict(pose_blockers or {})
        self.navigation_exclusions: list[set[str]] = []
        self.route_exclusions: list[set[str]] = []
        self.plans: list[dict] = []

    def build_navigation_obstacles(self, *_args, **kwargs):
        self.navigation_exclusions.append(
            set(kwargs.get("exclude_entity_ids") or ())
        )
        return []

    def build_base_route_obstacles(self, *_args, **kwargs):
        excluded = set(kwargs.get("exclude_entity_ids") or ())
        self.route_exclusions.append(excluded)
        obstacles = [
            obstacle
            for obstacle in self.existing_obstacles
            if obstacle.entity_id not in excluded
        ]
        if (
            self.prototype is not None
            and self.prototype.entity_id not in excluded
        ):
            obstacles.append(self.prototype)
        return obstacles

    def route_pose_clear(self, pose, obstacles, **_kwargs):
        blocker_id = self.pose_blockers.get(
            tuple(float(value) for value in pose[:3])
        )
        if blocker_id is not None and any(
            obstacle.entity_id == blocker_id
            for obstacle in obstacles
        ):
            return False, blocker_id
        return True, None

    def plan_base_route(
        self,
        start,
        goal,
        obstacles,
        *,
        position_ok,
    ):
        goal_pose = tuple(float(value) for value in goal[:3])
        self.plans.append(
            {
                "start": tuple(start),
                "goal": goal_pose,
                "obstacles": list(obstacles),
                "position_ok": position_ok,
            }
        )
        blocked = goal_pose in self.blocked_plan_goals
        return {
            "poses_world": (
                [] if blocked else [list(start), list(goal)]
            ),
            "reason_code": "NO_SAFE_DETOUR" if blocked else None,
            "direct_blocking_entity_ids": (
                ["box_1"] if blocked else []
            ),
            "expanded_states": 12 if blocked else 3,
        }

    @staticmethod
    def route_pose_path_clear(*_args, **_kwargs):
        return True, None

    @staticmethod
    def residual_navigation_obstacles(*_args, **_kwargs):
        return []

    @staticmethod
    def path_clear(*_args, **_kwargs):
        return True, None


def make_runtime(*, destination_category: str = "floor"):
    return SimpleNamespace(
        scene=SimpleNamespace(
            base_position_ok=lambda _x, _y: True,
        ),
        perception=SimpleNamespace(
            catalog={
                "box_1": {"category": "box"},
                "floor_1": {"category": destination_category},
            }
        ),
        world=SimpleNamespace(),
    )


def make_control():
    return SimpleNamespace(
        INTERACTION_PATH_POSITION_TOLERANCE=0.008,
        INTERACTION_PATH_YAW_TOLERANCE=0.02,
    )


def test_floor_placement_translates_hypothetical_obstacle_aabb() -> None:
    prototype = FakeRouteObstacle(
        "box_1",
        min_x=1.0,
        max_x=1.4,
        min_y=2.0,
        max_y=2.2,
    )
    navigation = FakeNavigation(prototype=prototype)

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(4.0, 5.0, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=((0.5, 0.0, 0.0),),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is True
    assert assessment.hypothetical_obstacle["entity_id"] == "box_1"
    assert assessment.hypothetical_obstacle["bounds"] == pytest.approx(
        [3.8, 4.2, 4.9, 5.1]
    )
    moved = navigation.plans[0]["obstacles"][-1]
    assert (
        moved.min_x,
        moved.max_x,
        moved.min_y,
        moved.max_y,
    ) == pytest.approx((3.8, 4.2, 4.9, 5.1))
    assert navigation.navigation_exclusions == [{"box_1"}]
    assert navigation.route_exclusions == [{"box_1"}, set()]


def test_floor_placement_does_not_mutate_source_obstacle() -> None:
    prototype = FakeRouteObstacle(
        "box_1",
        min_x=-0.4,
        max_x=0.0,
        min_y=-0.2,
        max_y=0.2,
    )
    original = prototype.to_dict()
    navigation = FakeNavigation(prototype=prototype)

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(1.0, 1.5, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=((0.5, 0.0, 0.0),),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is True
    assert prototype.to_dict() == original
    assert navigation.plans[0]["obstacles"][-1] is not prototype


def test_any_reachable_continuation_goal_is_sufficient() -> None:
    first_goal = (0.5, 0.0, 0.0)
    second_goal = (1.5, 0.0, 0.0)
    navigation = FakeNavigation(
        prototype=FakeRouteObstacle(
            "box_1",
            min_x=-0.2,
            max_x=0.2,
            min_y=-0.2,
            max_y=0.2,
        ),
        blocked_plan_goals=(first_goal,),
    )

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(2.0, 2.0, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=(first_goal, second_goal),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is True
    assert assessment.selected_goal_pose == second_goal
    assert assessment.route_attempt_count == 2
    assert assessment.rejections[0]["goal"] == list(first_goal)
    assert assessment.rejections[0]["code"] == "NO_SAFE_DETOUR"


def test_floor_placement_fails_closed_without_obstacle_prototype() -> None:
    navigation = FakeNavigation(prototype=None)

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(2.0, 2.0, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=((0.5, 0.0, 0.0),),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is False
    assert assessment.failure_mode == \
        "HYPOTHETICAL_OBSTACLE_UNAVAILABLE"
    assert assessment.route_attempt_count == 0
    assert navigation.plans == []
    assert assessment.rejections[0]["code"] == \
        "PERCEPTION_INSUFFICIENT"


def test_existing_goal_blockers_do_not_reject_non_degrading_layout() -> None:
    goal = (0.5, 0.0, 0.0)
    navigation = FakeNavigation(
        prototype=FakeRouteObstacle(
            "box_1",
            min_x=-0.2,
            max_x=0.2,
            min_y=-0.2,
            max_y=0.2,
        ),
        existing_obstacles=(
            FakeRouteObstacle(
                "table_1",
                min_x=0.3,
                max_x=0.7,
                min_y=-0.2,
                max_y=0.2,
            ),
        ),
        pose_blockers={goal: "table_1"},
    )

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(2.0, 2.0, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=(goal,),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is True
    assert assessment.acceptance_mode == "non_degrading"
    assert assessment.selected_goal_pose is None
    assert assessment.blocking_entity_ids == ()
    assert assessment.introduced_blocking_entity_ids == ()
    assert assessment.baseline_assessment["ok"] is False
    assert assessment.baseline_assessment[
        "blocking_entity_ids"
    ] == ["table_1"]


def test_newly_placed_object_blocking_goal_rejects_layout() -> None:
    goal = (0.5, 0.0, 0.0)
    navigation = FakeNavigation(
        prototype=FakeRouteObstacle(
            "box_1",
            min_x=-0.2,
            max_x=0.2,
            min_y=-0.2,
            max_y=0.2,
        ),
        pose_blockers={goal: "box_1"},
    )

    assessment = assess_post_placement_continuation(
        runtime=make_runtime(),
        object_id="box_1",
        destination_id="floor_1",
        placement_point=(0.5, 0.0, 0.1),
        start_pose=(-1.0, 0.0, 0.0),
        goal_poses=(goal,),
        navigation=navigation,
        control=make_control(),
    )

    assert assessment.ok is False
    assert assessment.acceptance_mode == "rejected"
    assert assessment.failure_mode == \
        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
    assert assessment.blocking_entity_ids == ("box_1",)
    assert assessment.introduced_blocking_entity_ids == ("box_1",)
    assert assessment.baseline_assessment["ok"] is True
