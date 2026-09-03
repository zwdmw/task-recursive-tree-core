from __future__ import annotations

from math import hypot
from types import SimpleNamespace

from task_recursive_tree.integrations.gemini_er2 import (
    region_space as region_space_module,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)
from task_recursive_tree.integrations.gemini_er2.region_space import (
    HarnessRegionSpaceAdapter,
    PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION,
    RegionLayoutSelection,
    layout_targets_artifact,
)
from task_recursive_tree.world.regions import (
    BatchSpaceAllocator,
    LayoutFailure,
    LayoutItem,
    LayoutPlacement,
    LayoutRequest,
    LayoutVerdict,
    RegionDefinition,
    RegionGeometry,
    RegionOccupancySnapshot,
    RegionOccupant,
    RegionShape,
    ReservationStatus,
    SpaceReservation,
)


def _definition() -> RegionDefinition:
    return RegionDefinition(
        region_ref="table_1/support",
        owner_id="table_1",
        selector="support",
        geometry=RegionGeometry(
            RegionShape.RECTANGLE,
            width=0.8,
            depth=0.5,
        ),
        allowed_relations=("on_support",),
        edge_margin=0.01,
        clearance=0.01,
    )


def _selection(
    *,
    snapshot: RegionOccupancySnapshot | None = None,
    robust_clearance_margin: float = 0.0,
) -> RegionLayoutSelection:
    snapshot = snapshot or RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=3,
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=snapshot,
            items=(
                LayoutItem("apple_1", 0.04),
                LayoutItem("apple_2", 0.04),
            ),
            reservation_group="batch-apples",
            robust_clearance_margin=robust_clearance_margin,
        )
    )
    assert result.plan is not None
    return RegionLayoutSelection(
        region={
            "region_ref": "table_1/support",
            "owner_ref": "table_1",
        },
        snapshot=snapshot,
        plan=result.plan,
        failure=None,
    )


def test_layout_target_adapter_emits_harness_schema_and_reservations() -> None:
    artifact = layout_targets_artifact(
        target_ref="layout/1",
        selection=_selection(),
        subject_ids=("apple_1", "apple_2"),
    )

    assert artifact["kind"] == "layout_targets"
    assert artifact["artifact_kind"] == "layout_targets"
    assert artifact["artifact_ref"] == "layout/1"
    assert artifact["anchor_id"] == "table_1"
    assert artifact["reservation_status"] == "active"
    assert (
        artifact["layout_model_version"]
        == PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION
    )
    assert set(artifact["targets"]) == {"apple_1", "apple_2"}
    assert {
        target["reservation_status"]
        for target in artifact["targets"].values()
    } == {"active"}
    assert {
        target["placement_operation_envelope_radius_m"]
        for target in artifact["targets"].values()
    } == {0.04}
    assert {
        target["placement_operation_envelope_model_ref"]
        for target in artifact["targets"].values()
    } == {None}


def test_layout_target_artifact_preserves_robust_clearance_margin() -> None:
    selection = _selection(robust_clearance_margin=0.04)
    artifact = layout_targets_artifact(
        target_ref="layout/robust-floor-target",
        selection=selection,
        subject_ids=("apple_1", "apple_2"),
    )

    assert selection.plan is not None
    assert selection.plan.to_dict()["robust_clearance_margin_m"] == 0.04
    assert artifact["robust_clearance_margin_m"] == 0.04


def test_layout_target_artifact_preserves_operation_envelope() -> None:
    operation_phases = frozenset({
        "terminal_descent",
        "open_at_release",
        "vertical_retreat",
    })
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=4,
    )
    result = BatchSpaceAllocator().allocate(
        LayoutRequest(
            snapshot=snapshot,
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
            reservation_group="batch-envelope",
        )
    )
    assert result.plan is not None

    artifact = layout_targets_artifact(
        target_ref="layout/envelope",
        selection=RegionLayoutSelection(
            region={
                "region_ref": "table_1/support",
                "owner_ref": "table_1",
            },
            snapshot=snapshot,
            plan=result.plan,
            failure=None,
        ),
        subject_ids=("apple_1", "apple_2"),
    )

    assert {
        target["radius_m"] for target in artifact["targets"].values()
    } == {0.04}
    assert {
        target["placement_operation_envelope_radius_m"]
        for target in artifact["targets"].values()
    } == {0.075}
    assert {
        target["placement_operation_envelope_model_ref"]
        for target in artifact["targets"].values()
    } == {"test-envelope/1.0"}
    assert {
        tuple(target["placement_operation_envelope_phases"])
        for target in artifact["targets"].values()
    } == {
        (
            "open_at_release",
            "terminal_descent",
            "vertical_retreat",
        )
    }


