from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Mapping

from task_recursive_tree.task.model import EdgeKind, NodeStatus
from task_recursive_tree.task.store import TaskTreeStore


class TaskTreeInspector:
    """Read-only observability surface for operators and external tools."""

    def __init__(self, store: TaskTreeStore) -> None:
        self._store = store

    def render_ascii(self) -> str:
        root_id = self._store.root_id
        if root_id is None:
            return "<empty task tree>"
        lines: list[str] = []
        self._render_node(root_id, "", True, None, lines)
        return "\n".join(lines)

    def status_counts(self) -> Mapping[NodeStatus, int]:
        return Counter(
            runtime.status for runtime in self._store.runtimes().values()
        )

    def save(self, path: str | Path) -> Path:
        return self._store.save(path)

    def _render_node(
        self,
        node_id: str,
        prefix: str,
        last: bool,
        edge_kind: EdgeKind | None,
        lines: list[str],
    ) -> None:
        spec = self._store.spec(node_id)
        runtime = self._store.runtime(node_id)
        connector = "" if edge_kind is None else ("`- " if last else "|- ")
        edge_label = " [repair]" if edge_kind is EdgeKind.REPAIR else ""
        diagnostic = (
            f" !{runtime.last_diagnostic.code}"
            if runtime.last_diagnostic
            else ""
        )
        lines.append(
            f"{prefix}{connector}{spec.task_type}{edge_label} "
            f"<{runtime.status.value}>{diagnostic} ({node_id})"
        )
        children = list(self._store.edges(node_id))
        child_prefix = prefix + (
            "" if edge_kind is None else ("   " if last else "|  ")
        )
        for index, edge in enumerate(children):
            self._render_node(
                edge.child_id,
                child_prefix,
                index == len(children) - 1,
                edge.kind,
                lines,
            )

