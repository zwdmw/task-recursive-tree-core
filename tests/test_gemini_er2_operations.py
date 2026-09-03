from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2 import operations
from task_recursive_tree.integrations.gemini_er2.artifacts import (
    ArtifactPolicyError,
)
from task_recursive_tree.integrations.gemini_er2.interaction_routes import (
    InteractionRouteFailure,
)
from task_recursive_tree.integrations.gemini_er2.operations import (
    HarnessSystemOperationAdapter,
)
from task_recursive_tree.integrations.gemini_er2.region_space import (
    RegionLayoutSelection,
)
from task_recursive_tree.task.model import (
    ControlKind,
    NodeOrigin,
    OperationKind,
    TaskNodeSpec,
)
from task_recursive_tree.world.regions import (
    LayoutFailure,
    LayoutVerdict,
)


@dataclass
class FakeToolResult:
    termination: str
    failure_code: str | None = None
    message: str | None = None
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "termination": self.termination,
            "failure_code": self.failure_code,
            "message": self.message,
            "data": dict(self.data),
        }


class FakeRequest:
    def __init__(self, name, request_id, arguments):
        self.name = name
        self.request_id = request_id
        self.arguments = arguments


class FakeArtifacts:
    def __init__(self):
        self.publications = []
        self.registered = []

    def publish(self, node, artifact, result=None):
        stored = dict(artifact)
        self.publications.append((node.node_id, stored))
        if result is not None:
            result["artifact_ref"] = stored["artifact_ref"]
            result["artifact_kind"] = stored["kind"]
        return stored

    def register_result(self, node, result):
        self.registered.append((node.node_id, dict(result)))
        ref = result.get("artifact_ref")
        return (ref,) if ref else ()

    def task(self):
        return SimpleNamespace()


class FakeTools:
    def __init__(self, names):
        self._names = set(names)

    def names(self):
        return set(self._names)


def _node(system_check, **params):
    tool_ref = params.pop("tool_ref", None)
    goal = params.pop("goal", {})
    return TaskNodeSpec(
        node_id=f"node/{system_check}",
        task_type=system_check,
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "system_check": system_check,
            **params,
            "__gemini_er2__": {
                "tool_ref": tool_ref,
                "task_type": system_check,
                "goal": goal,
            },
        },
    )


def test_registered_readonly_tool_is_dispatched_through_runtime(
    monkeypatch,
) -> None:
    calls = []

    def inspect(runtime, request, observation):
        calls.append((runtime, request, observation))
        return FakeToolResult("succeeded", message="inspected")

    modules = {
        "er2sim.macro_actions": SimpleNamespace(
            READONLY_HANDLERS={"inspect_entities": inspect},
            TASK_CONTROL_HANDLERS={},
        ),
        "er2sim.contracts": SimpleNamespace(MacroActionRequest=FakeRequest),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    runtime = SimpleNamespace(
        tools=FakeTools({"inspect_entities"}),
        world=SimpleNamespace(freeze_snapshot=lambda: {"snapshot": "world"}),
        _current_task_id="task-1",
    )

    def execute(request, allowed, handler):
        assert allowed == ["inspect_entities"]
        return handler(request)

    runtime.execute_readonly = execute
    artifacts = FakeArtifacts()
    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
    ).run(_node("inspect_entities", entity_ids=["cup"]))

    assert outcome.succeeded
    assert calls[0][1].request_id == "system:node/inspect_entities"
    assert calls[0][1].arguments == {"entity_ids": ["cup"]}
    assert calls[0][2] == {}
    assert artifacts.registered[0][1]["message"] == "inspected"


