from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.demo import demo_world
from task_recursive_tree.integrations.gemini_er2.operations import (
    HarnessSystemOperationAdapter,
)
from task_recursive_tree.integrations.gemini_er2.physical_runtime import (
    normalize_physical_result,
)
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.contracts import (
    PhysicalActionOutcome,
    PhysicalExecutionState,
)
from task_recursive_tree.task.kernel import TaskTreeKernel
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    FailureResolutionKind,
    GraphDelta,
    KernelLimits,
    NodeOrigin,
    NodeOutcome,
    NodeStatus,
    OperationKind,
    RepairProposal,
    TaskEdge,
    TaskNodeSpec,
)
from task_recursive_tree.task.store import TaskTreeStore


TRANSACTION_ID = "txn_cf5321ac"


def _confirmed_timeout_result() -> dict:
    return {
        "action_id": "action_b7236b",
        "transaction_id": TRANSACTION_ID,
        "request_id": "tree:recovery:place-object:attempt:1",
        "definition_ref": "place_object@1.0.0",
        "name": "place_object",
        "termination": "outcome_unknown",
        "effect_state": "confirmed",
        "verification": "true",
        "failure_code": "TOOL_TIMEOUT",
        "automatic_retry": "never",
        "resubmission_policy": "after_reobservation",
        "resubmission_requires_new_request": True,
        "effects": [
            {
                "effect_id": "effect_inside_support_region_0",
                "predicate": "inside_support_region",
                "participants": {
                    "subject": ["apple_1"],
                    "region_owner": ["floor_1"],
                },
                "state": "confirmed",
                "required": True,
                "evidence_refs": ["sim_pose_apple_1"],
            },
            {
                "effect_id": "effect_attached_to_any_end_effector_1",
                "predicate": "attached_to_any_end_effector",
                "participants": {"object": ["apple_1"]},
                "state": "refuted",
                "required": False,
                "evidence_refs": ["sim_gripper_holding_entity"],
                "predicate_details": {
                    "occupancy": "empty",
                    "held_entity_id": None,
                    "attachment_state": "none",
                },
            },
        ],
    }


@pytest.mark.parametrize(
    "normalizer",
    [
        normalize_physical_result,
        TaskTreeKernel._normalize_physical_outcome,
    ],
    ids=["physical_gateway", "task_kernel"],
)
def test_confirmed_timeout_uses_reconciliation_diagnostic(normalizer) -> None:
    outcome = normalizer(_confirmed_timeout_result())

    assert outcome.state is PhysicalExecutionState.OUTCOME_UNKNOWN
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "OUTCOME_UNKNOWN"
    assert outcome.diagnostic.details["failure_code"] == "TOOL_TIMEOUT"
    assert outcome.diagnostic.details["physical_failure_code"] == "TOOL_TIMEOUT"


class _Artifacts:
    def register_result(self, _node, _result):
        return ()


class _PhysicalSkill:
    def build_request(self, _node, context):
        return SimpleNamespace(
            name="place_object",
            request_id=context.request_id,
        )


class _ReconciliationResolver:
    entry_node_id = "root/place/reconcile"

    def __init__(self) -> None:
        self.diagnostics = []

    def propose(self, node, diagnostic, _context, _repair_index):
        self.diagnostics.append(diagnostic)
        reconciliation = TaskNodeSpec(
            node_id=self.entry_node_id,
            task_type="reconcile",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.REPAIR,
            parameters={
                "system_check": "reconcile",
                "transaction_id": TRANSACTION_ID,
                "trigger": "regression-test",
            },
        )
        return RepairProposal(
            delta=GraphDelta.from_specs(
                (reconciliation,),
                (
                    TaskEdge(
                        parent_id=node.node_id,
                        child_id=reconciliation.node_id,
                        kind=EdgeKind.REPAIR,
                    ),
                ),
            ),
            entry_node_id=reconciliation.node_id,
            rationale="Reconcile the outcome-unknown transaction",
            kind=FailureResolutionKind.RECONCILIATION,
        )


