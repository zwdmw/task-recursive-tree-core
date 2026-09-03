from __future__ import annotations

import pytest

from task_recursive_tree.artifacts import ArtifactStore
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.robot.ports import (
    ActionRequest,
    NavigateCommand,
)
from task_recursive_tree.robot.simulated import SimulatedRobotBackend
from task_recursive_tree.runtime.harness import ExecutionFailure, HarnessRuntime
from task_recursive_tree.runtime.transactions import (
    TransactionLedger,
    TransactionStatus,
)
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.model import WorldModel
from task_recursive_tree.world.predicates import Verifier
from task_recursive_tree.world.state import GridMap, Observation, RobotState


class FailingCommitLedger(TransactionLedger):
    def commit(self, transaction_id: str):
        raise RuntimeError("injected commit failure")


class InterruptingBackend(SimulatedRobotBackend):
    def execute(self, command) -> None:
        super().execute(command)
        raise SystemExit("injected backend interruption")


def test_commit_failure_reconciles_world_and_backend() -> None:
    model = RobotModel()
    grid = GridMap(4, 4)
    initial = Observation(
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0)),
        entities=(),
    )
    world = WorldModel(grid, initial)
    backend = SimulatedRobotBackend(model, grid, initial)
    ledger = FailingCommitLedger()
    runtime = HarnessRuntime(
        world=world,
        artifacts=ArtifactStore(),
        backend=backend,
        verifier=Verifier(),
        robot_model=model,
        ledger=ledger,
    )
    request = ActionRequest(
        request_id="commit-failure",
        action_name="Navigate",
        commands=(
            NavigateCommand(
                (Pose(0.5, 0.5), Pose(1.5, 0.5)),
                clearance_radius=model.base_radius,
            ),
        ),
        resources=frozenset({"base"}),
    )

    with pytest.raises(ExecutionFailure) as raised:
        runtime.execute(request)

    assert raised.value.diagnostic.code == "OUTCOME_UNKNOWN"
    assert world.snapshot().robot.base_pose == Pose(0.5, 0.5)
    assert backend.observe().robot.base_pose == Pose(0.5, 0.5)
    assert ledger.records()[0].status is TransactionStatus.ROLLED_BACK


def test_base_exception_reconciles_started_transaction_before_propagating() -> None:
    model = RobotModel()
    grid = GridMap(4, 4)
    initial = Observation(
        robot=RobotState(Pose(0.5, 0.5), (0.0, 0.0)),
        entities=(),
    )
    world = WorldModel(grid, initial)
    backend = InterruptingBackend(model, grid, initial)
    ledger = TransactionLedger()
    runtime = HarnessRuntime(
        world=world,
        artifacts=ArtifactStore(),
        backend=backend,
        verifier=Verifier(),
        robot_model=model,
        ledger=ledger,
    )
    request = ActionRequest(
        request_id="base-exception",
        action_name="Navigate",
        commands=(
            NavigateCommand(
                (Pose(0.5, 0.5), Pose(1.5, 0.5)),
                clearance_radius=model.base_radius,
            ),
        ),
        resources=frozenset({"base"}),
    )

    with pytest.raises(SystemExit, match="backend interruption"):
        runtime.execute(request)

    assert world.snapshot().robot.base_pose == Pose(0.5, 0.5)
    assert backend.observe().robot.base_pose == Pose(0.5, 0.5)
    records = ledger.records()
    assert len(records) == 1
    assert records[0].status is TransactionStatus.ROLLED_BACK
