from __future__ import annotations

import copy
import hashlib
import inspect
import json
import math
import sys
from dataclasses import dataclass, replace
from functools import wraps
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping, Sequence

from .composite_payload import (
    COMPOSITE_PAYLOAD_MODEL_VERSION,
    install_composite_payload_support,
)
from .paths import import_harness_module


HARNESS_SAFETY_MODEL_VERSION = "task_recursive_tree_harness_safety/3.5"


@dataclass(frozen=True)
class HarnessSafetyPolicy:
    base_translation_sample: float = 0.005
    base_rotation_sample: float = math.radians(0.5)
    held_translation_sample: float = 0.005
    held_rotation_sample: float = math.radians(0.5)
    held_departure_max_distance: float = 1.0
    held_departure_candidate_step: float = 0.04
    arm_edge_unit_resolution: float = 0.01
    arm_waypoint_unit_resolution: float = 0.01
    arm_collision_margin: float = 0.007
    recovery_motion_epsilon: float = 1e-6
    empty_finger_pair_tolerance: float = 0.01
    suspended_release_finger_pair_tolerance: float = 0.01
    suspended_release_settle_time: float = 0.2
    composite_payload_model_version: str = COMPOSITE_PAYLOAD_MODEL_VERSION
    refinement_depth: int = 8
    non_movable_anchor_translation_tolerance: float = 0.005
    non_movable_anchor_yaw_tolerance: float = math.radians(1.0)
    model_version: str = HARNESS_SAFETY_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "base_translation_sample": self.base_translation_sample,
            "base_rotation_sample": self.base_rotation_sample,
            "held_translation_sample": self.held_translation_sample,
            "held_rotation_sample": self.held_rotation_sample,
            "held_departure_max_distance": (
                self.held_departure_max_distance
            ),
            "held_departure_candidate_step": (
                self.held_departure_candidate_step
            ),
            "arm_edge_unit_resolution": self.arm_edge_unit_resolution,
            "arm_waypoint_unit_resolution": (
                self.arm_waypoint_unit_resolution
            ),
            "arm_collision_margin": self.arm_collision_margin,
            "recovery_motion_epsilon": self.recovery_motion_epsilon,
            "empty_finger_pair_tolerance": (
                self.empty_finger_pair_tolerance
            ),
            "suspended_release_finger_pair_tolerance": (
                self.suspended_release_finger_pair_tolerance
            ),
            "suspended_release_settle_time": (
                self.suspended_release_settle_time
            ),
            "composite_payload_model_version": (
                self.composite_payload_model_version
            ),
            "refinement_depth": self.refinement_depth,
            "non_movable_anchor_translation_tolerance": (
                self.non_movable_anchor_translation_tolerance
            ),
            "non_movable_anchor_yaw_tolerance": (
                self.non_movable_anchor_yaw_tolerance
            ),
            "base_motion_semantics": "rotate_then_translate",
            "base_sweep_certificate": (
                "adaptive_lipschitz_footprint_inflation"
            ),
            "empty_gripper_readiness": "semantic_pair_opening",
            "suspended_release_opening": "semantic_pair_clearance",
            "held_execution_contact": "explicit_held_entity_allowance",
            "workspace_recovery_collision": (
                "planner_margin_with_motion_and_hold_postconditions"
            ),
            "semantic_floor_contact": (
                "held_floor_geom_terminal_contact_only"
            ),
            "held_base_motion": (
                "se2_astar_with_composite_envelope_and_departure_repair"
            ),
            "non_movable_route_anchors": (
                "live_pose_drift_guard"
            ),
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


DEFAULT_HARNESS_SAFETY_POLICY = HarnessSafetyPolicy()
BASE_TRANSLATION_SAMPLE = (
    DEFAULT_HARNESS_SAFETY_POLICY.base_translation_sample
)
BASE_ROTATION_SAMPLE = DEFAULT_HARNESS_SAFETY_POLICY.base_rotation_sample
HELD_TRANSLATION_SAMPLE = (
    DEFAULT_HARNESS_SAFETY_POLICY.held_translation_sample
)
HELD_ROTATION_SAMPLE = DEFAULT_HARNESS_SAFETY_POLICY.held_rotation_sample
HELD_DEPARTURE_MAX_DISTANCE = (
    DEFAULT_HARNESS_SAFETY_POLICY.held_departure_max_distance
)
HELD_DEPARTURE_CANDIDATE_STEP = (
    DEFAULT_HARNESS_SAFETY_POLICY.held_departure_candidate_step
)
ARM_EDGE_UNIT_RESOLUTION = (
    DEFAULT_HARNESS_SAFETY_POLICY.arm_edge_unit_resolution
)
ARM_WAYPOINT_UNIT_RESOLUTION = (
    DEFAULT_HARNESS_SAFETY_POLICY.arm_waypoint_unit_resolution
)


@dataclass(frozen=True)
class HarnessSafetyInstallation:
    policy_fingerprint: str
    patched_modules: tuple[str, ...]
    already_installed: bool


ModuleLoader = Callable[..., ModuleType]


def safety_policy_artifact(
    policy: HarnessSafetyPolicy = DEFAULT_HARNESS_SAFETY_POLICY,
) -> dict[str, Any]:
    artifact = policy.to_dict()
    artifact["fingerprint"] = policy.fingerprint
    return artifact


def install_harness_safety(
    *,
    harness_root: str | None = None,
    module_loader: ModuleLoader = import_harness_module,
    policy: HarnessSafetyPolicy = DEFAULT_HARNESS_SAFETY_POLICY,
) -> HarnessSafetyInstallation:
    """Install process-local planning/execution consistency hardening."""
    modules = {
        name: module_loader(name, harness_root=harness_root)
        for name in (
            "er2sim.navigation_capabilities",
            "er2sim.navigation_planner",
            "er2sim.placement_capabilities",
            "er2sim.control",
            "er2sim.macro_actions",
            "er2sim.tree_executor",
            "er2sim.motion_planning",
            "er2sim.scene",
        )
    }
    navigation = modules["er2sim.navigation_capabilities"]
    placement = modules["er2sim.placement_capabilities"]
    control = modules["er2sim.control"]
    macro_actions = modules["er2sim.macro_actions"]
    motion = modules["er2sim.motion_planning"]
    scene = modules["er2sim.scene"]
    marker = "_TASK_RECURSIVE_TREE_SAFETY_POLICY_FINGERPRINT"
    already_installed = (
        getattr(navigation, marker, None) == policy.fingerprint
        and getattr(placement, marker, None) == policy.fingerprint
        and getattr(motion, marker, None) == policy.fingerprint
        and getattr(scene, marker, None) == policy.fingerprint
        and getattr(control, marker, None) == policy.fingerprint
        and getattr(macro_actions, marker, None) == policy.fingerprint
    )

    previous_edge = navigation.route_pose_edge_clear
    previous_path = navigation.route_pose_path_clear
    original_edge = _stored_original(
        navigation,
        "route_pose_edge_clear",
    )
    original_path = _stored_original(
        navigation,
        "route_pose_path_clear",
    )
    navigation_installed = (
        getattr(navigation, marker, None) == policy.fingerprint
    )
    if navigation_installed:
        hardened_edge = previous_edge
        hardened_path = previous_path
    else:
        hardened_edge = _make_hardened_route_edge(
            navigation,
            original_edge,
            policy,
        )
        hardened_path = _make_hardened_route_path(
            navigation,
            hardened_edge,
            original_path,
            policy,
        )
    _assert_keyword_compatible(
        hardened_edge,
        original_edge,
        label="navigation.route_pose_edge_clear",
    )
    _assert_keyword_compatible(
        hardened_path,
        original_path,
        label="navigation.route_pose_path_clear",
    )
    navigation.route_pose_edge_clear = hardened_edge
    navigation.route_pose_path_clear = hardened_path
    navigation.DEFAULT_FOOTPRINT_TRANSLATION_SAMPLE = (
        policy.base_translation_sample
    )
    navigation.DEFAULT_FOOTPRINT_ROTATION_SAMPLE = (
        policy.base_rotation_sample
    )
    setattr(navigation, marker, policy.fingerprint)

    placement.BASE_PATH_SAMPLE_STEP = policy.held_translation_sample
    placement.BASE_YAW_SAMPLE_STEP = policy.held_rotation_sample
    previous_departure = placement.held_departure_prefix
    original_departure = _stored_original(
        placement,
        "held_departure_prefix",
    )
    if getattr(placement, marker, None) == policy.fingerprint:
        hardened_departure = previous_departure
    else:
        hardened_departure = _make_hardened_held_departure(
            original_departure,
            policy,
        )
    _assert_keyword_compatible(
        hardened_departure,
        original_departure,
        label="placement.held_departure_prefix",
    )
    placement.held_departure_prefix = hardened_departure
    setattr(placement, marker, policy.fingerprint)

    if getattr(motion, marker, None) != policy.fingerprint:
        _install_arm_hardening(motion, policy)
    install_composite_payload_support(
        scene_module=scene,
        motion_module=motion,
        control_module=control,
        fingerprint=policy.fingerprint,
    )
    setattr(motion, marker, policy.fingerprint)

    scene_class = scene.SimScene
    previous_collision_assessment = (
        scene_class.manipulator_collision_assessment
    )
    original_collision_assessment = _stored_original(
        scene_class,
        "manipulator_collision_assessment",
    )
    if (
        getattr(
            previous_collision_assessment,
            "_task_recursive_tree_policy_fingerprint",
            None,
        )
        == policy.fingerprint
    ):
        hardened_collision_assessment = previous_collision_assessment
    else:
        hardened_collision_assessment = (
            _make_hardened_manipulator_collision_assessment(
                motion,
                original_collision_assessment,
                policy,
            )
        )
    _assert_keyword_compatible(
        hardened_collision_assessment,
        original_collision_assessment,
        label="SimScene.manipulator_collision_assessment",
    )
    scene_class.manipulator_collision_assessment = (
        hardened_collision_assessment
    )
    setattr(scene, marker, policy.fingerprint)

    control_installed = (
        getattr(control, marker, None) == policy.fingerprint
    )
    previous_empty_posture = control.assess_empty_base_motion_ready
    original_empty_posture = _stored_original(
        control,
        "assess_empty_base_motion_ready",
    )
    if control_installed:
        hardened_empty_posture = previous_empty_posture
    else:
        hardened_empty_posture = (
            _make_hardened_empty_base_motion_assessment(
                control,
                original_empty_posture,
                policy,
            )
        )
    _assert_keyword_compatible(
        hardened_empty_posture,
        original_empty_posture,
        label="control.assess_empty_base_motion_ready",
    )
    control.assess_empty_base_motion_ready = hardened_empty_posture
    supervisor_class = control.MotionSupervisor
    previous_drive_base_to = supervisor_class.drive_base_to
    original_drive_base_to = _stored_original(
        supervisor_class,
        "drive_base_to",
    )
    if (
        getattr(
            previous_drive_base_to,
            "_task_recursive_tree_policy_fingerprint",
            None,
        )
        == policy.fingerprint
    ):
        hardened_drive_base_to = previous_drive_base_to
    else:
        hardened_drive_base_to = _make_hardened_held_drive_base_to(
            control,
            navigation,
            placement,
            original_drive_base_to,
            policy,
            posture_transition_executor=getattr(
                macro_actions,
                "_execute_held_posture_transition",
                None,
            ),
        )
    _assert_keyword_compatible(
        hardened_drive_base_to,
        original_drive_base_to,
        label="MotionSupervisor.drive_base_to",
    )
    supervisor_class.drive_base_to = hardened_drive_base_to

    previous_open_gripper = supervisor_class.open_gripper
    original_open_gripper = _stored_original(
        supervisor_class,
        "open_gripper",
    )
    if control_installed:
        hardened_open_gripper = previous_open_gripper
    else:
        hardened_open_gripper = _make_hardened_open_gripper(
            control,
            original_open_gripper,
            policy,
        )
    _assert_keyword_compatible(
        hardened_open_gripper,
        original_open_gripper,
        label="MotionSupervisor.open_gripper",
    )
    supervisor_class.open_gripper = hardened_open_gripper
    for method_name in (
        "move_gripper_planned",
        "move_configuration_planned",
    ):
        previous_method = getattr(supervisor_class, method_name)
        original_method = _stored_original(
            supervisor_class,
            method_name,
        )
        hardened_method = (
            previous_method
            if control_installed
            else _make_hardened_planned_motion(original_method)
        )
        _assert_keyword_compatible(
            hardened_method,
            original_method,
            label=f"MotionSupervisor.{method_name}",
        )
        setattr(supervisor_class, method_name, hardened_method)
    control.MOTION_PLANNING_ENABLED = True
    control.MOTION_PLANNING_FALLBACK = False
    setattr(control, marker, policy.fingerprint)

    previous_supervisor_factory = macro_actions._supervisor
    original_supervisor_factory = _stored_original(
        macro_actions,
        "_supervisor",
    )
    if (
        getattr(
            previous_supervisor_factory,
            "_task_recursive_tree_policy_fingerprint",
            None,
        )
        == policy.fingerprint
    ):
        hardened_supervisor_factory = previous_supervisor_factory
    else:
        hardened_supervisor_factory = _make_runtime_supervisor_factory(
            original_supervisor_factory,
            policy,
        )
    _assert_keyword_compatible(
        hardened_supervisor_factory,
        original_supervisor_factory,
        label="macro_actions._supervisor",
    )
    macro_actions._supervisor = hardened_supervisor_factory

    previous_recover_workspace = (
        macro_actions.execute_recover_workspace
    )
    original_recover_workspace = _stored_original(
        macro_actions,
        "execute_recover_workspace",
    )
    if (
        getattr(
            previous_recover_workspace,
            "_task_recursive_tree_policy_fingerprint",
            None,
        )
        == policy.fingerprint
    ):
        hardened_recover_workspace = previous_recover_workspace
    else:
        hardened_recover_workspace = _make_hardened_recover_workspace(
            control,
            original_recover_workspace,
            policy,
        )
    _assert_keyword_compatible(
        hardened_recover_workspace,
        original_recover_workspace,
        label="macro_actions.execute_recover_workspace",
    )
    macro_actions.execute_recover_workspace = hardened_recover_workspace
    if isinstance(getattr(macro_actions, "EXECUTORS", None), dict):
        macro_actions.EXECUTORS["recover_workspace"] = (
            hardened_recover_workspace
        )
    setattr(macro_actions, marker, policy.fingerprint)

    candidates = _er2sim_modules(modules)
    _replace_function_references(
        candidates,
        "route_pose_edge_clear",
        original_edge,
        hardened_edge,
    )
    _replace_function_references(
        candidates,
        "route_pose_edge_clear",
        previous_edge,
        hardened_edge,
    )
    _replace_function_references(
        candidates,
        "route_pose_path_clear",
        original_path,
        hardened_path,
    )
    _replace_function_references(
        candidates,
        "route_pose_path_clear",
        previous_path,
        hardened_path,
    )
    _replace_function_references(
        candidates,
        "held_departure_prefix",
        original_departure,
        hardened_departure,
    )
    _replace_function_references(
        candidates,
        "held_departure_prefix",
        previous_departure,
        hardened_departure,
    )
    _replace_function_references(
        candidates,
        "assess_empty_base_motion_ready",
        original_empty_posture,
        hardened_empty_posture,
    )
    _replace_function_references(
        candidates,
        "assess_empty_base_motion_ready",
        previous_empty_posture,
        hardened_empty_posture,
    )
    _replace_function_references(
        candidates,
        "execute_recover_workspace",
        original_recover_workspace,
        hardened_recover_workspace,
    )
    _replace_function_references(
        candidates,
        "execute_recover_workspace",
        previous_recover_workspace,
        hardened_recover_workspace,
    )
    return HarnessSafetyInstallation(
        policy_fingerprint=policy.fingerprint,
        patched_modules=tuple(sorted(candidates)),
        already_installed=already_installed,
    )


