from __future__ import annotations

from dataclasses import dataclass

from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import (
    ControlKind,
    GraphDelta,
    NodeOrigin,
    OperationKind,
    TaskNodeSpec,
)


@dataclass(frozen=True)
class TaskTreeDefinition:
    root_id: str
    delta: GraphDelta
    task_id: str | None = None


class TaskCompiler:
    """Validates TaskProgram and emits only the initial root task."""

    def compile(self, program: TaskProgram) -> TaskTreeDefinition:
        if program.action != "place":
            raise ValueError(f"Unsupported action: {program.action}")
        root_id = f"program/{program.program_id}"
        root = TaskNodeSpec(
            node_id=root_id,
            task_type="Place",
            operation_kind=OperationKind.DECOMPOSER,
            control_kind=ControlKind.SEQUENCE,
            origin=NodeOrigin.PROGRAM,
            parameters={
                "scope": program.program_id,
                "object_selector": program.arguments["object_selector"],
                "destination_selector": program.arguments[
                    "destination_selector"
                ],
                "repair_depth": 0,
            },
            max_attempts=1,
            max_repairs=0,
        )
        return TaskTreeDefinition(
            root_id=root_id,
            delta=GraphDelta.from_specs((root,), root_id=root_id),
            task_id=program.program_id,
        )
