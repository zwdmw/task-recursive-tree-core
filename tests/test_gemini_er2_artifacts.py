from __future__ import annotations

from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2.artifacts import (
    ArtifactPolicyError,
    HarnessArtifactBridge,
)
from task_recursive_tree.integrations.gemini_er2.harness_safety import (
    DEFAULT_HARNESS_SAFETY_POLICY,
    HARNESS_SAFETY_MODEL_VERSION,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)
from task_recursive_tree.integrations.gemini_er2.route_validation import (
    ROUTE_VALIDATION_MODEL_VERSION,
)
from task_recursive_tree.task.model import (
    ControlKind,
    NodeOrigin,
    OperationKind,
    TaskNodeSpec,
)
from task_recursive_tree.task.store import TaskTreeStore


def _consumer() -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id="program/reposition",
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "path_ref": "path/1",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "artifact_kind": "detour_path",
                            "producer_node_id": "program/plan",
                            "continuation_node_id": "program/reposition",
                            "ref_key": "path_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )


def _artifact(**changes) -> dict:
    policy = DEFAULT_HARNESS_SAFETY_POLICY
    control = import_harness_module("er2sim.control")
    artifact = {
        "artifact_ref": "path/1",
        "artifact_kind": "detour_path",
        "kind": "detour_path",
        "producer_node_id": "program/plan",
        "continuation_node_id": "program/reposition",
        "collision_model_version": HARNESS_SAFETY_MODEL_VERSION,
        "route_validation_model_version": (
            ROUTE_VALIDATION_MODEL_VERSION
        ),
        "route_validation_policy_fingerprint": policy.fingerprint,
        "footprint_translation_sample": policy.base_translation_sample,
        "footprint_rotation_sample": policy.base_rotation_sample,
        "base_path_controller_model_version": getattr(
            control,
            "BASE_PATH_CONTROLLER_MODEL_VERSION",
            "continuous_pose_tracker/1.0",
        ),
        "route_validation": {
            "ok": True,
            "translation_sample": policy.base_translation_sample,
            "rotation_sample": policy.base_rotation_sample,
            "safety_policy_fingerprint": policy.fingerprint,
        },
    }
    artifact.update(changes)
    return artifact


def _bridge(artifact: dict) -> HarnessArtifactBridge:
    world = SimpleNamespace(
        revision=7,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"path/1": artifact},
            )
        },
    )
    return HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
    )


def test_explicit_route_artifact_must_match_declared_contract() -> None:
    module = import_harness_module("er2sim.task_artifacts")
    bridge = _bridge(_artifact(producer_node_id="wrong/producer"))

    with pytest.raises(
        module.ArtifactContractError,
        match="producer mismatch",
    ):
        bridge.resolve_for(
            _consumer(),
            artifact_kind="detour_path",
            ref_key="path_ref",
        )


def test_route_artifact_policy_mismatch_invalidates_publication() -> None:
    module = import_harness_module("er2sim.task_artifacts")
    bridge = _bridge(
        _artifact(route_validation_policy_fingerprint="old-policy")
    )

    with pytest.raises(
        module.ArtifactContractError,
        match="missing detour_path publication",
    ):
        bridge.resolve_for(
            _consumer(),
            artifact_kind="detour_path",
            ref_key="path_ref",
        )

    assert bridge.task().artifacts["path/1"]["invalidated"] is True


def test_current_route_policy_is_recorded_as_consumed() -> None:
    bridge = _bridge(_artifact())
    node = _consumer()

    resolved = bridge.resolve_for(
        node,
        artifact_kind="detour_path",
        ref_key="path_ref",
    )

    assert resolved == "path/1"
    assert bridge.consumed(node.node_id) == [
        {
            "artifact_ref": "path/1",
            "artifact_kind": "detour_path",
            "producer_node_id": "program/plan",
            "continuation_node_id": node.node_id,
            "world_revision": 7,
        }
    ]


