from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from task_recursive_tree.capabilities.astar import (
    PathNotFound,
    astar_grid,
)
from task_recursive_tree.capabilities.ik import solve_planar_2link
from task_recursive_tree.capabilities.rrt import (
    RRTPathNotFound,
    rrt_plan,
)
from task_recursive_tree.robot.model import RobotModel
from task_recursive_tree.artifacts import (
    ArtifactMetadata,
    BindingArtifact,
    NavigationPlan,
    PickPlan,
    TransferPlan,
    artifact_id,
    payload_transform_hash,
)
from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.world.geometry import Pose, heading, normalize_angle
from task_recursive_tree.world.state import GridCell, WorldSnapshot


class PlanningFailure(RuntimeError):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class PlannedPick:
    navigation: NavigationPlan
    pick: PickPlan


@dataclass(frozen=True)
class PlannedTransfer:
    navigation: NavigationPlan
    transfer: TransferPlan


class NavigationCapability:
    def __init__(self, robot_model: RobotModel) -> None:
        self._robot_model = robot_model

    def plan(
        self,
        *,
        start: Pose,
        goal: Pose,
        snapshot: WorldSnapshot,
        purpose: str,
        ignore_entity_ids: frozenset[str] = frozenset(),
        clearance_radius: float | None = None,
        dependency_entity_ids: tuple[str, ...] = (),
        ignore_dependency_obstacle_ids: frozenset[str] = frozenset(),
        include_robot_dependency: bool = False,
        seed: int = 0,
    ) -> NavigationPlan:
        clearance = (
            self._robot_model.base_radius
            if clearance_radius is None
            else clearance_radius
        )
        occupied, _ = snapshot.occupancy(
            ignore_entity_ids=ignore_entity_ids,
            inflation=clearance,
        )
        start_cell = snapshot.grid.world_to_cell(start)
        goal_cell = snapshot.grid.world_to_cell(goal)
        if start_cell in occupied:
            raise PathNotFound(start_cell, goal_cell, frozenset())
        result = astar_grid(
            start_cell,
            goal_cell,
            occupied=occupied,
            width=snapshot.grid.width,
            height=snapshot.grid.height,
        )
        path = self._poses_from_cells(result.cells, snapshot, goal)
        dependency_versions = snapshot.dependency_versions(
            dependency_entity_ids,
            include_nav_obstacles=True,
            ignore_nav_obstacle_ids=ignore_dependency_obstacle_ids,
            include_robot=include_robot_dependency,
        )
        metadata = _metadata(
            snapshot,
            self._robot_model,
            dependencies=dependency_versions,
            seed=seed,
        )
        return NavigationPlan(
            artifact_id=artifact_id("nav"),
            metadata=metadata,
            purpose=purpose,
            start=start,
            goal=goal,
            path=path,
            path_cost=result.cost * snapshot.grid.resolution,
            clearance_radius=clearance,
            ignored_entity_ids=ignore_entity_ids,
        )

    @staticmethod
    def _poses_from_cells(
        cells: tuple[GridCell, ...],
        snapshot: WorldSnapshot,
        goal: Pose,
    ) -> tuple[Pose, ...]:
        poses: list[Pose] = []
        for index, cell in enumerate(cells):
            pose = snapshot.grid.cell_to_pose(cell)
            if index + 1 < len(cells):
                next_pose = snapshot.grid.cell_to_pose(cells[index + 1])
                pose = Pose(pose.x, pose.y, yaw=heading(pose, next_pose))
            else:
                pose = Pose(pose.x, pose.y, yaw=goal.yaw)
            poses.append(pose)
        return tuple(poses)


