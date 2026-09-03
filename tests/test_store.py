from __future__ import annotations

import json

import pytest

from task_recursive_tree.task.model import (
    ControlKind,
    ExecutionFrame,
    GraphDelta,
    NodeOrigin,
    NodeStatus,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)
from task_recursive_tree.task.store import TaskTreeStore, TreeInvariantError


def node(node_id: str) -> TaskNodeSpec:
    return TaskNodeSpec(
        node_id=node_id,
        task_type="Test",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
    )


def test_store_accepts_only_claimed_writer() -> None:
    store = TaskTreeStore()
    writer = store.claim_writer()
    delta = GraphDelta.from_specs((node("root"),), root_id="root")
    with pytest.raises(TreeInvariantError):
        store.apply_delta(delta, writer=object())
    store.apply_delta(delta, writer=writer)
    assert store.root_id == "root"


def test_store_rejects_cycles() -> None:
    store = TaskTreeStore()
    writer = store.claim_writer()
    store.apply_delta(
        GraphDelta.from_specs(
            (node("a"), node("b")),
            (TaskEdge("a", "b"),),
            root_id="a",
        ),
        writer=writer,
    )
    with pytest.raises(TreeInvariantError, match="cycle"):
        store.apply_delta(
            GraphDelta.from_specs((), (TaskEdge("b", "a"),)),
            writer=writer,
        )


def test_store_rejects_unreachable_nodes() -> None:
    store = TaskTreeStore()
    writer = store.claim_writer()
    with pytest.raises(TreeInvariantError, match="unreachable"):
        store.apply_delta(
            GraphDelta.from_specs(
                (node("root"), node("orphan")),
                root_id="root",
            ),
            writer=writer,
        )


def test_mutation_rolls_back_all_store_state_on_failure() -> None:
    store = TaskTreeStore()
    writer = store.claim_writer()
    store.apply_delta(
        GraphDelta.from_specs((node("root"),), root_id="root"),
        writer=writer,
    )
    store.push_frame(ExecutionFrame("root"), writer=writer)
    original_events = store.events()
    original_stack = store.stack()

    with pytest.raises(TreeInvariantError, match="Expected frame"):
        with store.mutation(writer=writer):
            runtime = store.runtime("root")
            store.set_runtime(
                "root",
                runtime.evolve(status=NodeStatus.RUNNING),
                writer=writer,
            )
            store.append_event(
                "inside_failed_mutation",
                "root",
                {},
                writer=writer,
            )
            store.pop_frame("not-root", writer=writer)

    assert store.runtime("root").status is NodeStatus.PENDING
    assert store.events() == original_events
    assert store.stack() == original_stack


def test_store_save_atomically_replaces_snapshot(tmp_path) -> None:
    store = TaskTreeStore()
    writer = store.claim_writer()
    store.apply_delta(
        GraphDelta.from_specs((node("root"),), root_id="root"),
        writer=writer,
    )
    target = tmp_path / "tree.json"
    target.write_text('{"stale": true}', encoding="utf-8")

    saved = store.save(target)

    assert saved == target
    assert json.loads(target.read_text(encoding="utf-8"))["root_id"] == (
        "root"
    )
    assert not (tmp_path / "tree.json.tmp").exists()
