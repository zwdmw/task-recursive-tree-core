from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from math import hypot, isfinite
from typing import Any, Callable, Iterable, Mapping

from task_recursive_tree.world.regions import (
    BatchSpaceAllocator,
    LayoutFailure,
    LayoutItem,
    LayoutPlacement,
    LayoutPlanArtifact,
    LayoutRequest,
    LayoutVerdict,
    RegionDefinition,
    RegionExclusion,
    RegionGeometry,
    RegionOccupancySnapshot,
    RegionOccupant,
    RegionShape,
    ReservationStatus,
    SpaceReservation,
)

from .continuation_routes import continuation_route_context
from .paths import import_harness_module


FLOOR_REGION_SIZE_M = 7.0
_CLEAR_OF_WORKSPACE_FLOOR_ROBUST_CLEARANCE_MARGIN_M = 0.04
PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION = (
    "placement_operation_envelope/1.0"
)
# Public compatibility alias for callers that imported the original name.
PLACEMENT_ENVELOPE_MODEL_VERSION = (
    PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION
)
RELOCATION_LAYOUT_CONSTRAINT_KEYS = (
    "baseline_pose",
    "minimum_relocation_distance",
    "path_segments",
    "object_radius",
    "required_clearance",
    "minimum_improvement",
)
RELOCATION_CONTINUATION_CONTEXT_KEYS = (
    "continuation_anchor_pose",
    "continuation_goal_poses",
)
PROTECTED_RELOCATION_ENTITY_IDS_KEY = (
    "protected_relocation_entity_ids"
)


@dataclass(frozen=True)
class RegionLayoutSelection:
    region: Mapping[str, Any] | None
    snapshot: RegionOccupancySnapshot | None
    plan: LayoutPlanArtifact | None
    failure: LayoutFailure | None
    candidate_audit: tuple[Mapping[str, Any], ...] = ()
    relation: str | None = None

    @property
    def feasible(self) -> bool:
        return self.plan is not None


@dataclass(frozen=True)
class _LayoutTargetPlacement:
    x: float
    y: float
    radius: float
    placement_operation_envelope_radius: float
    placement_operation_envelope_model_ref: str
    placement_operation_envelope_phases: frozenset[str] | None
    status: str

    @property
    def active_operation_radius(self) -> float:
        if self.status == ReservationStatus.ACTIVE.value:
            return max(
                self.radius,
                self.placement_operation_envelope_radius,
            )
        return self.radius


@dataclass(frozen=True)
class _PlacementOperationEnvelope:
    radius: float
    model_ref: str
    phases: frozenset[str] | None = None


@dataclass(frozen=True)
class _RelocationLayoutConstraints:
    baseline_xy: tuple[float, float] | None
    minimum_relocation_distance: float
    path_segments: tuple[
        tuple[tuple[float, float], tuple[float, float]],
        ...,
    ]
    object_radius: float | None
    required_clearance: float
    minimum_improvement: float

    def allows(self, item: LayoutItem, x: float, y: float) -> bool:
        candidate = (float(x), float(y))
        if (
            self.baseline_xy is not None
            and hypot(
                candidate[0] - self.baseline_xy[0],
                candidate[1] - self.baseline_xy[1],
            ) + 1e-9 < self.minimum_relocation_distance
        ):
            return False
        if not self.path_segments:
            return True

        object_radius = (
            self.object_radius
            if self.object_radius is not None
            else item.radius
        )
        baseline_clearance = (
            min(
                _point_segment_distance(
                    self.baseline_xy,
                    start,
                    end,
                )
                for start, end in self.path_segments
            ) - object_radius
            if self.baseline_xy is not None
            else 0.0
        )
        clearance_threshold = max(
            self.required_clearance,
            baseline_clearance + self.minimum_improvement,
        )
        required_center_distance = object_radius + clearance_threshold
        return all(
            _point_segment_distance(candidate, start, end) + 1e-9
            >= required_center_distance
            for start, end in self.path_segments
        )