def test_placement_region_selection_is_locked_to_requested_owner() -> None:
    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            pass

        def scene_snapshot(self):
            return {"scene": "static"}

        def _region_index(self, snapshot):
            assert snapshot == {"scene": "static"}
            return {
                "table_1/support": {
                    "region_ref": "table_1/support",
                    "owner_ref": "table_1",
                    "selector": "support",
                    "allowed_relations": ["on_support"],
                },
                "plate_1/interior": {
                    "region_ref": "plate_1/interior",
                    "owner_ref": "plate_1",
                    "selector": "interior",
                    "allowed_relations": ["inside_support_region"],
                },
                "floor_1/support": {
                    "region_ref": "floor_1/support",
                    "owner_ref": "floor_1",
                    "selector": "support",
                    "allowed_relations": ["on_support"],
                },
            }

    adapter = StaticAdapter()

    assert adapter.placement_region_ref(
        "plate_1",
        "inside_support_region",
        selector="interior",
    ) == "plate_1/interior"
    assert adapter.placement_region_ref(
        "table_1",
        "on_support",
        requested_region_ref="floor_1/support",
        selector="support",
    ) is None


def test_selector_skips_infeasible_first_candidate() -> None:
    feasible = _selection()
    failed = LayoutFailure(
        verdict=LayoutVerdict.INFEASIBLE_PROVEN,
        code="NO_FEASIBLE_LAYOUT",
        message="small region cannot hold the batch",
        proven=True,
    )

    class Selector(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def scene_snapshot(self):
            return {}

        def candidate_refs(self, params, *, snapshot=None):
            del params, snapshot
            return ("plate_1/interior", "table_1/support")

        def plan_layout(
            self,
            region_ref,
            object_ids,
            **kwargs,
        ):
            del object_ids, kwargs
            self.calls.append(region_ref)
            if region_ref == "plate_1/interior":
                return RegionLayoutSelection(None, None, None, failed)
            return feasible

    selector = Selector()
    result = selector.select_layout(
        ("apple_1", "apple_2"),
        {},
        reservation_group="batch-apples",
    )

    assert result.feasible
    assert selector.calls == [
        "plate_1/interior",
        "table_1/support",
    ]
    assert [item["verdict"] for item in result.candidate_audit] == [
        "infeasible_proven",
        "feasible",
    ]


def test_layout_freshness_allows_expected_batch_revision_changes() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=9,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=-0.15,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self, value):
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=value.world_revision),
            )
            self.harness_root = None
            self.snapshot = value

        def inspect(self, region_ref, **kwargs):
            del region_ref, kwargs
            return self.snapshot

        def footprint_radius(self, entity_id):
            del entity_id
            return 0.04

    adapter = StaticAdapter(snapshot)
    artifact = {
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "artifact_ref": "layout/1",
        "region_ref": "table_1/support",
        "anchor_id": "table_1",
        "world_revision": 3,
        "reservation_group": "batch-apples",
        "subject_ids": ["apple_1", "apple_2"],
        "targets": {
            "apple_1": {
                "local_xy": [-0.15, 0.0],
                "radius_m": 0.04,
                "reservation_status": "fulfilled",
            },
            "apple_2": {
                "local_xy": [0.15, 0.0],
                "radius_m": 0.04,
                "placement_operation_envelope_radius_m": 0.075,
                "placement_operation_envelope_model_ref": (
                    "test-envelope/1.0"
                ),
                "reservation_status": "active",
            },
        },
    }

    assert adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/1",
    ) == (True, "")

    adapter.snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=10,
        occupants=(
            *snapshot.occupants,
            RegionOccupant(
                entity_id="cup_1",
                radius=0.05,
                x=0.15,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )
    valid, reason = adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/1",
    )
    assert valid is False
    assert "cup_1" in reason


def test_active_layout_target_keeps_clear_of_fulfilled_subject_envelope() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=10,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=-0.05,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self, value) -> None:
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=10),
            )
            self.snapshot = value

        def inspect(self, region_ref, **kwargs):
            del region_ref, kwargs
            return self.snapshot

    artifact = {
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "artifact_ref": "layout/envelope-refresh",
        "region_ref": "table_1/support",
        "anchor_id": "table_1",
        "world_revision": 9,
        "reservation_group": "batch-apples",
        "subject_ids": ["apple_1", "apple_2"],
        "targets": {
            "apple_1": {
                "local_xy": [-0.05, 0.0],
                "radius_m": 0.04,
                "placement_operation_envelope_radius_m": 0.075,
                "placement_operation_envelope_model_ref": (
                    "test-envelope/1.0"
                ),
                "reservation_status": "fulfilled",
            },
            "apple_2": {
                "local_xy": [0.05, 0.0],
                "radius_m": 0.04,
                "placement_operation_envelope_radius_m": 0.075,
                "placement_operation_envelope_model_ref": (
                    "test-envelope/1.0"
                ),
                "reservation_status": "active",
            },
        },
    }

    adapter = StaticAdapter(snapshot)
    valid, reason = adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/envelope-refresh",
    )
    assert valid is False
    assert "placement envelopes" in reason

    artifact["targets"]["apple_1"]["local_xy"] = [-0.065, 0.0]
    artifact["targets"]["apple_2"]["local_xy"] = [0.065, 0.0]
    adapter.snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=10,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=-0.065,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )

    assert adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/envelope-refresh",
    ) == (True, "")

    artifact["targets"]["apple_2"]["reservation_status"] = "fulfilled"
    artifact["targets"]["apple_1"]["local_xy"] = [-0.05, 0.0]
    artifact["targets"]["apple_2"]["local_xy"] = [0.05, 0.0]
    adapter.snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=11,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=-0.05,
                y=0.0,
                support_id="table_1",
            ),
            RegionOccupant(
                entity_id="apple_2",
                radius=0.04,
                x=0.05,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )

    assert adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/envelope-refresh",
    ) == (True, "")


