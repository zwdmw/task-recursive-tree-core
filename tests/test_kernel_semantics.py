from __future__ import annotations

import pytest

from task_recursive_tree.bootstrap import build_application
from task_recursive_tree.demo import demo_world
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.contracts import SemanticCheck, SemanticState
from task_recursive_tree.task.model import (
    ControlKind,
    ExecutionPolicy,
    GraphDelta,
    NodeOrigin,
    NodeOutcome,
    NodeStatus,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)


class SelectorDecomposer:
    def expand(self, node, context):
        fail = TaskNodeSpec(
            node_id=f"{node.node_id}/fail",
            task_type="AlwaysFail",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.DECOMPOSER,
            max_attempts=1,
            max_repairs=0,
        )
        succeed = TaskNodeSpec(
            node_id=f"{node.node_id}/succeed",
            task_type="AlwaysSucceed",
            operation_kind=OperationKind.SYSTEM,
            control_kind=ControlKind.LEAF,
            origin=NodeOrigin.DECOMPOSER,
        )
        return GraphDelta.from_specs(
            (fail, succeed),
            (
                TaskEdge(node.node_id, fail.node_id, order=0),
                TaskEdge(node.node_id, succeed.node_id, order=1),
            ),
        )


class FixedOperation:
    def __init__(self, succeeds: bool) -> None:
        self.succeeds = succeeds

    def run(self, node, context):
        if self.succeeds:
            return NodeOutcome.success()
        return NodeOutcome.failure("BRANCH_FAILED", "expected branch failure")


class RecordingEmptyDecomposer:
    def __init__(self) -> None:
        self.calls = 0

    def expand(self, node, context):
        del node, context
        self.calls += 1
        return GraphDelta()


class AlwaysSatisfiedSemantics:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate_goal(self, node, runtime):
        del node, runtime
        self.calls.append("goal")
        return SemanticCheck(SemanticState.SATISFIED)

    def evaluate_preconditions(self, node, runtime):
        del node, runtime
        self.calls.append("preconditions")
        return SemanticCheck(SemanticState.SATISFIED)

    def evaluate_postconditions(self, node, runtime):
        del node, runtime
        self.calls.append("postconditions")
        return SemanticCheck(SemanticState.SATISFIED)


def selector_definition() -> TaskTreeDefinition:
    root = TaskNodeSpec(
        node_id="selector-root",
        task_type="SelectorTest",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SELECTOR,
        origin=NodeOrigin.COMPILER,
        max_attempts=1,
        max_repairs=0,
    )
    return TaskTreeDefinition(
        root_id=root.node_id,
        delta=GraphDelta.from_specs((root,), root_id=root.node_id),
    )


def test_selector_tries_next_child_after_failure() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    application.kernel._decomposers["SelectorTest"] = SelectorDecomposer()
    application.kernel._system_operations["AlwaysFail"] = FixedOperation(False)
    application.kernel._system_operations["AlwaysSucceed"] = FixedOperation(True)
    application.kernel.initialize(selector_definition())

    assert application.kernel.run() is NodeStatus.SUCCEEDED
    assert application.store.runtime("selector-root/fail").status is NodeStatus.FAILED
    assert (
        application.store.runtime("selector-root/succeed").status
        is NodeStatus.SUCCEEDED
    )


def test_step_budget_does_not_poison_execution_stack() -> None:
    grid, observation = demo_world()
    application = build_application(grid, observation)
    application.kernel._decomposers["SelectorTest"] = SelectorDecomposer()
    application.kernel._system_operations["AlwaysFail"] = FixedOperation(False)
    application.kernel._system_operations["AlwaysSucceed"] = FixedOperation(True)
    application.kernel.initialize(selector_definition())

    first_status = application.kernel.run(max_steps=1)
    assert not first_status.terminal
    assert application.store.stack()
    assert application.kernel.run() is NodeStatus.SUCCEEDED
    assert application.store.stack() == ()


def test_kernel_rejects_unmounted_decomposer_nodes() -> None:
    orphan = TaskNodeSpec(
        node_id="orphan",
        task_type="Orphan",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.DECOMPOSER,
    )
    with pytest.raises(Exception, match="not mounted"):
        from task_recursive_tree.task.kernel import TaskTreeKernel

        TaskTreeKernel._validate_decomposition_delta(
            "parent", GraphDelta.from_specs((orphan,))
        )


def test_required_execution_policy_prevents_goal_short_circuit() -> None:
    from dataclasses import replace

    from task_recursive_tree.task.kernel import TaskTreeKernel
    from task_recursive_tree.task.store import TaskTreeStore

    grid, observation = demo_world()
    application = build_application(grid, observation)
    semantics = AlwaysSatisfiedSemantics()
    decomposer = RecordingEmptyDecomposer()
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(application._services, semantics=semantics),
        decomposers={"RequiredStructure": decomposer},
        system_operations={},
        physical_skills={},
        repair_resolver=application.kernel._repair_resolver,
    )
    root = TaskNodeSpec(
        node_id="required-root",
        task_type="RequiredStructure",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
        execution_policy=ExecutionPolicy.REQUIRE_EXECUTION,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs((root,), root_id=root.node_id),
        )
    )

    assert kernel.run() is NodeStatus.SUCCEEDED
    assert decomposer.calls == 1
    assert store.runtime(root.node_id).expanded is True
    assert semantics.calls == ["preconditions", "postconditions"]


def test_default_execution_policy_short_circuits_satisfied_goal() -> None:
    from dataclasses import replace

    from task_recursive_tree.task.kernel import TaskTreeKernel
    from task_recursive_tree.task.store import TaskTreeStore

    grid, observation = demo_world()
    application = build_application(grid, observation)
    semantics = AlwaysSatisfiedSemantics()
    decomposer = RecordingEmptyDecomposer()
    store = TaskTreeStore()
    kernel = TaskTreeKernel(
        store=store,
        services=replace(application._services, semantics=semantics),
        decomposers={"GoalDriven": decomposer},
        system_operations={},
        physical_skills={},
        repair_resolver=application.kernel._repair_resolver,
    )
    root = TaskNodeSpec(
        node_id="goal-driven-root",
        task_type="GoalDriven",
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=NodeOrigin.COMPILER,
    )
    kernel.initialize(
        TaskTreeDefinition(
            root_id=root.node_id,
            delta=GraphDelta.from_specs((root,), root_id=root.node_id),
        )
    )

    assert kernel.run() is NodeStatus.SUCCEEDED
    assert decomposer.calls == 0
    assert store.runtime(root.node_id).expanded is False
    assert semantics.calls == ["goal"]