def test_repair_publication_replaces_transport_posture_contract() -> None:
    producer_id = "program/prepare-transport/plan"
    consumer_id = "program/prepare-transport/move"
    old_ref = "posture/old"
    world = SimpleNamespace(
        revision=8,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={
                    old_ref: {
                        "artifact_ref": old_ref,
                        "artifact_kind": "transport_posture",
                        "kind": "transport_posture",
                        "producer_node_id": producer_id,
                        "continuation_node_id": consumer_id,
                        "object_id": "cup_1",
                    }
                },
            )
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
    )
    repair_producer = TaskNodeSpec(
        node_id=f"{consumer_id}/repair-0/plan-transport-posture",
        task_type="plan_transport_posture",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
        parameters={
            "__gemini_er2__": {
                "metadata": {
                    "artifact_produces": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "transport_posture",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "posture_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )
    consumer = TaskNodeSpec(
        node_id=consumer_id,
        task_type="move_to_transport_posture",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["cup_1"],
            "participants": {"manipuland": ["cup_1"]},
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "transport_posture",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "posture_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    publication = bridge.publish(
        repair_producer,
        {
            "artifact_ref": "posture/new",
            "artifact_kind": "transport_posture",
            "kind": "transport_posture",
            "object_id": "cup_1",
        },
    )
    resolved = bridge.resolve_for(
        consumer,
        artifact_kind="transport_posture",
        ref_key="posture_ref",
    )

    assert publication["producer_node_id"] == producer_id
    assert publication["continuation_node_id"] == consumer_id
    assert publication["publisher_node_id"] == repair_producer.node_id
    assert resolved == "posture/new"
    assert bridge.consumed(consumer_id)[0]["artifact_ref"] == "posture/new"


def test_stale_layout_policy_is_reported_without_destroying_reservation() -> None:
    artifact = {
        "artifact_ref": "layout/1",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "producer_node_id": "program/select-space",
        "continuation_node_id": "program/place",
        "continuation_scope_id": "program/place",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.0],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=9,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"layout/1": artifact},
            )
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (
                False,
                "layout region occupancy is incomplete",
            )
        ),
    )
    consumer = TaskNodeSpec(
        node_id="program/place",
        task_type="place_object",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["cup_1"],
            "participants": {"manipuland": ["cup_1"]},
            "target_ref": "layout/1",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "artifact_kind": "layout_targets",
                            "producer_node_id": "program/select-space",
                            "continuation_node_id": "program/place",
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    with pytest.raises(
        ArtifactPolicyError,
        match="occupancy is incomplete",
    ):
        bridge.resolve_for(
            consumer,
            artifact_kind="layout_targets",
            ref_key="target_ref",
        )

    assert "invalidated" not in world.tasks[
        "task-1"
    ].artifacts["layout/1"]
    assert world.tasks["task-1"].artifacts[
        "layout/1"
    ]["reservation_status"] == "active"


def test_placement_preparation_consumes_layout_for_placement_object() -> None:
    producer_id = "program/place/select-space"
    consumer_id = "program/place/prepare/reposition"
    artifact_ref = "layout/place/cup"
    artifact = {
        "artifact_ref": artifact_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "producer_node_id": producer_id,
        "continuation_node_id": consumer_id,
        "continuation_scope_id": "program/place",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "plate_1",
        "region_ref": "plate_1/interior",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.085],
                "radius_m": 0.035,
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=11,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={artifact_ref: artifact},
            )
        },
    )
    region_space = SimpleNamespace(
        validate_layout_targets=lambda *_args, **_kwargs: (True, "")
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=region_space,
    )
    consumer = TaskNodeSpec(
        node_id=consumer_id,
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["plate_1"],
            "reference_ids": ["plate_1"],
            "placement_object_ids": ["cup_1"],
            "participants": {
                "reference": ["plate_1"],
                "placement_object": ["cup_1"],
            },
            "purpose": "placement_execution_preparation",
            "interaction_target_kind": "placement_pose",
            "target_ref": artifact_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": "program/place",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    resolved = bridge.resolve_for(
        consumer,
        artifact_kind="layout_targets",
        ref_key="target_ref",
    )

    assert resolved == artifact_ref
    assert bridge.consumed(consumer_id)[0]["artifact_ref"] == artifact_ref
    assert "invalidated" not in artifact
    assert artifact["reservation_status"] == "active"
    assert artifact["targets"]["cup_1"]["reservation_status"] == "active"


