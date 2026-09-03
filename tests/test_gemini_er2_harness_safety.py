from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2 import harness_safety
from task_recursive_tree.integrations.gemini_er2.composite_payload import (
    COMPOSITE_PAYLOAD_MODEL_VERSION,
)
from task_recursive_tree.integrations.gemini_er2.harness_safety import (
    BASE_ROTATION_SAMPLE,
    BASE_TRANSLATION_SAMPLE,
    HELD_DEPARTURE_CANDIDATE_STEP,
    HELD_DEPARTURE_MAX_DISTANCE,
    HELD_ROTATION_SAMPLE,
    HELD_TRANSLATION_SAMPLE,
    install_harness_safety,
    safety_policy_artifact,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)


def test_hardening_catches_real_narrow_aisle_rotation_window() -> None:
    navigation = import_harness_module(
        "er2sim.navigation_capabilities"
    )
    original = getattr(
        navigation,
        "_TASK_RECURSIVE_TREE_ORIGINAL_ROUTE_POSE_EDGE_CLEAR",
        navigation.route_pose_edge_clear,
    )
    obstacle = navigation.BaseRouteObstacle(
        entity_id="aisle_right",
        min_x=1.10,
        max_x=1.30,
        min_y=-1.24,
        max_y=-0.20,
        bottom_z=0.0,
        top_z=0.60,
    )
    start = (
        0.7721934035543795,
        -1.345416314072568,
        math.pi / 2.0,
    )
    end = (start[0], start[1], 0.0)

    assert original(
        start,
        end,
        (obstacle,),
        rotation_sample=math.radians(2.0),
    ) == (True, None)

    install_harness_safety()

    assert navigation.route_pose_edge_clear(
        start,
        end,
        (obstacle,),
    ) == (False, "aisle_right")


def test_hardening_catches_thin_corner_translation_window() -> None:
    navigation = import_harness_module(
        "er2sim.navigation_capabilities"
    )
    original = getattr(
        navigation,
        "_TASK_RECURSIVE_TREE_ORIGINAL_ROUTE_POSE_EDGE_CLEAR",
        navigation.route_pose_edge_clear,
    )
    obstacle = navigation.BaseRouteObstacle(
        entity_id="thin_corner",
        min_x=0.196,
        max_x=0.197,
        min_y=0.053,
        max_y=0.054,
        bottom_z=0.0,
        top_z=0.10,
    )
    start = (0.0, 0.0, 0.0)
    end = (0.04, -0.04, 0.0)

    assert original(
        start,
        end,
        (obstacle,),
        translation_sample=0.02,
    ) == (True, None)

    install_harness_safety()

    assert navigation.route_pose_edge_clear(
        start,
        end,
        (obstacle,),
    ) == (False, "thin_corner")


def test_installation_is_idempotent_and_patches_imported_call_sites() -> None:
    first = install_harness_safety()
    second = install_harness_safety()
    navigation = import_harness_module(
        "er2sim.navigation_capabilities"
    )
    planner = import_harness_module("er2sim.navigation_planner")
    macro_actions = import_harness_module("er2sim.macro_actions")
    tree_executor = import_harness_module("er2sim.tree_executor")
    placement = import_harness_module(
        "er2sim.placement_capabilities"
    )
    control = import_harness_module("er2sim.control")
    motion = import_harness_module("er2sim.motion_planning")
    scene = import_harness_module("er2sim.scene")

    assert first.policy_fingerprint == second.policy_fingerprint
    assert second.already_installed is True
    assert planner.route_pose_edge_clear is \
        navigation.route_pose_edge_clear
    assert planner.route_pose_path_clear is \
        navigation.route_pose_path_clear
    assert macro_actions.route_pose_path_clear is \
        navigation.route_pose_path_clear
    assert tree_executor.route_pose_path_clear is \
        navigation.route_pose_path_clear
    assert macro_actions.assess_empty_base_motion_ready is \
        control.assess_empty_base_motion_ready
    assert navigation.DEFAULT_FOOTPRINT_TRANSLATION_SAMPLE == \
        BASE_TRANSLATION_SAMPLE
    assert navigation.DEFAULT_FOOTPRINT_ROTATION_SAMPLE == \
        BASE_ROTATION_SAMPLE
    assert placement.BASE_PATH_SAMPLE_STEP == HELD_TRANSLATION_SAMPLE
    assert placement.BASE_YAW_SAMPLE_STEP == HELD_ROTATION_SAMPLE
    assert control.MOTION_PLANNING_ENABLED is True
    assert control.MOTION_PLANNING_FALLBACK is False
    assert hasattr(
        motion.MujocoPlanningSnapshot,
        "_TASK_RECURSIVE_TREE_ORIGINAL_EDGE_VALID",
    )
    assert hasattr(
        motion.RRTConnectBackend,
        "_TASK_RECURSIVE_TREE_ORIGINAL_DENSIFY",
    )
    assert macro_actions.EXECUTORS["recover_workspace"] is \
        macro_actions.execute_recover_workspace
    assert getattr(
        scene.SimScene.manipulator_collision_assessment,
        "_task_recursive_tree_policy_fingerprint",
    ) == first.policy_fingerprint
    assert (
        safety_policy_artifact()["composite_payload_model_version"]
        == COMPOSITE_PAYLOAD_MODEL_VERSION
    )
    assert safety_policy_artifact()["held_execution_contact"] == (
        "explicit_held_entity_allowance"
    )
    assert safety_policy_artifact()["arm_collision_margin"] == \
        pytest.approx(motion.DEFAULT_PLANNER_COLLISION_MARGIN)


