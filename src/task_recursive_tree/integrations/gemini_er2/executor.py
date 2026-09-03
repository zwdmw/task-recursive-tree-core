from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from task_recursive_tree.task.compiler import TaskTreeDefinition
from task_recursive_tree.task.contracts import (
    DecompositionContext,
    KernelServices,
    RepairContext,
    SkillContext,
    SystemOperationContext,
)
from task_recursive_tree.task.kernel import TaskTreeKernel
from task_recursive_tree.task.model import (
    Diagnostic,
    KernelLimits,
    NodeStatus,
    TickResult,
)
from task_recursive_tree.task.store import TaskTreeStore

from .artifacts import HarnessArtifactBridge
from .compiler import KernelCompiledTree
from .decomposition import HarnessBuiltinTaskDecomposerAdapter
from .physical_runtime import HarnessPhysicalGateway
from .semantics import GeminiER2NodeSemantics
from .translation import translate_harness_tree
from .tree_view import KernelTreeProjection


class _HarnessWorldFacade:
    """Expose the minimal snapshot protocol required by the kernel context."""

    def __init__(self, world: Any) -> None:
        self._world = world

    def snapshot(self) -> Any:
        snapshot = getattr(self._world, "snapshot", None)
        if callable(snapshot):
            return snapshot()
        freeze = getattr(self._world, "freeze_snapshot", None)
        if callable(freeze):
            return freeze()
        return self._world


class _HarnessVerifierFacade:
    def evaluate(self, formula: Any, snapshot: Any) -> Any:
        del formula, snapshot
        raise RuntimeError(
            "Translated GeminiER2 nodes must use GeminiER2NodeSemantics"
        )


class KernelBudgetFacade:
    """Harness-compatible dynamic view over kernel tick accounting."""

    def __init__(
        self,
        kernel: TaskTreeKernel,
        config: Mapping[str, Any],
    ) -> None:
        self._kernel = kernel
        self._config = dict(config)

    @property
    def ticks(self) -> int:
        return self._kernel.ticks

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticks": self.ticks,
            **{
                key: copy.deepcopy(value)
                for key, value in self._config.items()
                if key.startswith("max_")
            },
        }


