from __future__ import annotations

from dataclasses import dataclass

from task_recursive_tree.artifacts import (
    Artifact,
    TransferPlan,
    payload_transform_hash,
)
from task_recursive_tree.world.state import WorldSnapshot


@dataclass(frozen=True)
class FreshnessResult:
    fresh: bool
    mismatches: tuple[str, ...] = ()


class ArtifactFreshnessChecker:
    def evaluate(
        self,
        artifact: Artifact,
        snapshot: WorldSnapshot,
        *,
        robot_model_version: str,
        collision_model_version: str,
    ) -> FreshnessResult:
        mismatches: list[str] = []
        current = {
            "map": snapshot.grid.revision,
            "frame_graph": snapshot.frame_graph_revision,
            "robot": snapshot.robot.state_epoch,
            "arm": snapshot.arm_state_fingerprint(),
        }
        current.update(
            {
                f"entity:{entity.entity_id}": entity.dependency_version
                for entity in snapshot.entities.values()
            }
        )
        for name, expected_version in artifact.metadata.dependency_versions:
            if name.startswith("nav_occupancy:"):
                ignored = tuple(
                    entity_id
                    for entity_id in name.partition(":")[2].split(",")
                    if entity_id
                )
                actual = snapshot.nav_occupancy_fingerprint(ignored)
            else:
                actual = current.get(name)
            if actual != expected_version:
                mismatches.append(
                    f"{name}: expected {expected_version}, observed {actual}"
                )
        if artifact.metadata.robot_model_version != robot_model_version:
            mismatches.append(
                "robot_model: expected "
                f"{artifact.metadata.robot_model_version}, "
                f"observed {robot_model_version}"
            )
        if artifact.metadata.collision_model_version != collision_model_version:
            mismatches.append(
                "collision_model: expected "
                f"{artifact.metadata.collision_model_version}, "
                f"observed {collision_model_version}"
            )
        if (
            isinstance(artifact, TransferPlan)
            and artifact.metadata.payload_transform_hash is not None
        ):
            entity = snapshot.entities.get(artifact.object_id)
            actual_payload_hash = (
                None
                if entity is None
                else payload_transform_hash(entity.entity_id, entity.radius)
            )
            if actual_payload_hash != artifact.metadata.payload_transform_hash:
                mismatches.append(
                    "payload_transform: expected "
                    f"{artifact.metadata.payload_transform_hash}, "
                    f"observed {actual_payload_hash}"
                )
        return FreshnessResult(not mismatches, tuple(mismatches))
