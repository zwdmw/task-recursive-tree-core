from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.integrations.gemini_er2 import (
    repair as repair_module,
)
from task_recursive_tree.integrations.gemini_er2.repair import (
    HarnessRepairResolver,
)
from task_recursive_tree.task.contracts import RepairContext
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    NodeOrigin,
    OperationKind,
    TaskNodeSpec,
)


class FakeFailureDiagnostic:
    def __init__(self, **values) -> None:
        self.__dict__.update(values)


def test_outcome_unknown_cannot_disable_reconciliation_with_details() -> None:
    diagnostic = Diagnostic(
        "OUTCOME_UNKNOWN",
        "typed unknown outcome",
        details={
            "physical_outcome_known": True,
            "requires_reconciliation": False,
        },
    )

    assert repair_module._requires_reconciliation(
        diagnostic,
        diagnostic.details,
    )


class RecordingRepairPlanner:
    calls = []
    sequence = 0

    def propose(self, *, parent, diagnostic, context):
        type(self).calls.append((parent, diagnostic, context))
        type(self).sequence += 1
        repair_node = SimpleNamespace(
            node_id=f"random-runtime-id-{type(self).sequence}",
            parent_id="random-parent",
            root_id="random-root",
            node_kind="repair",
            task_type="prepare_base_motion_posture",
            object_ref=None,
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={"predicate": "safety_clear"},
            goal={
                "predicate": "base_motion_ready",
                "participants": {"robot": ["robot_1"]},
                "desired_value": "true",
            },
            goal_scope="world",
            obligations=[],
            params={
                "purpose": "restore_base_motion_posture",
                "failed_stage": "escape_retract",
            },
            children=[],
            child_policy="sequence",
            decomposer_ref=None,
            tool_ref=None,
            action_ref="prepare_base_motion_posture",
            retry_policy={"max_attempts": 9, "max_repairs": 0},
            resource_policy={},
            origin="repair_planner",
            metadata={"runtime_only": True},
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref="builtin:prepare_base_motion_posture@1.0",
            max_attempts=1,
            invalidates_artifacts=["artifact/stale"],
            rationale="Restore a verified base-motion posture.",
        )


class FailingRepairPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, **_kwargs):
        self.calls += 1
        raise AssertionError(
            "protected layout failures must not use generic repairs"
        )


class ArtifactReplacementRepairPlanner:
    def __init__(self, task_type: str) -> None:
        self.task_type = task_type

    def propose(self, **_kwargs):
        repair_node = SimpleNamespace(
            node_id="runtime-replacement-plan",
            parent_id="runtime-parent",
            root_id="runtime-root",
            node_kind="repair",
            task_type=self.task_type,
            object_ref={"entity_id": "cup_1"},
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={},
            goal={},
            goal_scope="world",
            obligations=[],
            params={
                "object_ids": ["cup_1"],
                "participants": {"manipuland": ["cup_1"]},
                "system_check": self.task_type,
            },
            children=[],
            child_policy="sequence",
            decomposer_ref=None,
            tool_ref=None,
            action_ref=None,
            retry_policy={"max_attempts": 1, "max_repairs": 0},
            resource_policy={},
            origin="repair_planner",
            metadata={
                "artifact_produces": [
                    {
                        "artifact_kind": "unrelated",
                        "producer_node_id": "unrelated/producer",
                        "continuation_node_id": "unrelated/consumer",
                        "ref_key": "unrelated_ref",
                        "required": True,
                    }
                ]
            },
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref=f"builtin:{self.task_type}@1.0",
            max_attempts=1,
            invalidates_artifacts=[],
            rationale="Refresh the stale planning artifact.",
        )


class PlacementRepositionRepairPlanner:
    def propose(self, *, parent, **_kwargs):
        destination_id = str(parent.spec.params["destination_ids"][0])
        placement_object_id = str(parent.spec.params["object_ids"][0])
        repair_node = SimpleNamespace(
            node_id="runtime-placement-reposition",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="repair",
            task_type="reposition_for_interaction",
            object_ref={"entity_id": destination_id},
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={},
            goal={},
            goal_scope="world",
            obligations=[],
            params={
                "object_ids": [destination_id],
                "reference_ids": [destination_id],
                "participants": {
                    "reference": [destination_id],
                    "placement_object": [placement_object_id],
                },
                "placement_object_ids": [placement_object_id],
                "destination_ids": [destination_id],
                "interaction_target_kind": "placement_pose",
                "purpose": "repair_placement_reachability",
                "target_ref": parent.spec.params["target_ref"],
            },
            children=[],
            child_policy="sequence",
            decomposer_ref="builtin:reposition_for_interaction",
            tool_ref=None,
            action_ref=None,
            retry_policy={"max_attempts": 1, "max_repairs": 3},
            resource_policy={},
            origin="repair_planner",
            metadata={},
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref="builtin:reposition_for_placement@1.0",
            max_attempts=1,
            invalidates_artifacts=[],
            rationale="Reposition for the reserved placement target.",
        )


class VacatePlacementRepairPlanner:
    def propose(self, *, parent, **_kwargs):
        destination_id = str(parent.spec.params["destination_ids"][0])
        placement_object_id = str(parent.spec.params["object_ids"][0])
        repair_node = SimpleNamespace(
            node_id="runtime-vacate-placement",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="repair",
            task_type="vacate_placement_region",
            object_ref={"entity_id": destination_id},
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={},
            goal={},
            goal_scope="world",
            obligations=[],
            params={
                "object_ids": [destination_id],
                "reference_ids": [destination_id],
                "participants": {
                    "reference": [destination_id],
                    "placement_object": [placement_object_id],
                },
                "placement_object_ids": [placement_object_id],
                "destination_ids": [destination_id],
                "interaction_target_kind": "placement_pose",
                "purpose": "vacate_placement_region",
                "target_ref": parent.spec.params["target_ref"],
                "continuation_anchor_pose": [99.0, 99.0, 0.0],
                "continuation_goal_poses": [[99.0, 99.0, 0.0]],
            },
            children=[],
            child_policy="sequence",
            decomposer_ref="builtin:vacate_placement_region",
            tool_ref=None,
            action_ref=None,
            retry_policy={"max_attempts": 1, "max_repairs": 3},
            resource_policy={},
            origin="repair_planner",
            metadata={},
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref="builtin:vacate_placement_region@1.0",
            max_attempts=1,
            invalidates_artifacts=[],
            rationale="Vacate the reserved placement target.",
        )


class MultiArtifactDetourRepairPlanner:
    def propose(self, *, parent, **_kwargs):
        repair_node = SimpleNamespace(
            node_id="runtime-multi-artifact-detour",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="repair",
            task_type="plan_detour",
            object_ref=parent.spec.object_ref,
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={},
            goal={},
            goal_scope="world",
            obligations=[],
            params={
                "system_check": "plan_detour",
                "object_ids": list(parent.spec.params["object_ids"]),
                "reference_ids": list(parent.spec.params["reference_ids"]),
                "participants": dict(parent.spec.params["participants"]),
                "path_ref": parent.spec.params["path_ref"],
                "target_ref": parent.spec.params["target_ref"],
            },
            children=[],
            child_policy="sequence",
            decomposer_ref="builtin:plan_detour",
            tool_ref=None,
            action_ref=None,
            retry_policy={"max_attempts": 1, "max_repairs": 0},
            resource_policy={},
            origin="repair_planner",
            metadata={},
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref="builtin:refresh_detour@1.0",
            max_attempts=1,
            invalidates_artifacts=[],
            rationale="Refresh the path without discarding the layout.",
        )