def test_registered_task_control_tool_uses_task_control_entry(
    monkeypatch,
) -> None:
    calls = []

    def report(runtime, request, observation):
        calls.append((runtime, request, observation))
        return FakeToolResult("succeeded", message="blocked")

    modules = {
        "er2sim.macro_actions": SimpleNamespace(
            READONLY_HANDLERS={},
            TASK_CONTROL_HANDLERS={"report_blocked": report},
        ),
        "er2sim.contracts": SimpleNamespace(MacroActionRequest=FakeRequest),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    runtime = SimpleNamespace(
        tools=FakeTools({"report_blocked"}),
        world=SimpleNamespace(freeze_snapshot=lambda: {}),
    )
    runtime.execute_task_control = (
        lambda request, allowed, handler: handler(request)
    )

    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=FakeArtifacts(),
    ).run(_node("report_blocked", reason="occupied"))

    assert outcome.succeeded
    assert calls[0][1].arguments == {"reason": "occupied"}


def test_dispatch_falls_back_to_metadata_tool_ref() -> None:
    node = TaskNodeSpec(
        node_id="node/metadata-tool",
        task_type="inspect",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            "__gemini_er2__": {
                "tool_ref": "locate_entities",
                "task_type": "inspect",
                "goal": {},
            },
        },
    )
    adapter = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=FakeArtifacts(),
    )

    assert adapter.operation_for(node) == "locate_entities"


def test_planning_result_is_published_and_returned_as_artifact() -> None:
    artifacts = FakeArtifacts()
    runtime = SimpleNamespace(world=SimpleNamespace(revision=7))

    def planner(node, params):
        result = {"termination": "succeeded", "planned": params["target"]}
        artifacts.publish(
            node,
            {
                "kind": "detour_path",
                "artifact_ref": "path://7",
                "poses_world": [[0, 0, 0], [1, 0, 0]],
            },
            result,
        )
        return result

    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
        planning_handlers={"plan_detour": planner},
    ).run(_node("plan_detour", target="cup"))

    assert outcome.succeeded
    assert outcome.artifact_refs == ("path://7",)
    assert artifacts.publications[0][1]["kind"] == "detour_path"
    assert outcome.result["planned"] == "cup"


def test_transport_posture_planning_skips_colliding_candidate(
    monkeypatch,
) -> None:
    planner_calls = []

    class PlanningError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code
            self.message = message

    class FakePlan:
        @staticmethod
        def to_dict():
            return {
                "planner": "fake_rrt",
                "direct": True,
                "collision_checks": 7,
                "waypoint_count": 4,
            }

    class FakePlanner:
        def __init__(self, scene):
            assert scene is runtime.scene
            self.snapshot = SimpleNamespace(last_collision=None)

        def plan_to_configuration(self, candidate, *, held_entity_id):
            planner_calls.append((dict(candidate), held_entity_id))
            if candidate["lift"] < 0.42:
                self.snapshot.last_collision = (
                    "link_gripper_finger_right",
                    "table",
                )
                raise PlanningError(
                    "COLLISION",
                    "goal arm state is in collision",
                )
            self.snapshot.last_collision = None
            return FakePlan()

    candidates = (
        {"lift": 0.24, "arm_extend": 0.02, "wrist_yaw": 0.0},
        {"lift": 0.42, "arm_extend": 0.02, "wrist_yaw": 0.0},
    )
    modules = {
        "er2sim.transport_capabilities": SimpleNamespace(
            TRANSPORT_MODEL_VERSION="transport_capabilities/1.1",
            transport_posture_candidates=lambda *_args: candidates,
            transport_profile=lambda *_args: "compact",
        ),
        "er2sim.motion_planning": SimpleNamespace(
            EndEffectorPlanner=FakePlanner,
            PlanningError=PlanningError,
        ),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    runtime = SimpleNamespace(
        scene=SimpleNamespace(),
        perception=SimpleNamespace(),
        world=SimpleNamespace(revision=9),
    )
    artifacts = FakeArtifacts()

    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "plan_transport_posture",
            object_ids=["apple_2"],
            destination_ids=["floor_1"],
        )
    )

    assert outcome.succeeded
    assert [call[0]["lift"] for call in planner_calls] == [0.24, 0.42]
    artifact = artifacts.publications[0][1]
    assert artifact["target_configuration"] == candidates[1]
    assert artifact["selected_candidate_index"] == 1
    assert artifact["planning_summary"]["planner"] == "fake_rrt"
    assert artifact["candidate_rejections"] == [{
        "candidate_index": 0,
        "configuration": candidates[0],
        "failure_code": "COLLISION",
        "message": "goal arm state is in collision",
        "collision_pair": [
            "link_gripper_finger_right",
            "table",
        ],
    }]