def test_held_departure_search_grid_is_separate_from_sweep_sampling() -> None:
    captured = {}

    def original(
        scene,
        object_id,
        target_yaw,
        *,
        minimum_distance,
        max_distance,
        step,
    ):
        captured.update(
            scene=scene,
            object_id=object_id,
            target_yaw=target_yaw,
            minimum_distance=minimum_distance,
            max_distance=max_distance,
            step=step,
        )
        return {"ok": False}

    hardened = harness_safety._make_hardened_held_departure(
        original,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )

    hardened(object(), "box_1", math.pi / 4.0)

    assert captured["max_distance"] == pytest.approx(
        HELD_DEPARTURE_MAX_DISTANCE
    )
    assert captured["minimum_distance"] == pytest.approx(0.0)
    assert captured["step"] == pytest.approx(
        HELD_DEPARTURE_CANDIDATE_STEP
    )
    assert captured["step"] > HELD_TRANSLATION_SAMPLE

    hardened(
        object(),
        "box_1",
        0.0,
        minimum_distance=1.5,
        max_distance=2.0,
        step=0.10,
    )

    assert captured["max_distance"] == pytest.approx(
        HELD_DEPARTURE_MAX_DISTANCE
    )
    assert captured["minimum_distance"] == pytest.approx(
        HELD_DEPARTURE_MAX_DISTANCE
    )
    assert captured["step"] == pytest.approx(
        HELD_DEPARTURE_CANDIDATE_STEP
    )


def test_keyword_compatibility_check_uses_actual_wrapper_signature() -> None:
    def original(scene, *, existing=None, added=None):
        return scene, existing, added

    def stale_wrapper(scene, *, existing=None):
        return scene, existing

    stale_wrapper.__wrapped__ = original

    with pytest.raises(
        RuntimeError,
        match=r"collision_assessment.*added",
    ):
        harness_safety._assert_keyword_compatible(
            stale_wrapper,
            original,
            label="collision_assessment",
        )


def test_recovery_collision_assessment_uses_planner_margin() -> None:
    class Configuration:
        @staticmethod
        def vector():
            return (0.2, 0.1, 0.0)

    class Snapshot:
        margins = []
        sync_calls = []

        def __init__(self, live_scene, *, collision_margin=0.005):
            self.live_scene = live_scene
            self.collision_margin = float(collision_margin)
            self.last_collision = None
            self.margins.append(self.collision_margin)

        def sync(self, **kwargs):
            self.sync_calls.append(dict(kwargs))
            return None

        @staticmethod
        def current_configuration():
            return Configuration()

        @staticmethod
        def within_bounds(_configuration):
            return True

        def state_valid(self, _configuration):
            if self.collision_margin < 0.007:
                return True
            self.last_collision = (
                "link_gripper_finger_right",
                "table",
            )
            return False

    motion = SimpleNamespace(MujocoPlanningSnapshot=Snapshot)
    scene = SimpleNamespace(
        securely_attached_entity=lambda: "cup_1",
    )
    baseline_calls = []

    def original(
        live_scene,
        *,
        allowed_contact_entity_ids=(),
        allowed_contact_robot_body_names=None,
        held_entity_id=None,
    ):
        baseline_calls.append({
            "allowed_contact_entity_ids": tuple(
                allowed_contact_entity_ids
            ),
            "allowed_contact_robot_body_names": (
                None
                if allowed_contact_robot_body_names is None
                else tuple(allowed_contact_robot_body_names)
            ),
            "held_entity_id": held_entity_id,
        })
        snapshot = Snapshot(live_scene, collision_margin=0.005)
        snapshot.sync(
            allowed_contact_entity_ids=allowed_contact_entity_ids,
            allowed_contact_robot_body_names=(
                allowed_contact_robot_body_names
            ),
            held_entity_id=held_entity_id,
        )
        current = snapshot.current_configuration()
        return {
            "ok": snapshot.state_valid(current.vector()),
            "collisions": [],
        }

    hardened = (
        harness_safety._make_hardened_manipulator_collision_assessment(
            motion,
            original,
            harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
        )
    )

    assessment = hardened(
        scene,
        allowed_contact_entity_ids=("apple_1",),
        allowed_contact_robot_body_names=(
            "rubber_tip_left",
            "rubber_tip_right",
        ),
        held_entity_id="cup_1",
    )

    assert Snapshot.margins == pytest.approx([0.005, 0.007])
    expected_contacts = {
        "allowed_contact_entity_ids": ("apple_1",),
        "allowed_contact_robot_body_names": (
            "rubber_tip_left",
            "rubber_tip_right",
        ),
        "held_entity_id": "cup_1",
    }
    assert baseline_calls == [expected_contacts]
    assert Snapshot.sync_calls == [
        expected_contacts,
        expected_contacts,
    ]
    assert assessment["ok"] is False
    assert assessment["collision_margin"] == pytest.approx(0.007)
    assert assessment["collisions"][0]["pair"] == [
        "link_gripper_finger_right",
        "table",
    ]


