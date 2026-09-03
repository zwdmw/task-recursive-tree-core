from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from task_recursive_tree.selection.model import (
    SelectionRelation,
    SpatialSelector,
)
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import EntityState, WorldSnapshot


class SelectionFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SelectionResult:
    entity: EntityState
    candidate_ids: tuple[str, ...]
    rationale: str


class SpatialSelectorEngine:
    """Grounds semantic spatial expressions without invoking motion planners."""

    def select(
        self, selector: SpatialSelector, snapshot: WorldSnapshot
    ) -> SelectionResult:
        candidates = [
            entity
            for entity in snapshot.entities.values()
            if entity.kind == selector.entity_kind
            and selector.required_tags.issubset(entity.tags)
        ]
        if selector.region_id:
            region = snapshot.entity(selector.region_id)
            candidates = [
                entity
                for entity in candidates
                if self._inside_region(entity.pose, region)
            ]

        candidates.sort(key=lambda entity: entity.entity_id)
        if selector.relation is SelectionRelation.EXACT:
            candidates = [
                entity
                for entity in candidates
                if entity.entity_id == selector.entity_id
            ]

        if not candidates:
            raise SelectionFailure(
                "BINDING_NOT_FOUND",
                f"No entity matches selector {selector!r}",
            )

        reference = self._reference_pose(selector, snapshot)
        selected = self._rank(selector, candidates, reference, snapshot)
        return SelectionResult(
            entity=selected,
            candidate_ids=tuple(entity.entity_id for entity in candidates),
            rationale=(
                f"{selector.relation.value} in frame={selector.frame}, "
                f"metric={selector.metric}, tie={selector.tie_policy}"
            ),
        )

    def _rank(
        self,
        selector: SpatialSelector,
        candidates: list[EntityState],
        reference: Pose,
        snapshot: WorldSnapshot,
    ) -> EntityState:
        relation = selector.relation
        if relation in {SelectionRelation.EXACT, SelectionRelation.FIRST}:
            return candidates[0]
        if relation is SelectionRelation.LEFTMOST:
            return min(candidates, key=lambda item: (item.pose.x, item.entity_id))
        if relation is SelectionRelation.RIGHTMOST:
            return min(candidates, key=lambda item: (-item.pose.x, item.entity_id))
        if relation is SelectionRelation.NEAREST:
            return min(
                candidates,
                key=lambda item: (
                    hypot(item.pose.x - reference.x, item.pose.y - reference.y),
                    item.entity_id,
                ),
            )
        if relation is SelectionRelation.FARTHEST:
            return min(
                candidates,
                key=lambda item: (
                    -hypot(
                        item.pose.x - reference.x,
                        item.pose.y - reference.y,
                    ),
                    item.entity_id,
                ),
            )
        if relation is SelectionRelation.CORNER:
            if not selector.region_id:
                raise SelectionFailure(
                    "BINDING_INVALID",
                    "A corner selector requires region_id",
                )
            region = snapshot.entity(selector.region_id)
            target = self._corner_pose(region, selector.corner or "")
            return min(
                candidates,
                key=lambda item: (
                    hypot(item.pose.x - target.x, item.pose.y - target.y),
                    item.entity_id,
                ),
            )
        raise SelectionFailure(
            "BINDING_INVALID", f"Unsupported relation: {relation.value}"
        )

    @staticmethod
    def _reference_pose(
        selector: SpatialSelector, snapshot: WorldSnapshot
    ) -> Pose:
        if selector.reference_entity_id:
            return snapshot.entity(selector.reference_entity_id).pose
        if selector.reference == "robot":
            return snapshot.robot.base_pose
        if selector.reference == "origin":
            return Pose(0.0, 0.0)
        raise SelectionFailure(
            "BINDING_INVALID",
            f"Unknown selection reference: {selector.reference}",
        )

    @staticmethod
    def _inside_region(pose: Pose, region: EntityState) -> bool:
        bounds = region.property("bounds")
        if bounds:
            min_x, min_y, max_x, max_y = bounds
            return min_x <= pose.x <= max_x and min_y <= pose.y <= max_y
        return hypot(pose.x - region.pose.x, pose.y - region.pose.y) <= region.radius

    @staticmethod
    def _corner_pose(region: EntityState, corner: str) -> Pose:
        bounds = region.property("bounds")
        if bounds:
            min_x, min_y, max_x, max_y = bounds
        else:
            min_x = region.pose.x - region.radius
            min_y = region.pose.y - region.radius
            max_x = region.pose.x + region.radius
            max_y = region.pose.y + region.radius
        corners = {
            "southwest": Pose(min_x, min_y),
            "southeast": Pose(max_x, min_y),
            "northwest": Pose(min_x, max_y),
            "northeast": Pose(max_x, max_y),
        }
        try:
            return corners[corner.lower()]
        except KeyError as exc:
            raise SelectionFailure(
                "BINDING_INVALID", f"Unknown corner: {corner}"
            ) from exc

