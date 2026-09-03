from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.core.model import Diagnostic, PredicateFormula
from task_recursive_tree.demo import demo_world
from task_recursive_tree.robot.ports import ActionRequest
from task_recursive_tree.runtime.effects import PhysicalEffectError
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.contracts import (
    PhysicalActionOutcome,
    PhysicalExecutionState,
    SemanticCheck,
    SemanticState,
)
from task_recursive_tree.task.kernel import TaskTreeKernel
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    ExecutionPolicy,
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


class RecordingSkill:
    def build_request(self, node, context):
        return SimpleNamespace(
            request_id=context.request_id,
            action_name=node.task_type,
        )


class CancelThenSucceedRuntime:
    def __init__(self) -> None:
        self.request_ids: list[str] = []
        self.clear_calls = 0

    def execute(self, request):
        self.request_ids.append(request.request_id)
        if len(self.request_ids) == 1:
            return PhysicalActionOutcome(
                PhysicalExecutionState.CANCELLED,
                result={
                    "termination": "canceled",
                    "effect_state": "not_started",
                    "transaction_id": "txn-cancelled",
                    "cancellation": {
                        "dispatch_state": "stopped",
                        "transaction_status": "closed",
                        "quiescent": True,
                        "no_side_effects_verified": True,
                        "evidence_revision": "world:cancelled",
                    },
                },
                transaction_id="txn-cancelled",
            )
        return PhysicalActionOutcome.succeeded(
            result={
                "termination": "succeeded",
                "effect_state": "confirmed",
                "verification": "true",
                "transaction_id": "txn-succeeded",
            },
            transaction_id="txn-succeeded",
        )

    def clear_cancel_request(self) -> None:
        self.clear_calls += 1


class EmptyDecomposer:
    def expand(self, node, context):
        return GraphDelta()


class AmbiguousCancelRuntime:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def execute(self, request):
        self.request_ids.append(request.request_id)
        return PhysicalActionOutcome(
            PhysicalExecutionState.CANCELLED,
            result={
                "termination": "canceled",
                "transaction_id": "txn-partial",
            },
            transaction_id="txn-partial",
        )


class AlwaysSucceed:
    def run(self, node, context):
        return NodeOutcome.success()


class FailThenSucceedRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        del request
        self.calls += 1
        if self.calls == 1:
            return PhysicalActionOutcome(
                PhysicalExecutionState.FAILED,
                diagnostic=Diagnostic(
                    "PATH_BLOCKED",
                    "route changed before execution",
                ),
            )
        return PhysicalActionOutcome.succeeded()


class ImmediateSuccessRuntime:
    def execute(self, request):
        del request
        return PhysicalActionOutcome.succeeded()


class UnexpectedDispatchRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        del request
        self.calls += 1
        return PhysicalActionOutcome.succeeded()


class DurablePriorDispatchRunner:
    def run(self, runtime, effect):
        del runtime
        raise PhysicalEffectError(
            "request already has a durable result",
            details={
                "request_id": effect.request_id,
                "request_hash": effect.request_hash,
                "journal_state": "completed",
                "journal_result": {"termination": "succeeded"},
            },
        )


class ReservationFailureRunner:
    def run(self, runtime, effect):
        del runtime
        raise PhysicalEffectError(
            "journal reservation failed",
            details={
                "request_id": effect.request_id,
                "request_hash": effect.request_hash,
                "dispatch_stage": "reservation",
            },
        )


class KnownNoDispatchDiagnosticRunner:
    def run(self, runtime, effect):
        del runtime, effect
        raise DiagnosticFailure(
            Diagnostic(
                "RESOURCE_BUSY",
                "physical resources were unavailable before dispatch",
                details={
                    "dispatch_stage": "reservation",
                    "physical_dispatch_started": False,
                    "physical_outcome_known": True,
                    "requires_reconciliation": False,
                },
                retryable=True,
            )
        )