def test_transport_posture_planning_fails_when_all_candidates_collide(
    monkeypatch,
) -> None:
    class PlanningError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code
            self.message = message

    class FakePlanner:
        def __init__(self, _scene):
            self.snapshot = SimpleNamespace(
                last_collision=("link_gripper_finger_right", "table"),
            )

        def plan_to_configuration(self, _candidate, *, held_entity_id):
            assert held_entity_id == "apple_2"
            raise PlanningError("COLLISION", "candidate path is blocked")

    modules = {
        "er2sim.transport_capabilities": SimpleNamespace(
            TRANSPORT_MODEL_VERSION="transport_capabilities/1.1",
            transport_posture_candidates=lambda *_args: (
                {"lift": 0.42, "arm_extend": 0.02, "wrist_yaw": 0.0},
                {"lift": 0.50, "arm_extend": 0.02, "wrist_yaw": 0.0},
            ),
            transport_profile=lambda *_args: "compact",
        ),
        "er2sim.motion_planning": SimpleNamespace(
            EndEffectorPlanner=FakePlanner,
            PlanningError=PlanningError,
        ),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    artifacts = FakeArtifacts()
    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(
            scene=SimpleNamespace(),
            perception=SimpleNamespace(),
            world=SimpleNamespace(revision=9),
        ),
        artifacts=artifacts,
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "plan_transport_posture",
            object_ids=["apple_2"],
            destination_ids=["floor_1"],
        )
    )

    assert outcome.succeeded is False
    assert outcome.diagnostic.code == "NO_REACHABLE_POSE"
    assert outcome.result["details"]["candidate_rejections"]
    assert artifacts.publications == []


def test_assess_interaction_uses_commanded_gripper_target(
    monkeypatch,
) -> None:
    reachability_calls = []
    target = SimpleNamespace(
        point=(0.14, 0.43, 0.08),
        descriptor=lambda: {
            "kind": "entity_grasp",
            "reference_id": "apple_1",
            "point": [0.14, 0.43, 0.08],
        },
    )
    grasp_config = SimpleNamespace(
        policy="auto",
        source_region_id=None,
        source_contact_entity_ids=(),
        suction_standoff=0.0,
    )

    def gripper_target_for_grasp(
        scene,
        grasp_point,
        grasp_mode,
        *,
        suction_standoff,
    ):
        assert scene is runtime.scene
        assert grasp_point == target.point
        assert grasp_mode == "suction"
        assert suction_standoff == 0.0
        return (0.12, 0.43, 0.18)

    modules = {
        "er2sim.interaction_targets": SimpleNamespace(
            resolve_interaction_target=lambda *_args, **_kwargs: target,
            resolve_grasp_execution_config=(
                lambda *_args, **_kwargs: grasp_config
            ),
            is_placement_target=lambda _params: False,
        ),
        "er2sim.control": SimpleNamespace(
            gripper_target_for_grasp=gripper_target_for_grasp,
        ),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    runtime = SimpleNamespace(
        world=SimpleNamespace(revision=47),
        perception=SimpleNamespace(
            catalog={
                "apple_1": {
                    "geometry": {"grasp_mode": "suction"},
                }
            }
        ),
        scene=SimpleNamespace(
            point_reachability=lambda point: (
                reachability_calls.append(tuple(point))
                or {
                    "reachable": True,
                    "height": point[2],
                }
            )
        ),
    )
    artifacts = FakeArtifacts()

    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
        route_planner=object(),
    ).run(
        _node(
            "assess_interaction",
            object_ids=["apple_1"],
        )
    )

    assert outcome.succeeded
    assert reachability_calls == [(0.12, 0.43, 0.18)]
    assessment = outcome.result["assessment"]
    assert assessment["target"]["point"][2] == 0.08
    assert assessment["reachability_point"] == [0.12, 0.43, 0.18]
    assert assessment["reachability_model"] == "gripper_target_for_grasp"


