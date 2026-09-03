from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
from math import ceil, floor, hypot, isfinite, pi
from types import MappingProxyType
from typing import Any, Callable, Mapping


def _normalized_phases(
    value: Any,
    *,
    field_name: str,
) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(
        value,
        (list, tuple, set, frozenset),
    ):
        raise ValueError(f"{field_name} must be a phase sequence")
    normalized = [
        str(item).strip().casefold()
        for item in value
    ]
    if any(not phase for phase in normalized):
        raise ValueError(f"{field_name} must not contain empty phases")
    return frozenset(normalized)


class RegionShape(str, Enum):
    RECTANGLE = "rectangle"
    CIRCLE = "circle"


class RegionOccupancyState(str, Enum):
    EMPTY = "empty"
    PARTIALLY_OCCUPIED = "partially_occupied"
    SATURATED = "saturated"
    UNKNOWN = "unknown"


class ReservationStatus(str, Enum):
    ACTIVE = "active"
    FULFILLED = "fulfilled"
    RELEASED = "released"
    INVALIDATED = "invalidated"


class RegionRequirementKind(str, Enum):
    EMPTY = "empty"
    CLEAR_FOR = "clear_for"
    AVAILABLE_FOR = "available_for"
    TRANSPORT_READY = "transport_ready"


class RequirementPhase(str, Enum):
    AT_BINDING = "at_binding"
    BEFORE_PICK = "before_pick"
    BEFORE_BATCH = "before_batch"
    DURING_TRANSPORT = "during_transport"
    AT_FINAL = "at_final"


class LayoutVerdict(str, Enum):
    FEASIBLE = "feasible"
    INFEASIBLE_PROVEN = "infeasible_proven"
    SEARCH_EXHAUSTED = "search_exhausted"
    PERCEPTION_INSUFFICIENT = "perception_insufficient"


@dataclass(frozen=True)
class RegionGeometry:
    shape: RegionShape
    width: float | None = None
    depth: float | None = None
    radius: float | None = None

    def __post_init__(self) -> None:
        if self.shape is RegionShape.RECTANGLE:
            if self.width is None or self.depth is None:
                raise ValueError("rectangle regions require width and depth")
            if (
                not isfinite(self.width)
                or not isfinite(self.depth)
                or self.width <= 0.0
                or self.depth <= 0.0
            ):
                raise ValueError("rectangle dimensions must be positive")
        elif self.shape is RegionShape.CIRCLE:
            if (
                self.radius is None
                or not isfinite(self.radius)
                or self.radius <= 0.0
            ):
                raise ValueError("circle regions require a positive radius")

    def contains_disc(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        margin: float = 0.0,
    ) -> bool:
        required = max(0.0, float(radius)) + max(0.0, float(margin))
        if self.shape is RegionShape.RECTANGLE:
            assert self.width is not None and self.depth is not None
            return (
                abs(x) + required <= self.width / 2.0 + 1e-9
                and abs(y) + required <= self.depth / 2.0 + 1e-9
            )
        assert self.radius is not None
        return hypot(x, y) + required <= self.radius + 1e-9

    def usable_area(self, margin: float = 0.0) -> float:
        margin = max(0.0, float(margin))
        if self.shape is RegionShape.RECTANGLE:
            assert self.width is not None and self.depth is not None
            return max(0.0, self.width - 2.0 * margin) * max(
                0.0, self.depth - 2.0 * margin
            )
        assert self.radius is not None
        radius = max(0.0, self.radius - margin)
        return pi * radius * radius

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape.value,
            "width_m": self.width,
            "depth_m": self.depth,
            "radius_m": self.radius,
        }


