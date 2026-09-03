from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from task_recursive_tree.capabilities.rrt import (
    DEFAULT_EDGE_RESOLUTION,
    sample_joint_segment,
)
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.world.state import JointConfiguration


class ControllerFailure(RuntimeError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class JointTrajectoryController(Protocol):
    """Controller boundary implemented by a reference tracker or real MPC."""

    def validate(
        self,
        *,
        current: JointConfiguration,
        path: tuple[JointConfiguration, ...],
        model: RobotModel,
        collision_free: Callable[[JointConfiguration], bool],
    ) -> tuple[JointConfiguration, ...]: ...


@dataclass(frozen=True)
class ReferenceJointTrajectoryController:
    start_tolerance: float = 1e-5
    max_waypoint_step: float = 0.35
    edge_resolution: float = DEFAULT_EDGE_RESOLUTION

    def validate(
        self,
        *,
        current: JointConfiguration,
        path: tuple[JointConfiguration, ...],
        model: RobotModel,
        collision_free: Callable[[JointConfiguration], bool],
    ) -> tuple[JointConfiguration, ...]:
        if not path:
            raise ControllerFailure(
                Diagnostic("ARM_PATH_BLOCKED", "Arm path is empty")
            )
        if model.joint_distance(current, path[0]) > self.start_tolerance:
            raise ControllerFailure(
                Diagnostic(
                    "STALE_PLAN",
                    "Arm path does not start at the observed joint state",
                    details={
                        "observed_joints": current,
                        "path_start": path[0],
                    },
                    retryable=True,
                )
            )
        if not collision_free(current):
            raise ControllerFailure(
                Diagnostic(
                    "ARM_PATH_BLOCKED",
                    "Observed arm state is in collision or exceeds limits",
                    retryable=True,
                )
            )
        previous = current
        accepted: list[JointConfiguration] = [current]
        for waypoint in path:
            step = model.joint_distance(previous, waypoint)
            if step > self.max_waypoint_step:
                raise ControllerFailure(
                    Diagnostic(
                        "ARM_PATH_DISCONTINUOUS",
                        f"Joint waypoint step {step:.3f} exceeds controller limit",
                        details={
                            "step": step,
                            "limit": self.max_waypoint_step,
                        },
                    )
                )
            segment = sample_joint_segment(
                previous,
                waypoint,
                resolution=self.edge_resolution,
            )
            for sample in segment[1:]:
                if not collision_free(sample):
                    raise ControllerFailure(
                        Diagnostic(
                            "ARM_PATH_BLOCKED",
                            "Arm command entered collision or exceeded limits",
                            details={
                                "segment_start": previous,
                                "segment_end": waypoint,
                                "blocked_sample": sample,
                                "edge_resolution": self.edge_resolution,
                            },
                            retryable=True,
                        )
                    )
                accepted.append(sample)
            previous = waypoint
        return tuple(accepted)