def test_batch_layout_consumer_ignores_fulfilled_sibling_target() -> None:
    producer_id = "program/place-sequence/select-space"
    consumer_id = "program/place-apple-2/assess"
    artifact_ref = "layout/place/red-apples"
    artifact = {
        "artifact_ref": artifact_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "producer_node_id": producer_id,
        "continuation_node_id": consumer_id,
        "continuation_scope_id": "program/place-sequence",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "plate_1",
        "region_ref": "plate_1/interior",
        "subject_ids": ["apple_1", "apple_2"],
        "targets": {
            "apple_1": {
                "local_xy": [0.0, -0.05],
                "radius_m": 0.035,
                "reservation_status": "fulfilled",
            },
            "apple_2": {
                "local_xy": [0.0, 0.05],
                "radius_m": 0.035,
                "reservation_status": "active",
            },
        },
    }
    world = SimpleNamespace(
        revision=12,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={artifact_ref: artifact},
            )
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (True, "")
        ),
    )
    consumer = TaskNodeSpec(
        node_id=consumer_id,
        task_type="assess_placeability",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["apple_2"],
            "destination_ids": ["plate_1"],
            "batch_object_ids": ["apple_1", "apple_2"],
            "participants": {
                "manipuland": ["apple_2"],
                "destination": ["plate_1"],
            },
            "target_ref": artifact_ref,
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    resolved = bridge.resolve_for(
        consumer,
        artifact_kind="layout_targets",
        ref_key="target_ref",
    )

    assert resolved == artifact_ref
    assert bridge.consumed(consumer_id)[0]["artifact_ref"] == artifact_ref
    assert artifact["targets"]["apple_1"]["reservation_status"] == (
        "fulfilled"
    )
    assert artifact["targets"]["apple_2"]["reservation_status"] == "active"


def test_placement_preparation_without_subject_fails_closed() -> None:
    producer_id = "program/place/select-space"
    consumer_id = "program/place/prepare/reposition"
    artifact_ref = "layout/place/cup"
    artifact = {
        "artifact_ref": artifact_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "producer_node_id": producer_id,
        "continuation_node_id": consumer_id,
        "continuation_scope_id": "program/place",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "plate_1",
        "region_ref": "plate_1/interior",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.085],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=11,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={artifact_ref: artifact},
            )
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (True, "")
        ),
    )
    consumer = TaskNodeSpec(
        node_id=consumer_id,
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["plate_1"],
            "reference_ids": ["plate_1"],
            "participants": {"reference": ["plate_1"]},
            "purpose": "placement_execution_preparation",
            "interaction_target_kind": "placement_pose",
            "target_ref": artifact_ref,
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    with pytest.raises(
        ArtifactPolicyError,
        match="has no placement object binding",
    ):
        bridge.resolve_for(
            consumer,
            artifact_kind="layout_targets",
            ref_key="target_ref",
        )

    assert artifact["reservation_status"] == "active"
    assert artifact["targets"]["cup_1"]["reservation_status"] == "active"


def test_place_object_without_manipuland_fails_closed() -> None:
    producer_id = "program/place/select-space"
    consumer_id = "program/place/place-object"
    artifact_ref = "layout/place/cup"
    artifact = {
        "artifact_ref": artifact_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "producer_node_id": producer_id,
        "continuation_node_id": consumer_id,
        "continuation_scope_id": "program/place",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "plate_1",
        "region_ref": "plate_1/interior",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.085],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=11,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={artifact_ref: artifact},
            )
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (True, "")
        ),
    )
    consumer = TaskNodeSpec(
        node_id=consumer_id,
        task_type="place_object",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "destination_ids": ["plate_1"],
            "participants": {"destination": ["plate_1"]},
            "target_ref": artifact_ref,
            "__gemini_er2__": {
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": consumer_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )

    with pytest.raises(
        ArtifactPolicyError,
        match="has no manipuland binding",
    ):
        bridge.resolve_for(
            consumer,
            artifact_kind="layout_targets",
            ref_key="target_ref",
        )

    assert artifact["reservation_status"] == "active"
    assert artifact["targets"]["cup_1"]["reservation_status"] == "active"


def test_layout_reconciliation_uses_canonical_target_then_releases_old() -> None:
    first = {
        "artifact_ref": "layout/1",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "targets": {
            "can_1": {
                "local_xy": [1.2, -0.1],
                "reservation_status": "active",
            },
            "cup_1": {
                "local_xy": [1.2, 0.2],
                "reservation_status": "active",
            },
        },
    }
    second = {
        "artifact_ref": "layout/2",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "targets": {
            "can_1": {
                "local_xy": [1.4, -0.1],
                "reservation_status": "active",
            },
        },
    }
    world = SimpleNamespace(
        revision=12,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={
                    "layout/1": first,
                    "layout/2": second,
                },
            )
        },
    )
    region_space = SimpleNamespace(
        assess_layout_target_fulfillment=(
            lambda artifact, entity_id: (
                entity_id == "can_1",
                {
                    "artifact_ref": artifact["artifact_ref"],
                    "entity_id": entity_id,
                    "verified": entity_id == "can_1",
                },
            )
        )
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=region_space,
    )

    reconciled = bridge.reconcile_layout_reservations(
        entity_ids=("can_1",),
        artifact_refs=("layout/1", "layout/2"),
    )

    assert len(reconciled) == 1
    assert reconciled[0]["artifact_ref"] == "layout/2"
    assert reconciled[0]["entity_id"] == "can_1"
    assert first["targets"]["can_1"]["reservation_status"] == "active"
    assert second["targets"]["can_1"]["reservation_status"] == "fulfilled"
    assert second["targets"]["can_1"]["fulfillment_source"] == (
        "verified_physical_reconciliation"
    )
    assert first["targets"]["cup_1"]["reservation_status"] == "active"
    assert first["reservation_status"] == "active"

    released = bridge.release_layout_reservations_for_entities(
        ("can_1", "cup_1"),
        reason="superseded_by_layout_artifact:layout/2",
        exclude_artifact_refs=("layout/2",),
        replacement_artifact_ref="layout/2",
    )

    assert {
        (item["artifact_ref"], item["entity_id"])
        for item in released
    } == {
        ("layout/1", "can_1"),
        ("layout/1", "cup_1"),
    }
    assert first["targets"]["can_1"]["reservation_status"] == "released"
    assert first["targets"]["cup_1"]["reservation_status"] == "released"
    assert first["reservation_status"] == "released"
    assert first["superseded_by_artifact_ref"] == "layout/2"
    assert second["targets"]["can_1"]["reservation_status"] == "fulfilled"