class RouteBlockerRepairPlanner:
    def __init__(
        self,
        blocker_id="box_1",
        expected_blocking_ids=None,
    ):
        self.blocker_id = blocker_id
        self.expected_blocking_ids = (
            list(expected_blocking_ids)
            if expected_blocking_ids is not None
            else [blocker_id]
        )

    def propose(self, *, parent, diagnostic, **_kwargs):
        blocker_id = self.blocker_id
        assert diagnostic.blocking_entity_ids == self.expected_blocking_ids
        repair_node = SimpleNamespace(
            node_id=f"runtime-relocate-{blocker_id}",
            parent_id=parent.node_id,
            root_id=parent.spec.root_id,
            node_kind="repair",
            task_type="relocate_blocker",
            object_ref={"entity_id": blocker_id},
            actor_ref=None,
            from_state=None,
            to_state=None,
            preconditions={},
            goal={
                "predicate": "moved_from_pose",
                "participants": {"object": [blocker_id]},
                "desired_value": "true",
                "parameters": {
                    "baseline_pose": [0.6, -1.0, 0.0],
                    "minimum_distance": 0.39,
                },
            },
            goal_scope="world",
            obligations=[],
            params={
                "object_ids": [blocker_id],
                "participants": {"object": [blocker_id]},
                "continuation_anchor_pose": [99.0, 99.0, 0.0],
                "continuation_goal_poses": [[99.0, 99.0, 0.0]],
            },
            children=[],
            child_policy="sequence",
            decomposer_ref="builtin:relocate_blocker",
            tool_ref=None,
            action_ref=None,
            retry_policy={"max_attempts": 1, "max_repairs": 3},
            resource_policy={},
            origin="repair_planner",
            metadata={},
        )
        return SimpleNamespace(
            repair_node=repair_node,
            rule_ref="builtin:relocate_path_blocker@1.0",
            max_attempts=1,
            invalidates_artifacts=[],
            rationale="Relocate the verified route blocker.",
        )


class ConsumedArtifacts:
    def __init__(self, values) -> None:
        self.values = list(values)

    def consumed(self, node_id):
        assert node_id
        return list(self.values)


def install_fake_harness(monkeypatch) -> None:
    diagnostics = ModuleType("er2sim.task_diagnostics")
    diagnostics.FailureDiagnostic = FakeFailureDiagnostic
    repair_planner = ModuleType("er2sim.repair_planner")
    repair_planner.RepairPlanner = RecordingRepairPlanner
    monkeypatch.setitem(
        sys.modules,
        "er2sim.task_diagnostics",
        diagnostics,
    )
    monkeypatch.setitem(
        sys.modules,
        "er2sim.repair_planner",
        repair_planner,
    )
    monkeypatch.setattr(
        repair_module,
        "import_harness_module",
        lambda name, **_: sys.modules[name],
    )


def failed_node() -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id="program/place/reposition",
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "participants": {
                "object": ["apple_1"],
                "destination": ["box_1"],
            },
            "__gemini_er2__": {
                "root_id": "program/place",
                "parent_id": "program/place",
                "node_kind": "physical",
                "task_type": "reposition_for_interaction",
                "action_ref": "reposition_for_interaction",
                "metadata": {},
            },
        },
    )


def place_failure_case():
    producer_id = "program/empty-plate/select-floor-space"
    continuation_id = "program/empty-plate/clear-floor"
    target_ref = "layout/floor/staging"
    node = TaskNodeSpec(
        node_id="program/empty-plate/clear-floor/place-cup",
        task_type="place_object",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["cup_1"],
            "destination_ids": ["floor_1"],
            "participants": {
                "object": ["cup_1"],
                "destination": ["floor_1"],
            },
            "relation": "on_support",
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": continuation_id,
            "excluded_local_xy": [[0.1, -0.1]],
            "__gemini_er2__": {
                "root_id": "program/empty-plate",
                "parent_id": continuation_id,
                "node_kind": "physical",
                "task_type": "place_object",
                "action_ref": "place_object",
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": (
                                "program/empty-plate/clear-floor/place-cup"
                            ),
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                },
            },
        },
    )
    producer = {
        "node_id": producer_id,
        "parent_id": continuation_id,
        "root_id": "program/empty-plate",
        "node_kind": "planning",
        "task_type": "select_staging_region",
        "params": {
            "system_check": "select_staging",
            "object_ids": ["can_1", "cup_1"],
            "batch_object_ids": ["can_1", "cup_1"],
            "destination_ids": ["floor_1"],
            "participants": {
                "object": ["can_1", "cup_1"],
                "destination": ["floor_1"],
            },
            "relation": "on_support",
            "staging_id": "floor_1",
            "staging_region_ref": "floor_1/support",
            "target_ref": target_ref,
            "reservation_group": "clear-plate-floor-staging",
            "layout_continuation_scope_id": continuation_id,
            "excluded_local_xy": [[0.2, -0.2]],
            "producer_note": "preserved",
        },
        "children": [],
        "child_policy": "sequence",
        "decomposer_ref": None,
        "tool_ref": None,
        "action_ref": None,
        "resource_policy": {
            "claims": ["floor_1/support"],
            "exclusive": True,
            "allow_parallel": False,
        },
        "metadata": {
            "producer_note": "preserved",
            "artifact_produces": [
                {
                    "schema": "task_artifact/1.0",
                    "artifact_kind": "layout_targets",
                    "producer_node_id": producer_id,
                    "continuation_node_id": continuation_id,
                    "ref_key": "target_ref",
                    "required": True,
                }
            ],
        },
    }
    previous_repair = {
        "node_id": f"{node.node_id}/repair-0/select-staging-region-old",
        "parent_id": node.node_id,
        "params": {
            "target_ref": target_ref,
            "excluded_local_xy": [[0.4, -0.4]],
        },
    }
    tree = SimpleNamespace(
        nodes={
            producer_id: producer,
            previous_repair["node_id"]: previous_repair,
        }
    )
    artifact = {
        "artifact_ref": target_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "anchor_id": "floor_1",
        "region_ref": "floor_1/support",
        "reservation_group": "clear-plate-floor-staging",
        "continuation_scope_id": continuation_id,
        "excluded_local_xy": [[0.3, -0.3]],
        "targets": {
            "cup_1": {
                "local_xy": [1.232, 0.264],
                "reservation_status": "active",
            }
        },
    }
    return node, tree, artifact


def placeability_failure_case():
    place_node, tree, artifact = place_failure_case()
    node_id = "program/empty-plate/clear-floor/assess-cup"
    parameters = copy.deepcopy(dict(place_node.parameters))
    source = parameters["__gemini_er2__"]
    source["node_kind"] = "planning"
    source["task_type"] = "assess_placeability"
    source.pop("action_ref", None)
    source["metadata"]["artifact_consumes"][0][
        "continuation_node_id"
    ] = node_id
    return (
        TaskNodeSpec(
            node_id=node_id,
            task_type="assess_placeability",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.DECOMPOSER,
            parameters=parameters,
        ),
        tree,
        artifact,
    )


