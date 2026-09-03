from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2 import skill as skill_module
from task_recursive_tree.integrations.gemini_er2.artifacts import (
    ArtifactPolicyError,
)
from task_recursive_tree.integrations.gemini_er2.skill import (
    HarnessMacroActionSkill,
    PhysicalRequestValidationError,
)
from task_recursive_tree.task.contracts import SkillContext
from task_recursive_tree.task.model import (
    ControlKind,
    NodeOrigin,
    OperationKind,
    TaskNodeSpec,
)


@dataclass
class FakeMacroActionRequest:
    name: str
    request_id: str
    arguments: dict = field(default_factory=dict)
    expected_state: dict = field(default_factory=dict)
    input_snapshot_id: str | None = None


class FakeArtifactBridge:
    def __init__(
        self,
        refs=None,
        *,
        layout_refs=(),
        runtime=None,
    ) -> None:
        self.runtime = runtime or SimpleNamespace(
            world=_fake_world()
        )
        self.refs = dict(refs or {})
        self.layout_refs = set(layout_refs)
        self.calls = []

    def task(self):
        return SimpleNamespace(
            slots={
                "fruit": SimpleNamespace(
                    bound_entity_ids=["apple_1", "apple_2"]
                )
            }
        )

    def resolve_for(self, node, *, artifact_kind, ref_key):
        self.calls.append((node.node_id, artifact_kind, ref_key))
        return self.refs.get((artifact_kind, ref_key))

    def is_layout_target(self, artifact_ref):
        return artifact_ref in self.layout_refs


class FakeRecoveryRuntime:
    def __init__(self) -> None:
        self.world = _fake_world()
        self.permit_calls = []

    def issue_recovery_motion_permit(self, request, *, contract):
        self.permit_calls.append((request, contract))
        return "recovery-permit-9"


def _fake_world():
    return SimpleNamespace(
        freeze_snapshot=lambda: {
            "snapshot": {"id": "world-snapshot-7"}
        }
    )


def install_fake_contracts(monkeypatch) -> None:
    module = ModuleType("er2sim.contracts")
    module.MacroActionRequest = FakeMacroActionRequest
    monkeypatch.setitem(sys.modules, "er2sim.contracts", module)
    monkeypatch.setattr(
        skill_module,
        "import_harness_module",
        lambda name, **_: sys.modules[name],
    )


def physical_node(action_ref: str, **parameters) -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id=f"program/action/{action_ref}",
        task_type=action_ref,
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
        parameters={
            **parameters,
            "__gemini_er2__": {
                "root_id": "program",
                "action_ref": action_ref,
            },
        },
    )


def recovery_node(
    *,
    node_origin=NodeOrigin.REPAIR,
    source_origin="repair_planner",
    source_metadata=None,
    tree_metadata=None,
    **parameters,
) -> TaskNodeSpec:
    source = {
        "root_id": "program",
        "action_ref": "recover_workspace",
        "origin": source_origin,
        "metadata": dict(source_metadata or {}),
    }
    if tree_metadata is not None:
        source["tree"] = {"metadata": dict(tree_metadata)}
    return TaskNodeSpec(
        node_id="program/action/recover_workspace",
        task_type="recover_workspace",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=node_origin,
        parameters={
            "configuration_id": "stow",
            "component_scope": "manipulator",
            "recovery_contract": "bounded_workspace_recovery",
            **parameters,
            "__gemini_er2__": source,
        },
    )


def context(request_id="kernel-request-42") -> SkillContext:
    return SkillContext(
        artifacts=object(),
        task_id="task-1",
        node_id="node-1",
        attempt=3,
        request_id=request_id,
    )


def test_pick_request_uses_kernel_request_id_and_normalizes_roles(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)
    bridge = FakeArtifactBridge()
    skill = HarnessMacroActionSkill(artifact_bridge=bridge)
    node = physical_node(
        "pick_object",
        participants={
            "object": {"entity_ids": ["apple_1", "apple_1"]},
            "destination": ["table_1"],
        },
        source_region_id="bin_1",
        grasp_policy={"approach": "top"},
        waypoints_world=[[1.0, 2.0, 3.0]],
    )

    request = skill.build_request(node, context())

    assert isinstance(request, FakeMacroActionRequest)
    assert request.request_id == "kernel-request-42"
    assert request.name == "pick_object"
    assert request.arguments == {
        "participants": {
            "manipuland": {"entity_ids": ["apple_1"]}
        },
        "source_region_id": "bin_1",
        "grasp_policy": {"approach": "top"},
    }
    assert request.input_snapshot_id == "world-snapshot-7"
    assert bridge.calls == []