def test_assess_placeability_uses_declared_layout_target(
    monkeypatch,
) -> None:
    calls = {}

    class LayoutArtifacts(FakeArtifacts):
        def resolve_for(self, node, *, artifact_kind, ref_key):
            calls["resolution"] = (
                node.node_id,
                artifact_kind,
                ref_key,
            )
            return "layout/plate-floor"

    assessment = SimpleNamespace(
        ok=True,
        failure_code=None,
        reason="placement is feasible",
        verdict="true",
    )
    assessment.to_dict = lambda: {
        "failure_code": None,
        "reason": "placement is feasible",
        "verdict": "true",
    }

    def assess_placement(runtime, object_id, destination_id, **kwargs):
        calls["assessment"] = {
            "runtime": runtime,
            "object_id": object_id,
            "destination_id": destination_id,
            **kwargs,
        }
        return assessment

    modules = {
        "er2sim.placement_simulator": SimpleNamespace(
            assess_placement=assess_placement,
            placement_assessment_artifact=lambda value: {
                "kind": "placement_assessment",
                "artifact_ref": "assessment/plate-floor",
                **value.to_dict(),
            },
        ),
        "er2sim.interaction_targets": SimpleNamespace(
            resolve_layout_target_point=(
                lambda runtime, target_ref, object_id, destination_id: (
                    calls.setdefault(
                        "target",
                        (
                            runtime,
                            target_ref,
                            object_id,
                            destination_id,
                        ),
                    )
                    and (0.42, -1.36, 0.02)
                )
            ),
        ),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )
    runtime = SimpleNamespace(
        world=SimpleNamespace(revision=5),
        perception=SimpleNamespace(),
    )
    artifacts = LayoutArtifacts()

    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "assess_placeability",
            object_ids=["plate_1"],
            destination_ids=["floor_1"],
            relation="on_support",
            target_ref="layout/plate-floor",
            defer_reachability=True,
        )
    )

    assert outcome.succeeded
    assert calls["resolution"] == (
        "node/assess_placeability",
        "layout_targets",
        "target_ref",
    )
    assert calls["target"][1:] == (
        "layout/plate-floor",
        "plate_1",
        "floor_1",
    )
    assert calls["assessment"]["candidate_point"] == (
        0.42,
        -1.36,
        0.02,
    )
    assert calls["assessment"]["require_reachable"] is False
    assert outcome.result["layout_target_ref"] == "layout/plate-floor"
    assert artifacts.publications[0][1]["layout_target_ref"] == (
        "layout/plate-floor"
    )


def test_assess_placeability_reports_stale_layout_as_repairable(
    monkeypatch,
) -> None:
    assessment_calls = []

    class StaleLayoutArtifacts(FakeArtifacts):
        def resolve_for(self, node, *, artifact_kind, ref_key):
            del node, ref_key
            raise ArtifactPolicyError(
                artifact_kind=artifact_kind,
                artifact_ref="layout/stale-floor-target",
                reason="planned target is no longer clear",
            )

    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: SimpleNamespace(
            assess_placement=lambda *args, **kwargs: (
                assessment_calls.append((args, kwargs))
            ),
        )
        if name == "er2sim.placement_simulator"
        else None,
    )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=StaleLayoutArtifacts(),
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "assess_placeability",
            object_ids=["apple_2"],
            destination_ids=["floor_1"],
            target_ref="layout/stale-floor-target",
        )
    )

    assert not outcome.succeeded
    assert assessment_calls == []
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "STALE_ARTIFACT"
    assert outcome.diagnostic.retryable is True
    assert outcome.diagnostic.repairable is True
    assert outcome.diagnostic.details["artifact_kind"] == "layout_targets"
    assert outcome.diagnostic.details["artifact_ref"] == (
        "layout/stale-floor-target"
    )
    assert outcome.diagnostic.details["affected_refs"] == [
        "layout/stale-floor-target"
    ]
    assert outcome.diagnostic.details["policy_reason"] == (
        "planned target is no longer clear"
    )