class _ReconcileThenRepairResolver:
    reconciliation_id = "root/place/reconcile"
    repair_id = "root/place/repair"

    def __init__(self) -> None:
        self.calls = []

    def propose(self, node, diagnostic, _context, repair_index):
        self.calls.append((diagnostic.code, repair_index))
        if diagnostic.code == "OUTCOME_UNKNOWN":
            task_type = "reconcile"
            entry_id = self.reconciliation_id
            resolution_kind = FailureResolutionKind.RECONCILIATION
            parameters = {
                "system_check": "reconcile",
                "transaction_id": TRANSACTION_ID,
                "trigger": "regression-test",
            }
        else:
            assert diagnostic.code == "PATH_BLOCKED"
            task_type = "repair_check"
            entry_id = self.repair_id
            resolution_kind = FailureResolutionKind.REPAIR
            parameters = {}
        entry = TaskNodeSpec(
            node_id=entry_id,
            task_type=task_type,
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.REPAIR,
            parameters=parameters,
        )
        return RepairProposal(
            delta=GraphDelta.from_specs(
                (entry,),
                (
                    TaskEdge(
                        parent_id=node.node_id,
                        child_id=entry.node_id,
                        kind=EdgeKind.REPAIR,
                        order=0,
                    ),
                ),
            ),
            entry_node_id=entry.node_id,
            rationale=f"Resolve {diagnostic.code}",
            kind=resolution_kind,
        )


class _AlwaysSucceed:
    def run(self, _node, _context):
        return NodeOutcome.success()


class _Runtime:
    def __init__(
        self,
        effect_state: str,
        verification: str,
        *,
        resubmission_policy: str = "after_reobservation",
    ) -> None:
        self.world = SimpleNamespace(revision=42)
        self.effect_state = effect_state
        self.verification = verification
        self.resubmission_policy = resubmission_policy
        self.physical_request_ids = []
        self.reconciliation_calls = []

    def execute(self, request):
        self.physical_request_ids.append(request.request_id)
        result = _confirmed_timeout_result()
        result["request_id"] = request.request_id
        result["resubmission_policy"] = self.resubmission_policy
        if len(self.physical_request_ids) == 1:
            return result
        result.update(
            {
                "transaction_id": "txn-resubmitted",
                "termination": "succeeded",
                "effect_state": "confirmed",
                "verification": "true",
                "failure_code": None,
            }
        )
        result["effects"][0]["state"] = "confirmed"
        return result

    def reconcile_pending_transactions(
        self,
        trigger,
        *,
        transaction_id=None,
    ):
        self.reconciliation_calls.append((trigger, transaction_id))
        reconciled_result = _confirmed_timeout_result()
        reconciled_result["effect_state"] = self.effect_state
        reconciled_result["verification"] = self.verification
        reconciled_result["resubmission_policy"] = (
            self.resubmission_policy
        )
        reconciled_result["effects"][0]["state"] = self.effect_state
        reconciled_result["recovery_context"] = {
            "reconciliation_status": "closed",
            "reconciliation_trigger": trigger,
            "reconciliation_evidence_revision": "evidence-reconciled",
        }
        return [
            {
                "transaction_id": TRANSACTION_ID,
                "status": "closed",
                "effect_state": self.effect_state,
                "verification": self.verification,
                "closed_resources": ["robot_1/selected_gripper"],
                "evidence_revision": "evidence-reconciled",
                "result": reconciled_result,
            }
        ]


class _ReconcileThenRepairRuntime(_Runtime):
    def __init__(self) -> None:
        super().__init__("refuted", "false")

    def execute(self, request):
        self.physical_request_ids.append(request.request_id)
        if len(self.physical_request_ids) == 1:
            result = _confirmed_timeout_result()
            result["request_id"] = request.request_id
            return result
        if len(self.physical_request_ids) == 2:
            return PhysicalActionOutcome(
                state=PhysicalExecutionState.FAILED,
                diagnostic=Diagnostic(
                    "PATH_BLOCKED",
                    "route changed after reconciliation",
                    repairable=True,
                ),
            )
        return PhysicalActionOutcome.succeeded()