@dataclass(frozen=True)
class RegionExclusion:
    exclusion_ref: str
    geometry: RegionGeometry
    x: float = 0.0
    y: float = 0.0
    clearance: float = 0.0
    source_entity_ref: str | None = None
    blocked_phases: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.exclusion_ref:
            raise ValueError("exclusion_ref is required")
        if not all(isfinite(value) for value in (self.x, self.y)):
            raise ValueError("exclusion coordinates must be finite")
        if not isfinite(self.clearance) or self.clearance < 0.0:
            raise ValueError("exclusion clearance must not be negative")
        object.__setattr__(
            self,
            "blocked_phases",
            _normalized_phases(
                self.blocked_phases,
                field_name="exclusion blocked_phases",
            ),
        )

    def blocks_operation_phases(
        self,
        operation_phases: frozenset[str] | None,
    ) -> bool:
        if self.blocked_phases is None:
            return True
        if not self.blocked_phases:
            return False
        if operation_phases is None:
            return True
        return bool(self.blocked_phases.intersection(operation_phases))

    def intersects_disc(
        self,
        x: float,
        y: float,
        radius: float,
    ) -> bool:
        required = max(0.0, float(radius)) + self.clearance
        dx = abs(float(x) - self.x)
        dy = abs(float(y) - self.y)
        if self.geometry.shape is RegionShape.CIRCLE:
            assert self.geometry.radius is not None
            return hypot(dx, dy) <= (
                self.geometry.radius + required + 1e-9
            )

        assert (
            self.geometry.width is not None
            and self.geometry.depth is not None
        )
        nearest_dx = max(0.0, dx - self.geometry.width / 2.0)
        nearest_dy = max(0.0, dy - self.geometry.depth / 2.0)
        return hypot(nearest_dx, nearest_dy) <= required + 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "exclusion_ref": self.exclusion_ref,
            "local_xy": [self.x, self.y],
            "geometry": self.geometry.to_dict(),
            "clearance_m": self.clearance,
            "source_entity_ref": self.source_entity_ref,
            "blocked_phases": (
                sorted(self.blocked_phases)
                if self.blocked_phases is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RegionDefinition:
    region_ref: str
    owner_id: str
    selector: str
    geometry: RegionGeometry
    allowed_relations: tuple[str, ...] = ()
    edge_margin: float = 0.018
    clearance: float = 0.018
    exclusions: tuple[RegionExclusion, ...] = ()
    placement_slots: tuple[tuple[float, float], ...] = ()
    placement_slots_strict: bool = False

    def __post_init__(self) -> None:
        if not self.region_ref or not self.owner_id:
            raise ValueError("region_ref and owner_id are required")
        if (
            not isfinite(self.edge_margin)
            or not isfinite(self.clearance)
            or self.edge_margin < 0.0
            or self.clearance < 0.0
        ):
            raise ValueError("region margins must not be negative")
        normalized_slots: list[tuple[float, float]] = []
        for slot in self.placement_slots:
            if not isinstance(slot, (list, tuple)) or len(slot) != 2:
                raise ValueError("placement slots require local x/y pairs")
            point = (float(slot[0]), float(slot[1]))
            if not all(isfinite(value) for value in point):
                raise ValueError("placement slot coordinates must be finite")
            if point not in normalized_slots:
                normalized_slots.append(point)
        object.__setattr__(self, "placement_slots", tuple(normalized_slots))
        if self.placement_slots_strict and not normalized_slots:
            raise ValueError("strict placement slots require at least one slot")
        exclusion_refs = [
            exclusion.exclusion_ref for exclusion in self.exclusions
        ]
        if len(set(exclusion_refs)) != len(exclusion_refs):
            raise ValueError("region exclusion refs must be unique")

    def placement_issue(
        self,
        x: float,
        y: float,
        radius: float,
        *,
        operation_radius: float | None = None,
        operation_phases: frozenset[str] | None = None,
    ) -> str | None:
        physical_radius = max(0.0, float(radius))
        normalized_operation_phases = _normalized_phases(
            operation_phases,
            field_name="placement operation phases",
        )
        if not self.geometry.contains_disc(
            x,
            y,
            physical_radius,
            margin=self.edge_margin,
        ):
            return "outside_region"
        if self.placement_slots_strict and not any(
            hypot(float(x) - slot_x, float(y) - slot_y) <= 1e-6
            for slot_x, slot_y in self.placement_slots
        ):
            return "not_declared_slot"
        for exclusion in self.exclusions:
            exclusion_radius = physical_radius
            if (
                operation_radius is not None
                and exclusion.blocks_operation_phases(
                    normalized_operation_phases
                )
            ):
                exclusion_radius = max(
                    physical_radius,
                    float(operation_radius),
                )
            if exclusion.intersects_disc(x, y, exclusion_radius):
                return f"excluded:{exclusion.exclusion_ref}"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_ref": self.region_ref,
            "owner_id": self.owner_id,
            "selector": self.selector,
            "geometry": self.geometry.to_dict(),
            "allowed_relations": list(self.allowed_relations),
            "edge_margin_m": self.edge_margin,
            "clearance_m": self.clearance,
            "exclusions": [
                exclusion.to_dict()
                for exclusion in sorted(
                    self.exclusions,
                    key=lambda value: value.exclusion_ref,
                )
            ],
            "placement_slots": [
                [x, y] for x, y in self.placement_slots
            ],
            "placement_slots_strict": self.placement_slots_strict,
        }


