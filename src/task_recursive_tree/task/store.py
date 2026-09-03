from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from task_recursive_tree.task.model import (
    EdgeKind,
    ExecutionFrame,
    GraphDelta,
    NodeStatus,
    TaskEdge,
    TaskEvent,
    TaskNodeRuntime,
    TaskNodeSpec,
)


class TreeInvariantError(RuntimeError):
    pass


class _WriterCapability:
    __slots__ = ()


class TaskTreeStore:
    """Authoritative tree storage with one claimed mutation owner."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._writer: _WriterCapability | None = None
        self._root_id: str | None = None
        self._specs: dict[str, TaskNodeSpec] = {}
        self._runtimes: dict[str, TaskNodeRuntime] = {}
        self._edges: list[TaskEdge] = []
        self._stack: list[ExecutionFrame] = []
        self._events: list[TaskEvent] = []

    def claim_writer(self) -> object:
        with self._lock:
            if self._writer is not None:
                raise TreeInvariantError("TaskTreeStore already has a writer")
            self._writer = _WriterCapability()
            return self._writer

    @contextmanager
    def mutation(self, *, writer: object) -> Iterator[None]:
        with self._lock:
            self._require_writer(writer)
            snapshot = (
                self._root_id,
                dict(self._specs),
                dict(self._runtimes),
                list(self._edges),
                list(self._stack),
                list(self._events),
            )
            try:
                yield
            except BaseException:
                (
                    self._root_id,
                    self._specs,
                    self._runtimes,
                    self._edges,
                    self._stack,
                    self._events,
                ) = snapshot
                raise

    @property
    def root_id(self) -> str | None:
        with self._lock:
            return self._root_id

    def spec(self, node_id: str) -> TaskNodeSpec:
        with self._lock:
            try:
                return self._specs[node_id]
            except KeyError as exc:
                raise KeyError(f"Unknown task node: {node_id}") from exc

    def runtime(self, node_id: str) -> TaskNodeRuntime:
        with self._lock:
            try:
                return self._runtimes[node_id]
            except KeyError as exc:
                raise KeyError(f"Unknown task node: {node_id}") from exc

    def specs(self) -> Mapping[str, TaskNodeSpec]:
        with self._lock:
            return MappingProxyType(dict(self._specs))

    def runtimes(self) -> Mapping[str, TaskNodeRuntime]:
        with self._lock:
            return MappingProxyType(dict(self._runtimes))

    def edges(
        self,
        parent_id: str | None = None,
        *,
        kind: EdgeKind | None = None,
    ) -> tuple[TaskEdge, ...]:
        with self._lock:
            edges = [
                edge
                for edge in self._edges
                if (parent_id is None or edge.parent_id == parent_id)
                and (kind is None or edge.kind is kind)
            ]
        return tuple(
            sorted(edges, key=lambda edge: (edge.kind.value, edge.order))
        )

    def children(
        self, parent_id: str, *, kind: EdgeKind = EdgeKind.CHILD
    ) -> tuple[str, ...]:
        return tuple(
            edge.child_id for edge in self.edges(parent_id, kind=kind)
        )

    def stack(self) -> tuple[ExecutionFrame, ...]:
        with self._lock:
            return tuple(self._stack)

    def events(self) -> tuple[TaskEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def apply_delta(self, delta: GraphDelta, *, writer: object) -> None:
        with self._lock:
            self._require_writer(writer)
            new_specs = dict(self._specs)
            new_edges = list(self._edges)
            for command in delta.nodes:
                spec = command.spec
                if spec.node_id in new_specs:
                    raise TreeInvariantError(
                        f"Task node already exists: {spec.node_id}"
                    )
                new_specs[spec.node_id] = spec
            for command in delta.edges:
                edge = command.edge
                if edge.parent_id == edge.child_id:
                    raise TreeInvariantError("A task node cannot parent itself")
                if edge.parent_id not in new_specs:
                    raise TreeInvariantError(
                        f"Unknown edge parent: {edge.parent_id}"
                    )
                if edge.child_id not in new_specs:
                    raise TreeInvariantError(
                        f"Unknown edge child: {edge.child_id}"
                    )
                if any(
                    existing.parent_id == edge.parent_id
                    and existing.child_id == edge.child_id
                    and existing.kind is edge.kind
                    for existing in new_edges
                ):
                    raise TreeInvariantError(f"Duplicate task edge: {edge}")
                if any(
                    existing.parent_id == edge.parent_id
                    and existing.kind is edge.kind
                    and existing.order == edge.order
                    for existing in new_edges
                ):
                    raise TreeInvariantError(
                        f"Duplicate child order for {edge.parent_id}: {edge.order}"
                    )
                if any(
                    existing.child_id == edge.child_id for existing in new_edges
                ):
                    raise TreeInvariantError(
                        f"Task node has multiple parents: {edge.child_id}"
                    )
                new_edges.append(edge)

            prospective_root = self._root_id
            if delta.root_id is not None:
                if prospective_root is not None and prospective_root != delta.root_id:
                    raise TreeInvariantError("Task tree root is already set")
                if delta.root_id not in new_specs:
                    raise TreeInvariantError(
                        f"Unknown task root: {delta.root_id}"
                    )
                prospective_root = delta.root_id
            self._assert_acyclic(new_specs, new_edges)
            if prospective_root is not None:
                self._assert_reachable(
                    prospective_root, new_specs, new_edges
                )

            self._specs = new_specs
            self._edges = new_edges
            for command in delta.nodes:
                self._runtimes[command.spec.node_id] = TaskNodeRuntime()
            self._root_id = prospective_root
            self._append_event(
                "graph_delta",
                None,
                {
                    "nodes": tuple(
                        command.spec.node_id for command in delta.nodes
                    ),
                    "edges": tuple(
                        (
                            command.edge.parent_id,
                            command.edge.child_id,
                            command.edge.kind.value,
                        )
                        for command in delta.edges
                    ),
                },
            )

    def set_runtime(
        self,
        node_id: str,
        runtime: TaskNodeRuntime,
        *,
        writer: object,
    ) -> None:
        with self._lock:
            self._require_writer(writer)
            if node_id not in self._runtimes:
                raise KeyError(f"Unknown task node: {node_id}")
            previous = self._runtimes[node_id]
            self._runtimes[node_id] = runtime
            data: dict[str, Any] = {}
            if previous.status is not runtime.status:
                data["from"] = previous.status.value
                data["to"] = runtime.status.value
            if runtime.last_diagnostic != previous.last_diagnostic:
                data["diagnostic"] = (
                    runtime.last_diagnostic.code
                    if runtime.last_diagnostic
                    else None
                )
            self._append_event("runtime", node_id, data)

    def push_frame(
        self, frame: ExecutionFrame, *, writer: object
    ) -> None:
        with self._lock:
            self._require_writer(writer)
            if frame.node_id not in self._specs:
                raise KeyError(f"Unknown task node: {frame.node_id}")
            self._stack.append(frame)
            self._append_event(
                "stack_push", frame.node_id, {"phase": frame.phase.value}
            )

    def replace_top_frame(
        self, frame: ExecutionFrame, *, writer: object
    ) -> None:
        with self._lock:
            self._require_writer(writer)
            if not self._stack:
                raise TreeInvariantError("Execution stack is empty")
            if self._stack[-1].node_id != frame.node_id:
                raise TreeInvariantError("Can only replace the top stack frame")
            self._stack[-1] = frame

    def pop_frame(
        self, expected_node_id: str, *, writer: object
    ) -> ExecutionFrame:
        with self._lock:
            self._require_writer(writer)
            if not self._stack:
                raise TreeInvariantError("Execution stack is empty")
            frame = self._stack.pop()
            if frame.node_id != expected_node_id:
                self._stack.append(frame)
                raise TreeInvariantError(
                    f"Expected frame {expected_node_id}, got {frame.node_id}"
                )
            self._append_event("stack_pop", frame.node_id, {})
            return frame

    def append_event(
        self,
        event_type: str,
        node_id: str | None,
        data: Mapping[str, Any],
        *,
        writer: object,
    ) -> None:
        with self._lock:
            self._require_writer(writer)
            self._append_event(event_type, node_id, data)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        with self._lock:
            payload = {
                "root_id": self._root_id,
                "nodes": {
                    node_id: {
                        "spec": _jsonable(spec),
                        "runtime": _jsonable(self._runtimes[node_id]),
                    }
                    for node_id, spec in self._specs.items()
                },
                "edges": _jsonable(tuple(self._edges)),
                "execution_stack": _jsonable(tuple(self._stack)),
                "events": _jsonable(tuple(self._events)),
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def _require_writer(self, writer: object) -> None:
        if self._writer is not writer:
            raise TreeInvariantError(
                "Tree mutation rejected: caller is not the claimed kernel"
            )

    def _append_event(
        self,
        event_type: str,
        node_id: str | None,
        data: Mapping[str, Any],
    ) -> None:
        self._events.append(
            TaskEvent(
                sequence=len(self._events) + 1,
                event_type=event_type,
                node_id=node_id,
                data=data,
            )
        )

    @staticmethod
    def _assert_acyclic(
        specs: Mapping[str, TaskNodeSpec], edges: list[TaskEdge]
    ) -> None:
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in specs}
        indegree: dict[str, int] = {node_id: 0 for node_id in specs}
        for edge in edges:
            adjacency[edge.parent_id].append(edge.child_id)
            indegree[edge.child_id] += 1
        ready = [
            node_id for node_id, degree in indegree.items() if degree == 0
        ]
        visited = 0
        while ready:
            node_id = ready.pop()
            visited += 1
            for child_id in adjacency[node_id]:
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)
        if visited != len(specs):
            raise TreeInvariantError("GraphDelta introduces a cycle")

    @staticmethod
    def _assert_reachable(
        root_id: str,
        specs: Mapping[str, TaskNodeSpec],
        edges: list[TaskEdge],
    ) -> None:
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in specs}
        for edge in edges:
            adjacency[edge.parent_id].append(edge.child_id)
        reachable: set[str] = set()
        stack = [root_id]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(adjacency[node_id])
        unreachable = set(specs).difference(reachable)
        if unreachable:
            raise TreeInvariantError(
                f"Task nodes are unreachable from root: {sorted(unreachable)}"
            )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
