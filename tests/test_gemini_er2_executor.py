from __future__ import annotations

import inspect
from types import SimpleNamespace

from task_recursive_tree.integrations.gemini_er2.compiler import (
    KernelCompiledTree,
)
from task_recursive_tree.integrations.gemini_er2.executor import (
    KernelExecutorBridge,
)
from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.model import (
    ControlKind,
    GraphDelta,
    NodeOrigin,
    NodeOutcome,
    OperationKind,
    TaskNodeSpec,
)


class FakeWorld:
    def __init__(self) -> None:
        self.revision = 3
        self.tasks = {
            "task-1": SimpleNamespace(artifacts={}),
        }

    def freeze_snapshot(self):
        return {"snapshot": {"id": "scene-1", "revision": self.revision}}


class FakeRuntime:
    def __init__(self) -> None:
        self.world = FakeWorld()
        self.logger = None
        self.contexts = []

    def set_task_context(self, task_id):
        self.contexts.append(task_id)


class CompletionRuntime(FakeRuntime):
    def __init__(self, *, verified: bool = True) -> None:
        super().__init__()
        self.verified = verified
        self.calls: list[str] = []
        self.world.tasks["task-1"] = SimpleNamespace(
            id="task-1",
            revision=5,
            state="active",
            phase="tree_compiled",
            execution_mode="tree",
            tree_root_id="root",
            artifacts={},
        )

    def completion_audit(self, task_id):
        self.calls.append("completion_audit")
        task = self.world.tasks[task_id]
        return {
            "verified": self.verified,
            "task_revision": task.revision,
            "world_revision": self.world.revision,
            "completion_contract_digest": "digest-1",
        }

    def issue_tree_completion_claim(self, **kwargs):
        self.calls.append("issue_tree_completion_claim")
        return {
            "schema": "tree_completion_claim/1.0",
            "claim_ref": "claim-1",
            **kwargs,
        }

    def commit_task_completion(self, **kwargs):
        self.calls.append("commit_task_completion")
        task = self.world.tasks[kwargs["task_id"]]
        receipt = {
            "schema": "task_completion_receipt/1.0",
            "receipt_ref": "receipt-1",
            "task_id": kwargs["task_id"],
            "tree_root_id": kwargs["tree_root_id"],
            "finalizer_node_id": kwargs["finalizer_node_id"],
            "request_id": kwargs["request_id"],
            "post_task_revision": task.revision + 1,
        }
        task.artifacts[receipt["receipt_ref"]] = receipt
        task.state = "completed"
        task.phase = "completion_committed"
        task.revision += 1
        self.world.revision += 1
        return {
            "ok": True,
            "receipt": receipt,
            "scene_revision": self.world.revision,
        }

    def validate_completion_receipt(self, receipt, **kwargs):
        self.calls.append("validate_completion_receipt")
        task = self.world.tasks[kwargs["task_id"]]
        valid = (
            receipt == task.artifacts.get(receipt.get("receipt_ref"))
            and task.state == "completed"
            and all(
                str(receipt.get(key)) == str(value)
                for key, value in kwargs.items()
            )
        )
        return valid, "" if valid else "receipt mismatch"

    def revoke_tree_completion_claim(self, claim_ref, *, reason):
        del claim_ref, reason
        self.calls.append("revoke_tree_completion_claim")


class SuccessfulSystemOperation:
    def run(self, node, context):
        del node, context
        return NodeOutcome.success(result={"message": "checked"})


class UnusedSkill:
    def build_request(self, node, context):
        raise AssertionError((node, context))


class UnusedGateway:
    def execute(self, request):
        raise AssertionError(request)


class NoRepair:
    def propose(self, node, diagnostic, context, repair_index):
        del node, diagnostic, context, repair_index
        return None


def _definition() -> TaskTreeDefinition:
    root = TaskNodeSpec(
        node_id="root",
        task_type="inspect",
        operation_kind=OperationKind.SYSTEM,
        control_kind=ControlKind.LEAF,
        origin=NodeOrigin.COMPILER,
        parameters={
            "__gemini_er2__": {
                "root_id": "root",
                "node_kind": "observation",
                "task_type": "inspect",
                "tool_ref": "inspect_entities",
                "goal": {},
                "preconditions": {},
                "metadata": {},
                "tree": {
                    "task_id": "task-1",
                    "program_ref": "program-1",
                },
            }
        },
    )
    return TaskTreeDefinition(
        root_id="root",
        task_id="task-1",
        delta=GraphDelta.from_specs((root,), root_id="root"),
    )


def _executor(tree, *, runtime=None):
    return KernelExecutorBridge(
        tree=tree,
        runtime=runtime or FakeRuntime(),
        system_operation=SuccessfulSystemOperation(),
        physical_skill=UnusedSkill(),
        repair_resolver=NoRepair(),
        physical_gateway=UnusedGateway(),
    )


def test_executor_accepts_compiled_tree_and_exposes_session_protocol() -> None:
    compiled = KernelCompiledTree(
        definition=_definition(),
        task_id="task-1",
        program_ref="program-1",
        world_revision=3,
    )
    executor = _executor(compiled)

    for _ in range(8):
        tick = executor.tick()
        if tick.terminal:
            break

    assert tick.status == "SUCCEEDED"
    assert executor.tree.node("root").status == "SUCCEEDED"
    assert executor.budget.ticks > 0
    assert executor.paused is False
    snapshot = executor.snapshot()
    assert snapshot["schema"] == "kernel_executor_bridge/1.0"
    assert snapshot["tree"]["root_id"] == "root"
    assert snapshot["tree"]["nodes"]["root"]["runtime"]["last_result"] == {
        "message": "checked"
    }