def test_fulfilled_subject_drift_blocks_remaining_active_target() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=10,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=-0.02,
                y=0.0,
                support_id="table_1",
            ),
        ),
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=10),
            )

        def inspect(self, region_ref, **kwargs):
            del region_ref, kwargs
            return snapshot

    artifact = {
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "artifact_ref": "layout/drift",
        "region_ref": "table_1/support",
        "anchor_id": "table_1",
        "world_revision": 9,
        "reservation_group": "batch-apples",
        "subject_ids": ["apple_1", "apple_2"],
        "targets": {
            "apple_1": {
                "local_xy": [-0.05, 0.0],
                "radius_m": 0.04,
                "reservation_status": "fulfilled",
            },
            "apple_2": {
                "local_xy": [0.05, 0.0],
                "radius_m": 0.04,
                "placement_operation_envelope_radius_m": 0.075,
                "placement_operation_envelope_model_ref": (
                    "test-envelope/1.0"
                ),
                "reservation_status": "active",
            },
        },
    }

    valid, reason = StaticAdapter().validate_layout_targets(
        artifact,
        artifact_ref="layout/drift",
    )

    assert valid is False
    assert "apple_1" in reason
    assert "apple_2" in reason


def test_layout_validation_ignores_only_staged_entity_reservation() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=9,
        reservations=(
            SpaceReservation(
                reservation_id="old-group:cup_1",
                entity_id="cup_1",
                x=0.0,
                y=0.0,
                radius=0.04,
                world_revision=8,
                reservation_group="old-group",
                artifact_ref="layout/old",
                status=ReservationStatus.ACTIVE,
            ),
            SpaceReservation(
                reservation_id="old-group:can_1",
                entity_id="can_1",
                x=0.30,
                y=0.0,
                radius=0.04,
                world_revision=8,
                reservation_group="old-group",
                artifact_ref="layout/old",
                status=ReservationStatus.ACTIVE,
            ),
        ),
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self):
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=9),
                perception=SimpleNamespace(),
            )

        def inspect(self, region_ref, *, scene_snapshot=None):
            del scene_snapshot
            assert region_ref == "table_1/support"
            return snapshot

    adapter = StaticAdapter()
    artifact = {
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "artifact_ref": "layout/new",
        "region_ref": "table_1/support",
        "anchor_id": "table_1",
        "world_revision": 9,
        "reservation_group": "new-group",
        "subject_ids": ["cup_1"],
        "targets": {
            "cup_1": {
                "local_xy": [0.0, 0.0],
                "radius_m": 0.04,
                "placement_operation_envelope_radius_m": 0.075,
                "placement_operation_envelope_model_ref": (
                    "test-envelope/1.0"
                ),
                "reservation_status": "active",
            }
        },
    }

    valid, reason = adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/new",
    )
    assert valid is False
    assert "old-group:cup_1" in reason

    assert adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/new",
        ignored_reservation_bindings=(("layout/old", "cup_1"),),
    ) == (True, "")

    artifact["targets"]["cup_1"]["local_xy"] = [0.30, 0.0]
    valid, reason = adapter.validate_layout_targets(
        artifact,
        artifact_ref="layout/new",
        ignored_reservation_bindings=(("layout/old", "cup_1"),),
    )
    assert valid is False
    assert "old-group:can_1" in reason


def test_layout_fulfillment_requires_target_support_and_position() -> None:
    support = {"apple_1": "table_1"}
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=9,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=0.12,
                y=-0.08,
                support_id="table_1",
            ),
        ),
        evidence_refs=("sim_pose_apple_1",),
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self):
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=9),
                perception=SimpleNamespace(
                    support_of=lambda entity_id: support.get(entity_id),
                ),
            )
            self.snapshot = snapshot

        def inspect(self, region_ref, **kwargs):
            del region_ref, kwargs
            return self.snapshot

    adapter = StaticAdapter()
    artifact = {
        "kind": "layout_targets",
        "source": "task_recursive_tree_batch_allocator/1.0",
        "artifact_ref": "layout/fulfilled",
        "region_ref": "table_1/support",
        "anchor_id": "table_1",
        "targets": {
            "apple_1": {
                "local_xy": [0.12, -0.08],
                "radius_m": 0.04,
                "reservation_status": "active",
            }
        },
    }

    fulfilled, evidence = adapter.assess_layout_target_fulfillment(
        artifact,
        "apple_1",
    )

    assert fulfilled is True
    assert evidence["observed_support_id"] == "table_1"
    assert evidence["position_error_m"] == 0.0

    support["apple_1"] = "floor_1"
    fulfilled, evidence = adapter.assess_layout_target_fulfillment(
        artifact,
        "apple_1",
    )
    assert fulfilled is False
    assert evidence["reason_code"] == "NOT_SUPPORTED_BY_TARGET_ANCHOR"

    support["apple_1"] = "table_1"
    adapter.snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=10,
        occupants=(
            RegionOccupant(
                entity_id="apple_1",
                radius=0.04,
                x=0.22,
                y=-0.08,
                support_id="table_1",
            ),
        ),
    )
    fulfilled, evidence = adapter.assess_layout_target_fulfillment(
        artifact,
        "apple_1",
    )
    assert fulfilled is False
    assert evidence["reason_code"] == "TARGET_POSITION_MISSED"


