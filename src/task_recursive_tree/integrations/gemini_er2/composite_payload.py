from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
from types import ModuleType
from typing import Any, Callable


COMPOSITE_PAYLOAD_MODEL_VERSION = (
    "task_recursive_tree_composite_payload/1.1"
)
PERCEPTION_ATTRIBUTE = "_task_recursive_tree_perception"
FOLLOW_STATE_ATTRIBUTE = "_task_recursive_tree_payload_follow"
SNAPSHOT_MEMBERS_ATTRIBUTE = "_task_recursive_tree_payload_members"
SNAPSHOT_ENTITY_IDS_ATTRIBUTE = (
    "_task_recursive_tree_payload_entity_ids"
)
SNAPSHOT_CONTACT_BODIES_ATTRIBUTE = (
    "_task_recursive_tree_payload_contact_bodies"
)
SNAPSHOT_ASSEMBLY_BODIES_ATTRIBUTE = (
    "_task_recursive_tree_held_assembly_bodies"
)


@dataclass(frozen=True)
class _PayloadTransform:
    entity_id: str
    body_id: int
    qpos_address: int
    dof_address: int
    local_position: tuple[float, float, float]
    local_quaternion: tuple[float, float, float, float]
    contact_body_ids: frozenset[int]


@dataclass(frozen=True)
class _PayloadFollowState:
    root_entity_id: str
    root_body_id: int
    members: tuple[_PayloadTransform, ...]


class _PayloadModelIncomplete(RuntimeError):
    def __init__(
        self,
        root_entity_id: str,
        detected_entity_ids: tuple[str, ...],
        failures: list[dict[str, str]],
    ) -> None:
        self.root_entity_id = str(root_entity_id)
        self.detected_entity_ids = tuple(
            str(entity_id) for entity_id in detected_entity_ids
        )
        self.failures = tuple(dict(failure) for failure in failures)
        missing = ", ".join(
            f"{failure['entity_id']}:{failure['reason']}"
            for failure in self.failures
        )
        super().__init__(
            f"Composite payload for {self.root_entity_id!r} is incomplete: "
            f"{missing or 'unknown mapping failure'}"
        )

    def details(self, *, mode: str) -> dict[str, Any]:
        return {
            "failure_mode": "PAYLOAD_MODEL_INCOMPLETE",
            "model_version": COMPOSITE_PAYLOAD_MODEL_VERSION,
            "mode": str(mode),
            "root_entity_id": self.root_entity_id,
            "detected_entity_ids": list(self.detected_entity_ids),
            "failures": [dict(failure) for failure in self.failures],
        }


def bind_composite_payload_context(
    scene: Any,
    perception: Any,
) -> None:
    """Make perception available to process-local scene/planner adapters."""
    if scene is None or perception is None:
        return
    setattr(scene, PERCEPTION_ATTRIBUTE, perception)


def composite_payload_entity_ids(
    scene: Any,
    root_entity_id: str,
    *,
    perception: Any = None,
) -> tuple[str, ...]:
    """Return stable movable contents recursively supported by ``root``."""
    perception = (
        perception
        if perception is not None
        else getattr(scene, PERCEPTION_ATTRIBUTE, None)
    )
    catalog = getattr(perception, "catalog", None)
    if not isinstance(catalog, Mapping):
        return ()

    root_id = str(root_entity_id)
    root_meta = _metadata(catalog, root_id)
    if not _is_movable_container(root_meta):
        return ()

    candidates = tuple(
        str(entity_id)
        for entity_id, raw_meta in catalog.items()
        if str(entity_id) != root_id
        and _is_movable_physical(raw_meta)
    )
    discovered: list[str] = []
    seen = {root_id}
    owners = [root_id]
    while owners:
        owner_id = owners.pop(0)
        owner_meta = _metadata(catalog, owner_id)
        if not _is_movable_container(owner_meta):
            continue
        for candidate_id in candidates:
            if candidate_id in seen:
                continue
            if not _is_stably_supported(
                perception,
                candidate_id,
                owner_id,
                owner_meta,
            ):
                continue
            seen.add(candidate_id)
            discovered.append(candidate_id)
            if _is_movable_container(
                _metadata(catalog, candidate_id)
            ):
                owners.append(candidate_id)
    return tuple(discovered)