class ManipulationCapability:
    def __init__(
        self,
        robot_model: RobotModel,
        navigation: NavigationCapability,
    ) -> None:
        self._robot_model = robot_model
        self._navigation = navigation

    def plan_pick(
        self,
        binding: BindingArtifact,
        snapshot: WorldSnapshot,
        *,
        seed: int = 17,
    ) -> PlannedPick:
        object_state = snapshot.entity(binding.object_id)
        candidates = _stance_candidates(object_state.pose, snapshot)
        failures: list[str] = []
        best: tuple[float, NavigationPlan, tuple[tuple[float, ...], ...], tuple[float, ...], Pose] | None = None
        held_clearance = self._robot_model.navigation_clearance(
            held_object_radius=object_state.radius,
            joints=self._robot_model.transport_joints,
        )
        held_occupied, _ = snapshot.occupancy(
            ignore_entity_ids=(object_state.entity_id,),
            inflation=held_clearance,
        )

        for stance_cell in candidates:
            if stance_cell in held_occupied:
                failures.append(
                    f"stance {stance_cell}: held envelope cannot depart"
                )
                continue
            stance_center = snapshot.grid.cell_to_pose(stance_cell)
            stance = Pose(
                stance_center.x,
                stance_center.y,
                yaw=heading(stance_center, object_state.pose),
            )
            try:
                navigation = self._navigation.plan(
                    start=snapshot.robot.base_pose,
                    goal=stance,
                    snapshot=snapshot,
                    purpose="approach_pick",
                    ignore_entity_ids=frozenset({object_state.entity_id}),
                    dependency_entity_ids=(object_state.entity_id,),
                    ignore_dependency_obstacle_ids=frozenset(
                        {object_state.entity_id}
                    ),
                    include_robot_dependency=True,
                    seed=seed,
                )
            except PathNotFound:
                failures.append(f"stance {stance_cell}: no base route")
                continue

            relative = object_state.pose.relative_to(stance)
            if abs(relative.z) > self._robot_model.vertical_tolerance:
                failures.append(
                    f"stance {stance_cell}: object height is unreachable"
                )
                continue
            ik_candidates = solve_planar_2link(
                relative.x,
                relative.y,
                self._robot_model.link_lengths,
                self._robot_model.joint_limits,  # type: ignore[arg-type]
            )
            if not ik_candidates:
                failures.append(f"stance {stance_cell}: IK failed")
                continue

            for goal_joints in ik_candidates:
                collision_free = lambda joints: self._robot_model.arm_collision_free(
                    stance,
                    joints,
                    snapshot,
                    ignore_entity_ids=frozenset({object_state.entity_id}),
                )
                try:
                    arm_result = rrt_plan(
                        snapshot.robot.joints,
                        goal_joints,
                        joint_limits=self._robot_model.joint_limits,
                        collision_free=collision_free,
                        seed=seed,
                    )
                except RRTPathNotFound as exc:
                    failures.append(f"stance {stance_cell}: {exc}")
                    continue
                cost = navigation.path_cost + len(arm_result.path) * 0.01
                candidate = (
                    cost,
                    navigation,
                    arm_result.path,
                    goal_joints,
                    stance,
                )
                if best is None or candidate[0] < best[0]:
                    best = candidate

        if best is None:
            raise PlanningFailure(
                Diagnostic(
                    code="NO_PICK_PLAN",
                    message=f"No feasible pick plan for {binding.object_id}",
                    details={"causes": tuple(failures)},
                    retryable=True,
                )
            )

        _, navigation, arm_path, grasp_joints, stance = best
        metadata = _metadata(
            snapshot,
            self._robot_model,
            dependencies=snapshot.dependency_versions(
                (object_state.entity_id,),
                include_nav_obstacles=True,
                ignore_nav_obstacle_ids=(object_state.entity_id,),
                include_arm=True,
            ),
            seed=seed,
        )
        pick = PickPlan(
            artifact_id=artifact_id("pick"),
            metadata=metadata,
            object_id=object_state.entity_id,
            navigation_plan_ref=navigation.artifact_id,
            base_stance=stance,
            grasp_pose=object_state.pose,
            approach_path=arm_path,
            grasp_joints=grasp_joints,
        )
        return PlannedPick(navigation=navigation, pick=pick)


