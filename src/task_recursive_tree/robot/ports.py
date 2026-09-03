from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from task_recursive_tree.core.model import Diagnostic, PredicateFormula
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import (
    JointConfiguration,
    Observation,
)


@dataclass(frozen=True)
class NavigateCommand:
    path: tuple[Pose, ...]
    clearance_radius: float = 0.0
    ignore_entity_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ArmPathCommand:
    path: tuple[JointConfiguration, ...]


@dataclass(frozen=True)
class GripperCommand:
    close: bool
    object_id: str
    release_pose: Pose | None = None
    yaw_tolerance: float | None = None


RobotCommand = NavigateCommand | ArmPathCommand | GripperCommand


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    action_name: str
    commands: tuple[RobotCommand, ...]
    resources: frozenset[str]
    artifact_refs: tuple[str, ...] = ()
    preconditions: tuple[PredicateFormula, ...] = ()


@dataclass(frozen=True)
class ExecutionReceipt:
    request_id: str
    transaction_id: str
    snapshot_ref: str
    command_count: int


class RobotExecutionError(RuntimeError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class RobotBackend(Protocol):
    @property
    def supports_rollback(self) -> bool: ...

    def checkpoint(self) -> Any: ...

    def restore(self, checkpoint: Any) -> None: ...

    def execute(self, command: RobotCommand) -> None: ...

    def observe(self) -> Observation: ...
