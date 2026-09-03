from __future__ import annotations

from dataclasses import dataclass, replace

from task_recursive_tree.robot.controllers import (
    ControllerFailure,
    JointTrajectoryController,
    ReferenceJointTrajectoryController,
)
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.robot.ports import (
    ArmPathCommand,
    GripperCommand,
    NavigateCommand,
    RobotCommand,
    RobotExecutionError,
)
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.world.geometry import (
    distance_3d,
    normalize_angle,
)
from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    Observation,
    RobotState,
    WorldSnapshot,
)


@dataclass(frozen=True)
class SimulationCheckpoint:
    robot: RobotState
    entities: tuple[EntityState, ...]


class SimulatedRobotBackend:
    """Deterministic backend used to exercise the full runtime contract."""

    def __init__(
        self,
        robot_model: RobotModel,
        grid: GridMap,
        initial: Observation,
        arm_controller: JointTrajectoryController | None = None,
    ) -> None:
        self._robot_model = robot_model
        self._grid = grid
        self._robot = initial.robot
        self._entities = {
            entity.entity_id: entity for entity in initial.entities
        }
        self._frame_graph_revision = initial.frame_graph_revision
        self._arm_controller = (
            arm_controller or ReferenceJointTrajectoryController()
        )

    @property
    def supports_rollback(self) -> bool:
        return True

    def checkpoint(self) -> SimulationCheckpoint:
        return SimulationCheckpoint(
            robot=self._robot,
            entities=tuple(self._entities.values()),
        )

    def restore(self, checkpoint: SimulationCheckpoint) -> None:
        self._robot = checkpoint.robot
        self._entities = {
            entity.entity_id: entity for entity in checkpoint.entities
        }

    def execute(self, command: RobotCommand) -> None:
        if isinstance(command, NavigateCommand):
            self._navigate(command)
            return
        if isinstance(command, ArmPathCommand):
            self._move_arm(command)
            return
        if isinstance(command, GripperCommand):
            self._move_gripper(command)
            return
        raise TypeError(f"Unknown robot command: {type(command).__name__}")

    def observe(self) -> Observation:
        return Observation(
            robot=self._robot,
            entities=tuple(
                self._entities[entity_id]
                for entity_id in sorted(self._entities)
            ),
            frame_graph_revision=self._frame_graph_revision,
        )

    def _navigate(self, command: NavigateCommand) -> None:
        if not command.path:
            raise RobotExecutionError(
                Diagnostic(
                    "ROUTE_BLOCKED",
                    "Navigation command has an empty path",
                )
            )
        for pose in command.path:
            cell = self._grid.world_to_cell(pose)
            if not self._grid.in_bounds(cell):
                raise RobotExecutionError(
                    Diagnostic(
                        "ROUTE_BLOCKED",
                        f"Navigation leaves grid at {cell}",
                    )
                )
            snapshot = self._snapshot()
            ignored = set(command.ignore_entity_ids)
            if self._robot.held_object_id:
                ignored.add(self._robot.held_object_id)
            held_radius = (
                self._entities[self._robot.held_object_id].radius
                if self._robot.held_object_id
                else None
            )
            clearance = max(
                command.clearance_radius,
                self._robot_model.navigation_clearance(
                    held_object_radius=held_radius,
                    joints=self._robot.joints,
                ),
            )
            occupied, owners = snapshot.occupancy(
                ignore_entity_ids=frozenset(ignored),
                inflation=clearance,
            )
            if cell in occupied:
                owner = owners.get(cell)
                details = {"cell": cell}
                if owner:
                    details["blocker_id"] = owner
                raise RobotExecutionError(
                    Diagnostic(
                        (
                            "HELD_ENVELOPE_COLLISION"
                            if self._robot.held_object_id
                            else "ROUTE_BLOCKED"
                        ),
                        f"Navigation path is blocked at {cell}",
                        details=details,
                        retryable=bool(owner),
                    )
                )
            self._robot = replace(self._robot, base_pose=pose)
            self._follow_held_object()

    def _move_arm(self, command: ArmPathCommand) -> None:
        ignored = (
            frozenset({self._robot.held_object_id})
            if self._robot.held_object_id
            else frozenset()
        )

        def collision_free(joints):
            return self._robot_model.arm_collision_free(
                self._robot.base_pose,
                joints,
                self._snapshot(),
                ignore_entity_ids=ignored,
            )

        try:
            accepted_path = self._arm_controller.validate(
                current=self._robot.joints,
                path=command.path,
                model=self._robot_model,
                collision_free=collision_free,
            )
        except ControllerFailure as exc:
            raise RobotExecutionError(exc.diagnostic) from exc

        for joints in accepted_path:
            ignored = (
                frozenset({self._robot.held_object_id})
                if self._robot.held_object_id
                else frozenset()
            )
            self._robot = replace(self._robot, joints=joints)
            self._follow_held_object()

    def _move_gripper(self, command: GripperCommand) -> None:
        if command.close:
            if not self._robot.gripper_open or self._robot.held_object_id:
                raise RobotExecutionError(
                    Diagnostic(
                        "GRIPPER_STATE_INVALID",
                        "Cannot close an occupied or closed gripper",
                    )
                )
            try:
                object_state = self._entities[command.object_id]
            except KeyError as exc:
                raise RobotExecutionError(
                    Diagnostic(
                        "BINDING_NOT_FOUND",
                        f"Unknown grasp object {command.object_id}",
                    )
                ) from exc
            end_effector = self._robot_model.forward_kinematics(
                self._robot.base_pose, self._robot.joints
            )
            measured = distance_3d(end_effector, object_state.pose)
            if measured > self._robot_model.gripper_tolerance:
                raise RobotExecutionError(
                    Diagnostic(
                        "IK_FAILED",
                        f"End effector is {measured:.3f} m from the object",
                        details={"distance": measured},
                        retryable=True,
                    )
                )
            self._robot = replace(
                self._robot,
                gripper_open=False,
                held_object_id=object_state.entity_id,
            )
            self._follow_held_object()
            return

        if self._robot.held_object_id != command.object_id:
            raise RobotExecutionError(
                Diagnostic(
                    "HOLD_LOST",
                    f"Robot is not holding {command.object_id}",
                )
            )
        object_state = self._entities[command.object_id]
        end_effector = self._robot_model.forward_kinematics(
            self._robot.base_pose, self._robot.joints
        )
        if command.release_pose is not None:
            measured = distance_3d(end_effector, command.release_pose)
            if measured > self._robot_model.gripper_tolerance:
                raise RobotExecutionError(
                    Diagnostic(
                        "IK_FAILED",
                        "Release pose is not reached by the end effector",
                        details={
                            "distance": measured,
                            "end_effector_pose": end_effector,
                            "requested_release_pose": command.release_pose,
                        },
                        retryable=True,
                    )
                )
            if command.yaw_tolerance is not None:
                yaw_error = abs(
                    normalize_angle(
                        end_effector.yaw - command.release_pose.yaw
                    )
                )
                if yaw_error > command.yaw_tolerance:
                    raise RobotExecutionError(
                        Diagnostic(
                            "IK_FAILED",
                            "Release orientation is not reached",
                            details={
                                "yaw_error": yaw_error,
                                "yaw_tolerance": command.yaw_tolerance,
                            },
                            retryable=True,
                        )
                    )
        self._entities[command.object_id] = replace(
            object_state, pose=end_effector
        )
        self._robot = replace(
            self._robot,
            gripper_open=True,
            held_object_id=None,
        )

    def _follow_held_object(self) -> None:
        object_id = self._robot.held_object_id
        if object_id is None:
            return
        object_state = self._entities[object_id]
        self._entities[object_id] = replace(
            object_state,
            pose=self._robot_model.forward_kinematics(
                self._robot.base_pose, self._robot.joints
            ),
        )

    def _snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            revision=0,
            frame_graph_revision=self._frame_graph_revision,
            grid=self._grid,
            robot=self._robot,
            entities=self._entities,
        )