def _run_reconciliation_case(
    effect_state: str,
    verification: str,
    *,
    max_node_attempts: int = 3,
    resubmission_policy: str = "after_reobservation",
):
    runtime = _Runtime(
        effect_state,
        verification,
        resubmission_policy=resubmission_policy,
    )
    resolver = _ReconciliationResolver()
    grid, observation = demo_world()
    application = build_application(grid, observation)
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=runtime,
            semantics=None,
        ),
        decomposers={},
        system_operations={
            "reconcile": HarnessSystemOperationAdapter(
                runtime=runtime,
                artifacts=_Artifacts(),
                route_planner=object(),
            ),
        },
        physical_skills={"place_object": _PhysicalSkill()},
        repair_resolver=resolver,
        limits=KernelLimits(
            max_node_attempts=max_node_attempts,
            max_reconciliations=2,
        ),
    )
    physical_node = TaskNodeSpec(
        node_id="root/place",
        task_type="place_object",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=1,
        max_repairs=1,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=physical_node.node_id,
            delta=GraphDelta.from_specs(
                (physical_node,),
                root_id=physical_node.node_id,
            ),
            task_id="reconciliation-regression",
        )
    )
    status = kernel.run()
    return status, kernel, store, runtime, resolver


@pytest.mark.parametrize(
    (
        "effect_state",
        "verification",
        "expected_status",
    ),
    [
        ("confirmed", "true", NodeStatus.SUCCEEDED),
        (
            "refuted",
            "false",
            NodeStatus.SUCCEEDED,
        ),
    ],
    ids=["confirmed", "refuted"],
)
def test_reconciliation_mounts_and_only_dispatches_unique_request_ids(
    effect_state,
    verification,
    expected_status,
) -> None:
    status, _kernel, store, runtime, resolver = _run_reconciliation_case(
        effect_state,
        verification,
    )

    assert status is expected_status
    expected_requests = [
        "tree:reconciliation-regression:root/place:attempt:1"
    ]
    if effect_state == "refuted":
        expected_requests.append(
            "tree:reconciliation-regression:root/place:attempt:2"
        )
    assert runtime.physical_request_ids == expected_requests
    assert len(runtime.physical_request_ids) == len(
        set(runtime.physical_request_ids)
    )
    assert runtime.reconciliation_calls == [
        ("regression-test", TRANSACTION_ID)
    ]
    assert len(resolver.diagnostics) == 1
    assert resolver.diagnostics[0].code == "OUTCOME_UNKNOWN"
    assert (
        resolver.diagnostics[0].details["physical_failure_code"]
        == "TOOL_TIMEOUT"
    )

    reconciliation_id = resolver.entry_node_id
    assert store.spec(reconciliation_id).operation_kind is OperationKind.SYSTEM
    assert store.runtime(reconciliation_id).status is NodeStatus.SUCCEEDED
    assert store.edges("root/place", kind=EdgeKind.REPAIR) == (
        TaskEdge(
            parent_id="root/place",
            child_id=reconciliation_id,
            kind=EdgeKind.REPAIR,
        ),
    )
    mounted = [
        event
        for event in store.events()
        if event.event_type == "reconciliation_mounted"
    ]
    assert len(mounted) == 1
    assert mounted[0].node_id == "root/place"
    assert mounted[0].data["entry"] == reconciliation_id

    physical_runtime = store.runtime("root/place")
    assert physical_runtime.repairs == 0
    assert (
        physical_runtime.adapter_state["last_reconciliation_id"]
        == reconciliation_id
    )
    assert physical_runtime.status is NodeStatus.SUCCEEDED
    assert physical_runtime.last_diagnostic is None
    assert "outcome_unknown" not in physical_runtime.adapter_state
    assert "active_request_id" not in physical_runtime.adapter_state
    assert "active_request_hash" not in physical_runtime.adapter_state
    if effect_state == "confirmed":
        assert (
            physical_runtime.adapter_state["last_result"]["reconciliation"][
                "status"
            ]
            == "confirmed"
        )
        request_id = expected_requests[0]
        assert physical_runtime.adapter_state["last_request_id"] == request_id
        assert physical_runtime.adapter_state["last_reconciliation_state"] == (
            "confirmed"
        )
        assert physical_runtime.adapter_state["reconciled_request_ids"] == (
            request_id,
        )
        assert physical_runtime.adapter_state["dispatch_state"]["state"] == (
            "reconciled_succeeded"
        )
        assert (
            physical_runtime.adapter_state["dispatch_state"][
                "physical_outcome_known"
            ]
            is True
        )
        assert (
            physical_runtime.adapter_state["dispatch_state"][
                "requires_reconciliation"
            ]
            is False
        )
        assert (
            physical_runtime.adapter_state["attempt_ledger"][0]["state"]
            == "reconciled_succeeded"
        )
    else:
        assert (
            physical_runtime.adapter_state["last_result"]["termination"]
            == "succeeded"
        )
        assert physical_runtime.adapter_state["reconciled_resubmissions"] == 1
        assert physical_runtime.adapter_state["reconciled_request_ids"] == (
            "tree:reconciliation-regression:root/place:attempt:1",
        )
        assert physical_runtime.adapter_state["last_reconciliation_state"] == (
            "refuted"
        )
        assert (
            physical_runtime.adapter_state["attempt_ledger"][0]["state"]
            == "reconciled_refuted"
        )
        assert (
            physical_runtime.adapter_state["attempt_ledger"][0][
                "physical_outcome_known"
            ]
            is True
        )
        assert (
            physical_runtime.adapter_state["attempt_ledger"][0][
                "requires_reconciliation"
            ]
            is False
        )
        scheduled = [
            event
            for event in store.events()
            if event.event_type == "reconciled_resubmission_scheduled"
        ]
        assert len(scheduled) == 1
        assert scheduled[0].data["next_request_id"].endswith(
            "attempt:2"
        )