def held_path_diagnostic(*, near_pure_rotation: bool = False) -> Diagnostic:
    return Diagnostic(
        code="PATH_BLOCKED",
        message="the carried object route collides",
        details={
            "raw_failure_code": "HELD_PATH_COLLISION",
            "target_ref": "layout/floor/staging",
            "placement_object_id": "cup_1",
            "placement_destination_id": "floor_1",
            "near_pure_rotation": near_pure_rotation,
            "recovery_kind": (
                "reposition_held_rotation"
                if near_pure_rotation
                else "replan_placement_layout"
            ),
            "residual_state": {
                "control_error": {
                    "details": {
                        "raw_failure_code": "HELD_PATH_COLLISION",
                        "near_pure_rotation": near_pure_rotation,
                    }
                }
            },
        },
        repairable=True,
    )


def prepared_placement_collision_diagnostic(
    *,
    code: str = "COLLISION",
) -> Diagnostic:
    return Diagnostic(
        code=code,
        message="prepared placement collides with the table",
        details={
            "raw_failure_code": "PREPARED_PLACEMENT_COLLISION",
            "failure_mode": "PREPARED_PLACEMENT_COLLISION",
            "recovery_kind": "replan_placement_layout",
            "held_path_evidence": "confirmed_attachment",
            "target_ref": "layout/floor/staging",
            "placement_object_id": "cup_1",
            "placement_destination_id": "floor_1",
            "effects": [
                {
                    "predicate": "attached_to_any_end_effector",
                    "participants": {"object": ["cup_1"]},
                    "state": "confirmed",
                    "required": False,
                    "predicate_details": {
                        "held_entity_id": "cup_1",
                        "attachment_state": "secure",
                    },
                }
            ],
            "residual_state": {
                "control_error": {
                    "code": code,
                    "details": {
                        "collision_pair": ["cup_1", "table_1"],
                    },
                }
            },
        },
        repairable=True,
    )


def stale_layout_failure_case():
    producer_id = "program/place/select-space"
    scope_id = "program/place"
    node_id = "program/place/prepare/reposition"
    target_ref = "layout/place/cup"
    node = TaskNodeSpec(
        node_id=node_id,
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
            "destination_ids": ["plate_1"],
            "relation": "inside_support_region",
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": scope_id,
            "__gemini_er2__": {
                "root_id": scope_id,
                "parent_id": "program/place/prepare",
                "node_kind": "physical",
                "task_type": "reposition_for_interaction",
                "action_ref": "reposition_for_interaction",
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": node_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                },
            },
        },
    )
    producer = {
        "node_id": producer_id,
        "parent_id": scope_id,
        "root_id": scope_id,
        "node_kind": "planning",
        "task_type": "select_placement_space",
        "params": {
            "system_check": "plan_region_layout",
            "object_ids": ["cup_1"],
            "batch_object_ids": ["cup_1"],
            "destination_ids": ["plate_1"],
            "participants": {
                "object": ["cup_1"],
                "destination": ["plate_1"],
            },
            "relation": "inside_support_region",
            "region_ref": "plate_1/interior",
            "reservation_group": "place-region-cup",
            "lock_region_selection": True,
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": scope_id,
        },
        "children": [],
        "child_policy": "sequence",
        "decomposer_ref": None,
        "tool_ref": None,
        "action_ref": None,
        "resource_policy": {
            "claims": [],
            "exclusive": True,
            "allow_parallel": False,
        },
        "metadata": {
            "adapter_injected": True,
            "artifact_produces": [
                {
                    "schema": "task_artifact/1.0",
                    "artifact_kind": "layout_targets",
                    "producer_node_id": producer_id,
                    "continuation_node_id": scope_id,
                    "ref_key": "target_ref",
                    "required": True,
                }
            ],
        },
    }
    tree = SimpleNamespace(nodes={producer_id: producer})
    artifact = {
        "artifact_ref": target_ref,
        "artifact_kind": "layout_targets",
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "producer_node_id": producer_id,
        "continuation_node_id": scope_id,
        "continuation_scope_id": scope_id,
        "anchor_id": "plate_1",
        "region_ref": "plate_1/interior",
        "reservation_group": "place-region-cup",
        "reservation_status": "active",
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.085],
                "reservation_status": "active",
            }
        },
    }
    diagnostic = Diagnostic(
        code="STALE_ARTIFACT",
        message="layout target is stale",
        details={
            "action_name": "reposition_for_interaction",
            "artifact_kind": "layout_targets",
            "artifact_ref": target_ref,
            "affected_refs": [target_ref],
            "policy_reason": "layout target is occupied by can_1",
            "phase": "before_dispatch",
        },
        retryable=True,
        repairable=True,
    )
    return node, tree, artifact, diagnostic


class StaticArtifacts:
    def __init__(self, artifact) -> None:
        self.value = artifact

    def artifact(self, artifact_ref):
        if (
            self.value is not None
            and artifact_ref == self.value["artifact_ref"]
        ):
            return self.value
        return None


