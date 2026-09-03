from __future__ import annotations

from task_recursive_tree.bootstrap import TaskTreeApplication
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import NodeStatus


class LegacyHarnessFacade:
    """Small boundary for callers that still issue imperative place commands."""

    def __init__(self, application: TaskTreeApplication) -> None:
        self._application = application

    def place(
        self,
        *,
        object_id: str,
        object_kind: str,
        destination_id: str,
        destination_kind: str = "region",
        program_id: str | None = None,
    ) -> NodeStatus:
        program = TaskProgram.place(
            SpatialSelector.exact(object_id, object_kind),
            SpatialSelector.exact(destination_id, destination_kind),
            program_id=program_id,
        )
        return self._application.execute(program)