def test_workspace_recovery_moves_clears_collision_and_preserves_hold(
) -> None:
    control = import_harness_module("er2sim.control")
    scene = _RecoveryScene()
    runtime = SimpleNamespace(scene=scene)

    def original(_txn, _request, action_runtime, _cancel_event):
        action_runtime.scene.arm_extend -= 0.08
        action_runtime.scene.collision_active = False
        return {
            "phases": ["escape_retract"],
            "residual_state": {"safe_checkpoint": "after_move"},
        }

    hardened = harness_safety._make_hardened_recover_workspace(
        control,
        original,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )

    report = hardened(None, object(), runtime, None)

    audit = report["residual_state"][
        "safety_recovery_postcondition"
    ]
    assert audit["before_assessment"]["ok"] is False
    assert audit["after_assessment"]["ok"] is True
    assert audit["max_abs_joint_delta"] == pytest.approx(0.08)
    assert audit["held_entity_id_before"] == "cup_1"
    assert audit["held_entity_id_after"] == "cup_1"
    assert audit["rejection_reasons"] == []


@pytest.mark.parametrize(
    ("clear_collision", "keep_hold", "move_arm", "expected_reason"),
    [
        (False, True, True, "recovery_faults_remain"),
        (True, False, True, "secure_held_object_lost"),
        (True, True, False, "unsafe_state_without_motion"),
    ],
)
def test_workspace_recovery_fails_closed_on_postcondition_violation(
    clear_collision,
    keep_hold,
    move_arm,
    expected_reason,
) -> None:
    control = import_harness_module("er2sim.control")
    scene = _RecoveryScene()
    runtime = SimpleNamespace(scene=scene)

    def original(_txn, _request, action_runtime, _cancel_event):
        if move_arm:
            action_runtime.scene.arm_extend -= 0.05
        action_runtime.scene.collision_active = not clear_collision
        if not keep_hold:
            action_runtime.scene.held_entity_id = None
        return {"residual_state": {"safe_checkpoint": "after_move"}}

    hardened = harness_safety._make_hardened_recover_workspace(
        control,
        original,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )

    with pytest.raises(control.ControlError) as caught:
        hardened(None, object(), runtime, None)

    assert caught.value.code == "SAFETY_RECOVERY_FAILED"
    assert expected_reason in caught.value.details["rejection_reasons"]
    audit = caught.value.details["residual_state"][
        "safety_recovery_postcondition"
    ]
    assert expected_reason in audit["rejection_reasons"]


def test_planned_motion_execution_explicitly_allows_held_contact() -> None:
    captured = {}

    def original(
        _self,
        target,
        *,
        held_entity_id=None,
        allowed_contact_entity_ids=(),
        **kwargs,
    ):
        captured.update({
            "target": target,
            "held_entity_id": held_entity_id,
            "allowed_contact_entity_ids": allowed_contact_entity_ids,
            "kwargs": kwargs,
        })
        return {"ok": True}

    hardened = harness_safety._make_hardened_planned_motion(original)
    result = hardened(
        object(),
        (1.0, 2.0, 3.0),
        held_entity_id="can_1",
        allowed_contact_entity_ids=("floor_1", "can_1"),
        goal_seed={"lift": 0.2},
    )

    assert result == {"ok": True}
    assert captured["held_entity_id"] == "can_1"
    assert captured["allowed_contact_entity_ids"] == (
        "floor_1",
        "can_1",
    )
    assert captured["kwargs"]["goal_seed"] == {"lift": 0.2}


def test_semantic_floor_contact_is_held_and_geom_specific() -> None:
    install_harness_safety()
    motion = import_harness_module("er2sim.motion_planning")
    scene_module = import_harness_module("er2sim.scene")
    scene = scene_module.SimScene(headless=True)
    try:
        snapshot = motion.MujocoPlanningSnapshot(scene)
        snapshot.sync(
            held_entity_id="can_1",
            allowed_contact_entity_ids=("floor_1",),
            allowed_held_contact_entity_ids=("floor_1",),
        )
        mujoco = motion.mujoco

        def geom_id(name):
            return mujoco.mj_name2id(
                snapshot.model,
                mujoco.mjtObj.mjOBJ_GEOM,
                name,
            )

        can_geom = geom_id("can_1_geom")
        floor_geom = geom_id("floor")
        table_geom = geom_id("table_top")
        gripper_geom = next(
            geom_id
            for geom_id in range(snapshot.model.ngeom)
            if int(snapshot.model.geom_bodyid[geom_id])
            == snapshot._gripper_body
        )

        assert snapshot._allowed_contact_bodies == set()
        assert snapshot._allowed_held_contact_bodies == set()
        assert snapshot._contact_is_invalid(SimpleNamespace(
            dist=0.0,
            geom1=can_geom,
            geom2=floor_geom,
        )) is False
        assert snapshot._contact_is_invalid(SimpleNamespace(
            dist=0.0,
            geom1=can_geom,
            geom2=table_geom,
        )) is True
        assert snapshot._contact_is_invalid(SimpleNamespace(
            dist=0.0,
            geom1=gripper_geom,
            geom2=floor_geom,
        )) is True
    finally:
        scene.close()


def test_held_drive_repairs_unsafe_rotation_with_astar_departure() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _HeldNavigation()
    placement = _HeldPlacement()
    original_calls = []

    def original(*args, **kwargs):
        original_calls.append((args, kwargs))

    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        placement,
        original,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    hardened(
        supervisor,
        0.0,
        0.0,
        math.pi / 2.0,
        tol_pos=0.5,
    )

    assert original_calls == []
    assert len(navigation.plan_calls) == 2
    assert len(supervisor.follow_calls) == 1
    poses, kwargs = supervisor.follow_calls[0]
    assert any(pose[0] == pytest.approx(-0.2) for pose in poses)
    assert kwargs["stow"] is False
    assert kwargs["tol_pos"] == pytest.approx(0.04)
    assert supervisor.last_held_base_navigation["planner"] == (
        "se2_lattice_astar"
    )
    assert supervisor.last_held_base_navigation["anchor_drift"] == []
    assert perception.entity_pose("table_1") == (0.6, 0.0, 0.4)
    assert perception.entity_yaw("table_1") == 0.0