def test_repair_planner_receives_harness_parent_and_diagnostic(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    RecordingRepairPlanner.calls = []
    RecordingRepairPlanner.sequence = 0
    runtime = SimpleNamespace(
        _current_task_id="task-9",
        world=SimpleNamespace(revision=17),
    )
    resolver = HarnessRepairResolver(runtime=runtime)
    node = failed_node()
    diagnostic = Diagnostic(
        code="BASE_MOTION_POSTURE_NOT_READY",
        message="empty-arm retreat timed out",
        details={
            "kind": "state_recovery_required",
            "failure_mode": "EMPTY_BASE_MOTION_PREPARATION_TIMEOUT",
            "failed_predicate": "base_motion_ready",
            "failed_participants": {"robot": ["robot_1"]},
            "residual_state": {"failed_stage": "escape_retract"},
            "affected_refs": ["apple_1"],
        },
        retryable=True,
    )
    repair_context = RepairContext(
        world=SimpleNamespace(name="core-world"),
        artifacts=SimpleNamespace(name="core-artifacts"),
    )

    result = resolver.propose(node, diagnostic, repair_context, 2)

    assert result is not None
    parent, converted, planner_context = RecordingRepairPlanner.calls[0]
    assert parent.node_id == node.node_id
    assert parent.spec.action_ref == "reposition_for_interaction"
    assert converted.source_node_id == node.node_id
    assert converted.code == diagnostic.code
    assert converted.cause == diagnostic.message
    assert converted.failure_mode == \
        "EMPTY_BASE_MOTION_PREPARATION_TIMEOUT"
    assert converted.world_revision == 17
    assert converted.details["params"]["participants"] == {
        "object": ["apple_1"],
        "destination": ["box_1"],
    }
    assert planner_context.runtime is runtime
    assert planner_context.task_id == "task-9"
    assert planner_context.artifacts is repair_context.artifacts

    repair_spec = result.delta.nodes[0].spec
    repair_edge = result.delta.edges[0].edge
    assert repair_spec.node_id == result.entry_node_id
    assert repair_spec.node_id.startswith(
        "program/place/reposition/repair-2/"
        "prepare-base-motion-posture-"
    )
    assert repair_spec.max_attempts == 1
    assert repair_spec.origin is NodeOrigin.REPAIR
    assert result.invalidates_artifacts == ("artifact/stale",)
    assert repair_edge.parent_id == node.node_id
    assert repair_edge.child_id == result.entry_node_id
    assert repair_edge.kind is EdgeKind.REPAIR
    assert repair_edge.order == 2


def test_repair_graph_is_deterministic_despite_harness_runtime_ids(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    RecordingRepairPlanner.calls = []
    RecordingRepairPlanner.sequence = 0
    resolver = HarnessRepairResolver(
        runtime=SimpleNamespace(
            world=SimpleNamespace(revision=3),
        )
    )
    node = failed_node()
    diagnostic = Diagnostic(
        code="BASE_MOTION_POSTURE_NOT_READY",
        message="posture is not ready",
    )
    context = RepairContext(world=object(), artifacts=object())

    first = resolver.propose(node, diagnostic, context, 0)
    second = resolver.propose(node, diagnostic, context, 0)

    assert first is not None and second is not None
    assert first.entry_node_id == second.entry_node_id
    assert first.delta == second.delta
    assert RecordingRepairPlanner.sequence == 2


def test_dynamic_route_blocker_is_grounded_before_repair_translation(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    ensured = []
    task_decomposer = ModuleType("er2sim.task_decomposer")

    def ensure_entity(runtime, entity_id):
        ensured.append(entity_id)
        runtime.world.entities[entity_id] = SimpleNamespace(
            id=entity_id,
        )

    task_decomposer._ensure_world_entity = ensure_entity
    monkeypatch.setitem(
        sys.modules,
        "er2sim.task_decomposer",
        task_decomposer,
    )
    runtime = SimpleNamespace(
        world=SimpleNamespace(revision=9, entities={}),
        perception=SimpleNamespace(
            catalog={
                "box_1": {
                    "category": "box",
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "available",
                    },
                }
            },
        ),
    )

    result = HarnessRepairResolver(
        runtime=runtime,
        planner=RouteBlockerRepairPlanner(),
    ).propose(
        failed_node(),
        Diagnostic(
            code="PATH_BLOCKED",
            message="box_1 blocks every route",
            details={
                "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
                "blocking_entity_ids": ["box_1"],
                "verified_route_blocking_entity_ids": ["box_1"],
                "baseline_pose": [1.9, 1.2, -0.4],
                "candidate_rejections": [
                    {
                        "goal": [0.5, -0.4, 0.7],
                        "phase": "base_path",
                        "direct_blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [0.5, -0.4, 0.7],
                        "phase": "base_path",
                        "direct_blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [1.4, 0.2, -2.6],
                        "phase": "base_path",
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [-0.2, 0.8, -0.5],
                        "phase": "base_path",
                        "blocking_entity_ids": ["table_1"],
                    },
                    {
                        "goal": [-0.4, -0.7, 1.2],
                        "phase": "astar_after_held_departure",
                    },
                    {
                        "goal": [8.0, 8.0, 0.0],
                        "phase": "semantic_state_change",
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [9.0, 9.0, 0.0],
                        "phase": "candidate_generation",
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [10.0, 10.0, 0.0],
                        "phase": "held_precondition",
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [11.0, 11.0, 0.0],
                        "phase": "post_placement_continuation",
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [12.0, 12.0, 0.0],
                        "phase": "vacate_clearance",
                        "blocking_entity_ids": ["robot_1"],
                    },
                    {
                        "goal": [13.0, 13.0, 0.0],
                        "blocking_entity_ids": ["box_1"],
                    },
                    {
                        "goal": [float("nan"), 0.0, 0.0],
                        "phase": "base_path",
                        "blocking_entity_ids": ["box_1"],
                    },
                ],
            },
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is not None
    assert ensured == ["box_1"]
    assert "box_1" in runtime.world.entities
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "relocate_blocker"
    assert repair.parameters["object_ids"] == ["box_1"]
    assert repair.parameters["continuation_anchor_pose"] == [
        1.9,
        1.2,
        -0.4,
    ]
    assert repair.parameters["continuation_goal_poses"] == [
        [0.5, -0.4, 0.7],
        [1.4, 0.2, -2.6],
    ]
    assert repair.parameters[
        "protected_relocation_entity_ids"
    ] == ["apple_1", "box_1"]


def test_blocker_set_repair_marks_partial_relocation_then_replan(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    runtime = SimpleNamespace(
        world=SimpleNamespace(
            revision=9,
            entities={
                "cup_1": SimpleNamespace(revision=1),
                "box_1": SimpleNamespace(revision=2),
            },
        ),
        perception=SimpleNamespace(
            catalog={
                entity_id: {
                    "category": entity_id.split("_", 1)[0],
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "available",
                    },
                }
                for entity_id in ("cup_1", "box_1")
            },
        ),
    )
    details = {
        "raw_failure_code": "NO_SAFE_DETOUR",
        "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
        "recovery_kind": "relocate_interaction_blocker_set",
        "blocking_entity_ids": ["cup_1", "box_1"],
        "verified_route_blocking_entity_ids": [],
        "selected_minimal_blocker_set": ["cup_1", "box_1"],
        "selected_sufficient_blocker_set": ["cup_1", "box_1"],
        "minimality_proven": True,
        "selected_blocker_set_witness": {
            "candidate_index": 0,
            "goal": [0.5, -0.4, 0.7],
            "code": "NO_SAFE_DETOUR",
            "failure_mode": "ROUTE_OBSTACLE_BLOCKS_PATH",
        },
        "baseline_pose": [1.9, 1.2, -0.4],
        "candidate_rejections": [
            {
                "goal": [0.5, -0.4, 0.7],
                "phase": "base_path",
                "blocking_entity_ids": ["cup_1", "box_1"],
                "verified_route_blocking_entity_ids": [],
                "selected_minimal_blocker_set": [
                    "cup_1",
                    "box_1",
                ],
            }
        ],
    }

    result = HarnessRepairResolver(
        runtime=runtime,
        planner=RouteBlockerRepairPlanner(
            "cup_1",
            expected_blocking_ids=["cup_1", "box_1"],
        ),
    ).propose(
        failed_node(),
        Diagnostic(
            code="PATH_BLOCKED",
            message="a two-object set blocks every route",
            details=details,
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is not None
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "relocate_blocker"
    assert repair.parameters["object_ids"] == ["cup_1"]
    assert repair.parameters["blocker_set_entity_ids"] == [
        "cup_1",
        "box_1",
    ]
    assert repair.parameters[
        "selected_blocker_set_member_id"
    ] == "cup_1"
    assert repair.parameters["remaining_blocker_entity_ids"] == [
        "box_1"
    ]
    assert repair.parameters["blocker_set_recovery_semantics"] == \
        "partial_relocation_then_replan"
    assert repair.parameters["blocker_set_minimality_proven"] is True
    assert repair.parameters["blocker_set_id"].startswith(
        "counterfactual-blocker-set-"
    )
    assert repair.parameters["blocker_set_witness"] == details[
        "selected_blocker_set_witness"
    ]
    assert repair.parameters["continuation_anchor_pose"] == [
        1.9,
        1.2,
        -0.4,
    ]
    assert repair.parameters["continuation_goal_poses"] == [
        [0.5, -0.4, 0.7]
    ]


def test_interaction_endpoint_blocker_repair_relocates_apple(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    runtime = SimpleNamespace(
        world=SimpleNamespace(
            revision=9,
            entities={"apple_1": SimpleNamespace(revision=1)},
        ),
        perception=SimpleNamespace(
            catalog={
                "apple_1": {
                    "category": "apple",
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "available",
                    },
                }
            },
        ),
    )

    result = HarnessRepairResolver(
        runtime=runtime,
        planner=RouteBlockerRepairPlanner("apple_1"),
    ).propose(
        failed_node(),
        Diagnostic(
            code="PATH_BLOCKED",
            message="apple_1 blocks a candidate grasp approach",
            details={
                "raw_failure_code": "NO_REACHABLE_POSE",
                "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
                "recovery_kind": "relocate_interaction_blocker",
                "blocking_entity_ids": ["apple_1"],
                "verified_route_blocking_entity_ids": ["apple_1"],
            },
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is not None
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "relocate_blocker"
    assert repair.parameters["object_ids"] == ["apple_1"]
    assert "continuation_anchor_pose" not in repair.parameters
    assert "continuation_goal_poses" not in repair.parameters


def test_unrelated_route_blockers_clear_stale_continuation_context() -> None:
    repair_node = SimpleNamespace(
        task_type="relocate_blocker",
        object_ref={"entity_id": "box_1"},
        params={
            "object_ids": ["box_1"],
            "participants": {"object": ["box_1"]},
            "continuation_anchor_pose": [99.0, 99.0, 0.0],
            "continuation_goal_poses": [[99.0, 99.0, 0.0]],
        },
    )
    diagnostic = SimpleNamespace(
        details={
            "baseline_pose": [1.9, 1.2, -0.4],
            "candidate_rejections": [
                {
                    "goal": [0.5, -0.4, 0.7],
                    "phase": "base_path",
                    "blocking_entity_ids": ["table_1"],
                },
                {
                    "goal": [1.4, 0.2, -2.6],
                    "phase": "astar_after_held_departure",
                },
            ],
        },
    )

    repair_module._inject_relocation_continuation_context(
        repair_node,
        diagnostic,
    )

    assert "continuation_anchor_pose" not in repair_node.params
    assert "continuation_goal_poses" not in repair_node.params


def test_region_repair_filters_fixed_and_ungraspable_blockers() -> None:
    runtime = SimpleNamespace(
        world=SimpleNamespace(entities={}),
        perception=SimpleNamespace(
            catalog={
                "aisle_left": {
                    "movable": False,
                    "attributes": {"fixed": True},
                    "interaction_capabilities": {
                        "grasp": "unavailable",
                    },
                },
                "apple_2": {
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "unavailable",
                    },
                },
                "apple_3": {
                    "movable": True,
                    "interaction_capabilities": {
                        "grasp": "available",
                    },
                },
            }
        ),
    )
    repair_node = SimpleNamespace(
        task_type="clear_support_region",
        params={
            "region_owner_id": "floor_1",
            "target_object_id": "box_1",
        },
        goal={},
    )
    diagnostic = SimpleNamespace(
        code="REGION_OCCUPIED",
        blocking_entity_ids=[
            "aisle_left",
            "apple_2",
            "apple_3",
        ],
        details={},
    )

    obligations = repair_module._normalize_region_repair(
        repair_node,
        diagnostic,
        runtime,
    )

    assert obligations is not None
    assert repair_node.params["include_entity_ids"] == ["apple_3"]
    assert repair_node.params[
        "non_relocatable_blocking_entity_ids"
    ] == ["aisle_left", "apple_2"]
    assert repair_node.goal == {
        "predicate": "occupies_support_region",
        "participants": {
            "subject": ["apple_3"],
            "region_owner": ["floor_1"],
        },
        "desired_value": "false",
    }


def test_repair_resolver_rejects_static_relocation_target(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)

    class StaticWallRepairPlanner:
        def propose(self, *, parent, **_kwargs):
            repair_node = SimpleNamespace(
                node_id="runtime-relocate-wall",
                parent_id=parent.node_id,
                root_id=parent.spec.root_id,
                node_kind="repair",
                task_type="relocate_blocker",
                object_ref={"entity_id": "aisle_left"},
                actor_ref=None,
                from_state=None,
                to_state=None,
                preconditions={},
                goal={},
                goal_scope="world",
                obligations=[],
                params={
                    "object_ids": ["aisle_left"],
                    "participants": {"object": ["aisle_left"]},
                },
                children=[],
                child_policy="sequence",
                decomposer_ref="builtin:relocate_blocker",
                tool_ref=None,
                action_ref=None,
                retry_policy={"max_attempts": 1, "max_repairs": 3},
                resource_policy={},
                origin="repair_planner",
                metadata={},
            )
            return SimpleNamespace(
                repair_node=repair_node,
                rule_ref="builtin:relocate_path_blocker@1.0",
                max_attempts=1,
                invalidates_artifacts=[],
                rationale="Relocate the verified route blocker.",
            )

    runtime = SimpleNamespace(
        world=SimpleNamespace(revision=9, entities={}),
        perception=SimpleNamespace(
            catalog={
                "aisle_left": {
                    "movable": False,
                    "attributes": {"fixed": True},
                    "interaction_capabilities": {
                        "grasp": "unavailable",
                    },
                }
            }
        ),
    )

    result = HarnessRepairResolver(
        runtime=runtime,
        planner=StaticWallRepairPlanner(),
    ).propose(
        failed_node(),
        Diagnostic(
            code="PATH_BLOCKED",
            message="aisle_left blocks every route",
            details={
                "blocking_entity_ids": ["aisle_left"],
                "verified_route_blocking_entity_ids": ["aisle_left"],
            },
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is None


def test_diagnostic_prefers_placement_object_over_reference_object_ids(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    RecordingRepairPlanner.calls = []
    RecordingRepairPlanner.sequence = 0
    node, _tree, _artifact, _diagnostic = stale_layout_failure_case()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=18)),
        planner=RecordingRepairPlanner(),
    ).propose(
        node,
        Diagnostic(
            code="BASE_MOTION_POSTURE_NOT_READY",
            message="posture is not ready",
        ),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is not None
    converted = RecordingRepairPlanner.calls[0][1]
    assert converted.object_id == "cup_1"
    assert converted.destination_id == "plate_1"


def test_none_harness_proposal_is_preserved(monkeypatch) -> None:
    install_fake_harness(monkeypatch)

    class NoRepair:
        def propose(self, **_kwargs):
            return None

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=0)),
        planner=NoRepair(),
    ).propose(
        failed_node(),
        Diagnostic(code="NO_REPAIR", message="terminal"),
        RepairContext(world=object(), artifacts=object()),
        0,
    )

    assert result is None


@pytest.mark.parametrize(
    (
        "action_task_type",
        "repair_task_type",
        "artifact_kind",
        "ref_key",
    ),
    [
        (
            "move_to_transport_posture",
            "plan_transport_posture",
            "transport_posture",
            "posture_ref",
        ),
        (
            "follow_path",
            "plan_detour",
            "detour_path",
            "path_ref",
        ),
    ],
)
def test_artifact_repair_republishes_original_consumer_contract(
    monkeypatch,
    action_task_type,
    repair_task_type,
    artifact_kind,
    ref_key,
) -> None:
    install_fake_harness(monkeypatch)
    node_id = f"program/action/{action_task_type}"
    producer_id = f"program/plan/{artifact_kind}"
    old_ref = f"{artifact_kind}/old"
    node = TaskNodeSpec(
        node_id=node_id,
        task_type=action_task_type,
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["cup_1"],
            "participants": {"manipuland": ["cup_1"]},
            "__gemini_er2__": {
                "root_id": "program/action",
                "parent_id": "program/action",
                "node_kind": "physical",
                "task_type": action_task_type,
                "action_ref": action_task_type,
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": artifact_kind,
                            "producer_node_id": producer_id,
                            "continuation_node_id": node_id,
                            "ref_key": ref_key,
                            "required": True,
                        }
                    ]
                },
            },
        },
    )
    artifacts = ConsumedArtifacts(
        [
            {
                "artifact_ref": old_ref,
                "artifact_kind": artifact_kind,
                "producer_node_id": producer_id,
                "continuation_node_id": node_id,
            },
            {
                "artifact_ref": "unrelated/old",
                "artifact_kind": "unrelated",
                "producer_node_id": "unrelated/producer",
                "continuation_node_id": node_id,
            },
        ]
    )

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(
            world=SimpleNamespace(revision=19),
        ),
        planner=ArtifactReplacementRepairPlanner(repair_task_type),
    ).propose(
        node,
        Diagnostic(
            code="STALE_ARTIFACT",
            message=f"{artifact_kind} is stale",
            details={
                "artifact_kind": artifact_kind,
                "artifact_ref": old_ref,
            },
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=artifacts),
        0,
    )

    assert result is not None
    assert result.invalidates_artifacts == (old_ref,)
    repair = result.delta.nodes[0].spec
    metadata = repair.parameters["__gemini_er2__"]["metadata"]
    assert metadata["artifact_produces"] == [
        {
            "artifact_kind": "unrelated",
            "producer_node_id": "unrelated/producer",
            "continuation_node_id": "unrelated/consumer",
            "ref_key": "unrelated_ref",
            "required": True,
        },
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": artifact_kind,
            "producer_node_id": producer_id,
            "continuation_node_id": node_id,
            "ref_key": ref_key,
            "required": True,
        },
    ]


