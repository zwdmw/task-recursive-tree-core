from __future__ import annotations

from math import hypot

import pytest

from task_recursive_tree.world.regions import (
    BatchSpaceAllocator,
    LayoutItem,
    LayoutRequest,
    LayoutVerdict,
    RegionDefinition,
    RegionExclusion,
    RegionGeometry,
    RegionOccupancySnapshot,
    RegionOccupancyState,
    RegionOccupant,
    RegionReasoner,
    RegionRequirement,
    RegionRequirementKind,
    RegionShape,
    RequirementPhase,
    SpaceReservation,
)


def _definition(
    *,
    width: float = 0.6,
    depth: float = 0.4,
) -> RegionDefinition:
    return RegionDefinition(
        region_ref="table_1/support",
        owner_id="table_1",
        selector="support",
        geometry=RegionGeometry(
            RegionShape.RECTANGLE,
            width=width,
            depth=depth,
        ),
        edge_margin=0.01,
        clearance=0.01,
    )


def test_physical_empty_is_distinct_from_reserved_space() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=7,
        reservations=(
            SpaceReservation(
                reservation_id="batch:apple_1",
                entity_id="apple_1",
                x=0.0,
                y=0.0,
                radius=0.04,
                world_revision=7,
                reservation_group="batch",
                artifact_ref="layout/1",
            ),
        ),
    )
    requirement = RegionRequirement(
        kind=RegionRequirementKind.EMPTY,
        phase=RequirementPhase.BEFORE_BATCH,
        region_ref="table_1/support",
        owner_id="table_1",
    )

    assert snapshot.state is RegionOccupancyState.EMPTY
    assert snapshot.physical_state is RegionOccupancyState.EMPTY
    assert (
        snapshot.allocation_state
        is RegionOccupancyState.PARTIALLY_OCCUPIED
    )
    assert RegionReasoner().requirement_satisfied(
        snapshot,
        requirement,
    ) is True
    payload = snapshot.to_dict()
    assert payload["state"] == "empty"
    assert payload["physical_state"] == "empty"
    assert payload["allocation_state"] == "partially_occupied"


def test_batch_allocator_is_deterministic_and_non_overlapping() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=11,
    )
    request = LayoutRequest(
        snapshot=snapshot,
        items=(
            LayoutItem("apple_1", 0.04),
            LayoutItem("apple_2", 0.04),
            LayoutItem("apple_3", 0.04),
        ),
        reservation_group="batch-apples",
    )
    allocator = BatchSpaceAllocator()

    first = allocator.allocate(request)
    second = allocator.allocate(request)

    assert first.plan is not None
    assert second.plan == first.plan
    placements = first.plan.placements
    assert {item.entity_id for item in placements} == {
        "apple_1",
        "apple_2",
        "apple_3",
    }
    for index, item in enumerate(placements):
        assert snapshot.definition.geometry.contains_disc(
            item.x,
            item.y,
            item.radius,
            margin=snapshot.definition.edge_margin,
        )
        for other in placements[index + 1 :]:
            assert hypot(item.x - other.x, item.y - other.y) >= (
                item.radius
                + other.radius
                + snapshot.definition.clearance
                - 1e-9
            )


@pytest.mark.parametrize(
    "margin",
    (-0.001, float("inf"), float("-inf"), float("nan")),
)
def test_layout_request_rejects_invalid_robust_clearance_margin(
    margin,
) -> None:
    with pytest.raises(
        ValueError,
        match="robust_clearance_margin must be finite and non-negative",
    ):
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=_definition(),
                world_revision=11,
            ),
            items=(),
            reservation_group="invalid-robust-margin",
            robust_clearance_margin=margin,
        )


