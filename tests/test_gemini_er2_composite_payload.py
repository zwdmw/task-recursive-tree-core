from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from task_recursive_tree.integrations.gemini_er2 import (
    composite_payload as composite_payload_module,
)
from task_recursive_tree.integrations.gemini_er2.composite_payload import (
    FOLLOW_STATE_ATTRIBUTE,
    SNAPSHOT_ENTITY_IDS_ATTRIBUTE,
    bind_composite_payload_context,
    composite_payload_entity_ids,
)
from task_recursive_tree.integrations.gemini_er2.harness_safety import (
    install_harness_safety,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)


@pytest.fixture()
def payload_scene():
    install_harness_safety()
    scene_module = import_harness_module("er2sim.scene")
    perception_module = import_harness_module("er2sim.perception")
    scene = scene_module.SimScene(headless=True)
    perception = perception_module.OraclePerceptionService(scene)
    bind_composite_payload_context(scene, perception)
    try:
        yield scene_module, scene, perception
    finally:
        scene.close()


def test_payload_detection_recurses_only_through_stable_contents() -> None:
    class FakePerception:
        catalog = {
            "outer": {
                "body": "outer",
                "kind": "physical_object",
                "movable": True,
                "affordances": ["support_region"],
                "geometry": {},
            },
            "inner": {
                "body": "inner",
                "kind": "physical_object",
                "movable": True,
                "affordances": ["support_region"],
                "geometry": {},
            },
            "item": {
                "body": "item",
                "kind": "physical_object",
                "movable": True,
                "affordances": ["graspable"],
                "geometry": {},
            },
            "floating": {
                "body": "floating",
                "kind": "physical_object",
                "movable": True,
                "affordances": ["graspable"],
                "geometry": {},
            },
        }
        poses = {
            "outer": (0.0, 0.0, 0.0),
            "inner": (0.0, 0.0, 0.1),
            "item": (0.0, 0.0, 0.2),
            "floating": (0.0, 0.0, 0.8),
        }
        supports = {
            "inner": "outer",
            "item": "inner",
            "floating": "outer",
        }
        support_heights = {
            "outer": 0.1,
            "inner": 0.2,
        }

        def support_of(self, entity_id):
            return self.supports.get(entity_id)

        def entity_pose(self, entity_id):
            return self.poses.get(entity_id)

        def point_in_effective_support_region(
            self,
            _xy,
            subject_id,
            _owner_id,
        ):
            return subject_id != "floating"

        def support_surface_z(self, entity_id):
            return self.support_heights[entity_id]

        def entity_bottom_offset(self, _entity_id):
            return 0.0

    scene = SimpleNamespace(
        body={
            "outer": 1,
            "inner": 2,
            "item": 3,
            "floating": 4,
        }
    )

    assert composite_payload_entity_ids(
        scene,
        "outer",
        perception=FakePerception(),
    ) == ("inner", "item")


