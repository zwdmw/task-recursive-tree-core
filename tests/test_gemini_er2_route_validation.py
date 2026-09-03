from __future__ import annotations

from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2.route_validation import (
    validate_se2_route,
)


class FakeNavigation:
    DEFAULT_BASE_FOOTPRINT = object()
    DEFAULT_BASE_CLEARANCE = 0.3

    @staticmethod
    def route_pose_path_clear(*_args, **_kwargs):
        return True, None

    @staticmethod
    def residual_navigation_obstacles(*_args):
        return ()

    @staticmethod
    def path_clear(*_args, **_kwargs):
        return True, None


def test_dense_workspace_validation_checks_between_waypoints() -> None:
    checked: list[tuple[float, float]] = []

    def position_ok(x: float, y: float) -> bool:
        checked.append((x, y))
        return not 0.009 <= x <= 0.011

    evidence = validate_se2_route(
        navigation=FakeNavigation(),
        position_ok=position_ok,
        poses=((0.0, 0.0, 0.0), (0.04, 0.0, 0.0)),
        expected_start=(0.0, 0.0, 0.0),
        expected_goal=(0.04, 0.0, 0.0),
        navigation_obstacles=(),
        route_obstacles=(),
        endpoint_position_tolerance=0.001,
        endpoint_yaw_tolerance=0.001,
        translation_sample=0.005,
        rotation_sample=0.01,
    )

    assert evidence.ok is False
    assert evidence.reason_code == "NO_REACHABLE_POSE"
    assert evidence.failure_mode == "BASE_PATH_OUTSIDE_WORKSPACE"
    assert any(0.009 <= x <= 0.011 for x, _y in checked)


def test_endpoint_errors_are_structured() -> None:
    evidence = validate_se2_route(
        navigation=FakeNavigation(),
        position_ok=lambda _x, _y: True,
        poses=((0.0, 0.0, 0.0), (0.15, 0.0, 0.0)),
        expected_start=(0.0, 0.0, 0.0),
        expected_goal=(0.2, 0.0, 0.0),
        navigation_obstacles=(SimpleNamespace(entity_id="wall"),),
        route_obstacles=(),
        endpoint_position_tolerance=0.01,
        endpoint_yaw_tolerance=0.01,
    )

    assert evidence.ok is False
    assert evidence.reason_code == "ROUTE_GOAL_MISMATCH"
    assert evidence.failure_mode == "ROUTE_ENDPOINT_MISMATCH"
    assert evidence.goal_position_error == pytest.approx(0.05)