def test_reservation_reader_ignores_fulfilled_and_malformed_entries() -> None:
    artifacts = {
        "layout/1": {
            "kind": "layout_targets",
            "source": "task_recursive_tree_batch_allocator/1.0",
            "anchor_id": "table_1",
            "world_revision": "not-an-integer",
            "reservation_group": "batch-apples",
            "reservation_status": "active",
            "targets": {
                "apple_1": {
                    "local_xy": [0.0, 0.0],
                    "radius_m": 0.04,
                    "placement_operation_envelope_radius_m": 0.075,
                    "placement_operation_envelope_model_ref": (
                        "test-envelope/1.0"
                    ),
                    "reservation_status": "active",
                },
                "apple_2": {
                    "local_xy": [0.12, 0.0],
                    "radius_m": 0.04,
                    "reservation_status": "fulfilled",
                },
                "broken": {
                    "local_xy": ["bad", 0.0],
                    "radius_m": "bad",
                    "reservation_status": "active",
                },
            },
        },
        "legacy-layout": {
            "kind": "layout_targets",
            "anchor_id": "table_1",
            "targets": {
                "legacy": {
                    "local_xy": [0.2, 0.0],
                    "radius_m": 0.04,
                }
            },
        },
    }
    runtime = SimpleNamespace(
        world=SimpleNamespace(
            revision=12,
            tasks={
                "task-1": SimpleNamespace(artifacts=artifacts),
            },
        ),
        _current_task_id="task-1",
    )
    adapter = HarnessRegionSpaceAdapter.__new__(
        HarnessRegionSpaceAdapter
    )
    adapter.runtime = runtime

    reservations = adapter._reservations("table_1")

    assert len(reservations) == 1
    assert reservations[0].entity_id == "apple_1"
    assert reservations[0].artifact_ref == "layout/1"
    assert reservations[0].reservation_group == "batch-apples"
    assert reservations[0].placement_operation_envelope_phases is None


def test_legacy_target_recovers_missing_operation_phases() -> None:
    operation_phases = frozenset({
        "terminal_descent",
        "open_at_release",
        "vertical_retreat",
    })

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def placement_operation_envelope(self, entity_id, **kwargs):
            assert entity_id == "apple_1"
            assert kwargs == {
                "relation": "on_support",
                "destination_id": "table_1",
                "region_ref": "table_1/support",
            }
            return SimpleNamespace(
                radius=0.092,
                model_ref="current-envelope/1.0",
                phases=operation_phases,
            )

    adapter = StaticAdapter.__new__(StaticAdapter)
    envelope = adapter._target_operation_envelope(
        target={
            "placement_operation_envelope_radius_m": 0.075,
            "placement_operation_envelope_model_ref": "stored-envelope/1.0",
        },
        artifact={},
        entity_id="apple_1",
        relation="on_support",
        destination_id="table_1",
        region_ref="table_1/support",
    )

    assert envelope is not None
    assert envelope.radius == 0.092
    assert envelope.model_ref == "stored-envelope/1.0"
    assert envelope.phases == operation_phases


def test_excluded_layout_points_are_temporary_allocator_reservations() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=11,
    )

    class RecordingAllocator:
        def __init__(self) -> None:
            self.requests = []
            self.delegate = BatchSpaceAllocator()

        def allocate(self, request):
            self.requests.append(request)
            return self.delegate.allocate(request)

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=11),
                perception=SimpleNamespace(
                    entity_pose=lambda _entity_id: None,
                ),
            )
            self.harness_root = None
            self.allocator = RecordingAllocator()

        def inspect(self, region_ref, **_kwargs):
            assert region_ref == "table_1/support"
            return snapshot

        def region(self, region_ref):
            assert region_ref == "table_1/support"
            return {
                "region_ref": region_ref,
                "owner_ref": "table_1",
            }

        def footprint_radius(self, entity_id):
            assert entity_id == "apple_1"
            return 0.04

        def placement_operation_envelope(
            self,
            entity_id,
            **kwargs,
        ):
            assert entity_id == "apple_1"
            assert kwargs == {
                "relation": "on_support",
                "destination_id": "table_1",
                "region_ref": "table_1/support",
            }
            return SimpleNamespace(
                radius=0.075,
                model_ref="test-envelope/1.0",
                phases=frozenset({
                    "terminal_descent",
                    "open_at_release",
                    "vertical_retreat",
                }),
            )

    adapter = StaticAdapter()
    result = adapter.plan_layout(
        "table_1/support",
        ("apple_1",),
        reservation_group="repair-layout",
        excluded_local_xy=((0.0, 0.0), (0.12, 0.0)),
    )

    assert result.feasible
    assert snapshot.reservations == ()
    request = adapter.allocator.requests[0]
    assert request.items[0].radius == 0.04
    assert request.items[0].effective_operation_radius == 0.075
    assert request.items[0].placement_operation_envelope_phases == \
        frozenset({
            "terminal_descent",
            "open_at_release",
            "vertical_retreat",
        })
    exclusions = request.snapshot.reservations
    assert len(exclusions) == 2
    assert all(value.active for value in exclusions)
    assert {
        value.reservation_group for value in exclusions
    } == {"excluded:repair-layout"}
    assert {
        (value.x, value.y) for value in exclusions
    } == {(0.0, 0.0), (0.12, 0.0)}
    assert request.snapshot.evidence_refs == (
        "excluded_layout_target_0",
        "excluded_layout_target_1",
    )
    placement = result.plan.placements[0]
    for exclusion in exclusions:
        distance = (
            (placement.x - exclusion.x) ** 2
            + (placement.y - exclusion.y) ** 2
        ) ** 0.5
        assert distance + 1e-9 >= (
            max(
                placement.effective_operation_radius + exclusion.radius,
                placement.radius + exclusion.effective_operation_radius,
            )
            + snapshot.definition.clearance
        )