def test_executor_commits_world_task_before_reporting_root_success() -> None:
    runtime = CompletionRuntime()
    executor = _executor(_definition(), runtime=runtime)

    for _ in range(8):
        tick = executor.tick()
        if tick.terminal:
            break

    task = runtime.world.tasks["task-1"]
    snapshot = executor.snapshot()
    assert tick.status == "SUCCEEDED"
    assert task.state == "completed"
    assert task.phase == "completion_committed"
    assert snapshot["completion_audit"]["verified"] is True
    assert snapshot["completion_receipt"]["schema"] == \
        "task_completion_receipt/1.0"
    assert runtime.calls == [
        "completion_audit",
        "issue_tree_completion_claim",
        "commit_task_completion",
        "validate_completion_receipt",
    ]
    assert any(
        event.event_type == "task_completion_committed"
        for event in executor.store.events()
    )


def test_executor_blocks_success_when_completion_audit_fails() -> None:
    runtime = CompletionRuntime(verified=False)
    executor = _executor(_definition(), runtime=runtime)

    for _ in range(8):
        tick = executor.tick()
        if tick.terminal:
            break

    task = runtime.world.tasks["task-1"]
    root = executor.store.runtime("root")
    assert tick.status == "BLOCKED"
    assert executor.tree.node("root").status == "BLOCKED"
    assert task.state == "active"
    assert task.phase == "tree_compiled"
    assert root.adapter_state["completion_audit"]["verified"] is False
    assert root.last_diagnostic.code == "TASK_COMPLETION_PROTOCOL_FAILED"
    assert runtime.calls == ["completion_audit"]


def test_executor_accepts_harness_tree_and_supports_pause_cancel() -> None:
    tree = SimpleNamespace(
        root_id="root",
        task_id="task-1",
        program_ref="program-1",
        world_revision=3,
        metadata={},
        nodes={
            "root": {
                "spec": {
                    "node_id": "root",
                    "root_id": "root",
                    "task_type": "inspect",
                    "node_kind": "observation",
                    "tool_ref": "inspect_entities",
                    "params": {},
                    "preconditions": {},
                    "goal": {},
                    "children": [],
                }
            }
        },
    )
    executor = _executor(tree)

    executor.pause("operator requested stop")
    assert executor.tick().status == "PAUSED"
    assert executor.pause_reason == "operator requested stop"
    executor.resume()
    result = executor.cancel("superseded")

    assert result.status == "CANCELLED"
    assert result.terminal is True
    assert executor.tree.node("root").status == "CANCELLED"


def test_executor_default_adapter_wiring_uses_shared_artifact_bridge() -> None:
    executor = KernelExecutorBridge(
        tree=_definition(),
        runtime=FakeRuntime(),
    )

    physical_skill = executor.kernel._physical_skills["*"]
    assert physical_skill.artifact_bridge is executor.artifacts
    assert executor._physical_gateway._artifact_bridge is executor.artifacts
    assert executor.kernel._system_operations["*"].artifacts is \
        executor.artifacts
    assert executor.kernel._repair_resolver.runtime is executor.runtime


def test_executor_bridge_injects_kernel_limits_from_config() -> None:
    executor = KernelExecutorBridge(
        tree=_definition(),
        runtime=FakeRuntime(),
        config={
            "max_node_attempts": 5,
            "max_reconciliations": 7,
        },
        system_operation=SuccessfulSystemOperation(),
        physical_skill=UnusedSkill(),
        repair_resolver=NoRepair(),
        physical_gateway=UnusedGateway(),
    )

    assert executor.limits.max_node_attempts == 5
    assert executor.limits.max_reconciliations == 7
    assert executor.kernel.limits is executor.limits


def test_executor_bridge_promotes_verified_external_completion() -> None:
    executor = _executor(_definition())
    executor.cancel("primary route blocked")

    promoted = executor.promote_after_external_goal_verification(
        {
            "verified": True,
            "formula_value": "true",
            "semantic_value": "true",
        },
        recovery_ref="recovery/attempt-3",
    )

    assert promoted is True
    assert executor.tree.node("root").status == "SUCCEEDED"
    assert executor.tree.node("root").runtime.failure is None
    assert executor.snapshot()["terminal_status"] == "SUCCEEDED"


def test_external_promotion_commits_world_task_and_receipt() -> None:
    runtime = CompletionRuntime()
    executor = _executor(_definition(), runtime=runtime)
    executor.cancel("primary route blocked")
    audit = runtime.completion_audit("task-1")

    promoted = executor.promote_after_external_goal_verification(
        audit,
        recovery_ref="recovery/attempt-3",
    )

    task = runtime.world.tasks["task-1"]
    root = executor.store.runtime("root")
    assert promoted is True
    assert task.state == "completed"
    assert task.phase == "completion_committed"
    assert root.status.value == "succeeded"
    assert root.adapter_state["completion_receipt"]["receipt_ref"] == \
        "receipt-1"
    assert root.adapter_state[
        "last_external_goal_verification"
    ]["completion_receipt"]["receipt_ref"] == "receipt-1"
    assert any(
        event.event_type == "external_goal_verification_promoted"
        for event in executor.store.events()
    )


def test_executor_bridge_does_not_reference_legacy_executor() -> None:
    import task_recursive_tree.integrations.gemini_er2.executor as module

    source = inspect.getsource(module)
    assert "TreeExecutor" not in source
    assert "tree_executor" not in source