def test_publishing_new_layout_atomically_supersedes_old_reservation() -> None:
    old = {
        "artifact_ref": "layout/1",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "reservation_group": "staging-floor",
        "targets": {
            "cup_1": {
                "local_xy": [1.2, 0.2],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=15,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"layout/1": old},
            )
        },
    )
    producer = TaskNodeSpec(
        node_id="program/select-floor-space/repair-1",
        task_type="select_staging_region",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
        parameters={
            "target_ref": "layout/2",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_produces": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": "program/select-floor-space",
                            "continuation_node_id": "program/clear-floor",
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (True, "")
        ),
    )

    publication = bridge.publish(
        producer,
        {
            "artifact_ref": "layout/2",
            "artifact_kind": "layout_targets",
            "kind": "layout_targets",
            "source": "task_recursive_tree_batch_allocator/1.0",
            "anchor_id": "floor_1",
            "region_ref": "floor_1/support",
            "reservation_group": "staging-floor",
            "subject_ids": ["cup_1"],
            "targets": {
                "cup_1": {
                    "local_xy": [1.5, 0.3],
                }
            },
        },
    )

    stored = world.tasks["task-1"].artifacts
    assert publication["producer_node_id"] == "program/select-floor-space"
    assert publication["continuation_node_id"] == "program/clear-floor"
    assert publication["reservation_status"] == "active"
    assert publication["targets"]["cup_1"]["reservation_status"] == "active"
    assert publication["reservation_committed_world_revision"] == 15
    assert publication["superseded_reservations"] == [
        {
            "artifact_ref": "layout/1",
            "entity_id": "cup_1",
            "status": "released",
            "reason": "superseded_by_layout_artifact:layout/2",
            "replacement_artifact_ref": "layout/2",
        }
    ]
    assert stored["layout/2"]["reservation_status"] == "active"
    assert old["targets"]["cup_1"]["reservation_status"] == "released"
    assert old["targets"]["cup_1"]["superseded_by_artifact_ref"] == (
        "layout/2"
    )