def test_complete_layout_filter_rejects_exact_external_collisions(
    monkeypatch,
) -> None:
    snapshot = RegionOccupancySnapshot(
        definition=_definition(),
        world_revision=12,
    )
    assessments = []

    def assess_placement(
        _runtime,
        object_id,
        destination_id,
        **kwargs,
    ):
        point = tuple(kwargs["candidate_point"])
        assessments.append((object_id, destination_id, kwargs))
        occupants = []
        footprint_fits = "true"
        if point[0] == -0.2:
            occupants = ["aisle_left", "robot_1"]
        elif point[0] == 0.0:
            occupants = ["robot_1"]
        elif point[0] == 0.2:
            footprint_fits = "false"
        return SimpleNamespace(
            to_dict=lambda: {
                "support_surface_valid": "true",
                "capacity_sufficient": "true",
                "footprint_fits": footprint_fits,
                "release_safe": "true",
                "retreat_safe": "true",
                "occupant_ids": occupants,
            }
        )

    monkeypatch.setattr(
        region_space_module,
        "import_harness_module",
        lambda name, **_: (
            SimpleNamespace(
                assess_placement=assess_placement,
                ROBOT_ENTITY_ID="robot_1",
            )
            if name == "er2sim.placement_simulator"
            else None
        ),
    )

    class RecordingAllocator:
        def __init__(self) -> None:
            self.request = None
            self.delegate = BatchSpaceAllocator()

        def allocate(self, request):
            self.request = request
            return self.delegate.allocate(request)

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                scene=object(),
                world=SimpleNamespace(revision=12),
                perception=SimpleNamespace(
                    entity_pose=lambda _entity_id: None,
                    local_xy_to_world=lambda _owner_id, local: local,
                    support_surface_z=lambda _owner_id: 0.6,
                    entity_bottom_offset=lambda _entity_id: 0.1,
                ),
            )
            self.harness_root = None
            self.allocator = RecordingAllocator()

        def inspect(self, region_ref, **_kwargs):
            assert region_ref == "table_1/support"
            return snapshot

        def region(self, region_ref):
            return {
                "region_ref": region_ref,
                "owner_ref": "table_1",
            }

        def footprint_radius(self, entity_id):
            assert entity_id == "box_1"
            return 0.10

        def placement_operation_envelope(self, entity_id, **_kwargs):
            assert entity_id == "box_1"
            return SimpleNamespace(
                radius=0.10,
                model_ref="test-envelope/1.0",
                phases=None,
            )

    adapter = StaticAdapter()
    decision = adapter.plan_layout(
        "table_1/support",
        ("box_1",),
        reservation_group="exact-placement-filter",
        relation="on_support",
        destination_id="table_1",
    )

    assert decision.feasible
    assert adapter.allocator.request is not None
    assert adapter.allocator.request.candidate_filter is None
    layout_filter = adapter.allocator.request.layout_filter
    assert layout_filter is not None
    assert len(assessments) == 1
    assessments.clear()
    assert layout_filter((
        LayoutPlacement("box_1", -0.2, 0.0, 0.10),
    )) is False
    assert layout_filter((
        LayoutPlacement("box_1", 0.0, 0.0, 0.10),
    )) is True
    assert layout_filter((
        LayoutPlacement("box_1", 0.2, 0.0, 0.10),
    )) is False
    assert all(
        call[2]["require_reachable"] is False
        and call[2]["simulation_mode"] == "conservative"
        and call[2]["candidate_point"][2] == 0.7
        for call in assessments
    )