def test_held_drive_executes_selected_posture_before_base_path() -> None:
    control = import_harness_module("er2sim.control")
    macro_actions = import_harness_module("er2sim.macro_actions")
    scene = _PostureTransitionScene()
    runtime = SimpleNamespace(
        scene=scene,
        perception=_HeldPerception(),
        world=SimpleNamespace(),
    )
    navigation = _HeldNavigation()
    placement = _PostureTransitionPlacement()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        placement,
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
        posture_transition_executor=(
            macro_actions._execute_held_posture_transition
        ),
    )
    supervisor = _PostureTransitionSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    hardened(supervisor, 0.4, 0.0, 0.0)

    assert supervisor.events == [
        "held_departure:raise_clearance",
        "held_departure:retract_arm",
        "held_departure:align_wrist",
        "follow_base_path",
    ]
    assert supervisor.motion_calls[0][1][
        "allowed_held_contact_entity_ids"
    ] == ("table_1",)
    assert all(
        call[1]["allowed_held_contact_entity_ids"] == ()
        for call in supervisor.motion_calls[1:]
    )
    assert placement.assessment_calls[1]["arm_configuration"] == {
        "lift": 0.34,
        "arm_extend": 0.02,
        "wrist_yaw": 0.0,
    }
    assert "arm_configuration" not in placement.assessment_calls[2]
    report = supervisor.last_held_base_navigation
    assert report["posture_transition_execution"]["target_configuration"] == {
        "lift": 0.34,
        "arm_extend": 0.02,
        "wrist_yaw": 0.0,
    }
    assert report["posture_transition_revalidation"]["ok"] is True


def test_held_drive_revalidates_astar_sweep_before_execution() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _SweepMismatchNavigation()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        _AlwaysSafeHeldPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    hardened(supervisor, 0.4, 0.0, 0.0)

    assert len(navigation.plan_calls) == 2
    assert len(navigation.path_validation_calls) == 4
    assert navigation.path_validation_calls[0]["clear"] is False
    assert all(
        call["clear"] is True
        for call in navigation.path_validation_calls[1:]
    )
    poses, _kwargs = supervisor.follow_calls[0]
    assert any(pose[0] == pytest.approx(-0.2) for pose in poses)
    direct_attempt = supervisor.last_held_base_navigation["attempts"][0]
    assert direct_attempt["plan"]["reason_code"] == "PATH_SWEEP_BLOCKED"
    assert direct_attempt["plan"]["route_validation"][
        "blocking_entity_id"
    ] == "table_1"


def test_held_drive_searches_nonzero_base_safe_departure() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    runtime = SimpleNamespace(
        scene=scene,
        perception=_HeldPerception(),
        world=SimpleNamespace(),
    )
    navigation = _SweepMismatchNavigation()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        _ZeroDistancePrefixPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    hardened(supervisor, 0.4, 0.0, 0.0)

    attempt = supervisor.last_held_base_navigation["attempts"][1]
    prefix = attempt["departure_prefix"]
    assert prefix["base_aware"] is True
    assert prefix["base_aware_search"]["initial_distance"] == 0.0
    assert prefix["distance"] > 0.1
    assert prefix["base_route_validation"]["ok"] is True
    poses, _kwargs = supervisor.follow_calls[0]
    assert any(pose[0] < -0.1 for pose in poses)


def test_held_drive_keeps_provisional_astar_departure_heading() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    runtime = SimpleNamespace(
        scene=scene,
        perception=_HeldPerception(),
        world=SimpleNamespace(),
    )
    navigation = _ProvisionalSweepBlockedNavigation()
    placement = _RecordingFailedDeparturePlacement()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        placement,
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    with pytest.raises(control.ControlError):
        hardened(supervisor, 0.4, 0.0, 0.2)

    assert placement.departure_yaws[0] == pytest.approx(1.1)


def test_held_drive_structures_execution_path_blockage() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _HeldNavigation()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        _AlwaysSafeHeldPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )

    def reject_path():
        raise control.ControlError(
            "PATH_BLOCKED",
            "base footprint sweep blocked by obstacle table_1",
        )

    supervisor = _HeldSupervisor(scene, on_follow=reject_path)
    supervisor._task_recursive_tree_runtime = runtime

    with pytest.raises(control.ControlError) as caught:
        hardened(supervisor, 0.4, 0.0, 0.0)

    assert caught.value.code == "PATH_BLOCKED"
    assert caught.value.details["raw_failure_code"] == (
        "HELD_BASE_PATH_COLLISION"
    )
    assert caught.value.details["failure_mode"] == (
        "HELD_BASE_PATH_COLLISION"
    )
    assert caught.value.details["recovery_kind"] == (
        "reposition_held_base"
    )
    assert caught.value.details["held_entity_id"] == "can_1"
    assert caught.value.details["blocking_entity_ids"] == ["table_1"]
    assert supervisor.last_held_base_navigation["controller_error"][
        "code"
    ] == "PATH_BLOCKED"