def _stored_original(owner: Any, attribute: str) -> Any:
    stored_name = f"_TASK_RECURSIVE_TREE_ORIGINAL_{attribute.upper()}"
    original = getattr(owner, stored_name, None)
    if original is None:
        original = getattr(owner, attribute)
        setattr(owner, stored_name, original)
    return original


def _assert_keyword_compatible(
    wrapper: Callable[..., Any],
    original: Callable[..., Any],
    *,
    label: str,
) -> None:
    original_signature = inspect.signature(
        original,
        follow_wrapped=False,
    )
    wrapper_signature = inspect.signature(
        wrapper,
        follow_wrapped=False,
    )
    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in wrapper_signature.parameters.values()
    ):
        return
    missing = [
        name
        for index, (name, parameter) in enumerate(
            original_signature.parameters.items()
        )
        if not (
            index == 0
            and name in {"self", "cls"}
        )
        and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
        and name not in wrapper_signature.parameters
    ]
    if missing:
        raise RuntimeError(
            f"{label} safety wrapper does not accept keyword parameters: "
            f"{', '.join(sorted(missing))}"
        )


def _accepts_keyword(
    callable_object: Callable[..., Any],
    name: str,
) -> bool:
    try:
        signature = inspect.signature(
            inspect.unwrap(callable_object),
            follow_wrapped=False,
        )
    except (TypeError, ValueError):
        return False
    return (
        name in signature.parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
    )


def _make_hardened_planned_motion(
    original: Callable[..., Any],
) -> Callable[..., Any]:
    @wraps(original)
    def hardened(
        self: Any,
        *args: Any,
        held_entity_id: Any = None,
        allowed_contact_entity_ids: Sequence[str] = (),
        **kwargs: Any,
    ) -> Any:
        allowed = [
            str(entity_id)
            for entity_id in allowed_contact_entity_ids
            if str(entity_id)
        ]
        if held_entity_id is not None and str(held_entity_id):
            allowed.append(str(held_entity_id))
        return original(
            self,
            *args,
            held_entity_id=held_entity_id,
            allowed_contact_entity_ids=tuple(dict.fromkeys(allowed)),
            **kwargs,
        )

    return hardened


