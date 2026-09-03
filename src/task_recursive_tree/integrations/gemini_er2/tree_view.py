from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.model import (
    EdgeKind,
    FramePhase,
    NodeStatus,
    OperationKind,
)
from task_recursive_tree.task.store import TaskTreeStore

from .translation import GEMINI_ER2_METADATA_KEY


_HARNESS_STATUSES = frozenset(
    {
        "PENDING",
        "BINDING",
        "CHECKING",
        "WAITING_EVIDENCE",
        "READY",
        "EXPANDING",
        "EXECUTING",
        "VERIFYING",
        "REPAIRING",
        "WAITING_RECONCILIATION",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "BLOCKED_WITH_PROGRESS",
        "PARTIAL_SUCCESS",
        "CANCELLED",
        "PAUSED",
    }
)
_STATUS_ALIASES = {
    "running": "EXECUTING",
    "canceled": "CANCELLED",
}


def to_harness_status(value: Any) -> str:
    """Normalize Harness strings and kernel status enums to Harness spelling."""

    raw = value.value if isinstance(value, Enum) else value
    text = str(raw).strip()
    upper = text.upper()
    if upper in _HARNESS_STATUSES:
        return upper
    alias = _STATUS_ALIASES.get(text.lower())
    if alias is not None:
        return alias
    raise ValueError(f"Unsupported task node status: {value!r}")


class KernelTreeProjection:
    """Dynamic, read-only Harness-shaped view over a TaskTreeStore."""

    __slots__ = (
        "_store",
        "_task_id",
        "_program_ref",
        "_world_revision",
        "_metadata",
        "_runtime_details",
        "_nodes",
    )

    def __init__(
        self,
        store: TaskTreeStore,
        *,
        task_id: str | None = None,
        program_ref: str | None = None,
        world_revision: int | Callable[[], int | None] | None = None,
        metadata: (
            Mapping[str, Any]
            | Callable[[], Mapping[str, Any] | None]
            | None
        ) = None,
        runtime_details: (
            Mapping[str, Mapping[str, Any]]
            | Callable[[str], Mapping[str, Any] | None]
            | None
        ) = None,
    ) -> None:
        self._store = store
        self._task_id = task_id
        self._program_ref = program_ref
        self._world_revision = world_revision
        self._metadata = metadata
        self._runtime_details = runtime_details
        self._nodes = _NodeMapping(self)

    @property
    def root_id(self) -> str:
        root_id = self._store.root_id
        if root_id is None:
            raise RuntimeError("TaskTreeStore has no root")
        return root_id

    @property
    def nodes(self) -> Mapping[str, KernelNodeProjection]:
        return self._nodes

    @property
    def execution_stack(self) -> tuple[KernelExecutionFrameProjection, ...]:
        return tuple(
            KernelExecutionFrameProjection.from_kernel(frame)
            for frame in self._store.stack()
        )

    @property
    def event_seq(self) -> int:
        return len(self._store.events())

    @property
    def world_revision(self) -> int | None:
        if callable(self._world_revision):
            return self._world_revision()
        if self._world_revision is not None:
            return self._world_revision
        return self._tree_source().get("world_revision")

    @property
    def program_ref(self) -> str | None:
        if self._program_ref is not None:
            return self._program_ref
        value = self._tree_source().get("program_ref")
        return str(value) if value is not None else None

    @property
    def task_id(self) -> str | None:
        if self._task_id is not None:
            return self._task_id
        value = self._tree_source().get("task_id")
        return str(value) if value is not None else None

    @property
    def metadata(self) -> dict[str, Any]:
        result = _plain_mapping(self._tree_source().get("metadata"))
        supplied = self._metadata() if callable(self._metadata) else self._metadata
        if supplied is not None:
            result.update(_plain_mapping(supplied))
        return result

    def node(self, node_id: str) -> KernelNodeProjection:
        self._store.spec(node_id)
        return KernelNodeProjection(self, node_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "task_tree/1.0",
            "root_id": self.root_id,
            "nodes": {
                node_id: self.node(node_id).to_dict()
                for node_id in self.nodes
            },
            "execution_stack": [
                frame.to_dict() for frame in self.execution_stack
            ],
            "event_seq": self.event_seq,
            "world_revision": self.world_revision,
            "program_ref": self.program_ref,
            "task_id": self.task_id,
            "metadata": self.metadata,
        }

    def _source(self, node_id: str) -> dict[str, Any]:
        raw = self._store.spec(node_id).parameters.get(
            GEMINI_ER2_METADATA_KEY, {}
        )
        return _plain_mapping(raw)

    def _tree_source(self) -> dict[str, Any]:
        return _plain_mapping(self._source(self.root_id).get("tree"))

    def _details(self, node_id: str) -> dict[str, Any]:
        source = self._runtime_details
        if callable(source):
            return _plain_mapping(source(node_id))
        if source is None:
            return {}
        return _plain_mapping(source.get(node_id))

    def _parent_edge(self, node_id: str) -> Any | None:
        return next(
            (
                edge
                for edge in self._store.edges()
                if edge.child_id == node_id
            ),
            None,
        )

    def _children(self, node_id: str) -> tuple[str, ...]:
        return tuple(edge.child_id for edge in self._store.edges(node_id))

    def _repair_depth(self, node_id: str) -> int:
        depth = 0
        current_id = node_id
        visited: set[str] = set()
        while current_id not in visited:
            visited.add(current_id)
            edge = self._parent_edge(current_id)
            if edge is None:
                return depth
            if edge.kind is EdgeKind.REPAIR:
                depth += 1
            current_id = edge.parent_id
        return depth


