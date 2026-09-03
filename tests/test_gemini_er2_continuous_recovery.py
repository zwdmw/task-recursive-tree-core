from __future__ import annotations

from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2.continuous_recovery import (
    adapt_continuous_session_class,
)


class BaseContinuousSession:
    def __init__(self, *, perception) -> None:
        self.perception = perception
        self.base_policy_calls = 0
        self._executor = None

    def _apply_reconciled_staging_constraints(
        self,
        active,
        gripper,
    ) -> None:
        del gripper
        self.base_policy_calls += 1
        original = list(active.get("avoid_owner_ids") or ())
        protected = list(
            active.get("failure_protected_owner_ids") or ()
        )
        active["staging_avoidance_decision"] = {
            "relaxed": False,
            "evidence_reusable": False,
            "reused_floor_owner_ids": [],
            "original_avoid_owner_ids": original,
            "effective_avoid_owner_ids": original,
            "failure_protected_owner_ids": protected,
            "rejection_reasons": ["failure_evidence_present"],
        }

    @staticmethod
    def _primary_recovery_failure_owner_ids(staging_source):
        return list(staging_source.get("destination_ids") or ())


class FakeCapabilities:
    snapshot = {}

    @classmethod
    def build_scene_capability_snapshot(cls, perception):
        del perception
        return cls.snapshot

    @staticmethod
    def entity_index(snapshot):
        return {
            item["entity_ref"]: item
            for item in snapshot.get("entities", ())
        }

    @staticmethod
    def region_index(snapshot):
        return {
            region["region_ref"]: region
            for entity in snapshot.get("entities", ())
            for region in entity.get("semantic_regions", ())
        }


def recovery_context(
    *,
    object_id: str = "box_1",
    destination_id: str = "floor_1",
) -> dict:
    return {
        "intent": "finalize_primary",
        "avoid_owner_ids": [destination_id, "plate_1"],
        "failure_protected_owner_ids": [
            destination_id,
            "plate_1",
        ],
        "staging_avoidance_source": {
            "source_kind": "failure",
            "task_type": "place",
            "object_ids": [object_id],
            "destination_ids": [destination_id],
            "has_failure_diagnostic": True,
        },
    }


def capability_snapshot(
    *,
    owner_id: str = "floor_1",
    category: str = "floor",
    policy_includes_owner: bool = True,
) -> dict:
    region_ref = f"{owner_id}/support"
    policy = {}
    if policy_includes_owner:
        policy["default_destination_region_ref"] = region_ref
    return {
        "entities": [
            {
                "entity_ref": owner_id,
                "category": category,
                "semantic_regions": [
                    {
                        "region_ref": region_ref,
                        "owner_ref": owner_id,
                        "selector": "support",
                        "available": True,
                        "capacity_mode": "multi_object",
                        "allowed_relations": ["on_support"],
                    }
                ],
            }
        ],
        "policies": {
            "clear_support_region": policy,
        },
    }


def adapted_session(snapshot: dict):
    FakeCapabilities.snapshot = snapshot
    session_type = adapt_continuous_session_class(
        BaseContinuousSession,
        capability_module=FakeCapabilities,
    )
    return session_type(perception=object())


def failed_place_node(
    node_id: str,
    *,
    object_id: str,
    destination_id: str,
    parent_id: str | None = None,
    children: tuple[str, ...] = (),
    finished_at: float = 1.0,
):
    spec = SimpleNamespace(
        node_id=node_id,
        parent_id=parent_id,
        task_type="place",
        action_ref=None,
        children=list(children),
        params={
            "object_ids": [object_id],
            "destination_ids": [destination_id],
            "participants": {
                "manipuland": [object_id],
                "destination": [destination_id],
            },
        },
        object_ref={"entity_id": object_id},
    )
    runtime = SimpleNamespace(
        status="FAILED",
        failure={
            "code": "COLLISION",
            "destination_id": destination_id,
        },
        last_result={
            "termination": "failed",
            "failure_code": "COLLISION",
            "residual_state": {"phase": "arm_motion"},
        },
        transaction_id=None,
        finished_at=finished_at,
    )
    return SimpleNamespace(
        node_id=node_id,
        spec=spec,
        runtime=runtime,
        status="FAILED",
    )


def attach_tree(session, *nodes) -> None:
    session._executor = SimpleNamespace(
        tree=SimpleNamespace(
            nodes={node.node_id: node for node in nodes},
        )
    )