def test_clear_of_workspace_floor_layout_uses_robust_clearance_default(
) -> None:
    def captured_request(
        *,
        owner_id,
        category,
        placement_policy,
        explicit_margin=None,
    ):
        definition = RegionDefinition(
            region_ref=f"{owner_id}/support",
            owner_id=owner_id,
            selector="support",
            geometry=RegionGeometry(
                RegionShape.RECTANGLE,
                width=1.0,
                depth=1.0,
            ),
            allowed_relations=("on_support",),
            edge_margin=0.01,
            clearance=0.01,
        )
        snapshot = RegionOccupancySnapshot(
            definition=definition,
            world_revision=12,
        )

        class RecordingAllocator:
            def __init__(self) -> None:
                self.request = None

            def allocate(self, request):
                self.request = request
                return BatchSpaceAllocator().allocate(request)

        class StaticAdapter(HarnessRegionSpaceAdapter):
            def __init__(self) -> None:
                self.runtime = SimpleNamespace(
                    world=SimpleNamespace(revision=12),
                    perception=SimpleNamespace(
                        catalog={
                            owner_id: {"category": category},
                        },
                        entity_pose=lambda _entity_id: None,
                    ),
                )
                self.harness_root = None
                self.allocator = RecordingAllocator()

            def inspect(self, region_ref, **_kwargs):
                assert region_ref == definition.region_ref
                return snapshot

            def region(self, region_ref):
                assert region_ref == definition.region_ref
                return {
                    "region_ref": region_ref,
                    "owner_ref": owner_id,
                }

            def footprint_radius(self, entity_id):
                assert entity_id == "apple_2"
                return 0.04

            def placement_operation_envelope(
                self,
                entity_id,
                **kwargs,
            ):
                assert entity_id == "apple_2"
                assert kwargs["destination_id"] == owner_id
                return SimpleNamespace(
                    radius=0.04,
                    model_ref="test-envelope/1.0",
                    phases=None,
                )

        adapter = StaticAdapter()
        decision = adapter.plan_layout(
            definition.region_ref,
            ("apple_2",),
            reservation_group=f"staging-{owner_id}",
            placement_policy=placement_policy,
            robust_clearance_margin_m=explicit_margin,
        )
        assert decision.feasible
        assert adapter.allocator.request is not None
        return adapter.allocator.request

    floor_request = captured_request(
        owner_id="floor_1",
        category="floor",
        placement_policy="clear_of_workspace",
    )
    table_request = captured_request(
        owner_id="table_1",
        category="table",
        placement_policy="clear_of_workspace",
    )
    explicit_request = captured_request(
        owner_id="floor_1",
        category="floor",
        placement_policy="clear_of_workspace",
        explicit_margin=0.015,
    )

    assert floor_request.robust_clearance_margin == 0.04
    assert table_request.robust_clearance_margin == 0.0
    assert explicit_request.robust_clearance_margin == 0.015


def test_select_layout_forwards_robust_clearance_controls() -> None:
    calls = []

    class RecordingAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            pass

        def scene_snapshot(self):
            return {}

        def candidate_refs(self, params, *, snapshot=None):
            del params, snapshot
            return ("floor_1/support",)

        def plan_layout(self, region_ref, object_ids, **kwargs):
            calls.append((region_ref, tuple(object_ids), kwargs))
            return _selection()

    decision = RecordingAdapter().select_layout(
        ("apple_2",),
        {
            "placement_policy": "clear_of_workspace",
            "robust_clearance_margin_m": 0.02,
        },
        reservation_group="floor-staging",
    )

    assert decision.feasible
    assert calls[0][0:2] == (
        "floor_1/support",
        ("apple_2",),
    )
    assert calls[0][2]["placement_policy"] == "clear_of_workspace"
    assert calls[0][2]["robust_clearance_margin_m"] == 0.02


def test_relocation_layout_filters_baseline_and_path_corridor() -> None:
    snapshot = RegionOccupancySnapshot(
        definition=RegionDefinition(
            region_ref="floor_1/support",
            owner_id="floor_1",
            selector="support",
            geometry=RegionGeometry(
                RegionShape.RECTANGLE,
                width=2.0,
                depth=2.0,
            ),
            allowed_relations=("on_support",),
            edge_margin=0.01,
            clearance=0.01,
        ),
        world_revision=12,
    )

    class StaticAdapter(HarnessRegionSpaceAdapter):
        def __init__(self) -> None:
            self.runtime = SimpleNamespace(
                world=SimpleNamespace(revision=12),
                perception=SimpleNamespace(
                    entity_pose=lambda _entity_id: (0.0, 0.0, 0.10),
                    world_xy_to_local=(
                        lambda _owner_id, world_xy: tuple(world_xy)
                    ),
                ),
            )
            self.harness_root = None
            self.allocator = BatchSpaceAllocator()

        def inspect(self, region_ref, **_kwargs):
            assert region_ref == "floor_1/support"
            return snapshot

        def region(self, region_ref):
            assert region_ref == "floor_1/support"
            return {
                "region_ref": region_ref,
                "owner_ref": "floor_1",
            }

        def footprint_radius(self, entity_id):
            assert entity_id == "box_1"
            return 0.10

        def placement_operation_envelope(
            self,
            entity_id,
            **kwargs,
        ):
            assert entity_id == "box_1"
            assert kwargs == {
                "relation": "on_support",
                "destination_id": "floor_1",
                "region_ref": "floor_1/support",
            }
            return SimpleNamespace(
                radius=0.10,
                model_ref="test-envelope/1.0",
                phases=None,
            )

    decision = StaticAdapter().plan_layout(
        "floor_1/support",
        ("box_1",),
        reservation_group="relocate-path-blocker",
        relation="on_support",
        destination_id="floor_1",
        placement_constraints={
            "baseline_pose": [0.0, 0.0, 0.0],
            "minimum_relocation_distance": 0.39,
            "path_segments": [
                [[-1.0, 0.0], [1.0, 0.0]],
            ],
            "object_radius": 0.10,
            "required_clearance": 0.30,
            "minimum_improvement": 0.05,
        },
    )

    assert decision.feasible
    assert decision.plan is not None
    placement = decision.plan.placements[0]
    assert hypot(placement.x, placement.y) + 1e-9 >= 0.39
    assert abs(placement.y) + 1e-9 >= 0.40