def _make_runtime_supervisor_factory(
    original: Callable[..., Any],
    policy: HarnessSafetyPolicy,
) -> Callable[..., Any]:
    @wraps(original)
    def hardened(runtime: Any, cancel_event: Any) -> Any:
        supervisor = original(runtime, cancel_event)
        setattr(supervisor, "_task_recursive_tree_runtime", runtime)
        return supervisor

    setattr(
        hardened,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened


def _make_hardened_manipulator_collision_assessment(
    motion: ModuleType,
    original: Callable[..., Any],
    policy: HarnessSafetyPolicy,
) -> Callable[..., dict[str, Any]]:
    @wraps(original)
    def hardened(
        scene: Any,
        *,
        allowed_contact_entity_ids: Sequence[str] = (),
        allowed_contact_robot_body_names: Sequence[str] | None = None,
        held_entity_id: str | None = None,
        allowed_held_contact_entity_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        normalized_robot_body_names = (
            None
            if allowed_contact_robot_body_names is None
            else tuple(
                str(body_name)
                for body_name in allowed_contact_robot_body_names
            )
        )
        baseline_kwargs: dict[str, Any] = {
            "allowed_contact_entity_ids": tuple(
                str(entity_id)
                for entity_id in allowed_contact_entity_ids
            ),
            "allowed_contact_robot_body_names": (
                normalized_robot_body_names
            ),
            "held_entity_id": held_entity_id,
        }
        normalized_held_contacts = tuple(
            str(entity_id)
            for entity_id in allowed_held_contact_entity_ids
        )
        if normalized_held_contacts and _accepts_keyword(
            original,
            "allowed_held_contact_entity_ids",
        ):
            baseline_kwargs["allowed_held_contact_entity_ids"] = (
                normalized_held_contacts
            )
        baseline = original(
            scene,
            **baseline_kwargs,
        )
        baseline = (
            dict(baseline)
            if isinstance(baseline, Mapping)
            else {"ok": False, "collisions": []}
        )
        baseline["required_collision_margin"] = (
            policy.arm_collision_margin
        )
        if not bool(baseline.get("ok")):
            return baseline

        return _planner_margin_collision_assessment(
            scene,
            motion=motion,
            policy=policy,
            allowed_contact_entity_ids=allowed_contact_entity_ids,
            allowed_contact_robot_body_names=(
                normalized_robot_body_names
            ),
            held_entity_id=held_entity_id,
            allowed_held_contact_entity_ids=(
                allowed_held_contact_entity_ids
            ),
        )

    setattr(
        hardened,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened


def _planner_margin_collision_assessment(
    scene: Any,
    *,
    motion: ModuleType,
    policy: HarnessSafetyPolicy,
    allowed_contact_entity_ids: Sequence[str] = (),
    allowed_contact_robot_body_names: Sequence[str] | None = None,
    held_entity_id: str | None = None,
    allowed_held_contact_entity_ids: Sequence[str] = (),
) -> dict[str, Any]:
    margin = float(policy.arm_collision_margin)
    try:
        snapshot_name = (
            "_task_recursive_tree_recovery_collision_snapshot"
        )
        snapshot = getattr(scene, snapshot_name, None)
        if (
            snapshot is None
            or getattr(snapshot, "live_scene", scene) is not scene
            or not math.isclose(
                float(getattr(snapshot, "collision_margin", -1.0)),
                margin,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            snapshot = motion.MujocoPlanningSnapshot(
                scene,
                collision_margin=margin,
            )
            setattr(scene, snapshot_name, snapshot)

        held = held_entity_id
        if held is None:
            attached = getattr(scene, "securely_attached_entity", None)
            held = attached() if callable(attached) else None
        sync_kwargs: dict[str, Any] = {
            "held_entity_id": held,
            "allowed_contact_entity_ids": tuple(
                str(entity_id)
                for entity_id in allowed_contact_entity_ids
            ),
            "allowed_contact_robot_body_names": (
                None
                if allowed_contact_robot_body_names is None
                else tuple(
                    str(body_name)
                    for body_name in allowed_contact_robot_body_names
                )
            ),
        }
        normalized_held_contacts = tuple(
            str(entity_id)
            for entity_id in allowed_held_contact_entity_ids
        )
        if normalized_held_contacts:
            sync_kwargs["allowed_held_contact_entity_ids"] = (
                normalized_held_contacts
            )
        snapshot.sync(**sync_kwargs)
        current = snapshot.current_configuration()
        vector = current.vector()
        if not snapshot.within_bounds(vector):
            return {
                "ok": True,
                "collisions": [],
                "collision_margin": margin,
                "assessment_model": "planner_collision_snapshot",
                "joint_bounds_deferred_to_workspace_assessment": True,
            }
        if snapshot.state_valid(vector):
            return {
                "ok": True,
                "collisions": [],
                "collision_margin": margin,
                "assessment_model": "planner_collision_snapshot",
            }
        pair = [
            str(value)
            for value in tuple(snapshot.last_collision or ())
        ]
        return {
            "ok": False,
            "collisions": [{
                "code": "MANIPULATOR_COLLISION",
                "component": "manipulator",
                "pair": pair,
                "collision_margin": margin,
            }],
            "collision_margin": margin,
            "assessment_model": "planner_collision_snapshot",
        }
    except Exception as error:
        return {
            "ok": False,
            "collisions": [{
                "code": "MANIPULATOR_COLLISION_ASSESSMENT_FAILED",
                "component": "manipulator",
                "message": str(error),
                "collision_margin": margin,
            }],
            "collision_margin": margin,
            "assessment_model": "planner_collision_snapshot",
            "assessment_error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }


def _make_hardened_recover_workspace(
    control: ModuleType,
    original: Callable[..., Any],
    policy: HarnessSafetyPolicy,
) -> Callable[..., dict[str, Any]]:
    @wraps(original)
    def hardened(
        txn: Any,
        request: Any,
        runtime: Any,
        cancel_event: Any,
    ) -> dict[str, Any]:
        scene = runtime.scene
        before_assessment = _scene_recovery_assessment(scene)
        before_joint_state = _recovery_joint_state(scene, control)
        held_before = _securely_attached_entity(scene)

        raw_report = original(
            txn,
            request,
            runtime,
            cancel_event,
        )
        report = (
            dict(raw_report)
            if isinstance(raw_report, Mapping)
            else {}
        )
        after_assessment = _scene_recovery_assessment(scene)
        after_joint_state = _recovery_joint_state(scene, control)
        held_after = _securely_attached_entity(scene)
        joint_delta = {
            name: after_joint_state[name] - before_joint_state[name]
            for name in sorted(
                set(before_joint_state).intersection(after_joint_state)
            )
        }
        max_abs_joint_delta = max(
            (abs(value) for value in joint_delta.values()),
            default=0.0,
        )
        audit = {
            "policy_fingerprint": policy.fingerprint,
            "collision_margin": policy.arm_collision_margin,
            "motion_epsilon": policy.recovery_motion_epsilon,
            "before_assessment": before_assessment,
            "after_assessment": after_assessment,
            "joint_state_before": before_joint_state,
            "joint_state_after": after_joint_state,
            "joint_delta": joint_delta,
            "max_abs_joint_delta": max_abs_joint_delta,
            "held_entity_id_before": held_before,
            "held_entity_id_after": held_after,
        }
        residual = dict(report.get("residual_state") or {})
        residual["safety_recovery_postcondition"] = audit
        report["residual_state"] = residual

        rejection_reasons: list[str] = []
        if not bool(after_assessment.get("ok")):
            rejection_reasons.append("recovery_faults_remain")
        if held_before is not None and held_after != held_before:
            rejection_reasons.append("secure_held_object_lost")
        if (
            not bool(before_assessment.get("ok"))
            and max_abs_joint_delta <= policy.recovery_motion_epsilon
        ):
            rejection_reasons.append("unsafe_state_without_motion")
        if rejection_reasons:
            audit["rejection_reasons"] = rejection_reasons
            raise control.ControlError(
                "SAFETY_RECOVERY_FAILED",
                "workspace recovery failed its independent "
                "postconditions: "
                + ",".join(rejection_reasons),
                details={
                    "failure_mode":
                        "WORKSPACE_RECOVERY_POSTCONDITION_FAILED",
                    "rejection_reasons": rejection_reasons,
                    "residual_state": residual,
                },
            )
        audit["rejection_reasons"] = []
        return report

    setattr(
        hardened,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened


def _scene_recovery_assessment(scene: Any) -> dict[str, Any]:
    recover = getattr(scene, "recovery_assessment", None)
    if callable(recover):
        assessment = recover()
        return (
            copy.deepcopy(dict(assessment))
            if isinstance(assessment, Mapping)
            else {"ok": False, "faults": []}
        )

    workspace_reader = getattr(scene, "workspace_assessment", None)
    workspace = (
        workspace_reader()
        if callable(workspace_reader)
        else {"ok": False, "faults": []}
    )
    workspace = (
        dict(workspace)
        if isinstance(workspace, Mapping)
        else {"ok": False, "faults": []}
    )
    collision_reader = getattr(
        scene,
        "manipulator_collision_assessment",
        None,
    )
    collision = (
        collision_reader()
        if callable(collision_reader)
        else {"ok": False, "collisions": []}
    )
    collision = (
        dict(collision)
        if isinstance(collision, Mapping)
        else {"ok": False, "collisions": []}
    )
    faults = [
        copy.deepcopy(item)
        for item in (
            list(workspace.get("faults") or ())
            + list(collision.get("collisions") or ())
        )
        if isinstance(item, Mapping)
    ]
    return {
        "ok": bool(workspace.get("ok")) and bool(collision.get("ok")),
        "faults": faults,
        "workspace_assessment": copy.deepcopy(workspace),
        "collision_assessment": copy.deepcopy(collision),
    }


def _recovery_joint_state(
    scene: Any,
    control: ModuleType,
) -> dict[str, float]:
    state: dict[str, float] = {}
    readers = {
        "lift": getattr(scene, "lift_position", None),
        "arm_extend": getattr(scene, "arm_extension", None),
    }
    for name, reader in readers.items():
        if not callable(reader):
            continue
        try:
            value = float(reader())
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            state[name] = value

    joint_reader = getattr(scene, "joint_pos", None)
    if not callable(joint_reader):
        return state
    joint_names = [
        "wrist_yaw",
        "grip",
        *tuple(getattr(control, "FINGER_JOINTS", ())),
    ]
    for name in dict.fromkeys(str(value) for value in joint_names):
        try:
            value = float(joint_reader(name))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            state[name] = value
    return state


def _securely_attached_entity(scene: Any) -> str | None:
    reader = getattr(scene, "securely_attached_entity", None)
    if not callable(reader):
        return None
    try:
        value = reader()
    except Exception:
        return None
    if value is None or not str(value).strip():
        return None
    return str(value)


def _make_hardened_held_drive_base_to(
    control: ModuleType,
    navigation: ModuleType,
    placement: ModuleType,
    original: Callable[..., Any],
    policy: HarnessSafetyPolicy,
    *,
    posture_transition_executor: Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    @wraps(original)
    def hardened(
        self: Any,
        x: float,
        y: float,
        yaw: float,
        tol_pos: float = 0.04,
        tol_yaw: float = 0.03,
        settle: float = 0.4,
        max_time: float | None = None,
        stow: bool = True,
        vmax_trans: float = 0.5,
        avoid_table: bool = True,
    ) -> Any:
        try:
            held_entity_id = self.scene.securely_attached_entity()
        except Exception:
            held_entity_id = None
        runtime = getattr(self, "_task_recursive_tree_runtime", None)
        if (
            not stow
            or held_entity_id is None
            or runtime is None
            or getattr(runtime, "scene", None) is not self.scene
            or getattr(runtime, "perception", None) is None
        ):
            return original(
                self,
                x,
                y,
                yaw,
                tol_pos=tol_pos,
                tol_yaw=tol_yaw,
                settle=settle,
                max_time=max_time,
                stow=stow,
                vmax_trans=vmax_trans,
                avoid_table=avoid_table,
            )
        return _execute_held_astar_base_motion(
            self,
            runtime,
            str(held_entity_id),
            (x, y, yaw),
            control=control,
            navigation=navigation,
            placement=placement,
            policy=policy,
            tol_pos=tol_pos,
            tol_yaw=tol_yaw,
            settle=settle,
            max_time=max_time,
            vmax_trans=vmax_trans,
            posture_transition_executor=posture_transition_executor,
        )

    setattr(
        hardened,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened


def _execute_held_astar_base_motion(
    supervisor: Any,
    runtime: Any,
    held_entity_id: str,
    raw_goal: Sequence[float],
    *,
    control: ModuleType,
    navigation: ModuleType,
    placement: ModuleType,
    policy: HarnessSafetyPolicy,
    tol_pos: float,
    tol_yaw: float,
    settle: float,
    max_time: float | None,
    vmax_trans: float,
    posture_transition_executor: Callable[..., Any] | None,
) -> None:
    try:
        (base, current_yaw) = supervisor.scene.base_pose()
        start = (
            float(base[0]),
            float(base[1]),
            float(current_yaw),
        )
        goal = _pose3(raw_goal)
    except (TypeError, ValueError) as error:
        raise control.ControlError(
            "INVALID_REQUEST",
            "held base target must be a finite SE(2) pose",
        ) from error

    route_obstacles = tuple(navigation.build_base_route_obstacles(
        runtime.perception,
        world=getattr(runtime, "world", None),
        exclude_entity_ids=(held_entity_id,),
    ))
    anchor_snapshot = _snapshot_non_movable_route_anchors(
        runtime.perception,
        route_obstacles,
    )
    attempts: list[dict[str, Any]] = []
    effective_goal = goal
    goal_relaxation: dict[str, Any] | None = None
    selected_posture_transition: dict[str, Any] | None = None
    posture_transition_execution: Any = None
    posture_transition_revalidation: dict[str, Any] | None = None

    direct_plan, direct_poses = _compile_astar_route(
        navigation,
        supervisor.scene,
        start,
        goal,
        route_obstacles,
        policy=policy,
    )
    provisional_poses = list(direct_poses or ())
    if not provisional_poses:
        try:
            provisional_poses = _normalize_pose_sequence(
                direct_plan.get("poses_world") or ()
            )
        except (TypeError, ValueError):
            provisional_poses = []
    selected_poses: list[tuple[float, float, float]] | None = None
    if direct_poses is not None:
        direct_assessment = placement.assess_held_base_path(
            supervisor.scene,
            held_entity_id,
            direct_poses,
            goal[:2],
        )
        attempts.append({
            "phase": "direct_astar",
            "plan": direct_plan,
            "assessment": direct_assessment,
        })
        if direct_assessment.get("ok"):
            selected_poses = direct_poses
        elif "HOLD_LOST" in set(
            direct_assessment.get("reason_codes") or ()
        ):
            raise control.ControlError(
                "GRASP_FAILED",
                "held object was lost before base motion",
                details={
                    "held_entity_id": held_entity_id,
                    "held_path_assessment": direct_assessment,
                },
            )
    else:
        attempts.append({
            "phase": "direct_astar",
            "plan": direct_plan,
            "assessment": None,
        })

    if selected_poses is None:
        for departure_yaw in _departure_yaw_candidates(
            start,
            goal,
            provisional_poses,
        ):
            prefix = _base_aware_held_departure_prefix(
                navigation,
                placement,
                supervisor.scene,
                held_entity_id,
                departure_yaw,
                route_obstacles,
                policy=policy,
            )
            attempt: dict[str, Any] = {
                "phase": "held_departure_repair",
                "departure_yaw": departure_yaw,
                "departure_prefix": prefix,
            }
            if not prefix.get("ok"):
                attempts.append(attempt)
                if "HOLD_LOST" in set(
                    prefix.get("reason_codes") or ()
                ):
                    raise control.ControlError(
                        "GRASP_FAILED",
                        "held object was lost during departure planning",
                        details={
                            "held_entity_id": held_entity_id,
                            "departure_prefix": prefix,
                        },
                    )
                continue
            try:
                escape = _pose3(prefix["escape_pose"])
            except (KeyError, TypeError, ValueError):
                attempt["malformed"] = True
                attempts.append(attempt)
                continue
            rotated_escape = (
                escape[0],
                escape[1],
                float(departure_yaw),
            )
            try:
                prefix_poses = _normalize_pose_sequence(
                    prefix.get("poses") or (),
                )
            except (TypeError, ValueError):
                attempt["malformed"] = True
                attempts.append(attempt)
                continue
            relaxation = _held_rotation_goal_relaxation(
                start,
                goal,
                rotated_escape,
                direct_plan,
                tol_pos=tol_pos,
            )
            if relaxation is not None:
                combined = _dedupe_pose_sequence([
                    start,
                    *prefix_poses,
                    rotated_escape,
                ])
                base_validation = _validate_base_pose_path(
                    navigation,
                    combined,
                    route_obstacles,
                    policy=policy,
                )
                assessment = placement.assess_held_base_path(
                    supervisor.scene,
                    held_entity_id,
                    combined,
                    goal[:2],
                    **_held_posture_assessment_kwargs(prefix),
                )
                attempt["goal_relaxation"] = relaxation
                attempt["combined_route_validation"] = base_validation
                if not base_validation.get("ok"):
                    attempts.append(attempt)
                    continue
                attempt["assessment"] = assessment
                attempts.append(attempt)
                if assessment.get("ok"):
                    selected_poses = combined
                    effective_goal = rotated_escape
                    goal_relaxation = relaxation
                    transition = prefix.get("posture_transition")
                    if isinstance(transition, Mapping):
                        selected_posture_transition = copy.deepcopy(
                            dict(transition)
                        )
                    break
                if "HOLD_LOST" in set(
                    assessment.get("reason_codes") or ()
                ):
                    raise control.ControlError(
                        "GRASP_FAILED",
                        "held object was lost during relaxed-goal planning",
                        details={
                            "held_entity_id": held_entity_id,
                            "held_path_assessment": assessment,
                        },
                    )
                continue
            repaired_plan, repaired_poses = _compile_astar_route(
                navigation,
                supervisor.scene,
                rotated_escape,
                goal,
                route_obstacles,
                policy=policy,
            )
            attempt["plan"] = repaired_plan
            if repaired_poses is None:
                attempts.append(attempt)
                continue
            combined = _dedupe_pose_sequence([
                start,
                *prefix_poses,
                rotated_escape,
                *repaired_poses,
            ])
            base_validation = _validate_base_pose_path(
                navigation,
                combined,
                route_obstacles,
                policy=policy,
            )
            attempt["combined_route_validation"] = base_validation
            if not base_validation.get("ok"):
                attempts.append(attempt)
                continue
            assessment = placement.assess_held_base_path(
                supervisor.scene,
                held_entity_id,
                combined,
                goal[:2],
                **_held_posture_assessment_kwargs(prefix),
            )
            attempt["assessment"] = assessment
            attempts.append(attempt)
            if assessment.get("ok"):
                selected_poses = combined
                transition = prefix.get("posture_transition")
                if isinstance(transition, Mapping):
                    selected_posture_transition = copy.deepcopy(
                        dict(transition)
                    )
                break
            if "HOLD_LOST" in set(
                assessment.get("reason_codes") or ()
            ):
                raise control.ControlError(
                    "GRASP_FAILED",
                    "held object was lost during repaired route planning",
                    details={
                        "held_entity_id": held_entity_id,
                        "held_path_assessment": assessment,
                    },
                )

    if selected_poses is None:
        collision_pair = _last_collision_pair(attempts)
        raise control.ControlError(
            "PATH_BLOCKED",
            "A* found no route safe for the carried object envelope",
            details={
                "raw_failure_code": "HELD_PATH_COLLISION",
                "held_entity_id": held_entity_id,
                "collision_pair": collision_pair,
                "route_attempts": attempts,
                "start_pose": list(start),
                "requested_goal_pose": list(goal),
                "position_tolerance_m": float(tol_pos),
                "direct_goal_reason_code": direct_plan.get("reason_code"),
                "near_pure_rotation": (
                    math.dist(start[:2], goal[:2]) <= 0.01 + 1e-12
                ),
            },
        )

    execution_poses = list(selected_poses)
    if execution_poses and _same_pose(execution_poses[0], start):
        execution_poses.pop(0)
    detected_anchor_drift: list[dict[str, Any]] = []

    def reject_anchor_drift(
        drift: Sequence[Mapping[str, Any]],
        *,
        cause: Exception | None = None,
    ) -> None:
        report = {
            "model_version": policy.model_version,
            "held_entity_id": held_entity_id,
            "planner": "se2_lattice_astar",
            "poses_world": [list(pose) for pose in selected_poses],
            "attempts": attempts,
            "requested_goal_pose": list(goal),
            "effective_goal_pose": list(effective_goal),
            "goal_relaxation": copy.deepcopy(goal_relaxation),
            "posture_transition": copy.deepcopy(
                selected_posture_transition
            ),
            "posture_transition_execution": copy.deepcopy(
                posture_transition_execution
            ),
            "posture_transition_revalidation": copy.deepcopy(
                posture_transition_revalidation
            ),
            "non_movable_anchor_snapshot": anchor_snapshot,
            "anchor_drift": [
                dict(value) for value in drift
            ],
        }
        setattr(supervisor, "last_held_base_navigation", report)
        error = control.ControlError(
            "SAFETY_REJECTED",
            "a non-movable route obstacle changed pose during held motion",
            details={
                "raw_failure_code": "NON_MOVABLE_ANCHOR_DRIFT",
                "held_entity_id": held_entity_id,
                "anchor_drift": [
                    dict(value) for value in drift
                ],
                "translation_tolerance_m": (
                    policy.non_movable_anchor_translation_tolerance
                ),
                "yaw_tolerance_rad": (
                    policy.non_movable_anchor_yaw_tolerance
                ),
            },
        )
        if cause is not None:
            raise error from cause
        raise error

    def pose_validator(
        x: float,
        y: float,
        yaw: float,
    ) -> tuple[bool, str | None]:
        drift = _non_movable_anchor_drift(
            runtime.perception,
            anchor_snapshot,
            translation_tolerance=(
                policy.non_movable_anchor_translation_tolerance
            ),
            yaw_tolerance=policy.non_movable_anchor_yaw_tolerance,
        )
        if drift:
            detected_anchor_drift[:] = drift
            return False, str(drift[0]["entity_id"])
        return navigation.route_pose_clear(
            (float(x), float(y), float(yaw)),
            route_obstacles,
            footprint=navigation.DEFAULT_BASE_FOOTPRINT,
        )

    follow_path = getattr(supervisor, "follow_base_path", None)
    if not callable(follow_path):
        raise control.ControlError(
            "SAFETY_REJECTED",
            "held A* route requires the continuous base path controller",
        )
    initial_drift = _non_movable_anchor_drift(
        runtime.perception,
        anchor_snapshot,
        translation_tolerance=(
            policy.non_movable_anchor_translation_tolerance
        ),
        yaw_tolerance=policy.non_movable_anchor_yaw_tolerance,
    )
    if initial_drift:
        reject_anchor_drift(initial_drift)
    if selected_posture_transition is not None:
        if not callable(posture_transition_executor):
            raise control.ControlError(
                "SAFETY_REJECTED",
                "held route requires posture execution support",
                details={
                    "raw_failure_code": (
                        "HELD_POSTURE_EXECUTOR_UNAVAILABLE"
                    ),
                    "held_entity_id": held_entity_id,
                    "posture_transition": copy.deepcopy(
                        selected_posture_transition
                    ),
                },
            )
        posture_transition_execution = posture_transition_executor(
            supervisor,
            supervisor.scene,
            held_entity_id,
            copy.deepcopy(selected_posture_transition),
        )
        posture_transition_revalidation = (
            placement.assess_held_base_path(
                supervisor.scene,
                held_entity_id,
                selected_poses,
                goal[:2],
            )
        )
        attempts.append({
            "phase": "posture_transition_revalidation",
            "posture_transition": copy.deepcopy(
                selected_posture_transition
            ),
            "execution": copy.deepcopy(posture_transition_execution),
            "assessment": copy.deepcopy(
                posture_transition_revalidation
            ),
        })
        if not posture_transition_revalidation.get("ok"):
            reasons = set(
                posture_transition_revalidation.get("reason_codes") or ()
            )
            if "HOLD_LOST" in reasons:
                raise control.ControlError(
                    "GRASP_FAILED",
                    "held object was lost after posture transition",
                    details={
                        "held_entity_id": held_entity_id,
                        "posture_transition_execution": copy.deepcopy(
                            posture_transition_execution
                        ),
                        "held_path_assessment": copy.deepcopy(
                            posture_transition_revalidation
                        ),
                    },
                )
            raise control.ControlError(
                "PATH_BLOCKED",
                "held path became unsafe after posture transition",
                details={
                    "raw_failure_code": (
                        "HELD_PATH_INVALID_AFTER_POSTURE_TRANSITION"
                    ),
                    "held_entity_id": held_entity_id,
                    "posture_transition_execution": copy.deepcopy(
                        posture_transition_execution
                    ),
                    "held_path_assessment": copy.deepcopy(
                        posture_transition_revalidation
                    ),
                    "selected_poses_world": [
                        list(pose) for pose in selected_poses
                    ],
                },
            )
    try:
        report = follow_path(
            execution_poses,
            tol_pos=min(float(tol_pos), 0.04),
            tol_yaw=min(float(tol_yaw), 0.03),
            settle=float(settle),
            max_time=max_time,
            stow=False,
            vmax_trans=float(vmax_trans),
            pose_validator=pose_validator,
        )
    except Exception as error:
        drift = detected_anchor_drift or _non_movable_anchor_drift(
            runtime.perception,
            anchor_snapshot,
            translation_tolerance=(
                policy.non_movable_anchor_translation_tolerance
            ),
            yaw_tolerance=policy.non_movable_anchor_yaw_tolerance,
        )
        if drift:
            reject_anchor_drift(drift, cause=error)
        if (
            isinstance(error, control.ControlError)
            and str(getattr(error, "code", "")).upper()
            == "PATH_BLOCKED"
        ):
            controller_details = dict(
                getattr(error, "details", {}) or {}
            )
            blocking_entity_ids = [
                str(value)
                for value in (
                    controller_details.get("blocking_entity_ids") or ()
                )
                if value is not None and str(value)
            ]
            controller_message = str(
                getattr(error, "message", "") or error
            )
            collision_evidence, inferred_blockers = (
                _controller_path_collision_evidence(
                    controller_details,
                    controller_message,
                    held_entity_id=held_entity_id,
                )
            )
            blocking_entity_ids.extend(inferred_blockers)
            report = {
                "model_version": policy.model_version,
                "held_entity_id": held_entity_id,
                "planner": "se2_lattice_astar",
                "poses_world": [
                    list(pose) for pose in selected_poses
                ],
                "attempts": attempts,
                "requested_goal_pose": list(goal),
                "effective_goal_pose": list(effective_goal),
                "goal_relaxation": copy.deepcopy(goal_relaxation),
                "posture_transition": copy.deepcopy(
                    selected_posture_transition
                ),
                "posture_transition_execution": copy.deepcopy(
                    posture_transition_execution
                ),
                "posture_transition_revalidation": copy.deepcopy(
                    posture_transition_revalidation
                ),
                "controller_error": {
                    "code": str(getattr(error, "code", "")),
                    "message": controller_message,
                    "details": controller_details,
                },
                "non_movable_anchor_snapshot": anchor_snapshot,
                "anchor_drift": [],
            }
            setattr(supervisor, "last_held_base_navigation", report)
            if not collision_evidence:
                raise
            raise control.ControlError(
                "PATH_BLOCKED",
                "held A* route was blocked during base-path execution",
                details={
                    "raw_failure_code": "HELD_BASE_PATH_COLLISION",
                    "failure_mode": "HELD_BASE_PATH_COLLISION",
                    "recovery_kind": "reposition_held_base",
                    "held_entity_id": held_entity_id,
                    "blocking_entity_ids": list(
                        dict.fromkeys(blocking_entity_ids)
                    ),
                    "route_attempts": attempts,
                    "selected_poses_world": [
                        list(pose) for pose in selected_poses
                    ],
                    "requested_goal_pose": list(goal),
                    "effective_goal_pose": list(effective_goal),
                    "controller_error": report["controller_error"],
                },
            ) from error
        raise
    final_drift = _non_movable_anchor_drift(
        runtime.perception,
        anchor_snapshot,
        translation_tolerance=(
            policy.non_movable_anchor_translation_tolerance
        ),
        yaw_tolerance=policy.non_movable_anchor_yaw_tolerance,
    )
    if final_drift:
        reject_anchor_drift(final_drift)
    setattr(supervisor, "last_held_base_navigation", {
        "model_version": policy.model_version,
        "held_entity_id": held_entity_id,
        "planner": "se2_lattice_astar",
        "poses_world": [list(pose) for pose in selected_poses],
        "attempts": attempts,
        "requested_goal_pose": list(goal),
        "effective_goal_pose": list(effective_goal),
        "goal_relaxation": copy.deepcopy(goal_relaxation),
        "posture_transition": copy.deepcopy(
            selected_posture_transition
        ),
        "posture_transition_execution": copy.deepcopy(
            posture_transition_execution
        ),
        "posture_transition_revalidation": copy.deepcopy(
            posture_transition_revalidation
        ),
        "controller_report": report,
        "non_movable_anchor_snapshot": anchor_snapshot,
        "anchor_drift": [],
    })
    try:
        held_after = supervisor.scene.gripper_holding_entity()
    except Exception:
        held_after = None
    if str(held_after) != held_entity_id:
        raise control.ControlError(
            "GRASP_FAILED",
            "held object was lost during A* base motion",
            details={
                "expected_held_entity_id": held_entity_id,
                "observed_held_entity_id": (
                    str(held_after) if held_after is not None else None
                ),
            },
        )


def _snapshot_non_movable_route_anchors(
    perception: Any,
    obstacles: Sequence[Any],
) -> list[dict[str, Any]]:
    catalog = getattr(perception, "catalog", {})
    catalog = catalog if isinstance(catalog, Mapping) else {}
    anchors: list[dict[str, Any]] = []
    for obstacle in obstacles:
        raw_entity_id = getattr(obstacle, "entity_id", None)
        if raw_entity_id is None and isinstance(obstacle, str):
            raw_entity_id = obstacle
        entity_id = str(raw_entity_id or "")
        if not entity_id:
            continue
        metadata = catalog.get(entity_id)
        metadata = metadata if isinstance(metadata, Mapping) else {}
        movable = (
            bool(metadata.get("movable"))
            if "movable" in metadata
            else bool(getattr(obstacle, "movable", True))
        )
        if movable:
            continue
        pose = _non_movable_anchor_pose(perception, entity_id)
        anchors.append({
            "entity_id": entity_id,
            "expected_pose": list(pose) if pose is not None else None,
            "snapshot_status": (
                "observed" if pose is not None else "pose_unavailable"
            ),
        })
    return anchors


def _non_movable_anchor_drift(
    perception: Any,
    anchors: Sequence[Mapping[str, Any]],
    *,
    translation_tolerance: float,
    yaw_tolerance: float,
) -> list[dict[str, Any]]:
    drift: list[dict[str, Any]] = []
    for anchor in anchors:
        entity_id = str(anchor.get("entity_id") or "")
        expected_raw = anchor.get("expected_pose")
        if (
            not entity_id
        ):
            continue
        if (
            not isinstance(expected_raw, (list, tuple))
            or len(expected_raw) < 3
        ):
            drift.append({
                "entity_id": entity_id,
                "expected_pose": None,
                "observed_pose": None,
                "reason": "baseline_pose_unavailable",
            })
            continue
        expected = _pose3(expected_raw)
        observed = _non_movable_anchor_pose(perception, entity_id)
        if observed is None:
            drift.append({
                "entity_id": entity_id,
                "expected_pose": list(expected),
                "observed_pose": None,
                "reason": "pose_unavailable",
            })
            continue
        translation_error = math.dist(expected[:2], observed[:2])
        yaw_error = abs(_normalize_angle(observed[2] - expected[2]))
        if (
            translation_error
            <= float(translation_tolerance) + 1e-12
            and yaw_error <= float(yaw_tolerance) + 1e-12
        ):
            continue
        drift.append({
            "entity_id": entity_id,
            "expected_pose": list(expected),
            "observed_pose": list(observed),
            "translation_error_m": translation_error,
            "yaw_error_rad": yaw_error,
            "reason": "pose_drift",
        })
    return drift


def _non_movable_anchor_pose(
    perception: Any,
    entity_id: str,
) -> tuple[float, float, float] | None:
    pose_reader = getattr(perception, "entity_pose", None)
    if not callable(pose_reader):
        return None
    try:
        raw_pose = pose_reader(str(entity_id))
    except Exception:
        return None
    if not isinstance(raw_pose, (list, tuple)) or len(raw_pose) < 2:
        return None
    yaw_reader = getattr(perception, "entity_yaw", None)
    try:
        yaw = (
            float(yaw_reader(str(entity_id)))
            if callable(yaw_reader)
            else 0.0
        )
        pose = (
            float(raw_pose[0]),
            float(raw_pose[1]),
            _normalize_angle(yaw),
        )
    except Exception:
        return None
    return pose if all(math.isfinite(value) for value in pose) else None


def _compile_astar_route(
    navigation: ModuleType,
    scene: Any,
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
    obstacles: Sequence[Any],
    *,
    policy: HarnessSafetyPolicy,
) -> tuple[dict[str, Any], list[tuple[float, float, float]] | None]:
    try:
        plan = dict(navigation.plan_base_route(
            start,
            goal,
            obstacles,
            position_ok=scene.base_position_ok,
        ))
    except Exception as error:
        return {
            "selected": None,
            "reason_code": "PLANNER_EXCEPTION",
            "message": str(error),
            "exception_type": type(error).__name__,
        }, None
    if plan.get("selected") is None:
        return plan, None
    try:
        poses = _normalize_pose_sequence(plan.get("poses_world") or ())
    except (TypeError, ValueError):
        return {
            **plan,
            "selected": None,
            "reason_code": "PATH_MALFORMED",
        }, None
    if not poses:
        poses = [start, goal]
    poses = _dedupe_pose_sequence([start, *poses, goal])
    route_validation = _validate_base_pose_path(
        navigation,
        poses,
        obstacles,
        policy=policy,
    )
    if not route_validation.get("ok"):
        reason_code = str(
            route_validation.get("reason_code")
            or "PATH_SWEEP_BLOCKED"
        )
        blocker = route_validation.get("blocking_entity_id")
        direct_blockers = [
            str(value)
            for value in (
                plan.get("direct_blocking_entity_ids") or ()
            )
            if value is not None and str(value)
        ]
        if blocker is not None and str(blocker):
            direct_blockers.append(str(blocker))
        return {
            **plan,
            "planner_selected": copy.deepcopy(plan.get("selected")),
            "selected": None,
            "reason_code": reason_code,
            "direct_blocking_entity_ids": list(
                dict.fromkeys(direct_blockers)
            ),
            "route_validation": route_validation,
        }, None
    plan["route_validation"] = route_validation
    return plan, poses


def _base_aware_held_departure_prefix(
    navigation: ModuleType,
    placement: ModuleType,
    scene: Any,
    object_id: str,
    target_yaw: float,
    obstacles: Sequence[Any],
    *,
    policy: HarnessSafetyPolicy,
    max_distance: float = 0.40,
) -> dict[str, Any]:
    """Require both base-footprint and held-payload safety for departure."""

    raw_prefix = placement.held_departure_prefix(
        scene,
        object_id,
        target_yaw,
        max_distance=max_distance,
        step=policy.held_translation_sample,
    )
    if not isinstance(raw_prefix, Mapping):
        return {
            "ok": False,
            "reason_codes": ["PATH_MALFORMED"],
            "base_aware": True,
        }
    prefix = copy.deepcopy(dict(raw_prefix))
    if not prefix.get("ok"):
        prefix["base_aware"] = True
        return prefix

    try:
        (base, current_yaw) = scene.base_pose()
        start = (
            float(base[0]),
            float(base[1]),
            float(current_yaw),
        )
        target_yaw = float(target_yaw)
        escape = _pose3(prefix["escape_pose"])
        prefix_poses = _normalize_pose_sequence(prefix.get("poses") or ())
    except (KeyError, TypeError, ValueError):
        return {
            **prefix,
            "ok": False,
            "reason_codes": ["PATH_MALFORMED"],
            "base_aware": True,
        }

    candidate = _dedupe_pose_sequence([
        start,
        *prefix_poses,
        escape,
        (escape[0], escape[1], target_yaw),
    ])
    validation = _validate_base_pose_path(
        navigation,
        candidate,
        obstacles,
        policy=policy,
    )
    prefix["base_route_validation"] = validation
    prefix["base_aware"] = True
    if validation.get("ok"):
        return prefix

    try:
        initial_distance = float(prefix.get("distance") or 0.0)
        max_distance = float(max_distance)
    except (TypeError, ValueError):
        initial_distance = 0.0
        max_distance = 0.40
    step = float(policy.held_translation_sample)
    start_index = max(1, int(math.floor(initial_distance / step)) + 1)
    sample_count = max(start_index, int(math.ceil(max_distance / step)))
    tested = 0
    blockers: list[str] = []
    last_validation = validation
    last_assessment: Mapping[str, Any] | None = None

    for index in range(start_index, sample_count + 1):
        distance = min(max_distance, index * step)
        if distance <= initial_distance + 1e-12:
            continue
        tested += 1
        escape = (
            start[0] - distance * math.cos(start[2]),
            start[1] - distance * math.sin(start[2]),
            start[2],
        )
        rotated_escape = (escape[0], escape[1], target_yaw)
        candidate = _dedupe_pose_sequence([
            start,
            escape,
            rotated_escape,
        ])
        last_validation = _validate_base_pose_path(
            navigation,
            candidate,
            obstacles,
            policy=policy,
        )
        blocker = last_validation.get("blocking_entity_id")
        if blocker is not None and str(blocker):
            blockers.append(str(blocker))
        if not last_validation.get("ok"):
            continue

        assessment = placement.assess_held_base_path(
            scene,
            str(object_id),
            candidate,
            escape[:2],
            **_held_posture_assessment_kwargs(prefix),
        )
        last_assessment = (
            copy.deepcopy(dict(assessment))
            if isinstance(assessment, Mapping)
            else None
        )
        if isinstance(assessment, Mapping) and assessment.get("ok"):
            return {
                **prefix,
                "ok": True,
                "poses": [list(start), list(escape)],
                "escape_pose": list(escape),
                "target_yaw": target_yaw,
                "distance": distance,
                "assessment": copy.deepcopy(dict(assessment)),
                "reason_codes": [],
                "base_route_validation": last_validation,
                "base_aware": True,
                "base_aware_search": {
                    "tested_nonzero_candidates": tested,
                    "initial_distance": initial_distance,
                    "selected_distance": distance,
                    "blocking_entity_ids": list(dict.fromkeys(blockers)),
                },
            }
        if (
            isinstance(assessment, Mapping)
            and "HOLD_LOST" in set(assessment.get("reason_codes") or ())
        ):
            break

    reason_codes = ["BASE_PATH_COLLISION"]
    if isinstance(last_assessment, Mapping):
        reason_codes.extend(last_assessment.get("reason_codes") or ())
    return {
        **prefix,
        "ok": False,
        "poses": [list(start)],
        "distance": None,
        "assessment": copy.deepcopy(last_assessment),
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "base_route_validation": last_validation,
        "blocking_entity_ids": list(dict.fromkeys(blockers)),
        "base_aware": True,
        "base_aware_search": {
            "tested_nonzero_candidates": tested,
            "initial_distance": initial_distance,
            "selected_distance": None,
        },
    }


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


def _validate_base_pose_path(
    navigation: ModuleType,
    poses: Sequence[Sequence[float]],
    obstacles: Sequence[Any],
    *,
    policy: HarnessSafetyPolicy,
) -> dict[str, Any]:
    route_validator = getattr(
        navigation,
        "route_pose_path_clear",
        None,
    )
    if not callable(route_validator):
        return {
            "ok": False,
            "reason_code": "PATH_VALIDATOR_UNAVAILABLE",
            "model_version": policy.model_version,
            "safety_policy_fingerprint": policy.fingerprint,
        }
    try:
        clear, blocker = route_validator(
            poses,
            obstacles,
            footprint=navigation.DEFAULT_BASE_FOOTPRINT,
            translation_sample=policy.base_translation_sample,
            rotation_sample=policy.base_rotation_sample,
        )
    except Exception as error:
        return {
            "ok": False,
            "reason_code": "PATH_VALIDATION_EXCEPTION",
            "message": str(error),
            "exception_type": type(error).__name__,
            "model_version": policy.model_version,
            "safety_policy_fingerprint": policy.fingerprint,
        }
    return {
        "ok": bool(clear),
        "reason_code": None if clear else "PATH_SWEEP_BLOCKED",
        "blocking_entity_id": (
            str(blocker) if blocker is not None else None
        ),
        "model_version": policy.model_version,
        "safety_policy_fingerprint": policy.fingerprint,
        "translation_sample": policy.base_translation_sample,
        "rotation_sample": policy.base_rotation_sample,
    }


def _departure_yaw_candidates(
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
    provisional_poses: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    candidates: list[float] = []
    for raw_pose in provisional_poses:
        pose = _pose3(raw_pose)
        if math.dist(start[:2], pose[:2]) > 1e-6:
            candidates.append(pose[2])
            break
    candidates.append(goal[2])
    if math.dist(start[:2], goal[:2]) > 1e-6:
        candidates.append(math.atan2(
            goal[1] - start[1],
            goal[0] - start[0],
        ))
    unique: list[float] = []
    for candidate in candidates:
        normalized = _normalize_angle(candidate)
        if any(
            abs(_normalize_angle(normalized - existing)) <= 1e-6
            for existing in unique
        ):
            continue
        unique.append(normalized)
    return tuple(unique)


def _held_rotation_goal_relaxation(
    start: tuple[float, float, float],
    goal: tuple[float, float, float],
    escape_goal: tuple[float, float, float],
    direct_plan: Mapping[str, Any],
    *,
    tol_pos: float,
) -> dict[str, Any] | None:
    try:
        tolerance = float(tol_pos)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(tolerance) or tolerance < 0.0:
        return None
    requested_translation = math.dist(start[:2], goal[:2])
    requested_rotation = abs(_normalize_angle(goal[2] - start[2]))
    if requested_translation > 0.01 + 1e-12:
        return None
    if requested_rotation <= 1e-6:
        return None
    if str(direct_plan.get("reason_code") or "") != (
        "GOAL_POSE_IN_COLLISION"
    ):
        return None
    position_error = math.dist(escape_goal[:2], goal[:2])
    if position_error > tolerance + 1e-12:
        return None
    return {
        "kind": "tolerance_bounded_held_rotation_escape",
        "reason_code": "GOAL_POSE_IN_COLLISION",
        "requested_goal_pose": list(goal),
        "effective_goal_pose": list(escape_goal),
        "requested_translation_m": requested_translation,
        "requested_rotation_rad": requested_rotation,
        "position_error_m": position_error,
        "position_tolerance_m": tolerance,
    }


def _normalize_pose_sequence(
    raw_poses: Iterable[Sequence[float]],
) -> list[tuple[float, float, float]]:
    return [_pose3(raw_pose) for raw_pose in raw_poses]


def _dedupe_pose_sequence(
    poses: Iterable[Sequence[float]],
) -> list[tuple[float, float, float]]:
    result: list[tuple[float, float, float]] = []
    for raw_pose in poses:
        pose = _pose3(raw_pose)
        if result and _same_pose(result[-1], pose):
            continue
        result.append(pose)
    return result


def _same_pose(
    first: Sequence[float],
    second: Sequence[float],
) -> bool:
    return (
        math.dist(first[:2], second[:2]) <= 1e-9
        and abs(_normalize_angle(first[2] - second[2])) <= 1e-9
    )


def _last_collision_pair(
    attempts: Sequence[Mapping[str, Any]],
) -> list[str]:
    for attempt in reversed(attempts):
        assessment = attempt.get("assessment")
        if not isinstance(assessment, Mapping):
            prefix = attempt.get("departure_prefix")
            if isinstance(prefix, Mapping):
                assessment = prefix.get("assessment")
        if not isinstance(assessment, Mapping):
            continue
        issue = assessment.get("issue")
        if not isinstance(issue, Mapping):
            planning_error = assessment.get("planning_error")
            issue = (
                planning_error
                if isinstance(planning_error, Mapping)
                else {}
            )
        pair = issue.get("collision_pair")
        if isinstance(pair, (list, tuple)):
            return [str(value) for value in pair]
    return []


def _controller_path_collision_evidence(
    details: Mapping[str, Any],
    message: str,
    *,
    held_entity_id: str,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    for key in (
        "blocking_entity_ids",
        "blocking_entity_id",
        "obstacle_entity_ids",
        "obstacle_entity_id",
    ):
        raw = details.get(key)
        if isinstance(raw, str):
            blockers.append(raw)
        elif isinstance(raw, (list, tuple, set, frozenset)):
            blockers.extend(str(value) for value in raw if str(value))

    collision_pair = details.get("collision_pair")
    if isinstance(collision_pair, (list, tuple)):
        blockers.extend(
            str(value)
            for value in collision_pair
            if str(value) and str(value) != str(held_entity_id)
        )

    message_text = str(message or "")
    marker = " by obstacle "
    if marker in message_text:
        blocker = message_text.split(marker, 1)[1].strip()
        if blocker:
            blockers.append(blocker)

    evidence_codes = {
        str(details.get(key) or "").upper()
        for key in (
            "raw_failure_code",
            "failure_code",
            "failure_mode",
            "reason_code",
        )
    }
    explicit_code = any(
        "COLLISION" in code
        or code in {
            "BASE_FOOTPRINT_SWEEP_BLOCKED",
            "FOOTPRINT_SWEEP_BLOCKED",
            "OBSTACLE_BLOCKED",
        }
        for code in evidence_codes
        if code
    )
    lowered_message = message_text.casefold()
    explicit_message = any(
        marker_text in lowered_message
        for marker_text in (
            "blocked by obstacle",
            "footprint sweep blocked",
            "collision",
        )
    )
    unique_blockers = [
        value
        for value in dict.fromkeys(blockers)
        if value and value != str(held_entity_id)
    ]
    return bool(unique_blockers or explicit_code or explicit_message), (
        unique_blockers
    )


def _er2sim_modules(
    required: Mapping[str, ModuleType],
) -> dict[str, ModuleType]:
    modules = dict(required)
    for name, module in tuple(sys.modules.items()):
        if (
            isinstance(module, ModuleType)
            and (name == "er2sim" or name.startswith("er2sim."))
        ):
            modules.setdefault(name, module)
    return modules


def _replace_function_references(
    modules: Mapping[str, ModuleType],
    attribute: str,
    original: Any,
    replacement: Any,
) -> None:
    for module in modules.values():
        if getattr(module, attribute, None) is original:
            setattr(module, attribute, replacement)


def _make_hardened_route_edge(
    navigation: ModuleType,
    original_edge: Callable[..., tuple[bool, str | None]],
    policy: HarnessSafetyPolicy,
) -> Callable[..., tuple[bool, str | None]]:
    @wraps(original_edge)
    def hardened_route_pose_edge_clear(
        start: Sequence[float],
        end: Sequence[float],
        obstacles: Iterable[Any],
        *,
        footprint: Any = None,
        translation_sample: float = policy.base_translation_sample,
        rotation_sample: float = policy.base_rotation_sample,
    ) -> tuple[bool, str | None]:
        footprint = (
            navigation.DEFAULT_BASE_FOOTPRINT
            if footprint is None
            else footprint
        )
        translation_step = _positive_cap(
            translation_sample,
            policy.base_translation_sample,
            "translation_sample",
        )
        rotation_step = _positive_cap(
            rotation_sample,
            policy.base_rotation_sample,
            "rotation_sample",
        )
        start_pose = _pose3(start)
        end_pose = _pose3(end)
        obstacle_list = tuple(obstacles)
        if not obstacle_list:
            return True, None

        route_pose_clear = navigation.route_pose_clear
        clear, blocker = route_pose_clear(
            start_pose,
            obstacle_list,
            footprint=footprint,
        )
        if not clear:
            return False, blocker

        if not _supports_sweep_certificate(footprint):
            return original_edge(
                start_pose,
                end_pose,
                obstacle_list,
                footprint=footprint,
                translation_sample=translation_step,
                rotation_sample=rotation_step,
            )

        yaw_delta = _normalize_angle(end_pose[2] - start_pose[2])
        rotation_steps = max(
            1,
            int(math.ceil(abs(yaw_delta) / rotation_step)),
        )
        for index in range(rotation_steps):
            yaw_a = start_pose[2] + yaw_delta * (
                index / rotation_steps
            )
            yaw_b = start_pose[2] + yaw_delta * (
                (index + 1) / rotation_steps
            )
            clear, blocker = _certify_rotation_interval(
                navigation,
                start_pose[0],
                start_pose[1],
                yaw_a,
                yaw_b,
                obstacle_list,
                footprint,
                policy,
                depth=0,
            )
            if not clear:
                return False, blocker

        distance = math.hypot(
            end_pose[0] - start_pose[0],
            end_pose[1] - start_pose[1],
        )
        translation_steps = max(
            1,
            int(math.ceil(distance / translation_step)),
        )
        for index in range(translation_steps):
            ratio_a = index / translation_steps
            ratio_b = (index + 1) / translation_steps
            point_a = (
                start_pose[0]
                + (end_pose[0] - start_pose[0]) * ratio_a,
                start_pose[1]
                + (end_pose[1] - start_pose[1]) * ratio_a,
            )
            point_b = (
                start_pose[0]
                + (end_pose[0] - start_pose[0]) * ratio_b,
                start_pose[1]
                + (end_pose[1] - start_pose[1]) * ratio_b,
            )
            clear, blocker = _certify_translation_interval(
                navigation,
                point_a,
                point_b,
                end_pose[2],
                obstacle_list,
                footprint,
                policy,
                depth=0,
            )
            if not clear:
                return False, blocker
        return True, None

    setattr(
        hardened_route_pose_edge_clear,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened_route_pose_edge_clear


def _make_hardened_route_path(
    navigation: ModuleType,
    hardened_edge: Callable[..., tuple[bool, str | None]],
    original_path: Callable[..., tuple[bool, str | None]],
    policy: HarnessSafetyPolicy,
) -> Callable[..., tuple[bool, str | None]]:
    @wraps(original_path)
    def hardened_route_pose_path_clear(
        poses: Iterable[Sequence[float]],
        obstacles: Iterable[Any],
        *,
        footprint: Any = None,
        translation_sample: float = policy.base_translation_sample,
        rotation_sample: float = policy.base_rotation_sample,
    ) -> tuple[bool, str | None]:
        footprint = (
            navigation.DEFAULT_BASE_FOOTPRINT
            if footprint is None
            else footprint
        )
        path = [_pose3(pose) for pose in poses]
        obstacle_list = tuple(obstacles)
        if not path:
            return True, None
        if len(path) == 1:
            return navigation.route_pose_clear(
                path[0],
                obstacle_list,
                footprint=footprint,
            )
        for start, end in zip(path, path[1:]):
            clear, blocker = hardened_edge(
                start,
                end,
                obstacle_list,
                footprint=footprint,
                translation_sample=translation_sample,
                rotation_sample=rotation_sample,
            )
            if not clear:
                return False, blocker
        return True, None

    setattr(
        hardened_route_pose_path_clear,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened_route_pose_path_clear


def _supports_sweep_certificate(footprint: Any) -> bool:
    return (
        hasattr(footprint, "max_radius")
        and hasattr(footprint, "safety_margin")
        and hasattr(footprint, "__dataclass_fields__")
    )


def _certify_rotation_interval(
    navigation: ModuleType,
    x: float,
    y: float,
    yaw_a: float,
    yaw_b: float,
    obstacles: tuple[Any, ...],
    footprint: Any,
    policy: HarnessSafetyPolicy,
    *,
    depth: int,
) -> tuple[bool, str | None]:
    midpoint = (yaw_a + yaw_b) / 2.0
    guard = float(footprint.max_radius) * abs(yaw_b - yaw_a) / 2.0
    expanded = replace(
        footprint,
        safety_margin=float(footprint.safety_margin) + guard,
    )
    clear, blocker = navigation.route_pose_clear(
        (x, y, midpoint),
        obstacles,
        footprint=expanded,
    )
    if clear:
        return True, None
    exact_clear, exact_blocker = navigation.route_pose_clear(
        (x, y, midpoint),
        obstacles,
        footprint=footprint,
    )
    if not exact_clear:
        return False, exact_blocker
    if depth >= policy.refinement_depth:
        return False, blocker
    left_clear, left_blocker = _certify_rotation_interval(
        navigation,
        x,
        y,
        yaw_a,
        midpoint,
        obstacles,
        footprint,
        policy,
        depth=depth + 1,
    )
    if not left_clear:
        return False, left_blocker
    return _certify_rotation_interval(
        navigation,
        x,
        y,
        midpoint,
        yaw_b,
        obstacles,
        footprint,
        policy,
        depth=depth + 1,
    )


def _certify_translation_interval(
    navigation: ModuleType,
    point_a: tuple[float, float],
    point_b: tuple[float, float],
    yaw: float,
    obstacles: tuple[Any, ...],
    footprint: Any,
    policy: HarnessSafetyPolicy,
    *,
    depth: int,
) -> tuple[bool, str | None]:
    midpoint = (
        (point_a[0] + point_b[0]) / 2.0,
        (point_a[1] + point_b[1]) / 2.0,
    )
    guard = math.dist(point_a, point_b) / 2.0
    expanded = replace(
        footprint,
        safety_margin=float(footprint.safety_margin) + guard,
    )
    clear, blocker = navigation.route_pose_clear(
        (midpoint[0], midpoint[1], yaw),
        obstacles,
        footprint=expanded,
    )
    if clear:
        return True, None
    exact_clear, exact_blocker = navigation.route_pose_clear(
        (midpoint[0], midpoint[1], yaw),
        obstacles,
        footprint=footprint,
    )
    if not exact_clear:
        return False, exact_blocker
    if depth >= policy.refinement_depth:
        return False, blocker
    left_clear, left_blocker = _certify_translation_interval(
        navigation,
        point_a,
        midpoint,
        yaw,
        obstacles,
        footprint,
        policy,
        depth=depth + 1,
    )
    if not left_clear:
        return False, left_blocker
    return _certify_translation_interval(
        navigation,
        midpoint,
        point_b,
        yaw,
        obstacles,
        footprint,
        policy,
        depth=depth + 1,
    )


def _make_hardened_held_departure(
    original: Callable[..., dict[str, Any]],
    policy: HarnessSafetyPolicy,
) -> Callable[..., dict[str, Any]]:
    @wraps(original)
    def hardened_held_departure_prefix(
        scene: Any,
        object_id: str,
        target_yaw: float,
        *,
        minimum_distance: float = 0.0,
        max_distance: float = policy.held_departure_max_distance,
        step: float = policy.held_departure_candidate_step,
    ) -> dict[str, Any]:
        bounded_max_distance = _nonnegative_cap(
            max_distance,
            policy.held_departure_max_distance,
            "max_distance",
        )
        return original(
            scene,
            object_id,
            target_yaw,
            minimum_distance=_nonnegative_cap(
                minimum_distance,
                bounded_max_distance,
                "minimum_distance",
            ),
            max_distance=bounded_max_distance,
            step=_positive_cap(
                step,
                policy.held_departure_candidate_step,
                "step",
            ),
        )

    setattr(
        hardened_held_departure_prefix,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened_held_departure_prefix


def _make_hardened_empty_base_motion_assessment(
    control: ModuleType,
    original: Callable[[Any], dict[str, Any]],
    policy: HarnessSafetyPolicy,
) -> Callable[[Any], dict[str, Any]]:
    @wraps(original)
    def hardened_empty_base_motion_ready(
        scene: Any,
    ) -> dict[str, Any]:
        assessment = original(scene)
        faults = assessment.get("faults")
        joint_state = assessment.get("joint_state")
        if (
            assessment.get("ok")
            or not isinstance(faults, list)
            or not isinstance(joint_state, Mapping)
        ):
            return assessment

        finger_faults = [
            fault
            for fault in faults
            if isinstance(fault, Mapping)
            and fault.get("code")
            == "GRIPPER_NOT_OPEN_FOR_BASE_MOTION"
        ]
        if not finger_faults:
            return assessment
        try:
            current_sum = sum(
                float(joint_state[name])
                for name in control.FINGER_JOINTS
            )
            target_sum = sum(
                float(control.FINGER_OPEN_FINGERS[name])
                for name in control.FINGER_JOINTS
            )
        except (KeyError, TypeError, ValueError):
            return assessment
        if (
            not math.isfinite(current_sum)
            or not math.isfinite(target_sum)
            or current_sum
            < target_sum - policy.empty_finger_pair_tolerance
        ):
            return assessment

        remaining = [
            fault
            for fault in faults
            if not (
                isinstance(fault, Mapping)
                and fault.get("code")
                == "GRIPPER_NOT_OPEN_FOR_BASE_MOTION"
            )
        ]
        hardened = dict(assessment)
        hardened["faults"] = remaining
        hardened["ok"] = not remaining
        hardened["finger_pair_assessment"] = {
            "semantic": "opening_sum",
            "current_sum": current_sum,
            "target_sum": target_sum,
            "minimum_sum": (
                target_sum - policy.empty_finger_pair_tolerance
            ),
            "individual_faults_suppressed": len(finger_faults),
        }
        return hardened

    setattr(
        hardened_empty_base_motion_ready,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened_empty_base_motion_ready


def _make_hardened_open_gripper(
    control: ModuleType,
    original: Callable[..., None],
    policy: HarnessSafetyPolicy,
) -> Callable[..., None]:
    @wraps(original)
    def hardened_open_gripper(
        self: Any,
        *,
        gap: float | None = None,
        contact_entity_id: str | None = None,
        allow_contact: bool = False,
    ) -> None:
        if (
            str(getattr(self, "current_phase", ""))
            != "place_open_suspended_gripper"
        ):
            original(
                self,
                gap=gap,
                contact_entity_id=contact_entity_id,
                allow_contact=allow_contact,
            )
            return

        targets = {
            str(name): float(value)
            for name, value in (
                control.finger_targets_for_gap(gap)
                if gap is not None
                else control.FINGER_OPEN_FINGERS
            ).items()
        }
        targets["grip"] = float(control.GRIP_OPEN)
        target_sum = sum(
            targets[str(name)] for name in control.FINGER_JOINTS
        )
        minimum_sum = (
            target_sum
            - policy.suspended_release_finger_pair_tolerance
        )
        ready_since: float | None = None
        last_observation: dict[str, Any] = {}

        def release_clearance_ready() -> bool:
            nonlocal ready_since
            try:
                finger_positions = {
                    str(name): float(self.scene.joint_pos(name))
                    for name in control.FINGER_JOINTS
                }
                current_sum = sum(finger_positions.values())
                grip_position = float(self.scene.joint_pos("grip"))
                now = float(self.scene.time())
            except (AttributeError, KeyError, TypeError, ValueError):
                ready_since = None
                last_observation.clear()
                last_observation["observation_error"] = True
                return False
            ready = (
                math.isfinite(current_sum)
                and math.isfinite(grip_position)
                and math.isfinite(now)
                and current_sum >= minimum_sum
                and abs(grip_position - targets["grip"]) <= 0.005
            )
            if not ready:
                ready_since = None
            elif ready_since is None:
                ready_since = now
            ready_duration = (
                max(0.0, now - ready_since)
                if ready_since is not None
                else 0.0
            )
            last_observation.clear()
            last_observation.update(
                {
                    "model_version": policy.model_version,
                    "requested_gap": gap,
                    "finger_positions": finger_positions,
                    "current_sum": current_sum,
                    "target_sum": target_sum,
                    "minimum_sum": minimum_sum,
                    "grip_position": grip_position,
                    "grip_target": targets["grip"],
                    "grip_error": abs(
                        grip_position - targets["grip"]
                    ),
                    "ready": ready,
                    "ready_since": ready_since,
                    "ready_duration": ready_duration,
                    "required_settle_time": (
                        policy.suspended_release_settle_time
                    ),
                }
            )
            return ready and (
                now - ready_since
                >= policy.suspended_release_settle_time
            )

        try:
            self.move_arm(
                targets,
                tol=0.005,
                settle=policy.suspended_release_settle_time,
                stop_condition=release_clearance_ready,
                finger_contact_entity_id=contact_entity_id,
                finger_opening_contact_is_terminal=allow_contact,
            )
        except control.ToolTimeout as error:
            residual = dict(
                getattr(error, "residual_state", {}) or {}
            )
            residual["suspended_release_opening"] = dict(
                last_observation
            )
            raise control.ToolTimeout(
                getattr(error, "phase", "open_suspended_gripper"),
                residual_state=residual,
            ) from error

    setattr(
        hardened_open_gripper,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    return hardened_open_gripper


def _install_arm_hardening(
    motion: ModuleType,
    policy: HarnessSafetyPolicy,
) -> None:
    snapshot_class = motion.MujocoPlanningSnapshot
    backend_class = motion.RRTConnectBackend

    original_sync_name = (
        "_TASK_RECURSIVE_TREE_ORIGINAL_SEMANTIC_CONTACT_SYNC"
    )
    original_sync = getattr(
        snapshot_class,
        original_sync_name,
        None,
    )
    if original_sync is None:
        original_sync = snapshot_class.sync
        setattr(
            snapshot_class,
            original_sync_name,
            original_sync,
        )

    @wraps(original_sync)
    def hardened_semantic_contact_sync(
        self: Any,
        *args: Any,
        allowed_contact_entity_ids: Iterable[str] = (),
        allowed_held_contact_entity_ids: Iterable[str] = (),
        **kwargs: Any,
    ) -> Any:
        regular_contacts = tuple(
            str(entity_id)
            for entity_id in allowed_contact_entity_ids
            if str(entity_id) != "floor_1"
        )
        held_contacts = tuple(
            str(entity_id)
            for entity_id in allowed_held_contact_entity_ids
            if str(entity_id) != "floor_1"
        )
        setattr(
            self,
            "_task_recursive_tree_allow_held_floor_contact",
            any(
                str(entity_id) == "floor_1"
                for entity_id in allowed_held_contact_entity_ids
            ),
        )
        return original_sync(
            self,
            *args,
            allowed_contact_entity_ids=regular_contacts,
            allowed_held_contact_entity_ids=held_contacts,
            **kwargs,
        )

    setattr(
        hardened_semantic_contact_sync,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    snapshot_class.sync = hardened_semantic_contact_sync

    original_contact_name = (
        "_TASK_RECURSIVE_TREE_ORIGINAL_CONTACT_IS_INVALID"
    )
    original_contact = getattr(
        snapshot_class,
        original_contact_name,
        None,
    )
    if original_contact is None:
        original_contact = snapshot_class._contact_is_invalid
        setattr(
            snapshot_class,
            original_contact_name,
            original_contact,
        )

    @wraps(original_contact)
    def hardened_contact_is_invalid(
        self: Any,
        contact: Any,
    ) -> bool:
        if getattr(
            self,
            "_task_recursive_tree_allow_held_floor_contact",
            False,
        ):
            body1 = int(self.model.geom_bodyid[contact.geom1])
            body2 = int(self.model.geom_bodyid[contact.geom2])
            held_body = getattr(self, "_held_body", None)
            if held_body is not None and (
                body1 == held_body or body2 == held_body
            ):
                held_is_first = body1 == held_body
                other_body = body2 if held_is_first else body1
                other_geom = (
                    int(contact.geom2)
                    if held_is_first
                    else int(contact.geom1)
                )
                geom_name = motion.mujoco.mj_id2name(
                    self.model,
                    motion.mujoco.mjtObj.mjOBJ_GEOM,
                    other_geom,
                )
                if other_body == 0 and geom_name == "floor":
                    return False
        return bool(original_contact(self, contact))

    setattr(
        hardened_contact_is_invalid,
        "_task_recursive_tree_policy_fingerprint",
        policy.fingerprint,
    )
    snapshot_class._contact_is_invalid = hardened_contact_is_invalid

    original_edge_name = "_TASK_RECURSIVE_TREE_ORIGINAL_EDGE_VALID"
    original_edge = getattr(snapshot_class, original_edge_name, None)
    if original_edge is None:
        original_edge = snapshot_class.edge_valid
        setattr(snapshot_class, original_edge_name, original_edge)

    @wraps(original_edge)
    def hardened_arm_edge_valid(
        self: Any,
        start: Sequence[float],
        goal: Sequence[float],
        *,
        unit_resolution: float = policy.arm_edge_unit_resolution,
        include_start: bool = False,
    ) -> bool:
        return original_edge(
            self,
            start,
            goal,
            unit_resolution=_positive_cap(
                unit_resolution,
                policy.arm_edge_unit_resolution,
                "unit_resolution",
            ),
            include_start=include_start,
        )

    snapshot_class.edge_valid = hardened_arm_edge_valid

    original_dense_name = "_TASK_RECURSIVE_TREE_ORIGINAL_DENSIFY"
    original_densify = getattr(backend_class, original_dense_name, None)
    if original_densify is None:
        original_densify = backend_class._densify
        setattr(backend_class, original_dense_name, original_densify)

    @wraps(original_densify)
    def hardened_densify(
        path: list[Any],
        resolution: float = policy.arm_waypoint_unit_resolution,
    ) -> list[Any]:
        return original_densify(
            path,
            resolution=_positive_cap(
                resolution,
                policy.arm_waypoint_unit_resolution,
                "resolution",
            ),
        )

    backend_class._densify = staticmethod(hardened_densify)


def _positive_cap(value: Any, cap: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return min(number, float(cap))


def _nonnegative_cap(value: Any, cap: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return min(number, float(cap))


def _pose3(raw: Sequence[float]) -> tuple[float, float, float]:
    if len(raw) < 3:
        raise ValueError("route pose must contain x, y, and yaw")
    pose = (float(raw[0]), float(raw[1]), float(raw[2]))
    if not all(math.isfinite(value) for value in pose):
        raise ValueError("route pose must be finite")
    return pose


def _normalize_angle(value: float) -> float:
    return (float(value) + math.pi) % (2.0 * math.pi) - math.pi


__all__ = [
    "ARM_EDGE_UNIT_RESOLUTION",
    "ARM_WAYPOINT_UNIT_RESOLUTION",
    "BASE_ROTATION_SAMPLE",
    "BASE_TRANSLATION_SAMPLE",
    "DEFAULT_HARNESS_SAFETY_POLICY",
    "HARNESS_SAFETY_MODEL_VERSION",
    "HELD_DEPARTURE_CANDIDATE_STEP",
    "HELD_DEPARTURE_MAX_DISTANCE",
    "HELD_ROTATION_SAMPLE",
    "HELD_TRANSLATION_SAMPLE",
    "HarnessSafetyInstallation",
    "HarnessSafetyPolicy",
    "install_harness_safety",
    "safety_policy_artifact",
]
