from __future__ import annotations

from task_recursive_tree.capabilities.planning import PlanningFailure
from task_recursive_tree.artifacts import (
    ArtifactMetadata,
    BindingArtifact,
    PickPlan,
    TransferPlan,
    artifact_id,
)
from task_recursive_tree.selection.engine import SelectionFailure
from task_recursive_tree.task.contracts import SystemOperationContext
from task_recursive_tree.core.model import PredicateFormula
from task_recursive_tree.task.model import NodeOutcome, TaskNodeSpec


class ResolveAndInspectOperation:
    def run(
        self, node: TaskNodeSpec, context: SystemOperationContext
    ) -> NodeOutcome:
        snapshot = context.world.snapshot()
        try:
            object_result = context.selector.select(
                node.parameters["object_selector"], snapshot
            )
            destination_result = context.selector.select(
                node.parameters["destination_selector"], snapshot
            )
        except SelectionFailure as exc:
            return NodeOutcome.failure(exc.code, str(exc))
        if object_result.entity.entity_id == destination_result.entity.entity_id:
            return NodeOutcome.failure(
                "BINDING_INVALID",
                "Object and destination resolved to the same entity",
            )
        metadata = ArtifactMetadata(
            snapshot_ref=snapshot.snapshot_ref,
            dependency_versions=snapshot.dependency_versions(
                (
                    object_result.entity.entity_id,
                    destination_result.entity.entity_id,
                )
            ),
            frame_graph_revision=snapshot.frame_graph_revision,
            robot_state_epoch=snapshot.robot.state_epoch,
            robot_model_version=context.robot_model.model_version,
            collision_model_version=context.robot_model.collision_model_version,
            assumptions=(
                object_result.rationale,
                destination_result.rationale,
            ),
        )
        binding = BindingArtifact(
            artifact_id=artifact_id("binding"),
            metadata=metadata,
            object_id=object_result.entity.entity_id,
            destination_id=destination_result.entity.entity_id,
            object_candidates=object_result.candidate_ids,
            destination_candidates=destination_result.candidate_ids,
            grounding_evidence=(
                object_result.rationale,
                destination_result.rationale,
            ),
        )
        scope = str(node.parameters["scope"])
        reference = context.artifacts.publish(scope, "binding", binding)
        return NodeOutcome.success(reference)


class PlanPickOperation:
    def run(
        self, node: TaskNodeSpec, context: SystemOperationContext
    ) -> NodeOutcome:
        scope = str(node.parameters["scope"])
        try:
            binding = context.artifacts.resolve(
                scope, "binding", BindingArtifact
            )
            stale = _stale_outcome(context, binding)
            if stale is not None:
                return stale
            result = context.capabilities.manipulation.plan_pick(
                binding, context.world.snapshot()
            )
        except KeyError as exc:
            return NodeOutcome.failure("NO_BINDING", str(exc))
        except PlanningFailure as exc:
            return NodeOutcome(
                succeeded=False, diagnostic=exc.diagnostic
            )
        nav_ref = context.artifacts.publish(
            scope, "pick_navigation", result.navigation
        )
        pick_ref = context.artifacts.publish(scope, "pick_plan", result.pick)
        return NodeOutcome.success(nav_ref, pick_ref)


class PlanTransferOperation:
    def run(
        self, node: TaskNodeSpec, context: SystemOperationContext
    ) -> NodeOutcome:
        scope = str(node.parameters["scope"])
        try:
            binding = context.artifacts.resolve(
                scope, "binding", BindingArtifact
            )
            pick = context.artifacts.resolve(scope, "pick_plan", PickPlan)
            snapshot = context.world.snapshot()
            if snapshot.robot.held_object_id != binding.object_id:
                for artifact in (binding, pick):
                    stale = _stale_outcome(context, artifact)
                    if stale is not None:
                        return stale
            result = context.capabilities.transfer.plan_transfer(
                binding, pick, snapshot
            )
        except KeyError as exc:
            return NodeOutcome.failure("NO_PICK_PLAN", str(exc))
        except PlanningFailure as exc:
            return NodeOutcome(
                succeeded=False, diagnostic=exc.diagnostic
            )
        nav_ref = context.artifacts.publish(
            scope, "transfer_navigation", result.navigation
        )
        transfer_ref = context.artifacts.publish(
            scope, "transfer_plan", result.transfer
        )
        return NodeOutcome.success(nav_ref, transfer_ref)


class VerifyOperation:
    def __init__(self, verification: str) -> None:
        self._verification = verification

    def run(
        self, node: TaskNodeSpec, context: SystemOperationContext
    ) -> NodeOutcome:
        scope = str(node.parameters["scope"])
        snapshot = context.world.snapshot()
        try:
            binding = context.artifacts.resolve(
                scope, "binding", BindingArtifact
            )
            formulae = self._formulae(scope, binding, context)
        except KeyError as exc:
            return NodeOutcome.failure("OUTCOME_UNKNOWN", str(exc))
        for formula in formulae:
            result = context.verifier.evaluate(formula, snapshot)
            if not result.satisfied:
                code = (
                    "HOLD_LOST"
                    if formula.name == "object_held"
                    else "OUTCOME_UNKNOWN"
                )
                return NodeOutcome.failure(
                    code,
                    f"Predicate {formula.name} was not satisfied",
                    details=dict(result.evidence.observations),
                    retryable=False,
                )
        return NodeOutcome.success()

    def _formulae(
        self,
        scope: str,
        binding: BindingArtifact,
        context: SystemOperationContext,
    ) -> tuple[PredicateFormula, ...]:
        if self._verification == "held":
            return (
                PredicateFormula(
                    "object_held", {"object_id": binding.object_id}
                ),
            )
        if self._verification == "placement_ready":
            transfer = context.artifacts.resolve(
                scope, "transfer_plan", TransferPlan
            )
            return (
                PredicateFormula(
                    "object_held", {"object_id": binding.object_id}
                ),
                PredicateFormula(
                    "base_near",
                    {
                        "target_pose": transfer.destination_stance,
                        "tolerance": 0.2,
                    },
                ),
            )
        if self._verification == "released":
            return (
                PredicateFormula(
                    "object_released", {"object_id": binding.object_id}
                ),
            )
        if self._verification == "place_goal":
            return (
                PredicateFormula(
                    "object_at",
                    {
                        "object_id": binding.object_id,
                        "destination_id": binding.destination_id,
                    },
                ),
            )
        raise ValueError(f"Unknown verification: {self._verification}")


def default_system_operations() -> dict[str, object]:
    return {
        "ResolveAndInspect": ResolveAndInspectOperation(),
        "PlanPick": PlanPickOperation(),
        "PlanTransfer": PlanTransferOperation(),
        "VerifyHeld": VerifyOperation("held"),
        "VerifyPlacementReady": VerifyOperation("placement_ready"),
        "VerifyReleased": VerifyOperation("released"),
        "VerifyPlaceGoal": VerifyOperation("place_goal"),
    }


def _stale_outcome(
    context: SystemOperationContext,
    artifact,
) -> NodeOutcome | None:
    result = context.freshness.evaluate(
        artifact,
        context.world.snapshot(),
        robot_model_version=context.robot_model.model_version,
        collision_model_version=context.robot_model.collision_model_version,
    )
    if result.fresh:
        return None
    return NodeOutcome.failure(
        "STALE_PLAN",
        f"Artifact {artifact.artifact_id} is stale",
        details={"mismatches": result.mismatches},
        retryable=True,
    )