@dataclass(frozen=True)
class RegionOccupant:
    entity_id: str
    radius: float
    x: float
    y: float
    movable: bool = True
    support_id: str | None = None

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("occupant entity_id is required")
        if self.radius < 0.0:
            raise ValueError("occupant radius must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "radius_m": self.radius,
            "local_xy": [self.x, self.y],
            "movable": self.movable,
            "support_id": self.support_id,
        }


@dataclass(frozen=True)
class SpaceReservation:
    reservation_id: str
    entity_id: str
    x: float
    y: float
    radius: float
    world_revision: int
    reservation_group: str | None = None
    artifact_ref: str | None = None
    status: ReservationStatus = ReservationStatus.ACTIVE
    placement_operation_envelope_radius: float | None = None
    placement_operation_envelope_model_ref: str | None = None
    placement_operation_envelope_phases: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if self.radius <= 0.0:
            raise ValueError("reservation radius must be positive")
        if (
            self.placement_operation_envelope_radius is not None
            and (
                not isfinite(self.placement_operation_envelope_radius)
                or self.placement_operation_envelope_radius <= 0.0
            )
        ):
            raise ValueError(
                "reservation placement envelope radius must be positive"
            )
        object.__setattr__(
            self,
            "placement_operation_envelope_phases",
            _normalized_phases(
                self.placement_operation_envelope_phases,
                field_name="reservation placement operation phases",
            ),
        )

    @property
    def active(self) -> bool:
        return self.status is ReservationStatus.ACTIVE

    @property
    def effective_operation_radius(self) -> float:
        return max(
            self.radius,
            self.placement_operation_envelope_radius or self.radius,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "entity_id": self.entity_id,
            "local_xy": [self.x, self.y],
            "radius_m": self.radius,
            "placement_operation_envelope_radius_m": (
                self.effective_operation_radius
            ),
            "placement_operation_envelope_model_ref": (
                self.placement_operation_envelope_model_ref
            ),
            "placement_operation_envelope_phases": (
                sorted(self.placement_operation_envelope_phases)
                if self.placement_operation_envelope_phases is not None
                else None
            ),
            "world_revision": self.world_revision,
            "reservation_group": self.reservation_group,
            "artifact_ref": self.artifact_ref,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class RegionOccupancySnapshot:
    definition: RegionDefinition
    world_revision: int
    occupants: tuple[RegionOccupant, ...] = ()
    reservations: tuple[SpaceReservation, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    complete: bool = True

    @property
    def fingerprint(self) -> str:
        payload = {
            "definition": self.definition.to_dict(),
            "world_revision": self.world_revision,
            "occupants": [
                item.to_dict()
                for item in sorted(
                    self.occupants, key=lambda value: value.entity_id
                )
            ],
            "reservations": [
                item.to_dict()
                for item in sorted(
                    self.reservations,
                    key=lambda value: value.reservation_id,
                )
            ],
            "complete": self.complete,
        }
        digest = sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        return f"region-state-{digest}"

    @property
    def physical_state(self) -> RegionOccupancyState:
        if not self.complete:
            return RegionOccupancyState.UNKNOWN
        return self._state_for(self.occupants)

    @property
    def allocation_state(self) -> RegionOccupancyState:
        if not self.complete:
            return RegionOccupancyState.UNKNOWN
        active_reservations = tuple(
            reservation
            for reservation in self.reservations
            if reservation.active
        )
        return self._state_for((*self.occupants, *active_reservations))

    @property
    def state(self) -> RegionOccupancyState:
        """Backward-compatible physical occupancy state."""

        return self.physical_state

    def _state_for(
        self,
        items: tuple[RegionOccupant | SpaceReservation, ...],
    ) -> RegionOccupancyState:
        if not items:
            return RegionOccupancyState.EMPTY
        occupied_area = sum(
            pi * item.radius * item.radius for item in items
        )
        usable_area = self.definition.geometry.usable_area(
            self.definition.edge_margin
        )
        if usable_area <= 0.0 or occupied_area >= usable_area * 0.9:
            return RegionOccupancyState.SATURATED
        return RegionOccupancyState.PARTIALLY_OCCUPIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "region_occupancy_snapshot",
            "definition": self.definition.to_dict(),
            "world_revision": self.world_revision,
            "state": self.state.value,
            "physical_state": self.physical_state.value,
            "allocation_state": self.allocation_state.value,
            "state_fingerprint": self.fingerprint,
            "occupants": [item.to_dict() for item in self.occupants],
            "reservations": [
                item.to_dict() for item in self.reservations
            ],
            "evidence_refs": list(self.evidence_refs),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class RegionRequirement:
    kind: RegionRequirementKind
    phase: RequirementPhase
    region_ref: str | None = None
    owner_id: str | None = None
    object_ids: tuple[str, ...] = ()
    preserve_entity_ids: tuple[str, ...] = ()
    repair_policy: str = "recursive_repair"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "phase": self.phase.value,
            "region_ref": self.region_ref,
            "owner_id": self.owner_id,
            "object_ids": list(self.object_ids),
            "preserve_entity_ids": list(self.preserve_entity_ids),
            "repair_policy": self.repair_policy,
        }


@dataclass(frozen=True)
class LayoutItem:
    entity_id: str
    radius: float
    current_x: float | None = None
    current_y: float | None = None
    placement_operation_envelope_radius: float | None = None
    placement_operation_envelope_model_ref: str | None = None
    placement_operation_envelope_phases: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.entity_id:
            raise ValueError("layout item entity_id is required")
        if not isfinite(self.radius) or self.radius <= 0.0:
            raise ValueError("layout item radius must be positive")
        if (
            self.placement_operation_envelope_radius is not None
            and (
                not isfinite(self.placement_operation_envelope_radius)
                or self.placement_operation_envelope_radius <= 0.0
            )
        ):
            raise ValueError(
                "layout item placement envelope radius must be positive"
            )
        object.__setattr__(
            self,
            "placement_operation_envelope_phases",
            _normalized_phases(
                self.placement_operation_envelope_phases,
                field_name="layout item placement operation phases",
            ),
        )

    @property
    def effective_operation_radius(self) -> float:
        return max(
            self.radius,
            self.placement_operation_envelope_radius or self.radius,
        )


@dataclass(frozen=True)
class LayoutRequest:
    snapshot: RegionOccupancySnapshot
    items: tuple[LayoutItem, ...]
    reservation_group: str
    preserve_entity_ids: frozenset[str] = frozenset()
    max_search_states: int = 25000
    candidate_filter: Callable[[LayoutItem, float, float], bool] | None = None
    layout_filter: (
        Callable[[tuple[LayoutPlacement, ...]], bool] | None
    ) = None
    robust_clearance_margin: float = 0.0

    def __post_init__(self) -> None:
        if not self.reservation_group:
            raise ValueError("reservation_group is required")
        try:
            robust_clearance_margin = float(
                self.robust_clearance_margin
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "robust_clearance_margin must be finite and non-negative"
            ) from exc
        if (
            not isfinite(robust_clearance_margin)
            or robust_clearance_margin < 0.0
        ):
            raise ValueError(
                "robust_clearance_margin must be finite and non-negative"
            )
        object.__setattr__(
            self,
            "robust_clearance_margin",
            robust_clearance_margin,
        )
        if self.max_search_states < 1:
            raise ValueError("max_search_states must be positive")
        if (
            self.candidate_filter is not None
            and not callable(self.candidate_filter)
        ):
            raise ValueError("candidate_filter must be callable")
        if self.layout_filter is not None and not callable(
            self.layout_filter
        ):
            raise ValueError("layout_filter must be callable")


@dataclass(frozen=True)
class LayoutPlacement:
    entity_id: str
    x: float
    y: float
    radius: float
    placement_operation_envelope_radius: float | None = None
    placement_operation_envelope_model_ref: str | None = None
    placement_operation_envelope_phases: frozenset[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "placement_operation_envelope_phases",
            _normalized_phases(
                self.placement_operation_envelope_phases,
                field_name="layout placement operation phases",
            ),
        )

    @property
    def effective_operation_radius(self) -> float:
        return max(
            self.radius,
            self.placement_operation_envelope_radius or self.radius,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "local_xy": [self.x, self.y],
            "radius_m": self.radius,
            "placement_operation_envelope_radius_m": (
                self.effective_operation_radius
            ),
            "placement_operation_envelope_model_ref": (
                self.placement_operation_envelope_model_ref
            ),
            "placement_operation_envelope_phases": (
                sorted(self.placement_operation_envelope_phases)
                if self.placement_operation_envelope_phases is not None
                else None
            ),
        }


@dataclass(frozen=True)
class LayoutPlanArtifact:
    plan_id: str
    region_ref: str
    world_revision: int
    state_fingerprint: str
    reservation_group: str
    placements: tuple[LayoutPlacement, ...]
    search_states: int
    robust_clearance_margin: float = 0.0

    @property
    def reservations(self) -> tuple[SpaceReservation, ...]:
        return tuple(
            SpaceReservation(
                reservation_id=(
                    f"{self.reservation_group}:{placement.entity_id}"
                ),
                entity_id=placement.entity_id,
                x=placement.x,
                y=placement.y,
                radius=placement.radius,
                world_revision=self.world_revision,
                reservation_group=self.reservation_group,
                placement_operation_envelope_radius=(
                    placement.effective_operation_radius
                ),
                placement_operation_envelope_model_ref=(
                    placement.placement_operation_envelope_model_ref
                ),
                placement_operation_envelope_phases=(
                    placement.placement_operation_envelope_phases
                ),
            )
            for placement in self.placements
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "region_layout_plan",
            "plan_id": self.plan_id,
            "region_ref": self.region_ref,
            "world_revision": self.world_revision,
            "state_fingerprint": self.state_fingerprint,
            "reservation_group": self.reservation_group,
            "robust_clearance_margin_m": self.robust_clearance_margin,
            "placements": [
                placement.to_dict() for placement in self.placements
            ],
            "reservations": [
                reservation.to_dict()
                for reservation in self.reservations
            ],
            "search_states": self.search_states,
        }


@dataclass(frozen=True)
class LayoutFailure:
    verdict: LayoutVerdict
    code: str
    message: str
    proven: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "details", MappingProxyType(dict(self.details))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "code": self.code,
            "message": self.message,
            "proven": self.proven,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class LayoutResult:
    plan: LayoutPlanArtifact | None = None
    failure: LayoutFailure | None = None

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.failure is None):
            raise ValueError("layout result requires exactly one outcome")

    @property
    def feasible(self) -> bool:
        return self.plan is not None