def test_reposition_resolves_only_declared_artifact_inputs(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)
    bridge = FakeArtifactBridge(
        {
            ("detour_path", "path_ref"): "path/verified",
            ("layout_targets", "target_ref"): "layout/verified",
        },
        layout_refs={"layout/verified"},
    )
    skill = HarnessMacroActionSkill(artifact_bridge=bridge)
    node = physical_node(
        "reposition_for_interaction",
        participants={
            "object": ["plate_1"],
            "placement_object": ["apple_1"],
            "destination": ["ignored_1"],
        },
        purpose="placement_reachability",
        path_ref="path/declared",
        target_ref="layout/declared",
        relation="inside_support_region",
        require_held_load=True,
        forbidden_internal_pose=[0.0, 1.0],
    )

    request = skill.build_request(node, context())

    assert request.arguments == {
        "participants": {
            "placement_object": {"entity_ids": ["apple_1"]},
            "reference": {"entity_ids": ["plate_1"]},
        },
        "purpose": "placement_reachability",
        "relation": "inside_support_region",
        "require_held_load": True,
        "interaction_target_kind": "placement_pose",
        "path_ref": "path/verified",
        "target_ref": "layout/verified",
    }
    assert request.artifact_refs == (
        "path/verified",
        "layout/verified",
    )
    assert bridge.calls == [
        (
            node.node_id,
            "detour_path",
            "path_ref",
        ),
        (
            node.node_id,
            "layout_targets",
            "target_ref",
        ),
    ]


def test_stale_layout_becomes_repairable_pre_dispatch_diagnostic(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)

    class StaleLayoutBridge(FakeArtifactBridge):
        def resolve_for(self, node, *, artifact_kind, ref_key):
            del node, ref_key
            raise ArtifactPolicyError(
                artifact_kind=artifact_kind,
                artifact_ref="layout/stale",
                reason="layout region occupancy is incomplete",
            )

    skill = HarnessMacroActionSkill(
        artifact_bridge=StaleLayoutBridge(),
    )
    node = physical_node(
        "place_object",
        participants={
            "manipuland": ["apple_1"],
            "destination": ["floor_1"],
        },
        target_ref="layout/stale",
    )

    with pytest.raises(PhysicalRequestValidationError) as caught:
        skill.build_request(node, context())

    diagnostic = caught.value.diagnostic
    assert diagnostic.code == "STALE_ARTIFACT"
    assert diagnostic.retryable is True
    assert diagnostic.repairable is True
    assert diagnostic.details["artifact_ref"] == "layout/stale"
    assert diagnostic.details["policy_reason"] == (
        "layout region occupancy is incomplete"
    )


@pytest.mark.parametrize(
    ("action_ref", "parameters", "refs", "expected"),
    [
        (
            "place_object",
            {
                "participants": {
                    "source": ["apple_1"],
                    "destination": ["box_1"],
                },
                "target_ref": "layout/raw",
            },
            {("layout_targets", "target_ref"): "layout/ok"},
            {
                "relation": "inside_support_region",
                "target_ref": "layout/ok",
            },
        ),
        (
            "move_to_transport_posture",
            {
                "participants": {
                    "object": ["apple_1"],
                    "destination": ["table_1"],
                },
                "posture_ref": "posture/raw",
            },
            {("transport_posture", "posture_ref"): "posture/ok"},
            {"posture_ref": "posture/ok"},
        ),
        (
            "follow_path",
            {
                "participants": {"destination": ["table_1"]},
                "path_ref": "path/raw",
                "laps": 2,
            },
            {("detour_path", "path_ref"): "path/ok"},
            {"path_ref": "path/ok", "laps": 2},
        ),
        (
            "acquire_view",
            {
                "participants": {"reference": ["apple_1"]},
                "purpose": "resolve_visibility",
            },
            {},
            {"purpose": "resolve_visibility"},
        ),
        (
            "prepare_base_motion_posture",
            {
                "purpose": "restore_base_motion_posture",
                "failure_mode": "EMPTY_BASE_MOTION_POSTURE_NOT_READY",
                "failed_stage": "escape_retract",
            },
            {},
            {
                "purpose": "restore_base_motion_posture",
                "failure_mode": "EMPTY_BASE_MOTION_POSTURE_NOT_READY",
                "failed_stage": "escape_retract",
            },
        ),
    ],
)
def test_supported_actions_preserve_only_their_contract_parameters(
    monkeypatch,
    action_ref,
    parameters,
    refs,
    expected,
) -> None:
    install_fake_contracts(monkeypatch)
    bridge = FakeArtifactBridge(refs, layout_refs={"layout/ok"})
    request = HarnessMacroActionSkill(
        artifact_bridge=bridge
    ).build_request(physical_node(action_ref, **parameters), context())

    for key, value in expected.items():
        assert request.arguments[key] == value
    assert request.name == action_ref