def test_batch_allocator_applies_and_records_robust_clearance() -> None:
    definition = _definition(width=1.0, depth=1.0)
    obstacle = RegionOccupant(
        entity_id="table_1",
        radius=0.10,
        x=0.0,
        y=0.0,
        movable=False,
        support_id="floor_1",
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=definition,
                world_revision=12,
                occupants=(obstacle,),
            ),
            items=(LayoutItem("apple_2", 0.04),),
            reservation_group="floor-staging",
            robust_clearance_margin=0.04,
        )
    )

    assert result.plan is not None
    placement = result.plan.placements[0]
    assert hypot(
        placement.x - obstacle.x,
        placement.y - obstacle.y,
    ) + 1e-9 >= (
        placement.effective_operation_radius
        + obstacle.radius
        + definition.clearance
        + 0.04
    )
    assert result.plan.robust_clearance_margin == 0.04
    assert result.plan.to_dict()["robust_clearance_margin_m"] == 0.04


def test_batch_allocator_backtracks_when_complete_layout_is_rejected() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(width=1.0, depth=1.0),
        world_revision=12,
    )
    checked: list[tuple[tuple[str, float, float], ...]] = []

    def accept_after_first(
        placements,
    ) -> bool:
        checked.append(
            tuple(
                (item.entity_id, item.x, item.y)
                for item in placements
            )
        )
        return len(checked) > 1

    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=snapshot,
            items=(LayoutItem("apple_2", 0.04),),
            reservation_group="lazy-complete-layout-check",
            layout_filter=accept_after_first,
        )
    )

    assert result.plan is not None
    assert len(checked) == 2
    selected = result.plan.placements[0]
    assert checked[0] != (
        (selected.entity_id, selected.x, selected.y),
    )
    assert checked[1] == (
        (selected.entity_id, selected.x, selected.y),
    )
    assert result.plan.search_states >= 2


def test_robust_clearance_margin_participates_in_plan_identity() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=12,
    )
    allocator = BatchSpaceAllocator()
    base = allocator.allocate(
        LayoutRequest(
            snapshot=snapshot,
            items=(LayoutItem("apple_2", 0.04),),
            reservation_group="same-layout-input",
        )
    )
    robust = allocator.allocate(
        LayoutRequest(
            snapshot=snapshot,
            items=(LayoutItem("apple_2", 0.04),),
            reservation_group="same-layout-input",
            robust_clearance_margin=0.04,
        )
    )

    assert base.plan is not None
    assert robust.plan is not None
    assert base.plan.placements == robust.plan.placements
    assert base.plan.plan_id != robust.plan.plan_id


def test_circle_allocator_finds_symmetric_two_disc_layout() -> None:
    definition = RegionDefinition(
        region_ref="plate_1/interior",
        owner_id="plate_1",
        selector="interior",
        geometry=RegionGeometry(
            RegionShape.CIRCLE,
            radius=0.14,
        ),
        edge_margin=0.015,
        clearance=0.015,
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=definition,
                world_revision=12,
            ),
            items=(
                LayoutItem("apple_1", 0.04),
                LayoutItem("apple_2", 0.04),
            ),
            reservation_group="red-apples-on-plate",
        )
    )

    assert result.plan is not None
    placements = result.plan.placements
    assert len(placements) == 2
    assert hypot(
        placements[0].x - placements[1].x,
        placements[0].y - placements[1].y,
    ) >= 0.095 - 1e-9
    assert all(
        definition.placement_issue(
            placement.x,
            placement.y,
            placement.radius,
        )
        is None
        for placement in placements
    )


def test_circle_candidate_pitch_uses_clearance() -> None:
    definition = RegionDefinition(
        region_ref="small_plate/interior",
        owner_id="small_plate",
        selector="interior",
        geometry=RegionGeometry(
            RegionShape.CIRCLE,
            radius=0.10,
        ),
        edge_margin=0.005,
        clearance=0.03,
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=definition,
                world_revision=13,
            ),
            items=(
                LayoutItem("ball_1", 0.04),
                LayoutItem("ball_2", 0.04),
            ),
            reservation_group="clearance-sensitive",
        )
    )

    assert result.plan is not None
    first, second = result.plan.placements
    assert hypot(first.x - second.x, first.y - second.y) >= 0.11 - 1e-9


