from __future__ import annotations

from task_recursive_tree.selection.engine import SpatialSelectorEngine
from task_recursive_tree.selection.model import (
    SelectionRelation,
    SpatialSelector,
)
from task_recursive_tree.world.geometry import Pose
from task_recursive_tree.world.state import (
    EntityState,
    GridMap,
    RobotState,
    WorldSnapshot,
)


def snapshot() -> WorldSnapshot:
    entities = {
        "left": EntityState("left", "object", Pose(1.0, 2.0)),
        "middle": EntityState("middle", "object", Pose(3.0, 2.0)),
        "right": EntityState("right", "object", Pose(5.0, 2.0)),
    }
    return WorldSnapshot(
        revision=4,
        frame_graph_revision=0,
        grid=GridMap(8, 8),
        robot=RobotState(Pose(2.8, 2.0), (0.0, 0.0)),
        entities=entities,
    )


def test_semantic_spatial_relations_are_deterministic() -> None:
    engine = SpatialSelectorEngine()
    leftmost = engine.select(
        SpatialSelector(
            entity_kind="object",
            relation=SelectionRelation.LEFTMOST,
        ),
        snapshot(),
    )
    nearest = engine.select(
        SpatialSelector(
            entity_kind="object",
            relation=SelectionRelation.NEAREST,
        ),
        snapshot(),
    )
    assert leftmost.entity.entity_id == "left"
    assert nearest.entity.entity_id == "middle"


def test_selector_coerces_llm_friendly_values() -> None:
    selector = SpatialSelector(
        entity_kind="object",
        relation="leftmost",  # type: ignore[arg-type]
        required_tags={"red"},  # type: ignore[arg-type]
    )
    assert selector.relation is SelectionRelation.LEFTMOST
    assert selector.required_tags == frozenset({"red"})

