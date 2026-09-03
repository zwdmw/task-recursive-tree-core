from __future__ import annotations

from task_recursive_tree.robot.ports import (
    ActionRequest,
    ArmPathCommand,
    GripperCommand,
    NavigateCommand,
)
from task_recursive_tree.artifacts import (
    NavigationPlan,
    PickPlan,
    TransferPlan,
)
from task_recursive_tree.task.contracts import SkillContext
from task_recursive_tree.core.model import PredicateFormula
from task_recursive_tree.task.model import TaskNodeSpec


def _request_id(node: TaskNodeSpec, context: SkillContext) -> str:
    if context.request_id:
        return context.request_id
    return (
        f"tree:{context.task_id or 'standalone'}:{node.node_id}:"
        f"attempt:{max(1, context.attempt)}"
    )


class NavigateToPickStanceSkill:
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> ActionRequest:
        scope = str(node.parameters["scope"])
        pick = context.artifacts.resolve(scope, "pick_plan", PickPlan)
        navigation = context.artifacts.get(
            pick.navigation_plan_ref, NavigationPlan
        )
        return ActionRequest(
            request_id=_request_id(node, context),
            action_name=node.task_type,
            commands=(
                NavigateCommand(
                    navigation.path,
                    clearance_radius=navigation.clearance_radius,
                    ignore_entity_ids=navigation.ignored_entity_ids,
                ),
            ),
            resources=frozenset({"base"}),
            artifact_refs=(navigation.artifact_id, pick.artifact_id),
        )


class ExecuteGraspSkill:
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> ActionRequest:
        scope = str(node.parameters["scope"])
        pick = context.artifacts.resolve(scope, "pick_plan", PickPlan)
        return ActionRequest(
            request_id=_request_id(node, context),
            action_name=node.task_type,
            commands=(
                ArmPathCommand(pick.approach_path),
                GripperCommand(close=True, object_id=pick.object_id),
            ),
            resources=frozenset({"arm", "gripper"}),
            artifact_refs=(pick.artifact_id,),
            preconditions=(
                PredicateFormula(
                    "base_near",
                    {"target_pose": pick.base_stance, "tolerance": 0.2},
                ),
                PredicateFormula(
                    "joints_near",
                    {
                        "target_joints": pick.approach_path[0],
                        "tolerance": 1e-5,
                    },
                ),
            ),
        )


class MoveToTransportPostureSkill:
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> ActionRequest:
        scope = str(node.parameters["scope"])
        transfer = context.artifacts.resolve(
            scope, "transfer_plan", TransferPlan
        )
        return ActionRequest(
            request_id=_request_id(node, context),
            action_name=node.task_type,
            commands=(ArmPathCommand(transfer.to_transport_path),),
            resources=frozenset({"arm"}),
            artifact_refs=(transfer.artifact_id,),
            preconditions=(
                PredicateFormula(
                    "object_held", {"object_id": transfer.object_id}
                ),
            ),
        )


class NavigateHeldSkill:
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> ActionRequest:
        scope = str(node.parameters["scope"])
        transfer = context.artifacts.resolve(
            scope, "transfer_plan", TransferPlan
        )
        navigation = context.artifacts.get(
            transfer.navigation_plan_ref, NavigationPlan
        )
        return ActionRequest(
            request_id=_request_id(node, context),
            action_name=node.task_type,
            commands=(
                NavigateCommand(
                    navigation.path,
                    clearance_radius=navigation.clearance_radius,
                    ignore_entity_ids=navigation.ignored_entity_ids,
                ),
            ),
            resources=frozenset({"base"}),
            artifact_refs=(navigation.artifact_id, transfer.artifact_id),
            preconditions=(
                PredicateFormula(
                    "object_held", {"object_id": transfer.object_id}
                ),
                PredicateFormula(
                    "base_near",
                    {"target_pose": navigation.start, "tolerance": 0.2},
                ),
            ),
        )


class ExecuteReleaseSkill:
    def build_request(
        self, node: TaskNodeSpec, context: SkillContext
    ) -> ActionRequest:
        scope = str(node.parameters["scope"])
        transfer = context.artifacts.resolve(
            scope, "transfer_plan", TransferPlan
        )
        return ActionRequest(
            request_id=_request_id(node, context),
            action_name=node.task_type,
            commands=(
                ArmPathCommand(transfer.placement_path),
                GripperCommand(
                    close=False,
                    object_id=transfer.object_id,
                    release_pose=transfer.placement_pose,
                    yaw_tolerance=transfer.placement_yaw_tolerance,
                ),
            ),
            resources=frozenset({"arm", "gripper"}),
            artifact_refs=(transfer.artifact_id,),
            preconditions=(
                PredicateFormula(
                    "object_held", {"object_id": transfer.object_id}
                ),
                PredicateFormula(
                    "base_near",
                    {
                        "target_pose": transfer.destination_stance,
                        "tolerance": 0.2,
                    },
                ),
            ),
        )


def default_physical_skills() -> dict[str, object]:
    return {
        "NavigateToPickStance": NavigateToPickStanceSkill(),
        "ExecuteGrasp": ExecuteGraspSkill(),
        "MoveToTransportPosture": MoveToTransportPostureSkill(),
        "NavigateHeld": NavigateHeldSkill(),
        "ExecuteRelease": ExecuteReleaseSkill(),
    }
