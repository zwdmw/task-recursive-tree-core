from __future__ import annotations

from dataclasses import dataclass, field

from task_recursive_tree.artifacts import ArtifactStore
from task_recursive_tree.capabilities.planning import CapabilityRegistry
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.robot.simulated import SimulatedRobotBackend
from task_recursive_tree.runtime.effects import (
    EffectRunner,
    SynchronousEffectRunner,
)
from task_recursive_tree.runtime.harness import HarnessRuntime
from task_recursive_tree.selection.engine import SpatialSelectorEngine
from task_recursive_tree.skills.physical import default_physical_skills
from task_recursive_tree.task.compiler import TaskCompiler
from task_recursive_tree.runtime.freshness import ArtifactFreshnessChecker
from task_recursive_tree.task.contracts import (
    DecompositionContext,
    KernelServices,
    RepairContext,
    SkillContext,
    SystemOperationContext,
)
from task_recursive_tree.task.decomposers import (
    PickDecomposer,
    PlaceDecomposer,
    ReleaseDecomposer,
    RepairPlanDecomposer,
    RouteBlockedRepairDecomposer,
    TransferHeldDecomposer,
)
from task_recursive_tree.task.inspector import TaskTreeInspector
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.kernel import TaskTreeKernel
from task_recursive_tree.task.model import NodeStatus
from task_recursive_tree.task.operations import default_system_operations
from task_recursive_tree.task.repair import DefaultRepairResolver
from task_recursive_tree.task.store import TaskTreeStore
from task_recursive_tree.world.model import WorldModel
from task_recursive_tree.world.predicates import Verifier
from task_recursive_tree.world.state import GridMap, Observation


@dataclass(frozen=True)
class TaskTreeApplication:
    compiler: TaskCompiler
    kernel: TaskTreeKernel
    store: TaskTreeStore
    inspector: TaskTreeInspector
    world: WorldModel
    artifacts: ArtifactStore
    _runtime: HarnessRuntime = field(repr=False)
    _services: KernelServices = field(repr=False)
    _effect_runner: EffectRunner = field(repr=False)

    def execute(
        self,
        program: TaskProgram,
        *,
        max_steps: int = 10000,
    ) -> NodeStatus:
        definition = self.compiler.compile(program)
        self.kernel.initialize(definition)
        return self.kernel.run(max_steps=max_steps)

    def new_execution(self) -> TaskTreeApplication:
        """Create a clean task tree over the same live physical runtime."""
        return _build_execution(
            compiler=self.compiler,
            world=self.world,
            artifacts=self.artifacts,
            runtime=self._runtime,
            services=self._services,
            effect_runner=self._effect_runner,
        )

    def transaction_records(self):
        return self._runtime.transaction_records()


def build_application(
    grid: GridMap,
    initial_observation: Observation,
    *,
    robot_model: RobotModel | None = None,
    effect_runner: EffectRunner | None = None,
) -> TaskTreeApplication:
    model = robot_model or RobotModel()
    runner = (
        effect_runner
        if effect_runner is not None
        else SynchronousEffectRunner()
    )
    world = WorldModel(grid, initial_observation)
    artifacts = ArtifactStore()
    backend = SimulatedRobotBackend(model, grid, initial_observation)
    verifier = Verifier()
    runtime = HarnessRuntime(
        world=world,
        artifacts=artifacts,
        backend=backend,
        verifier=verifier,
        robot_model=model,
    )
    freshness = ArtifactFreshnessChecker()
    services = KernelServices(
        decomposition=DecompositionContext(),
        system=SystemOperationContext(
            world=world,
            artifacts=artifacts,
            selector=SpatialSelectorEngine(),
            capabilities=CapabilityRegistry.create(model),
            verifier=verifier,
            robot_model=model,
            freshness=freshness,
        ),
        skill=SkillContext(artifacts=artifacts),
        repair=RepairContext(world=world, artifacts=artifacts),
        runtime=runtime,
    )
    return _build_execution(
        compiler=TaskCompiler(),
        world=world,
        artifacts=artifacts,
        runtime=runtime,
        services=services,
        effect_runner=runner,
    )


def _build_execution(
    *,
    compiler: TaskCompiler,
    world: WorldModel,
    artifacts: ArtifactStore,
    runtime: HarnessRuntime,
    services: KernelServices,
    effect_runner: EffectRunner,
) -> TaskTreeApplication:
    store = TaskTreeStore()
    decomposers: dict[str, object] = {
        "Place": PlaceDecomposer(),
        "Pick": PickDecomposer(),
        "TransferHeld": TransferHeldDecomposer(),
        "Release": ReleaseDecomposer(),
        "RouteBlockedRepair": RouteBlockedRepairDecomposer(),
        "RepairPickPlan": RepairPlanDecomposer("PlanPick"),
        "RepairTransferPlan": RepairPlanDecomposer("PlanTransfer"),
    }
    kernel = TaskTreeKernel(
        store=store,
        services=services,
        decomposers=decomposers,
        system_operations=default_system_operations(),
        physical_skills=default_physical_skills(),
        repair_resolver=DefaultRepairResolver(),
        effect_runner=effect_runner,
    )
    return TaskTreeApplication(
        compiler=compiler,
        kernel=kernel,
        store=store,
        inspector=TaskTreeInspector(store),
        world=world,
        artifacts=artifacts,
        _runtime=runtime,
        _services=services,
        _effect_runner=effect_runner,
    )