def test_cross_group_layout_replacement_validates_before_release() -> None:
    old = {
        "artifact_ref": "layout/old",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "reservation_group": "old-staging-group",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [1.2, 0.2],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=18,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"layout/old": old},
            )
        },
    )

    class AtomicReplacementValidator:
        def __init__(self):
            self.calls = []

        def validate_layout_targets(
            self,
            publication,
            *,
            artifact_ref=None,
            ignored_reservation_bindings=(),
        ):
            call = {
                "artifact_ref": artifact_ref,
                "reservation_group": publication["reservation_group"],
                "ignored": tuple(ignored_reservation_bindings),
                "old_status_during_validation": old["targets"][
                    "cup_1"
                ]["reservation_status"],
            }
            self.calls.append(call)
            valid = (
                call["old_status_during_validation"] == "active"
                and call["ignored"] == (("layout/old", "cup_1"),)
            )
            return valid, "" if valid else "replacement was not atomic"

    validator = AtomicReplacementValidator()
    producer = TaskNodeSpec(
        node_id="program/select-floor-space/repair-cross-group",
        task_type="select_staging_region",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
        parameters={
            "target_ref": "layout/new",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_produces": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": "program/select-floor-space",
                            "continuation_node_id": "program/clear-floor",
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=validator,
    )

    publication = bridge.publish(
        producer,
        {
            "artifact_ref": "layout/new",
            "artifact_kind": "layout_targets",
            "kind": "layout_targets",
            "source": "task_recursive_tree_batch_allocator/1.0",
            "anchor_id": "floor_1",
            "region_ref": "floor_1/support",
            "reservation_group": "new-staging-group",
            "subject_ids": ["cup_1"],
            "targets": {
                "cup_1": {
                    "local_xy": [1.2, 0.2],
                    "reservation_status": "active",
                }
            },
        },
    )

    assert validator.calls == [
        {
            "artifact_ref": "layout/new",
            "reservation_group": "new-staging-group",
            "ignored": (("layout/old", "cup_1"),),
            "old_status_during_validation": "active",
        }
    ]
    assert publication["reservation_status"] == "active"
    assert old["targets"]["cup_1"]["reservation_status"] == "released"
    assert world.tasks["task-1"].artifacts["layout/new"] == publication


def test_invalid_layout_publication_does_not_release_old_reservation() -> None:
    module = import_harness_module("er2sim.task_artifacts")
    old = {
        "artifact_ref": "layout/1",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "reservation_group": "staging-floor",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [1.2, 0.2],
                "reservation_status": "active",
            }
        },
    }
    world = SimpleNamespace(
        revision=16,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"layout/1": old},
            )
        },
    )
    producer = TaskNodeSpec(
        node_id="program/select-floor-space/repair-1",
        task_type="select_staging_region",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
        parameters={
            "target_ref": "layout/2",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_produces": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": "program/select-floor-space",
                            "continuation_node_id": "program/clear-floor",
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (
                False,
                "target overlaps live occupied space",
            )
        ),
    )

    with pytest.raises(
        module.ArtifactContractError,
        match="target overlaps live occupied space",
    ):
        bridge.publish(
            producer,
            {
                "artifact_ref": "layout/2",
                "artifact_kind": "layout_targets",
                "kind": "layout_targets",
                "source": "task_recursive_tree_batch_allocator/1.0",
                "anchor_id": "floor_1",
                "region_ref": "floor_1/support",
                "reservation_group": "staging-floor",
                "subject_ids": ["cup_1"],
                "targets": {
                    "cup_1": {
                        "local_xy": [1.5, 0.3],
                    }
                },
            },
        )

    assert "layout/2" not in world.tasks["task-1"].artifacts
    assert old["reservation_status"] == "active"
    assert old["targets"]["cup_1"]["reservation_status"] == "active"