def test_box_placement_model_assigns_two_red_objects_to_safe_slots(
        tmp_path) -> None:
    scene_module = import_harness_module("er2sim.scene")
    perception_module = import_harness_module("er2sim.perception")

    scene = scene_module.SimScene(headless=True)
    try:
        perception = perception_module.OraclePerceptionService(
            scene,
            media_dir=str(tmp_path / "media"),
        )
        runtime = SimpleNamespace(
            scene=scene,
            perception=perception,
            world=SimpleNamespace(revision=6, tasks={}),
            _current_task_id=None,
        )
        adapter = HarnessRegionSpaceAdapter(runtime=runtime)
        scene_snapshot = adapter.scene_snapshot()
        decision = adapter.plan_layout(
            "box_1/interior",
            ("apple_1", "apple_2"),
            reservation_group="two-red-apples",
            scene_snapshot=scene_snapshot,
        )

        assert decision.feasible
        assert decision.snapshot is not None
        definition = decision.snapshot.definition
        assert definition.geometry.shape is RegionShape.RECTANGLE
        assert definition.geometry.width == 0.21
        assert definition.geometry.depth == 0.21
        assert definition.placement_slots_strict is True
        assert definition.exclusions[0].source_entity_ref == \
            "box_1_handle"
        assert definition.exclusions[0].blocked_phases == \
            frozenset({"release_drop"})
        assert [
            (placement.entity_id, placement.x, placement.y)
            for placement in decision.plan.placements
        ] == [
            ("apple_1", 0.055, 0.047),
            ("apple_2", -0.055, -0.047),
        ]
        first, second = decision.plan.placements
        assert hypot(first.x - second.x, first.y - second.y) >= \
            max(
                first.effective_operation_radius + second.radius,
                first.radius + second.effective_operation_radius,
            ) + definition.clearance - 1e-9
    finally:
        scene.close()


def test_recursive_recovery_showcase_reserves_table_space_for_blocker(
        tmp_path) -> None:
    scene_module = import_harness_module("er2sim.scene")
    registry_module = import_harness_module("er2sim.scene_registry")
    perception_module = import_harness_module("er2sim.perception")
    placement_module = import_harness_module("er2sim.placement_simulator")
    targets_module = import_harness_module("er2sim.interaction_targets")

    definition = registry_module.load_default_scene_registry().get(
        "recursive_recovery_showcase"
    )
    scene = scene_module.SimScene(
        headless=True,
        scene_definition=definition,
        scene_seed=0,
    )
    try:
        perception = perception_module.OraclePerceptionService(
            scene,
            media_dir=str(tmp_path / "media"),
        )
        runtime = SimpleNamespace(
            scene=scene,
            perception=perception,
            world=SimpleNamespace(revision=7, tasks={}),
            _current_task_id=None,
        )
        adapter = HarnessRegionSpaceAdapter(runtime=runtime)

        decision = adapter.plan_layout(
            "table_1/support",
            ("box_1",),
            reservation_group="recursive-showcase-box-parking",
            relation="on_support",
            destination_id="table_1",
        )

        assert decision.feasible
        assert decision.plan is not None
        artifact = layout_targets_artifact(
            target_ref="layout/showcase/box-parking",
            selection=decision,
            subject_ids=("box_1",),
        )
        point = targets_module.resolve_layout_target_point_from_artifact(
            perception,
            artifact,
            "box_1",
            "table_1",
        )
        assert point is not None

        assessment = placement_module.assess_placement(
            runtime,
            "box_1",
            "table_1",
            relation="on_support",
            require_reachable=False,
            candidate_point=point,
        )
        assert assessment.ok
        assert assessment.occupant_ids == []
    finally:
        scene.close()


def test_relocation_layout_defers_continuation_until_release_stance(
        tmp_path) -> None:
    scene_module = import_harness_module("er2sim.scene")
    registry_module = import_harness_module("er2sim.scene_registry")
    perception_module = import_harness_module("er2sim.perception")

    definition = registry_module.load_default_scene_registry().get(
        "recursive_recovery_showcase"
    )
    scene = scene_module.SimScene(
        headless=True,
        scene_definition=definition,
        scene_seed=0,
    )
    try:
        anchor = [
            1.2385795440144707,
            1.0601051183038135,
            -1.568095909008518,
        ]
        goals = [
            [
                1.4823533361752725,
                0.22259445290804591,
                -2.6179938779914944,
            ],
            [
                1.4823533361752725,
                -0.35740554709195405,
                2.6179938779914944,
            ],
            [
                0.5543119629421824,
                -0.4931521861300698,
                0.7853981633974483,
            ],
            [
                0.6900586019802983,
                -0.5697002812869284,
                1.0471975511965976,
            ],
        ]
        scene.set_base_hold(*anchor)
        scene.step()
        perception = perception_module.OraclePerceptionService(
            scene,
            media_dir=str(tmp_path / "media"),
        )
        runtime = SimpleNamespace(
            scene=scene,
            perception=perception,
            world=SimpleNamespace(revision=61, tasks={}),
            _current_task_id=None,
        )
        adapter = HarnessRegionSpaceAdapter(runtime=runtime)

        decision = adapter.plan_layout(
            "floor_1/support",
            ("apple_2",),
            reservation_group="real-failure-continuation-regression",
            relation="on_support",
            destination_id="floor_1",
            placement_constraints={
                "baseline_pose": [
                    1.233804812811751,
                    0.4498653352429099,
                    0.03963281816108476,
                ],
                "minimum_relocation_distance": 0.2,
                "path_segments": [
                    [anchor[:2], goal[:2]] for goal in goals
                ],
                "object_radius": 0.04,
                "required_clearance": 0.3,
                "minimum_improvement": 0.05,
                "continuation_anchor_pose": anchor,
                "continuation_goal_poses": goals,
            },
        )

        assert decision.feasible
        assert decision.plan is not None
        assert decision.failure is None
    finally:
        scene.close()


