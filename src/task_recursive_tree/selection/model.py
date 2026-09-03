from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SelectionRelation(str, Enum):
    EXACT = "exact"
    FIRST = "first"
    LEFTMOST = "leftmost"
    RIGHTMOST = "rightmost"
    NEAREST = "nearest"
    FARTHEST = "farthest"
    CORNER = "corner"


@dataclass(frozen=True)
class SpatialSelector:
    entity_kind: str
    relation: SelectionRelation = SelectionRelation.FIRST
    entity_id: str | None = None
    required_tags: frozenset[str] = frozenset()
    region_id: str | None = None
    reference_entity_id: str | None = None
    reference: str = "robot"
    frame: str = "world"
    metric: str = "euclidean"
    cardinality: str = "one"
    tie_policy: str = "stable_id"
    corner: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.relation, str):
            object.__setattr__(
                self, "relation", SelectionRelation(self.relation)
            )
        if not isinstance(self.required_tags, frozenset):
            object.__setattr__(
                self, "required_tags", frozenset(self.required_tags)
            )
        if self.cardinality != "one":
            raise ValueError("The reference runtime currently requires cardinality='one'")
        if self.tie_policy != "stable_id":
            raise ValueError("The reference runtime supports tie_policy='stable_id'")
        if self.metric != "euclidean":
            raise ValueError("The reference runtime supports metric='euclidean'")
        if self.relation is SelectionRelation.EXACT and not self.entity_id:
            raise ValueError("EXACT selection requires entity_id")
        if self.relation is SelectionRelation.CORNER and not self.corner:
            raise ValueError("CORNER selection requires corner")

    @classmethod
    def exact(cls, entity_id: str, entity_kind: str) -> SpatialSelector:
        return cls(
            entity_kind=entity_kind,
            relation=SelectionRelation.EXACT,
            entity_id=entity_id,
        )
