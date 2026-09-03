from __future__ import annotations

from task_recursive_tree.artifacts import BindingArtifact
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.contracts import RepairContext
from task_recursive_tree.task.model import (
    ControlKind,
    EdgeKind,
    GraphDelta,
    NodeOrigin,
    OperationKind,
    RepairProposal,
    TaskEdge,
    TaskNodeSpec,
)


class DefaultRepairResolver:
    def propose(
        self,
        node: TaskNodeSpec,
        diagnostic,
        context: RepairContext,
        repair_index: int,
    ) -> RepairProposal | None:
        snapshot = context.world.snapshot()
        if diagnostic.code in {
            "ROUTE_BLOCKED",
            "HELD_ENVELOPE_COLLISION",
        } and snapshot.robot.held_object_id:
            if node.task_type == "NavigateHeld":
                return self._refresh_plan(node, repair_index)
            return None
        if diagnostic.code == "ROUTE_BLOCKED":
            return self._relocate_blocker(
                node, diagnostic, context, repair_index
            )
        if diagnostic.code == "STALE_PLAN":
            return self._refresh_plan(node, repair_index)
        return None

    def _relocate_blocker(
        self,
        node: TaskNodeSpec,
        diagnostic,
        context: RepairContext,
        repair_index: int,
    ) -> RepairProposal | None:
        blocker_id = diagnostic.details.get("blocker_id")
        if not blocker_id:
            return None
        if int(node.parameters.get("repair_depth", 0)) >= 1:
            return None
        try:
            binding = context.artifacts.resolve(
                str(node.parameters["scope"]), "binding", BindingArtifact
            )
        except KeyError:
            binding = None
        if binding is not None and blocker_id == binding.object_id:
            return None
        snapshot = context.world.snapshot()
        blocker = snapshot.entities.get(str(blocker_id))
        if blocker is None or not bool(blocker.property("movable", False)):
            return None
        parking_regions = sorted(
            (
                entity
                for entity in snapshot.entities.values()
                if entity.kind == "region" and "parking" in entity.tags
            ),
            key=lambda entity: entity.entity_id,
        )
        if not parking_regions:
            return None
        parking = parking_regions[0]
        repair_scope = (
            f"{node.parameters['scope']}:repair:{repair_index}:{blocker.entity_id}"
        )
        repair_id = f"{node.node_id}/repair-{repair_index}/route-blocked"
        repair_root = TaskNodeSpec(
            node_id=repair_id,
            task_type="RouteBlockedRepair",
            operation_kind=OperationKind.DECOMPOSER,
            control_kind=ControlKind.SEQUENCE,
            origin=NodeOrigin.REPAIR,
            parameters={
                "scope": node.parameters["scope"],
                "repair_scope": repair_scope,
                "object_selector": SpatialSelector.exact(
                    blocker.entity_id, blocker.kind
                ),
                "destination_selector": SpatialSelector.exact(
                    parking.entity_id, parking.kind
                ),
                "repair_depth": int(
                    node.parameters.get("repair_depth", 0)
                )
                + 1,
            },
            max_attempts=1,
            max_repairs=0,
        )
        delta = GraphDelta.from_specs(
            (repair_root,),
            (
                TaskEdge(
                    parent_id=node.node_id,
                    child_id=repair_root.node_id,
                    kind=EdgeKind.REPAIR,
                    order=repair_index,
                ),
            ),
        )
        return RepairProposal(
            delta=delta,
            entry_node_id=repair_root.node_id,
            rationale=(
                f"Relocate verified blocker {blocker.entity_id} "
                f"to parking region {parking.entity_id}, then refresh PickPlan"
            ),
        )

    @staticmethod
    def _refresh_plan(
        node: TaskNodeSpec, repair_index: int
    ) -> RepairProposal | None:
        pick_tasks = {
            "PlanPick",
            "PlanTransfer",
            "NavigateToPickStance",
            "ExecuteGrasp",
        }
        transfer_tasks = {
            "MoveToTransportPosture",
            "NavigateHeld",
            "ExecuteRelease",
        }
        if node.task_type in pick_tasks:
            task_type = "RepairPickPlan"
            include_navigation = node.task_type == "ExecuteGrasp"
        elif node.task_type in transfer_tasks:
            task_type = "RepairTransferPlan"
            include_navigation = node.task_type == "ExecuteRelease"
        else:
            return None
        repair_id = f"{node.node_id}/repair-{repair_index}/refresh-plan"
        repair_root = TaskNodeSpec(
            node_id=repair_id,
            task_type=task_type,
            operation_kind=OperationKind.DECOMPOSER,
            control_kind=ControlKind.SEQUENCE,
            origin=NodeOrigin.REPAIR,
            parameters={
                "scope": node.parameters["scope"],
                "include_navigation": include_navigation,
                "repair_depth": node.parameters.get("repair_depth", 0),
            },
            max_attempts=1,
            max_repairs=0,
        )
        delta = GraphDelta.from_specs(
            (repair_root,),
            (
                TaskEdge(
                    parent_id=node.node_id,
                    child_id=repair_root.node_id,
                    kind=EdgeKind.REPAIR,
                    order=repair_index,
                ),
            ),
        )
        return RepairProposal(
            delta=delta,
            entry_node_id=repair_root.node_id,
            rationale=f"Refresh plans required by {node.task_type}",
        )