class FailingBuildSkill:
    def build_request(self, node, context):
        del node, context
        raise ValueError("request validation failed")


class DiagnosticFailure(RuntimeError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class ContradictoryDispatchEvidenceRuntime:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def execute(self, request):
        self.request_ids.append(request.request_id)
        raise DiagnosticFailure(
            Diagnostic(
                "GUARD_FAILED",
                "adapter supplied incomplete no-dispatch evidence",
                details={
                    "dispatch_stage": "not_started",
                    "physical_dispatch_started": False,
                    "physical_outcome_known": False,
                    "requires_reconciliation": False,
                },
                retryable=True,
            )
        )


class ContradictoryTypedOutcomeRuntime:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def execute(self, request):
        self.request_ids.append(request.request_id)
        details = {
            "dispatch_stage": "not_started",
            "physical_dispatch_started": False,
            "physical_outcome_known": True,
            "requires_reconciliation": False,
        }
        return PhysicalActionOutcome(
            PhysicalExecutionState.OUTCOME_UNKNOWN,
            result=details,
            diagnostic=Diagnostic(
                "RESOURCE_BUSY",
                "adapter returned a contradictory typed outcome",
                details=details,
                retryable=True,
            ),
        )


class OutcomeUnknownBuildSkill:
    def build_request(self, node, context):
        del node, context
        raise DiagnosticFailure(
            Diagnostic(
                "OUTCOME_UNKNOWN",
                "request preparation reported an unknown outcome",
                details={
                    "dispatch_stage": "execution",
                    "physical_dispatch_started": True,
                    "physical_outcome_known": False,
                    "requires_reconciliation": True,
                },
            )
        )


class GuardFailingSkill:
    def build_request(self, node, context):
        return ActionRequest(
            request_id=context.request_id,
            action_name=node.task_type,
            commands=(),
            resources=frozenset(),
            preconditions=(
                PredicateFormula(
                    "object_held",
                    {"object_id": "not-held"},
                ),
            ),
        )


class MissingArtifactSkill:
    def build_request(self, node, context):
        return ActionRequest(
            request_id=context.request_id,
            action_name=node.task_type,
            commands=(),
            resources=frozenset(),
            artifact_refs=("artifact/does-not-exist",),
        )


class RaisingDiagnosticRuntime:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        del request
        self.calls += 1
        raise DiagnosticFailure(
            Diagnostic(
                "PATH_BLOCKED",
                "backend raised after dispatch began",
            )
        )


class FailThreeTimesThenSucceedRuntime:
    def __init__(self) -> None:
        self.request_ids: list[str] = []

    def execute(self, request):
        self.request_ids.append(request.request_id)
        if len(self.request_ids) <= 3:
            return PhysicalActionOutcome(
                PhysicalExecutionState.FAILED,
                diagnostic=Diagnostic(
                    "PATH_BLOCKED",
                    "the repaired route still needs refinement",
                    repairable=True,
                ),
            )
        return PhysicalActionOutcome.succeeded()


class RecordingInvalidator:
    def __init__(self) -> None:
        self.calls = []

    def invalidate(self, artifact_refs) -> None:
        self.calls.append(tuple(artifact_refs))


class FailingTransactionalInvalidator(RecordingInvalidator):
    @contextmanager
    def mutation(self):
        snapshot = list(self.calls)
        try:
            yield
        except BaseException:
            self.calls = snapshot
            raise

    def invalidate(self, artifact_refs) -> None:
        super().invalidate(artifact_refs)
        raise RuntimeError("artifact invalidation failed")


class InvalidationRepairResolver:
    def propose(self, node, diagnostic, context, repair_index):
        del diagnostic, context
        repair = TaskNodeSpec(
            node_id=f"{node.node_id}/repair-{repair_index}",
            task_type="AlwaysSucceed",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.REPAIR,
        )
        return RepairProposal(
            delta=GraphDelta.from_specs(
                (repair,),
                (
                    TaskEdge(
                        node.node_id,
                        repair.node_id,
                        kind=EdgeKind.REPAIR,
                    ),
                ),
            ),
            entry_node_id=repair.node_id,
            rationale="Refresh the blocked route",
            invalidates_artifacts=("path/stale",),
        )


class RepeatedRepairResolver:
    def propose(self, node, diagnostic, context, repair_index):
        del diagnostic, context
        repair = TaskNodeSpec(
            node_id=f"{node.node_id}/repair-{repair_index}",
            task_type="AlwaysSucceed",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.REPAIR,
        )
        return RepairProposal(
            delta=GraphDelta.from_specs(
                (repair,),
                (
                    TaskEdge(
                        node.node_id,
                        repair.node_id,
                        kind=EdgeKind.REPAIR,
                    ),
                ),
            ),
            entry_node_id=repair.node_id,
            rationale="Refine the physical plan",
        )


class AlwaysSatisfiedSemantics:
    def evaluate_goal(self, node, runtime):
        del node, runtime
        return SemanticCheck(SemanticState.SATISFIED)

    def evaluate_preconditions(self, node, runtime):
        del node, runtime
        return SemanticCheck(SemanticState.SATISFIED)

    def evaluate_postconditions(self, node, runtime):
        del node, runtime
        return SemanticCheck(SemanticState.SATISFIED)


def _physical_kernel(
    runtime,
    *,
    execution_policy=ExecutionPolicy.SKIP_IF_GOAL_SATISFIED,
    semantics=None,
    effect_runner=None,
    physical_skill=None,
    max_attempts=1,
) -> tuple[TaskTreeKernel, TaskTreeStore]:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    selected_runtime = (
        application._services.runtime
        if runtime is None
        else runtime
    )
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=selected_runtime,
            semantics=semantics,
        ),
        decomposers={},
        system_operations={},
        physical_skills={
            "PhysicalTest": physical_skill or RecordingSkill()
        },
        repair_resolver=application.kernel._repair_resolver,
        effect_runner=effect_runner,
    )
    root = TaskNodeSpec(
        node_id="root/action",
        task_type="PhysicalTest",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=max_attempts,
        max_repairs=0,
        execution_policy=execution_policy,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs(
                (root,), root_id=root.node_id
            ),
            task_id="task-42",
        )
    )
    return kernel, store