class RegionReasoner:
    def requirement_satisfied(
        self,
        snapshot: RegionOccupancySnapshot,
        requirement: RegionRequirement,
    ) -> bool | None:
        if not snapshot.complete:
            return None
        if requirement.kind is RegionRequirementKind.EMPTY:
            return not snapshot.occupants
        if requirement.kind is RegionRequirementKind.CLEAR_FOR:
            allowed = set(requirement.object_ids)
            allowed.update(requirement.preserve_entity_ids)
            return all(
                occupant.entity_id in allowed
                for occupant in snapshot.occupants
            )
        return None


class BatchSpaceAllocator:
    """Deterministic conservative disc layout over a support region."""

    def allocate(self, request: LayoutRequest) -> LayoutResult:
        snapshot = request.snapshot
        if not snapshot.complete:
            return LayoutResult(
                failure=LayoutFailure(
                    verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                    code="PERCEPTION_INSUFFICIENT",
                    message="Region occupancy is incomplete",
                    details={
                        "region_ref": snapshot.definition.region_ref,
                        "state_fingerprint": snapshot.fingerprint,
                    },
                )
            )

        items = tuple(
            sorted(
                request.items,
                key=lambda item: (
                    -item.effective_operation_radius,
                    -item.radius,
                    item.entity_id,
                ),
            )
        )
        if not items:
            return LayoutResult(
                plan=self._plan(request, (), search_states=0)
            )

        definition = snapshot.definition
        geometry = definition.geometry
        effective_clearance = (
            definition.clearance
            + request.robust_clearance_margin
        )
        for item in items:
            if not geometry.contains_disc(
                0.0,
                0.0,
                item.radius,
                margin=definition.edge_margin,
            ):
                return LayoutResult(
                    failure=LayoutFailure(
                        verdict=LayoutVerdict.INFEASIBLE_PROVEN,
                        code="NO_FEASIBLE_LAYOUT",
                        message=(
                            f"{item.entity_id} does not fit inside "
                            f"{definition.region_ref}"
                        ),
                        proven=True,
                        details={
                            "region_ref": definition.region_ref,
                            "entity_id": item.entity_id,
                            "radius_m": item.radius,
                            "reason": "individual_footprint_too_large",
                        },
                    )
                )

        incoming_ids = {item.entity_id for item in items}
        fixed = [
            occupant
            for occupant in snapshot.occupants
            if occupant.entity_id not in incoming_ids
        ]
        reserved = [
            item
            for item in snapshot.reservations
            if item.active and item.entity_id not in incoming_ids
        ]
        obstacles = tuple(fixed + reserved)
        occupied_area = sum(pi * item.radius * item.radius for item in obstacles)
        incoming_area = sum(pi * item.radius * item.radius for item in items)
        usable_area = geometry.usable_area(definition.edge_margin)
        if occupied_area + incoming_area > usable_area + 1e-9:
            return LayoutResult(
                failure=LayoutFailure(
                    verdict=LayoutVerdict.INFEASIBLE_PROVEN,
                    code="NO_FEASIBLE_LAYOUT",
                    message=(
                        f"{definition.region_ref} has insufficient usable area"
                    ),
                    proven=True,
                    details={
                        "region_ref": definition.region_ref,
                        "usable_area_m2": usable_area,
                        "required_area_m2": occupied_area + incoming_area,
                        "reason": "area_lower_bound",
                    },
                )
            )

        candidate_pitches = self._candidate_pitches(
            items,
            effective_clearance,
        )
        candidates: dict[str, tuple[tuple[float, float], ...]] = {}
        for item in items:
            values = self._candidate_points(
                definition,
                item,
                pitches=candidate_pitches,
            )
            if request.candidate_filter is not None:
                values = tuple(
                    (x, y)
                    for x, y in values
                    if request.candidate_filter(item, x, y)
                )
            candidates[item.entity_id] = values
        if any(not values for values in candidates.values()):
            strict = definition.placement_slots_strict
            return LayoutResult(
                failure=LayoutFailure(
                    verdict=(
                        LayoutVerdict.INFEASIBLE_PROVEN
                        if strict
                        else LayoutVerdict.SEARCH_EXHAUSTED
                    ),
                    code=(
                        "NO_FEASIBLE_LAYOUT"
                        if strict
                        else "CANDIDATE_SPACE_EXHAUSTED"
                    ),
                    message=(
                        f"{definition.region_ref} has no valid center "
                        "for at least one item"
                    ),
                    proven=strict,
                    details={
                        "region_ref": definition.region_ref,
                        "reason": (
                            "strict_slot_has_no_individual_candidate"
                            if strict
                            else "candidate_space_has_no_individual_candidate"
                        ),
                    },
                )
            )

        placements: list[LayoutPlacement] = []
        search_states = 0
        budget_exhausted = False

        def place(index: int) -> bool:
            nonlocal budget_exhausted, search_states
            if index >= len(items):
                return (
                    request.layout_filter is None
                    or request.layout_filter(tuple(placements))
                )
            item = items[index]
            for x, y in candidates[item.entity_id]:
                search_states += 1
                if search_states > request.max_search_states:
                    budget_exhausted = True
                    return False
                if not self._clear(
                    x,
                    y,
                    item,
                    obstacles,
                    placements,
                    effective_clearance,
                ):
                    continue
                placements.append(
                    LayoutPlacement(
                        entity_id=item.entity_id,
                        x=x,
                        y=y,
                        radius=item.radius,
                        placement_operation_envelope_radius=(
                            item.effective_operation_radius
                        ),
                        placement_operation_envelope_model_ref=(
                            item.placement_operation_envelope_model_ref
                        ),
                        placement_operation_envelope_phases=(
                            item.placement_operation_envelope_phases
                        ),
                    )
                )
                if place(index + 1):
                    return True
                placements.pop()
                if budget_exhausted:
                    return False
            return False

        if place(0):
            ordered = tuple(
                sorted(placements, key=lambda item: item.entity_id)
            )
            return LayoutResult(
                plan=self._plan(
                    request,
                    ordered,
                    search_states=search_states,
                )
            )

        if budget_exhausted:
            return LayoutResult(
                failure=LayoutFailure(
                    verdict=LayoutVerdict.SEARCH_EXHAUSTED,
                    code="SEARCH_BUDGET_EXHAUSTED",
                    message=(
                        f"No layout was found for {definition.region_ref} "
                        "within the deterministic search budget"
                    ),
                    details={
                        "region_ref": definition.region_ref,
                        "search_states": search_states,
                        "max_search_states": request.max_search_states,
                        "state_fingerprint": snapshot.fingerprint,
                    },
                )
            )
        if definition.placement_slots_strict:
            return LayoutResult(
                failure=LayoutFailure(
                    verdict=LayoutVerdict.INFEASIBLE_PROVEN,
                    code="NO_FEASIBLE_LAYOUT",
                    message=(
                        f"No strict slot assignment exists for "
                        f"{definition.region_ref}"
                    ),
                    proven=True,
                    details={
                        "region_ref": definition.region_ref,
                        "search_states": search_states,
                        "state_fingerprint": snapshot.fingerprint,
                        "reason": "strict_slot_assignment_exhausted",
                    },
                )
            )
        return LayoutResult(
            failure=LayoutFailure(
                verdict=LayoutVerdict.SEARCH_EXHAUSTED,
                code="CANDIDATE_SPACE_EXHAUSTED",
                message=(
                    f"No layout was found for {definition.region_ref} "
                    "within the deterministic candidate model"
                ),
                details={
                    "region_ref": definition.region_ref,
                    "search_states": search_states,
                    "state_fingerprint": snapshot.fingerprint,
                    "reason": "candidate_space_exhausted",
                },
            )
        )

    @staticmethod
    def _clear(
        x: float,
        y: float,
        item: LayoutItem,
        obstacles: tuple[RegionOccupant | SpaceReservation, ...],
        placements: list[LayoutPlacement],
        clearance: float,
    ) -> bool:
        for obstacle in obstacles:
            obstacle_envelope = (
                obstacle.effective_operation_radius
                if isinstance(obstacle, SpaceReservation)
                else obstacle.radius
            )
            if hypot(x - obstacle.x, y - obstacle.y) + 1e-9 < (
                BatchSpaceAllocator._required_separation(
                    item.radius,
                    item.effective_operation_radius,
                    obstacle.radius,
                    obstacle_envelope,
                    clearance,
                )
            ):
                return False
        for placement in placements:
            if hypot(x - placement.x, y - placement.y) + 1e-9 < (
                BatchSpaceAllocator._required_separation(
                    item.radius,
                    item.effective_operation_radius,
                    placement.radius,
                    placement.effective_operation_radius,
                    clearance,
                )
            ):
                return False
        return True

    @staticmethod
    def _required_separation(
        first_radius: float,
        first_envelope_radius: float,
        second_radius: float,
        second_envelope_radius: float,
        clearance: float,
    ) -> float:
        return max(
            first_envelope_radius + second_radius,
            first_radius + second_envelope_radius,
        ) + clearance

    @staticmethod
    def _candidate_pitches(
        items: tuple[LayoutItem, ...],
        clearance: float,
    ) -> tuple[float, ...]:
        pitches = {0.04}
        for item in items:
            pitches.add(
                max(
                    0.04,
                    item.radius
                    + item.effective_operation_radius
                    + clearance,
                )
            )
        for index, item in enumerate(items):
            for other in items[index + 1 :]:
                pitches.add(
                    BatchSpaceAllocator._required_separation(
                        item.radius,
                        item.effective_operation_radius,
                        other.radius,
                        other.effective_operation_radius,
                        clearance,
                    )
                )
        return tuple(
            sorted(
                {
                    round(float(pitch), 9)
                    for pitch in pitches
                    if isfinite(pitch) and pitch > 0.0
                }
            )
        )

    @staticmethod
    def _candidate_points(
        definition: RegionDefinition,
        item: LayoutItem,
        *,
        pitches: tuple[float, ...],
    ) -> tuple[tuple[float, float], ...]:
        geometry = definition.geometry
        margin = definition.edge_margin
        preferred: list[tuple[float, float]] = []
        for x, y in definition.placement_slots:
            if definition.placement_issue(
                x,
                y,
                item.radius,
                operation_radius=item.effective_operation_radius,
                operation_phases=(
                    item.placement_operation_envelope_phases
                ),
            ) is None:
                point = (round(float(x), 6), round(float(y), 6))
                if point not in preferred:
                    preferred.append(point)
        if definition.placement_slots_strict:
            return tuple(preferred)

        points: list[tuple[float, float]] = []
        if item.current_x is not None and item.current_y is not None:
            if definition.placement_issue(
                item.current_x,
                item.current_y,
                item.radius,
                operation_radius=item.effective_operation_radius,
                operation_phases=(
                    item.placement_operation_envelope_phases
                ),
            ) is None:
                points.append((item.current_x, item.current_y))
        if definition.placement_issue(
            0.0,
            0.0,
            item.radius,
            operation_radius=item.effective_operation_radius,
            operation_phases=item.placement_operation_envelope_phases,
        ) is None:
            points.append((0.0, 0.0))

        if geometry.shape is RegionShape.RECTANGLE:
            assert geometry.width is not None and geometry.depth is not None
            x_limit = geometry.width / 2.0 - item.radius - margin
            y_limit = geometry.depth / 2.0 - item.radius - margin
        else:
            assert geometry.radius is not None
            x_limit = y_limit = geometry.radius - item.radius - margin
            for pitch in pitches:
                half_pitch = pitch / 2.0
                for x, y in (
                    (-half_pitch, 0.0),
                    (half_pitch, 0.0),
                    (0.0, -half_pitch),
                    (0.0, half_pitch),
                ):
                    if definition.placement_issue(
                        x,
                        y,
                        item.radius,
                        operation_radius=item.effective_operation_radius,
                        operation_phases=(
                            item.placement_operation_envelope_phases
                        ),
                    ) is None:
                        points.append((x, y))

        if x_limit >= 0.0 and y_limit >= 0.0:
            for pitch in pitches:
                x_start = ceil(-x_limit / pitch)
                x_stop = floor(x_limit / pitch)
                y_start = ceil(-y_limit / pitch)
                y_stop = floor(y_limit / pitch)
                for x_index in range(x_start, x_stop + 1):
                    for y_index in range(y_start, y_stop + 1):
                        x = x_index * pitch
                        y = y_index * pitch
                        if definition.placement_issue(
                            x,
                            y,
                            item.radius,
                            operation_radius=(
                                item.effective_operation_radius
                            ),
                            operation_phases=(
                                item.placement_operation_envelope_phases
                            ),
                        ) is None:
                            points.append((x, y))

        origin_x = item.current_x if item.current_x is not None else 0.0
        origin_y = item.current_y if item.current_y is not None else 0.0
        unique_fallback = tuple(dict.fromkeys(
            (round(float(x), 6), round(float(y), 6))
            for x, y in points
            if (round(float(x), 6), round(float(y), 6))
            not in preferred
        ))
        ordered_fallback = sorted(
            unique_fallback,
            key=lambda value: (
                hypot(value[0] - origin_x, value[1] - origin_y),
                hypot(value[0], value[1]),
                value[1],
                value[0],
            ),
        )
        return tuple((*preferred, *ordered_fallback))

    @staticmethod
    def _plan(
        request: LayoutRequest,
        placements: tuple[LayoutPlacement, ...],
        *,
        search_states: int,
    ) -> LayoutPlanArtifact:
        payload = {
            "region_ref": request.snapshot.definition.region_ref,
            "world_revision": request.snapshot.world_revision,
            "state_fingerprint": request.snapshot.fingerprint,
            "reservation_group": request.reservation_group,
            "robust_clearance_margin_m": (
                request.robust_clearance_margin
            ),
            "placements": [
                placement.to_dict() for placement in placements
            ],
        }
        digest = sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:20]
        return LayoutPlanArtifact(
            plan_id=f"region-layout-{digest}",
            region_ref=request.snapshot.definition.region_ref,
            world_revision=request.snapshot.world_revision,
            state_fingerprint=request.snapshot.fingerprint,
            reservation_group=request.reservation_group,
            placements=placements,
            search_states=search_states,
            robust_clearance_margin=request.robust_clearance_margin,
        )


__all__ = [
    "BatchSpaceAllocator",
    "LayoutFailure",
    "LayoutItem",
    "LayoutPlacement",
    "LayoutPlanArtifact",
    "LayoutRequest",
    "LayoutResult",
    "LayoutVerdict",
    "RegionDefinition",
    "RegionExclusion",
    "RegionGeometry",
    "RegionOccupancySnapshot",
    "RegionOccupancyState",
    "RegionOccupant",
    "RegionReasoner",
    "RegionRequirement",
    "RegionRequirementKind",
    "RegionShape",
    "ReservationStatus",
    "RequirementPhase",
    "SpaceReservation",
]