def test_held_drive_preserves_noncollision_path_tracking_failure() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    runtime = SimpleNamespace(
        scene=scene,
        perception=_HeldPerception(),
        world=SimpleNamespace(),
    )

    def reject_tracking():
        raise control.ControlError(
            "PATH_BLOCKED",
            "local path tracker missed waypoint",
        )

    supervisor = _HeldSupervisor(scene, on_follow=reject_tracking)
    supervisor._task_recursive_tree_runtime = runtime
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        _HeldNavigation(),
        _AlwaysSafeHeldPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )

    with pytest.raises(control.ControlError) as caught:
        hardened(supervisor, 0.4, 0.0, 0.0)

    assert caught.value.code == "PATH_BLOCKED"
    assert caught.value.message == "local path tracker missed waypoint"
    assert "raw_failure_code" not in caught.value.details
    assert supervisor.last_held_base_navigation["controller_error"][
        "message"
    ] == "local path tracker missed waypoint"


def test_held_drive_relaxes_colliding_pure_rotation_goal_within_tolerance(
) -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _HeldGoalCollisionNavigation()
    placement = _HeldPlacement()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        placement,
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    hardened(
        supervisor,
        0.0,
        0.0,
        math.pi / 2.0,
        tol_pos=0.5,
    )

    assert len(navigation.plan_calls) == 1
    poses, _kwargs = supervisor.follow_calls[0]
    assert poses[-1] == pytest.approx((-0.2, 0.0, math.pi / 2.0))
    assert (0.0, 0.0, math.pi / 2.0) not in poses
    report = supervisor.last_held_base_navigation
    relaxation = report["goal_relaxation"]
    assert relaxation["reason_code"] == "GOAL_POSE_IN_COLLISION"
    assert relaxation["position_error_m"] == pytest.approx(0.2)
    assert relaxation["position_tolerance_m"] == pytest.approx(0.5)
    assert report["requested_goal_pose"] == pytest.approx(
        (0.0, 0.0, math.pi / 2.0)
    )
    assert report["effective_goal_pose"] == pytest.approx(
        (-0.2, 0.0, math.pi / 2.0)
    )
    assert perception.entity_pose("table_1") == (0.6, 0.0, 0.4)
    assert perception.entity_yaw("table_1") == 0.0


def test_held_drive_rejects_goal_relaxation_beyond_position_tolerance(
) -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _HeldGoalCollisionNavigation()
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        _HeldPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    with pytest.raises(control.ControlError) as caught:
        hardened(
            supervisor,
            0.0,
            0.0,
            math.pi / 2.0,
            tol_pos=0.1,
        )

    assert caught.value.code == "PATH_BLOCKED"
    assert caught.value.details["direct_goal_reason_code"] == (
        "GOAL_POSE_IN_COLLISION"
    )
    assert caught.value.details["near_pure_rotation"] is True
    assert len(navigation.plan_calls) == 2
    assert supervisor.follow_calls == []


def test_held_drive_rejects_non_movable_route_anchor_drift() -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = _HeldPerception()
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    navigation = _HeldNavigation()
    placement = _HeldPlacement()

    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        navigation,
        placement,
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(
        scene,
        on_follow=lambda: perception.poses.__setitem__(
            "table_1",
            (0.62, 0.0, 0.4),
        ),
    )
    supervisor._task_recursive_tree_runtime = runtime

    with pytest.raises(control.ControlError) as caught:
        hardened(
            supervisor,
            0.0,
            0.0,
            math.pi / 2.0,
            tol_pos=0.5,
        )

    assert caught.value.code == "SAFETY_REJECTED"
    assert caught.value.details["raw_failure_code"] == (
        "NON_MOVABLE_ANCHOR_DRIFT"
    )
    assert caught.value.details["anchor_drift"][0]["entity_id"] == (
        "table_1"
    )
    assert supervisor.last_held_base_navigation["anchor_drift"][0][
        "translation_error_m"
    ] == pytest.approx(0.02)


@pytest.mark.parametrize(
    "failure_mode",
    ["pose_unavailable", "yaw_reader_failed"],
)
def test_held_drive_fails_closed_when_anchor_baseline_is_unavailable(
    failure_mode,
) -> None:
    control = import_harness_module("er2sim.control")
    scene = _HeldBaseScene()
    perception = (
        _UnavailableAnchorPerception()
        if failure_mode == "pose_unavailable"
        else _FailingAnchorYawPerception()
    )
    runtime = SimpleNamespace(
        scene=scene,
        perception=perception,
        world=SimpleNamespace(),
    )
    hardened = harness_safety._make_hardened_held_drive_base_to(
        control,
        _HeldNavigation(),
        _HeldPlacement(),
        lambda *_args, **_kwargs: None,
        harness_safety.DEFAULT_HARNESS_SAFETY_POLICY,
    )
    supervisor = _HeldSupervisor(scene)
    supervisor._task_recursive_tree_runtime = runtime

    with pytest.raises(control.ControlError) as caught:
        hardened(
            supervisor,
            0.0,
            0.0,
            math.pi / 2.0,
            tol_pos=0.5,
        )

    assert caught.value.code == "SAFETY_REJECTED"
    assert caught.value.details["anchor_drift"][0]["reason"] == (
        "baseline_pose_unavailable"
    )
    assert supervisor.follow_calls == []


def test_empty_base_posture_uses_semantic_finger_pair_opening() -> None:
    install_harness_safety()
    control = import_harness_module("er2sim.control")
    scene = _PostureScene(
        finger_left=0.0175,
        finger_right=0.0610,
    )

    assessment = control.assess_empty_base_motion_ready(scene)

    assert assessment["ok"] is True
    assert assessment["faults"] == []
    assert assessment["finger_pair_assessment"]["semantic"] == \
        "opening_sum"