def test_repair_after_reconciliation_gets_next_mount_order() -> None:
    runtime = _ReconcileThenRepairRuntime()
    resolver = _ReconcileThenRepairResolver()
    grid, observation = demo_world()
    application = build_application(grid, observation)
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=runtime,
            semantics=None,
        ),
        decomposers={},
        system_operations={
            "reconcile": HarnessSystemOperationAdapter(
                runtime=runtime,
                artifacts=_Artifacts(),
                route_planner=object(),
            ),
            "repair_check": _AlwaysSucceed(),
        },
        physical_skills={"place_object": _PhysicalSkill()},
        repair_resolver=resolver,
        limits=KernelLimits(
            max_node_attempts=3,
            max_reconciliations=2,
        ),
    )
    physical_node = TaskNodeSpec(
        node_id="root/place",
        task_type="place_object",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=1,
        max_repairs=1,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=physical_node.node_id,
            delta=GraphDelta.from_specs(
                (physical_node,),
                root_id=physical_node.node_id,
            ),
            task_id="reconciliation-then-repair",
        )
    )

    assert kernel.run(max_steps=100) is NodeStatus.SUCCEEDED
    assert resolver.calls == [
        ("OUTCOME_UNKNOWN", 0),
        ("PATH_BLOCKED", 0),
    ]
    repair_edges = store.edges("root/place", kind=EdgeKind.REPAIR)
    assert [
        (edge.child_id, edge.order) for edge in repair_edges
    ] == [
        (resolver.reconciliation_id, 0),
        (resolver.repair_id, 1),
    ]
    assert runtime.physical_request_ids == [
        "tree:reconciliation-then-repair:root/place:attempt:1",
        "tree:reconciliation-then-repair:root/place:attempt:2",
        "tree:reconciliation-then-repair:root/place:attempt:3",
    ]


def test_refuted_reconciliation_blocks_when_dispatch_budget_is_exhausted() -> None:
    status, _kernel, store, runtime, _resolver = (
        _run_reconciliation_case(
            "refuted",
            "false",
            max_node_attempts=1,
        )
    )

    assert status is NodeStatus.BLOCKED
    assert runtime.physical_request_ids == [
        "tree:reconciliation-regression:root/place:attempt:1"
    ]
    physical_runtime = store.runtime("root/place")
    assert physical_runtime.last_diagnostic is not None
    assert (
        physical_runtime.last_diagnostic.code
        == "NODE_ATTEMPTS_EXHAUSTED"
    )


def test_refuted_reconciliation_requires_after_reobservation_policy() -> None:
    status, _kernel, store, runtime, _resolver = (
        _run_reconciliation_case(
            "refuted",
            "false",
            resubmission_policy="not_applicable",
        )
    )

    assert status is NodeStatus.FAILED
    assert runtime.physical_request_ids == [
        "tree:reconciliation-regression:root/place:attempt:1"
    ]
    physical_runtime = store.runtime("root/place")
    assert physical_runtime.last_diagnostic is not None
    assert (
        physical_runtime.last_diagnostic.code
        == "RECONCILIATION_REFUTED"
    )