@pytest.mark.parametrize(
    ("source_origin", "source_metadata", "tree_metadata"),
    [
        ("repair_planner", {}, None),
        (
            "system_recovery",
            {"system_owned_recovery": True},
            None,
        ),
        (
            "continuous_session_recovery",
            {},
            {"system_owned_recovery": True},
        ),
    ],
)
def test_recover_workspace_accepts_only_trusted_internal_sources(
    monkeypatch,
    source_origin,
    source_metadata,
    tree_metadata,
) -> None:
    install_fake_contracts(monkeypatch)
    runtime = FakeRecoveryRuntime()
    bridge = FakeArtifactBridge(runtime=runtime)
    skill = HarnessMacroActionSkill(artifact_bridge=bridge)
    node = recovery_node(
        source_origin=source_origin,
        source_metadata=source_metadata,
        tree_metadata=tree_metadata,
        participants={"object": ["box_1"]},
        ignored_model_parameter="not-forwarded",
    )

    request = skill.build_request(node, context())

    assert request.name == "recover_workspace"
    assert request.arguments == {
        "configuration_id": "stow",
        "component_scope": "manipulator",
    }
    assert "participants" not in request.arguments
    assert runtime.permit_calls == [
        (request, "bounded_workspace_recovery")
    ]
    assert request.expected_state == {
        "recovery_motion_permit": "recovery-permit-9"
    }
    assert request.input_snapshot_id == "world-snapshot-7"


def test_recover_workspace_rejects_llm_system_only_forgery(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)
    runtime = FakeRecoveryRuntime()
    skill = HarnessMacroActionSkill(
        artifact_bridge=FakeArtifactBridge(runtime=runtime)
    )
    node = recovery_node(
        node_origin=NodeOrigin.REPAIR,
        source_origin="llm",
        source_metadata={"system_owned_recovery": True},
    )

    with pytest.raises(PhysicalRequestValidationError) as captured:
        skill.build_request(node, context())

    assert captured.value.diagnostic.code == \
        "SYSTEM_ONLY_ACTION_NOT_ALLOWED"
    assert captured.value.diagnostic.details["source_origin"] == "llm"
    assert runtime.permit_calls == []


@pytest.mark.parametrize(
    "missing_parameter",
    [
        "configuration_id",
        "component_scope",
        "recovery_contract",
    ],
)
def test_recover_workspace_requires_bounded_recovery_contract(
    monkeypatch,
    missing_parameter,
) -> None:
    install_fake_contracts(monkeypatch)
    runtime = FakeRecoveryRuntime()
    parameters = {missing_parameter: ""}
    skill = HarnessMacroActionSkill(
        artifact_bridge=FakeArtifactBridge(runtime=runtime)
    )

    with pytest.raises(PhysicalRequestValidationError) as captured:
        skill.build_request(
            recovery_node(**parameters),
            context(),
        )

    assert captured.value.diagnostic.code == "INVALID_PHYSICAL_REQUEST"
    assert captured.value.diagnostic.details["missing_parameters"] == [
        missing_parameter
    ]
    assert runtime.permit_calls == []


def test_request_requires_kernel_request_id_and_resolved_follow_path(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)
    skill = HarnessMacroActionSkill(artifact_bridge=FakeArtifactBridge())

    with pytest.raises(ValueError, match="SkillContext.request_id"):
        skill.build_request(physical_node("pick_object"), context(None))
    with pytest.raises(
        PhysicalRequestValidationError,
        match="requires a declared path_ref",
    ):
        skill.build_request(physical_node("follow_path"), context())

    with pytest.raises(
        PhysicalRequestValidationError,
        match="requires a declared path_ref",
    ):
        skill.build_request(
            physical_node(
                "reposition_for_interaction",
                participants={"reference": ["plate_1"]},
            ),
            context(),
        )


def test_route_request_fails_closed_without_world_snapshot(
    monkeypatch,
) -> None:
    install_fake_contracts(monkeypatch)
    bridge = FakeArtifactBridge(
        {("detour_path", "path_ref"): "path/verified"}
    )
    bridge.runtime.world.freeze_snapshot = lambda: {}
    skill = HarnessMacroActionSkill(artifact_bridge=bridge)

    with pytest.raises(PhysicalRequestValidationError) as captured:
        skill.build_request(
            physical_node(
                "follow_path",
                path_ref="path/declared",
            ),
            context(),
        )

    assert captured.value.diagnostic.code == "STALE_RELEVANT_STATE"
    assert captured.value.diagnostic.retryable is True
    assert captured.value.diagnostic.details["affected_refs"] == [
        "path/verified"
    ]