def test_rotated_held_box_floor_layout_avoids_fixed_wall(tmp_path) -> None:
    scene_module = import_harness_module("er2sim.scene")
    registry_module = import_harness_module("er2sim.scene_registry")
    perception_module = import_harness_module("er2sim.perception")
    placement_module = import_harness_module("er2sim.placement_simulator")
    targets_module = import_harness_module("er2sim.interaction_targets")

    definition = registry_module.load_default_scene_registry().get(
        "recursive_recovery_showcase"
    )
    scene = scene_module.SimScene(
        headless=True,
        scene_definition=definition,
        scene_seed=0,
    )
    try:
        perception = perception_module.OraclePerceptionService(
            scene,
            media_dir=str(tmp_path / "media"),
        )
        original_entity_yaw = perception.entity_yaw
        held_yaw = -2.705245480627233
        perception.entity_yaw = lambda entity_id: (
            held_yaw
            if entity_id == "box_1"
            else original_entity_yaw(entity_id)
        )
        runtime = SimpleNamespace(
            scene=scene,
            perception=perception,
            world=SimpleNamespace(revision=99, tasks={}),
            _current_task_id=None,
        )
        adapter = HarnessRegionSpaceAdapter(runtime=runtime)
        layout_filter = adapter._physical_placement_layout_filter(
            destination_id="floor_1",
            relation="on_support",
            batch_entity_ids=frozenset({"box_1"}),
        )
        assert layout_filter is not None
        assert layout_filter((
            LayoutPlacement(
                "box_1",
                -0.28,
                -1.44,
                adapter.footprint_radius("box_1"),
            ),
        )) is False

        decision = adapter.plan_layout(
            "floor_1/support",
            ("box_1",),
            reservation_group="held-box-floor-staging",
            relation="on_support",
            destination_id="floor_1",
            excluded_local_xy=((0.6, -1.12),),
        )

        assert decision.feasible
        artifact = layout_targets_artifact(
            target_ref="layout/floor/held-box",
            selection=decision,
            subject_ids=("box_1",),
        )
        point = targets_module.resolve_layout_target_point_from_artifact(
            perception,
            artifact,
            "box_1",
            "floor_1",
        )
        assert point is not None
        assessment = placement_module.assess_placement(
            runtime,
            "box_1",
            "floor_1",
            relation="on_support",
            require_reachable=False,
            candidate_point=point,
        )
        assert set(assessment.occupant_ids) <= {"robot_1"}
        assert "aisle_left" not in assessment.occupant_ids
        assert "aisle_right" not in assessment.occupant_ids
    finally:
        scene.close()


def test_narrow_aisle_floor_layout_avoids_fixed_walls(tmp_path) -> None:
    scene_module = import_harness_module("er2sim.scene")
    registry_module = import_harness_module("er2sim.scene_registry")
    perception_module = import_harness_module("er2sim.perception")
    placement_module = import_harness_module("er2sim.placement_simulator")
    targets_module = import_harness_module("er2sim.interaction_targets")

    definition = registry_module.load_default_scene_registry().get(
        "narrow_aisle"
    )
    scene = scene_module.SimScene(
        headless=True,
        scene_definition=definition,
        scene_seed=0,
    )
    try:
        perception = perception_module.OraclePerceptionService(
            scene,
            media_dir=str(tmp_path / "media"),
        )
        runtime = SimpleNamespace(
            scene=scene,
            perception=perception,
            world=SimpleNamespace(revision=5, tasks={}),
            _current_task_id=None,
        )
        adapter = HarnessRegionSpaceAdapter(runtime=runtime)

        decision = adapter.plan_layout(
            "floor_1/support",
            ("plate_1",),
            reservation_group="place-plate-on-floor",
        )

        assert decision.feasible
        artifact = layout_targets_artifact(
            target_ref="layout/floor/plate",
            selection=decision,
            subject_ids=("plate_1",),
        )
        point = targets_module.resolve_layout_target_point_from_artifact(
            perception,
            artifact,
            "plate_1",
            "floor_1",
        )
        assert point is not None

        assessment = placement_module.assess_placement(
            runtime,
            "plate_1",
            "floor_1",
            relation="on_support",
            require_reachable=False,
            candidate_point=point,
        )
        assert assessment.ok
        assert "aisle_left" not in assessment.occupant_ids
        assert "aisle_right" not in assessment.occupant_ids
    finally:
        scene.close()