def install_composite_payload_support(
    *,
    scene_module: ModuleType,
    motion_module: ModuleType,
    control_module: ModuleType,
    fingerprint: str,
) -> None:
    """Patch Harness scene and planning classes in the current process."""
    _install_scene_follow_support(
        scene_module,
        control_module,
        fingerprint,
    )
    _install_planning_support(motion_module, fingerprint)


def _metadata(
    catalog: Mapping[Any, Any],
    entity_id: str,
) -> Mapping[str, Any]:
    value = catalog.get(entity_id)
    return value if isinstance(value, Mapping) else {}


def _is_movable_physical(raw_meta: Any) -> bool:
    if not isinstance(raw_meta, Mapping):
        return False
    return (
        raw_meta.get("kind") == "physical_object"
        and bool(raw_meta.get("movable"))
    )


def _is_movable_container(raw_meta: Any) -> bool:
    if not _is_movable_physical(raw_meta):
        return False
    return "support_region" in {
        str(value) for value in raw_meta.get("affordances", ())
    }


def _is_stably_supported(
    perception: Any,
    subject_id: str,
    owner_id: str,
    owner_meta: Mapping[str, Any],
) -> bool:
    try:
        if str(perception.support_of(subject_id)) != owner_id:
            return False
        subject_pose = _finite_pose(
            perception.entity_pose(subject_id),
            length=3,
        )
        owner_pose = _finite_pose(
            perception.entity_pose(owner_id),
            length=3,
        )
        if subject_pose is None or owner_pose is None:
            return False
        effective_region = getattr(
            perception,
            "point_in_effective_support_region",
            None,
        )
        if callable(effective_region):
            inside = effective_region(
                (subject_pose[0], subject_pose[1]),
                subject_id,
                owner_id,
            )
        else:
            inside = perception.point_in_support_region(
                (subject_pose[0], subject_pose[1]),
                owner_id,
            )
        if not bool(inside):
            return False

        support_z = float(perception.support_surface_z(owner_id))
        bottom = (
            subject_pose[2]
            - float(perception.entity_bottom_offset(subject_id))
        )
        geometry = owner_meta.get("geometry")
        geometry = geometry if isinstance(geometry, Mapping) else {}
        tolerance = _nonnegative_number(
            geometry.get("payload_support_tolerance", 0.03),
            default=0.03,
        )
        if abs(bottom - support_z) > tolerance:
            return False
        rim_offset = geometry.get("rim_offset")
        if rim_offset is not None:
            rim_z = owner_pose[2] + float(rim_offset)
            if bottom > rim_z + tolerance:
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _finite_pose(
    raw: Any,
    *,
    length: int,
) -> tuple[float, ...] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < length:
        return None
    pose = tuple(float(raw[index]) for index in range(length))
    return pose if all(math.isfinite(value) for value in pose) else None


def _nonnegative_number(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number) or number < 0.0:
        return float(default)
    return number


