from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import asdict, fields, is_dataclass, replace
from enum import Enum
from time import time
from typing import Any, Mapping

from task_recursive_tree.runtime.effects import (
    EffectRunner,
    PhysicalEffect,
    PhysicalEffectError,
    SynchronousEffectRunner,
)
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.contracts import (
    KernelServices,
    PhysicalActionOutcome,
    PhysicalExecutionState,
    SemanticCheck,
    SemanticState,
    normalize_cancellation_outcome,
)
from task_recursive_tree.task.model import (
    ControlKind,
    Diagnostic,
    EdgeKind,
    ExecutionPolicy,
    ExecutionFrame,
    FailureResolutionKind,
    FramePhase,
    GraphDelta,
    KernelLimits,
    NodeOutcome,
    NodeStatus,
    OperationKind,
    TaskNodeRuntime,
    TickResult,
)
from task_recursive_tree.task.store import TaskTreeStore


class KernelConfigurationError(RuntimeError):
    pass


class TaskTreeKernel:
    """The sole tree writer and deterministic stack-based interpreter."""

    def __init__(
        self,
        *,
        store: TaskTreeStore,
        services: KernelServices,
        decomposers: dict[str, object],
        system_operations: dict[str, object],
        physical_skills: dict[str, object],
        repair_resolver: object,
        limits: KernelLimits | None = None,
        effect_runner: EffectRunner | None = None,
    ) -> None:
        self._store = store
        self._services = services
        self._decomposers = decomposers
        self._system_operations = system_operations
        self._physical_skills = physical_skills
        self._repair_resolver = repair_resolver
        self._limits = limits or KernelLimits()
        self._effect_runner = effect_runner or SynchronousEffectRunner()
        self._writer = self._store.claim_writer()
        self._task_id: str | None = None
        self._paused = False
        self._pause_reason: str | None = None
        self._ticks = 0
        self._diagnostics: list[dict[str, Any]] = []

    @property
    def store(self) -> TaskTreeStore:
        return self._store

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pause_reason(self) -> str | None:
        return self._pause_reason

    @property
    def ticks(self) -> int:
        return self._ticks

    @property
    def diagnostics(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._diagnostics)

    @property
    def limits(self) -> KernelLimits:
        return self._limits

    def initialize(self, definition: TaskTreeDefinition) -> None:
        if self._store.root_id is not None:
            raise KernelConfigurationError("TaskTreeKernel is already initialized")
        self._task_id = definition.task_id or definition.root_id
        self._store.apply_delta(
            definition.delta, writer=self._writer
        )

    def run(self, *, max_steps: int = 10000) -> NodeStatus:
        root_id = self._require_root()
        for _ in range(max_steps):
            root_status = self._store.runtime(root_id).status
            if root_status.terminal:
                return root_status
            tick = self.tick()
            if tick.status == "PAUSED":
                return self._store.runtime(root_id).status
        self._store.append_event(
            "step_budget_exhausted",
            root_id,
            {"max_steps": max_steps},
            writer=self._writer,
        )
        return self._store.runtime(root_id).status

    def tick(self) -> TickResult:
        root_id = self._require_root()
        root_runtime = self._store.runtime(root_id)
        if root_runtime.status.terminal:
            return self._terminal_tick(root_id, root_runtime)
        if self._paused:
            node_id, phase = self._active_location(root_id)
            return TickResult(
                status="PAUSED",
                node_id=node_id,
                phase=phase,
                changed=False,
                terminal=False,
                message=self._pause_reason or "paused",
            )

        changed = self.step()
        self._ticks += 1
        root_runtime = self._store.runtime(root_id)
        node_id, phase = self._active_location(root_id)
        if self._paused:
            return TickResult(
                status="PAUSED",
                node_id=node_id,
                phase=phase,
                changed=changed,
                terminal=False,
                message=self._pause_reason or "paused",
            )
        if root_runtime.status.terminal:
            return self._terminal_tick(
                root_id, root_runtime, changed=changed
            )
        return TickResult(
            status=self._running_tick_status(phase),
            node_id=node_id,
            phase=phase,
            changed=changed,
            terminal=False,
            message="task tree advanced" if changed else "no change",
        )

    def pause(self, reason: str = "paused") -> None:
        root_id = self._require_root()
        if self._store.runtime(root_id).status.terminal:
            return
        self._paused = True
        self._pause_reason = str(reason)
        self._store.append_event(
            "paused",
            self._active_location(root_id)[0],
            {"reason": self._pause_reason},
            writer=self._writer,
        )

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        self._pause_reason = None
        self._store.append_event(
            "resumed",
            self._active_location(self._require_root())[0],
            {},
            writer=self._writer,
        )

    def cancel(self, reason: str = "cancelled") -> TickResult:
        root_id = self._require_root()
        root_runtime = self._store.runtime(root_id)
        if root_runtime.status.terminal:
            return self._terminal_tick(root_id, root_runtime)
        diagnostic = Diagnostic(
            "TREE_CANCELLED",
            str(reason),
            details={"reason": str(reason)},
            retryable=False,
        )
        with self._store.mutation(writer=self._writer):
            for node_id, runtime in self._store.runtimes().items():
                if runtime.status.terminal:
                    continue
                state = dict(runtime.adapter_state)
                state.setdefault(
                    "initial_terminal_status",
                    NodeStatus.CANCELLED.value,
                )
                self._store.set_runtime(
                    node_id,
                    runtime.evolve(
                        status=NodeStatus.CANCELLED,
                        phase="terminal",
                        last_diagnostic=diagnostic,
                        finished_at=time(),
                        adapter_state=state,
                    ),
                    writer=self._writer,
                )
            while self._store.stack():
                self._store.pop_frame(
                    self._store.stack()[-1].node_id,
                    writer=self._writer,
                )
            self._store.append_event(
                "tree_cancelled",
                root_id,
                {"reason": str(reason)},
                writer=self._writer,
            )
        self._paused = False
        self._pause_reason = None
        self._record_diagnostic(root_id, diagnostic)
        return TickResult(
            status="CANCELLED",
            node_id=root_id,
            phase="terminal",
            changed=True,
            terminal=True,
            message=str(reason),
            diagnostic_id=diagnostic.code,
        )

    def resume_after_external_recovery(
        self, recovery_ref: str | None = None
    ) -> None:
        stack = self._store.stack()
        if stack:
            frame = stack[-1]
            runtime = self._store.runtime(frame.node_id)
            state = dict(runtime.adapter_state)
            state.pop("active_request_id", None)
            state.pop("transaction_id", None)
            state.pop("outcome_unknown", None)
            recoveries = list(state.get("external_recoveries", ()))
            recoveries.append(
                {
                    "recovery_ref": recovery_ref,
                    "resumed_at": time(),
                }
            )
            state["external_recoveries"] = tuple(recoveries)
            self._store.set_runtime(
                frame.node_id,
                runtime.evolve(
                    status=NodeStatus.RUNNING,
                    phase=FramePhase.GOAL_CHECK.value,
                    last_diagnostic=None,
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.replace_top_frame(
                frame.evolve(phase=FramePhase.GOAL_CHECK),
                writer=self._writer,
            )
        self.resume()

    def promote_after_external_goal_verification(
        self,
        audit: Mapping[str, Any],
        *,
        recovery_ref: str | None = None,
        completion_receipt: Mapping[str, Any] | None = None,
    ) -> bool:
        """Promote a terminal root after independent live-goal verification."""
        if audit.get("verified") is not True:
            return False
        root_id = self._require_root()
        promoted_at = time()
        audit_payload = _jsonable(audit)
        receipt_payload = (
            _jsonable(completion_receipt)
            if completion_receipt is not None
            else None
        )
        with self._store.mutation(writer=self._writer):
            runtime = self._store.runtime(root_id)
            if not runtime.status.terminal:
                return False
            state = dict(runtime.adapter_state)
            if (
                runtime.status is NodeStatus.SUCCEEDED
                and state.get("completion_finalized") is True
            ):
                return True
            previous_diagnostic = (
                _jsonable(runtime.last_diagnostic)
                if runtime.last_diagnostic is not None
                else None
            )
            record = {
                "verified": True,
                "recovery_ref": recovery_ref,
                "promoted_at": promoted_at,
                "previous_status": runtime.status.value,
                "previous_diagnostic": previous_diagnostic,
                "completion_audit": audit_payload,
                "completion_receipt": receipt_payload,
            }
            history = list(
                state.get("external_goal_verification_promotions", ())
            )
            history.append(record)
            state["external_goal_verification_promotions"] = tuple(history)
            state["last_external_goal_verification"] = record
            state["completion_audit"] = audit_payload
            if completion_receipt is not None:
                state["completion_receipt"] = receipt_payload
            state["task_completion_source"] = (
                "external_goal_verification"
            )
            state.setdefault(
                "initial_terminal_status",
                runtime.status.value,
            )
            state["terminal_transition_kind"] = (
                "external_goal_verified"
            )
            state["completion_finalized"] = True
            self._store.set_runtime(
                root_id,
                runtime.evolve(
                    status=NodeStatus.SUCCEEDED,
                    phase="terminal",
                    last_diagnostic=None,
                    finished_at=(
                        runtime.finished_at
                        if runtime.status is NodeStatus.SUCCEEDED
                        else promoted_at
                    ),
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.append_event(
                "external_goal_verification_promoted",
                root_id,
                record,
                writer=self._writer,
            )
        self._paused = False
        self._pause_reason = None
        return True

    def record_task_completion_commit(
        self,
        audit: Mapping[str, Any],
        *,
        completion_receipt: Mapping[str, Any] | None = None,
        source: str = "runtime_completion_commit",
    ) -> bool:
        """Attach the authoritative task commit to a successful root."""
        if audit.get("verified") is not True:
            return False
        root_id = self._require_root()
        committed_at = time()
        audit_payload = _jsonable(audit)
        receipt_payload = (
            _jsonable(completion_receipt)
            if completion_receipt is not None
            else None
        )
        record = {
            "verified": True,
            "source": str(source),
            "committed_at": committed_at,
            "completion_audit": audit_payload,
            "completion_receipt": receipt_payload,
        }
        with self._store.mutation(writer=self._writer):
            runtime = self._store.runtime(root_id)
            if runtime.status is not NodeStatus.SUCCEEDED:
                return False
            state = dict(runtime.adapter_state)
            if state.get("completion_finalized") is True:
                return (
                    state.get("completion_audit") == audit_payload
                    and (
                        completion_receipt is None
                        or state.get("completion_receipt")
                        == receipt_payload
                    )
                )
            history = list(state.get("task_completion_commits", ()))
            history.append(record)
            state["task_completion_commits"] = tuple(history)
            state["last_task_completion_commit"] = record
            state["completion_audit"] = audit_payload
            if completion_receipt is not None:
                state["completion_receipt"] = receipt_payload
            state["task_completion_source"] = str(source)
            state.setdefault(
                "initial_terminal_status",
                runtime.status.value,
            )
            state["terminal_transition_kind"] = "completion_committed"
            state["completion_finalized"] = True
            self._store.set_runtime(
                root_id,
                runtime.evolve(adapter_state=state),
                writer=self._writer,
            )
            self._store.append_event(
                "task_completion_committed",
                root_id,
                record,
                writer=self._writer,
            )
        return True

    def reject_after_completion_verification(
        self,
        diagnostic: Diagnostic,
        *,
        audit: Mapping[str, Any] | None = None,
    ) -> bool:
        """Fail closed when a successful root cannot commit its task."""
        root_id = self._require_root()
        rejected_at = time()
        record = {
            "rejected_at": rejected_at,
            "diagnostic": _jsonable(diagnostic),
            "completion_audit": (
                _jsonable(audit) if audit is not None else None
            ),
        }
        with self._store.mutation(writer=self._writer):
            runtime = self._store.runtime(root_id)
            if (
                runtime.status is not NodeStatus.SUCCEEDED
                or runtime.adapter_state.get("completion_finalized") is True
            ):
                return False
            state = dict(runtime.adapter_state)
            state["last_task_completion_failure"] = record
            if audit is not None:
                state["completion_audit"] = _jsonable(audit)
            state.setdefault(
                "initial_terminal_status",
                runtime.status.value,
            )
            state["terminal_transition_kind"] = "completion_rejected"
            state["completion_finalized"] = True
            self._store.set_runtime(
                root_id,
                runtime.evolve(
                    status=NodeStatus.BLOCKED,
                    phase="terminal",
                    last_diagnostic=diagnostic,
                    finished_at=rejected_at,
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.append_event(
                "task_completion_rejected",
                root_id,
                record,
                writer=self._writer,
            )
        self._record_diagnostic(root_id, diagnostic)
        return True

    def snapshot(self) -> dict[str, Any]:
        root_id = self._require_root()
        return {
            "schema": "task_recursive_tree/kernel/1.0",
            "task_id": self._task_id,
            "tree": {
                "root_id": root_id,
                "nodes": {
                    node_id: {
                        "spec": _jsonable(spec),
                        "runtime": _jsonable(
                            self._store.runtime(node_id)
                        ),
                    }
                    for node_id, spec in self._store.specs().items()
                },
                "edges": _jsonable(self._store.edges()),
                "execution_stack": _jsonable(self._store.stack()),
                "events": _jsonable(self._store.events()),
            },
            "budget": {
                "ticks": self._ticks,
                "max_node_attempts": self._limits.max_node_attempts,
                "max_reconciliations": self._limits.max_reconciliations,
            },
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "diagnostics": _jsonable(self._diagnostics),
            "terminal_status": (
                self._external_status(
                    self._store.runtime(root_id).status
                )
                if self._store.runtime(root_id).status.terminal
                else None
            ),
        }

    def step(self) -> bool:
        if self._paused:
            return False
        root_id = self._require_root()
        if not self._store.stack():
            if self._store.runtime(root_id).status.terminal:
                return False
            self._store.push_frame(
                ExecutionFrame(root_id), writer=self._writer
            )
            return True

        frame = self._store.stack()[-1]
        spec = self._store.spec(frame.node_id)
        runtime = self._store.runtime(frame.node_id)

        if runtime.status.terminal:
            self._store.pop_frame(
                frame.node_id, writer=self._writer
            )
            return True
        if frame.phase is FramePhase.ENTER:
            return self._enter(frame, spec, runtime)
        if frame.phase is FramePhase.GOAL_CHECK:
            return self._check_goal(frame, spec, runtime)
        if frame.phase is FramePhase.PRECONDITIONS:
            return self._check_preconditions(frame, spec, runtime)
        if frame.phase is FramePhase.EXECUTE:
            return self._execute(frame, spec, runtime)
        if frame.phase is FramePhase.VERIFY:
            return self._verify(frame, spec, runtime)
        if frame.phase is FramePhase.CHILDREN:
            return self._advance_children(frame, spec, runtime)
        if frame.phase is FramePhase.REPAIR:
            return self._advance_repair(frame, spec, runtime)
        if frame.phase is FramePhase.RECONCILIATION:
            return self._advance_reconciliation(frame, spec, runtime)
        raise KernelConfigurationError(f"Unknown frame phase: {frame.phase}")

    def _enter(self, frame, spec, runtime) -> bool:
        if runtime.status is NodeStatus.PENDING:
            runtime = runtime.evolve(
                status=NodeStatus.RUNNING,
                phase=FramePhase.GOAL_CHECK.value,
                attempts=runtime.attempts + 1,
                started_at=runtime.started_at or time(),
                finished_at=None,
            )
            self._store.set_runtime(
                spec.node_id, runtime, writer=self._writer
            )
        self._store.replace_top_frame(
            frame.evolve(phase=FramePhase.GOAL_CHECK),
            writer=self._writer,
        )
        return True

    def _check_goal(self, frame, spec, runtime) -> bool:
        if spec.execution_policy is ExecutionPolicy.REQUIRE_EXECUTION:
            self._set_phase(
                frame, runtime, FramePhase.PRECONDITIONS
            )
            return True
        check = self._semantic_evaluation("goal", spec, runtime)
        if check is not None:
            if check.state is SemanticState.SATISFIED:
                obligation_failure = self._obligation_failure(
                    spec,
                    runtime,
                )
                if obligation_failure is not None:
                    self._handle_failure(
                        frame,
                        spec,
                        runtime,
                        obligation_failure,
                    )
                    return True
                self._succeed(
                    spec.node_id,
                    runtime,
                    NodeOutcome.success(result=check.evidence),
                )
                return True
            if check.state is SemanticState.UNKNOWN:
                self._handle_failure(
                    frame,
                    spec,
                    runtime,
                    self._semantic_failure(
                        check,
                        code="GOAL_STATE_UNKNOWN",
                        message=f"Goal state is unknown for {spec.node_id}",
                    ),
                )
                return True
        self._set_phase(
            frame, runtime, FramePhase.PRECONDITIONS
        )
        return True

    def _check_preconditions(self, frame, spec, runtime) -> bool:
        semantic_check = self._semantic_evaluation(
            "preconditions", spec, runtime
        )
        if (
            semantic_check is not None
            and semantic_check.state is not SemanticState.SATISFIED
        ):
            self._handle_failure(
                frame,
                spec,
                runtime,
                self._semantic_failure(
                    semantic_check,
                    code="PRECONDITION_FAILED",
                    message=f"Preconditions failed for {spec.node_id}",
                ),
            )
            return True

        precondition_failure = self._evaluate_conditions(
            spec.preconditions,
            code="PRECONDITION_FAILED",
            message_prefix="Precondition",
        )
        if precondition_failure is not None:
            self._handle_failure(frame, spec, runtime, precondition_failure)
            return True

        if spec.operation_kind is OperationKind.DECOMPOSER:
            existing_children = self._store.children(
                spec.node_id, kind=EdgeKind.CHILD
            )
            if not runtime.expanded and existing_children:
                runtime = runtime.evolve(
                    expanded=True,
                    phase=FramePhase.CHILDREN.value,
                )
                self._store.set_runtime(
                    spec.node_id, runtime, writer=self._writer
                )
            if not runtime.expanded:
                decomposer = self._handler(
                    self._decomposers, spec.task_type
                )
                if decomposer is None:
                    raise KernelConfigurationError(
                        f"No decomposer registered for {spec.task_type}"
                    )
                delta = decomposer.expand(
                    spec, self._services.decomposition
                )
                self._validate_decomposition_delta(spec.node_id, delta)
                with self._store.mutation(writer=self._writer):
                    self._store.apply_delta(delta, writer=self._writer)
                    runtime = self._store.runtime(spec.node_id).evolve(
                        expanded=True,
                        phase=FramePhase.CHILDREN.value,
                    )
                    self._store.set_runtime(
                        spec.node_id, runtime, writer=self._writer
                    )
                    self._store.replace_top_frame(
                        frame.evolve(
                            phase=FramePhase.CHILDREN,
                            next_child_index=0,
                            active_child_id=None,
                        ),
                        writer=self._writer,
                    )
                return True
            self._store.replace_top_frame(
                frame.evolve(
                    phase=FramePhase.CHILDREN,
                    next_child_index=0,
                    active_child_id=None,
                ),
                writer=self._writer,
            )
            self._store.set_runtime(
                spec.node_id,
                runtime.evolve(phase=FramePhase.CHILDREN.value),
                writer=self._writer,
            )
            return True
        self._set_phase(frame, runtime, FramePhase.EXECUTE)
        return True

    def _execute(self, frame, spec, runtime) -> bool:
        outcome = self._execute_leaf(spec, runtime)
        runtime = self._store.runtime(spec.node_id)
        if isinstance(outcome, PhysicalActionOutcome):
            runtime = self._record_physical_outcome(
                spec.node_id, runtime, outcome
            )
            if outcome.state is PhysicalExecutionState.CANCELLED:
                state = dict(runtime.adapter_state)
                state["last_cancelled_request_id"] = state.pop(
                    "active_request_id", None
                )
                state["last_cancelled_request_hash"] = state.pop(
                    "active_request_hash", None
                )
                state.pop("transaction_id", None)
                self._store.set_runtime(
                    spec.node_id,
                    runtime.evolve(
                        status=NodeStatus.RUNNING,
                        phase=FramePhase.GOAL_CHECK.value,
                        last_diagnostic=None,
                        adapter_state=state,
                    ),
                    writer=self._writer,
                )
                self._store.replace_top_frame(
                    frame.evolve(phase=FramePhase.GOAL_CHECK),
                    writer=self._writer,
                )
                self.pause(
                    "operator stopped the current physical action"
                )
                clear_cancel = getattr(
                    self._services.runtime,
                    "clear_cancel_request",
                    None,
                )
                if callable(clear_cancel):
                    clear_cancel()
                return True
            if outcome.state is PhysicalExecutionState.OUTCOME_UNKNOWN:
                diagnostic = self._canonical_outcome_unknown_diagnostic(
                    outcome
                )
                state = dict(runtime.adapter_state)
                state["outcome_unknown"] = True
                self._store.set_runtime(
                    spec.node_id,
                    runtime.evolve(
                        phase=FramePhase.RECONCILIATION.value,
                        last_diagnostic=diagnostic,
                        adapter_state=state,
                    ),
                    writer=self._writer,
                )
                self._store.replace_top_frame(
                    frame.evolve(
                        phase=FramePhase.RECONCILIATION,
                        original_diagnostic=diagnostic,
                    ),
                    writer=self._writer,
                )
                self._handle_failure(
                    self._store.stack()[-1],
                    spec,
                    self._store.runtime(spec.node_id),
                    NodeOutcome(
                        succeeded=False,
                        diagnostic=diagnostic,
                        result=outcome.result,
                    ),
                )
                return True
            if outcome.state is PhysicalExecutionState.FAILED:
                diagnostic = outcome.diagnostic or Diagnostic(
                    "PHYSICAL_EXECUTION_FAILED",
                    "Physical action was not confirmed",
                    details=dict(outcome.result),
                    retryable=False,
                )
                self._handle_failure(
                    frame,
                    spec,
                    runtime,
                    NodeOutcome(
                        succeeded=False,
                        diagnostic=diagnostic,
                        result=outcome.result,
                    ),
                )
                return True
            outcome = NodeOutcome.success(
                *outcome.artifact_refs,
                result=outcome.result,
            )

        if outcome.succeeded:
            state = dict(runtime.adapter_state)
            if outcome.result:
                state["last_result"] = dict(outcome.result)
            self._store.set_runtime(
                spec.node_id,
                runtime.evolve(
                    phase=FramePhase.VERIFY.value,
                    output_artifacts=outcome.artifact_refs,
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.replace_top_frame(
                frame.evolve(phase=FramePhase.VERIFY),
                writer=self._writer,
            )
            return True
        self._handle_failure(frame, spec, runtime, outcome)
        return True

    def _verify(self, frame, spec, runtime) -> bool:
        semantic_check = self._semantic_evaluation(
            "postconditions", spec, runtime
        )
        if (
            semantic_check is not None
            and semantic_check.state is not SemanticState.SATISFIED
        ):
            self._handle_failure(
                frame,
                spec,
                runtime,
                self._semantic_failure(
                    semantic_check,
                    code="POSTCONDITION_FAILED",
                    message=f"Postconditions failed for {spec.node_id}",
                ),
            )
            return True
        postcondition_failure = self._evaluate_conditions(
            spec.postconditions,
            code="POSTCONDITION_FAILED",
            message_prefix="Postcondition",
        )
        if postcondition_failure is not None:
            self._handle_failure(
                frame, spec, runtime, postcondition_failure
            )
            return True
        obligation_failure = self._obligation_failure(spec, runtime)
        if obligation_failure is not None:
            self._handle_failure(
                frame,
                spec,
                runtime,
                obligation_failure,
            )
            return True
        self._succeed(
            spec.node_id,
            runtime,
            NodeOutcome.success(*runtime.output_artifacts),
        )
        return True

    def _advance_children(self, frame, spec, runtime) -> bool:
        children = self._store.children(spec.node_id, kind=EdgeKind.CHILD)
        if frame.active_child_id is not None:
            child_runtime = self._store.runtime(frame.active_child_id)
            if not child_runtime.status.terminal:
                self._store.push_frame(
                    ExecutionFrame(frame.active_child_id),
                    writer=self._writer,
                )
                return True
            if child_runtime.status is NodeStatus.SUCCEEDED:
                runtime = self._inherit_child_obligations(
                    spec,
                    runtime,
                    self._store.spec(frame.active_child_id),
                    child_runtime,
                )
            if (
                spec.control_kind is ControlKind.SELECTOR
                and child_runtime.status is NodeStatus.SUCCEEDED
            ):
                self._set_phase(frame, runtime, FramePhase.VERIFY)
                return True
            if child_runtime.status is NodeStatus.SUCCEEDED:
                self._store.replace_top_frame(
                    frame.evolve(active_child_id=None),
                    writer=self._writer,
                )
                return True
            if spec.control_kind is ControlKind.SELECTOR:
                self._store.replace_top_frame(
                    frame.evolve(
                        active_child_id=None,
                        last_child_diagnostic=(
                            child_runtime.last_diagnostic
                        ),
                    ),
                    writer=self._writer,
                )
                return True
            self._fail_composite(
                spec.node_id,
                runtime,
                child_runtime.last_diagnostic,
            )
            return True

        if frame.next_child_index < len(children):
            child_id = children[frame.next_child_index]
            self._store.replace_top_frame(
                frame.evolve(
                    next_child_index=frame.next_child_index + 1,
                    active_child_id=child_id,
                ),
                writer=self._writer,
            )
            self._store.push_frame(
                ExecutionFrame(child_id), writer=self._writer
            )
            return True

        if (
            spec.control_kind is ControlKind.SELECTOR
        ):
            diagnostic = frame.last_child_diagnostic or Diagnostic(
                "SELECTOR_EXHAUSTED",
                f"Selector {spec.node_id} has no successful child",
            )
            self._fail(
                spec.node_id, runtime, diagnostic
            )
            return True
        self._set_phase(frame, runtime, FramePhase.VERIFY)
        return True

    def _advance_repair(self, frame, spec, runtime) -> bool:
        repair_id = frame.active_child_id
        if repair_id is None:
            raise KernelConfigurationError("Repair frame has no repair child")
        repair_runtime = self._store.runtime(repair_id)
        if not repair_runtime.status.terminal:
            self._store.push_frame(
                ExecutionFrame(repair_id), writer=self._writer
            )
            return True
        if repair_runtime.status is NodeStatus.SUCCEEDED:
            runtime = self._inherit_child_obligations(
                spec,
                runtime,
                self._store.spec(repair_id),
                repair_runtime,
            )
            state = dict(runtime.adapter_state)
            dispatch_epoch_event = None
            if spec.operation_kind is OperationKind.PHYSICAL:
                completed_attempts = self._dispatch_attempts_in_epoch(
                    state
                )
                dispatch_epoch = int(state.get("dispatch_epoch", 0)) + 1
                state["dispatch_attempts_in_epoch"] = 0
                state["dispatch_epoch"] = dispatch_epoch
                state["last_repair_dispatch_attempts"] = completed_attempts
                state["last_repair_node_id"] = repair_id
                dispatch_epoch_event = {
                    "repair_node_id": repair_id,
                    "completed_dispatch_attempts": completed_attempts,
                    "total_dispatch_attempts": int(
                        state.get("dispatch_attempt", 0)
                    ),
                    "dispatch_epoch": dispatch_epoch,
                }
            refreshed = runtime.evolve(
                status=NodeStatus.PENDING,
                phase=FramePhase.ENTER.value,
                last_diagnostic=None,
                adapter_state=state,
            )
            with self._store.mutation(writer=self._writer):
                self._store.set_runtime(
                    spec.node_id, refreshed, writer=self._writer
                )
                if dispatch_epoch_event is not None:
                    self._store.append_event(
                        "physical_dispatch_epoch_reset",
                        spec.node_id,
                        dispatch_epoch_event,
                        writer=self._writer,
                    )
                self._store.replace_top_frame(
                    frame.evolve(
                        phase=FramePhase.ENTER,
                        active_child_id=None,
                        original_diagnostic=None,
                    ),
                    writer=self._writer,
                )
            return True
        diagnostic = (
            repair_runtime.last_diagnostic or frame.original_diagnostic
        )
        self._fail(spec.node_id, runtime, diagnostic)
        return True

    def _advance_reconciliation(self, frame, spec, runtime) -> bool:
        reconciliation_id = frame.active_child_id
        if reconciliation_id is None:
            raise KernelConfigurationError(
                "Reconciliation frame has no reconciliation child"
            )
        reconciliation_runtime = self._store.runtime(reconciliation_id)
        if not reconciliation_runtime.status.terminal:
            self._store.push_frame(
                ExecutionFrame(reconciliation_id),
                writer=self._writer,
            )
            return True

        reconciliation_result = dict(
            reconciliation_runtime.adapter_state.get("last_result", {})
        )
        state = dict(runtime.adapter_state)
        state.pop("active_reconciliation_id", None)
        state["last_reconciliation_id"] = reconciliation_id
        state["reconciliation_result"] = reconciliation_result
        refreshed = runtime.evolve(adapter_state=state)
        self._store.set_runtime(
            spec.node_id,
            refreshed,
            writer=self._writer,
        )

        if reconciliation_runtime.status is not NodeStatus.SUCCEEDED:
            diagnostic = (
                reconciliation_runtime.last_diagnostic
                or runtime.last_diagnostic
                or frame.original_diagnostic
            )
            self._fail(spec.node_id, refreshed, diagnostic)
            return True

        semantic_check = self._semantic_evaluation(
            "postconditions",
            spec,
            refreshed,
        )
        if semantic_check is None:
            reconciled_state = self._reconciled_result_state(
                reconciliation_result
            )
        else:
            reconciled_state = semantic_check.state

        if reconciled_state is SemanticState.UNKNOWN:
            diagnostic = (
                semantic_check.diagnostic
                if semantic_check is not None
                else None
            ) or Diagnostic(
                "OUTCOME_UNKNOWN",
                "Reconciliation did not establish the physical action result",
                details={
                    "reconciliation_node_id": reconciliation_id,
                    "reconciliation_result": reconciliation_result,
                    "original_result": state.get("last_result"),
                },
                retryable=False,
            )
            self._fail(spec.node_id, refreshed, diagnostic)
            return True

        if reconciled_state is SemanticState.UNSATISFIED:
            semantic_evidence = (
                dict(semantic_check.evidence)
                if semantic_check is not None
                else {}
            )
            authorization = self._reconciled_resubmission_authorization(
                reconciliation_result,
                expected_transaction_id=state.get("transaction_id"),
                original_result=state.get("last_result"),
                semantic_evidence=semantic_evidence,
            )
            dispatch_attempt = int(state.get("dispatch_attempt", 0))
            dispatch_attempts_in_epoch = (
                self._dispatch_attempts_in_epoch(state)
            )
            previous_request_id = state.get("active_request_id")
            state = self._resolve_reconciled_dispatch(
                state,
                reconciliation_id=reconciliation_id,
                reconciliation_status="refuted",
                resolved_state="reconciled_refuted",
            )
            refreshed = refreshed.evolve(adapter_state=state)
            if (
                authorization["allowed"]
                and dispatch_attempts_in_epoch
                < self._limits.max_node_attempts
            ):
                state["reconciled_resubmissions"] = int(
                    state.get("reconciled_resubmissions", 0)
                ) + 1
                state["resubmission_authorization"] = authorization
                state["last_result"] = dict(
                    authorization["action_result"]
                )
                state.pop("transaction_id", None)
                evidence_revision = authorization.get(
                    "evidence_revision"
                )
                evidence_revisions = list(
                    state.get(
                        "reconciliation_evidence_revisions",
                        (),
                    )
                )
                if evidence_revision:
                    evidence_revisions.append(str(evidence_revision))
                state["reconciliation_evidence_revisions"] = tuple(
                    dict.fromkeys(evidence_revisions)
                )
                resumed = refreshed.evolve(
                    status=NodeStatus.RUNNING,
                    phase=FramePhase.GOAL_CHECK.value,
                    last_diagnostic=None,
                    finished_at=None,
                    adapter_state=state,
                )
                next_attempt = dispatch_attempt + 1
                next_request_id = self._physical_request_id(
                    spec.node_id, next_attempt
                )
                with self._store.mutation(writer=self._writer):
                    self._store.set_runtime(
                        spec.node_id,
                        resumed,
                        writer=self._writer,
                    )
                    self._store.append_event(
                        "reconciled_resubmission_scheduled",
                        spec.node_id,
                        {
                            "entry": reconciliation_id,
                            "previous_request_id": previous_request_id,
                            "next_request_id": next_request_id,
                            "attempt": next_attempt,
                            "evidence_revision": evidence_revision,
                        },
                        writer=self._writer,
                    )
                    self._store.replace_top_frame(
                        frame.evolve(
                            phase=FramePhase.GOAL_CHECK,
                            active_child_id=None,
                            original_diagnostic=None,
                        ),
                        writer=self._writer,
                    )
                return True

            if (
                authorization["allowed"]
                and dispatch_attempts_in_epoch
                >= self._limits.max_node_attempts
            ):
                diagnostic = Diagnostic(
                    "NODE_ATTEMPTS_EXHAUSTED",
                    "Reconciliation authorized a new request, but the "
                    "physical dispatch budget is exhausted",
                    details={
                        "max_node_attempts": (
                            self._limits.max_node_attempts
                        ),
                        "dispatch_attempts": dispatch_attempt,
                        "dispatch_attempts_in_epoch": (
                            dispatch_attempts_in_epoch
                        ),
                        "dispatch_epoch": int(
                            state.get("dispatch_epoch", 0)
                        ),
                        "reconciliation_node_id": reconciliation_id,
                        "authorization": authorization,
                        "semantic_evidence": semantic_evidence,
                    },
                    retryable=False,
                )
            else:
                diagnostic = Diagnostic(
                    "RECONCILIATION_REFUTED",
                    "Reconciliation confirmed that the physical goal is "
                    "false and did not authorize resubmission",
                    details={
                        "reconciliation_node_id": reconciliation_id,
                        "reconciliation_result": reconciliation_result,
                        "semantic_evidence": semantic_evidence,
                        "original_result": state.get("last_result"),
                        "authorization": authorization,
                    },
                    retryable=False,
                )
            self._fail(spec.node_id, refreshed, diagnostic)
            return True

        postcondition_failure = self._evaluate_conditions(
            spec.postconditions,
            code="POSTCONDITION_FAILED",
            message_prefix="Postcondition",
        )
        if postcondition_failure is not None:
            self._fail(
                spec.node_id,
                refreshed,
                postcondition_failure.diagnostic,
            )
            return True

        result = dict(state.get("last_result") or {})
        result["reconciliation"] = {
            "node_id": reconciliation_id,
            "status": "confirmed",
            "result": reconciliation_result,
            "semantic_evidence": (
                dict(semantic_check.evidence)
                if semantic_check is not None
                else {}
            ),
        }
        state = self._resolve_reconciled_dispatch(
            state,
            reconciliation_id=reconciliation_id,
            reconciliation_status="confirmed",
            resolved_state="reconciled_succeeded",
        )
        refreshed = refreshed.evolve(adapter_state=state)
        self._store.append_event(
            "reconciliation_closed",
            spec.node_id,
            {
                "entry": reconciliation_id,
                "transaction_id": state.get("transaction_id"),
            },
            writer=self._writer,
        )
        self._succeed(
            spec.node_id,
            refreshed,
            NodeOutcome.success(
                *refreshed.output_artifacts,
                result=result,
            ),
        )
        return True

    def _execute_leaf(
        self, spec, runtime
    ) -> NodeOutcome | PhysicalActionOutcome:
        if spec.operation_kind is OperationKind.SYSTEM:
            operation = self._handler(
                self._system_operations, spec.task_type
            )
            if operation is None:
                raise KernelConfigurationError(
                    f"No system operation registered for {spec.task_type}"
                )
            return operation.run(spec, self._services.system)
        if spec.operation_kind is OperationKind.PHYSICAL:
            skill = self._handler(
                self._physical_skills, spec.task_type
            )
            if skill is None:
                raise KernelConfigurationError(
                    f"No physical skill registered for {spec.task_type}"
                )
            state = dict(runtime.adapter_state)
            dispatch_attempt = int(
                state.get("dispatch_attempt", 0)
            ) + 1
            dispatch_attempts_in_epoch = (
                self._dispatch_attempts_in_epoch(state) + 1
            )
            if (
                dispatch_attempts_in_epoch
                > self._limits.max_node_attempts
            ):
                return PhysicalActionOutcome(
                    state=PhysicalExecutionState.FAILED,
                    result={
                        "termination": "failed",
                        "failure_code": "NODE_ATTEMPTS_EXHAUSTED",
                        "dispatch_attempts": dispatch_attempt - 1,
                        "dispatch_attempts_in_epoch": (
                            dispatch_attempts_in_epoch - 1
                        ),
                        "dispatch_epoch": int(
                            state.get("dispatch_epoch", 0)
                        ),
                        "max_node_attempts": (
                            self._limits.max_node_attempts
                        ),
                    },
                    diagnostic=Diagnostic(
                        "NODE_ATTEMPTS_EXHAUSTED",
                        f"Physical dispatch limit "
                        f"{self._limits.max_node_attempts} exhausted",
                        details={
                            "dispatch_attempts": dispatch_attempt - 1,
                            "dispatch_attempts_in_epoch": (
                                dispatch_attempts_in_epoch - 1
                            ),
                            "dispatch_epoch": int(
                                state.get("dispatch_epoch", 0)
                            ),
                            "max_node_attempts": (
                                self._limits.max_node_attempts
                            ),
                        },
                        retryable=False,
                    ),
                )
            task_id = self._task_id or self._require_root()
            request_id = self._physical_request_id(
                spec.node_id,
                dispatch_attempt,
            )
            skill_context = self._services.skill.for_dispatch(
                task_id=task_id,
                node_id=spec.node_id,
                attempt=dispatch_attempt,
                request_id=request_id,
            )
            try:
                request = skill.build_request(spec, skill_context)
                actual_request_id = getattr(
                    request, "request_id", request_id
                )
                if str(actual_request_id) != request_id:
                    raise KernelConfigurationError(
                        "Physical skills must use the kernel dispatch "
                        f"request ID {request_id!r}"
                    )
                effect = PhysicalEffect.from_request(
                    request_id,
                    request,
                )
                attempt_ledger = [
                    dict(item)
                    for item in state.get("attempt_ledger", ())
                    if isinstance(item, Mapping)
                ]
                prior = next(
                    (
                        item
                        for item in attempt_ledger
                        if item.get("request_id") == request_id
                    ),
                    None,
                )
                if (
                    prior is not None
                    and prior.get("request_hash")
                    != effect.request_hash
                ):
                    raise KernelConfigurationError(
                        "Physical request ID was reused with a different "
                        f"payload: {request_id}"
                    )
                dispatch_record = {
                    "request_id": request_id,
                    "request_hash": effect.request_hash,
                    "attempt": dispatch_attempt,
                    "dispatch_epoch": int(
                        state.get("dispatch_epoch", 0)
                    ),
                    "state": "awaiting_physical",
                }
                if prior is None:
                    attempt_ledger.append(dispatch_record)
                else:
                    prior.update(dispatch_record)
                state.update(
                    {
                        "dispatch_attempt": dispatch_attempt,
                        "dispatch_attempts_in_epoch": (
                            dispatch_attempts_in_epoch
                        ),
                        "active_request_id": request_id,
                        "active_request_hash": effect.request_hash,
                        "dispatch_state": dispatch_record,
                        "attempt_ledger": tuple(attempt_ledger),
                    }
                )
                self._store.set_runtime(
                    spec.node_id,
                    runtime.evolve(
                        phase=FramePhase.EXECUTE.value,
                        adapter_state=state,
                    ),
                    writer=self._writer,
                )
            except Exception as exc:
                return self._physical_exception_outcome(
                    exc,
                    dispatch_invoked=False,
                )
            try:
                raw_result = self._effect_runner.run(
                    self._services.runtime,
                    effect,
                )
                return self._normalize_physical_outcome(raw_result)
            except Exception as exc:
                return self._physical_exception_outcome(
                    exc,
                    dispatch_invoked=True,
                )
        raise KernelConfigurationError(
            f"Cannot execute leaf with kind {spec.operation_kind}"
        )

    def _handle_failure(self, frame, spec, runtime, outcome) -> None:
        diagnostic = outcome.diagnostic
        if diagnostic is None:
            self._fail(spec.node_id, runtime, None)
            return
        state = dict(runtime.adapter_state)
        requires_reconciliation = (
            self._diagnostic_requires_reconciliation(diagnostic)
        )
        reconciliation_attempts = int(
            state.get("reconciliation_attempts", 0)
        )
        can_reconcile = (
            requires_reconciliation
            and reconciliation_attempts < self._limits.max_reconciliations
        )
        can_repair = runtime.repairs < spec.max_repairs
        proposal = None
        if can_repair or can_reconcile:
            resolution_index = (
                reconciliation_attempts
                if requires_reconciliation
                else runtime.repairs
            )
            proposal = self._repair_resolver.propose(
                spec,
                diagnostic,
                self._services.repair,
                resolution_index,
            )
        if proposal is not None:
            resolution_kind = getattr(
                proposal,
                "kind",
                FailureResolutionKind.REPAIR,
            )
            if (
                resolution_kind is FailureResolutionKind.RECONCILIATION
                and not can_reconcile
            ):
                proposal = None
            elif (
                resolution_kind is FailureResolutionKind.REPAIR
                and not can_repair
            ):
                proposal = None
        if proposal is None:
            if requires_reconciliation and not can_reconcile:
                exhausted = Diagnostic(
                    "RECONCILIATION_EXHAUSTED",
                    "Physical outcome reconciliation budget is exhausted",
                    details={
                        "reconciliation_attempts": reconciliation_attempts,
                        "max_reconciliations": (
                            self._limits.max_reconciliations
                        ),
                        "original_diagnostic": diagnostic.code,
                        "original_details": dict(diagnostic.details),
                    },
                    retryable=False,
                )
                self._fail(spec.node_id, runtime, exhausted)
                return
            if diagnostic.retryable and runtime.attempts < spec.max_attempts:
                self._store.set_runtime(
                    spec.node_id,
                    runtime.evolve(
                        status=NodeStatus.PENDING,
                        phase=FramePhase.ENTER.value,
                        last_diagnostic=diagnostic,
                    ),
                    writer=self._writer,
                )
                self._store.replace_top_frame(
                    frame.evolve(phase=FramePhase.ENTER),
                    writer=self._writer,
                )
                self._store.append_event(
                    "retry_scheduled",
                    spec.node_id,
                    {
                        "attempt": runtime.attempts + 1,
                        "diagnostic": diagnostic.code,
                    },
                    writer=self._writer,
                )
                return
            self._fail(spec.node_id, runtime, diagnostic)
            return
        repair_delta = self._assign_repair_mount_order(
            spec.node_id,
            proposal.entry_node_id,
            proposal.delta,
        )
        self._validate_repair_delta(
            spec.node_id,
            proposal.entry_node_id,
            repair_delta,
        )
        invalidated_artifact_refs = tuple(
            dict.fromkeys(
                str(value)
                for value in getattr(
                    proposal,
                    "invalidates_artifacts",
                    (),
                )
                if str(value)
            )
        )
        invalidate_artifacts = None
        if invalidated_artifact_refs:
            invalidate_artifacts = getattr(
                self._services.repair.artifacts,
                "invalidate",
                None,
            )
            if not callable(invalidate_artifacts):
                raise KernelConfigurationError(
                    "Repair proposal requires artifact invalidation, but "
                    "the artifact service does not implement invalidate()"
                )
        resolution_kind = getattr(
            proposal,
            "kind",
            FailureResolutionKind.REPAIR,
        )
        phase = (
            FramePhase.RECONCILIATION
            if resolution_kind is FailureResolutionKind.RECONCILIATION
            else FramePhase.REPAIR
        )
        event_type = (
            "reconciliation_mounted"
            if phase is FramePhase.RECONCILIATION
            else "repair_mounted"
        )
        if phase is FramePhase.RECONCILIATION:
            state["reconciliation_attempts"] = reconciliation_attempts + 1
            state["active_reconciliation_id"] = proposal.entry_node_id
        persistent_obligations = tuple(
            value
            for value in getattr(
                proposal,
                "persistent_obligations",
                (),
            )
            if isinstance(value, Mapping)
        )
        active_obligations = self._merge_obligations(
            runtime.active_obligations,
            persistent_obligations,
        )
        repair_count = runtime.repairs + (
            1 if phase is FramePhase.REPAIR else 0
        )
        updated = runtime.evolve(
            status=NodeStatus.RUNNING,
            phase=phase.value,
            repairs=repair_count,
            last_diagnostic=diagnostic,
            adapter_state=state,
            active_obligations=active_obligations,
        )
        artifact_mutation = getattr(
            self._services.repair.artifacts,
            "mutation",
            None,
        )
        artifact_context = (
            artifact_mutation()
            if invalidated_artifact_refs
            and callable(artifact_mutation)
            else nullcontext()
        )
        with artifact_context:
            with self._store.mutation(writer=self._writer):
                self._store.apply_delta(
                    repair_delta, writer=self._writer
                )
                self._store.set_runtime(
                    spec.node_id, updated, writer=self._writer
                )
                self._store.append_event(
                    event_type,
                    spec.node_id,
                    {
                        "entry": proposal.entry_node_id,
                        "rationale": proposal.rationale,
                        "kind": resolution_kind.value,
                        "invalidated_artifact_refs": list(
                            invalidated_artifact_refs
                        ),
                        "persistent_obligations": _jsonable(
                            persistent_obligations
                        ),
                    },
                    writer=self._writer,
                )
                self._store.replace_top_frame(
                    frame.evolve(
                        phase=phase,
                        active_child_id=proposal.entry_node_id,
                        original_diagnostic=diagnostic,
                    ),
                    writer=self._writer,
                )
                self._store.push_frame(
                    ExecutionFrame(proposal.entry_node_id),
                    writer=self._writer,
                )
                if invalidate_artifacts is not None:
                    invalidate_artifacts(invalidated_artifact_refs)

    def _evaluate_conditions(
        self,
        formulae,
        *,
        code: str,
        message_prefix: str,
    ) -> NodeOutcome | None:
        snapshot = self._services.system.world.snapshot()
        for formula in formulae:
            result = self._services.system.verifier.evaluate(
                formula, snapshot
            )
            if not result.satisfied:
                return NodeOutcome.failure(
                    code,
                    f"{message_prefix} {formula.name} failed",
                    details=dict(result.evidence.observations),
                )
        return None

    def _succeed(self, node_id, runtime, outcome) -> None:
        state = dict(runtime.adapter_state)
        active_request_id = state.pop("active_request_id", None)
        if active_request_id is not None:
            state["last_request_id"] = active_request_id
        if outcome.result:
            state["last_result"] = dict(outcome.result)
        state.setdefault(
            "initial_terminal_status",
            NodeStatus.SUCCEEDED.value,
        )
        with self._store.mutation(writer=self._writer):
            self._store.set_runtime(
                node_id,
                runtime.evolve(
                    status=NodeStatus.SUCCEEDED,
                    phase="terminal",
                    output_artifacts=(
                        outcome.artifact_refs
                        or runtime.output_artifacts
                    ),
                    last_diagnostic=None,
                    finished_at=time(),
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.pop_frame(node_id, writer=self._writer)

    def _fail_composite(self, node_id, runtime, diagnostic) -> None:
        self._fail(node_id, runtime, diagnostic)

    def _fail(self, node_id, runtime, diagnostic) -> None:
        status = self._failure_status(diagnostic)
        state = dict(runtime.adapter_state)
        state.setdefault("initial_terminal_status", status.value)
        with self._store.mutation(writer=self._writer):
            self._store.set_runtime(
                node_id,
                runtime.evolve(
                    status=status,
                    phase="terminal",
                    last_diagnostic=diagnostic,
                    finished_at=time(),
                    adapter_state=state,
                ),
                writer=self._writer,
            )
            self._store.pop_frame(node_id, writer=self._writer)
        if diagnostic is not None:
            self._record_diagnostic(node_id, diagnostic)

    def _set_phase(
        self,
        frame: ExecutionFrame,
        runtime: TaskNodeRuntime,
        phase: FramePhase,
    ) -> None:
        self._store.set_runtime(
            frame.node_id,
            runtime.evolve(phase=phase.value),
            writer=self._writer,
        )
        self._store.replace_top_frame(
            frame.evolve(phase=phase),
            writer=self._writer,
        )

    def _semantic_evaluation(
        self,
        kind: str,
        spec,
        runtime,
    ) -> SemanticCheck | None:
        semantics = self._services.semantics
        if semantics is None:
            return None
        method = getattr(semantics, f"evaluate_{kind}", None)
        if not callable(method):
            return None
        value = method(spec, runtime)
        if value is None or isinstance(value, SemanticCheck):
            return value
        if isinstance(value, bool):
            return SemanticCheck(
                SemanticState.SATISFIED
                if value
                else SemanticState.UNSATISFIED
            )
        if isinstance(value, Mapping):
            raw_state = str(
                value.get("state")
                or value.get("value")
                or "unknown"
            ).lower()
            state = {
                "true": SemanticState.SATISFIED,
                "satisfied": SemanticState.SATISFIED,
                "false": SemanticState.UNSATISFIED,
                "unsatisfied": SemanticState.UNSATISFIED,
                "unknown": SemanticState.UNKNOWN,
            }.get(raw_state, SemanticState.UNKNOWN)
            return SemanticCheck(
                state=state,
                evidence=dict(value),
            )
        raise KernelConfigurationError(
            f"Unsupported semantic result for {kind}: {value!r}"
        )

    def _obligation_failure(
        self,
        spec,
        runtime,
    ) -> NodeOutcome | None:
        check = self._semantic_evaluation(
            "obligations",
            spec,
            runtime,
        )
        if check is None or check.state is SemanticState.SATISFIED:
            return None
        return self._semantic_failure(
            check,
            code="OBLIGATION_FAILED",
            message=f"Obligations failed for {spec.node_id}",
        )

    def _inherit_child_obligations(
        self,
        parent_spec,
        parent_runtime,
        child_spec,
        child_runtime,
    ):
        inherited = self._collect_obligations(
            child_spec,
            child_runtime,
        )
        merged = self._merge_obligations(
            parent_runtime.active_obligations,
            inherited,
        )
        if merged == parent_runtime.active_obligations:
            return parent_runtime
        updated = parent_runtime.evolve(active_obligations=merged)
        self._store.set_runtime(
            parent_spec.node_id,
            updated,
            writer=self._writer,
        )
        return updated

    def _collect_obligations(
        self,
        spec,
        runtime,
    ) -> tuple[Mapping[str, Any], ...]:
        collected: tuple[Mapping[str, Any], ...] = tuple(
            runtime.active_obligations
        )
        semantics = self._services.semantics
        method = (
            getattr(semantics, "collect_obligations", None)
            if semantics is not None
            else None
        )
        if not callable(method):
            return collected
        supplied = method(spec, runtime)
        if supplied is None:
            return collected
        if not isinstance(supplied, (tuple, list)):
            raise KernelConfigurationError(
                "collect_obligations must return a tuple or list"
            )
        invalid = [
            value
            for value in supplied
            if not isinstance(value, Mapping)
        ]
        if invalid:
            raise KernelConfigurationError(
                "collect_obligations returned a non-mapping value"
            )
        return self._merge_obligations(collected, tuple(supplied))

    @staticmethod
    def _merge_obligations(
        current: tuple[Mapping[str, Any], ...],
        incoming: tuple[Mapping[str, Any], ...],
    ) -> tuple[Mapping[str, Any], ...]:
        result: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for value in (*current, *incoming):
            plain = _jsonable(value)
            key = json.dumps(
                plain,
                sort_keys=True,
                separators=(",", ":"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(value))
        return tuple(result)

    @staticmethod
    def _semantic_failure(
        check: SemanticCheck,
        *,
        code: str,
        message: str,
    ) -> NodeOutcome:
        if check.diagnostic is not None:
            return NodeOutcome(
                succeeded=False,
                diagnostic=check.diagnostic,
                result=check.evidence,
            )
        return NodeOutcome.failure(
            code,
            message,
            details=check.evidence,
            retryable=False,
        )

    @staticmethod
    def _physical_exception_outcome(
        exc: Exception,
        *,
        dispatch_invoked: bool,
    ) -> PhysicalActionOutcome:
        diagnostic = getattr(exc, "diagnostic", None)
        details = (
            dict(diagnostic.details)
            if isinstance(diagnostic, Diagnostic)
            else {}
        )
        if isinstance(exc, PhysicalEffectError):
            details.update(exc.details)
        stage = str(details.get("dispatch_stage") or "").casefold()
        journal_state = str(
            details.get("journal_state") or ""
        ).casefold()
        if not stage:
            if journal_state in {
                "dispatching",
                "completed",
                "outcome_unknown",
                "commit_ambiguous",
            }:
                stage = "prior_dispatch"
            elif dispatch_invoked:
                stage = "dispatch_unknown"
            else:
                stage = "not_started"
        if not dispatch_invoked:
            stage = "not_started"

        source_dispatch_started = details.get(
            "physical_dispatch_started"
        )
        physical_dispatch_started = (
            False
            if not dispatch_invoked
            else (
                bool(source_dispatch_started)
                if isinstance(source_dispatch_started, bool)
                else stage not in {"not_started", "reservation"}
            )
        )
        source_outcome_known = details.get("physical_outcome_known")
        physical_outcome_known = (
            True
            if not dispatch_invoked
            else (
                bool(source_outcome_known)
                if isinstance(source_outcome_known, bool)
                else not physical_dispatch_started
            )
        )
        source_requires_reconciliation = details.get(
            "requires_reconciliation"
        )
        requires_reconciliation = (
            False
            if not dispatch_invoked
            else (
                True
                if source_requires_reconciliation is True
                else not physical_outcome_known
            )
        )
        details.update(
            {
                "exception_type": type(exc).__name__,
                "dispatch_stage": stage,
                "physical_dispatch_started": (
                    physical_dispatch_started
                ),
                "physical_outcome_known": physical_outcome_known,
                "requires_reconciliation": requires_reconciliation,
            }
        )
        result = {
            "exception": str(exc),
            "dispatch_stage": stage,
            "physical_dispatch_started": physical_dispatch_started,
            "physical_outcome_known": physical_outcome_known,
            "requires_reconciliation": requires_reconciliation,
        }
        if isinstance(diagnostic, Diagnostic):
            diagnostic_details = dict(diagnostic.details)
            diagnostic_details.update(details)
            diagnostic_code = diagnostic.code
            if (
                not physical_dispatch_started
                and diagnostic_code == "OUTCOME_UNKNOWN"
            ):
                diagnostic_details.setdefault(
                    "physical_failure_code",
                    diagnostic_code,
                )
                diagnostic_code = (
                    "PHYSICAL_DISPATCH_FAILED"
                    if dispatch_invoked
                    else "PHYSICAL_DISPATCH_PREPARATION_FAILED"
                )
            diagnostic = replace(
                diagnostic,
                code=diagnostic_code,
                details=diagnostic_details,
            )
            state = (
                PhysicalExecutionState.OUTCOME_UNKNOWN
                if TaskTreeKernel._diagnostic_requires_reconciliation(
                    diagnostic
                )
                else PhysicalExecutionState.FAILED
            )
            return PhysicalActionOutcome(
                state=state,
                diagnostic=diagnostic,
                result=result,
            )

        if physical_dispatch_started:
            return PhysicalActionOutcome(
                state=PhysicalExecutionState.OUTCOME_UNKNOWN,
                result=result,
                diagnostic=Diagnostic(
                    "OUTCOME_UNKNOWN",
                    f"Physical dispatch raised: {exc}",
                    details=details,
                    retryable=False,
                ),
            )
        code = (
            "PHYSICAL_DISPATCH_FAILED"
            if dispatch_invoked
            else "PHYSICAL_DISPATCH_PREPARATION_FAILED"
        )
        return PhysicalActionOutcome(
            state=PhysicalExecutionState.FAILED,
            result=result,
            diagnostic=Diagnostic(
                code,
                f"Physical dispatch did not start: {exc}",
                details=details,
                retryable=False,
            ),
        )

    @staticmethod
    def _diagnostic_requires_reconciliation(
        diagnostic: Diagnostic,
    ) -> bool:
        details = dict(diagnostic.details)
        if diagnostic.code == "OUTCOME_UNKNOWN":
            return True
        requires_reconciliation = details.get(
            "requires_reconciliation"
        )
        if requires_reconciliation is True:
            return True
        physical_outcome_known = details.get("physical_outcome_known")
        if physical_outcome_known is False:
            return True
        if physical_outcome_known is True:
            return False
        physical_dispatch_started = details.get(
            "physical_dispatch_started"
        )
        if physical_dispatch_started is True:
            return True
        dispatch_stage = str(
            details.get("dispatch_stage") or ""
        ).casefold()
        if dispatch_stage in {
            "prior_dispatch",
            "execution",
            "completion",
            "dispatch_unknown",
        }:
            return True
        if requires_reconciliation is False:
            return False
        if physical_dispatch_started is False:
            return False
        if dispatch_stage in {"not_started", "reservation"}:
            return False
        termination = str(details.get("termination") or "").lower()
        effect_state = str(details.get("effect_state") or "").lower()
        verification = str(details.get("verification") or "").lower()
        return (
            termination in {"outcome_unknown", "timed_out"}
            or effect_state == "unknown"
            or verification == "unknown"
        )

    @staticmethod
    def _canonical_outcome_unknown_diagnostic(
        outcome: PhysicalActionOutcome,
    ) -> Diagnostic:
        source = outcome.diagnostic
        details = (
            dict(source.details)
            if source is not None
            else {}
        )
        details.update(dict(outcome.result))
        details["physical_outcome_known"] = False
        details["requires_reconciliation"] = True
        physical_failure_code = (
            details.get("failure_code")
            or (source.code if source is not None else None)
        )
        if (
            physical_failure_code
            and physical_failure_code != "OUTCOME_UNKNOWN"
        ):
            details.setdefault(
                "physical_failure_code",
                str(physical_failure_code),
            )
        return Diagnostic(
            "OUTCOME_UNKNOWN",
            (
                source.message
                if source is not None
                else "Physical action outcome requires reconciliation"
            ),
            details=details,
            retryable=False,
        )

    @staticmethod
    def _reconciled_result_state(
        result: Mapping[str, Any],
    ) -> SemanticState:
        raw_entries = result.get("reconciled")
        entries = (
            list(raw_entries)
            if isinstance(raw_entries, (list, tuple))
            else []
        )
        if not entries:
            entries = [result]

        saw_confirmed = False
        saw_refuted = False
        for item in entries:
            if not isinstance(item, Mapping):
                return SemanticState.UNKNOWN
            status = str(item.get("status") or "").lower()
            effect_state = str(
                item.get("effect_state") or ""
            ).lower()
            verification = str(
                item.get("verification") or ""
            ).lower()
            if status in {"pending", "unresolvable", "missing"}:
                return SemanticState.UNKNOWN
            if (
                effect_state in {"", "unknown", "expected", "partial"}
                or verification in {"", "unknown", "not_run"}
            ):
                return SemanticState.UNKNOWN
            if (
                effect_state in {"refuted", "false", "failed", "rejected"}
                or verification in {"false", "failed", "refuted"}
            ):
                saw_refuted = True
            elif (
                effect_state == "confirmed"
                and verification in {"true", "confirmed"}
            ):
                saw_confirmed = True
            else:
                return SemanticState.UNKNOWN
        if saw_refuted:
            return SemanticState.UNSATISFIED
        if saw_confirmed:
            return SemanticState.SATISFIED
        return SemanticState.UNKNOWN

    @staticmethod
    def _matching_reconciliation_entry(
        result: Mapping[str, Any],
        transaction_id: object,
    ) -> Mapping[str, Any] | None:
        raw_entries = result.get("reconciled")
        entries = (
            list(raw_entries)
            if isinstance(raw_entries, (list, tuple))
            else []
        )
        if not entries:
            entries = [result]
        expected = (
            str(transaction_id)
            if transaction_id is not None
            and str(transaction_id).strip()
            else None
        )
        matching = [
            item
            for item in entries
            if isinstance(item, Mapping)
            and (
                expected is None
                or str(item.get("transaction_id") or "") == expected
            )
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    @classmethod
    def _reconciled_resubmission_authorization(
        cls,
        result: Mapping[str, Any],
        *,
        expected_transaction_id: object,
        original_result: object,
        semantic_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        original = (
            dict(original_result)
            if isinstance(original_result, Mapping)
            else {}
        )
        entry = cls._matching_reconciliation_entry(
            result, expected_transaction_id
        )
        if entry is None:
            return {
                "allowed": False,
                "reason": "matching_reconciliation_entry_missing",
                "action_result": original,
            }

        action_result = (
            dict(entry.get("result"))
            if isinstance(entry.get("result"), Mapping)
            else dict(original)
        )
        status = str(entry.get("status") or "").strip().lower()
        policy = str(
            action_result.get("resubmission_policy")
            or original.get("resubmission_policy")
            or ""
        ).strip().lower()
        requires_new_request = _boolean_value(
            action_result.get(
                "resubmission_requires_new_request",
                original.get(
                    "resubmission_requires_new_request",
                    True,
                ),
            ),
            default=True,
        )
        recovery_context = (
            dict(action_result.get("recovery_context"))
            if isinstance(
                action_result.get("recovery_context"),
                Mapping,
            )
            else {}
        )
        evidence_revision = (
            entry.get("evidence_revision")
            or recovery_context.get(
                "reconciliation_evidence_revision"
            )
            or semantic_evidence.get("evidence_revision")
        )

        reason = "authorized"
        allowed = True
        if status != "closed":
            allowed = False
            reason = "transaction_not_closed"
        elif policy != "after_reobservation":
            allowed = False
            reason = "resubmission_policy_not_after_reobservation"
        elif not requires_new_request:
            allowed = False
            reason = "new_request_not_required_by_contract"
        elif not evidence_revision:
            allowed = False
            reason = "fresh_reconciliation_evidence_missing"

        return {
            "allowed": allowed,
            "reason": reason,
            "transaction_id": entry.get("transaction_id"),
            "status": status,
            "resubmission_policy": policy,
            "resubmission_requires_new_request": requires_new_request,
            "evidence_revision": evidence_revision,
            "action_result": action_result,
        }

    def _physical_request_id(
        self,
        node_id: str,
        dispatch_attempt: int,
    ) -> str:
        task_id = self._task_id or self._require_root()
        return (
            f"tree:{task_id}:{node_id}:"
            f"attempt:{dispatch_attempt}"
        )

    @staticmethod
    def _dispatch_attempts_in_epoch(
        state: Mapping[str, Any],
    ) -> int:
        if "dispatch_attempts_in_epoch" in state:
            return max(
                0,
                int(state.get("dispatch_attempts_in_epoch", 0)),
            )
        return max(0, int(state.get("dispatch_attempt", 0)))

    @staticmethod
    def _resolve_reconciled_dispatch(
        state: Mapping[str, Any],
        *,
        reconciliation_id: str,
        reconciliation_status: str,
        resolved_state: str,
    ) -> dict[str, Any]:
        updated = dict(state)
        current_dispatch = (
            dict(updated["dispatch_state"])
            if isinstance(updated.get("dispatch_state"), Mapping)
            else {}
        )
        request_id = updated.pop("active_request_id", None)
        if request_id is None:
            request_id = current_dispatch.get("request_id")
        request_hash = updated.pop("active_request_hash", None)
        if request_hash is None:
            request_hash = current_dispatch.get("request_hash")

        updated.pop("outcome_unknown", None)
        updated["last_reconciliation_state"] = reconciliation_status
        if request_id is not None:
            updated["last_request_id"] = request_id
            history = [
                str(value)
                for value in updated.get("reconciled_request_ids", ())
                if value
            ]
            if str(request_id) not in history:
                history.append(str(request_id))
            updated["reconciled_request_ids"] = tuple(history)
        if request_hash is not None:
            updated["last_request_hash"] = request_hash

        resolution = {
            "state": resolved_state,
            "physical_outcome_known": True,
            "requires_reconciliation": False,
            "reconciliation_node_id": reconciliation_id,
            "reconciliation_status": reconciliation_status,
        }
        if (
            current_dispatch
            and request_id is not None
            and current_dispatch.get("request_id") == request_id
        ):
            current_dispatch.update(resolution)
            updated["dispatch_state"] = current_dispatch

        attempt_ledger = [
            dict(item)
            for item in updated.get("attempt_ledger", ())
            if isinstance(item, Mapping)
        ]
        if request_id is not None:
            for item in reversed(attempt_ledger):
                if item.get("request_id") == request_id:
                    item.update(resolution)
                    break
        updated["attempt_ledger"] = tuple(attempt_ledger)
        return updated

    def _record_physical_outcome(
        self,
        node_id: str,
        runtime: TaskNodeRuntime,
        outcome: PhysicalActionOutcome,
    ) -> TaskNodeRuntime:
        state = dict(runtime.adapter_state)
        state["last_result"] = dict(outcome.result)
        request_id = state.get("active_request_id")
        request_hash = state.get("active_request_hash")
        transaction_id = (
            outcome.transaction_id
            or outcome.result.get("transaction_id")
        )
        if transaction_id:
            state["transaction_id"] = str(transaction_id)
        dispatch_stage = str(
            outcome.result.get("dispatch_stage") or ""
        ).casefold()
        not_dispatched = (
            outcome.state is PhysicalExecutionState.FAILED
            and dispatch_stage in {"not_started", "reservation"}
            and outcome.result.get("physical_dispatch_started") is False
            and outcome.result.get("physical_outcome_known") is True
            and outcome.result.get("requires_reconciliation") is False
        )
        outcome_state = (
            "not_dispatched"
            if not_dispatched
            else outcome.state.value
        )
        if request_id is not None:
            dispatch_state = {
                "request_id": request_id,
                "request_hash": request_hash,
                "attempt": int(state.get("dispatch_attempt", 0)),
                "dispatch_epoch": int(
                    state.get("dispatch_epoch", 0)
                ),
                "state": outcome_state,
                "dispatch_stage": outcome.result.get(
                    "dispatch_stage"
                ),
                "physical_dispatch_started": outcome.result.get(
                    "physical_dispatch_started"
                ),
                "physical_outcome_known": outcome.result.get(
                    "physical_outcome_known"
                ),
                "requires_reconciliation": outcome.result.get(
                    "requires_reconciliation"
                ),
                "transaction_id": (
                    str(transaction_id) if transaction_id else None
                ),
            }
            state["dispatch_state"] = dispatch_state
            attempt_ledger = [
                dict(item)
                for item in state.get("attempt_ledger", ())
                if isinstance(item, Mapping)
            ]
            for item in reversed(attempt_ledger):
                if item.get("request_id") == request_id:
                    item.update(dispatch_state)
                    break
            state["attempt_ledger"] = tuple(attempt_ledger)
        if not_dispatched:
            state["dispatch_attempts_in_epoch"] = max(
                0,
                int(state.get("dispatch_attempts_in_epoch", 0)) - 1,
            )
        if outcome.state in {
            PhysicalExecutionState.SUCCEEDED,
            PhysicalExecutionState.FAILED,
        }:
            request_id = state.pop("active_request_id", None)
            request_hash = state.pop("active_request_hash", None)
            if request_id is not None:
                state["last_request_id"] = request_id
            if request_hash is not None:
                state["last_request_hash"] = request_hash
        updated = runtime.evolve(
            output_artifacts=(
                outcome.artifact_refs or runtime.output_artifacts
            ),
            last_diagnostic=outcome.diagnostic,
            adapter_state=state,
        )
        self._store.set_runtime(
            node_id, updated, writer=self._writer
        )
        return updated

    @staticmethod
    def _normalize_physical_outcome(
        raw_result: object,
    ) -> PhysicalActionOutcome:
        if isinstance(raw_result, PhysicalActionOutcome):
            if raw_result.state is PhysicalExecutionState.CANCELLED:
                return normalize_cancellation_outcome(raw_result)
            if raw_result.state is PhysicalExecutionState.OUTCOME_UNKNOWN:
                return PhysicalActionOutcome(
                    PhysicalExecutionState.OUTCOME_UNKNOWN,
                    result=raw_result.result,
                    diagnostic=(
                        TaskTreeKernel
                        ._canonical_outcome_unknown_diagnostic(raw_result)
                    ),
                    artifact_refs=raw_result.artifact_refs,
                    transaction_id=raw_result.transaction_id,
                )
            return raw_result
        result = _result_dict(raw_result)
        termination = str(
            result.get("termination") or "succeeded"
        ).lower()
        effect_state = str(
            result.get("effect_state") or "confirmed"
        ).lower()
        verification = str(
            result.get("verification") or "true"
        ).lower()
        transaction_id = result.get("transaction_id")

        if termination in {"canceled", "cancelled"}:
            return normalize_cancellation_outcome(
                PhysicalActionOutcome(
                    PhysicalExecutionState.CANCELLED,
                    result=result,
                    transaction_id=(
                        str(transaction_id) if transaction_id else None
                    ),
                )
            )

        required_states = [
            str(effect.get("state", "unknown")).lower()
            for effect in result.get("effects", ())
            if isinstance(effect, Mapping)
            and bool(effect.get("required", True))
        ]
        has_unknown_effect = any(
            state in {"", "unknown", "partial"}
            for state in required_states
        )
        has_false_effect = any(
            state in {"false", "failed", "rejected"}
            for state in required_states
        )
        if (
            termination in {"", "outcome_unknown", "timed_out"}
            or effect_state in {"unknown", "partial"}
            or verification == "unknown"
            or has_unknown_effect
        ):
            physical_failure_code = result.get("failure_code")
            if (
                physical_failure_code
                and physical_failure_code != "OUTCOME_UNKNOWN"
            ):
                result.setdefault(
                    "physical_failure_code",
                    physical_failure_code,
                )
            diagnostic = Diagnostic(
                "OUTCOME_UNKNOWN",
                _physical_message(
                    result,
                    "Physical action outcome requires reconciliation",
                ),
                details=result,
                retryable=False,
            )
            return PhysicalActionOutcome(
                PhysicalExecutionState.OUTCOME_UNKNOWN,
                result=result,
                diagnostic=diagnostic,
                transaction_id=(
                    str(transaction_id) if transaction_id else None
                ),
            )

        if (
            termination == "succeeded"
            and not has_false_effect
            and effect_state not in {"false", "failed", "rejected"}
            and verification not in {"false", "failed"}
        ):
            return PhysicalActionOutcome.succeeded(
                result=result,
                transaction_id=(
                    str(transaction_id) if transaction_id else None
                ),
            )

        code = str(
            result.get("failure_code")
            or "PHYSICAL_EXECUTION_FAILED"
        )
        return PhysicalActionOutcome(
            PhysicalExecutionState.FAILED,
            result=result,
            diagnostic=Diagnostic(
                code,
                _physical_message(
                    result, "Physical action was not confirmed"
                ),
                details=result,
                retryable=code in {
                    "RESOURCE_BUSY",
                    "STALE_RELEVANT_STATE",
                },
            ),
            transaction_id=(
                str(transaction_id) if transaction_id else None
            ),
        )

    @staticmethod
    def _handler(
        registry: Mapping[str, object], task_type: str
    ) -> object | None:
        return registry.get(task_type) or registry.get("*")

    @staticmethod
    def _failure_status(
        diagnostic: Diagnostic | None,
    ) -> NodeStatus:
        if diagnostic is None:
            return NodeStatus.FAILED
        requires_reconciliation = (
            TaskTreeKernel._diagnostic_requires_reconciliation(
                diagnostic
            )
        )
        if (
            diagnostic.retryable
            or diagnostic.repairable
            or requires_reconciliation
            or diagnostic.code in {
                "GOAL_STATE_UNKNOWN",
                "RESOURCE_BUSY",
                "NODE_ATTEMPTS_EXHAUSTED",
                "RECONCILIATION_EXHAUSTED",
            }
            or diagnostic.code.endswith("_BLOCKED")
        ):
            return NodeStatus.BLOCKED
        return NodeStatus.FAILED

    def _record_diagnostic(
        self, node_id: str, diagnostic: Diagnostic
    ) -> None:
        self._diagnostics.append(
            {
                "node_id": node_id,
                "code": diagnostic.code,
                "message": diagnostic.message,
                "details": dict(diagnostic.details),
                "retryable": diagnostic.retryable,
                "repairable": diagnostic.repairable,
            }
        )

    def _active_location(
        self, root_id: str
    ) -> tuple[str, str]:
        stack = self._store.stack()
        if stack:
            frame = stack[-1]
            return frame.node_id, frame.phase.value
        runtime = self._store.runtime(root_id)
        return root_id, runtime.phase

    @staticmethod
    def _running_tick_status(phase: str) -> str:
        return {
            FramePhase.ENTER.value: "CHECKING",
            FramePhase.GOAL_CHECK.value: "CHECKING",
            FramePhase.PRECONDITIONS.value: "CHECKING",
            FramePhase.EXECUTE.value: "EXECUTING",
            FramePhase.VERIFY.value: "VERIFYING",
            FramePhase.CHILDREN.value: "EXPANDING",
            FramePhase.REPAIR.value: "REPAIRING",
            FramePhase.RECONCILIATION.value: "WAITING_RECONCILIATION",
        }.get(phase, "EXECUTING")

    def _terminal_tick(
        self,
        root_id: str,
        runtime: TaskNodeRuntime,
        *,
        changed: bool = False,
    ) -> TickResult:
        diagnostic = runtime.last_diagnostic
        return TickResult(
            status=self._external_status(runtime.status),
            node_id=root_id,
            phase="terminal",
            changed=changed,
            terminal=True,
            message=(
                diagnostic.message
                if diagnostic is not None
                else f"task tree {runtime.status.value}"
            ),
            diagnostic_id=(
                diagnostic.code if diagnostic is not None else None
            ),
        )

    @staticmethod
    def _external_status(status: NodeStatus) -> str:
        return {
            NodeStatus.PENDING: "PENDING",
            NodeStatus.RUNNING: "EXECUTING",
            NodeStatus.SUCCEEDED: "SUCCEEDED",
            NodeStatus.FAILED: "FAILED",
            NodeStatus.BLOCKED: "BLOCKED",
            NodeStatus.CANCELLED: "CANCELLED",
        }[status]

    @staticmethod
    def _validate_decomposition_delta(
        parent_id: str, delta: GraphDelta
    ) -> None:
        if delta.root_id is not None:
            raise KernelConfigurationError(
                "A decomposer cannot replace the task-tree root"
            )
        new_ids = {command.spec.node_id for command in delta.nodes}
        adjacency: dict[str, list[str]] = {
            parent_id: [],
            **{node_id: [] for node_id in new_ids},
        }
        for command in delta.edges:
            edge = command.edge
            if edge.kind is not EdgeKind.CHILD:
                raise KernelConfigurationError(
                    "A decomposer may only add child edges"
                )
            if edge.child_id not in new_ids:
                raise KernelConfigurationError(
                    "A decomposer edge must target a newly declared node"
                )
            if edge.parent_id not in adjacency:
                raise KernelConfigurationError(
                    "A decomposer edge escapes its expansion subtree"
                )
            adjacency[edge.parent_id].append(edge.child_id)
        reachable = TaskTreeKernel._reachable(parent_id, adjacency)
        if new_ids.difference(reachable):
            raise KernelConfigurationError(
                "A decomposer returned nodes not mounted below its node"
            )

    def _assign_repair_mount_order(
        self,
        failed_node_id: str,
        entry_node_id: str,
        delta: GraphDelta,
    ) -> GraphDelta:
        existing_orders = tuple(
            edge.order
            for edge in self._store.edges(
                failed_node_id,
                kind=EdgeKind.REPAIR,
            )
        )
        next_order = max(existing_orders, default=-1) + 1
        rewritten_edges = []
        for command in delta.edges:
            edge = command.edge
            if (
                edge.parent_id == failed_node_id
                and edge.child_id == entry_node_id
                and edge.kind is EdgeKind.REPAIR
            ):
                command = replace(
                    command,
                    edge=replace(edge, order=next_order),
                )
            rewritten_edges.append(command)
        return replace(delta, edges=tuple(rewritten_edges))

    @staticmethod
    def _validate_repair_delta(
        failed_node_id: str,
        entry_node_id: str,
        delta: GraphDelta,
    ) -> None:
        if delta.root_id is not None:
            raise KernelConfigurationError(
                "A repair cannot replace the task-tree root"
            )
        new_ids = {command.spec.node_id for command in delta.nodes}
        if entry_node_id not in new_ids:
            raise KernelConfigurationError(
                "Repair entry must be declared by its GraphDelta"
            )
        mount_edges = []
        adjacency: dict[str, list[str]] = {
            node_id: [] for node_id in new_ids
        }
        for command in delta.edges:
            edge = command.edge
            if (
                edge.parent_id == failed_node_id
                and edge.child_id == entry_node_id
                and edge.kind is EdgeKind.REPAIR
            ):
                mount_edges.append(edge)
                continue
            if (
                edge.kind is not EdgeKind.CHILD
                or edge.parent_id not in new_ids
                or edge.child_id not in new_ids
            ):
                raise KernelConfigurationError(
                    "A repair GraphDelta escapes its proposed subtree"
                )
            adjacency[edge.parent_id].append(edge.child_id)
        if len(mount_edges) != 1:
            raise KernelConfigurationError(
                "Repair must have exactly one mount edge from the failed node"
            )
        reachable = TaskTreeKernel._reachable(entry_node_id, adjacency)
        if new_ids.difference(reachable):
            raise KernelConfigurationError(
                "Repair GraphDelta contains nodes outside its entry subtree"
            )

    @staticmethod
    def _reachable(
        root_id: str, adjacency: dict[str, list[str]]
    ) -> set[str]:
        reachable: set[str] = set()
        stack = [root_id]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(adjacency.get(node_id, ()))
        return reachable

    def _require_root(self) -> str:
        root_id = self._store.root_id
        if root_id is None:
            raise KernelConfigurationError("Task tree has not been initialized")
        return root_id


def _result_dict(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        result = to_dict()
        if isinstance(result, Mapping):
            return {str(key): item for key, item in result.items()}
    if is_dataclass(value):
        result = asdict(value)
        return {str(key): item for key, item in result.items()}
    return {"value": repr(value)}


def _physical_message(
    result: Mapping[str, Any], fallback: str
) -> str:
    residual = result.get("residual_state")
    if isinstance(residual, Mapping):
        reason = (
            residual.get("rejection_reason")
            or residual.get("reason")
        )
        if reason:
            return str(reason)
    message = result.get("message")
    return str(message) if message else fallback


def _boolean_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
