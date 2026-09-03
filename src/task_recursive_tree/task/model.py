from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from task_recursive_tree.core.model import Diagnostic, PredicateFormula


class OperationKind(str, Enum):
    DECOMPOSER = "decomposer"
    SYSTEM = "system"
    PHYSICAL = "physical"


class ExecutionPolicy(str, Enum):
    SKIP_IF_GOAL_SATISFIED = "skip_if_goal_satisfied"
    REQUIRE_EXECUTION = "require_execution"


class ControlKind(str, Enum):
    LEAF = "leaf"
    SEQUENCE = "sequence"
    SELECTOR = "selector"


class NodeOrigin(str, Enum):
    PROGRAM = "program"
    COMPILER = "compiler"
    DECOMPOSER = "decomposer"
    REPAIR = "repair"


class EdgeKind(str, Enum):
    CHILD = "child"
    REPAIR = "repair"


class FailureResolutionKind(str, Enum):
    REPAIR = "repair"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True)
class KernelLimits:
    """Global execution limits independent of per-node retry policy.

    ``max_node_attempts`` bounds physical dispatches against one fixed
    planning state. A successful explicit repair starts a new dispatch epoch,
    while the monotonic request attempt number remains unchanged.
    """

    max_node_attempts: int = 3
    max_reconciliations: int = 16

    def __post_init__(self) -> None:
        if self.max_node_attempts < 1:
            raise ValueError("max_node_attempts must be at least one")
        if self.max_reconciliations < 0:
            raise ValueError("max_reconciliations must not be negative")


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            NodeStatus.SUCCEEDED,
            NodeStatus.FAILED,
            NodeStatus.BLOCKED,
            NodeStatus.CANCELLED,
        }


class FramePhase(str, Enum):
    ENTER = "enter"
    GOAL_CHECK = "goal_check"
    PRECONDITIONS = "preconditions"
    EXECUTE = "execute"
    VERIFY = "verify"
    CHILDREN = "children"
    REPAIR = "repair"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True)
class TaskNodeSpec:
    node_id: str
    task_type: str
    operation_kind: OperationKind
    control_kind: ControlKind
    origin: NodeOrigin
    parameters: Mapping[str, Any] = field(default_factory=dict)
    preconditions: tuple[PredicateFormula, ...] = ()
    postconditions: tuple[PredicateFormula, ...] = ()
    max_attempts: int = 2
    max_repairs: int = 1
    execution_policy: ExecutionPolicy = (
        ExecutionPolicy.SKIP_IF_GOAL_SATISFIED
    )

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id must not be empty")
        if not self.task_type:
            raise ValueError("task_type must not be empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.max_repairs < 0:
            raise ValueError("max_repairs must not be negative")
        object.__setattr__(
            self, "parameters", MappingProxyType(dict(self.parameters))
        )


@dataclass(frozen=True)
class TaskNodeRuntime:
    status: NodeStatus = NodeStatus.PENDING
    phase: str = FramePhase.ENTER.value
    attempts: int = 0
    repairs: int = 0
    expanded: bool = False
    output_artifacts: tuple[str, ...] = ()
    last_diagnostic: Diagnostic | None = None
    started_at: float | None = None
    finished_at: float | None = None
    adapter_state: Mapping[str, Any] = field(default_factory=dict)
    active_obligations: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "adapter_state", MappingProxyType(dict(self.adapter_state))
        )
        object.__setattr__(
            self,
            "active_obligations",
            _normalize_obligations(self.active_obligations),
        )

    def evolve(self, **changes: Any) -> TaskNodeRuntime:
        return replace(self, **changes)


@dataclass(frozen=True)
class TaskEdge:
    parent_id: str
    child_id: str
    kind: EdgeKind = EdgeKind.CHILD
    order: int = 0


@dataclass(frozen=True)
class ExecutionFrame:
    node_id: str
    phase: FramePhase = FramePhase.ENTER
    next_child_index: int = 0
    active_child_id: str | None = None
    original_diagnostic: Diagnostic | None = None
    last_child_diagnostic: Diagnostic | None = None

    def evolve(self, **changes: Any) -> ExecutionFrame:
        return replace(self, **changes)


@dataclass(frozen=True)
class AddNode:
    spec: TaskNodeSpec


@dataclass(frozen=True)
class AddEdge:
    edge: TaskEdge


@dataclass(frozen=True)
class GraphDelta:
    nodes: tuple[AddNode, ...] = ()
    edges: tuple[AddEdge, ...] = ()
    root_id: str | None = None

    @classmethod
    def from_specs(
        cls,
        specs: tuple[TaskNodeSpec, ...],
        edges: tuple[TaskEdge, ...] = (),
        root_id: str | None = None,
    ) -> GraphDelta:
        return cls(
            nodes=tuple(AddNode(spec) for spec in specs),
            edges=tuple(AddEdge(edge) for edge in edges),
            root_id=root_id,
        )


@dataclass(frozen=True)
class NodeOutcome:
    succeeded: bool
    artifact_refs: tuple[str, ...] = ()
    diagnostic: Diagnostic | None = None
    result: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "result", MappingProxyType(dict(self.result))
        )

    @classmethod
    def success(
        cls,
        *artifact_refs: str,
        result: Mapping[str, Any] | None = None,
    ) -> NodeOutcome:
        return cls(True, tuple(artifact_refs), None, result or {})

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        repairable: bool = False,
    ) -> NodeOutcome:
        return cls(
            False,
            (),
            Diagnostic(
                code=code,
                message=message,
                details=details or {},
                retryable=retryable,
                repairable=repairable,
            ),
            {},
        )


@dataclass(frozen=True)
class RepairProposal:
    delta: GraphDelta
    entry_node_id: str
    rationale: str
    kind: FailureResolutionKind = FailureResolutionKind.REPAIR
    invalidates_artifacts: tuple[str, ...] = ()
    persistent_obligations: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "invalidates_artifacts",
            tuple(
                dict.fromkeys(
                    str(value)
                    for value in self.invalidates_artifacts
                    if str(value)
                )
            ),
        )
        object.__setattr__(
            self,
            "persistent_obligations",
            _normalize_obligations(self.persistent_obligations),
        )


@dataclass(frozen=True)
class TaskEvent:
    sequence: int
    event_type: str
    node_id: str | None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class TickResult:
    status: str
    node_id: str | None = None
    phase: str | None = None
    changed: bool = False
    terminal: bool = False
    message: str = ""
    diagnostic_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_obligations(
    values: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    normalized: list[Mapping[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise TypeError("task obligations must be mappings")
        normalized.append(
            MappingProxyType(copy.deepcopy(dict(value)))
        )
    return tuple(normalized)