def _install_scene_follow_support(
    scene_module: ModuleType,
    control_module: ModuleType,
    fingerprint: str,
) -> None:
    scene_class = scene_module.SimScene
    marker = "_TASK_RECURSIVE_TREE_COMPOSITE_PAYLOAD_FINGERPRINT"
    if getattr(scene_class, marker, None) == fingerprint:
        return

    original_start = _stored_method(
        scene_class,
        "start_object_kin_follow",
    )
    original_stop = _stored_method(
        scene_class,
        "stop_object_kin_follow",
    )
    original_apply = _stored_method(
        scene_class,
        "_apply_object_kin_follow",
    )

    @wraps(original_start)
    def start_object_kin_follow(self: Any, entity_id: str) -> None:
        setattr(self, FOLLOW_STATE_ATTRIBUTE, None)
        scene_module.mujoco.mj_forward(self.model, self.data)
        original_start(self, entity_id)
        if getattr(self, "_obj_kin", None) is None:
            return
        try:
            members = _capture_live_payload_members(
                scene_module,
                self,
                str(entity_id),
            )
        except _PayloadModelIncomplete as error:
            original_stop(self)
            setattr(self, FOLLOW_STATE_ATTRIBUTE, None)
            raise control_module.ControlError(
                "PAYLOAD_MODEL_INCOMPLETE",
                str(error),
                details=error.details(mode="live_follow"),
            ) from error
        if not members:
            return
        root_body = _scene_body_id(self, str(entity_id))
        if root_body is None:
            return
        setattr(
            self,
            FOLLOW_STATE_ATTRIBUTE,
            _PayloadFollowState(
                root_entity_id=str(entity_id),
                root_body_id=root_body,
                members=members,
            ),
        )

    @wraps(original_stop)
    def stop_object_kin_follow(self: Any) -> None:
        try:
            original_stop(self)
        finally:
            setattr(self, FOLLOW_STATE_ATTRIBUTE, None)

    @wraps(original_apply)
    def apply_object_kin_follow(self: Any) -> None:
        original_apply(self)
        state = getattr(self, FOLLOW_STATE_ATTRIBUTE, None)
        if not isinstance(state, _PayloadFollowState):
            return
        active = getattr(self, "_obj_kin", None)
        if (
            not isinstance(active, tuple)
            or not active
            or str(active[0]) != state.root_entity_id
        ):
            setattr(self, FOLLOW_STATE_ATTRIBUTE, None)
            return
        scene_module.mujoco.mj_forward(self.model, self.data)
        _project_members(
            scene_module.np,
            scene_module.mujoco,
            self.model,
            self.data,
            state.root_body_id,
            state.members,
        )

    _mark_wrapper(start_object_kin_follow, fingerprint)
    _mark_wrapper(stop_object_kin_follow, fingerprint)
    _mark_wrapper(apply_object_kin_follow, fingerprint)
    scene_class.start_object_kin_follow = start_object_kin_follow
    scene_class.stop_object_kin_follow = stop_object_kin_follow
    scene_class._apply_object_kin_follow = apply_object_kin_follow
    setattr(scene_class, marker, fingerprint)


def _install_planning_support(
    motion_module: ModuleType,
    fingerprint: str,
) -> None:
    snapshot_class = motion_module.MujocoPlanningSnapshot
    marker = "_TASK_RECURSIVE_TREE_COMPOSITE_PAYLOAD_FINGERPRINT"
    if getattr(snapshot_class, marker, None) == fingerprint:
        return

    original_sync = _stored_method(snapshot_class, "sync")
    original_project = _stored_method(
        snapshot_class,
        "_project_held_body",
    )
    original_contact = _stored_method(
        snapshot_class,
        "_contact_is_invalid",
    )

    @wraps(original_sync)
    def sync(self: Any, *args: Any, **kwargs: Any) -> None:
        _clear_snapshot_payload(self)
        original_sync(self, *args, **kwargs)
        held_entity_id = kwargs.get("held_entity_id")
        if not held_entity_id or self._held_body is None:
            return
        try:
            members = _capture_planning_payload_members(
                motion_module,
                self,
                str(held_entity_id),
            )
        except _PayloadModelIncomplete as error:
            _clear_snapshot_payload(self)
            raise motion_module.PlanningError(
                "PAYLOAD_MODEL_INCOMPLETE",
                str(error),
            ) from error
        if not members:
            return
        payload_contact_bodies = frozenset(
            body_id
            for member in members
            for body_id in member.contact_body_ids
        )
        held_contact_bodies = _body_subtree(
            self.model,
            int(self._held_body),
        )
        setattr(self, SNAPSHOT_MEMBERS_ATTRIBUTE, members)
        setattr(
            self,
            SNAPSHOT_ENTITY_IDS_ATTRIBUTE,
            tuple(member.entity_id for member in members),
        )
        setattr(
            self,
            SNAPSHOT_CONTACT_BODIES_ATTRIBUTE,
            payload_contact_bodies,
        )
        setattr(
            self,
            SNAPSHOT_ASSEMBLY_BODIES_ATTRIBUTE,
            frozenset(held_contact_bodies | payload_contact_bodies),
        )
        self._project_held_body()

    @wraps(original_project)
    def project_held_body(self: Any) -> None:
        original_project(self)
        members = getattr(self, SNAPSHOT_MEMBERS_ATTRIBUTE, ())
        if self._held_body is None or not members:
            return
        _project_members(
            motion_module.np,
            motion_module.mujoco,
            self.model,
            self.data,
            int(self._held_body),
            tuple(members),
        )

    @wraps(original_contact)
    def contact_is_invalid(self: Any, contact: Any) -> bool:
        payload_bodies = getattr(
            self,
            SNAPSHOT_CONTACT_BODIES_ATTRIBUTE,
            frozenset(),
        )
        if not payload_bodies:
            return original_contact(self, contact)
        if float(contact.dist) > self.collision_margin + 1e-6:
            return False

        body1 = int(self.model.geom_bodyid[contact.geom1])
        body2 = int(self.model.geom_bodyid[contact.geom2])
        payload1 = body1 in payload_bodies
        payload2 = body2 in payload_bodies
        if not (payload1 or payload2):
            return original_contact(self, contact)

        assembly_bodies = getattr(
            self,
            SNAPSHOT_ASSEMBLY_BODIES_ATTRIBUTE,
            frozenset(),
        )
        if body1 in assembly_bodies and body2 in assembly_bodies:
            return False

        moving_body = body1 if payload1 else body2
        other_body = body2 if payload1 else body1
        if other_body in self._robot_bodies:
            _record_collision(
                motion_module,
                self,
                moving_body,
                other_body,
            )
            return True
        _record_collision(
            motion_module,
            self,
            moving_body,
            other_body,
        )
        return True

    _mark_wrapper(sync, fingerprint)
    _mark_wrapper(project_held_body, fingerprint)
    _mark_wrapper(contact_is_invalid, fingerprint)
    snapshot_class.sync = sync
    snapshot_class._project_held_body = project_held_body
    snapshot_class._contact_is_invalid = contact_is_invalid
    setattr(snapshot_class, marker, fingerprint)