def test_batch_allocator_reserves_placement_operation_envelopes() -> None:
    operation_phases = frozenset({
        "terminal_descent",
        "open_at_release",
        "vertical_retreat",
    })
    definition = RegionDefinition(
        region_ref="plate_1/interior",
        owner_id="plate_1",
        selector="interior",
        geometry=RegionGeometry(
            RegionShape.CIRCLE,
            radius=0.14,
        ),
        edge_margin=0.015,
        clearance=0.015,
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=definition,
                world_revision=14,
            ),
            items=(
                LayoutItem(
                    "apple_1",
                    0.04,
                    placement_operation_envelope_radius=0.075,
                    placement_operation_envelope_model_ref=(
                        "test-envelope/1.0"
                    ),
                    placement_operation_envelope_phases=operation_phases,
                ),
                LayoutItem(
                    "apple_2",
                    0.04,
                    placement_operation_envelope_radius=0.075,
                    placement_operation_envelope_model_ref=(
                        "test-envelope/1.0"
                    ),
                    placement_operation_envelope_phases=operation_phases,
                ),
            ),
            reservation_group="operation-envelope",
        )
    )

    assert result.plan is not None
    first, second = result.plan.placements
    assert hypot(first.x - second.x, first.y - second.y) >= 0.13 - 1e-9
    assert all(placement.radius == 0.04 for placement in (first, second))
    assert all(
        placement.effective_operation_radius == 0.075
        for placement in (first, second)
    )
    assert all(
        placement.placement_operation_envelope_phases == operation_phases
        for placement in (first, second)
    )
    assert all(
        definition.placement_issue(
            placement.x,
            placement.y,
            placement.radius,
        )
        is None
        for placement in (first, second)
    )
    assert all(
        not definition.geometry.contains_disc(
            placement.x,
            placement.y,
            placement.effective_operation_radius,
            margin=definition.edge_margin,
        )
        for placement in (first, second)
    )
    assert all(
        reservation.radius == 0.04
        and reservation.effective_operation_radius == 0.075
        and reservation.placement_operation_envelope_model_ref
        == "test-envelope/1.0"
        and reservation.placement_operation_envelope_phases
        == operation_phases
        for reservation in result.plan.reservations
    )


def test_exclusion_inflates_only_for_matching_operation_phase() -> None:
    operation_phases = frozenset({
        "terminal_descent",
        "open_at_release",
        "vertical_retreat",
    })
    handle_geometry = RegionGeometry(
        RegionShape.RECTANGLE,
        width=0.015,
        depth=0.26,
    )
    release_drop_only = RegionDefinition(
        region_ref="box_1/interior",
        owner_id="box_1",
        selector="interior",
        geometry=RegionGeometry(
            RegionShape.RECTANGLE,
            width=0.21,
            depth=0.21,
        ),
        edge_margin=0.004,
        clearance=0.004,
        exclusions=(
            RegionExclusion(
                exclusion_ref="box_1_handle",
                geometry=handle_geometry,
                clearance=0.004,
                blocked_phases=frozenset({"release_drop"}),
            ),
        ),
    )
    retreat_blocked = RegionDefinition(
        region_ref="box_1/interior",
        owner_id="box_1",
        selector="interior",
        geometry=release_drop_only.geometry,
        edge_margin=0.004,
        clearance=0.004,
        exclusions=(
            RegionExclusion(
                exclusion_ref="box_1_handle",
                geometry=handle_geometry,
                clearance=0.004,
                blocked_phases=frozenset({"vertical_retreat"}),
            ),
        ),
    )

    assert release_drop_only.placement_issue(
        0.055,
        0.047,
        0.04,
        operation_radius=0.092,
        operation_phases=operation_phases,
    ) is None
    assert release_drop_only.placement_issue(
        0.04,
        0.0,
        0.04,
        operation_radius=0.092,
        operation_phases=operation_phases,
    ) == "excluded:box_1_handle"
    assert retreat_blocked.placement_issue(
        0.055,
        0.047,
        0.04,
        operation_radius=0.092,
        operation_phases=operation_phases,
    ) == "excluded:box_1_handle"
    assert retreat_blocked.placement_issue(
        0.055,
        0.047,
        0.04,
        operation_radius=0.092,
    ) == "excluded:box_1_handle"