def test_artifact_repair_without_consumer_contract_fails_closed(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node = failed_node()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=20)),
        planner=ArtifactReplacementRepairPlanner(
            "plan_transport_posture"
        ),
    ).propose(
        node,
        Diagnostic(
            code="STALE_ARTIFACT",
            message="transport posture is stale",
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=ConsumedArtifacts(())),
        0,
    )

    assert result is None


def test_placement_reposition_repair_inherits_layout_contract(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, _tree, artifact = place_failure_case()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=20)),
        planner=PlacementRepositionRepairPlanner(),
    ).propose(
        node,
        Diagnostic(
            code="NO_REACHABLE_POSE",
            message="reserved placement target is not reachable",
            details={
                "target_role": "destination",
                "object_id": "cup_1",
                "destination_id": "floor_1",
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    repair = result.delta.nodes[0].spec
    params = repair.parameters
    assert params["target_ref"] == "layout/floor/staging"
    assert params["layout_producer_node_id"] == (
        "program/empty-plate/select-floor-space"
    )
    assert params["layout_continuation_scope_id"] == (
        "program/empty-plate/clear-floor"
    )
    metadata = params["__gemini_er2__"]["metadata"]
    assert metadata["artifact_consumes"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": (
                "program/empty-plate/select-floor-space"
            ),
            "continuation_node_id": repair.node_id,
            "ref_key": "target_ref",
            "required": True,
        }
    ]


def test_detour_repair_replaces_path_and_inherits_layout_contract(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node_id = "program/place/reposition"
    path_producer_id = "program/place/plan-path"
    layout_producer_id = "program/place/select-space"
    path_ref = "path/old"
    target_ref = "layout/place/cup"
    node = TaskNodeSpec(
        node_id=node_id,
        task_type="reposition_for_interaction",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["plate_1"],
            "reference_ids": ["plate_1"],
            "participants": {
                "reference": ["plate_1"],
                "placement_object": ["cup_1"],
            },
            "path_ref": path_ref,
            "target_ref": target_ref,
            "layout_producer_node_id": layout_producer_id,
            "layout_continuation_scope_id": "program/place",
            "reservation_group": "place-region-cup",
            "__gemini_er2__": {
                "root_id": "program/place",
                "parent_id": "program/place/prepare",
                "node_kind": "physical",
                "task_type": "reposition_for_interaction",
                "action_ref": "reposition_for_interaction",
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "detour_path",
                            "producer_node_id": path_producer_id,
                            "continuation_node_id": node_id,
                            "ref_key": "path_ref",
                            "required": True,
                        },
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": layout_producer_id,
                            "continuation_node_id": node_id,
                            "ref_key": "target_ref",
                            "required": True,
                        },
                    ]
                },
            },
        },
    )
    artifacts = ConsumedArtifacts(
        [
            {
                "artifact_ref": path_ref,
                "artifact_kind": "detour_path",
                "producer_node_id": path_producer_id,
                "continuation_node_id": node_id,
            },
            {
                "artifact_ref": target_ref,
                "artifact_kind": "layout_targets",
                "producer_node_id": layout_producer_id,
                "continuation_node_id": node_id,
            },
        ]
    )

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        planner=MultiArtifactDetourRepairPlanner(),
    ).propose(
        node,
        Diagnostic(
            code="STALE_ARTIFACT",
            message="detour path is stale",
            details={
                "artifact_kind": "detour_path",
                "artifact_ref": path_ref,
            },
            repairable=True,
        ),
        RepairContext(world=object(), artifacts=artifacts),
        0,
    )

    assert result is not None
    assert result.invalidates_artifacts == (path_ref,)
    repair = result.delta.nodes[0].spec
    params = repair.parameters
    assert params["target_ref"] == target_ref
    assert params["layout_producer_node_id"] == layout_producer_id
    assert params["layout_continuation_scope_id"] == "program/place"
    assert params["reservation_group"] == "place-region-cup"
    metadata = params["__gemini_er2__"]["metadata"]
    assert metadata["artifact_produces"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "detour_path",
            "producer_node_id": path_producer_id,
            "continuation_node_id": node_id,
            "ref_key": "path_ref",
            "required": True,
        }
    ]
    assert metadata["artifact_consumes"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": layout_producer_id,
            "continuation_node_id": repair.node_id,
            "ref_key": "target_ref",
            "required": True,
        }
    ]