class _NodeMapping(Mapping[str, "KernelNodeProjection"]):
    __slots__ = ("_tree",)

    def __init__(self, tree: KernelTreeProjection) -> None:
        self._tree = tree

    def __getitem__(self, node_id: str) -> KernelNodeProjection:
        return self._tree.node(node_id)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._tree._store.specs()))

    def __len__(self) -> int:
        return len(self._tree._store.specs())


class KernelNodeProjection:
    __slots__ = ("_tree", "_node_id")

    def __init__(self, tree: KernelTreeProjection, node_id: str) -> None:
        self._tree = tree
        self._node_id = node_id

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def spec(self) -> KernelNodeSpecProjection:
        return KernelNodeSpecProjection(self._tree, self._node_id)

    @property
    def runtime(self) -> KernelNodeRuntimeProjection:
        return KernelNodeRuntimeProjection(self._tree, self._node_id)

    @property
    def status(self) -> str:
        return self.runtime.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "runtime": self.runtime.to_dict(),
        }


class KernelNodeSpecProjection:
    __slots__ = ("_tree", "_node_id")

    def __init__(self, tree: KernelTreeProjection, node_id: str) -> None:
        self._tree = tree
        self._node_id = node_id

    @property
    def _kernel(self) -> Any:
        return self._tree._store.spec(self._node_id)

    @property
    def _source(self) -> dict[str, Any]:
        return self._tree._source(self._node_id)

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def parent_id(self) -> str | None:
        edge = self._tree._parent_edge(self._node_id)
        return edge.parent_id if edge is not None else None

    @property
    def root_id(self) -> str:
        return self._tree.root_id

    @property
    def node_kind(self) -> str:
        value = self._source.get("node_kind")
        if value is not None:
            return str(value)
        return {
            OperationKind.DECOMPOSER: "task",
            OperationKind.SYSTEM: "system",
            OperationKind.PHYSICAL: "physical",
        }[self._kernel.operation_kind]

    @property
    def task_type(self) -> str:
        return self._kernel.task_type

    @property
    def object_ref(self) -> Any:
        return _plain_value(self._source.get("object_ref"))

    @property
    def actor_ref(self) -> Any:
        return _plain_value(self._source.get("actor_ref"))

    @property
    def from_state(self) -> Any:
        return _plain_value(self._source.get("from_state"))

    @property
    def to_state(self) -> Any:
        return _plain_value(self._source.get("to_state"))

    @property
    def preconditions(self) -> Any:
        return _plain_value(self._source.get("preconditions", {}))

    @property
    def goal(self) -> Any:
        return _plain_value(self._source.get("goal", {}))

    @property
    def goal_scope(self) -> str:
        return str(self._source.get("goal_scope", "world"))

    @property
    def obligations(self) -> list[Any]:
        return list(_plain_value(self._source.get("obligations", [])))

    @property
    def params(self) -> dict[str, Any]:
        params = {
            key: value
            for key, value in self._kernel.parameters.items()
            if key != GEMINI_ER2_METADATA_KEY
        }
        return _plain_mapping(params)

    @property
    def children(self) -> list[str]:
        return list(self._tree._children(self._node_id))

    @property
    def child_policy(self) -> str:
        return str(self._source.get("child_policy", "sequence"))

    @property
    def decomposer_ref(self) -> str | None:
        value = self._source.get("decomposer_ref")
        return str(value) if value is not None else None

    @property
    def tool_ref(self) -> str | None:
        value = self._source.get("tool_ref")
        return str(value) if value is not None else None

    @property
    def action_ref(self) -> str | None:
        value = self._source.get("action_ref")
        if value is not None:
            return str(value)
        if self._kernel.operation_kind is OperationKind.PHYSICAL:
            return self._kernel.task_type
        return None

    @property
    def retry_policy(self) -> dict[str, Any]:
        source = self._source.get("retry_policy")
        if source:
            return _plain_mapping(source)
        return {
            "max_attempts": self._kernel.max_attempts,
            "max_repairs": self._kernel.max_repairs,
            "retry_on": [],
            "requires_new_world_revision": True,
        }

    @property
    def resource_policy(self) -> dict[str, Any]:
        source = self._source.get("resource_policy")
        if source:
            return _plain_mapping(source)
        return {"claims": [], "exclusive": True, "allow_parallel": False}

    @property
    def origin(self) -> str:
        return str(self._source.get("origin", self._kernel.origin.value))

    @property
    def metadata(self) -> dict[str, Any]:
        return _plain_mapping(self._source.get("metadata"))

    @property
    def is_atomic(self) -> bool:
        return bool(self.action_ref) and not self.children

    @property
    def is_control(self) -> bool:
        return self._kernel.control_kind.value != "leaf"

    @property
    def is_composite(self) -> bool:
        return bool(self.children) or not self.action_ref

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "root_id": self.root_id,
            "node_kind": self.node_kind,
            "task_type": self.task_type,
            "object_ref": self.object_ref,
            "actor_ref": self.actor_ref,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "preconditions": self.preconditions,
            "goal": self.goal,
            "goal_scope": self.goal_scope,
            "obligations": self.obligations,
            "params": self.params,
            "children": self.children,
            "child_policy": self.child_policy,
            "decomposer_ref": self.decomposer_ref,
            "tool_ref": self.tool_ref,
            "action_ref": self.action_ref,
            "retry_policy": self.retry_policy,
            "resource_policy": self.resource_policy,
            "origin": self.origin,
            "metadata": self.metadata,
        }