class KernelExecutorBridge:
    """Harness executor protocol implemented by the new task-tree kernel."""

    def __init__(
        self,
        *,
        tree: Any,
        runtime: Any,
        logger: Any = None,
        config: Mapping[str, Any] | None = None,
        harness_root: str | Path | None = None,
        decomposer: Any = None,
        system_operation: Any = None,
        physical_skill: Any = None,
        repair_resolver: Any = None,
        physical_gateway: Any = None,
    ) -> None:
        self.runtime = runtime
        self.logger = logger or getattr(runtime, "logger", None)
        self.config = dict(config or {})
        budget_config = dict(self.config.get("budget") or {})
        self.limits = KernelLimits(
            max_node_attempts=int(
                budget_config.get(
                    "max_node_attempts",
                    self.config.get("max_node_attempts", 3),
                )
            ),
            max_reconciliations=int(
                budget_config.get(
                    "max_reconciliations",
                    self.config.get("max_reconciliations", 16),
                )
            ),
        )
        self.harness_root = (
            str(Path(harness_root).resolve())
            if harness_root is not None
            else None
        )

        definition, source = _definition_and_source(tree)
        self.store = TaskTreeStore()
        task_id = (
            definition.task_id
            or getattr(source, "task_id", None)
            or definition.root_id
        )
        _set_task_context(runtime, task_id)

        self.artifacts = HarnessArtifactBridge(
            runtime=runtime,
            store=self.store,
            task_id=str(task_id) if task_id is not None else None,
            harness_root=self.harness_root,
        )
        self.tree = KernelTreeProjection(
            self.store,
            task_id=str(task_id) if task_id is not None else None,
            program_ref=getattr(source, "program_ref", None),
            world_revision=lambda: getattr(runtime.world, "revision", None),
            metadata={
                "executor_bridge": "gemini_er2_kernel/1.0",
                "authoritative_store": "TaskTreeStore",
            },
            runtime_details=self._runtime_details,
        )

        if decomposer is None:
            decomposer = HarnessBuiltinTaskDecomposerAdapter(
                runtime=runtime,
                task_id=str(task_id) if task_id is not None else None,
                tree=self.tree,
                config=self.config,
                harness_root=self.harness_root,
            )
        if physical_gateway is None:
            physical_gateway = HarnessPhysicalGateway(
                runtime=runtime,
                artifact_bridge=self.artifacts,
                harness_root=self.harness_root,
            )
        if system_operation is None:
            from .operations import HarnessSystemOperationAdapter

            system_operation = _construct(
                HarnessSystemOperationAdapter,
                runtime=runtime,
                store=self.store,
                artifacts=self.artifacts,
                task_id=task_id,
                config=self.config,
                harness_root=self.harness_root,
            )
        if physical_skill is None:
            from .skill import HarnessMacroActionSkill

            physical_skill = _construct(
                HarnessMacroActionSkill,
                runtime=runtime,
                store=self.store,
                artifacts=self.artifacts,
                artifact_bridge=self.artifacts,
                task_id=task_id,
                config=self.config,
                harness_root=self.harness_root,
            )
        if repair_resolver is None:
            from .repair import HarnessRepairResolver

            repair_resolver = _construct(
                HarnessRepairResolver,
                runtime=runtime,
                store=self.store,
                tree=self.tree,
                artifacts=self.artifacts,
                task_id=task_id,
                config=self.config,
                harness_root=self.harness_root,
            )

        world_facade = _HarnessWorldFacade(runtime.world)
        services = KernelServices(
            decomposition=DecompositionContext(),
            system=SystemOperationContext(
                world=world_facade,
                artifacts=self.artifacts,
                selector=None,
                capabilities=None,
                verifier=_HarnessVerifierFacade(),
                robot_model=None,
                freshness=None,
            ),
            skill=SkillContext(artifacts=self.artifacts),
            repair=RepairContext(
                world=world_facade,
                artifacts=self.artifacts,
            ),
            runtime=physical_gateway,
            semantics=GeminiER2NodeSemantics(runtime=runtime),
        )
        self._physical_gateway = physical_gateway
        self.kernel = TaskTreeKernel(
            store=self.store,
            services=services,
            decomposers={"*": decomposer},
            system_operations={"*": system_operation},
            physical_skills={"*": physical_skill},
            repair_resolver=repair_resolver,
            limits=self.limits,
        )
        self.kernel.initialize(definition)
        self.budget = KernelBudgetFacade(self.kernel, self.config)
        self._layout_reservations_closed = False
        self._completion_finalized = False

    @property
    def paused(self) -> bool:
        return self.kernel.paused

    @property
    def pause_reason(self) -> str | None:
        return self.kernel.pause_reason

    @property
    def diagnostics(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(dict(item)) for item in self.kernel.diagnostics]

    @property
    def completion_audit(self) -> dict[str, Any] | None:
        value = self.store.runtime(
            self.tree.root_id
        ).adapter_state.get("completion_audit")
        return (
            copy.deepcopy(dict(value))
            if isinstance(value, Mapping)
            else None
        )

    @property
    def completion_receipt(self) -> dict[str, Any] | None:
        value = self.store.runtime(
            self.tree.root_id
        ).adapter_state.get("completion_receipt")
        return (
            copy.deepcopy(dict(value))
            if isinstance(value, Mapping)
            else None
        )

    def tick(self) -> TickResult:
        result = self.kernel.tick()
        if (
            result.terminal
            and result.status == "SUCCEEDED"
            and not self._completion_finalized
        ):
            completion = self._ensure_task_completion(
                source="kernel_root_succeeded"
            )
            if completion["required"] and not completion["ok"]:
                diagnostic = Diagnostic(
                    code="TASK_COMPLETION_PROTOCOL_FAILED",
                    message=str(
                        completion.get("message")
                        or "task completion could not be committed"
                    ),
                    details=copy.deepcopy(completion),
                    repairable=True,
                )
                self.kernel.reject_after_completion_verification(
                    diagnostic,
                    audit=completion.get("completion_audit"),
                )
                result = TickResult(
                    status="BLOCKED",
                    node_id=self.tree.root_id,
                    phase="terminal",
                    changed=True,
                    terminal=True,
                    message=diagnostic.message,
                    diagnostic_id=diagnostic.code,
                )
            else:
                if completion["required"]:
                    self.kernel.record_task_completion_commit(
                        completion["completion_audit"],
                        completion_receipt=completion.get(
                            "completion_receipt"
                        ),
                        source=str(completion["source"]),
                    )
                self._completion_finalized = True
        if result.terminal:
            self._close_layout_reservations(
                reason=f"task_{result.status.casefold()}"
            )
        return result

    def pause(self, reason: str = "paused") -> None:
        self.kernel.pause(reason)

    def resume(self) -> None:
        self.kernel.resume()

    def cancel(self, reason: str = "cancelled") -> TickResult:
        active_request_id = next(
            (
                str(state.adapter_state["active_request_id"])
                for state in reversed(list(self.store.runtimes().values()))
                if state.adapter_state.get("active_request_id")
            ),
            None,
        )
        if active_request_id is not None:
            cancel = getattr(self._physical_gateway, "cancel", None)
            if callable(cancel):
                cancel(active_request_id)
        result = self.kernel.cancel(reason)
        self._close_layout_reservations(reason=f"task_cancelled:{reason}")
        return result

    def resume_after_external_recovery(
        self,
        recovery_ref: str | None = None,
    ) -> None:
        stack = self.store.stack()
        if stack:
            consumed = self.artifacts.consumed(stack[-1].node_id)
            self.artifacts.invalidate(
                tuple(
                    str(item["artifact_ref"])
                    for item in consumed
                    if item.get("artifact_ref")
                )
            )
        self.kernel.resume_after_external_recovery(recovery_ref)

    def promote_after_external_goal_verification(
        self,
        audit: Mapping[str, Any],
        *,
        recovery_ref: str | None = None,
    ) -> bool:
        root_runtime = self.store.runtime(self.tree.root_id)
        if (
            audit.get("verified") is not True
            or not root_runtime.status.terminal
        ):
            return False
        completion = self._ensure_task_completion(
            audit=audit,
            source="external_goal_verification",
        )
        if completion["required"] and not completion["ok"]:
            return False
        promoted = self.kernel.promote_after_external_goal_verification(
            audit,
            recovery_ref=recovery_ref,
            completion_receipt=completion.get("completion_receipt"),
        )
        if promoted:
            self._completion_finalized = True
            self._close_layout_reservations(
                reason="task_succeeded_after_external_verification"
            )
        return promoted

    def snapshot(self) -> dict[str, Any]:
        root_status = self.store.runtime(self.tree.root_id).status
        return {
            "schema": "kernel_executor_bridge/1.0",
            "task_id": self.tree.task_id,
            "tree": self.tree.to_dict(),
            "kernel": self.kernel.snapshot(),
            "budget": self.budget.to_dict(),
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "diagnostics": self.diagnostics,
            "completion_audit": self.completion_audit,
            "completion_receipt": self.completion_receipt,
            "terminal_status": (
                self.tree.node(self.tree.root_id).status
                if root_status.terminal
                else None
            ),
        }

    def save_snapshot(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                self.snapshot(),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def _runtime_details(self, node_id: str) -> dict[str, Any]:
        return {
            "published_artifacts": self.artifacts.published(node_id),
            "consumed_artifacts": self.artifacts.consumed(node_id),
        }

    def _ensure_task_completion(
        self,
        *,
        audit: Mapping[str, Any] | None = None,
        source: str,
    ) -> dict[str, Any]:
        task_id = self.tree.task_id
        tasks = getattr(getattr(self.runtime, "world", None), "tasks", {})
        task = (
            tasks.get(task_id)
            if task_id is not None and hasattr(tasks, "get")
            else None
        )
        required = (
            task is not None
            and getattr(task, "execution_mode", None) == "tree"
            and str(getattr(task, "tree_root_id", "") or "")
            == str(self.tree.root_id)
        )
        result: dict[str, Any] = {
            "required": required,
            "ok": not required,
            "source": str(source),
            "task_id": task_id,
            "tree_root_id": self.tree.root_id,
        }
        if not required:
            return result

        _set_task_context(self.runtime, task_id)
        if getattr(task, "state", None) == "completed":
            receipt = self._existing_completion_receipt(task)
            result.update({
                "ok": receipt is not None,
                "completion_audit": (
                    copy.deepcopy(dict(audit))
                    if isinstance(audit, Mapping)
                    else {"verified": True, "source": "existing_receipt"}
                ),
                "completion_receipt": receipt,
                "message": (
                    None
                    if receipt is not None
                    else "completed task has no completion receipt"
                ),
            })
            return result
        if getattr(task, "state", None) != "active":
            result["message"] = (
                "task completion requires active state; "
                f"observed {getattr(task, 'state', None)!r}"
            )
            return result

        required_methods = (
            "completion_audit",
            "issue_tree_completion_claim",
            "commit_task_completion",
            "validate_completion_receipt",
        )
        missing = [
            name for name in required_methods
            if not callable(getattr(self.runtime, name, None))
        ]
        if missing:
            result["message"] = (
                "task completion runtime methods are unavailable: "
                + ", ".join(missing)
            )
            return result

        try:
            completion_audit = (
                dict(audit)
                if isinstance(audit, Mapping)
                else dict(self.runtime.completion_audit(str(task_id)) or {})
            )
        except Exception as exc:
            result["message"] = f"completion audit failed: {exc}"
            result["error_type"] = type(exc).__name__
            return result
        result["completion_audit"] = copy.deepcopy(completion_audit)
        if completion_audit.get("verified") is not True:
            result["message"] = "completion audit did not verify the task"
            return result

        expected_task_version = int(getattr(task, "revision", 0))
        finalizer_node_id = (
            f"{self.tree.root_id}/runtime-finish-task"
        )
        request_id = (
            f"kernel:{task_id}:{self.tree.root_id}:finish-task"
        )
        claim_ref: str | None = None
        try:
            claim = self.runtime.issue_tree_completion_claim(
                task_id=str(task_id),
                tree_root_id=str(self.tree.root_id),
                finalizer_node_id=finalizer_node_id,
                request_id=request_id,
                expected_task_version=expected_task_version,
            )
            claim_ref = str(claim.get("claim_ref") or "")
            if not claim_ref:
                raise RuntimeError("completion claim has no claim_ref")
            commit = dict(self.runtime.commit_task_completion(
                task_id=str(task_id),
                request_id=request_id,
                expected_task_version=expected_task_version,
                tree_root_id=str(self.tree.root_id),
                finalizer_node_id=finalizer_node_id,
                completion_claim_ref=claim_ref,
                audit=copy.deepcopy(completion_audit),
            ) or {})
        except Exception as exc:
            self._revoke_completion_claim(claim_ref, str(exc))
            result["message"] = f"completion commit failed: {exc}"
            result["error_type"] = type(exc).__name__
            return result
        if commit.get("ok") is not True:
            message = str(
                commit.get("message") or "completion commit was rejected"
            )
            self._revoke_completion_claim(claim_ref, message)
            result.update({
                "message": message,
                "failure_code": commit.get("failure_code"),
            })
            return result

        receipt = commit.get("receipt")
        try:
            valid, reason = self.runtime.validate_completion_receipt(
                copy.deepcopy(receipt),
                task_id=str(task_id),
                tree_root_id=str(self.tree.root_id),
                finalizer_node_id=finalizer_node_id,
                request_id=request_id,
            )
        except Exception as exc:
            result["message"] = (
                f"completion receipt validation failed: {exc}"
            )
            result["error_type"] = type(exc).__name__
            return result
        if valid is not True:
            result["message"] = str(
                reason or "completion receipt was rejected"
            )
            return result

        result.update({
            "ok": True,
            "completion_receipt": copy.deepcopy(dict(receipt)),
            "finalizer_node_id": finalizer_node_id,
            "request_id": request_id,
        })
        return result

    @staticmethod
    def _existing_completion_receipt(task: Any) -> dict[str, Any] | None:
        artifacts = getattr(task, "artifacts", {})
        values = (
            artifacts.values()
            if hasattr(artifacts, "values")
            else ()
        )
        for value in values:
            if (
                isinstance(value, Mapping)
                and value.get("schema") == "task_completion_receipt/1.0"
            ):
                return copy.deepcopy(dict(value))
        return None

    def _revoke_completion_claim(
        self,
        claim_ref: str | None,
        reason: str,
    ) -> None:
        revoke = getattr(
            self.runtime, "revoke_tree_completion_claim", None
        )
        if claim_ref and callable(revoke):
            try:
                revoke(claim_ref, reason=str(reason))
            except Exception:
                pass

    def _close_layout_reservations(self, *, reason: str) -> None:
        if self._layout_reservations_closed:
            return
        self.artifacts.release_layout_reservations(reason=reason)
        self._layout_reservations_closed = True


def _definition_and_source(
    tree: Any,
) -> tuple[TaskTreeDefinition, Any]:
    if isinstance(tree, KernelCompiledTree):
        return tree.definition, tree
    if isinstance(tree, TaskTreeDefinition):
        return tree, tree
    definition = getattr(tree, "definition", None)
    if isinstance(definition, TaskTreeDefinition):
        return definition, tree
    return translate_harness_tree(tree), tree


def _set_task_context(runtime: Any, task_id: Any) -> None:
    if task_id is None:
        return
    set_context = getattr(runtime, "set_task_context", None)
    if not callable(set_context):
        return
    try:
        set_context(str(task_id))
    except (AttributeError, KeyError, RuntimeError):
        pass


def _construct(factory: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(factory)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    supplied = (
        kwargs
        if accepts_kwargs
        else {
            key: value
            for key, value in kwargs.items()
            if key in signature.parameters
        }
    )
    return factory(**supplied)


__all__ = [
    "KernelBudgetFacade",
    "KernelExecutorBridge",
]