def test_strict_slots_avoid_fixed_exclusion_in_declared_order() -> None:
    definition = RegionDefinition(
        region_ref="box_1/interior",
        owner_id="box_1",
        selector="interior",
        geometry=RegionGeometry(
            RegionShape.RECTANGLE,
            width=0.21,
            depth=0.21,
        ),
        edge_margin=0.004,
        clearance=0.004,
        exclusions=(
            RegionExclusion(
                exclusion_ref="box_1_handle",
                geometry=RegionGeometry(
                    RegionShape.RECTANGLE,
                    width=0.015,
                    depth=0.26,
                ),
                clearance=0.004,
                source_entity_ref="box_1_handle",
            ),
        ),
        placement_slots=(
            (0.055, 0.047),
            (-0.055, 0.047),
            (0.055, -0.047),
            (-0.055, -0.047),
        ),
        placement_slots_strict=True,
    )

    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=definition,
                world_revision=14,
            ),
            items=(
                LayoutItem("apple_1", 0.04),
                LayoutItem("apple_2", 0.04),
            ),
            reservation_group="two-red-apples",
        )
    )

    assert result.plan is not None
    assert [
        (placement.entity_id, placement.x, placement.y)
        for placement in result.plan.placements
    ] == [
        ("apple_1", 0.055, 0.047),
        ("apple_2", -0.055, 0.047),
    ]
    assert definition.placement_issue(0.0, 0.0, 0.04) == \
        "not_declared_slot"
    assert all(
        definition.placement_issue(
            placement.x,
            placement.y,
            placement.radius,
        ) is None
        for placement in result.plan.placements
    )


def test_batch_allocator_proves_individual_footprint_infeasible() -> None:
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=_definition(width=0.3, depth=0.3),
                world_revision=2,
            ),
            items=(LayoutItem("plate_1", 0.16),),
            reservation_group="oversized",
        )
    )

    assert result.failure is not None
    assert result.failure.verdict is LayoutVerdict.INFEASIBLE_PROVEN
    assert result.failure.code == "NO_FEASIBLE_LAYOUT"
    assert result.failure.proven is True
    assert (
        result.failure.details["reason"]
        == "individual_footprint_too_large"
    )


def test_batch_allocator_reports_search_budget_exhaustion() -> None:
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=_definition(),
                world_revision=3,
            ),
            items=(
                LayoutItem("apple_1", 0.04),
                LayoutItem("apple_2", 0.04),
            ),
            reservation_group="budget",
            max_search_states=1,
        )
    )

    assert result.failure is not None
    assert result.failure.verdict is LayoutVerdict.SEARCH_EXHAUSTED
    assert result.failure.code == "SEARCH_BUDGET_EXHAUSTED"
    assert result.failure.proven is False


def test_batch_allocator_rejects_incomplete_occupancy_evidence() -> None:
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=RegionOccupancySnapshot(
                definition=_definition(),
                world_revision=4,
                complete=False,
            ),
            items=(LayoutItem("apple_1", 0.04),),
            reservation_group="unknown",
        )
    )

    assert result.failure is not None
    assert result.failure.verdict is LayoutVerdict.PERCEPTION_INSUFFICIENT
    assert result.failure.code == "PERCEPTION_INSUFFICIENT"