def test_empty_base_posture_rejects_a_truly_closed_finger_pair() -> None:
    install_harness_safety()
    control = import_harness_module("er2sim.control")
    scene = _PostureScene(
        finger_left=0.005,
        finger_right=0.010,
    )

    assessment = control.assess_empty_base_motion_ready(scene)

    assert assessment["ok"] is False
    assert {
        fault["code"] for fault in assessment["faults"]
    } == {"GRIPPER_NOT_OPEN_FOR_BASE_MOTION"}


def test_suspended_release_accepts_stable_semantic_pair_clearance() -> None:
    install_harness_safety()
    control = import_harness_module("er2sim.control")
    supervisor = object.__new__(control.MotionSupervisor)
    supervisor.current_phase = "place_open_suspended_gripper"
    supervisor.scene = _ReleaseOpenScene(
        finger_left=0.0428,
        finger_right=0.0284,
        grip=0.0396,
    )
    calls = []

    def move_arm(targets, **kwargs):
        calls.append((dict(targets), dict(kwargs)))
        stop_condition = kwargs["stop_condition"]
        assert stop_condition() is False
        supervisor.scene.now = 0.21
        assert stop_condition() is True

    supervisor.move_arm = move_arm

    supervisor.open_gripper(
        gap=0.10,
        contact_entity_id="apple_3",
        allow_contact=True,
    )

    assert len(calls) == 1
    assert calls[0][1]["settle"] == 0.2
    assert calls[0][1]["finger_contact_entity_id"] == "apple_3"
    assert calls[0][1]["finger_opening_contact_is_terminal"] is True
    assert sum(
        calls[0][0][name] for name in control.FINGER_JOINTS
    ) == pytest.approx(0.05)


def test_non_release_open_gripper_keeps_harness_completion_rules() -> None:
    install_harness_safety()
    control = import_harness_module("er2sim.control")
    supervisor = object.__new__(control.MotionSupervisor)
    supervisor.current_phase = "grasp_approach"
    supervisor.scene = _ReleaseOpenScene(
        finger_left=0.02,
        finger_right=0.02,
        grip=0.04,
    )
    calls = []

    def move_arm(targets, **kwargs):
        calls.append((dict(targets), dict(kwargs)))

    supervisor.move_arm = move_arm

    supervisor.open_gripper(
        gap=0.10,
        contact_entity_id="apple_3",
        allow_contact=True,
    )

    assert len(calls) == 1
    assert "stop_condition" not in calls[0][1]
    assert calls[0][1]["finger_contact_entity_id"] == "apple_3"
    assert calls[0][1]["finger_opening_contact_is_terminal"] is True
    assert sum(
        calls[0][0][name] for name in control.FINGER_JOINTS
    ) == pytest.approx(0.05)


def test_suspended_release_timeout_reports_pair_and_grip_state() -> None:
    install_harness_safety()
    control = import_harness_module("er2sim.control")
    supervisor = object.__new__(control.MotionSupervisor)
    supervisor.current_phase = "place_open_suspended_gripper"
    supervisor.scene = _ReleaseOpenScene(
        finger_left=0.04,
        finger_right=0.04,
        grip=0.02,
    )

    def move_arm(_targets, **kwargs):
        assert kwargs["stop_condition"]() is False
        supervisor.scene.now = 40.0
        assert kwargs["stop_condition"]() is False
        raise control.ToolTimeout("arm_motion")

    supervisor.move_arm = move_arm

    with pytest.raises(control.ToolTimeout) as caught:
        supervisor.open_gripper()

    report = caught.value.residual_state[
        "suspended_release_opening"
    ]
    assert report["current_sum"] == pytest.approx(0.08)
    assert report["minimum_sum"] == pytest.approx(0.07)
    assert report["grip_position"] == pytest.approx(0.02)
    assert report["grip_target"] == pytest.approx(control.GRIP_OPEN)
    assert report["ready"] is False
    assert report["ready_duration"] == 0.0


class _RecoveryScene:
    def __init__(self) -> None:
        self.collision_active = True
        self.held_entity_id = "cup_1"
        self.lift = 0.3
        self.arm_extend = 0.16
        self.joints = {
            "wrist_yaw": 0.0,
            "grip": 0.04,
            "joint_gripper_finger_left_open": 0.02,
            "joint_gripper_finger_right_open": 0.02,
        }

    def recovery_assessment(self):
        collisions = (
            [{
                "code": "MANIPULATOR_COLLISION",
                "component": "manipulator",
                "pair": [
                    "link_gripper_finger_right",
                    "table",
                ],
            }]
            if self.collision_active
            else []
        )
        return {
            "ok": not collisions,
            "faults": collisions,
            "collision_assessment": {
                "ok": not collisions,
                "collisions": collisions,
            },
        }

    def securely_attached_entity(self):
        return self.held_entity_id

    def lift_position(self):
        return self.lift

    def arm_extension(self):
        return self.arm_extend

    def joint_pos(self, name):
        return self.joints[name]


class _PostureScene:
    def __init__(
        self,
        *,
        finger_left: float,
        finger_right: float,
    ) -> None:
        self._joints = {
            "wrist_yaw": 0.0,
            "joint_gripper_finger_left_open": finger_left,
            "joint_gripper_finger_right_open": finger_right,
        }
        self.model = SimpleNamespace()

    def securely_attached_entity(self):
        return None

    def arm_extension(self):
        return 0.0

    def lift_position(self):
        control = import_harness_module("er2sim.control")
        return control.TRANSIT_LIFT

    def joint_pos(self, name):
        return self._joints[name]

    def manipulator_collision_assessment(self):
        return {"ok": True, "collisions": []}