def test_stale_layout_republishes_original_producer_without_detour(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact, diagnostic = stale_layout_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=22)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    assert result.invalidates_artifacts == ()
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_placement_space"
    assert repair.operation_kind is OperationKind.SYSTEM
    assert repair.origin is NodeOrigin.REPAIR
    assert repair.max_repairs == 0
    params = repair.parameters
    assert params["object_ids"] == ["cup_1"]
    assert params["batch_object_ids"] == ["cup_1"]
    assert params["destination_ids"] == ["plate_1"]
    assert params["target_ref"] == "layout/place/cup"
    assert params["layout_producer_node_id"] == (
        "program/place/select-space"
    )
    assert params["layout_continuation_scope_id"] == "program/place"
    assert params["repair_reason"] == "stale_layout_artifact"
    assert params["stale_artifact_ref"] == "layout/place/cup"
    assert params["stale_artifact_reason"] == (
        "layout target is occupied by can_1"
    )
    assert "failed_layout_local_xy" not in params
    metadata = params["__gemini_er2__"]["metadata"]
    assert metadata["role"] == "stale_layout_republication_repair"
    assert metadata["artifact_produces"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": "program/place/select-space",
            "continuation_node_id": "program/place",
            "ref_key": "target_ref",
            "required": True,
        }
    ]