def test_assess_placeability_reports_local_occupants_as_blockers(
    monkeypatch,
) -> None:
    class LayoutArtifacts(FakeArtifacts):
        def resolve_for(self, node, *, artifact_kind, ref_key):
            del node, artifact_kind, ref_key
            return "layout/box-floor"

    assessment = SimpleNamespace(
        ok=False,
        failure_code="REGION_OCCUPIED",
        reason="candidate is occupied",
        verdict="false",
    )
    assessment.to_dict = lambda: {
        "failure_code": "REGION_OCCUPIED",
        "reason": "candidate is occupied",
        "verdict": "false",
        "occupant_ids": ["apple_3", "robot_1"],
    }
    modules = {
        "er2sim.placement_simulator": SimpleNamespace(
            assess_placement=lambda *_args, **_kwargs: assessment,
            placement_assessment_artifact=lambda value: {
                "kind": "placement_assessment",
                "artifact_ref": "assessment/box-floor",
                **value.to_dict(),
            },
        ),
        "er2sim.interaction_targets": SimpleNamespace(
            resolve_layout_target_point=(
                lambda *_args: (0.44, -1.36, 0.0)
            ),
        ),
    }
    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: modules[name],
    )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=LayoutArtifacts(),
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "assess_placeability",
            object_ids=["box_1"],
            destination_ids=["floor_1"],
            relation="on_support",
            target_ref="layout/box-floor",
        )
    )

    assert not outcome.succeeded
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.repairable is True
    assert outcome.diagnostic.details["blocking_entity_ids"] == [
        "apple_3"
    ]
    assert outcome.diagnostic.details["failed_predicate"] == (
        "occupies_support_region"
    )
    assert outcome.diagnostic.details["expected_value"] == "false"


def test_assess_placeability_keeps_contract_errors_nonrepairable(
    monkeypatch,
) -> None:
    class InvalidLayoutArtifacts(FakeArtifacts):
        def resolve_for(self, node, *, artifact_kind, ref_key):
            del node, artifact_kind, ref_key
            raise ValueError("consumer contract is malformed")

    monkeypatch.setattr(
        operations,
        "import_harness_module",
        lambda name, **_: SimpleNamespace()
        if name == "er2sim.placement_simulator"
        else None,
    )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=InvalidLayoutArtifacts(),
        route_planner=object(),
        region_space=object(),
    ).run(
        _node(
            "assess_placeability",
            object_ids=["apple_2"],
            destination_ids=["floor_1"],
            target_ref="layout/invalid-contract",
        )
    )

    assert not outcome.succeeded
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "INVALID_ARTIFACT_CONTRACT"
    assert outcome.diagnostic.retryable is False
    assert outcome.diagnostic.repairable is False
    assert outcome.diagnostic.details["artifact_ref"] == (
        "layout/invalid-contract"
    )


def test_harness_failure_fields_are_preserved() -> None:
    artifacts = FakeArtifacts()
    runtime = SimpleNamespace(world=SimpleNamespace(revision=2))
    outcome = HarnessSystemOperationAdapter(
        runtime=runtime,
        artifacts=artifacts,
        planning_handlers={
            "assess_interaction": lambda _node, _params: {
                "termination": "outcome_unknown",
                "failure_code": "NO_REACHABLE_POSE",
                "message": "target is blocked",
                "retryable": True,
                "result": {"blocking_entity_ids": ["box"]},
            }
        },
    ).run(_node("assess_interaction"))

    assert not outcome.succeeded
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "NO_REACHABLE_POSE"
    assert outcome.diagnostic.message == "target is blocked"
    assert outcome.diagnostic.retryable is True
    assert outcome.diagnostic.details["result"]["result"][
        "blocking_entity_ids"
    ] == ["box"]
    assert outcome.result["result"]["blocking_entity_ids"] == ["box"]