def test_failed_held_item_can_reuse_its_trusted_floor_destination() -> None:
    session = adapted_session(capability_snapshot())
    active = recovery_context()
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="box_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    assert session.base_policy_calls == 1
    assert active["avoid_owner_ids"] == ["plate_1"]
    assert active["failure_protected_owner_ids"] == ["plate_1"]
    decision = active["staging_avoidance_decision"]
    assert decision["relaxed"] is True
    assert decision["relaxation_mode"] == (
        "failed_original_floor_staging"
    )
    assert decision["reused_floor_owner_ids"] == ["floor_1"]
    assert decision["effective_avoid_owner_ids"] == ["plate_1"]
    assert decision["failure_protected_owner_ids"] == ["plate_1"]
    exception = decision["failed_floor_recovery"]
    assert exception["allowed"] is True
    assert exception["owner_id"] == "floor_1"
    assert exception["region_ref"] == "floor_1/support"
    assert exception["original_failure_protected_owner_ids"] == [
        "floor_1",
        "plate_1",
    ]
    assert exception["effective_failure_protected_owner_ids"] == [
        "plate_1"
    ]


def test_finalize_refines_late_root_failure_to_deepest_held_place(
) -> None:
    session = adapted_session(capability_snapshot())
    root = failed_place_node(
        "place_plate",
        object_id="plate_1",
        destination_id="floor_1",
        children=("place_cup_parent",),
        finished_at=30.0,
    )
    cup_parent = failed_place_node(
        "place_cup_parent",
        object_id="cup_1",
        destination_id="floor_1",
        parent_id="place_plate",
        children=("place_cup_deep",),
        finished_at=20.0,
    )
    cup_deep = failed_place_node(
        "place_cup_deep",
        object_id="cup_1",
        destination_id="floor_1",
        parent_id="place_cup_parent",
        finished_at=10.0,
    )
    attach_tree(session, root, cup_parent, cup_deep)
    active = recovery_context(object_id="plate_1")
    active["staging_avoidance_source"]["source_node_id"] = (
        "place_plate"
    )
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="cup_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    source = active["staging_avoidance_source"]
    assert source["source_node_id"] == "place_cup_deep"
    assert source["object_ids"] == ["cup_1"]
    assert source["destination_ids"] == ["floor_1"]
    assert active["avoid_owner_ids"] == []
    assert active["failure_protected_owner_ids"] == []
    refinement = active["staging_source_refinement"]
    assert refinement["status"] == "refined"
    assert refinement["selected_tree_depth"] == 2
    assert refinement["original_source"]["object_ids"] == ["plate_1"]
    assert refinement["final_source"]["object_ids"] == ["cup_1"]
    decision = active["staging_avoidance_decision"]
    assert decision["relaxed"] is True
    assert decision["source_refinement"]["selected_source_node_id"] == (
        "place_cup_deep"
    )


def test_finalize_refines_table_root_to_held_box_floor_branch() -> None:
    session = adapted_session(capability_snapshot())
    root = failed_place_node(
        "place_plate_on_table",
        object_id="plate_1",
        destination_id="table_1",
        children=("place_box_on_floor",),
        finished_at=30.0,
    )
    box_floor = failed_place_node(
        "place_box_on_floor",
        object_id="box_1",
        destination_id="floor_1",
        parent_id="place_plate_on_table",
        finished_at=20.0,
    )
    attach_tree(session, root, box_floor)
    active = recovery_context(
        object_id="plate_1",
        destination_id="table_1",
    )
    active["staging_avoidance_source"]["source_node_id"] = (
        "place_plate_on_table"
    )
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="box_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    source = active["staging_avoidance_source"]
    assert source["source_node_id"] == "place_box_on_floor"
    assert source["object_ids"] == ["box_1"]
    assert source["destination_ids"] == ["floor_1"]
    refinement = active["staging_source_refinement"]
    assert refinement["status"] == "refined"
    assert refinement["effective_avoid_owner_ids"] == ["floor_1"]
    assert refinement[
        "effective_failure_protected_owner_ids"
    ] == ["floor_1"]
    assert active["avoid_owner_ids"] == []
    assert active["failure_protected_owner_ids"] == []
    decision = active["staging_avoidance_decision"]
    assert decision["relaxed"] is True
    assert decision["reused_floor_owner_ids"] == ["floor_1"]


def test_finalize_source_refinement_fails_closed_without_matching_item(
) -> None:
    session = adapted_session(capability_snapshot())
    root = failed_place_node(
        "place_plate",
        object_id="plate_1",
        destination_id="floor_1",
        finished_at=30.0,
    )
    attach_tree(session, root)
    active = recovery_context(object_id="plate_1")
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="cup_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    assert active["staging_avoidance_source"]["object_ids"] == [
        "plate_1"
    ]
    assert active["avoid_owner_ids"] == ["floor_1", "plate_1"]
    refinement = active["staging_source_refinement"]
    assert refinement["status"] == "no_matching_source"
    assert refinement["rejection_reasons"] == [
        "no_matching_failed_place_source"
    ]
    assert active["staging_avoidance_decision"]["relaxed"] is False


