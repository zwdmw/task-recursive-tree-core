from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from task_recursive_tree.core.model import PredicateFormula
from task_recursive_tree.world.geometry import (
    Pose,
    distance,
    distance_3d,
    normalize_angle,
)
from task_recursive_tree.world.state import WorldSnapshot


@dataclass(frozen=True)
class Evidence:
    predicate: str
    snapshot_ref: str
    observations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observations", MappingProxyType(dict(self.observations))
        )


@dataclass(frozen=True)
class PredicateResult:
    satisfied: bool
    evidence: Evidence


class Verifier:
    """Evaluates predicates exclusively from an immutable world snapshot."""

    def evaluate(
        self, formula: PredicateFormula, snapshot: WorldSnapshot
    ) -> PredicateResult:
        handler = getattr(self, f"_evaluate_{formula.name}", None)
        if handler is None:
            raise ValueError(f"Unknown predicate: {formula.name}")
        satisfied, observations = handler(dict(formula.arguments), snapshot)
        return PredicateResult(
            satisfied=satisfied,
            evidence=Evidence(
                predicate=formula.name,
                snapshot_ref=snapshot.snapshot_ref,
                observations=observations,
            ),
        )

    @staticmethod
    def _evaluate_object_held(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        object_id = str(arguments["object_id"])
        actual = snapshot.robot.held_object_id
        return actual == object_id, {
            "expected_object_id": object_id,
            "held_object_id": actual,
        }

    @staticmethod
    def _evaluate_object_released(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        object_id = str(arguments["object_id"])
        actual = snapshot.robot.held_object_id
        return actual != object_id, {
            "object_id": object_id,
            "held_object_id": actual,
        }

    @staticmethod
    def _evaluate_object_at(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        object_state = snapshot.entity(str(arguments["object_id"]))
        destination = snapshot.entity(str(arguments["destination_id"]))
        measured = distance_3d(object_state.pose, destination.pose)
        tolerance = float(
            arguments.get(
                "tolerance",
                destination.property("acceptance_radius", destination.radius),
            )
        )
        yaw_tolerance = destination.property("yaw_tolerance")
        yaw_error = abs(
            normalize_angle(object_state.pose.yaw - destination.pose.yaw)
        )
        yaw_satisfied = (
            True
            if yaw_tolerance is None
            else yaw_error <= float(yaw_tolerance)
        )
        return measured <= tolerance and yaw_satisfied, {
            "distance": measured,
            "tolerance": tolerance,
            "yaw_error": yaw_error,
            "yaw_tolerance": yaw_tolerance,
            "object_pose": object_state.pose,
            "destination_pose": destination.pose,
        }

    @staticmethod
    def _evaluate_base_near(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        target = arguments["target_pose"]
        if not isinstance(target, Pose):
            raise TypeError("base_near.target_pose must be a Pose")
        measured = distance(snapshot.robot.base_pose, target)
        tolerance = float(arguments.get("tolerance", 0.35))
        return measured <= tolerance, {
            "distance": measured,
            "tolerance": tolerance,
            "target_pose": target,
        }

    @staticmethod
    def _evaluate_entity_exists(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        entity_id = str(arguments["entity_id"])
        return entity_id in snapshot.entities, {"entity_id": entity_id}

    @staticmethod
    def _evaluate_joints_near(
        arguments: dict[str, Any], snapshot: WorldSnapshot
    ) -> tuple[bool, dict[str, Any]]:
        target = tuple(float(value) for value in arguments["target_joints"])
        actual = snapshot.robot.joints
        if len(target) != len(actual):
            return False, {
                "target_joints": target,
                "actual_joints": actual,
                "distance": float("inf"),
            }
        measured = sum(
            (left - right) ** 2
            for left, right in zip(actual, target)
        ) ** 0.5
        tolerance = float(arguments.get("tolerance", 1e-5))
        return measured <= tolerance, {
            "target_joints": target,
            "actual_joints": actual,
            "distance": measured,
            "tolerance": tolerance,
        }