def test_stale_placeability_assessment_republishes_layout_producer(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    _node, tree, artifact, _diagnostic = stale_layout_failure_case()
    producer_id = "program/place/select-space"
    node_id = "program/place/assess-placeability"
    target_ref = "layout/place/cup"
    node = TaskNodeSpec(
        node_id=node_id,
        task_type="assess_placeability",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "object_ids": ["cup_1"],
            "destination_ids": ["plate_1"],
            "participants": {
                "object": ["cup_1"],
                "destination": ["plate_1"],
            },
            "relation": "inside_support_region",
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "layout_continuation_scope_id": "program/place",
            "__gemini_er2__": {
                "root_id": "program/place",
                "parent_id": "program/place",
                "node_kind": "planning",
                "task_type": "assess_placeability",
                "metadata": {
                    "artifact_consumes": [
                        {
                            "schema": "task_artifact/1.0",
                            "artifact_kind": "layout_targets",
                            "producer_node_id": producer_id,
                            "continuation_node_id": node_id,
                            "ref_key": "target_ref",
                            "required": True,
                        }
                    ]
                },
            },
        },
    )
    diagnostic = Diagnostic(
        code="STALE_ARTIFACT",
        message="planned target is no longer clear",
        details={
            "artifact_kind": "layout_targets",
            "artifact_ref": target_ref,
            "affected_refs": [target_ref],
            "policy_reason": "planned target is no longer clear",
            "phase": "before_assessment",
        },
        retryable=True,
        repairable=True,
    )
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=22)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_placement_space"
    assert repair.parameters["system_check"] == "plan_region_layout"
    assert repair.parameters["object_ids"] == ["cup_1"]
    assert repair.parameters["target_ref"] == target_ref
    assert repair.parameters["repair_reason"] == "stale_layout_artifact"
    assert repair.parameters["stale_artifact_reason"] == (
        "planned target is no longer clear"
    )


def test_changed_target_region_republishes_original_layout_producer(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact, _diagnostic = stale_layout_failure_case()
    target_ref = node.parameters["target_ref"]
    diagnostic = Diagnostic(
        code="TARGET_REGION_CHANGED",
        message="Cannot resolve route target for plate_1",
        details={
            "params": dict(node.parameters),
            "reference_id": "plate_1",
            "artifact_kind": "layout_targets",
            "artifact_ref": target_ref,
            "affected_refs": [target_ref],
        },
        retryable=True,
        repairable=True,
    )
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=23)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_placement_space"
    assert repair.parameters["repair_reason"] == "stale_layout_artifact"
    assert repair.parameters["stale_artifact_ref"] == target_ref
    assert repair.parameters["layout_producer_node_id"] == (
        "program/place/select-space"
    )
    metadata = repair.parameters["__gemini_er2__"]["metadata"]
    assert metadata["role"] == "stale_layout_republication_repair"


def test_stale_layout_with_incomplete_producer_contract_fails_closed(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact, diagnostic = stale_layout_failure_case()
    tree.nodes["program/place/select-space"]["metadata"].pop(
        "artifact_produces"
    )
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=22)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is None
    assert planner.calls == 0


def test_stale_layout_unknown_outcome_never_uses_generic_repair(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact, diagnostic = stale_layout_failure_case()
    planner = FailingRepairPlanner()
    diagnostic = Diagnostic(
        code=diagnostic.code,
        message=diagnostic.message,
        details={
            **dict(diagnostic.details),
            "termination": "outcome_unknown",
            "effect_state": "unknown",
            "verification": "unknown",
        },
        retryable=False,
        repairable=True,
    )

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=22)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is None
    assert planner.calls == 0


