from __future__ import annotations

from hashlib import sha256
from dataclasses import dataclass, field
from math import ceil, floor, hypot
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from task_recursive_tree.world.geometry import Pose

GridCell = tuple[int, int]
JointConfiguration = tuple[float, ...]


@dataclass(frozen=True)
class GridMap:
    width: int
    height: int
    resolution: float = 1.0
    origin_x: float = 0.0
    origin_y: float = 0.0
    static_occupied: frozenset[GridCell] = frozenset()
    revision: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Grid dimensions must be positive")
        if self.resolution <= 0.0:
            raise ValueError("Grid resolution must be positive")
        for cell in self.static_occupied:
            if not self.in_bounds(cell):
                raise ValueError(f"Static occupied cell is out of bounds: {cell}")

    def in_bounds(self, cell: GridCell) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def world_to_cell(self, pose: Pose) -> GridCell:
        return (
            floor((pose.x - self.origin_x) / self.resolution),
            floor((pose.y - self.origin_y) / self.resolution),
        )

    def cell_to_pose(self, cell: GridCell, *, yaw: float = 0.0) -> Pose:
        return Pose(
            x=self.origin_x + (cell[0] + 0.5) * self.resolution,
            y=self.origin_y + (cell[1] + 0.5) * self.resolution,
            yaw=yaw,
        )

    def footprint(self, pose: Pose, radius: float) -> frozenset[GridCell]:
        cells: set[GridCell] = set()
        min_x = floor(
            (pose.x - radius - self.origin_x) / self.resolution
        )
        max_x = floor(
            (pose.x + radius - self.origin_x) / self.resolution
        )
        min_y = floor(
            (pose.y - radius - self.origin_y) / self.resolution
        )
        max_y = floor(
            (pose.y + radius - self.origin_y) / self.resolution
        )
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                cell = (cell_x, cell_y)
                if not self.in_bounds(cell):
                    continue
                left = self.origin_x + cell_x * self.resolution
                right = left + self.resolution
                bottom = self.origin_y + cell_y * self.resolution
                top = bottom + self.resolution
                closest_x = max(left, min(pose.x, right))
                closest_y = max(bottom, min(pose.y, top))
                if hypot(
                    pose.x - closest_x, pose.y - closest_y
                ) <= radius + 1e-9:
                    cells.add(cell)
        return frozenset(cells)

    def inflated_static(self, clearance: float) -> frozenset[GridCell]:
        if clearance <= 0.0:
            return self.static_occupied
        result = set(self.static_occupied)
        cell_span = ceil(
            (clearance + self.resolution * 0.5) / self.resolution
        )
        for occupied_x, occupied_y in self.static_occupied:
            left = self.origin_x + occupied_x * self.resolution
            right = left + self.resolution
            bottom = self.origin_y + occupied_y * self.resolution
            top = bottom + self.resolution
            for dx in range(-cell_span, cell_span + 1):
                for dy in range(-cell_span, cell_span + 1):
                    candidate = (occupied_x + dx, occupied_y + dy)
                    if not self.in_bounds(candidate):
                        continue
                    center = self.cell_to_pose(candidate)
                    closest_x = max(left, min(center.x, right))
                    closest_y = max(bottom, min(center.y, top))
                    if hypot(
                        center.x - closest_x, center.y - closest_y
                    ) <= clearance + 1e-9:
                        result.add(candidate)
        return frozenset(result)


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    kind: str
    pose: Pose
    radius: float = 0.2
    tags: frozenset[str] = frozenset()
    properties: Mapping[str, Any] = field(default_factory=dict)
    version: int = 0
    generation: int = 0

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ValueError("entity version must not be negative")
        if self.generation < 0:
            raise ValueError("entity generation must not be negative")
        object.__setattr__(
            self, "properties", MappingProxyType(dict(self.properties))
        )

    @property
    def dependency_version(self) -> int:
        return (self.generation << 32) | self.version

    def property(self, name: str, default: Any = None) -> Any:
        return self.properties.get(name, default)


