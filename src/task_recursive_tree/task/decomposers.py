from __future__ import annotations

from dataclasses import dataclass

from task_recursive_tree.task.contracts import DecompositionContext
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    GraphDelta,
    NodeOrigin,
    OperationKind,
    TaskEdge,
    TaskNodeSpec,
)


def _leaf(
    parent: TaskNodeSpec,
    suffix: str,
    task_type: str,
    operation_kind: OperationKind,
    *,
    origin: NodeOrigin = NodeOrigin.DECOMPOSER,
    parameters: dict[str, object] | None = None,
    max_attempts: int = 2,
    max_repairs: int = 1,
) -> TaskNodeSpec:
    inherited = dict(parent.parameters)
    inherited.update(parameters or {})
    return TaskNodeSpec(
        node_id=f"{parent.node_id}/{suffix}",
        task_type=task_type,
        operation_kind=operation_kind,
        control_kind=ControlKind.LEAF,
        origin=origin,
        parameters=inherited,
        max_attempts=max_attempts,
        max_repairs=max_repairs,
    )


def _sequence(
    parent: TaskNodeSpec,
    suffix: str,
    task_type: str,
    *,
    origin: NodeOrigin = NodeOrigin.DECOMPOSER,
    parameters: dict[str, object] | None = None,
) -> TaskNodeSpec:
    inherited = dict(parent.parameters)
    inherited.update(parameters or {})
    return TaskNodeSpec(
        node_id=f"{parent.node_id}/{suffix}",
        task_type=task_type,
        operation_kind=OperationKind.DECOMPOSER,
        control_kind=ControlKind.SEQUENCE,
        origin=origin,
        parameters=inherited,
        max_attempts=1,
        max_repairs=0,
    )


def _delta(parent: TaskNodeSpec, children: tuple[TaskNodeSpec, ...]) -> GraphDelta:
    return GraphDelta.from_specs(
        children,
        tuple(
            TaskEdge(
                parent_id=parent.node_id,
                child_id=child.node_id,
                kind=EdgeKind.CHILD,
                order=index,
            )
            for index, child in enumerate(children)
        ),
    )


class PlaceDecomposer:
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        common = {
            "object_selector": node.parameters["object_selector"],
            "destination_selector": node.parameters["destination_selector"],
        }
        children = (
            _leaf(
                node,
                "resolve",
                "ResolveAndInspect",
                OperationKind.SYSTEM,
                parameters=common,
            ),
            _leaf(node, "plan-pick", "PlanPick", OperationKind.SYSTEM),
            _leaf(
                node,
                "plan-transfer",
                "PlanTransfer",
                OperationKind.SYSTEM,
                max_attempts=2,
                max_repairs=1,
            ),
            _sequence(node, "pick", "Pick"),
            _sequence(node, "transfer-held", "TransferHeld"),
            _sequence(node, "release", "Release"),
            _leaf(
                node,
                "verify-goal",
                "VerifyPlaceGoal",
                OperationKind.SYSTEM,
                max_repairs=0,
            ),
        )
        return _delta(node, children)


class PickDecomposer:
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        children = (
            _leaf(
                node,
                "navigate",
                "NavigateToPickStance",
                OperationKind.PHYSICAL,
            ),
            _leaf(
                node,
                "grasp",
                "ExecuteGrasp",
                OperationKind.PHYSICAL,
            ),
            _leaf(
                node,
                "verify-held",
                "VerifyHeld",
                OperationKind.SYSTEM,
                max_repairs=0,
            ),
        )
        return _delta(node, children)


class TransferHeldDecomposer:
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        children = (
            _leaf(
                node,
                "transport-posture",
                "MoveToTransportPosture",
                OperationKind.PHYSICAL,
            ),
            _leaf(
                node,
                "navigate-held",
                "NavigateHeld",
                OperationKind.PHYSICAL,
            ),
            _leaf(
                node,
                "verify-ready",
                "VerifyPlacementReady",
                OperationKind.SYSTEM,
                max_repairs=0,
            ),
        )
        return _delta(node, children)


class ReleaseDecomposer:
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        children = (
            _leaf(
                node,
                "execute",
                "ExecuteRelease",
                OperationKind.PHYSICAL,
            ),
            _leaf(
                node,
                "verify",
                "VerifyReleased",
                OperationKind.SYSTEM,
                max_repairs=0,
            ),
        )
        return _delta(node, children)


class RouteBlockedRepairDecomposer:
    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        relocate = TaskNodeSpec(
            node_id=f"{node.node_id}/relocate",
            task_type="Place",
            operation_kind=OperationKind.DECOMPOSER,
            control_kind=ControlKind.SEQUENCE,
            origin=NodeOrigin.REPAIR,
            parameters={
                "scope": node.parameters["repair_scope"],
                "object_selector": node.parameters["object_selector"],
                "destination_selector": node.parameters[
                    "destination_selector"
                ],
                "repair_depth": node.parameters["repair_depth"],
            },
            max_attempts=1,
            max_repairs=0,
        )
        replan_pick = _leaf(
            node,
            "replan-pick",
            "PlanPick",
            OperationKind.SYSTEM,
            origin=NodeOrigin.REPAIR,
            max_repairs=0,
        )
        return _delta(node, (relocate, replan_pick))


@dataclass(frozen=True)
class RepairPlanDecomposer:
    plan_task_type: str
    include_navigation: bool = False

    def expand(
        self, node: TaskNodeSpec, context: DecompositionContext
    ) -> GraphDelta:
        plan_suffix = "replan-pick" if self.plan_task_type == "PlanPick" else "replan-transfer"
        children: list[TaskNodeSpec] = [
            _leaf(
                node,
                plan_suffix,
                self.plan_task_type,
                OperationKind.SYSTEM,
                origin=NodeOrigin.REPAIR,
                max_repairs=0,
            )
        ]
        if self.plan_task_type == "PlanPick":
            children.append(
                _leaf(
                    node,
                    "replan-transfer",
                    "PlanTransfer",
                    OperationKind.SYSTEM,
                    origin=NodeOrigin.REPAIR,
                    max_repairs=0,
                )
            )
        include_navigation = bool(
            node.parameters.get("include_navigation", self.include_navigation)
        )
        if include_navigation:
            navigation_type = (
                "NavigateToPickStance"
                if self.plan_task_type == "PlanPick"
                else "NavigateHeld"
            )
            children.append(
                _leaf(
                    node,
                    "renavigate",
                    navigation_type,
                    OperationKind.PHYSICAL,
                    origin=NodeOrigin.REPAIR,
                    max_repairs=0,
                )
            )
        return _delta(node, tuple(children))