def test_scene_follow_preserves_content_pose_relative_to_container(
    payload_scene,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    before_local = _relative_pose(
        scene_module,
        scene.model,
        scene.data,
        scene.body["box_1"],
        scene.body["cup_1"],
    )
    before_world = np.asarray(
        scene.data.xpos[scene.body["cup_1"]],
        dtype=float,
    ).copy()

    scene.start_object_kin_follow("box_1")
    assert getattr(scene, FOLLOW_STATE_ATTRIBUTE) is not None
    (base, yaw) = scene.base_pose()
    scene.set_base_pose_kin(
        x=float(base[0]) + 0.20,
        y=float(base[1]) + 0.10,
        yaw=float(yaw) + 0.35,
    )
    scene._apply_base_kin()
    scene._apply_object_kin_follow()

    after_local = _relative_pose(
        scene_module,
        scene.model,
        scene.data,
        scene.body["box_1"],
        scene.body["cup_1"],
    )
    after_world = np.asarray(
        scene.data.xpos[scene.body["cup_1"]],
        dtype=float,
    )
    assert np.allclose(after_local[0], before_local[0], atol=1e-8)
    assert _quaternions_equivalent(
        after_local[1],
        before_local[1],
    )
    assert not np.allclose(after_world, before_world)

    scene.stop_object_kin_follow()
    assert getattr(scene, FOLLOW_STATE_ATTRIBUTE) is None


def test_planning_snapshot_projects_all_payload_members(
    payload_scene,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    motion = import_harness_module("er2sim.motion_planning")
    snapshot = motion.MujocoPlanningSnapshot(scene)

    snapshot.sync(held_entity_id="box_1")

    assert getattr(
        snapshot,
        SNAPSHOT_ENTITY_IDS_ATTRIBUTE,
    ) == ("cup_1",)
    cup_body = snapshot._planning_body_for_entity("cup_1")
    assert cup_body is not None
    before_local = _relative_pose(
        motion,
        snapshot.model,
        snapshot.data,
        snapshot._held_body,
        cup_body,
    )
    before_world = np.asarray(
        snapshot.data.xpos[cup_body],
        dtype=float,
    ).copy()
    current = snapshot.current_configuration().vector()
    target = snapshot.clamp(
        current + np.asarray([0.04, 0.03, 0.25], dtype=float)
    )

    snapshot.set_configuration(target)

    after_local = _relative_pose(
        motion,
        snapshot.model,
        snapshot.data,
        snapshot._held_body,
        cup_body,
    )
    after_world = np.asarray(
        snapshot.data.xpos[cup_body],
        dtype=float,
    )
    assert np.allclose(after_local[0], before_local[0], atol=1e-8)
    assert _quaternions_equivalent(
        after_local[1],
        before_local[1],
    )
    assert not np.allclose(after_world, before_world)


def test_payload_collision_policy_is_scoped_to_the_carried_assembly(
    payload_scene,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    motion = import_harness_module("er2sim.motion_planning")
    snapshot = motion.MujocoPlanningSnapshot(scene)
    snapshot.sync(held_entity_id="box_1")

    box_geom = _geom_id(motion, snapshot.model, "box_1_geom")
    cup_geom = _geom_id(motion, snapshot.model, "cup_1_geom")
    table_geom = _geom_id(motion, snapshot.model, "table_top")
    robot_geom = next(
        geom_id
        for geom_id in range(snapshot.model.ngeom)
        if int(snapshot.model.geom_bodyid[geom_id])
        in snapshot._robot_bodies
    )

    assert snapshot._contact_is_invalid(
        _contact(box_geom, cup_geom)
    ) is False
    assert snapshot._contact_is_invalid(
        _contact(cup_geom, table_geom)
    ) is True
    assert snapshot._contact_is_invalid(
        _contact(cup_geom, robot_geom)
    ) is True

    snapshot.sync(
        held_entity_id="box_1",
        allowed_held_contact_entity_ids=("table_1",),
    )
    assert snapshot._contact_is_invalid(
        _contact(box_geom, table_geom)
    ) is False
    assert snapshot._contact_is_invalid(
        _contact(cup_geom, table_geom)
    ) is True
    assert set(snapshot.last_collision or ()) == {"cup_1", "table"}

    snapshot.sync(
        held_entity_id="box_1",
        allowed_contact_entity_ids=("table_1",),
    )
    assert snapshot._contact_is_invalid(
        _contact(cup_geom, table_geom)
    ) is True


def test_live_payload_model_rejects_a_missing_member_body(
    payload_scene,
    monkeypatch,
) -> None:
    scene_module, scene, _perception = payload_scene
    control = import_harness_module("er2sim.control")
    monkeypatch.setattr(
        composite_payload_module,
        "composite_payload_entity_ids",
        lambda *_args, **_kwargs: ("cup_1",),
    )
    removed = scene.body.pop("cup_1")
    try:
        with pytest.raises(control.ControlError) as caught:
            scene.start_object_kin_follow("box_1")
    finally:
        scene.body["cup_1"] = removed

    assert caught.value.code == "PAYLOAD_MODEL_INCOMPLETE"
    assert caught.value.details["mode"] == "live_follow"
    assert caught.value.details["failures"] == [
        {
            "entity_id": "cup_1",
            "reason": "scene_body_unavailable",
        }
    ]
    assert scene._obj_kin is None
    assert getattr(scene, FOLLOW_STATE_ATTRIBUTE) is None


def test_planning_payload_model_rejects_a_missing_member_mapping(
    payload_scene,
    monkeypatch,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    motion = import_harness_module("er2sim.motion_planning")
    snapshot = motion.MujocoPlanningSnapshot(scene)
    original = snapshot._planning_body_for_entity
    monkeypatch.setattr(
        snapshot,
        "_planning_body_for_entity",
        lambda entity_id: (
            None if entity_id == "cup_1" else original(entity_id)
        ),
    )

    with pytest.raises(motion.PlanningError) as caught:
        snapshot.sync(held_entity_id="box_1")

    assert caught.value.code == "PAYLOAD_MODEL_INCOMPLETE"
    assert "cup_1:planning_body_unavailable" in caught.value.message


def test_planning_payload_model_rejects_a_non_free_member(
    payload_scene,
    monkeypatch,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    motion = import_harness_module("er2sim.motion_planning")
    snapshot = motion.MujocoPlanningSnapshot(scene)
    monkeypatch.setattr(
        composite_payload_module,
        "_free_joint_addresses",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(motion.PlanningError) as caught:
        snapshot.sync(held_entity_id="box_1")

    assert caught.value.code == "PAYLOAD_MODEL_INCOMPLETE"
    assert "cup_1:planning_free_joint_required" in caught.value.message


def test_planning_payload_model_rejects_a_partial_member_mapping(
    payload_scene,
    monkeypatch,
) -> None:
    scene_module, scene, perception = payload_scene
    _stage_in_box(scene_module, scene, perception, "cup_1")
    _stage_in_box(scene_module, scene, perception, "can_1")
    motion = import_harness_module("er2sim.motion_planning")
    snapshot = motion.MujocoPlanningSnapshot(scene)
    original = snapshot._planning_body_for_entity
    monkeypatch.setattr(
        snapshot,
        "_planning_body_for_entity",
        lambda entity_id: (
            None if entity_id == "can_1" else original(entity_id)
        ),
    )

    with pytest.raises(motion.PlanningError) as caught:
        snapshot.sync(held_entity_id="box_1")

    assert caught.value.code == "PAYLOAD_MODEL_INCOMPLETE"
    assert "can_1:planning_body_unavailable" in caught.value.message
    assert "cup_1:" not in caught.value.message


def _stage_in_box(
    scene_module,
    scene,
    perception,
    entity_id: str,
) -> None:
    world_xy = perception.local_xy_to_world(
        "box_1",
        (0.05, 0.04),
    )
    support_z = perception.support_surface_z("box_1")
    assert world_xy is not None
    assert support_z is not None
    position = (
        float(world_xy[0]),
        float(world_xy[1]),
        float(support_z)
        + float(perception.entity_bottom_offset(entity_id)),
    )
    body_id = scene.body[entity_id]
    joint_id = int(scene.model.body_jntadr[body_id])
    qpos_address = int(scene.model.jnt_qposadr[joint_id])
    dof_address = int(scene.model.jnt_dofadr[joint_id])
    scene.data.qpos[qpos_address:qpos_address + 3] = position
    scene.data.qpos[qpos_address + 3:qpos_address + 7] = [
        1.0,
        0.0,
        0.0,
        0.0,
    ]
    scene.data.qvel[dof_address:dof_address + 6] = 0.0
    scene_module.mujoco.mj_forward(scene.model, scene.data)
    assert perception.support_of(entity_id) == "box_1"
    assert entity_id in composite_payload_entity_ids(
        scene,
        "box_1",
        perception=perception,
    )


def _relative_pose(
    module,
    model,
    data,
    parent_body: int,
    child_body: int,
) -> tuple[np.ndarray, np.ndarray]:
    module.mujoco.mj_forward(model, data)
    inverse_parent = np.empty(4, dtype=float)
    local_position = np.empty(3, dtype=float)
    local_quaternion = np.empty(4, dtype=float)
    module.mujoco.mju_negQuat(
        inverse_parent,
        np.asarray(data.xquat[parent_body], dtype=float),
    )
    module.mujoco.mju_rotVecQuat(
        local_position,
        np.asarray(
            data.xpos[child_body] - data.xpos[parent_body],
            dtype=float,
        ),
        inverse_parent,
    )
    module.mujoco.mju_mulQuat(
        local_quaternion,
        inverse_parent,
        np.asarray(data.xquat[child_body], dtype=float),
    )
    module.mujoco.mju_normalize4(local_quaternion)
    return local_position, local_quaternion


def _quaternions_equivalent(left, right) -> bool:
    return bool(
        np.allclose(left, right, atol=1e-8)
        or np.allclose(left, -np.asarray(right), atol=1e-8)
    )


def _geom_id(module, model, name: str) -> int:
    geom_id = module.mujoco.mj_name2id(
        model,
        module.mujoco.mjtObj.mjOBJ_GEOM,
        name,
    )
    assert geom_id >= 0
    return int(geom_id)


def _contact(geom1: int, geom2: int):
    return SimpleNamespace(
        dist=0.0,
        geom1=geom1,
        geom2=geom2,
    )