class TransferCapability:
    def __init__(
        self,
        robot_model: RobotModel,
        navigation: NavigationCapability,
    ) -> None:
        self._robot_model = robot_model
        self._navigation = navigation

    def plan_transfer(
        self,
        binding: BindingArtifact,
        pick: PickPlan,
        snapshot: WorldSnapshot,
        *,
        seed: int = 31,
    ) -> PlannedTransfer:
        object_state = snapshot.entity(binding.object_id)
        destination = snapshot.entity(binding.destination_id)
        placement_poses = _placement_candidates(destination.pose, destination.properties)
        placement_yaw_tolerance = destination.property("yaw_tolerance")
        if placement_yaw_tolerance is not None:
            placement_yaw_tolerance = float(placement_yaw_tolerance)
        transport_joints = self._robot_model.transport_joints
        already_held = snapshot.robot.held_object_id == object_state.entity_id
        transfer_start = (
            snapshot.robot.base_pose if already_held else pick.base_stance
        )
        transport_start_joints = (
            snapshot.robot.joints if already_held else pick.grasp_joints
        )
        failures: list[str] = []
        route_attempts = 0
        route_failures: list[tuple[Pose, float]] = []
        best: tuple[
            float,
            NavigationPlan,
            Pose,
            Pose,
            tuple[tuple[float, ...], ...],
            tuple[tuple[float, ...], ...],
        ] | None = None

        held_clearance = self._robot_model.navigation_clearance(
            held_object_radius=object_state.radius,
            joints=transport_joints,
        )
        for placement in placement_poses:
            for stance_cell in _stance_candidates(placement, snapshot):
                stance_center = snapshot.grid.cell_to_pose(stance_cell)
                stance = Pose(
                    stance_center.x,
                    stance_center.y,
                    yaw=heading(stance_center, placement),
                )
                route_attempts += 1
                try:
                    navigation = self._navigation.plan(
                        start=transfer_start,
                        goal=stance,
                        snapshot=snapshot,
                        purpose="navigate_held",
                        ignore_entity_ids=frozenset({object_state.entity_id}),
                        clearance_radius=held_clearance,
                        dependency_entity_ids=(destination.entity_id,),
                        ignore_dependency_obstacle_ids=frozenset(
                            {object_state.entity_id}
                        ),
                        include_robot_dependency=False,
                        seed=seed,
                    )
                except PathNotFound:
                    route_failures.append((stance, held_clearance))
                    failures.append(
                        f"placement at ({placement.x:.2f}, {placement.y:.2f}), "
                        f"stance {stance_cell}: no held route"
                    )
                    continue

                relative = placement.relative_to(stance)
                if abs(relative.z) > self._robot_model.vertical_tolerance:
                    failures.append(
                        f"stance {stance_cell}: placement height is unreachable"
                    )
                    continue
                ik_candidates = solve_planar_2link(
                    relative.x,
                    relative.y,
                    self._robot_model.link_lengths,
                    self._robot_model.joint_limits,  # type: ignore[arg-type]
                )
                if not ik_candidates:
                    failures.append(f"stance {stance_cell}: placement IK failed")
                    continue

                pick_collision_free = (
                    lambda joints: self._robot_model.arm_collision_free(
                        pick.base_stance,
                        joints,
                        snapshot,
                        ignore_entity_ids=frozenset({object_state.entity_id}),
                    )
                )
                try:
                    to_transport = rrt_plan(
                        transport_start_joints,
                        transport_joints,
                        joint_limits=self._robot_model.joint_limits,
                        collision_free=pick_collision_free,
                        seed=seed,
                    )
                except RRTPathNotFound as exc:
                    failures.append(f"transport posture: {exc}")
                    continue

                for place_joints in ik_candidates:
                    if placement_yaw_tolerance is not None:
                        end_effector = self._robot_model.forward_kinematics(
                            stance, place_joints
                        )
                        yaw_error = abs(
                            normalize_angle(
                                end_effector.yaw - placement.yaw
                            )
                        )
                        if yaw_error > placement_yaw_tolerance:
                            failures.append(
                                f"stance {stance_cell}: placement yaw "
                                f"error {yaw_error:.3f}"
                            )
                            continue
                    place_collision_free = (
                        lambda joints: self._robot_model.arm_collision_free(
                            stance,
                            joints,
                            snapshot,
                            ignore_entity_ids=frozenset(
                                {object_state.entity_id, destination.entity_id}
                            ),
                        )
                    )
                    try:
                        placement_path = rrt_plan(
                            transport_joints,
                            place_joints,
                            joint_limits=self._robot_model.joint_limits,
                            collision_free=place_collision_free,
                            seed=seed + 1,
                        )
                    except RRTPathNotFound as exc:
                        failures.append(f"placement arm path: {exc}")
                        continue
                    cost = (
                        navigation.path_cost
                        + 0.01 * len(to_transport.path)
                        + 0.01 * len(placement_path.path)
                    )
                    candidate = (
                        cost,
                        navigation,
                        stance,
                        placement,
                        to_transport.path,
                        placement_path.path,
                    )
                    if best is None or candidate[0] < best[0]:
                        best = candidate

        if best is None:
            blocker_witness = None
            if route_attempts > 0 and len(route_failures) == route_attempts:
                blocker_witness = _find_verified_blocker(
                    start=transfer_start,
                    failed_goals=route_failures,
                    snapshot=snapshot,
                    object_id=binding.object_id,
                )
            blocker_id = (
                blocker_witness[0] if blocker_witness is not None else None
            )
            details: dict[str, Any] = {"causes": tuple(failures)}
            if blocker_id:
                details["blocker_id"] = blocker_id
                details["blocker_witness"] = blocker_witness[1]
            route_only_failure = (
                route_attempts > 0 and len(route_failures) == route_attempts
            )
            raise PlanningFailure(
                Diagnostic(
                    code=(
                        "ROUTE_BLOCKED"
                        if blocker_id
                        else (
                            "HELD_ENVELOPE_COLLISION"
                            if route_only_failure
                            else "NO_TRANSFER_PLAN"
                        )
                    ),
                    message=(
                        f"No feasible transfer plan from {binding.object_id} "
                        f"to {binding.destination_id}"
                    ),
                    details=details,
                    retryable=bool(blocker_id),
                )
            )

        (
            _,
            navigation,
            stance,
            placement,
            to_transport,
            placement_path,
        ) = best
        transfer_dependencies = snapshot.dependency_versions(
            (destination.entity_id,),
            include_nav_obstacles=True,
            ignore_nav_obstacle_ids=(object_state.entity_id,),
        )
        metadata = _metadata(
            snapshot,
            self._robot_model,
            dependencies=transfer_dependencies,
            payload_transform_hash=payload_transform_hash(
                object_state.entity_id, object_state.radius
            ),
            assumptions=("object will be held before transfer execution",),
            seed=seed,
        )
        transfer = TransferPlan(
            artifact_id=artifact_id("transfer"),
            metadata=metadata,
            object_id=object_state.entity_id,
            destination_id=destination.entity_id,
            navigation_plan_ref=navigation.artifact_id,
            destination_stance=stance,
            placement_pose=placement,
            transport_joints=transport_joints,
            to_transport_path=to_transport,
            placement_path=placement_path,
            placement_yaw_tolerance=placement_yaw_tolerance,
        )
        return PlannedTransfer(navigation=navigation, transfer=transfer)