class _ReleaseOpenScene:
    def __init__(
        self,
        *,
        finger_left: float,
        finger_right: float,
        grip: float,
    ) -> None:
        self.now = 0.0
        self._joints = {
            "joint_gripper_finger_left_open": finger_left,
            "joint_gripper_finger_right_open": finger_right,
            "grip": grip,
        }

    def joint_pos(self, name):
        return self._joints[name]

    def time(self):
        return self.now


class _HeldBaseScene:
    def securely_attached_entity(self):
        return "can_1"

    def gripper_holding_entity(self):
        return "can_1"

    def base_pose(self):
        return ((0.0, 0.0, 0.0), 0.0)

    @staticmethod
    def base_position_ok(x, y):
        return math.isfinite(x) and math.isfinite(y)


class _HeldPerception:
    def __init__(self):
        self.catalog = {
            "table_1": {
                "kind": "workspace",
                "category": "table",
                "movable": False,
            }
        }
        self.poses = {"table_1": (0.6, 0.0, 0.4)}
        self.yaws = {"table_1": 0.0}

    def entity_pose(self, entity_id):
        return self.poses.get(entity_id)

    def entity_yaw(self, entity_id):
        return self.yaws.get(entity_id, 0.0)


class _UnavailableAnchorPerception(_HeldPerception):
    def entity_pose(self, entity_id):
        if entity_id == "table_1":
            return None
        return super().entity_pose(entity_id)


class _FailingAnchorYawPerception(_HeldPerception):
    def entity_yaw(self, entity_id):
        if entity_id == "table_1":
            raise RuntimeError("yaw sensor unavailable")
        return super().entity_yaw(entity_id)


class _HeldSupervisor:
    def __init__(self, scene, on_follow=None):
        self.scene = scene
        self.base_timeout = 90.0
        self.follow_calls = []
        self.on_follow = on_follow

    def follow_base_path(self, poses, **kwargs):
        self.follow_calls.append((list(poses), dict(kwargs)))
        if self.on_follow is not None:
            self.on_follow()
        return {
            "controller": "test_continuous_controller",
            "poses_executed": len(poses),
        }


class _HeldNavigation:
    DEFAULT_BASE_FOOTPRINT = object()

    def __init__(self):
        self.plan_calls = []
        self.path_validation_calls = []

    @staticmethod
    def build_base_route_obstacles(*args, **kwargs):
        return ("table_1",)

    def plan_base_route(self, start, goal, obstacles, **kwargs):
        self.plan_calls.append((start, goal, obstacles, kwargs))
        poses = [start, goal]
        return {
            "selected": [(pose[0], pose[1]) for pose in poses],
            "poses_world": [list(pose) for pose in poses],
            "planner": "se2_lattice_astar",
            "reason_code": None,
        }

    @staticmethod
    def route_pose_clear(*args, **kwargs):
        return True, None

    def route_pose_path_clear(self, poses, obstacles, **kwargs):
        self.path_validation_calls.append({
            "poses": list(poses),
            "obstacles": tuple(obstacles),
            "kwargs": dict(kwargs),
            "clear": True,
        })
        return True, None


class _HeldGoalCollisionNavigation(_HeldNavigation):
    def plan_base_route(self, start, goal, obstacles, **kwargs):
        self.plan_calls.append((start, goal, obstacles, kwargs))
        if (
            math.dist(goal[:2], (0.0, 0.0)) <= 1e-9
            and abs(
                harness_safety._normalize_angle(goal[2] - math.pi / 2.0)
            )
            <= 1e-9
        ):
            return {
                "selected": None,
                "poses_world": [],
                "planner": "se2_lattice_astar",
                "reason_code": "GOAL_POSE_IN_COLLISION",
                "direct_blocking_entity_ids": ["table_1"],
            }
        poses = [start, goal]
        return {
            "selected": [(pose[0], pose[1]) for pose in poses],
            "poses_world": [list(pose) for pose in poses],
            "planner": "se2_lattice_astar",
            "reason_code": None,
        }


class _SweepMismatchNavigation(_HeldNavigation):
    def route_pose_path_clear(self, poses, obstacles, **kwargs):
        normalized = [tuple(float(value) for value in pose) for pose in poses]
        clear = any(pose[0] < -0.1 for pose in normalized)
        self.path_validation_calls.append({
            "poses": normalized,
            "obstacles": tuple(obstacles),
            "kwargs": dict(kwargs),
            "clear": clear,
        })
        return (True, None) if clear else (False, "table_1")


class _ProvisionalSweepBlockedNavigation(_HeldNavigation):
    def plan_base_route(self, start, goal, obstacles, **kwargs):
        self.plan_calls.append((start, goal, obstacles, kwargs))
        poses = [
            start,
            (0.1, 0.0, 1.1),
            goal,
        ]
        return {
            "selected": [(pose[0], pose[1]) for pose in poses],
            "poses_world": [list(pose) for pose in poses],
            "planner": "se2_lattice_astar",
            "reason_code": None,
        }

    def route_pose_path_clear(self, poses, obstacles, **kwargs):
        normalized = [tuple(float(value) for value in pose) for pose in poses]
        self.path_validation_calls.append({
            "poses": normalized,
            "obstacles": tuple(obstacles),
            "kwargs": dict(kwargs),
            "clear": False,
        })
        return False, "table_1"