class KernelNodeRuntimeProjection:
    __slots__ = ("_tree", "_node_id")

    def __init__(self, tree: KernelTreeProjection, node_id: str) -> None:
        self._tree = tree
        self._node_id = node_id

    @property
    def _kernel(self) -> Any:
        return self._tree._store.runtime(self._node_id)

    @property
    def _details(self) -> dict[str, Any]:
        return self._tree._details(self._node_id)

    @property
    def _adapter_state(self) -> dict[str, Any]:
        return _plain_mapping(self._kernel.adapter_state)

    @property
    def status(self) -> str:
        return to_harness_status(self._details.get("status", self._kernel.status))

    @property
    def phase(self) -> str:
        if self._details.get("phase") is not None:
            return str(self._details["phase"])
        frame = next(
            (
                item
                for item in reversed(self._tree._store.stack())
                if item.node_id == self._node_id
            ),
            None,
        )
        if frame is not None:
            return frame.phase.value
        if self._kernel.status.terminal:
            return "terminal"
        return str(self._kernel.phase)

    @property
    def failure(self) -> dict[str, Any] | None:
        if "failure" in self._details:
            value = self._details.get("failure")
            return _plain_mapping(value) if value is not None else None
        diagnostic = self._kernel.last_diagnostic
        if diagnostic is None:
            return None
        return _diagnostic_dict(diagnostic, self._node_id)

    @property
    def last_result(self) -> dict[str, Any] | None:
        value = self._details.get(
            "last_result",
            self._adapter_state.get("last_result"),
        )
        return _plain_mapping(value) if value is not None else None

    @property
    def transaction_id(self) -> str | None:
        return _optional_text(
            self._details.get(
                "transaction_id",
                self._adapter_state.get("transaction_id"),
            )
        )

    @property
    def runtime_request_id(self) -> str | None:
        state = self._adapter_state
        return _optional_text(
            self._details.get(
                "runtime_request_id",
                state.get("active_request_id")
                or state.get("last_request_id")
                or state.get("last_cancelled_request_id"),
            )
        )

    @property
    def finished_at(self) -> float | None:
        value = self._details.get(
            "finished_at",
            self._kernel.finished_at,
        )
        return float(value) if value is not None else None

    def to_dict(self) -> dict[str, Any]:
        details = self._details
        diagnostic = self._kernel.last_diagnostic
        diagnostics = details.get("diagnostics")
        if diagnostics is None:
            diagnostics = [diagnostic.message] if diagnostic is not None else []
        evidence_refs = details.get(
            "evidence_refs", list(self._kernel.output_artifacts)
        )
        adapter_state = self._adapter_state
        return {
            "status": self.status,
            "phase": self.phase,
            "child_cursor": int(details.get("child_cursor", 0)),
            "attempts": int(
                adapter_state.get(
                    "dispatch_attempt",
                    self._kernel.attempts,
                )
            ),
            "repair_depth": int(
                details.get(
                    "repair_depth", self._tree._repair_depth(self._node_id)
                )
            ),
            "repair_attempts_by_signature": _plain_mapping(
                details.get("repair_attempts_by_signature")
            ),
            "reconciliation_attempts": int(
                details.get(
                    "reconciliation_attempts",
                    adapter_state.get("reconciliation_attempts", 0),
                )
            ),
            "reconciliation_evidence_revisions": list(
                _plain_value(
                    details.get(
                        "reconciliation_evidence_revisions",
                        adapter_state.get(
                            "reconciliation_evidence_revisions",
                            [],
                        ),
                    )
                )
            ),
            "progress_certificates": list(
                _plain_value(details.get("progress_certificates", []))
            ),
            "entered_world_revision": details.get("entered_world_revision"),
            "last_checked_world_revision": details.get(
                "last_checked_world_revision"
            ),
            "read_set": _plain_mapping(details.get("read_set")),
            "evidence_refs": list(_plain_value(evidence_refs)),
            "transaction_id": self.transaction_id,
            "runtime_request_id": self.runtime_request_id,
            "last_result": self.last_result,
            "published_artifacts": list(
                _plain_value(
                    details.get(
                        "published_artifacts",
                        adapter_state.get("published_artifacts", []),
                    )
                )
            ),
            "consumed_artifacts": list(
                _plain_value(
                    details.get(
                        "consumed_artifacts",
                        adapter_state.get("consumed_artifacts", []),
                    )
                )
            ),
            "failure": self.failure,
            "waiting_on_node_id": _optional_text(
                details.get("waiting_on_node_id")
            ),
            "return_value": details.get(
                "return_value",
                self.status if self._kernel.status.terminal else None,
            ),
            "last_goal_value": details.get("last_goal_value"),
            "last_precondition_value": details.get(
                "last_precondition_value"
            ),
            "diagnostics": list(_plain_value(diagnostics)),
            "active_obligations": list(
                _plain_value(self._kernel.active_obligations)
            ),
            "started_at": details.get(
                "started_at",
                self._kernel.started_at,
            ),
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True)
class KernelExecutionFrameProjection:
    node_id: str
    phase: str
    child_cursor: int = 0
    waiting_for_child: str | None = None
    waiting_kind: str | None = None
    pending_condition: str | None = None
    pending_condition_index: int = 0
    active_repair_id: str | None = None
    active_reconciliation_id: str | None = None
    alternative_cursor: int = 0
    child_result: str | None = None
    child_diagnostic_id: str | None = None
    condition_recheck_count: int = 0
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_kernel(cls, frame: Any) -> "KernelExecutionFrameProjection":
        phase = frame.phase.value if isinstance(frame.phase, FramePhase) else str(
            frame.phase
        )
        return cls(
            node_id=frame.node_id,
            phase=phase,
            child_cursor=frame.next_child_index,
            waiting_for_child=frame.active_child_id,
            waiting_kind=phase if frame.active_child_id is not None else None,
            active_repair_id=(
                frame.active_child_id if phase == FramePhase.REPAIR.value else None
            ),
            child_diagnostic_id=(
                frame.last_child_diagnostic.code
                if frame.last_child_diagnostic is not None
                else None
            ),
            metadata={},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _diagnostic_dict(
    diagnostic: Diagnostic, node_id: str
) -> dict[str, Any]:
    details = _plain_mapping(diagnostic.details)
    return {
        "source_node_id": node_id,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "details": details,
        "retryable": diagnostic.retryable,
        "repairable": diagnostic.repairable,
        **details,
    }


def _plain_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    plain = _plain_value(value)
    if not isinstance(plain, dict):
        raise TypeError(f"Expected a mapping, got {type(value).__name__}")
    return plain


def _plain_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _plain_value(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain_value(item) for item in value), key=repr)
    return copy.deepcopy(value)


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None