def test_physical_cancel_pauses_without_terminalizing_node() -> None:
    runtime = CancelThenSucceedRuntime()
    kernel, store = _physical_kernel(runtime)

    for _ in range(10):
        tick = kernel.tick()
        if tick.status == "PAUSED":
            break
    else:
        raise AssertionError("kernel did not pause after cancellation")

    node_runtime = store.runtime("root/action")
    assert kernel.paused
    assert node_runtime.status is NodeStatus.RUNNING
    assert node_runtime.phase == "goal_check"
    assert store.stack()[-1].node_id == "root/action"
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1"
    ]
    assert runtime.clear_calls == 1
    dispatch = node_runtime.adapter_state["dispatch_state"]
    assert dispatch["state"] == "cancelled"
    assert dispatch["request_hash"]

    kernel.resume()
    for _ in range(10):
        tick = kernel.tick()
        if tick.terminal:
            break
    assert tick.status == "SUCCEEDED"
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1",
        "tree:task-42:root/action:attempt:2",
    ]
    assert store.runtime("root/action").attempts == 1


def test_required_physical_execution_retries_after_cancelled_result() -> None:
    runtime = CancelThenSucceedRuntime()
    kernel, store = _physical_kernel(
        runtime,
        execution_policy=ExecutionPolicy.REQUIRE_EXECUTION,
        semantics=AlwaysSatisfiedSemantics(),
    )

    for _ in range(10):
        tick = kernel.tick()
        if tick.status == "PAUSED":
            break
    else:
        raise AssertionError("kernel did not pause after cancellation")

    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1"
    ]
    assert store.runtime("root/action").status is NodeStatus.RUNNING

    kernel.resume()
    for _ in range(10):
        tick = kernel.tick()
        if tick.terminal:
            break

    assert tick.status == "SUCCEEDED"
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1",
        "tree:task-42:root/action:attempt:2",
    ]