def test_post_placement_continuation_failure_reselects_layout(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    place_node, tree, artifact = place_failure_case()
    node_id = "program/empty-plate/clear-floor/build-placement-route"
    parameters = copy.deepcopy(dict(place_node.parameters))
    parameters["placement_object_ids"] = ["cup_1"]
    parameters["participants"]["placement_object"] = ["cup_1"]
    source = parameters["__gemini_er2__"]
    source["node_kind"] = "planning"
    source["task_type"] = "build_obstacle_map"
    source.pop("action_ref", None)
    source["metadata"]["artifact_consumes"][0][
        "continuation_node_id"
    ] = node_id
    node = TaskNodeSpec(
        node_id=node_id,
        task_type="build_obstacle_map",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters=parameters,
    )
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        Diagnostic(
            code="INTERACTION_POSE_BLOCKED",
            message="release would isolate the continuation",
            details={
                "failure_mode": (
                    "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
                ),
                "recovery_kind": "replan_placement_layout",
                "target_ref": "layout/floor/staging",
                "placement_object_id": "cup_1",
                "placement_destination_id": "floor_1",
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_staging_region"
    assert repair.parameters["repair_reason"] == (
        "post_placement_continuation_unreachable"
    )
    assert repair.parameters["failed_layout_local_xy"] == [1.232, 0.264]
    assert [1.232, 0.264] in repair.parameters["excluded_local_xy"]


def test_placeability_collision_reselects_local_layout(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = placeability_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        Diagnostic(
            code="REGION_OCCUPIED",
            message="the reserved floor target is occupied",
            details={
                "target_ref": "layout/floor/staging",
                "placement_object_id": "cup_1",
                "placement_destination_id": "floor_1",
                "blocking_entity_ids": ["apple_3"],
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_staging_region"
    assert repair.parameters["repair_reason"] == (
        "placement_assessment_collision"
    )
    assert repair.parameters["failed_layout_local_xy"] == [
        1.232,
        0.264,
    ]
    assert ["apple_3"] not in [
        repair.parameters.get("include_entity_ids")
    ]


def test_self_occupancy_with_external_blocker_reselects_layout(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = placeability_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        Diagnostic(
            code="SELF_OCCUPANCY_BLOCKS_PLACEMENT",
            message="robot and aisle wall overlap the floor target",
            details={
                "target_ref": "layout/floor/staging",
                "placement_object_id": "cup_1",
                "placement_destination_id": "floor_1",
                "occupant_ids": ["aisle_left", "robot_1"],
                "blocking_entity_ids": ["aisle_left"],
                "assessment": {
                    "occupant_ids": ["aisle_left", "robot_1"],
                    "details": {
                        "robot_clearance": {
                            "robot_id": "robot_1",
                        }
                    },
                },
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_staging_region"
    assert repair.parameters["repair_reason"] == (
        "placement_assessment_collision"
    )


def test_pure_robot_self_occupancy_uses_generic_vacate_repair(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = placeability_failure_case()
    calls = []

    class RecordingNoopPlanner:
        def propose(self, **kwargs):
            calls.append(kwargs)
            return None

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=RecordingNoopPlanner(),
    ).propose(
        node,
        Diagnostic(
            code="SELF_OCCUPANCY_BLOCKS_PLACEMENT",
            message="only the robot base overlaps the floor target",
            details={
                "target_ref": "layout/floor/staging",
                "placement_object_id": "cup_1",
                "placement_destination_id": "floor_1",
                "occupant_ids": ["robot_1"],
                "blocking_entity_ids": [],
                "assessment": {
                    "occupant_ids": ["robot_1"],
                    "details": {
                        "robot_clearance": {
                            "robot_id": "robot_1",
                        }
                    },
                },
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is None
    assert len(calls) == 1


def test_vacate_repair_inherits_relocation_continuation_context(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = placeability_failure_case()
    continuation_context = {
        "continuation_anchor_pose": [1.96, 1.18, -1.57],
        "continuation_goal_poses": [
            [0.69, -0.57, 1.05],
            [0.55, -0.49, 0.79],
        ],
    }
    parameters = copy.deepcopy(dict(node.parameters))
    parameters.update(continuation_context)
    node = TaskNodeSpec(
        node_id=node.node_id,
        task_type=node.task_type,
        operation_kind=node.operation_kind,
        control_kind=node.control_kind,
        origin=node.origin,
        parameters=parameters,
    )

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=VacatePlacementRepairPlanner(),
    ).propose(
        node,
        Diagnostic(
            code="SELF_OCCUPANCY_BLOCKS_PLACEMENT",
            message="only the robot base overlaps the floor target",
            details={
                "target_ref": "layout/floor/staging",
                "placement_object_id": "cup_1",
                "placement_destination_id": "floor_1",
                "occupant_ids": ["robot_1"],
                "blocking_entity_ids": [],
                "assessment": {
                    "occupant_ids": ["robot_1"],
                    "details": {
                        "robot_clearance": {
                            "robot_id": "robot_1",
                        }
                    },
                },
            },
            repairable=True,
        ),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "vacate_placement_region"
    for key, value in continuation_context.items():
        assert repair.parameters[key] == value


def test_place_held_path_collision_reselects_layout_without_detour(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = place_failure_case()
    planner = FailingRepairPlanner()
    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        held_path_diagnostic(),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        1,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_staging_region"
    assert repair.operation_kind is OperationKind.SYSTEM
    assert repair.control_kind is ControlKind.LEAF
    assert repair.origin is NodeOrigin.REPAIR
    assert repair.max_repairs == 0
    assert result.invalidates_artifacts == ()
    params = repair.parameters
    assert params["system_check"] == "select_staging"
    assert params["object_ids"] == ["cup_1"]
    assert params["batch_object_ids"] == ["cup_1"]
    assert params["target_ref"] == "layout/floor/staging"
    assert params["layout_producer_node_id"] == (
        "program/empty-plate/select-floor-space"
    )
    assert params["layout_continuation_scope_id"] == (
        "program/empty-plate/clear-floor"
    )
    assert params["lock_region_selection"] is True
    assert params["producer_note"] == "preserved"
    assert {
        tuple(value) for value in params["excluded_local_xy"]
    } == {
        (0.1, -0.1),
        (0.2, -0.2),
        (0.3, -0.3),
        (0.4, -0.4),
        (1.232, 0.264),
    }
    metadata = params["__gemini_er2__"]["metadata"]
    assert metadata["producer_note"] == "preserved"
    assert metadata["artifact_produces"] == [
        {
            "schema": "task_artifact/1.0",
            "artifact_kind": "layout_targets",
            "producer_node_id": (
                "program/empty-plate/select-floor-space"
            ),
            "continuation_node_id": (
                "program/empty-plate/clear-floor"
            ),
            "ref_key": "target_ref",
            "required": True,
        }
    ]


@pytest.mark.parametrize(
    "failure_code",
    ["COLLISION", "INTERACTION_POSE_BLOCKED"],
)
def test_prepared_place_collision_reselects_layout_without_workspace_recovery(
    monkeypatch,
    failure_code,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = place_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        prepared_placement_collision_diagnostic(code=failure_code),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is not None
    assert planner.calls == 0
    repair = result.delta.nodes[0].spec
    assert repair.task_type == "select_staging_region"
    params = repair.parameters
    assert params["repair_reason"] == "prepared_placement_collision"
    assert params["failed_layout_local_xy"] == [1.232, 0.264]
    assert [1.232, 0.264] in params["excluded_local_xy"]
    metadata = params["__gemini_er2__"]["metadata"]
    assert metadata["role"] == "placement_layout_reselection_repair"
    assert metadata["failed_local_xy"] == [1.232, 0.264]


def test_place_confirmed_hold_without_raw_code_never_plans_detour(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = place_failure_case()
    planner = FailingRepairPlanner()
    diagnostic = Diagnostic(
        code="PATH_BLOCKED",
        message="base footprint sweep blocked by obstacle table_1",
        details={
            "target_ref": "layout/floor/staging",
            "effects": [
                {
                    "predicate": "attached_to_any_end_effector",
                    "participants": {"object": ["cup_1"]},
                    "state": "confirmed",
                    "required": False,
                    "predicate_details": {
                        "held_entity_id": "cup_1",
                        "attachment_state": "secure",
                    },
                }
            ],
            "residual_state": {
                "control_error": {
                    "code": "PATH_BLOCKED",
                    "message": (
                        "base footprint sweep blocked by obstacle table_1"
                    ),
                }
            },
        },
        repairable=True,
    )

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        diagnostic,
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is None
    assert planner.calls == 0


def test_place_rotation_collision_does_not_mount_generic_detour(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, artifact = place_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        held_path_diagnostic(near_pure_rotation=True),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(artifact),
        ),
        0,
    )

    assert result is None
    assert planner.calls == 0


def test_place_collision_with_missing_layout_fails_closed(
    monkeypatch,
) -> None:
    install_fake_harness(monkeypatch)
    node, tree, _artifact = place_failure_case()
    planner = FailingRepairPlanner()

    result = HarnessRepairResolver(
        runtime=SimpleNamespace(world=SimpleNamespace(revision=21)),
        tree=tree,
        planner=planner,
    ).propose(
        node,
        held_path_diagnostic(),
        RepairContext(
            world=object(),
            artifacts=StaticArtifacts(None),
        ),
        0,
    )

    assert result is None
    assert planner.calls == 0


def test_integration_sources_do_not_depend_on_legacy_executor() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "src/task_recursive_tree/integrations/gemini_er2/skill.py",
        "src/task_recursive_tree/integrations/gemini_er2/repair.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "TreeExecutor" not in source
