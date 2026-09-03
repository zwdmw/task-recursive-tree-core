from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from task_recursive_tree.artifacts import ArtifactStore
from task_recursive_tree.capabilities.planning import CapabilityRegistry
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.runtime.freshness import ArtifactFreshnessChecker
from task_recursive_tree.selection.engine import SpatialSelectorEngine
from task_recursive_tree.task.model import (
    GraphDelta,
    NodeOutcome,
    RepairProposal,
    TaskNodeRuntime,
    TaskNodeSpec,
)
from task_recursive_tree.world.model import WorldModel
from task_recursive_tree.world.predicates import Verifier


@dataclass(frozen=True)
class DecompositionContext:
    """Intentionally empty: decomposition may only inspect the node."""


@dataclass(frozen=True)
class SystemOperationContext:
    world: WorldModel
    artifacts: ArtifactStore
    selector: SpatialSelectorEngine
    capabilities: CapabilityRegistry
    verifier: Verifier
    robot_model: RobotModel
    freshness: ArtifactFreshnessChecker


@dataclass(frozen=True)
class SkillContext:
    artifacts: Any
    task_id: str | None = None
    node_id: str | None = None
    attempt: int = 0
    request_id: str | None = None

    def for_dispatch(
        self,
        *,
        task_id: str,
        node_id: str,
        attempt: int,
        request_id: str,
    ) -> SkillContext:
        return replace(
            self,
            task_id=task_id,
            node_id=node_id,
            attempt=attempt,
            request_id=request_id,
        )


@dataclass(frozen=True)
class RepairContext:
    world: WorldModel
    artifacts: ArtifactStore


class SemanticState(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SemanticCheck:
    state: SemanticState
    diagnostic: Diagnostic | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence", MappingProxyType(dict(self.evidence))
        )


class PhysicalExecutionState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class PhysicalActionOutcome:
    state: PhysicalExecutionState
    result: Mapping[str, Any] = field(default_factory=dict)
    diagnostic: Diagnostic | None = None
    artifact_refs: tuple[str, ...] = ()
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "result", MappingProxyType(dict(self.result))
        )

    @classmethod
    def succeeded(
        cls,
        *,
        result: Mapping[str, Any] | None = None,
        artifact_refs: tuple[str, ...] = (),
        transaction_id: str | None = None,
    ) -> PhysicalActionOutcome:
        return cls(
            PhysicalExecutionState.SUCCEEDED,
            result or {},
            None,
            artifact_refs,
            transaction_id,
        )


def normalize_cancellation_outcome(
    outcome: PhysicalActionOutcome,
) -> PhysicalActionOutcome:
    """Require positive evidence before treating cancellation as effect-free."""

    if outcome.state is not PhysicalExecutionState.CANCELLED:
        return outcome
    result = dict(outcome.result)
    if _certified_effect_free_cancellation(result):
        return outcome

    result.setdefault("termination", "canceled")
    result.setdefault("effect_state", "unknown")
    result.setdefault("verification", "unknown")
    result.setdefault("failure_code", "CANCELLED_EFFECT_UNKNOWN")
    result["cancellation_requires_reconciliation"] = True
    return PhysicalActionOutcome(
        PhysicalExecutionState.OUTCOME_UNKNOWN,
        result=result,
        diagnostic=Diagnostic(
            "OUTCOME_UNKNOWN",
            (
                "Cancelled physical action lacks certified "
                "no-side-effect evidence"
            ),
            details=result,
            retryable=False,
        ),
        artifact_refs=outcome.artifact_refs,
        transaction_id=outcome.transaction_id,
    )


def _certified_effect_free_cancellation(
    result: Mapping[str, Any],
) -> bool:
    certificate = result.get("cancellation")
    if not isinstance(certificate, Mapping):
        return False

    dispatch_state = str(
        certificate.get("dispatch_state") or ""
    ).casefold()
    transaction_status = str(
        certificate.get("transaction_status") or ""
    ).casefold()
    if dispatch_state not in {"not_dispatched", "stopped"}:
        return False
    if transaction_status not in {"not_created", "closed"}:
        return False
    if result.get("transaction_id") and transaction_status != "closed":
        return False
    if certificate.get("quiescent") is not True:
        return False
    if certificate.get("no_side_effects_verified") is not True:
        return False
    if not str(certificate.get("evidence_revision") or ""):
        return False

    effect_state = str(result.get("effect_state") or "").casefold()
    if effect_state in {"confirmed", "partial", "unknown"}:
        return False
    no_effect_states = {
        "",
        "none",
        "not_started",
        "not_applied",
        "refuted",
        "false",
    }
    required_effect_states = {
        str(effect.get("state") or "").casefold()
        for effect in result.get("effects", ())
        if isinstance(effect, Mapping)
        and bool(effect.get("required", True))
    }
    return required_effect_states.issubset(no_effect_states)


class PhysicalActionRuntime(Protocol):
    def execute(self, request: object) -> object: ...


class NodeSemantics(Protocol):
    def evaluate_goal(
        self, node: TaskNodeSpec, runtime: TaskNodeRuntime
    ) -> SemanticCheck | None: ...

    def evaluate_preconditions(
        self, node: TaskNodeSpec, runtime: TaskNodeRuntime
    ) -> SemanticCheck | None: ...

    def evaluate_postconditions(
        self, node: TaskNodeSpec, runtime: TaskNodeRuntime
    ) -> SemanticCheck | None: ...

    def evaluate_obligations(
        self, node: TaskNodeSpec, runtime: TaskNodeRuntime
    ) -> SemanticCheck | None: ...

    def collect_obligations(
        self, node: TaskNodeSpec, runtime: TaskNodeRuntime
    ) -> tuple[Mapping[str, Any], ...]: ...


@dataclass(frozen=True)
class KernelServices:
    decomposition: DecompositionContext
    system: SystemOperationContext
    skill: SkillContext
    repair: RepairContext
    runtime: PhysicalActionRuntime
    semantics: NodeSemantics | None = None


class Decomposer(Protocol):
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta: ...


class SystemOperation(Protocol):
    def run(
        self, node: TaskNodeSpec, context: SystemOperationContext
    ) -> NodeOutcome: ...


class PhysicalSkill(Protocol):
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> object: ...


class RepairResolver(Protocol):
    def propose(
        self,
        node: TaskNodeSpec,
        diagnostic: Diagnostic,
        context: RepairContext,
        repair_index: int,
    ) -> RepairProposal | None: ...