def test_ambiguous_physical_cancel_requires_reconciliation() -> None:
    runtime = AmbiguousCancelRuntime()
    kernel, store = _physical_kernel(runtime)

    assert kernel.run(max_steps=20) is NodeStatus.BLOCKED

    node_runtime = store.runtime("root/action")
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1"
    ]
    assert node_runtime.last_diagnostic.code == "OUTCOME_UNKNOWN"
    assert node_runtime.adapter_state["outcome_unknown"] is True
    assert (
        node_runtime.adapter_state["dispatch_state"]["state"]
        == "outcome_unknown"
    )
    assert (
        node_runtime.adapter_state["attempt_ledger"][0]["state"]
        == "outcome_unknown"
    )
    assert (
        node_runtime.adapter_state["active_request_id"]
        == "tree:task-42:root/action:attempt:1"
    )


def test_durable_effect_error_evidence_reaches_reconciliation() -> None:
    runtime = UnexpectedDispatchRuntime()
    kernel, store = _physical_kernel(
        runtime,
        effect_runner=DurablePriorDispatchRunner(),
    )

    assert kernel.run(max_steps=20) is NodeStatus.BLOCKED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.calls == 0
    assert diagnostic.code == "OUTCOME_UNKNOWN"
    assert diagnostic.details["journal_state"] == "completed"
    assert diagnostic.details["request_id"] == (
        "tree:task-42:root/action:attempt:1"
    )
    assert diagnostic.details["journal_result"] == {
        "termination": "succeeded"
    }
    assert (
        node_runtime.adapter_state["attempt_ledger"][0]["state"]
        == "outcome_unknown"
    )


def test_reservation_failure_is_failed_without_reconciliation() -> None:
    runtime = UnexpectedDispatchRuntime()
    kernel, store = _physical_kernel(
        runtime,
        effect_runner=ReservationFailureRunner(),
    )

    assert kernel.run(max_steps=20) is NodeStatus.FAILED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.calls == 0
    assert diagnostic.code == "PHYSICAL_DISPATCH_FAILED"
    assert diagnostic.details["dispatch_stage"] == "reservation"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert "outcome_unknown" not in node_runtime.adapter_state
    assert (
        node_runtime.adapter_state["attempt_ledger"][0]["state"]
        == "not_dispatched"
    )
    assert (
        node_runtime.adapter_state["attempt_ledger"][0][
            "dispatch_stage"
        ]
        == "reservation"
    )
    assert (
        node_runtime.adapter_state["dispatch_attempts_in_epoch"]
        == 0
    )


def test_plain_runner_exception_preserves_no_dispatch_evidence() -> None:
    runtime = UnexpectedDispatchRuntime()
    kernel, store = _physical_kernel(
        runtime,
        effect_runner=KnownNoDispatchDiagnosticRunner(),
    )

    assert kernel.run(max_steps=20) is NodeStatus.BLOCKED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.calls == 0
    assert diagnostic.code == "RESOURCE_BUSY"
    assert diagnostic.details["dispatch_stage"] == "reservation"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert diagnostic.details["physical_outcome_known"] is True
    assert diagnostic.details["requires_reconciliation"] is False
    assert node_runtime.adapter_state["dispatch_state"]["state"] == (
        "not_dispatched"
    )
    assert node_runtime.adapter_state["attempt_ledger"][0]["state"] == (
        "not_dispatched"
    )
    assert node_runtime.adapter_state["dispatch_attempts_in_epoch"] == 0
    assert node_runtime.adapter_state.get("reconciliation_attempts", 0) == 0
    assert "outcome_unknown" not in node_runtime.adapter_state


