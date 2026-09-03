from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import JointConfiguration


def artifact_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


@dataclass(frozen=True)
class ArtifactMetadata:
    snapshot_ref: str
    dependency_versions: tuple[tuple[str, int], ...]
    frame_graph_revision: int
    robot_state_epoch: int
    robot_model_version: str
    collision_model_version: str
    payload_transform_hash: str | None = None
    assumptions: tuple[str, ...] = ()
    random_seed: int = 0


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    metadata: ArtifactMetadata

    @property
    def kind(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class BindingArtifact(Artifact):
    object_id: str
    destination_id: str
    object_candidates: tuple[str, ...]
    destination_candidates: tuple[str, ...]
    grounding_evidence: tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        return "binding"


@dataclass(frozen=True)
class NavigationPlan(Artifact):
    purpose: str
    start: Pose
    goal: Pose
    path: tuple[Pose, ...]
    path_cost: float
    clearance_radius: float
    ignored_entity_ids: frozenset[str] = frozenset()

    @property
    def kind(self) -> str:
        return "navigation_plan"


@dataclass(frozen=True)
class PickPlan(Artifact):
    object_id: str
    navigation_plan_ref: str
    base_stance: Pose
    grasp_pose: Pose
    approach_path: tuple[JointConfiguration, ...]
    grasp_joints: JointConfiguration

    @property
    def kind(self) -> str:
        return "pick_plan"


@dataclass(frozen=True)
class TransferPlan(Artifact):
    object_id: str
    destination_id: str
    navigation_plan_ref: str
    destination_stance: Pose
    placement_pose: Pose
    transport_joints: JointConfiguration
    to_transport_path: tuple[JointConfiguration, ...]
    placement_path: tuple[JointConfiguration, ...]
    placement_yaw_tolerance: float | None = None

    @property
    def kind(self) -> str:
        return "transfer_plan"


def payload_transform_hash(object_id: str, radius: float) -> str:
    return f"{object_id}:{radius:.6f}"