@dataclass(frozen=True)
class RobotState:
    base_pose: Pose
    joints: JointConfiguration
    gripper_open: bool = True
    held_object_id: str | None = None
    state_epoch: int = 0


@dataclass(frozen=True)
class Observation:
    robot: RobotState
    entities: tuple[EntityState, ...]
    frame_graph_revision: int = 0
    source_epoch: str = "default"
    sequence: int = 0
    observed_at: float | None = None
    complete: bool = True
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("observation sequence must not be negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                "observation confidence must be between 0 and 1"
            )


@dataclass(frozen=True)
class WorldSnapshot:
    revision: int
    frame_graph_revision: int
    grid: GridMap
    robot: RobotState
    entities: Mapping[str, EntityState]
    source_epoch: str = "default"
    observation_sequence: int = 0
    observed_at: float | None = None
    observation_complete: bool = True
    observation_confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "entities", MappingProxyType(dict(self.entities))
        )

    @property
    def snapshot_ref(self) -> str:
        return f"world:{self.revision}"

    def entity(self, entity_id: str) -> EntityState:
        try:
            return self.entities[entity_id]
        except KeyError as exc:
            raise KeyError(f"Unknown entity: {entity_id}") from exc

    def occupancy(
        self,
        *,
        ignore_entity_ids: Iterable[str] = (),
        inflation: float = 0.0,
    ) -> tuple[frozenset[GridCell], Mapping[GridCell, str]]:
        ignored = set(ignore_entity_ids)
        occupied = set(self.grid.inflated_static(inflation))
        owners: dict[GridCell, str] = {}
        for entity in self.entities.values():
            if entity.entity_id in ignored:
                continue
            if not bool(entity.property("nav_obstacle", False)):
                continue
            cells = self.grid.footprint(
                entity.pose, max(0.0, entity.radius + inflation)
            )
            occupied.update(cells)
            for cell in cells:
                owners[cell] = entity.entity_id
        return frozenset(occupied), MappingProxyType(owners)

    def nav_occupancy_fingerprint(
        self, ignore_entity_ids: Iterable[str] = ()
    ) -> int:
        ignored = set(ignore_entity_ids)
        records = [
            (
                entity.entity_id,
                entity.generation,
                entity.version,
                round(entity.pose.x, 6),
                round(entity.pose.y, 6),
                round(entity.radius, 6),
            )
            for entity in self.entities.values()
            if entity.entity_id not in ignored
            and bool(entity.property("nav_obstacle", False))
        ]
        records.sort()
        canonical = repr(
            (
                self.grid.revision,
                tuple(sorted(self.grid.static_occupied)),
                tuple(records),
            )
        ).encode("utf-8")
        return int.from_bytes(sha256(canonical).digest()[:8], "big")

    def arm_state_fingerprint(self) -> int:
        canonical = repr(
            tuple(round(value, 8) for value in self.robot.joints)
        ).encode("ascii")
        return int.from_bytes(sha256(canonical).digest()[:8], "big")

    def dependency_versions(
        self,
        entity_ids: Iterable[str] = (),
        *,
        include_nav_obstacles: bool = False,
        ignore_nav_obstacle_ids: Iterable[str] = (),
        include_robot: bool = False,
        include_arm: bool = False,
    ) -> tuple[tuple[str, int], ...]:
        dependencies: dict[str, int] = {
            "map": self.grid.revision,
            "frame_graph": self.frame_graph_revision,
        }
        for entity_id in entity_ids:
            dependencies[f"entity:{entity_id}"] = (
                self.entity(entity_id).dependency_version
            )
        if include_nav_obstacles:
            ignored = tuple(sorted(set(ignore_nav_obstacle_ids)))
            key = "nav_occupancy:" + ",".join(ignored)
            dependencies[key] = self.nav_occupancy_fingerprint(ignored)
        if include_robot:
            dependencies["robot"] = self.robot.state_epoch
        if include_arm:
            dependencies["arm"] = self.arm_state_fingerprint()
        return tuple(sorted(dependencies.items()))