def test_request_build_failure_is_failed_before_dispatch_state() -> None:
    runtime = UnexpectedDispatchRuntime()
    kernel, store = _physical_kernel(
        runtime,
        physical_skill=FailingBuildSkill(),
    )

    assert kernel.run(max_steps=20) is NodeStatus.FAILED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.calls == 0
    assert diagnostic.code == "PHYSICAL_DISPATCH_PREPARATION_FAILED"
    assert diagnostic.details["dispatch_stage"] == "not_started"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert "attempt_ledger" not in node_runtime.adapter_state
    assert "outcome_unknown" not in node_runtime.adapter_state


def test_predispatch_diagnostic_cannot_force_reconciliation() -> None:
    runtime = UnexpectedDispatchRuntime()
    kernel, store = _physical_kernel(
        runtime,
        physical_skill=OutcomeUnknownBuildSkill(),
    )

    assert kernel.run(max_steps=20) is NodeStatus.FAILED

    diagnostic = store.runtime("root/action").last_diagnostic
    assert diagnostic.code == "PHYSICAL_DISPATCH_PREPARATION_FAILED"
    assert diagnostic.details["physical_failure_code"] == (
        "OUTCOME_UNKNOWN"
    )
    assert diagnostic.details["dispatch_stage"] == "not_started"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert runtime.calls == 0


def test_harness_guard_failure_is_known_before_backend_dispatch() -> None:
    kernel, store = _physical_kernel(
        None,
        physical_skill=GuardFailingSkill(),
    )
    runtime = kernel._services.runtime

    assert kernel.run(max_steps=20) is NodeStatus.FAILED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert diagnostic.code == "GUARD_FAILED"
    assert diagnostic.details["dispatch_stage"] == "not_started"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert diagnostic.details["physical_outcome_known"] is True
    assert node_runtime.adapter_state["dispatch_state"]["state"] == (
        "not_dispatched"
    )
    assert node_runtime.adapter_state["dispatch_attempts_in_epoch"] == 0
    assert runtime.transaction_records() == ()


def test_harness_missing_artifact_is_known_before_backend_dispatch() -> None:
    kernel, store = _physical_kernel(
        None,
        physical_skill=MissingArtifactSkill(),
    )
    runtime = kernel._services.runtime

    assert kernel.run(max_steps=20) is NodeStatus.FAILED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert diagnostic.code == "PHYSICAL_PREPARATION_FAILED"
    assert diagnostic.details["dispatch_stage"] == "not_started"
    assert diagnostic.details["physical_dispatch_started"] is False
    assert diagnostic.details["physical_outcome_known"] is True
    assert diagnostic.details["requires_reconciliation"] is False
    assert node_runtime.adapter_state["dispatch_state"]["state"] == (
        "not_dispatched"
    )
    assert node_runtime.adapter_state["attempt_ledger"][0]["state"] == (
        "not_dispatched"
    )
    assert node_runtime.adapter_state["dispatch_attempts_in_epoch"] == 0
    assert node_runtime.adapter_state.get("reconciliation_attempts", 0) == 0
    assert "outcome_unknown" not in node_runtime.adapter_state
    assert runtime.transaction_records() == ()


def test_runtime_exception_diagnostic_requires_reconciliation() -> None:
    runtime = RaisingDiagnosticRuntime()
    kernel, store = _physical_kernel(runtime)

    assert kernel.run(max_steps=20) is NodeStatus.BLOCKED

    diagnostic = store.runtime("root/action").last_diagnostic
    assert diagnostic.code == "OUTCOME_UNKNOWN"
    assert diagnostic.details["physical_failure_code"] == "PATH_BLOCKED"
    assert diagnostic.details["dispatch_stage"] == "execution"
    assert diagnostic.details["physical_dispatch_started"] is True
    assert runtime.calls == 1