def test_layout_replan_reconciles_without_releasing_before_publish() -> None:
    calls = []

    class ReservationArtifacts(FakeArtifacts):
        def reconcile_layout_reservations(self, *, entity_ids=None):
            calls.append(("reconcile", tuple(entity_ids or ())))
            return ({"entity_id": "can_1", "status": "fulfilled"},)

        def release_layout_reservations_for_entities(
            self,
            entity_ids,
            *,
            reason,
        ):
            raise AssertionError(
                "old reservations may only be released after publication"
            )

    adapter = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=ReservationArtifacts(),
        route_planner=object(),
        region_space=object(),
    )
    node = _node("select_staging")

    report = adapter._prepare_layout_allocation(
        node,
        ("can_1", "cup_1"),
    )

    assert calls == [("reconcile", ("can_1", "cup_1"))]
    assert report["fulfilled"][0]["entity_id"] == "can_1"
    assert report["released"] == []


def test_plan_region_layout_forwards_relocation_constraints() -> None:
    calls = {}
    constraints = {
        "baseline_pose": [0.60, -1.00, 0.0],
        "minimum_relocation_distance": 0.39,
        "path_segments": [
            [[0.20, -1.00], [1.20, -1.00]],
        ],
        "object_radius": 0.17,
        "required_clearance": 0.30,
        "minimum_improvement": 0.05,
        "continuation_anchor_pose": [1.90, 1.20, -0.40],
        "continuation_goal_poses": [
            [0.50, -0.40, 0.70],
            [1.40, 0.20, -2.60],
        ],
    }

    class RecordingRegionSpace:
        def plan_layout(self, region_ref, object_ids, **kwargs):
            calls["region_ref"] = region_ref
            calls["object_ids"] = object_ids
            calls["kwargs"] = kwargs
            return RegionLayoutSelection(
                region=None,
                snapshot=None,
                plan=None,
                failure=LayoutFailure(
                    verdict=LayoutVerdict.SEARCH_EXHAUSTED,
                    code="CANDIDATE_SPACE_EXHAUSTED",
                    message="test stop after parameter capture",
                ),
            )

    adapter = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=FakeArtifacts(),
        route_planner=object(),
        region_space=RecordingRegionSpace(),
    )
    node = _node(
        "plan_region_layout",
        batch_object_ids=["box_1"],
        destination_ids=["floor_1"],
        region_ref="floor_1/support",
        relation="on_support",
        placement_policy="clear_of_workspace",
        robust_clearance_margin_m=0.025,
        **constraints,
    )

    result = adapter._run_plan_region_layout(
        node,
        dict(node.parameters),
    )

    assert result["failure_code"] == "CANDIDATE_SPACE_EXHAUSTED"
    assert calls["region_ref"] == "floor_1/support"
    assert calls["object_ids"] == ("box_1",)
    assert calls["kwargs"]["placement_constraints"] == constraints
    assert calls["kwargs"]["placement_policy"] == "clear_of_workspace"
    assert calls["kwargs"]["robust_clearance_margin_m"] == 0.025


def test_continuation_route_failure_is_repairable() -> None:
    class FailingRoutePlanner:
        @staticmethod
        def plan(_reference_id, _params):
            return InteractionRouteFailure(
                code="INTERACTION_POSE_BLOCKED",
                message="placement blocks the original task continuation",
                details={
                    "failure_mode": (
                        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
                    ),
                    "recovery_kind": "replan_placement_layout",
                    "blocking_entity_ids": ["box_1"],
                },
            )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=FakeArtifacts(),
        route_planner=FailingRoutePlanner(),
        region_space=object(),
    ).run(
        _node(
            "plan_detour",
            destination_ids=["floor_1"],
        )
    )

    assert outcome.succeeded is False
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "INTERACTION_POSE_BLOCKED"
    assert outcome.diagnostic.repairable is True
    assert outcome.diagnostic.details["failure_mode"] == \
        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
    assert outcome.result["repairable"] is True