def _stored_method(owner: type[Any], name: str) -> Callable[..., Any]:
    stored_name = (
        f"_TASK_RECURSIVE_TREE_ORIGINAL_{name.upper()}"
    )
    original = getattr(owner, stored_name, None)
    if original is None:
        original = getattr(owner, name)
        setattr(owner, stored_name, original)
    return original


def _mark_wrapper(wrapper: Callable[..., Any], fingerprint: str) -> None:
    setattr(
        wrapper,
        "_task_recursive_tree_policy_fingerprint",
        fingerprint,
    )


def _capture_live_payload_members(
    scene_module: ModuleType,
    scene: Any,
    root_entity_id: str,
) -> tuple[_PayloadTransform, ...]:
    perception = getattr(scene, PERCEPTION_ATTRIBUTE, None)
    entity_ids = composite_payload_entity_ids(
        scene,
        root_entity_id,
        perception=perception,
    )
    root_body = _scene_body_id(scene, root_entity_id)
    if root_body is None:
        raise _PayloadModelIncomplete(
            root_entity_id,
            entity_ids,
            [
                {
                    "entity_id": root_entity_id,
                    "reason": "root_scene_body_unavailable",
                }
            ],
        )
    scene_module.mujoco.mj_forward(scene.model, scene.data)
    return _capture_members(
        scene_module.np,
        scene_module.mujoco,
        scene.model,
        scene.data,
        root_body,
        root_entity_id,
        entity_ids,
        lambda entity_id: _scene_body_id(scene, entity_id),
    )


