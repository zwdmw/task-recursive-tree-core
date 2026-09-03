from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src" / "task_recursive_tree"


def imports_under(package: str) -> set[str]:
    imported: set[str] = set()
    for path in (ROOT / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                imported.update(alias.name for alias in item.names)
            elif isinstance(item, ast.ImportFrom) and item.module:
                imported.add(item.module)
    return imported


def test_selection_does_not_depend_on_planning_or_control() -> None:
    imported = imports_under("selection")
    forbidden = (
        "task_recursive_tree.capabilities",
        "task_recursive_tree.robot",
        "task_recursive_tree.runtime",
        "task_recursive_tree.skills",
    )
    assert not any(
        module.startswith(prefix)
        for module in imported
        for prefix in forbidden
    )


def test_world_does_not_depend_on_planning_or_physical_execution() -> None:
    imported = imports_under("world")
    forbidden = (
        "task_recursive_tree.capabilities",
        "task_recursive_tree.robot",
        "task_recursive_tree.runtime.harness",
        "task_recursive_tree.skills",
    )
    assert not any(
        module.startswith(prefix)
        for module in imported
        for prefix in forbidden
    )


def test_skills_depend_on_backend_ports_not_backend_implementations() -> None:
    imported = imports_under("skills")
    assert "task_recursive_tree.robot.ports" in imported
    assert not any(
        module.startswith("task_recursive_tree.robot.simulated")
        for module in imported
    )


def test_capabilities_do_not_depend_on_task_or_runtime_packages() -> None:
    imported = imports_under("capabilities")
    assert not any(
        module.startswith("task_recursive_tree.task")
        or module.startswith("task_recursive_tree.runtime")
        for module in imported
    )


def test_role_contexts_do_not_expose_physical_execution() -> None:
    from dataclasses import fields

    from task_recursive_tree.bootstrap import TaskTreeApplication
    from task_recursive_tree.task.contracts import (
        DecompositionContext,
        RepairContext,
        SkillContext,
        SystemOperationContext,
    )

    assert fields(DecompositionContext) == ()
    assert {item.name for item in fields(SkillContext)} == {
        "artifacts",
        "task_id",
        "node_id",
        "attempt",
        "request_id",
    }
    assert "runtime" not in {
        item.name for item in fields(SkillContext)
    }
    assert "backend" not in {
        item.name for item in fields(SkillContext)
    }
    assert {item.name for item in fields(RepairContext)} == {
        "world",
        "artifacts",
    }
    assert "runtime" not in {
        item.name for item in fields(SystemOperationContext)
    }
    assert "backend" not in {
        item.name for item in fields(TaskTreeApplication)
    }


def test_new_execution_reuses_injected_effect_runner() -> None:
    from task_recursive_tree.bootstrap import build_application
    from task_recursive_tree.demo import demo_world
    from task_recursive_tree.runtime.effects import (
        SynchronousEffectRunner,
    )

    grid, observation = demo_world()
    runner = SynchronousEffectRunner()
    application = build_application(
        grid,
        observation,
        effect_runner=runner,
    )
    fresh_execution = application.new_execution()

    assert application._effect_runner is runner
    assert application.kernel._effect_runner is runner
    assert fresh_execution._effect_runner is runner
    assert fresh_execution.kernel._effect_runner is runner
    runner.close()