@dataclass(frozen=True)
class CapabilityRegistry:
    navigation: NavigationCapability
    manipulation: ManipulationCapability
    transfer: TransferCapability

    @classmethod
    def create(cls, robot_model: RobotModel) -> CapabilityRegistry:
        navigation = NavigationCapability(robot_model)
        return cls(
            navigation=navigation,
            manipulation=ManipulationCapability(robot_model, navigation),
            transfer=TransferCapability(robot_model, navigation),
        )


def _metadata(
    snapshot: WorldSnapshot,
    robot_model: RobotModel,
    *,
    dependencies: tuple[tuple[str, int], ...],
    payload_transform_hash: str | None = None,
    assumptions: tuple[str, ...] = (),
    seed: int,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        snapshot_ref=snapshot.snapshot_ref,
        dependency_versions=dependencies,
        frame_graph_revision=snapshot.frame_graph_revision,
        robot_state_epoch=snapshot.robot.state_epoch,
        robot_model_version=robot_model.model_version,
        collision_model_version=robot_model.collision_model_version,
        payload_transform_hash=payload_transform_hash,
        assumptions=assumptions,
        random_seed=seed,
    )


def _stance_candidates(
    target: Pose, snapshot: WorldSnapshot
) -> tuple[GridCell, ...]:
    center = snapshot.grid.world_to_cell(target)
    stand_off = 1
    offsets = (
        (stand_off, 0),
        (-stand_off, 0),
        (0, stand_off),
        (0, -stand_off),
        (stand_off, stand_off),
        (stand_off, -stand_off),
        (-stand_off, stand_off),
        (-stand_off, -stand_off),
    )
    return tuple(
        (center[0] + dx, center[1] + dy)
        for dx, dy in offsets
        if snapshot.grid.in_bounds((center[0] + dx, center[1] + dy))
    )


def _placement_candidates(
    destination_pose: Pose, properties: Mapping[str, Any]
) -> tuple[Pose, ...]:
    offsets = properties.get("placement_offsets", ((0.0, 0.0),))
    return tuple(
        destination_pose.translated(float(dx), float(dy))
        for dx, dy in offsets
    )


def _find_verified_blocker(
    *,
    start: Pose,
    failed_goals: list[tuple[Pose, float]],
    snapshot: WorldSnapshot,
    object_id: str,
) -> tuple[str, Mapping[str, Any]] | None:
    start_cell = snapshot.grid.world_to_cell(start)
    witnesses: list[tuple[float, str, Pose]] = []
    for entity in snapshot.entities.values():
        if entity.entity_id == object_id:
            continue
        if not bool(entity.property("nav_obstacle", False)):
            continue
        if not bool(entity.property("movable", False)):
            continue
        for goal, clearance in failed_goals:
            occupied, _ = snapshot.occupancy(
                ignore_entity_ids=(object_id, entity.entity_id),
                inflation=clearance,
            )
            try:
                result = astar_grid(
                    start_cell,
                    snapshot.grid.world_to_cell(goal),
                    occupied=occupied,
                    width=snapshot.grid.width,
                    height=snapshot.grid.height,
                )
            except PathNotFound:
                continue
            witnesses.append((result.cost, entity.entity_id, goal))
            break
    if not witnesses:
        return None
    witnesses.sort()
    cost, blocker_id, restored_goal = witnesses[0]
    return blocker_id, {
        "method": "counterfactual_astar",
        "restored_goal": restored_goal,
        "restored_path_cost": cost * snapshot.grid.resolution,
    }