def _capture_planning_payload_members(
    motion_module: ModuleType,
    snapshot: Any,
    root_entity_id: str,
) -> tuple[_PayloadTransform, ...]:
    live_scene = snapshot.live_scene
    perception = getattr(live_scene, PERCEPTION_ATTRIBUTE, None)
    entity_ids = composite_payload_entity_ids(
        live_scene,
        root_entity_id,
        perception=perception,
    )
    live_root_body = _scene_body_id(live_scene, root_entity_id)
    if live_root_body is None:
        raise _PayloadModelIncomplete(
            root_entity_id,
            entity_ids,
            [
                {
                    "entity_id": root_entity_id,
                    "reason": "root_live_body_unavailable",
                }
            ],
        )
    motion_module.mujoco.mj_forward(
        live_scene.model,
        live_scene.data,
    )
    root_position = motion_module.np.asarray(
        live_scene.data.xpos[live_root_body],
        dtype=float,
    )
    root_quaternion = motion_module.np.asarray(
        live_scene.data.xquat[live_root_body],
        dtype=float,
    )

    members: list[_PayloadTransform] = []
    failures: list[dict[str, str]] = []
    for entity_id in entity_ids:
        live_body = _scene_body_id(live_scene, entity_id)
        planning_body = snapshot._planning_body_for_entity(entity_id)
        if live_body is None:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "live_body_unavailable",
                }
            )
            continue
        if planning_body is None:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "planning_body_unavailable",
                }
            )
            continue
        addresses = _free_joint_addresses(
            motion_module.mujoco,
            snapshot.model,
            int(planning_body),
        )
        if addresses is None:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "planning_free_joint_required",
                }
            )
            continue
        local_position, local_quaternion = _local_pose(
            motion_module.np,
            motion_module.mujoco,
            root_position,
            root_quaternion,
            motion_module.np.asarray(
                live_scene.data.xpos[live_body],
                dtype=float,
            ),
            motion_module.np.asarray(
                live_scene.data.xquat[live_body],
                dtype=float,
            ),
        )
        members.append(
            _PayloadTransform(
                entity_id=entity_id,
                body_id=int(planning_body),
                qpos_address=addresses[0],
                dof_address=addresses[1],
                local_position=local_position,
                local_quaternion=local_quaternion,
                contact_body_ids=frozenset(
                    _body_subtree(
                        snapshot.model,
                        int(planning_body),
                    )
                ),
            )
        )
    if failures:
        raise _PayloadModelIncomplete(
            root_entity_id,
            entity_ids,
            failures,
        )
    return tuple(members)


def _capture_members(
    np_module: Any,
    mujoco_module: Any,
    model: Any,
    data: Any,
    root_body: int,
    root_entity_id: str,
    entity_ids: tuple[str, ...],
    body_resolver: Callable[[str], int | None],
) -> tuple[_PayloadTransform, ...]:
    root_position = np_module.asarray(
        data.xpos[root_body],
        dtype=float,
    )
    root_quaternion = np_module.asarray(
        data.xquat[root_body],
        dtype=float,
    )
    members: list[_PayloadTransform] = []
    failures: list[dict[str, str]] = []
    for entity_id in entity_ids:
        body_id = body_resolver(entity_id)
        if body_id is None:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "scene_body_unavailable",
                }
            )
            continue
        if body_id == root_body:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "payload_body_aliases_root",
                }
            )
            continue
        addresses = _free_joint_addresses(
            mujoco_module,
            model,
            body_id,
        )
        if addresses is None:
            failures.append(
                {
                    "entity_id": entity_id,
                    "reason": "scene_free_joint_required",
                }
            )
            continue
        local_position, local_quaternion = _local_pose(
            np_module,
            mujoco_module,
            root_position,
            root_quaternion,
            np_module.asarray(data.xpos[body_id], dtype=float),
            np_module.asarray(data.xquat[body_id], dtype=float),
        )
        members.append(
            _PayloadTransform(
                entity_id=entity_id,
                body_id=body_id,
                qpos_address=addresses[0],
                dof_address=addresses[1],
                local_position=local_position,
                local_quaternion=local_quaternion,
                contact_body_ids=frozenset(
                    _body_subtree(model, body_id)
                ),
            )
        )
    if failures:
        raise _PayloadModelIncomplete(
            root_entity_id,
            entity_ids,
            failures,
        )
    return tuple(members)


def _local_pose(
    np_module: Any,
    mujoco_module: Any,
    root_position: Any,
    root_quaternion: Any,
    member_position: Any,
    member_quaternion: Any,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float, float],
]:
    inverse_root = np_module.empty(4, dtype=float)
    local_position = np_module.empty(3, dtype=float)
    local_quaternion = np_module.empty(4, dtype=float)
    mujoco_module.mju_negQuat(inverse_root, root_quaternion)
    mujoco_module.mju_rotVecQuat(
        local_position,
        member_position - root_position,
        inverse_root,
    )
    mujoco_module.mju_mulQuat(
        local_quaternion,
        inverse_root,
        member_quaternion,
    )
    mujoco_module.mju_normalize4(local_quaternion)
    return (
        tuple(float(value) for value in local_position),
        tuple(float(value) for value in local_quaternion),
    )