def test_incomplete_no_dispatch_evidence_cannot_trigger_redispatch() -> None:
    runtime = ContradictoryDispatchEvidenceRuntime()
    kernel, store = _physical_kernel(runtime, max_attempts=2)

    assert kernel.run(max_steps=30) is NodeStatus.BLOCKED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1"
    ]
    assert diagnostic.code == "OUTCOME_UNKNOWN"
    assert diagnostic.details["physical_failure_code"] == "GUARD_FAILED"
    assert diagnostic.details["dispatch_stage"] == "execution"
    assert diagnostic.details["physical_dispatch_started"] is True
    assert diagnostic.details["physical_outcome_known"] is False
    assert diagnostic.details["requires_reconciliation"] is True
    assert node_runtime.adapter_state["outcome_unknown"] is True
    assert node_runtime.adapter_state["dispatch_state"]["state"] == (
        "outcome_unknown"
    )
    assert node_runtime.adapter_state["attempt_ledger"][0]["state"] == (
        "outcome_unknown"
    )
    assert node_runtime.adapter_state["active_request_id"] == (
        "tree:task-42:root/action:attempt:1"
    )


def test_typed_outcome_unknown_cannot_self_certify_as_known() -> None:
    runtime = ContradictoryTypedOutcomeRuntime()
    kernel, store = _physical_kernel(runtime, max_attempts=2)

    assert kernel.run(max_steps=30) is NodeStatus.BLOCKED

    node_runtime = store.runtime("root/action")
    diagnostic = node_runtime.last_diagnostic
    assert runtime.request_ids == [
        "tree:task-42:root/action:attempt:1"
    ]
    assert diagnostic.code == "OUTCOME_UNKNOWN"
    assert diagnostic.details["physical_failure_code"] == "RESOURCE_BUSY"
    assert diagnostic.details["physical_outcome_known"] is False
    assert diagnostic.details["requires_reconciliation"] is True
    assert node_runtime.adapter_state["outcome_unknown"] is True
    assert node_runtime.adapter_state["dispatch_state"]["state"] == (
        "outcome_unknown"
    )
    assert node_runtime.adapter_state["active_request_id"] == (
        "tree:task-42:root/action:attempt:1"
    )


def test_repair_mount_invalidates_declared_artifacts() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    runtime = FailThenSucceedRuntime()
    artifacts = RecordingInvalidator()
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=runtime,
            repair=replace(
                application._services.repair,
                artifacts=artifacts,
            ),
        ),
        decomposers={},
        system_operations={"AlwaysSucceed": AlwaysSucceed()},
        physical_skills={"PhysicalTest": RecordingSkill()},
        repair_resolver=InvalidationRepairResolver(),
    )
    root = TaskNodeSpec(
        node_id="root/action",
        task_type="PhysicalTest",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=2,
        max_repairs=1,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs((root,), root_id=root.node_id),
            task_id="artifact-invalidation",
        )
    )

    assert kernel.run(max_steps=50) is NodeStatus.SUCCEEDED
    assert artifacts.calls == [("path/stale",)]
    mounted = next(
        event
        for event in store.events()
        if event.event_type == "repair_mounted"
    )
    assert mounted.data["invalidated_artifact_refs"] == ["path/stale"]


def test_repair_mount_rolls_back_tree_and_artifacts_together() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    runtime = FailThenSucceedRuntime()
    artifacts = FailingTransactionalInvalidator()
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=runtime,
            repair=replace(
                application._services.repair,
                artifacts=artifacts,
            ),
        ),
        decomposers={},
        system_operations={"AlwaysSucceed": AlwaysSucceed()},
        physical_skills={"PhysicalTest": RecordingSkill()},
        repair_resolver=InvalidationRepairResolver(),
    )
    root = TaskNodeSpec(
        node_id="root/action",
        task_type="PhysicalTest",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=2,
        max_repairs=1,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs((root,), root_id=root.node_id),
            task_id="artifact-rollback",
        )
    )

    try:
        kernel.run(max_steps=50)
    except RuntimeError as exc:
        assert "artifact invalidation failed" in str(exc)
    else:
        raise AssertionError("Artifact failure must abort repair mounting")

    assert artifacts.calls == []
    assert set(store.specs()) == {"root/action"}
    assert not any(
        event.event_type == "repair_mounted"
        for event in store.events()
    )