def test_same_ref_partial_republication_retains_other_subjects() -> None:
    existing = {
        "artifact_ref": "layout/1",
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "reservation_status": "active",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "reservation_group": "staging-floor",
        "subject_ids": ["can_1", "cup_1"],
        "layout_generation": 1,
        "targets": {
            "can_1": {
                "local_xy": [1.2, -0.2],
                "reservation_status": "active",
            },
            "cup_1": {
                "local_xy": [1.2, 0.2],
                "reservation_status": "active",
            },
        },
    }
    world = SimpleNamespace(
        revision=17,
        tasks={
            "task-1": SimpleNamespace(
                artifacts={"layout/1": existing},
            )
        },
    )
    producer = TaskNodeSpec(
        node_id="program/select-floor-space/repair-2",
        task_type="select_staging_region",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.REPAIR,
        parameters={
            "target_ref": "layout/1",
            "__gemini_er2__": {
                "metadata": {
                    "artifact_produces": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": "program/select-floor-space",
                            "continuation_node_id": "program/clear-floor",
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                }
            },
        },
    )
    bridge = HarnessArtifactBridge(
        runtime=SimpleNamespace(world=world),
        store=TaskTreeStore(),
        task_id="task-1",
        region_space=SimpleNamespace(
            validate_layout_targets=lambda *_args, **_kwargs: (True, "")
        ),
    )

    publication = bridge.publish(
        producer,
        {
            "artifact_ref": "layout/1",
            "artifact_kind": "layout_targets",
            "kind": "layout_targets",
            "source": "task_recursive_tree_batch_allocator/1.0",
            "anchor_id": "floor_1",
            "region_ref": "floor_1/support",
            "reservation_group": "staging-floor",
            "subject_ids": ["cup_1"],
            "targets": {
                "cup_1": {
                    "local_xy": [1.5, 0.3],
                    "reservation_status": "active",
                }
            },
        },
    )

    assert publication["subject_ids"] == ["can_1", "cup_1"]
    assert publication["replanned_subject_ids"] == ["cup_1"]
    assert publication["layout_generation"] == 2
    assert publication["targets"]["can_1"]["local_xy"] == [1.2, -0.2]
    assert publication["targets"]["can_1"][
        "reservation_status"
    ] == "active"
    assert publication["targets"]["cup_1"]["local_xy"] == [1.5, 0.3]
    assert world.tasks["task-1"].artifacts["layout/1"] == publication