@pytest.mark.parametrize(
    ("recovery_kind", "blocking_ids", "verified_ids"),
    (
        (
            "relocate_interaction_blocker",
            ["apple_1"],
            ["apple_1"],
        ),
        (
            "relocate_interaction_blocker_set",
            ["cup_1", "box_1"],
            [],
        ),
    ),
)
def test_interaction_blocker_route_failure_is_repairable(
    recovery_kind,
    blocking_ids,
    verified_ids,
) -> None:
    class FailingRoutePlanner:
        @staticmethod
        def plan(_reference_id, _params):
            return InteractionRouteFailure(
                code="PATH_BLOCKED",
                message="apple_1 blocks a candidate grasp approach",
                details={
                    "raw_failure_code": "NO_REACHABLE_POSE",
                    "failure_mode": "ROUTE_ENDPOINT_IN_COLLISION",
                    "recovery_kind": recovery_kind,
                    "blocking_entity_ids": blocking_ids,
                    "verified_route_blocking_entity_ids": verified_ids,
                },
            )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=FakeArtifacts(),
        route_planner=FailingRoutePlanner(),
        region_space=object(),
    ).run(
        _node(
            "plan_detour",
            object_ids=["plate_1"],
            reference_ids=["plate_1"],
        )
    )

    assert outcome.succeeded is False
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "PATH_BLOCKED"
    assert outcome.diagnostic.repairable is True
    assert outcome.diagnostic.details["raw_failure_code"] == \
        "NO_REACHABLE_POSE"
    assert outcome.diagnostic.details[
        "verified_route_blocking_entity_ids"
    ] == verified_ids
    assert outcome.result["repairable"] is True


def test_pending_transaction_blocks_layout_allocation() -> None:
    class PendingArtifacts(FakeArtifacts):
        def pending_reconciliation_transaction_ids(self):
            return ("txn-place-1",)

        def reconcile_layout_reservations(self, **_kwargs):
            raise AssertionError(
                "layout reservations cannot be reconciled while pending"
            )

    class NoRegionSelection:
        def select_layout(self, *_args, **_kwargs):
            raise AssertionError(
                "space allocation cannot run while a transaction is pending"
            )

    outcome = HarnessSystemOperationAdapter(
        runtime=SimpleNamespace(),
        artifacts=PendingArtifacts(),
        route_planner=object(),
        region_space=NoRegionSelection(),
    ).run(
        _node(
            "select_staging",
            batch_object_ids=["cup_1"],
            staging_region_ref="floor_1/support",
        )
    )

    assert not outcome.succeeded
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "OUTCOME_UNKNOWN"
    assert outcome.diagnostic.retryable is True
    assert outcome.diagnostic.repairable is True
    assert outcome.diagnostic.details["pending_transaction_ids"] == [
        "txn-place-1"
    ]
    assert outcome.result["details"]["pending_transaction_ids"] == [
        "txn-place-1"
    ]


@pytest.mark.parametrize("operation", ["reconcile", "reconcile_all"])
def test_reconcile_only_reconciles_pending_transactions(operation) -> None:
    calls = []

    class Runtime:
        world = SimpleNamespace(revision=3)

        def reconcile_pending_transactions(
            self,
            trigger,
            *,
            transaction_id=None,
        ):
            calls.append((trigger, transaction_id))
            return [{"transaction_id": "txn-1", "status": "closed"}]

        def dispatch(self, *_args, **_kwargs):
            raise AssertionError("reconciliation must not dispatch an action")

    outcome = HarnessSystemOperationAdapter(
        runtime=Runtime(),
        artifacts=FakeArtifacts(),
    ).run(
        _node(
            operation,
            trigger="test",
            transaction_id="txn-1",
        )
    )

    assert outcome.succeeded
    assert calls == [
        ("test", "txn-1" if operation == "reconcile" else None)
    ]


def test_adapter_source_has_no_legacy_executor_dependency() -> None:
    source = Path(operations.__file__).read_text(encoding="utf-8").lower()
    forbidden = ("tree" + "executor", "tree_" + "executor")
    assert all(token not in source for token in forbidden)