def test_successful_repairs_start_new_physical_dispatch_epochs() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    runtime = FailThreeTimesThenSucceedRuntime()
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(
            application._services,
            runtime=runtime,
        ),
        decomposers={},
        system_operations={"AlwaysSucceed": AlwaysSucceed()},
        physical_skills={"PhysicalTest": RecordingSkill()},
        repair_resolver=RepeatedRepairResolver(),
        limits=KernelLimits(max_node_attempts=1),
    )
    root = TaskNodeSpec(
        node_id="root/action",
        task_type="PhysicalTest",
        operation_kind=OperationKind.PHYSICAL,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        max_attempts=1,
        max_repairs=3,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs(
                (root,),
                root_id=root.node_id,
            ),
            task_id="repair-dispatch-epochs",
        )
    )

    assert kernel.run(max_steps=200) is NodeStatus.SUCCEEDED
    assert runtime.request_ids == [
        "tree:repair-dispatch-epochs:root/action:attempt:1",
        "tree:repair-dispatch-epochs:root/action:attempt:2",
        "tree:repair-dispatch-epochs:root/action:attempt:3",
        "tree:repair-dispatch-epochs:root/action:attempt:4",
    ]
    adapter_state = store.runtime("root/action").adapter_state
    assert adapter_state["dispatch_attempt"] == 4
    assert adapter_state["dispatch_attempts_in_epoch"] == 1
    assert adapter_state["dispatch_epoch"] == 3
    epoch_events = [
        event
        for event in store.events()
        if event.event_type == "physical_dispatch_epoch_reset"
    ]
    assert len(epoch_events) == 3
    assert [
        event.data["completed_dispatch_attempts"]
        for event in epoch_events
    ] == [1, 1, 1]


def test_cancel_preserves_completed_node_audit() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=application._services,
        decomposers={"Prebuilt": EmptyDecomposer()},
        system_operations={"AlwaysSucceed": AlwaysSucceed()},
        physical_skills={},
        repair_resolver=application.kernel._repair_resolver,
    )
    root = TaskNodeSpec(
        node_id="root",
        task_type="Prebuilt",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        max_attempts=1,
        max_repairs=0,
    )
    first = TaskNodeSpec(
        node_id="root/first",
        task_type="AlwaysSucceed",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
    )
    second = TaskNodeSpec(
        node_id="root/second",
        task_type="AlwaysSucceed",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id="root",
            delta=GraphDelta.from_specs(
                (root, first, second),
                (
                    TaskEdge("root", "root/first", order=0),
                    TaskEdge("root", "root/second", order=1),
                ),
                root_id="root",
            ),
            task_id="cancel-audit",
        )
    )

    for _ in range(30):
        kernel.tick()
        if store.runtime("root/first").status is NodeStatus.SUCCEEDED:
            break
    result = kernel.cancel("superseded")

    assert result.status == "CANCELLED"
    assert result.terminal
    assert store.runtime("root/first").status is NodeStatus.SUCCEEDED
    assert store.runtime("root").status is NodeStatus.CANCELLED
    assert store.runtime("root/second").status is NodeStatus.CANCELLED
    assert store.stack() == ()


