from __future__ import annotations

from task_recursive_tree.artifacts import ArtifactStore
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.robot.ports import (
    ActionRequest,
    ExecutionReceipt,
    RobotBackend,
    RobotExecutionError,
)
from task_recursive_tree.runtime.freshness import ArtifactFreshnessChecker
from task_recursive_tree.runtime.leases import ResourceBusy, ResourceLeaseManager
from task_recursive_tree.runtime.transactions import TransactionLedger
from task_recursive_tree.world.model import WorldModel
from task_recursive_tree.world.predicates import Verifier


class ExecutionFailure(RuntimeError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class HarnessRuntime:
    """The only component allowed to execute physical action requests."""

    def __init__(
        self,
        *,
        world: WorldModel,
        artifacts: ArtifactStore,
        backend: RobotBackend,
        verifier: Verifier,
        robot_model: RobotModel,
        leases: ResourceLeaseManager | None = None,
        ledger: TransactionLedger | None = None,
        freshness: ArtifactFreshnessChecker | None = None,
    ) -> None:
        self._world = world
        self._artifacts = artifacts
        self._backend = backend
        self._verifier = verifier
        self._robot_model = robot_model
        self._leases = leases or ResourceLeaseManager()
        self._ledger = ledger or TransactionLedger()
        self._freshness = freshness or ArtifactFreshnessChecker()

    def transaction_records(self):
        return self._ledger.records()

    def execute(self, request: ActionRequest) -> ExecutionReceipt:
        try:
            snapshot = self._world.snapshot()
            for artifact_ref in request.artifact_refs:
                artifact = self._artifacts.get(artifact_ref)
                result = self._freshness.evaluate(
                    artifact,
                    snapshot,
                    robot_model_version=self._robot_model.model_version,
                    collision_model_version=(
                        self._robot_model.collision_model_version
                    ),
                )
                if not result.fresh:
                    raise ExecutionFailure(
                        Diagnostic(
                            "STALE_PLAN",
                            f"Artifact {artifact_ref} is stale",
                            details={
                                "mismatches": result.mismatches,
                                **_dispatch_evidence(False),
                            },
                            retryable=True,
                        )
                    )
            for formula in request.preconditions:
                result = self._verifier.evaluate(formula, snapshot)
                if not result.satisfied:
                    raise ExecutionFailure(
                        Diagnostic(
                            "GUARD_FAILED",
                            f"Physical guard {formula.name} failed",
                            details={
                                **dict(result.evidence.observations),
                                **_dispatch_evidence(False),
                            },
                            retryable=False,
                        )
                    )
        except ExecutionFailure:
            raise
        except Exception as exc:
            raise ExecutionFailure(
                Diagnostic(
                    "PHYSICAL_PREPARATION_FAILED",
                    f"Physical preparation failed: {exc}",
                    details=_dispatch_evidence(False),
                    retryable=False,
                )
            ) from exc

        command_attempted = False
        try:
            lease_context = self._leases.acquire(request.resources)
            with lease_context:
                supports_rollback = self._backend.supports_rollback
                checkpoint = (
                    self._backend.checkpoint()
                    if supports_rollback
                    else None
                )
                transaction = self._ledger.begin(request.request_id)
                completed_commands = 0
                updated = snapshot
                try:
                    for command in request.commands:
                        command_attempted = True
                        self._backend.execute(command)
                        completed_commands += 1
                        updated = self._world.ingest(
                            self._backend.observe()
                        )
                    self._ledger.commit(transaction.transaction_id)
                    return ExecutionReceipt(
                        request_id=request.request_id,
                        transaction_id=transaction.transaction_id,
                        snapshot_ref=updated.snapshot_ref,
                        command_count=completed_commands,
                    )
                except BaseException as exc:
                    reconciliation_errors: list[str] = []
                    if supports_rollback:
                        try:
                            self._backend.restore(checkpoint)
                        except Exception as restore_error:
                            reconciliation_errors.append(
                                f"backend restore failed: {restore_error}"
                            )
                    try:
                        reconciled = self._world.ingest(
                            self._backend.observe()
                        )
                    except Exception as reconciliation_error:
                        reconciled = None
                        reconciliation_errors.append(
                            "world reconciliation failed: "
                            f"{reconciliation_error}"
                        )
                    try:
                        if supports_rollback and not reconciliation_errors:
                            self._ledger.rollback(
                                transaction.transaction_id, str(exc)
                            )
                        else:
                            self._ledger.fail_reconciled(
                                transaction.transaction_id, str(exc)
                            )
                    except Exception as ledger_error:
                        reconciliation_errors.append(
                            f"ledger update failed: {ledger_error}"
                        )

                    if not isinstance(exc, Exception):
                        add_note = getattr(exc, "add_note", None)
                        if callable(add_note):
                            for error in reconciliation_errors:
                                add_note(error)
                        raise

                    if isinstance(exc, RobotExecutionError):
                        source = exc.diagnostic
                    else:
                        source = Diagnostic(
                            "OUTCOME_UNKNOWN",
                            f"Physical transaction failed: {exc}",
                            retryable=False,
                        )
                    details = dict(source.details)
                    details.update(
                        {
                            "completed_commands": completed_commands,
                            "rollback_supported": supports_rollback,
                            "reconciled_snapshot": (
                                reconciled.snapshot_ref
                                if reconciled is not None
                                else None
                            ),
                            "reconciliation_errors": tuple(
                                reconciliation_errors
                            ),
                            **_dispatch_evidence(
                                command_attempted,
                            ),
                        }
                    )
                    raise ExecutionFailure(
                        Diagnostic(
                            source.code,
                            source.message,
                            details=details,
                            retryable=(
                                source.retryable
                                and not reconciliation_errors
                            ),
                            repairable=source.repairable,
                        )
                    ) from exc
        except ExecutionFailure:
            raise
        except ResourceBusy as exc:
            raise ExecutionFailure(
                Diagnostic(
                    "RESOURCE_BUSY",
                    str(exc),
                    details=_dispatch_evidence(False),
                    retryable=True,
                )
            ) from exc
        except Exception as exc:
            dispatched = bool(command_attempted)
            raise ExecutionFailure(
                Diagnostic(
                    (
                        "OUTCOME_UNKNOWN"
                        if dispatched
                        else "PHYSICAL_PREPARATION_FAILED"
                    ),
                    (
                        f"Physical execution failed: {exc}"
                        if dispatched
                        else f"Physical preparation failed: {exc}"
                    ),
                    details=_dispatch_evidence(dispatched),
                    retryable=False,
                )
            ) from exc


def _dispatch_evidence(
    physical_dispatch_started: bool,
) -> dict[str, object]:
    return {
        "dispatch_stage": (
            "execution"
            if physical_dispatch_started
            else "not_started"
        ),
        "physical_dispatch_started": physical_dispatch_started,
        "physical_outcome_known": not physical_dispatch_started,
        "requires_reconciliation": physical_dispatch_started,
    }
