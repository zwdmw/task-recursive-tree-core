from __future__ import annotations

from dataclasses import replace

from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.artifacts import (
    ArtifactMetadata,
    NavigationPlan,
    TransferPlan,
    payload_transform_hash,
)
from task_recursive_tree.runtime.freshness import ArtifactFreshnessChecker
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    Observation,
    RobotState,
    WorldSnapshot,
)
from task_recursive_tree.world.model import WorldModel


def test_freshness_is_derived_from_dependency_versions() -> None:
    model = RobotModel()
    entity = EntityState("box", "object", Pose(1.5, 1.5), version=2)
    snapshot = WorldSnapshot(
        revision=3,
        frame_graph_revision=1,
        grid=GridMap(4, 4, revision=7),
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0), state_epoch=4),
        entities={"box": entity},
    )
    plan = NavigationPlan(
        artifact_id="nav-test",
        metadata=ArtifactMetadata(
            snapshot_ref=snapshot.snapshot_ref,
            dependency_versions=(
                ("entity:box", 2),
                ("frame_graph", 1),
                ("map", 7),
            ),
            frame_graph_revision=1,
            robot_state_epoch=4,
            robot_model_version=model.model_version,
            collision_model_version=model.collision_model_version,
        ),
        purpose="test",
        start=Pose(0.5, 0.5),
        goal=Pose(2.5, 2.5),
        path=(Pose(0.5, 0.5), Pose(2.5, 2.5)),
        path_cost=2.8,
        clearance_radius=model.base_radius,
        ignored_entity_ids=frozenset(),
    )
    checker = ArtifactFreshnessChecker()
    assert checker.evaluate(
        plan,
        snapshot,
        robot_model_version=model.model_version,
        collision_model_version=model.collision_model_version,
    ).fresh

    changed = replace(
        snapshot,
        entities={"box": replace(entity, version=3)},
    )
    result = checker.evaluate(
        plan,
        changed,
        robot_model_version=model.model_version,
        collision_model_version=model.collision_model_version,
    )
    assert not result.fresh
    assert result.mismatches == ("entity:box: expected 2, observed 3",)


def test_new_navigation_obstacle_invalidates_occupancy_fingerprint() -> None:
    model = RobotModel()
    snapshot = WorldSnapshot(
        revision=1,
        frame_graph_revision=0,
        grid=GridMap(5, 5),
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0)),
        entities={},
    )
    dependency = snapshot.dependency_versions(
        include_nav_obstacles=True
    )
    plan = NavigationPlan(
        artifact_id="nav-scene",
        metadata=ArtifactMetadata(
            snapshot_ref=snapshot.snapshot_ref,
            dependency_versions=dependency,
            frame_graph_revision=0,
            robot_state_epoch=0,
            robot_model_version=model.model_version,
            collision_model_version=model.collision_model_version,
        ),
        purpose="scene-test",
        start=Pose(0.5, 0.5),
        goal=Pose(4.5, 4.5),
        path=(Pose(0.5, 0.5), Pose(4.5, 4.5)),
        path_cost=5.6,
        clearance_radius=model.base_radius,
    )
    added = replace(
        snapshot,
        entities={
            "new-wall": EntityState(
                "new-wall",
                "object",
                Pose(2.5, 2.5),
                properties={"nav_obstacle": True},
            )
        },
    )
    result = ArtifactFreshnessChecker().evaluate(
        plan,
        added,
        robot_model_version=model.model_version,
        collision_model_version=model.collision_model_version,
    )
    assert not result.fresh
    assert result.mismatches[0].startswith("nav_occupancy:")


def test_transfer_payload_hash_is_checked_independently() -> None:
    model = RobotModel()
    object_state = EntityState(
        "payload", "object", Pose(1.5, 1.5), radius=0.2
    )
    snapshot = WorldSnapshot(
        revision=1,
        frame_graph_revision=0,
        grid=GridMap(5, 5),
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0)),
        entities={"payload": object_state},
    )
    metadata = ArtifactMetadata(
        snapshot_ref=snapshot.snapshot_ref,
        dependency_versions=(("map", 0),),
        frame_graph_revision=0,
        robot_state_epoch=0,
        robot_model_version=model.model_version,
        collision_model_version=model.collision_model_version,
        payload_transform_hash=payload_transform_hash("payload", 0.2),
    )
    transfer = TransferPlan(
        artifact_id="transfer-payload",
        metadata=metadata,
        object_id="payload",
        destination_id="destination",
        navigation_plan_ref="nav",
        destination_stance=Pose(2.5, 1.5),
        placement_pose=Pose(3.5, 1.5),
        transport_joints=model.transport_joints,
        to_transport_path=(model.transport_joints,),
        placement_path=(model.transport_joints,),
    )
    changed = replace(
        snapshot,
        entities={"payload": replace(object_state, radius=0.35)},
    )
    result = ArtifactFreshnessChecker().evaluate(
        transfer,
        changed,
        robot_model_version=model.model_version,
        collision_model_version=model.collision_model_version,
    )
    assert not result.fresh
    assert result.mismatches[0].startswith("payload_transform:")


def test_entity_reappearance_changes_dependency_generation() -> None:
    grid = GridMap(4, 4)
    robot = RobotState(Pose(0.5, 0.5), (0.0, 0.0))
    entity = EntityState("box", "object", Pose(1.5, 1.5))
    world = WorldModel(
        grid,
        Observation(
            robot=robot,
            entities=(entity,),
            source_epoch="camera-a",
            sequence=1,
        ),
    )
    first = world.snapshot()
    first_dependency = dict(
        first.dependency_versions(("box",))
    )["entity:box"]

    world.ingest(
        Observation(
            robot=robot,
            entities=(),
            source_epoch="camera-a",
            sequence=2,
        )
    )
    reappeared = world.ingest(
        Observation(
            robot=robot,
            entities=(entity,),
            source_epoch="camera-a",
            sequence=3,
        )
    )

    assert reappeared.entity("box").version == 0
    assert reappeared.entity("box").generation == 1
    assert (
        dict(reappeared.dependency_versions(("box",)))["entity:box"]
        != first_dependency
    )


def test_partial_observation_does_not_delete_unobserved_entities() -> None:
    grid = GridMap(4, 4)
    robot = RobotState(Pose(0.5, 0.5), (0.0, 0.0))
    first = EntityState("first", "object", Pose(1.5, 1.5))
    second = EntityState("second", "object", Pose(2.5, 1.5))
    world = WorldModel(
        grid,
        Observation(
            robot=robot,
            entities=(first, second),
            source_epoch="camera-a",
            sequence=1,
        ),
    )

    snapshot = world.ingest(
        Observation(
            robot=robot,
            entities=(replace(first, pose=Pose(1.5, 2.5)),),
            source_epoch="camera-a",
            sequence=2,
            complete=False,
            confidence=0.8,
        )
    )

    assert set(snapshot.entities) == {"first", "second"}
    assert snapshot.entity("first").version == 1
    assert snapshot.observation_complete is False
    assert snapshot.observation_confidence == 0.8