class HarnessRegionSpaceAdapter:
    """Adapt Harness scene evidence to the kernel-owned region protocol."""

    def __init__(
        self,
        *,
        runtime: Any,
        harness_root: str | None = None,
        allocator: BatchSpaceAllocator | None = None,
    ) -> None:
        self.runtime = runtime
        self.harness_root = harness_root
        self.allocator = allocator or BatchSpaceAllocator()
        install_floor_region_frame(getattr(runtime, "perception", None))

    def scene_snapshot(self) -> dict[str, Any]:
        module = import_harness_module(
            "er2sim.scene_capabilities",
            harness_root=self.harness_root,
        )
        return dict(
            module.build_scene_capability_snapshot(
                self.runtime.perception
            )
        )

    def candidate_refs(
        self,
        params: Mapping[str, Any],
        *,
        snapshot: Mapping[str, Any] | None = None,
    ) -> tuple[str, ...]:
        scene_snapshot = dict(snapshot or self.scene_snapshot())
        module = import_harness_module(
            "er2sim.scene_capabilities",
            harness_root=self.harness_root,
        )
        regions = module.region_index(scene_snapshot)
        avoided = {
            str(value)
            for value in params.get("avoid_owner_ids", ()) or ()
            if value is not None
        }
        refs: list[str] = []
        explicit_candidates = params.get("candidate_region_refs")
        if isinstance(explicit_candidates, str):
            refs.append(explicit_candidates)
        elif isinstance(explicit_candidates, (list, tuple)):
            refs.extend(str(value) for value in explicit_candidates)
        for key in ("staging_region_ref", "region_ref"):
            value = params.get(key)
            if isinstance(value, str) and value:
                refs.append(value)

        requested_owner = _first_entity(params, "destination")
        if requested_owner:
            refs.extend(
                str(ref)
                for ref, region in regions.items()
                if str(region.get("owner_ref")) == requested_owner
            )

        policy_name = str(
            params.get("required_policy") or "clear_support_region"
        )
        policy = (
            scene_snapshot.get("policies", {}).get(policy_name, {}) or {}
        )
        default_ref = policy.get("default_destination_region_ref")
        if isinstance(default_ref, str) and default_ref:
            refs.append(default_ref)
        refs.extend(
            str(value)
            for value in policy.get("destination_region_refs", ()) or ()
            if value is not None
        )

        normalized: list[str] = []
        for raw_ref in refs:
            ref = _resolve_region_ref(str(raw_ref), regions)
            if ref is None or ref in normalized:
                continue
            region = regions[ref]
            if str(region.get("owner_ref")) in avoided:
                continue
            normalized.append(ref)
        return tuple(normalized)

    def placement_region_ref(
        self,
        destination_id: str,
        relation: str,
        *,
        requested_region_ref: str | None = None,
        selector: str | None = None,
        snapshot: Mapping[str, Any] | None = None,
    ) -> str | None:
        """Resolve the exact destination-owned region for one placement."""

        scene_snapshot = dict(snapshot or self.scene_snapshot())
        regions = self._region_index(scene_snapshot)
        destination_id = str(destination_id)
        relation = str(relation)
        requested_selector = str(selector) if selector else None

        if requested_region_ref:
            resolved = _resolve_region_ref(
                str(requested_region_ref),
                regions,
            )
            candidates = (
                ((resolved, regions[resolved]),)
                if resolved is not None
                else ()
            )
        else:
            candidates = tuple(
                (str(ref), region)
                for ref, region in regions.items()
                if str(region.get("owner_ref") or "") == destination_id
            )

        for region_ref, region in candidates:
            if str(region.get("owner_ref") or "") != destination_id:
                continue
            allowed = tuple(
                str(value)
                for value in region.get("allowed_relations", ()) or ()
            )
            if allowed and relation not in allowed:
                continue
            if (
                requested_selector is not None
                and str(region.get("selector") or "") != requested_selector
            ):
                continue
            return region_ref
        return None

    def inspect(
        self,
        region_ref: str,
        *,
        scene_snapshot: Mapping[str, Any] | None = None,
    ) -> RegionOccupancySnapshot:
        snapshot = dict(scene_snapshot or self.scene_snapshot())
        module = import_harness_module(
            "er2sim.scene_capabilities",
            harness_root=self.harness_root,
        )
        regions = module.region_index(snapshot)
        resolved_ref = _resolve_region_ref(region_ref, regions)
        if resolved_ref is None:
            raise ValueError(f"Unknown support region: {region_ref}")
        capability = regions[resolved_ref]
        definition = self._definition(resolved_ref, capability)
        perception = self.runtime.perception
        occupant_ids = tuple(
            dict.fromkeys(
                str(value)
                for value in capability.get("direct_occupants", ()) or ()
            )
        )
        occupants: list[RegionOccupant] = []
        complete = True
        for entity_id in occupant_ids:
            if entity_id == definition.owner_id:
                continue
            pose = _entity_pose(perception, entity_id)
            local = (
                self._world_xy_to_local(
                    definition.owner_id,
                    (float(pose[0]), float(pose[1])),
                )
                if pose is not None
                else None
            )
            radius = self.footprint_radius(entity_id)
            if local is None or radius is None:
                complete = False
                continue
            metadata = _catalog_entry(perception, entity_id)
            occupants.append(
                RegionOccupant(
                    entity_id=entity_id,
                    radius=radius,
                    x=float(local[0]),
                    y=float(local[1]),
                    movable=bool(metadata.get("movable", False)),
                    support_id=definition.owner_id,
                )
            )
        return RegionOccupancySnapshot(
            definition=definition,
            world_revision=self.world_revision,
            occupants=tuple(occupants),
            reservations=self._reservations(definition.owner_id),
            evidence_refs=tuple(
                f"sim_pose_{entity_id}" for entity_id in occupant_ids
            ),
            complete=complete,
        )

    def plan_layout(
        self,
        region_ref: str,
        object_ids: tuple[str, ...] | list[str],
        *,
        reservation_group: str,
        relation: str | None = None,
        destination_id: str | None = None,
        preserve_entity_ids: tuple[str, ...] | list[str] = (),
        excluded_local_xy: tuple[tuple[float, float], ...] | list[
            tuple[float, float]
        ] = (),
        placement_policy: str | None = None,
        robust_clearance_margin_m: Any | None = None,
        placement_constraints: Mapping[str, Any] | None = None,
        max_search_states: int = 25000,
        scene_snapshot: Mapping[str, Any] | None = None,
    ) -> RegionLayoutSelection:
        try:
            occupancy = self.inspect(
                region_ref,
                scene_snapshot=scene_snapshot,
            )
        except Exception as exc:
            failure = LayoutFailure(
                verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                code="PERCEPTION_INSUFFICIENT",
                message=f"Cannot inspect {region_ref}: {exc}",
                details={
                    "region_ref": str(region_ref),
                    "exception_type": type(exc).__name__,
                },
            )
            return RegionLayoutSelection(None, None, None, failure)

        resolved_destination_id = str(
            destination_id or occupancy.definition.owner_id
        )
        if resolved_destination_id != occupancy.definition.owner_id:
            failure = LayoutFailure(
                verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                code="PERCEPTION_INSUFFICIENT",
                message=(
                    f"Layout destination {resolved_destination_id!r} does "
                    f"not own region {occupancy.definition.region_ref!r}"
                ),
                details={
                    "region_ref": occupancy.definition.region_ref,
                    "region_owner_id": occupancy.definition.owner_id,
                    "destination_id": resolved_destination_id,
                },
            )
            return RegionLayoutSelection(
                self.region(occupancy.definition.region_ref),
                occupancy,
                None,
                failure,
            )
        resolved_relation = str(
            relation
            or (
                occupancy.definition.allowed_relations[0]
                if occupancy.definition.allowed_relations
                else "on_support"
            )
        )
        try:
            robust_clearance_margin = (
                _layout_robust_clearance_margin(
                    getattr(self.runtime, "perception", None),
                    occupancy.definition.owner_id,
                    placement_policy=placement_policy,
                    explicit_margin=robust_clearance_margin_m,
                )
            )
            relocation_constraints = self._relocation_layout_constraints(
                occupancy.definition.owner_id,
                placement_constraints or {},
            )
            # The layout point does not determine the robot's release stance.
            # Validate the context here and defer route feasibility until the
            # prepare-placement route has an actual stance to assess.
            continuation_route_context(placement_constraints or {})
        except ValueError as exc:
            failure = LayoutFailure(
                verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                code="INVALID_PLACEMENT_CONSTRAINTS",
                message=str(exc),
                details={
                    "region_ref": occupancy.definition.region_ref,
                    "destination_id": resolved_destination_id,
                    "placement_policy": placement_policy,
                    "robust_clearance_margin_m": (
                        robust_clearance_margin_m
                    ),
                },
            )
            return RegionLayoutSelection(
                self.region(occupancy.definition.region_ref),
                occupancy,
                None,
                failure,
                relation=resolved_relation,
            )
        items: list[LayoutItem] = []
        for entity_id in dict.fromkeys(str(value) for value in object_ids):
            radius = self.footprint_radius(entity_id)
            operation_envelope = self.placement_operation_envelope(
                entity_id,
                relation=resolved_relation,
                destination_id=resolved_destination_id,
                region_ref=occupancy.definition.region_ref,
            )
            pose = _entity_pose(self.runtime.perception, entity_id)
            current = (
                self._world_xy_to_local(
                    occupancy.definition.owner_id,
                    (float(pose[0]), float(pose[1])),
                )
                if pose is not None
                else None
            )
            if radius is None or operation_envelope is None:
                failure = LayoutFailure(
                    verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                    code="PERCEPTION_INSUFFICIENT",
                    message=(
                        f"Missing footprint or placement envelope for "
                        f"{entity_id}"
                    ),
                    details={
                        "entity_id": entity_id,
                        "region_ref": occupancy.definition.region_ref,
                        "footprint_radius_m": radius,
                        "placement_operation_envelope_radius_m": (
                            operation_envelope.radius
                            if operation_envelope is not None
                            else None
                        ),
                        "placement_operation_envelope_model_ref": (
                            operation_envelope.model_ref
                            if operation_envelope is not None
                            else None
                        ),
                        "placement_operation_envelope_phases": (
                            sorted(operation_envelope.phases)
                            if (
                                operation_envelope is not None
                                and operation_envelope.phases is not None
                            )
                            else None
                        ),
                    },
                )
                return RegionLayoutSelection(
                    self.region(occupancy.definition.region_ref),
                    occupancy,
                    None,
                    failure,
                )
            items.append(
                LayoutItem(
                    entity_id=entity_id,
                    radius=radius,
                    current_x=(
                        float(current[0]) if current is not None else None
                    ),
                    current_y=(
                        float(current[1]) if current is not None else None
                    ),
                    placement_operation_envelope_radius=(
                        operation_envelope.radius
                    ),
                    placement_operation_envelope_model_ref=(
                        operation_envelope.model_ref
                    ),
                    placement_operation_envelope_phases=getattr(
                        operation_envelope,
                        "phases",
                        None,
                    ),
                )
            )
        exclusions = _finite_local_xy_values(excluded_local_xy)
        if exclusions and items:
            exclusion_radius = max(
                0.04,
                max(item.radius for item in items),
            )
            exclusion_envelope_radius = max(
                exclusion_radius,
                max(
                    item.effective_operation_radius
                    for item in items
                ),
            )
            exclusion_operation_phases = _combined_operation_phases(items)
            occupancy = replace(
                occupancy,
                reservations=(
                    *occupancy.reservations,
                    *(
                        SpaceReservation(
                            reservation_id=(
                                f"excluded-layout-target:{index}:"
                                f"{x:.6f}:{y:.6f}"
                            ),
                            entity_id=(
                                f"excluded-layout-target:{index}"
                            ),
                            x=x,
                            y=y,
                            radius=exclusion_radius,
                            world_revision=occupancy.world_revision,
                            reservation_group=(
                                f"excluded:{reservation_group}"
                            ),
                            status=ReservationStatus.ACTIVE,
                            placement_operation_envelope_radius=(
                                exclusion_envelope_radius
                            ),
                            placement_operation_envelope_model_ref=(
                                "temporary_layout_exclusion/1.0"
                            ),
                            placement_operation_envelope_phases=(
                                exclusion_operation_phases
                            ),
                        )
                        for index, (x, y) in enumerate(exclusions)
                    ),
                ),
                evidence_refs=(
                    *occupancy.evidence_refs,
                    *(
                        f"excluded_layout_target_{index}"
                        for index in range(len(exclusions))
                    ),
                ),
            )
        try:
            physical_layout_filter = (
                self._physical_placement_layout_filter(
                    destination_id=resolved_destination_id,
                    relation=resolved_relation,
                    batch_entity_ids=frozenset(
                        item.entity_id for item in items
                    ),
                )
            )
        except Exception as exc:
            failure = LayoutFailure(
                verdict=LayoutVerdict.PERCEPTION_INSUFFICIENT,
                code="PERCEPTION_INSUFFICIENT",
                message=(
                    "Cannot validate physical placement candidates for "
                    f"{occupancy.definition.region_ref}: {exc}"
                ),
                details={
                    "region_ref": occupancy.definition.region_ref,
                    "destination_id": resolved_destination_id,
                    "exception_type": type(exc).__name__,
                },
            )
            return RegionLayoutSelection(
                self.region(occupancy.definition.region_ref),
                occupancy,
                None,
                failure,
                relation=resolved_relation,
            )
        candidate_filter = (
            relocation_constraints.allows
            if relocation_constraints is not None
            else None
        )
        result = self.allocator.allocate(
            LayoutRequest(
                snapshot=occupancy,
                items=tuple(items),
                reservation_group=str(reservation_group),
                preserve_entity_ids=frozenset(
                    str(value) for value in preserve_entity_ids
                ),
                robust_clearance_margin=robust_clearance_margin,
                max_search_states=max(1, int(max_search_states)),
                candidate_filter=candidate_filter,
                layout_filter=physical_layout_filter,
            )
        )
        return RegionLayoutSelection(
            self.region(occupancy.definition.region_ref),
            occupancy,
            result.plan,
            result.failure,
            relation=resolved_relation,
        )

    def select_layout(
        self,
        object_ids: tuple[str, ...] | list[str],
        params: Mapping[str, Any],
        *,
        reservation_group: str,
        preserve_entity_ids: tuple[str, ...] | list[str] = (),
        excluded_local_xy: tuple[tuple[float, float], ...] | list[
            tuple[float, float]
        ] = (),
        max_search_states: int = 25000,
    ) -> RegionLayoutSelection:
        scene_snapshot = self.scene_snapshot()
        refs = self.candidate_refs(params, snapshot=scene_snapshot)
        if bool(params.get("lock_region_selection", False)):
            locked = params.get("staging_region_ref") or params.get(
                "region_ref"
            )
            refs = (
                tuple(
                    ref
                    for ref in refs
                    if ref == _resolve_region_ref(
                        str(locked),
                        self._region_index(scene_snapshot),
                    )
                )
                if locked
                else refs[:1]
            )

        audit: list[dict[str, Any]] = []
        failures: list[LayoutFailure] = []
        for region_ref in refs:
            decision = self.plan_layout(
                region_ref,
                object_ids,
                reservation_group=reservation_group,
                relation=(
                    str(params.get("relation"))
                    if params.get("relation") is not None
                    else None
                ),
                preserve_entity_ids=preserve_entity_ids,
                excluded_local_xy=excluded_local_xy,
                placement_policy=(
                    str(params.get("placement_policy"))
                    if params.get("placement_policy") is not None
                    else None
                ),
                robust_clearance_margin_m=params.get(
                    "robust_clearance_margin_m"
                ),
                placement_constraints={
                    key: copy.deepcopy(params[key])
                    for key in (
                        *RELOCATION_LAYOUT_CONSTRAINT_KEYS,
                        *RELOCATION_CONTINUATION_CONTEXT_KEYS,
                    )
                    if params.get(key) is not None
                },
                max_search_states=max_search_states,
                scene_snapshot=scene_snapshot,
            )
            audit.append(
                {
                    "region_ref": region_ref,
                    "owner_id": (
                        decision.snapshot.definition.owner_id
                        if decision.snapshot is not None
                        else None
                    ),
                    "verdict": (
                        "feasible"
                        if decision.feasible
                        else decision.failure.verdict.value
                        if decision.failure is not None
                        else "unknown"
                    ),
                    "failure": (
                        decision.failure.to_dict()
                        if decision.failure is not None
                        else None
                    ),
                }
            )
            if decision.feasible:
                return RegionLayoutSelection(
                    decision.region,
                    decision.snapshot,
                    decision.plan,
                    None,
                    tuple(audit),
                    decision.relation,
                )
            if decision.failure is not None:
                failures.append(decision.failure)

        failure = _aggregate_failure(refs, failures, audit)
        return RegionLayoutSelection(
            None,
            None,
            None,
            failure,
            tuple(audit),
        )

    def validate_layout_targets(
        self,
        artifact: Mapping[str, Any],
        *,
        artifact_ref: str | None = None,
        ignored_reservation_bindings: Iterable[
            tuple[str, str]
        ] = (),
    ) -> tuple[bool, str]:
        """Validate a batch layout against the current region geometry.

        World revisions may advance while the batch is placed one item at a
        time. The artifact remains usable when its local targets still fit,
        completed subjects remain at their targets, and no external occupant
        or reservation has claimed the planned space.
        """

        if str(artifact.get("kind") or "") != "layout_targets":
            return False, "layout target payload has the wrong kind"
        region_ref = str(artifact.get("region_ref") or "")
        anchor_id = str(artifact.get("anchor_id") or "")
        if not region_ref or not anchor_id:
            return False, "layout target payload is missing its region anchor"
        planned_revision = _integer_or_none(
            artifact.get("world_revision")
        )
        if (
            planned_revision is not None
            and planned_revision > self.world_revision
        ):
            return False, "layout target comes from a future world revision"

        try:
            snapshot = self.inspect(region_ref)
        except Exception as exc:
            return False, f"layout region cannot be inspected: {exc}"
        if not snapshot.complete:
            return False, "layout region occupancy is incomplete"
        if snapshot.definition.owner_id != anchor_id:
            return False, "layout anchor no longer matches the region owner"

        raw_subjects = artifact.get("subject_ids")
        if not isinstance(raw_subjects, (list, tuple)) or not raw_subjects:
            return False, "layout target payload has no subject set"
        subject_ids = tuple(
            dict.fromkeys(str(value) for value in raw_subjects)
        )
        targets = artifact.get("targets")
        if not isinstance(targets, Mapping):
            return False, "layout target payload has no target map"
        if set(str(value) for value in targets) != set(subject_ids):
            return False, "layout target entity set changed"

        placements: dict[str, _LayoutTargetPlacement] = {}
        for entity_id in subject_ids:
            target = targets.get(entity_id)
            if not isinstance(target, Mapping):
                return False, f"layout target for {entity_id} is malformed"
            local = target.get("local_xy")
            if not isinstance(local, (list, tuple)) or len(local) != 2:
                return False, f"layout target for {entity_id} has no local_xy"
            try:
                x = float(local[0])
                y = float(local[1])
                status = _reservation_status(
                    target.get("reservation_status")
                )
                radius_value = _target_radius(
                    target,
                    artifact,
                    entity_id,
                )
                if radius_value is None:
                    radius_value = self.footprint_radius(entity_id)
                radius = float(radius_value)
                operation_envelope = (
                    self._target_operation_envelope(
                        target=target,
                        artifact=artifact,
                        entity_id=entity_id,
                        relation=str(
                            artifact.get("relation")
                            or (
                                snapshot.definition.allowed_relations[0]
                                if snapshot.definition.allowed_relations
                                else "on_support"
                            )
                        ),
                        destination_id=anchor_id,
                        region_ref=region_ref,
                    )
                    if status == ReservationStatus.ACTIVE.value
                    else _PlacementOperationEnvelope(
                        radius=radius,
                        model_ref="fulfilled_physical_footprint/1.0",
                        phases=frozenset(),
                    )
                )
            except (TypeError, ValueError):
                return False, f"layout target for {entity_id} is malformed"
            if operation_envelope is None:
                return False, (
                    f"layout target for {entity_id} has no reproducible "
                    "placement operation envelope"
                )
            placement_operation_envelope_radius = float(
                operation_envelope.radius
            )
            if not all(
                isfinite(value)
                for value in (
                    x,
                    y,
                    radius,
                    placement_operation_envelope_radius,
                )
            ):
                return False, f"layout target for {entity_id} is non-finite"
            if (
                radius <= 0.0
                or placement_operation_envelope_radius <= 0.0
            ):
                return False, f"layout target for {entity_id} has no footprint"
            active_operation_radius = (
                max(radius, placement_operation_envelope_radius)
                if status == ReservationStatus.ACTIVE.value
                else radius
            )
            placement_issue = snapshot.definition.placement_issue(
                x,
                y,
                radius,
                operation_radius=active_operation_radius,
                operation_phases=operation_envelope.phases,
            )
            if placement_issue is not None:
                return False, (
                    f"layout target for {entity_id} is invalid: "
                    f"{placement_issue}"
                )
            placements[entity_id] = _LayoutTargetPlacement(
                x=x,
                y=y,
                radius=radius,
                placement_operation_envelope_radius=max(
                    radius,
                    placement_operation_envelope_radius,
                ),
                placement_operation_envelope_model_ref=(
                    operation_envelope.model_ref
                ),
                placement_operation_envelope_phases=(
                    operation_envelope.phases
                ),
                status=status,
            )

        ordered = list(subject_ids)
        for index, entity_id in enumerate(ordered):
            placement = placements[entity_id]
            for other_id in ordered[index + 1 :]:
                other = placements[other_id]
                if hypot(
                    placement.x - other.x,
                    placement.y - other.y,
                ) + 1e-9 < _required_placement_separation(
                    placement.radius,
                    placement.active_operation_radius,
                    other.radius,
                    other.active_operation_radius,
                    snapshot.definition.clearance,
                ):
                    return False, (
                        f"layout targets for {entity_id} and {other_id} "
                        "have overlapping placement envelopes"
                    )

        subject_set = set(subject_ids)
        tolerance = max(0.04, snapshot.definition.clearance)
        for occupant in snapshot.occupants:
            if occupant.entity_id in subject_set:
                placement = placements[occupant.entity_id]
                if (
                    placement.status == ReservationStatus.FULFILLED.value
                    and hypot(
                        occupant.x - placement.x,
                        occupant.y - placement.y,
                    )
                    > tolerance
                ):
                    return False, (
                        f"fulfilled layout target for {occupant.entity_id} "
                        "was displaced"
                    )
                if placement.status == ReservationStatus.FULFILLED.value:
                    for other_id, other in placements.items():
                        if (
                            other_id == occupant.entity_id
                            or other.status != ReservationStatus.ACTIVE.value
                        ):
                            continue
                        if hypot(
                            occupant.x - other.x,
                            occupant.y - other.y,
                        ) + 1e-9 < (
                            occupant.radius
                            + other.active_operation_radius
                            + snapshot.definition.clearance
                        ):
                            return False, (
                                f"fulfilled subject {occupant.entity_id} "
                                f"blocks active layout target for {other_id}"
                            )
                continue
            for entity_id, placement in placements.items():
                if hypot(
                    placement.x - occupant.x,
                    placement.y - occupant.y,
                ) + 1e-9 < (
                    placement.active_operation_radius
                    + occupant.radius
                    + snapshot.definition.clearance
                ):
                    return False, (
                        f"layout target for {entity_id} is occupied by "
                        f"{occupant.entity_id}"
                    )

        ignored_bindings: set[tuple[str, str]] = set()
        for binding in ignored_reservation_bindings:
            if not isinstance(binding, (list, tuple)) or len(binding) != 2:
                continue
            ignored_bindings.add((str(binding[0]), str(binding[1])))
        for reservation in snapshot.reservations:
            if not reservation.active:
                continue
            if (
                artifact_ref
                and reservation.artifact_ref == str(artifact_ref)
            ):
                continue
            if (
                str(reservation.artifact_ref or ""),
                str(reservation.entity_id),
            ) in ignored_bindings:
                continue
            for entity_id, placement in placements.items():
                if hypot(
                    placement.x - reservation.x,
                    placement.y - reservation.y,
                ) + 1e-9 < _required_placement_separation(
                    placement.radius,
                    placement.active_operation_radius,
                    reservation.radius,
                    reservation.effective_operation_radius,
                    snapshot.definition.clearance,
                ):
                    return False, (
                        f"layout target for {entity_id} conflicts with "
                        f"reservation {reservation.reservation_id}"
                    )
        return True, ""

    def assess_layout_target_fulfillment(
        self,
        artifact: Mapping[str, Any],
        entity_id: str,
    ) -> tuple[bool, dict[str, Any]]:
        """Verify that one reserved target is physically and semantically met."""

        entity_id = str(entity_id)
        artifact_ref = str(artifact.get("artifact_ref") or "")
        region_ref = str(artifact.get("region_ref") or "")
        anchor_id = str(artifact.get("anchor_id") or "")
        evidence: dict[str, Any] = {
            "artifact_ref": artifact_ref or None,
            "entity_id": entity_id,
            "region_ref": region_ref or None,
            "anchor_id": anchor_id or None,
            "world_revision": self.world_revision,
        }
        if (
            str(artifact.get("kind") or "") != "layout_targets"
            or str(artifact.get("source") or "")
            != "task_recursive_tree_batch_allocator/1.0"
            or artifact.get("invalidated")
            or not region_ref
            or not anchor_id
        ):
            evidence["reason_code"] = "INVALID_LAYOUT_TARGET"
            return False, evidence

        targets = artifact.get("targets")
        target = (
            targets.get(entity_id)
            if isinstance(targets, Mapping)
            else None
        )
        if not isinstance(target, Mapping):
            evidence["reason_code"] = "TARGET_NOT_FOUND"
            return False, evidence
        local = target.get("local_xy")
        if not isinstance(local, (list, tuple)) or len(local) != 2:
            evidence["reason_code"] = "TARGET_POSITION_MALFORMED"
            return False, evidence
        try:
            target_x = float(local[0])
            target_y = float(local[1])
        except (TypeError, ValueError):
            evidence["reason_code"] = "TARGET_POSITION_MALFORMED"
            return False, evidence
        if not all(isfinite(value) for value in (target_x, target_y)):
            evidence["reason_code"] = "TARGET_POSITION_MALFORMED"
            return False, evidence

        try:
            snapshot = self.inspect(region_ref)
        except Exception as exc:
            evidence.update({
                "reason_code": "REGION_INSPECTION_FAILED",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            })
            return False, evidence
        if not snapshot.complete:
            evidence["reason_code"] = "REGION_OCCUPANCY_INCOMPLETE"
            return False, evidence
        if snapshot.definition.owner_id != anchor_id:
            evidence["reason_code"] = "ANCHOR_MISMATCH"
            return False, evidence
        radius = _target_radius(target, artifact, entity_id)
        if radius is None:
            radius = self.footprint_radius(entity_id)
        if radius is None:
            evidence["reason_code"] = "TARGET_FOOTPRINT_UNKNOWN"
            return False, evidence
        placement_issue = snapshot.definition.placement_issue(
            target_x,
            target_y,
            radius,
        )
        if placement_issue is not None:
            evidence.update({
                "reason_code": "TARGET_NO_LONGER_VALID",
                "placement_issue": placement_issue,
            })
            return False, evidence

        occupant = next(
            (
                value
                for value in snapshot.occupants
                if value.entity_id == entity_id
            ),
            None,
        )
        if occupant is None or str(occupant.support_id or "") != anchor_id:
            evidence["reason_code"] = "NOT_SUPPORTED_BY_TARGET_ANCHOR"
            return False, evidence

        support_of = getattr(self.runtime.perception, "support_of", None)
        if callable(support_of):
            try:
                observed_support = support_of(entity_id)
            except Exception as exc:
                evidence.update({
                    "reason_code": "SUPPORT_OBSERVATION_FAILED",
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                })
                return False, evidence
            evidence["observed_support_id"] = (
                str(observed_support)
                if observed_support is not None
                else None
            )
            if str(observed_support or "") != anchor_id:
                evidence["reason_code"] = (
                    "NOT_SUPPORTED_BY_TARGET_ANCHOR"
                )
                return False, evidence

        fallback_tolerance = max(
            0.04,
            float(snapshot.definition.clearance),
        )
        tolerance = _positive_float(
            target.get(
                "position_tolerance_m",
                artifact.get("position_tolerance_m"),
            ),
            fallback_tolerance,
        )
        distance = hypot(
            float(occupant.x) - target_x,
            float(occupant.y) - target_y,
        )
        evidence.update({
            "target_local_xy": [target_x, target_y],
            "observed_local_xy": [
                float(occupant.x),
                float(occupant.y),
            ],
            "position_error_m": distance,
            "position_tolerance_m": tolerance,
            "evidence_refs": list(dict.fromkeys([
                *snapshot.evidence_refs,
                f"sim_pose_{entity_id}",
                f"sim_pose_{anchor_id}",
            ])),
        })
        if distance > tolerance + 1e-9:
            evidence["reason_code"] = "TARGET_POSITION_MISSED"
            return False, evidence

        evidence["reason_code"] = None
        evidence["verified"] = True
        return True, evidence

    def region(self, region_ref: str) -> Mapping[str, Any] | None:
        snapshot = self.scene_snapshot()
        regions = self._region_index(snapshot)
        resolved = _resolve_region_ref(region_ref, regions)
        return copy.deepcopy(regions.get(resolved)) if resolved else None

    @property
    def world_revision(self) -> int:
        return int(getattr(self.runtime.world, "revision", 0) or 0)

    def footprint_radius(self, entity_id: str) -> float | None:
        perception = self.runtime.perception
        metadata = _catalog_entry(perception, entity_id)
        geometry = metadata.get("geometry") or {}
        value = geometry.get("footprint_radius")
        if value is None and geometry.get("type") in {"sphere", "cylinder"}:
            value = geometry.get("radius")
        size = geometry.get("size")
        if (
            value is None
            and geometry.get("type") == "box"
            and isinstance(size, (list, tuple))
            and len(size) >= 2
        ):
            value = max(float(size[0]), float(size[1])) / 2.0
        if value is None:
            reader = getattr(perception, "entity_horizontal_radius", None)
            if callable(reader):
                try:
                    value = reader(str(entity_id))
                except Exception:
                    value = None
        try:
            radius = float(value)
        except (TypeError, ValueError):
            return None
        return radius if isfinite(radius) and radius > 0.0 else None

    def placement_operation_envelope(
        self,
        entity_id: str,
        *,
        relation: str,
        destination_id: str,
        region_ref: str,
    ) -> _PlacementOperationEnvelope | None:
        if getattr(self.runtime, "perception", None) is None:
            return None
        physical_radius = self.footprint_radius(entity_id)
        if physical_radius is None:
            return None
        try:
            module = import_harness_module(
                "er2sim.placement_capabilities",
                harness_root=self.harness_root,
            )
        except Exception:
            return None
        reader = getattr(module, "placement_operation_envelope", None)
        if not callable(reader):
            return None
        scene = getattr(self.runtime, "scene", None)
        perception = getattr(self.runtime, "perception", None)
        if scene is None or perception is None:
            return None
        try:
            envelope = reader(
                scene,
                perception,
                str(entity_id),
                relation=str(relation),
                destination_id=str(destination_id),
                region_ref=str(region_ref),
            )
        except Exception:
            return None
        value = (
            envelope.get("radius_m")
            if isinstance(envelope, Mapping)
            else envelope
        )
        model_ref = (
            str(envelope.get("model_ref") or "")
            if isinstance(envelope, Mapping)
            else ""
        )
        try:
            phases = (
                _operation_phases(envelope.get("phases"))
                if isinstance(envelope, Mapping)
                else None
            )
        except ValueError:
            return None
        try:
            radius = float(value)
        except (TypeError, ValueError):
            return None
        if not isfinite(radius) or radius <= 0.0 or not model_ref:
            return None
        return _PlacementOperationEnvelope(
            radius=max(physical_radius, radius),
            model_ref=model_ref,
            phases=phases,
        )

    def _target_operation_envelope(
        self,
        *,
        target: Mapping[str, Any],
        artifact: Mapping[str, Any],
        entity_id: str,
        relation: str,
        destination_id: str,
        region_ref: str,
    ) -> _PlacementOperationEnvelope | None:
        radius = _target_placement_operation_envelope_radius(
            target,
            artifact,
            entity_id,
        )
        if radius is not None:
            model_ref = _target_placement_operation_envelope_model_ref(
                target,
                artifact,
                entity_id,
            )
            phases = _target_placement_operation_envelope_phases(
                target,
                artifact,
                entity_id,
            )
            if phases is None:
                current = self.placement_operation_envelope(
                    entity_id,
                    relation=relation,
                    destination_id=destination_id,
                    region_ref=region_ref,
                )
                current_phases = (
                    getattr(current, "phases", None)
                    if current is not None
                    else None
                )
                if current is not None and current_phases is not None:
                    return _PlacementOperationEnvelope(
                        radius=max(radius, current.radius),
                        model_ref=model_ref or current.model_ref,
                        phases=current_phases,
                    )
            return _PlacementOperationEnvelope(
                radius=radius,
                model_ref=(
                    model_ref
                    or "legacy_placement_operation_envelope/1.0"
                ),
                phases=phases,
            )
        return self.placement_operation_envelope(
            entity_id,
            relation=relation,
            destination_id=destination_id,
            region_ref=region_ref,
        )

    def _definition(
        self,
        region_ref: str,
        capability: Mapping[str, Any],
    ) -> RegionDefinition:
        owner_id = str(capability.get("owner_ref") or "")
        selector = str(capability.get("selector") or "support")
        metadata = _catalog_entry(self.runtime.perception, owner_id)
        category = str(metadata.get("category") or "")
        geometry = metadata.get("geometry") or {}
        placement_model = capability.get("placement_model")
        if not isinstance(placement_model, Mapping):
            reader = getattr(
                self.runtime.perception,
                "placement_region_spec",
                None,
            )
            if callable(reader):
                try:
                    placement_model = reader(owner_id, selector)
                except Exception:
                    placement_model = None
        if not isinstance(placement_model, Mapping):
            placement_model = {}
        if category == "floor":
            shape = RegionGeometry(
                RegionShape.RECTANGLE,
                width=FLOOR_REGION_SIZE_M,
                depth=FLOOR_REGION_SIZE_M,
            )
        else:
            shape = _placement_region_geometry(
                owner_id,
                placement_model,
                geometry,
            )
        clearance = _positive_float(
            placement_model.get(
                "object_clearance",
                geometry.get("placement_clearance"),
            ),
            0.018,
        )
        edge_margin = _positive_float(
            placement_model.get(
                "edge_margin",
                geometry.get(
                    "placement_edge_margin",
                    geometry.get("edge_margin"),
                ),
            ),
            clearance,
        )
        placement_slots, slots_from_legacy = _placement_slots(
            placement_model,
            geometry,
        )
        strict_slots = (
            str(placement_model.get("slot_policy") or "").casefold()
            == "strict"
            or bool(placement_model.get("placement_slots_strict", False))
            or bool(geometry.get("placement_slots_strict", False))
            or (
                slots_from_legacy
                and str(geometry.get("type") or "").casefold() == "box"
            )
        )
        return RegionDefinition(
            region_ref=region_ref,
            owner_id=owner_id,
            selector=selector,
            geometry=shape,
            allowed_relations=tuple(
                str(value)
                for value in capability.get("allowed_relations", ()) or ()
            ),
            edge_margin=edge_margin,
            clearance=clearance,
            exclusions=_placement_exclusions(
                placement_model,
                geometry,
                default_clearance=clearance,
            ),
            placement_slots=placement_slots,
            placement_slots_strict=strict_slots,
        )

    def _world_xy_to_local(
        self,
        owner_id: str,
        world_xy: tuple[float, float],
    ) -> tuple[float, float] | None:
        reader = getattr(
            self.runtime.perception,
            "world_xy_to_local",
            None,
        )
        if callable(reader):
            try:
                value = reader(owner_id, world_xy)
            except Exception:
                value = None
            if value is not None:
                return float(value[0]), float(value[1])
        if _is_floor(self.runtime.perception, owner_id):
            return float(world_xy[0]), float(world_xy[1])
        return None

    def _physical_placement_layout_filter(
        self,
        *,
        destination_id: str,
        relation: str,
        batch_entity_ids: frozenset[str],
    ) -> Callable[[tuple[LayoutPlacement, ...]], bool] | None:
        runtime = self.runtime
        perception = getattr(runtime, "perception", None)
        required_readers = (
            getattr(perception, "local_xy_to_world", None),
            getattr(perception, "support_surface_z", None),
            getattr(perception, "entity_bottom_offset", None),
        )
        if (
            getattr(runtime, "scene", None) is None
            or getattr(runtime, "world", None) is None
            or not all(callable(reader) for reader in required_readers)
        ):
            return None

        simulator = import_harness_module(
            "er2sim.placement_simulator",
            harness_root=self.harness_root,
        )
        assess_placement = getattr(simulator, "assess_placement", None)
        if not callable(assess_placement):
            raise RuntimeError(
                "Harness placement simulator has no assess_placement"
            )
        robot_id = str(
            getattr(simulator, "ROBOT_ENTITY_ID", "robot_1")
        )
        allowed_occupants = {
            *batch_entity_ids,
            str(destination_id),
            robot_id,
        }

        def allows(
            placements: tuple[LayoutPlacement, ...],
        ) -> bool:
            for placement in placements:
                try:
                    world_xy = perception.local_xy_to_world(
                        destination_id,
                        (float(placement.x), float(placement.y)),
                    )
                    support_z = perception.support_surface_z(destination_id)
                    bottom_offset = perception.entity_bottom_offset(
                        placement.entity_id
                    )
                    if world_xy is None or support_z is None:
                        return False
                    candidate_point = (
                        float(world_xy[0]),
                        float(world_xy[1]),
                        float(support_z) + float(bottom_offset),
                    )
                    if not all(isfinite(value) for value in candidate_point):
                        return False
                    assessment = assess_placement(
                        runtime,
                        placement.entity_id,
                        destination_id,
                        relation=relation,
                        require_reachable=False,
                        simulation_mode="conservative",
                        candidate_point=candidate_point,
                    )
                    serializer = getattr(assessment, "to_dict", None)
                    if callable(serializer):
                        data = serializer()
                    elif isinstance(assessment, Mapping):
                        data = dict(assessment)
                    else:
                        data = {
                            key: getattr(assessment, key, None)
                            for key in (
                                "support_surface_valid",
                                "capacity_sufficient",
                                "footprint_fits",
                                "release_safe",
                                "retreat_safe",
                                "occupant_ids",
                            )
                        }
                    if not isinstance(data, Mapping):
                        return False
                    required_true = (
                        "support_surface_valid",
                        "capacity_sufficient",
                        "footprint_fits",
                        "release_safe",
                        "retreat_safe",
                    )
                    if any(
                        str(data.get(key) or "").casefold() != "true"
                        for key in required_true
                    ):
                        return False
                    external_occupants = {
                        str(value)
                        for value in data.get("occupant_ids", ()) or ()
                        if str(value) not in allowed_occupants
                    }
                    if external_occupants:
                        return False
                except Exception:
                    return False
            return True

        return allows

    def _relocation_layout_constraints(
        self,
        owner_id: str,
        params: Mapping[str, Any],
    ) -> _RelocationLayoutConstraints | None:
        if not any(
            params.get(key) is not None
            for key in RELOCATION_LAYOUT_CONSTRAINT_KEYS
        ):
            return None

        baseline_raw = params.get("baseline_pose")
        baseline_world = _xy_value(baseline_raw)
        baseline_local = (
            self._world_xy_to_local(owner_id, baseline_world)
            if baseline_world is not None
            else None
        )
        minimum_distance = _nonnegative_float(
            params.get("minimum_relocation_distance"),
            default=0.0,
            field_name="minimum_relocation_distance",
        )
        if minimum_distance > 0.0 and baseline_local is None:
            raise ValueError(
                "minimum relocation distance requires a transformable "
                "baseline_pose"
            )

        path_segments: list[
            tuple[tuple[float, float], tuple[float, float]]
        ] = []
        raw_segments = params.get("path_segments")
        if raw_segments is not None:
            if not isinstance(raw_segments, (list, tuple)):
                raise ValueError("path_segments must be a sequence")
            for raw_segment in raw_segments:
                if (
                    not isinstance(raw_segment, (list, tuple))
                    or len(raw_segment) != 2
                ):
                    raise ValueError(
                        "path_segments require start/end point pairs"
                    )
                start_world = _xy_value(raw_segment[0])
                end_world = _xy_value(raw_segment[1])
                if start_world is None or end_world is None:
                    raise ValueError(
                        "path_segments contain an invalid world point"
                    )
                start_local = self._world_xy_to_local(
                    owner_id,
                    start_world,
                )
                end_local = self._world_xy_to_local(
                    owner_id,
                    end_world,
                )
                if start_local is None or end_local is None:
                    raise ValueError(
                        "path_segments cannot be transformed into the "
                        "placement region frame"
                    )
                path_segments.append((start_local, end_local))
        if path_segments and baseline_local is None:
            raise ValueError(
                "path clearance constraints require a transformable "
                "baseline_pose"
            )

        object_radius = _optional_nonnegative_float(
            params.get("object_radius"),
            field_name="object_radius",
        )
        required_clearance = _nonnegative_float(
            params.get("required_clearance"),
            default=0.0,
            field_name="required_clearance",
        )
        minimum_improvement = _nonnegative_float(
            params.get("minimum_improvement"),
            default=0.01,
            field_name="minimum_improvement",
        )
        return _RelocationLayoutConstraints(
            baseline_xy=baseline_local,
            minimum_relocation_distance=minimum_distance,
            path_segments=tuple(path_segments),
            object_radius=object_radius,
            required_clearance=required_clearance,
            minimum_improvement=minimum_improvement,
        )

    def _reservations(
        self,
        owner_id: str,
    ) -> tuple[SpaceReservation, ...]:
        task = _current_task(self.runtime)
        artifacts = getattr(task, "artifacts", {}) if task is not None else {}
        if not isinstance(artifacts, Mapping):
            return ()
        reservations: list[SpaceReservation] = []
        for artifact_ref, artifact in artifacts.items():
            if (
                not isinstance(artifact, Mapping)
                or artifact.get("invalidated")
                or str(artifact.get("anchor_id") or "") != owner_id
                or str(artifact.get("kind") or "") != "layout_targets"
                or str(artifact.get("source") or "")
                != "task_recursive_tree_batch_allocator/1.0"
                or not artifact.get("reservation_group")
                or artifact.get("reservation_status") is None
                or _reservation_status(
                    artifact.get("reservation_status")
                )
                != ReservationStatus.ACTIVE.value
            ):
                continue
            planned_revision = _integer_or_none(
                artifact.get("world_revision")
            )
            if (
                planned_revision is not None
                and planned_revision > self.world_revision
            ):
                continue
            group = str(artifact.get("reservation_group") or "")
            for entity_id, target in (
                artifact.get("targets", {}) or {}
            ).items():
                if not isinstance(target, Mapping):
                    continue
                status = _reservation_status(
                    target.get("reservation_status")
                )
                if status != ReservationStatus.ACTIVE.value:
                    continue
                local = target.get("local_xy")
                radius = _target_radius(
                    target,
                    artifact,
                    str(entity_id),
                )
                if (
                    not isinstance(local, (list, tuple))
                    or len(local) != 2
                    or radius is None
                ):
                    continue
                operation_envelope = self._target_operation_envelope(
                    target=target,
                    artifact=artifact,
                    entity_id=str(entity_id),
                    relation=str(
                        artifact.get("relation") or "on_support"
                    ),
                    destination_id=owner_id,
                    region_ref=str(
                        artifact.get("region_ref")
                        or f"{owner_id}/support"
                    ),
                )
                if operation_envelope is None:
                    raise ValueError(
                        "Active legacy layout target "
                        f"{artifact_ref!r}/{entity_id!s} has no stored "
                        "operation envelope and the current Harness model "
                        "cannot reproduce one"
                    )
                reservations.append(
                    SpaceReservation(
                        reservation_id=(
                            str(target.get("reservation_id"))
                            if target.get("reservation_id")
                            else f"{group}:{entity_id}"
                        ),
                        entity_id=str(entity_id),
                        x=float(local[0]),
                        y=float(local[1]),
                        radius=float(radius),
                        world_revision=(
                            planned_revision
                            if planned_revision is not None
                            else self.world_revision
                        ),
                        reservation_group=group or None,
                        artifact_ref=str(artifact_ref),
                        status=ReservationStatus.ACTIVE,
                        placement_operation_envelope_radius=(
                            max(
                                float(radius),
                                float(operation_envelope.radius),
                            )
                        ),
                        placement_operation_envelope_model_ref=(
                            operation_envelope.model_ref
                        ),
                        placement_operation_envelope_phases=(
                            operation_envelope.phases
                        ),
                    )
                )
        return tuple(
            {
                reservation.reservation_id: reservation
                for reservation in reservations
            }.values()
        )

    def _region_index(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        module = import_harness_module(
            "er2sim.scene_capabilities",
            harness_root=self.harness_root,
        )
        return module.region_index(dict(snapshot))


def install_floor_region_frame(perception: Any) -> None:
    """Give geometry-less floor regions an explicit world-aligned 2D frame."""

    if perception is None or getattr(
        perception,
        "_task_recursive_tree_floor_frame",
        False,
    ):
        return
    local_to_world = getattr(perception, "local_xy_to_world", None)
    world_to_local = getattr(perception, "world_xy_to_local", None)
    if not callable(local_to_world) or not callable(world_to_local):
        return

    def wrapped_local_to_world(
        entity_id: str,
        local_xy: tuple[float, float],
    ) -> tuple[float, float] | None:
        value = local_to_world(entity_id, local_xy)
        if value is None and _is_floor(perception, entity_id):
            return float(local_xy[0]), float(local_xy[1])
        return value

    def wrapped_world_to_local(
        entity_id: str,
        world_xy: tuple[float, float],
    ) -> tuple[float, float] | None:
        value = world_to_local(entity_id, world_xy)
        if value is None and _is_floor(perception, entity_id):
            return float(world_xy[0]), float(world_xy[1])
        return value

    setattr(perception, "local_xy_to_world", wrapped_local_to_world)
    setattr(perception, "world_xy_to_local", wrapped_world_to_local)
    setattr(perception, "_task_recursive_tree_floor_frame", True)


def layout_targets_artifact(
    *,
    target_ref: str,
    selection: RegionLayoutSelection,
    subject_ids: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    if (
        selection.plan is None
        or selection.snapshot is None
        or selection.region is None
    ):
        raise ValueError("A feasible region selection is required")
    plan = selection.plan
    owner_id = selection.snapshot.definition.owner_id
    relation = str(
        selection.relation
        or (
            selection.snapshot.definition.allowed_relations[0]
            if selection.snapshot.definition.allowed_relations
            else "on_support"
        )
    )
    placements = {
        placement.entity_id: placement
        for placement in plan.placements
    }
    ordered_ids = tuple(dict.fromkeys(str(value) for value in subject_ids))
    return {
        "kind": "layout_targets",
        "artifact_kind": "layout_targets",
        "artifact_ref": str(target_ref),
        "source": "task_recursive_tree_batch_allocator/1.0",
        "region_ref": plan.region_ref,
        "anchor_id": owner_id,
        "relation": relation,
        "subject_ids": list(ordered_ids),
        "world_revision": plan.world_revision,
        "state_fingerprint": plan.state_fingerprint,
        "reservation_group": plan.reservation_group,
        "robust_clearance_margin_m": plan.robust_clearance_margin,
        "reservation_status": ReservationStatus.ACTIVE.value,
        "plan_id": plan.plan_id,
        "layout_model_version": (
            PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION
        ),
        "targets": {
            entity_id: {
                "local_xy": [
                    float(placements[entity_id].x),
                    float(placements[entity_id].y),
                ],
                "radius_m": float(placements[entity_id].radius),
                "placement_operation_envelope_radius_m": float(
                    placements[
                        entity_id
                    ].effective_operation_radius
                ),
                "placement_operation_envelope_model_ref": (
                    placements[
                        entity_id
                    ].placement_operation_envelope_model_ref
                ),
                "placement_operation_envelope_phases": (
                    sorted(
                        placements[
                            entity_id
                        ].placement_operation_envelope_phases
                    )
                    if placements[
                        entity_id
                    ].placement_operation_envelope_phases is not None
                    else None
                ),
                "reservation_id": (
                    f"{plan.reservation_group}:{entity_id}"
                ),
                "reservation_status": ReservationStatus.ACTIVE.value,
            }
            for entity_id in ordered_ids
            if entity_id in placements
        },
        "candidate_audit": [
            copy.deepcopy(dict(value))
            for value in selection.candidate_audit
        ],
    }


def _aggregate_failure(
    refs: tuple[str, ...],
    failures: list[LayoutFailure],
    audit: list[dict[str, Any]],
) -> LayoutFailure:
    if not refs:
        return LayoutFailure(
            verdict=LayoutVerdict.INFEASIBLE_PROVEN,
            code="NO_STAGING_REGION",
            message="No trusted staging region is declared",
            proven=True,
            details={"candidate_refs": [], "candidate_audit": audit},
        )
    if any(
        failure.verdict is LayoutVerdict.SEARCH_EXHAUSTED
        for failure in failures
    ):
        verdict = LayoutVerdict.SEARCH_EXHAUSTED
        search_codes = {failure.code for failure in failures}
        if search_codes == {"CANDIDATE_SPACE_EXHAUSTED"}:
            code = "CANDIDATE_SPACE_EXHAUSTED"
            message = (
                "Staging layout exhausted its deterministic candidate model"
            )
        else:
            code = "SEARCH_BUDGET_EXHAUSTED"
            message = (
                "Staging layout search exhausted its deterministic budget"
            )
        proven = False
    elif failures and all(
        failure.verdict is LayoutVerdict.PERCEPTION_INSUFFICIENT
        for failure in failures
    ):
        verdict = LayoutVerdict.PERCEPTION_INSUFFICIENT
        code = "PERCEPTION_INSUFFICIENT"
        message = "Staging regions lack complete geometry or occupancy evidence"
        proven = False
    else:
        verdict = LayoutVerdict.INFEASIBLE_PROVEN
        code = "NO_FEASIBLE_LAYOUT"
        message = "No trusted staging region can hold the complete batch"
        proven = True
    return LayoutFailure(
        verdict=verdict,
        code=code,
        message=message,
        proven=proven,
        details={
            "candidate_refs": list(refs),
            "candidate_audit": copy.deepcopy(audit),
        },
    )


def _resolve_region_ref(
    raw_ref: str,
    regions: Mapping[str, Mapping[str, Any]],
) -> str | None:
    candidates = [raw_ref]
    if raw_ref.startswith("region://"):
        candidates.append(raw_ref[len("region://") :])
    else:
        candidates.append(f"region://{raw_ref}")
    for candidate in candidates:
        if candidate in regions:
            return candidate
    return next(
        (
            str(ref)
            for ref, region in regions.items()
            if str(region.get("owner_ref")) == raw_ref
        ),
        None,
    )


def _xy_value(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        point = (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None
    return point if all(isfinite(item) for item in point) else None


def _optional_nonnegative_float(
    value: Any,
    *,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    return _nonnegative_float(
        value,
        default=0.0,
        field_name=field_name,
    )


def _nonnegative_float(
    value: Any,
    *,
    default: float,
    field_name: str,
) -> float:
    if value is None:
        return float(default)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not isfinite(result) or result < 0.0:
        raise ValueError(f"{field_name} must be finite and nonnegative")
    return result


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return hypot(point[0] - start[0], point[1] - start[1])
    projection = (
        (point[0] - start[0]) * dx
        + (point[1] - start[1]) * dy
    ) / length_sq
    ratio = max(0.0, min(1.0, projection))
    nearest = (
        start[0] + ratio * dx,
        start[1] + ratio * dy,
    )
    return hypot(point[0] - nearest[0], point[1] - nearest[1])


def _placement_region_geometry(
    owner_id: str,
    placement_model: Mapping[str, Any],
    legacy_geometry: Mapping[str, Any],
) -> RegionGeometry:
    boundary = placement_model.get("boundary")
    if isinstance(boundary, Mapping):
        center = boundary.get("center_xy", (0.0, 0.0))
        if not isinstance(center, (list, tuple)) or len(center) < 2:
            raise ValueError(
                f"Region owner {owner_id!r} has a malformed boundary center"
            )
        center_xy = (float(center[0]), float(center[1]))
        if (
            not all(isfinite(value) for value in center_xy)
            or hypot(*center_xy) > 1e-9
        ):
            raise ValueError(
                f"Region owner {owner_id!r} uses an unsupported "
                "off-center boundary"
            )
        shape = str(boundary.get("shape") or "").casefold()
        if shape in {"rectangle", "box"}:
            half_extents = boundary.get("half_extents_xy")
            size = boundary.get("size")
            if isinstance(half_extents, (list, tuple)) and len(
                half_extents
            ) >= 2:
                width = 2.0 * float(half_extents[0])
                depth = 2.0 * float(half_extents[1])
            elif isinstance(size, (list, tuple)) and len(size) >= 2:
                width = float(size[0])
                depth = float(size[1])
            else:
                width = float(boundary["width"])
                depth = float(boundary["depth"])
            return RegionGeometry(
                RegionShape.RECTANGLE,
                width=width,
                depth=depth,
            )
        if shape == "circle":
            return RegionGeometry(
                RegionShape.CIRCLE,
                radius=float(boundary["radius"]),
            )
        raise ValueError(
            f"Region owner {owner_id!r} has unsupported boundary shape "
            f"{shape!r}"
        )

    interior_size = legacy_geometry.get("interior_size")
    if isinstance(interior_size, (list, tuple)) and len(interior_size) >= 2:
        return RegionGeometry(
            RegionShape.RECTANGLE,
            width=float(interior_size[0]),
            depth=float(interior_size[1]),
        )
    if (
        str(legacy_geometry.get("type") or "").casefold() == "box"
        and legacy_geometry.get("inner_radius") is not None
    ):
        interior_half_extent = float(legacy_geometry["inner_radius"])
        return RegionGeometry(
            RegionShape.RECTANGLE,
            width=2.0 * interior_half_extent,
            depth=2.0 * interior_half_extent,
        )
    if legacy_geometry.get("inner_radius") is not None:
        return RegionGeometry(
            RegionShape.CIRCLE,
            radius=float(legacy_geometry["inner_radius"]),
        )
    size = legacy_geometry.get("size")
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        return RegionGeometry(
            RegionShape.RECTANGLE,
            width=float(size[0]),
            depth=float(size[1]),
        )
    if legacy_geometry.get("radius") is not None:
        return RegionGeometry(
            RegionShape.CIRCLE,
            radius=float(legacy_geometry["radius"]),
        )
    raise ValueError(f"Region owner {owner_id!r} has no usable geometry")


def _placement_slots(
    placement_model: Mapping[str, Any],
    legacy_geometry: Mapping[str, Any],
) -> tuple[tuple[tuple[float, float], ...], bool]:
    raw_slots = placement_model.get("slots")
    legacy = False
    if raw_slots is None:
        raw_slots = legacy_geometry.get("placement_slots")
        legacy = raw_slots is not None
    if raw_slots is None:
        raw_slots = legacy_geometry.get("place_offsets")
        legacy = raw_slots is not None
    if raw_slots is None:
        return (), False
    if not isinstance(raw_slots, (list, tuple)):
        raise ValueError("placement slots must be a sequence")

    slots: list[tuple[float, float]] = []
    for raw in raw_slots:
        value = raw.get("center_xy") if isinstance(raw, Mapping) else raw
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            raise ValueError("placement slot is missing center_xy")
        point = (float(value[0]), float(value[1]))
        if not all(isfinite(item) for item in point):
            raise ValueError("placement slot coordinates must be finite")
        if point not in slots:
            slots.append(point)
    return tuple(slots), legacy


def _placement_exclusions(
    placement_model: Mapping[str, Any],
    legacy_geometry: Mapping[str, Any],
    *,
    default_clearance: float,
) -> tuple[RegionExclusion, ...]:
    raw_exclusions = placement_model.get("exclusions")
    if raw_exclusions is None:
        raw_exclusions = legacy_geometry.get("placement_exclusions")
    if raw_exclusions is None:
        return ()
    if not isinstance(raw_exclusions, (list, tuple)):
        raise ValueError("placement exclusions must be a sequence")

    result: list[RegionExclusion] = []
    for index, raw in enumerate(raw_exclusions):
        if not isinstance(raw, Mapping):
            raise ValueError("placement exclusion must be an object")
        exclusion_ref = str(
            raw.get("exclusion_id")
            or raw.get("exclusion_ref")
            or f"exclusion_{index}"
        )
        center = raw.get("center", raw.get("local_xy", (0.0, 0.0)))
        if not isinstance(center, (list, tuple)) or len(center) < 2:
            raise ValueError(
                f"placement exclusion {exclusion_ref!r} has no center"
            )
        x, y = float(center[0]), float(center[1])
        shape = str(raw.get("shape") or "").casefold()
        if shape in {"rectangle", "box"}:
            half_extents = raw.get(
                "half_extents_xy",
                raw.get("half_extents"),
            )
            size = raw.get("size")
            if isinstance(half_extents, (list, tuple)) and len(
                half_extents
            ) >= 2:
                width = 2.0 * float(half_extents[0])
                depth = 2.0 * float(half_extents[1])
            elif isinstance(size, (list, tuple)) and len(size) >= 2:
                width = float(size[0])
                depth = float(size[1])
            else:
                width = float(raw["width"])
                depth = float(raw["depth"])
            exclusion_geometry = RegionGeometry(
                RegionShape.RECTANGLE,
                width=width,
                depth=depth,
            )
        elif shape == "circle":
            exclusion_geometry = RegionGeometry(
                RegionShape.CIRCLE,
                radius=float(raw["radius"]),
            )
        else:
            raise ValueError(
                f"placement exclusion {exclusion_ref!r} has unsupported "
                f"shape {shape!r}"
            )
        result.append(
            RegionExclusion(
                exclusion_ref=exclusion_ref,
                geometry=exclusion_geometry,
                x=x,
                y=y,
                clearance=_positive_float(
                    raw.get("clearance"),
                    default_clearance,
                ),
                source_entity_ref=str(
                    raw.get("source_geom")
                    or raw.get("source_entity_ref")
                    or exclusion_ref
                ),
                blocked_phases=_operation_phases(
                    raw.get("blocked_phases")
                ),
            )
        )
    return tuple(result)


def _catalog_entry(perception: Any, entity_id: str) -> dict[str, Any]:
    catalog = getattr(perception, "catalog", {})
    value = catalog.get(str(entity_id), {}) if isinstance(
        catalog,
        Mapping,
    ) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _is_floor(perception: Any, entity_id: str) -> bool:
    metadata = _catalog_entry(perception, str(entity_id))
    return str(metadata.get("category") or "").casefold() == "floor"


def _layout_robust_clearance_margin(
    perception: Any,
    owner_id: str,
    *,
    placement_policy: str | None,
    explicit_margin: Any | None,
) -> float:
    if explicit_margin is not None:
        try:
            margin = float(explicit_margin)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "robust_clearance_margin_m must be finite and non-negative"
            ) from exc
        if not isfinite(margin) or margin < 0.0:
            raise ValueError(
                "robust_clearance_margin_m must be finite and non-negative"
            )
        return margin
    if (
        str(placement_policy or "").strip().casefold()
        == "clear_of_workspace"
        and _is_floor(perception, owner_id)
    ):
        return _CLEAR_OF_WORKSPACE_FLOOR_ROBUST_CLEARANCE_MARGIN_M
    return 0.0


def _entity_pose(
    perception: Any,
    entity_id: str,
) -> tuple[float, float, float] | None:
    reader = getattr(perception, "entity_pose", None)
    if not callable(reader):
        return None
    try:
        value = reader(str(entity_id))
    except Exception:
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        pose = tuple(float(value[index]) for index in range(3))
    except (TypeError, ValueError):
        return None
    return pose if all(isfinite(item) for item in pose) else None


def _current_task(runtime: Any) -> Any | None:
    world = getattr(runtime, "world", None)
    tasks = getattr(world, "tasks", {}) if world is not None else {}
    if not isinstance(tasks, Mapping):
        return None
    task_id = getattr(runtime, "_current_task_id", None)
    if task_id is None:
        resolver = getattr(runtime, "_task_id", None)
        if callable(resolver):
            try:
                task_id = resolver(required=False)
            except Exception:
                task_id = None
    if task_id is not None and str(task_id) in tasks:
        return tasks[str(task_id)]
    return next(iter(tasks.values())) if len(tasks) == 1 else None


def _first_entity(
    params: Mapping[str, Any],
    role: str,
) -> str | None:
    for key in (f"{role}_id", f"{role}_ids"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
    participants = params.get("participants")
    if isinstance(participants, Mapping):
        value = participants.get(role)
        if isinstance(value, Mapping):
            value = value.get("entity_ids") or value.get("entity_id")
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
    return None


def _target_radius(
    target: Mapping[str, Any],
    artifact: Mapping[str, Any],
    entity_id: str,
) -> float | None:
    value = target.get("radius_m")
    if value is None:
        radii = artifact.get("radii")
        if isinstance(radii, Mapping):
            value = radii.get(str(entity_id))
    try:
        radius = float(value)
    except (TypeError, ValueError):
        return None
    return radius if isfinite(radius) and radius > 0.0 else None


def _target_placement_operation_envelope_radius(
    target: Mapping[str, Any],
    artifact: Mapping[str, Any],
    entity_id: str,
) -> float | None:
    value = target.get("placement_operation_envelope_radius_m")
    if value is None:
        value = target.get("placement_envelope_radius_m")
    if value is None:
        radii = artifact.get("placement_operation_envelope_radii")
        if not isinstance(radii, Mapping):
            radii = artifact.get("placement_envelope_radii")
        if isinstance(radii, Mapping):
            value = radii.get(str(entity_id))
    try:
        radius = float(value)
    except (TypeError, ValueError):
        return None
    return radius if isfinite(radius) and radius > 0.0 else None


def _target_placement_operation_envelope_model_ref(
    target: Mapping[str, Any],
    artifact: Mapping[str, Any],
    entity_id: str,
) -> str | None:
    value = target.get("placement_operation_envelope_model_ref")
    if value is None:
        refs = artifact.get("placement_operation_envelope_model_refs")
        if isinstance(refs, Mapping):
            value = refs.get(str(entity_id))
    if value is None:
        value = artifact.get("layout_model_version")
    text = str(value or "").strip()
    return text or None


def _target_placement_operation_envelope_phases(
    target: Mapping[str, Any],
    artifact: Mapping[str, Any],
    entity_id: str,
) -> frozenset[str] | None:
    value = target.get("placement_operation_envelope_phases")
    if value is None:
        values = artifact.get(
            "placement_operation_envelope_phases_by_entity"
        )
        if isinstance(values, Mapping):
            value = values.get(str(entity_id))
    return _operation_phases(value)


def _operation_phases(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(
        value,
        (list, tuple, set, frozenset),
    ):
        raise ValueError("operation phases must be a sequence")
    normalized = [
        str(item).strip().casefold()
        for item in value
    ]
    if any(not phase for phase in normalized):
        raise ValueError("operation phases must not contain empty values")
    return frozenset(normalized)


def _combined_operation_phases(
    items: Iterable[LayoutItem],
) -> frozenset[str] | None:
    phase_sets = [
        item.placement_operation_envelope_phases
        for item in items
    ]
    if any(phases is None for phases in phase_sets):
        return None
    return frozenset().union(
        *(phases for phases in phase_sets if phases is not None)
    )


def _required_placement_separation(
    first_radius: float,
    first_placement_radius: float,
    second_radius: float,
    second_placement_radius: float,
    clearance: float,
) -> float:
    return max(
        first_placement_radius + second_radius,
        first_radius + second_placement_radius,
    ) + max(0.0, float(clearance))


def _reservation_status(value: Any) -> str:
    text = str(
        value.value if isinstance(value, ReservationStatus) else value or ""
    ).casefold()
    return text or ReservationStatus.ACTIVE.value


def _finite_local_xy_values(
    values: Any,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[tuple[float, float]] = []
    for raw in values:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            point = (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            continue
        if all(isfinite(value) for value in point):
            result.append(point)
    return tuple(dict.fromkeys(result))


def _integer_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_float(value: Any, fallback: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return result if isfinite(result) and result >= 0.0 else float(fallback)


__all__ = [
    "FLOOR_REGION_SIZE_M",
    "HarnessRegionSpaceAdapter",
    "PLACEMENT_ENVELOPE_MODEL_VERSION",
    "PLACEMENT_OPERATION_ENVELOPE_MODEL_VERSION",
    "RegionLayoutSelection",
    "install_floor_region_frame",
    "layout_targets_artifact",
]