def _project_members(
    np_module: Any,
    mujoco_module: Any,
    model: Any,
    data: Any,
    root_body: int,
    members: tuple[_PayloadTransform, ...],
) -> None:
    root_position = np_module.asarray(
        data.xpos[root_body],
        dtype=float,
    )
    root_quaternion = np_module.asarray(
        data.xquat[root_body],
        dtype=float,
    )
    for member in members:
        world_offset = np_module.empty(3, dtype=float)
        world_quaternion = np_module.empty(4, dtype=float)
        mujoco_module.mju_rotVecQuat(
            world_offset,
            np_module.asarray(member.local_position, dtype=float),
            root_quaternion,
        )
        mujoco_module.mju_mulQuat(
            world_quaternion,
            root_quaternion,
            np_module.asarray(
                member.local_quaternion,
                dtype=float,
            ),
        )
        mujoco_module.mju_normalize4(world_quaternion)
        data.qpos[
            member.qpos_address:member.qpos_address + 3
        ] = root_position + world_offset
        data.qpos[
            member.qpos_address + 3:member.qpos_address + 7
        ] = world_quaternion
        data.qvel[
            member.dof_address:member.dof_address + 6
        ] = 0.0
    mujoco_module.mj_forward(model, data)


def _free_joint_addresses(
    mujoco_module: Any,
    model: Any,
    body_id: int,
) -> tuple[int, int] | None:
    joint_id = int(model.body_jntadr[body_id])
    if (
        joint_id < 0
        or int(model.jnt_type[joint_id])
        != int(mujoco_module.mjtJoint.mjJNT_FREE)
    ):
        return None
    return (
        int(model.jnt_qposadr[joint_id]),
        int(model.jnt_dofadr[joint_id]),
    )


def _scene_body_id(scene: Any, entity_id: str) -> int | None:
    body_map = getattr(scene, "body", None)
    if not isinstance(body_map, Mapping):
        return None
    body_id = body_map.get(entity_id)
    if body_id is None:
        perception = getattr(scene, PERCEPTION_ATTRIBUTE, None)
        catalog = getattr(perception, "catalog", {})
        metadata = (
            catalog.get(entity_id)
            if isinstance(catalog, Mapping)
            else None
        )
        if isinstance(metadata, Mapping):
            body_name = metadata.get("body")
            if body_name is not None:
                body_id = body_map.get(str(body_name))
    return int(body_id) if body_id is not None else None


def _body_subtree(model: Any, root_body: int) -> set[int]:
    bodies: set[int] = set()
    for body_id in range(int(model.nbody)):
        current = body_id
        while current > 0:
            if current == root_body:
                bodies.add(body_id)
                break
            current = int(model.body_parentid[current])
    if root_body == 0:
        bodies.add(0)
    return bodies


def _clear_snapshot_payload(snapshot: Any) -> None:
    setattr(snapshot, SNAPSHOT_MEMBERS_ATTRIBUTE, ())
    setattr(snapshot, SNAPSHOT_ENTITY_IDS_ATTRIBUTE, ())
    setattr(
        snapshot,
        SNAPSHOT_CONTACT_BODIES_ATTRIBUTE,
        frozenset(),
    )
    setattr(
        snapshot,
        SNAPSHOT_ASSEMBLY_BODIES_ATTRIBUTE,
        frozenset(),
    )


def _record_collision(
    motion_module: ModuleType,
    snapshot: Any,
    moving_body: int,
    other_body: int,
) -> None:
    snapshot.last_collision = (
        motion_module._object_name(
            snapshot.model,
            motion_module.mujoco.mjtObj.mjOBJ_BODY,
            moving_body,
        ),
        motion_module._object_name(
            snapshot.model,
            motion_module.mujoco.mjtObj.mjOBJ_BODY,
            other_body,
        ),
    )


__all__ = [
    "COMPOSITE_PAYLOAD_MODEL_VERSION",
    "FOLLOW_STATE_ATTRIBUTE",
    "PERCEPTION_ATTRIBUTE",
    "SNAPSHOT_ASSEMBLY_BODIES_ATTRIBUTE",
    "SNAPSHOT_CONTACT_BODIES_ATTRIBUTE",
    "SNAPSHOT_ENTITY_IDS_ATTRIBUTE",
    "SNAPSHOT_MEMBERS_ATTRIBUTE",
    "bind_composite_payload_context",
    "composite_payload_entity_ids",
    "install_composite_payload_support",
]