def test_finalize_source_refinement_requires_secure_hold() -> None:
    session = adapted_session(capability_snapshot())
    cup = failed_place_node(
        "place_cup",
        object_id="cup_1",
        destination_id="floor_1",
    )
    attach_tree(session, cup)
    active = recovery_context(object_id="plate_1")
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="cup_1",
        attachment_state="uncertain",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    refinement = active["staging_source_refinement"]
    assert refinement["status"] == "ineligible"
    assert "held_object_not_secure" in refinement["rejection_reasons"]
    assert active["staging_avoidance_source"]["object_ids"] == [
        "plate_1"
    ]
    assert active["staging_avoidance_decision"]["relaxed"] is False


def test_refined_non_floor_destination_remains_protected() -> None:
    session = adapted_session(capability_snapshot(
        owner_id="table_1",
        category="table",
    ))
    cup = failed_place_node(
        "place_cup",
        object_id="cup_1",
        destination_id="table_1",
    )
    attach_tree(session, cup)
    active = recovery_context(
        object_id="plate_1",
        destination_id="table_1",
    )
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="cup_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    assert active["staging_source_refinement"]["status"] == "refined"
    assert active["avoid_owner_ids"] == ["table_1"]
    assert active["failure_protected_owner_ids"] == ["table_1"]
    exception = active["staging_avoidance_decision"][
        "failed_floor_recovery"
    ]
    assert exception["allowed"] is False
    assert "destination_not_floor" in exception["rejection_reasons"]


def test_ambiguous_held_place_destinations_do_not_refine_source(
) -> None:
    session = adapted_session(capability_snapshot())
    root = failed_place_node(
        "place_plate",
        object_id="plate_1",
        destination_id="floor_1",
        children=("place_cup_floor_1", "place_cup_floor_2"),
    )
    cup_floor_1 = failed_place_node(
        "place_cup_floor_1",
        object_id="cup_1",
        destination_id="floor_1",
        parent_id="place_plate",
    )
    cup_floor_2 = failed_place_node(
        "place_cup_floor_2",
        object_id="cup_1",
        destination_id="floor_2",
        parent_id="place_plate",
    )
    attach_tree(session, root, cup_floor_1, cup_floor_2)
    active = recovery_context(object_id="plate_1")
    active["avoid_owner_ids"] = ["floor_1", "floor_2"]
    active["failure_protected_owner_ids"] = [
        "floor_1",
        "floor_2",
    ]
    gripper = SimpleNamespace(
        occupancy="holding",
        held_entity_id="cup_1",
        attachment_state="secure",
    )

    session._apply_reconciled_staging_constraints(active, gripper)

    refinement = active["staging_source_refinement"]
    assert refinement["status"] == "ambiguous"
    assert refinement["ambiguous_destination_ids"] == [
        "floor_1",
        "floor_2",
    ]
    assert active["staging_avoidance_source"]["object_ids"] == [
        "plate_1"
    ]
    assert active["avoid_owner_ids"] == ["floor_1", "floor_2"]
    assert active["staging_avoidance_decision"]["relaxed"] is False


@pytest.mark.parametrize(
    ("gripper", "snapshot", "expected_reason"),
    [
        (
            SimpleNamespace(
                occupancy="empty",
                held_entity_id=None,
                attachment_state="none",
            ),
            capability_snapshot(),
            "gripper_not_holding",
        ),
        (
            SimpleNamespace(
                occupancy="holding",
                held_entity_id="cup_1",
                attachment_state="secure",
            ),
            capability_snapshot(),
            "held_object_mismatch",
        ),
        (
            SimpleNamespace(
                occupancy="holding",
                held_entity_id="box_1",
                attachment_state="secure",
            ),
            capability_snapshot(category="table"),
            "destination_not_floor",
        ),
        (
            SimpleNamespace(
                occupancy="holding",
                held_entity_id="box_1",
                attachment_state="secure",
            ),
            capability_snapshot(policy_includes_owner=False),
            "destination_not_trusted_open_floor",
        ),
    ],
)
def test_failed_destination_protection_is_preserved_without_all_evidence(
    gripper,
    snapshot,
    expected_reason,
) -> None:
    session = adapted_session(snapshot)
    active = recovery_context()

    session._apply_reconciled_staging_constraints(active, gripper)

    assert active["avoid_owner_ids"] == ["floor_1", "plate_1"]
    assert active["failure_protected_owner_ids"] == [
        "floor_1",
        "plate_1",
    ]
    decision = active["staging_avoidance_decision"]
    assert decision["relaxed"] is False
    exception = decision["failed_floor_recovery"]
    assert exception["allowed"] is False
    assert expected_reason in exception["rejection_reasons"]