def test_external_goal_verification_promotes_terminal_root_with_audit(
) -> None:
    kernel, store = _physical_kernel(CancelThenSucceedRuntime())
    kernel.cancel("primary path was blocked")
    previous = store.runtime("root/action")

    promoted = kernel.promote_after_external_goal_verification(
        {
            "verified": True,
            "formula_value": "true",
            "semantic_value": "true",
            "world_revision": 9,
        },
        recovery_ref="recovery/attempt-3",
    )

    current = store.runtime("root/action")
    assert promoted is True
    assert current.status is NodeStatus.SUCCEEDED
    assert current.last_diagnostic is None
    assert previous.last_diagnostic is not None
    record = current.adapter_state[
        "last_external_goal_verification"
    ]
    assert record["previous_status"] == "cancelled"
    assert record["previous_diagnostic"]["code"] == "TREE_CANCELLED"
    assert record["recovery_ref"] == "recovery/attempt-3"
    assert record["completion_audit"]["verified"] is True
    assert any(
        event.event_type == "external_goal_verification_promoted"
        for event in store.events()
    )


def test_external_goal_verification_rejects_unverified_audit() -> None:
    kernel, store = _physical_kernel(CancelThenSucceedRuntime())
    kernel.cancel("primary path was blocked")

    promoted = kernel.promote_after_external_goal_verification(
        {"verified": False},
        recovery_ref="recovery/attempt-3",
    )

    assert promoted is False
    assert store.runtime("root/action").status is NodeStatus.CANCELLED


def test_external_goal_verification_is_idempotent_and_finalized() -> None:
    kernel, store = _physical_kernel(CancelThenSucceedRuntime())
    kernel.cancel("primary path was blocked")
    audit = {
        "verified": True,
        "formula_value": "true",
        "semantic_value": "true",
    }

    assert kernel.promote_after_external_goal_verification(audit) is True
    first = store.runtime("root/action")
    first_finished_at = first.finished_at
    assert kernel.promote_after_external_goal_verification(audit) is True
    second = store.runtime("root/action")

    assert second.finished_at == first_finished_at
    assert second.adapter_state["initial_terminal_status"] == "cancelled"
    assert second.adapter_state["completion_finalized"] is True
    assert second.adapter_state["terminal_transition_kind"] == (
        "external_goal_verified"
    )
    assert len(
        [
            event
            for event in store.events()
            if event.event_type
            == "external_goal_verification_promoted"
        ]
    ) == 1
    assert kernel.reject_after_completion_verification(
        Diagnostic("STALE_FAILURE", "old completion failure")
    ) is False
    assert store.runtime("root/action").status is NodeStatus.SUCCEEDED


def test_completion_commit_is_idempotent_and_prevents_late_rejection() -> None:
    kernel, store = _physical_kernel(ImmediateSuccessRuntime())
    assert kernel.run(max_steps=20) is NodeStatus.SUCCEEDED
    audit = {"verified": True, "world_revision": 7}
    receipt = {"receipt_ref": "receipt-7"}

    assert kernel.record_task_completion_commit(
        audit,
        completion_receipt=receipt,
    ) is True
    assert kernel.record_task_completion_commit(
        audit,
        completion_receipt=receipt,
    ) is True

    runtime = store.runtime("root/action")
    assert runtime.adapter_state["completion_finalized"] is True
    assert runtime.adapter_state["terminal_transition_kind"] == (
        "completion_committed"
    )
    assert len(
        [
            event
            for event in store.events()
            if event.event_type == "task_completion_committed"
        ]
    ) == 1
    assert kernel.reject_after_completion_verification(
        Diagnostic("STALE_FAILURE", "old completion failure")
    ) is False
    assert store.runtime("root/action").status is NodeStatus.SUCCEEDED


def test_terminal_root_cannot_be_masked_by_pause() -> None:
    kernel, store = _physical_kernel(ImmediateSuccessRuntime())
    assert kernel.run(max_steps=20) is NodeStatus.SUCCEEDED

    kernel.pause("too late")
    tick = kernel.tick()

    assert kernel.paused is False
    assert tick.status == "SUCCEEDED"
    assert tick.terminal is True
    assert not any(
        event.event_type == "paused"
        for event in store.events()
    )