class _HeldPlacement:
    @staticmethod
    def held_departure_prefix(
        scene,
        object_id,
        target_yaw,
        **_kwargs,
    ):
        return {
            "ok": True,
            "poses": [
                [0.0, 0.0, 0.0],
                [-0.2, 0.0, 0.0],
            ],
            "escape_pose": [-0.2, 0.0, 0.0],
            "target_yaw": target_yaw,
            "reason_codes": [],
        }

    @staticmethod
    def assess_held_base_path(
        scene,
        object_id,
        poses,
        interaction_point,
    ):
        repaired = any(float(pose[0]) < -0.1 for pose in poses)
        if repaired:
            return {"ok": True, "reason_codes": []}
        return {
            "ok": False,
            "reason_codes": ["HELD_PATH_COLLISION"],
            "issue": {
                "code": "COLLISION",
                "collision_pair": ["can_1", "table_1"],
            },
        }


class _AlwaysSafeHeldPlacement(_HeldPlacement):
    @staticmethod
    def assess_held_base_path(
        scene,
        object_id,
        poses,
        interaction_point,
    ):
        return {"ok": True, "reason_codes": []}


class _PostureTransitionScene(_HeldBaseScene):
    def __init__(self):
        self.lift = 0.26
        self.arm_extend = 0.04
        self.wrist_yaw = 0.18
        self.following = False

    def lift_position(self):
        return self.lift

    def arm_extension(self):
        return self.arm_extend

    def joint_pos(self, name):
        assert name == "wrist_yaw"
        return self.wrist_yaw

    def start_object_kin_follow(self, entity_id):
        assert entity_id == "can_1"
        self.following = True

    def stop_object_kin_follow(self):
        self.following = False

    @staticmethod
    def manipulator_collision_assessment(**_kwargs):
        return {"ok": True, "collisions": []}


class _PostureTransitionSupervisor(_HeldSupervisor):
    def __init__(self, scene):
        super().__init__(scene)
        self.events = []
        self.motion_calls = []

    def move_configuration_planned(self, target, **kwargs):
        self.events.append(str(kwargs["execution_stage"]))
        self.motion_calls.append((dict(target), dict(kwargs)))
        self.scene.lift = float(target["lift"])
        self.scene.arm_extend = float(target["arm_extend"])
        self.scene.wrist_yaw = float(target["wrist_yaw"])
        return {"planner": "test"}

    def follow_base_path(self, poses, **kwargs):
        self.events.append("follow_base_path")
        return super().follow_base_path(poses, **kwargs)


class _PostureTransitionPlacement:
    def __init__(self):
        self.assessment_calls = []

    @staticmethod
    def held_departure_prefix(
        scene,
        object_id,
        target_yaw,
        **_kwargs,
    ):
        del object_id
        (base, current_yaw) = scene.base_pose()
        start = [float(base[0]), float(base[1]), float(current_yaw)]
        escape = [-0.2, 0.0, float(current_yaw)]
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
        return {
            "ok": True,
            "poses": [start, escape],
            "escape_pose": escape,
            "target_yaw": float(target_yaw),
            "distance": 0.2,
            "reason_codes": [],
            "posture_transition": transition,
        }

    def assess_held_base_path(
        self,
        scene,
        object_id,
        poses,
        interaction_point,
        **kwargs,
    ):
        del object_id, poses, interaction_point
        self.assessment_calls.append(dict(kwargs))
        configuration = kwargs.get("arm_configuration")
        if configuration is not None:
            return {"ok": True, "reason_codes": []}
        if (
            scene.lift == pytest.approx(0.34)
            and scene.arm_extend == pytest.approx(0.02)
            and scene.wrist_yaw == pytest.approx(0.0)
        ):
            return {"ok": True, "reason_codes": []}
        return {
            "ok": False,
            "reason_codes": ["HELD_PATH_COLLISION"],
            "issue": {
                "code": "COLLISION",
                "collision_pair": ["can_1", "table_1"],
            },
        }


class _ZeroDistancePrefixPlacement:
    @staticmethod
    def held_departure_prefix(
        scene,
        object_id,
        target_yaw,
        **_kwargs,
    ):
        del object_id
        (base, current_yaw) = scene.base_pose()
        start = [float(base[0]), float(base[1]), float(current_yaw)]
        return {
            "ok": True,
            "poses": [start],
            "escape_pose": start,
            "target_yaw": float(target_yaw),
            "distance": 0.0,
            "assessment": {"ok": True, "reason_codes": []},
            "reason_codes": [],
        }

    @staticmethod
    def assess_held_base_path(
        scene,
        object_id,
        poses,
        interaction_point,
    ):
        del scene, object_id, interaction_point
        if any(float(pose[0]) < -0.1 for pose in poses):
            return {"ok": True, "reason_codes": []}
        return {
            "ok": False,
            "reason_codes": ["HELD_PATH_COLLISION"],
        }


class _RecordingFailedDeparturePlacement:
    def __init__(self):
        self.departure_yaws = []

    def held_departure_prefix(
        self,
        scene,
        object_id,
        target_yaw,
        **_kwargs,
    ):
        del scene, object_id
        self.departure_yaws.append(float(target_yaw))
        return {
            "ok": False,
            "reason_codes": ["HELD_PATH_COLLISION"],
        }

    @staticmethod
    def assess_held_base_path(*_args, **_kwargs):
        raise AssertionError("blocked provisional route must not be assessed")
