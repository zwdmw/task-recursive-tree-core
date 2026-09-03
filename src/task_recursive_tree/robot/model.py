from __future__ import annotations

from dataclasses import dataclass
from math import cos, hypot, sin, sqrt

from task_recursive_tree.world.geometry import Pose, point_segment_distance
from task_recursive_tree.world.state import JointConfiguration, WorldSnapshot


@dataclass(frozen=True)
class RobotModel:
    link_lengths: tuple[float, float] = (0.8, 0.8)
    joint_limits: tuple[tuple[float, float], ...] = (
        (-3.141592653589793, 3.141592653589793),
        (-2.8, 2.8),
    )
    base_radius: float = 0.32
    gripper_tolerance: float = 0.18
    vertical_tolerance: float = 0.05
    transport_joints: JointConfiguration = (1.4, -2.8)
    model_version: str = "reference-arm-v1"
    collision_model_version: str = "reference-collision-v1"

    def __post_init__(self) -> None:
        if len(self.joint_limits) != 2:
            raise ValueError("The reference arm requires two joint limits")

    def forward_kinematics(
        self, base: Pose, joints: JointConfiguration
    ) -> Pose:
        if len(joints) != 2:
            raise ValueError("The reference arm requires two joints")
        q1, q2 = joints
        link1, link2 = self.link_lengths
        angle1 = base.yaw + q1
        angle2 = angle1 + q2
        return Pose(
            x=base.x + link1 * cos(angle1) + link2 * cos(angle2),
            y=base.y + link1 * sin(angle1) + link2 * sin(angle2),
            z=base.z,
            yaw=angle2,
        )

    def joint_positions(
        self, base: Pose, joints: JointConfiguration
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        q1, q2 = joints
        link1, link2 = self.link_lengths
        base_xy = (base.x, base.y)
        elbow = (
            base.x + link1 * cos(base.yaw + q1),
            base.y + link1 * sin(base.yaw + q1),
        )
        end = (
            elbow[0] + link2 * cos(base.yaw + q1 + q2),
            elbow[1] + link2 * sin(base.yaw + q1 + q2),
        )
        return base_xy, elbow, end

    def in_joint_limits(self, joints: JointConfiguration) -> bool:
        return len(joints) == len(self.joint_limits) and all(
            lower <= value <= upper
            for value, (lower, upper) in zip(joints, self.joint_limits)
        )

    def navigation_clearance(
        self,
        *,
        held_object_radius: float | None = None,
        joints: JointConfiguration | None = None,
    ) -> float:
        if held_object_radius is None:
            return self.base_radius
        posture = joints or self.transport_joints
        end_x, end_y = self._relative_end_effector(posture)
        payload_radius = hypot(end_x, end_y) + held_object_radius
        return max(self.base_radius, payload_radius)

    def joint_distance(
        self, left: JointConfiguration, right: JointConfiguration
    ) -> float:
        return sqrt(
            sum(
                (left_value - right_value) ** 2
                for left_value, right_value in zip(left, right)
            )
        )

    def arm_collision_free(
        self,
        base: Pose,
        joints: JointConfiguration,
        snapshot: WorldSnapshot,
        *,
        ignore_entity_ids: frozenset[str] = frozenset(),
        clearance: float = 0.05,
    ) -> bool:
        if not self.in_joint_limits(joints):
            return False
        shoulder, elbow, end = self.joint_positions(base, joints)
        for entity in snapshot.entities.values():
            if entity.entity_id in ignore_entity_ids:
                continue
            if not bool(entity.property("arm_obstacle", False)):
                continue
            threshold = entity.radius + clearance
            first = point_segment_distance(
                entity.pose.x,
                entity.pose.y,
                shoulder[0],
                shoulder[1],
                elbow[0],
                elbow[1],
            )
            second = point_segment_distance(
                entity.pose.x,
                entity.pose.y,
                elbow[0],
                elbow[1],
                end[0],
                end[1],
            )
            if min(first, second) <= threshold:
                return False
        for cell in snapshot.grid.static_occupied:
            center = snapshot.grid.cell_to_pose(cell)
            threshold = snapshot.grid.resolution * 0.48 + clearance
            first = point_segment_distance(
                center.x,
                center.y,
                shoulder[0],
                shoulder[1],
                elbow[0],
                elbow[1],
            )
            second = point_segment_distance(
                center.x,
                center.y,
                elbow[0],
                elbow[1],
                end[0],
                end[1],
            )
            if min(first, second) <= threshold:
                return False
        return True

    def _relative_end_effector(
        self, joints: JointConfiguration
    ) -> tuple[float, float]:
        q1, q2 = joints
        link1, link2 = self.link_lengths
        return (
            link1 * cos(q1) + link2 * cos(q1 + q2),
            link1 * sin(q1) + link2 * sin(q1 + q2),
        )
