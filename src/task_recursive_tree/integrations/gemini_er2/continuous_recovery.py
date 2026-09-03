from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .paths import import_harness_module


def adapt_continuous_session_class(
    base_session_type: type,
    *,
    harness_root: str | Path | None = None,
    capability_module: Any = None,
) -> type:
    """Add Task Recursive Tree recovery policy to a Harness session class."""

    if not callable(
        getattr(
            base_session_type,
            "_apply_reconciled_staging_constraints",
            None,
        )
    ):
        raise TypeError(
            "ContinuousTaskSession must expose "
            "_apply_reconciled_staging_constraints"
        )
    capabilities = capability_module or import_harness_module(
        "er2sim.scene_capabilities",
        harness_root=harness_root,
    )

    class TaskRecursiveTreeContinuousSession(base_session_type):
        def _apply_reconciled_staging_constraints(
            self,
            active: dict[str, Any],
            gripper: Any,
        ) -> None:
            source_refinement = (
                _refine_finalize_primary_staging_source(
                    self,
                    active,
                    gripper,
                )
            )
            super()._apply_reconciled_staging_constraints(active, gripper)
            _allow_failed_original_floor_staging(
                active,
                gripper,
                perception=getattr(self, "perception", None),
                capabilities=capabilities,
            )
            if source_refinement is not None:
                decision = active.get("staging_avoidance_decision")
                if not isinstance(decision, dict):
                    decision = {}
                    active["staging_avoidance_decision"] = decision
                decision["source_refinement"] = copy.deepcopy(
                    source_refinement
                )

    TaskRecursiveTreeContinuousSession.__name__ = (
        "TaskRecursiveTreeContinuousSession"
    )
    TaskRecursiveTreeContinuousSession.__qualname__ = (
        "TaskRecursiveTreeContinuousSession"
    )
    TaskRecursiveTreeContinuousSession.__module__ = __name__
    return TaskRecursiveTreeContinuousSession


def _refine_finalize_primary_staging_source(
    session: Any,
    active: dict[str, Any],
    gripper: Any,
) -> dict[str, Any] | None:
    if str(active.get("intent") or "") != "finalize_primary":
        return None

    original_source = _mapping(active.get("staging_avoidance_source"))
    held_entity_id = _optional_text(
        getattr(gripper, "held_entity_id", None)
    )
    audit: dict[str, Any] = {
        "status": "unchanged",
        "changed": False,
        "held_entity_id": held_entity_id,
        "original_source": copy.deepcopy(original_source),
        "final_source": copy.deepcopy(original_source),
        "original_avoid_owner_ids": _unique_text(
            active.get("avoid_owner_ids")
        ),
        "original_failure_protected_owner_ids": _unique_text(
            active.get("failure_protected_owner_ids")
        ),
        "candidate_records": [],
        "rejection_reasons": [],
    }
    active["staging_source_refinement"] = audit

    eligibility_reasons: list[str] = []
    _require(
        eligibility_reasons,
        str(getattr(gripper, "occupancy", "")).casefold()
        == "holding",
        "gripper_not_holding",
    )
    _require(
        eligibility_reasons,
        str(
            getattr(gripper, "attachment_state", "")
        ).casefold() == "secure",
        "held_object_not_secure",
    )
    _require(
        eligibility_reasons,
        held_entity_id is not None,
        "held_object_unknown",
    )
    if eligibility_reasons:
        audit["status"] = "ineligible"
        audit["rejection_reasons"] = eligibility_reasons
        return audit

    if (
        str(original_source.get("source_kind") or "") == "failure"
        and str(
            original_source.get("task_type") or ""
        ).casefold() == "place"
        and _unique_text(original_source.get("object_ids"))
        == [held_entity_id]
        and len(
            _unique_text(original_source.get("destination_ids"))
        ) == 1
        and original_source.get("has_failure_diagnostic") is True
    ):
        audit["status"] = "already_matches_held_object"
        return audit

    executor = getattr(session, "_executor", None)
    tree = getattr(executor, "tree", None)
    nodes = getattr(tree, "nodes", None)
    if tree is None or not isinstance(nodes, Mapping):
        audit["status"] = "tree_unavailable"
        audit["rejection_reasons"] = ["primary_tree_unavailable"]
        return audit

    candidates: list[dict[str, Any]] = []
    for node in nodes.values():
        spec = getattr(node, "spec", None)
        if (
            str(getattr(spec, "task_type", "")).casefold()
            != "place"
        ):
            continue
        object_ids = _session_node_object_ids(session, node)
        if object_ids != [held_entity_id]:
            continue

        evidence_node_ids = _failure_branch_evidence_node_ids(
            session,
            tree,
            node,
        )
        if not evidence_node_ids:
            continue

        destination_ids = _session_node_destination_ids(
            session,
            node,
        )
        node_id = str(
            getattr(node, "node_id", None)
            or getattr(spec, "node_id", "")
        )
        depth = _session_node_depth(session, tree, node_id)
        candidate_reasons: list[str] = []
        _require(
            candidate_reasons,
            len(destination_ids) == 1,
            "destination_not_singular",
        )
        _require(
            candidate_reasons,
            depth is not None,
            "tree_depth_unavailable",
        )
        candidate_record = {
            "source_node_id": node_id,
            "tree_depth": depth,
            "object_ids": object_ids,
            "destination_ids": destination_ids,
            "failure_evidence_node_ids": evidence_node_ids,
            "eligible": not candidate_reasons,
            "rejection_reasons": candidate_reasons,
        }
        audit["candidate_records"].append(candidate_record)
        if candidate_reasons:
            continue
        runtime = getattr(node, "runtime", None)
        finished_at = getattr(runtime, "finished_at", None)
        candidates.append({
            "node": node,
            "source_node_id": node_id,
            "tree_depth": int(depth),
            "finished_at": (
                float(finished_at)
                if isinstance(finished_at, (int, float))
                else 0.0
            ),
            "destination_ids": destination_ids,
            "failure_evidence_node_ids": evidence_node_ids,
        })

    if not candidates:
        audit["status"] = "no_matching_source"
        audit["rejection_reasons"] = [
            "no_matching_failed_place_source"
        ]
        return audit

    destination_options = sorted({
        candidate["destination_ids"][0]
        for candidate in candidates
    })
    if len(destination_options) != 1:
        audit["status"] = "ambiguous"
        audit["ambiguous_destination_ids"] = destination_options
        audit["rejection_reasons"] = [
            "ambiguous_failed_place_destinations"
        ]
        return audit

    selected = max(
        candidates,
        key=lambda candidate: (
            candidate["tree_depth"],
            candidate["finished_at"],
            candidate["source_node_id"],
        ),
    )
    selected_source = _session_staging_source_record(
        session,
        selected["node"],
        tree_depth=selected["tree_depth"],
        destination_ids=selected["destination_ids"],
        failure_evidence_node_ids=(
            selected["failure_evidence_node_ids"]
        ),
    )
    active["staging_avoidance_source"] = selected_source

    failure_owner_reader = getattr(
        session,
        "_primary_recovery_failure_owner_ids",
        None,
    )
    failure_owner_ids: list[str] = []
    if callable(failure_owner_reader):
        try:
            failure_owner_ids = _unique_text(
                failure_owner_reader(selected_source)
            )
        except Exception:
            failure_owner_ids = []
    if not failure_owner_ids:
        failure_owner_ids = list(selected["destination_ids"])
    avoid_owner_ids = _unique_text(
        list(selected["destination_ids"]) + failure_owner_ids
    )
    active["failure_protected_owner_ids"] = failure_owner_ids
    active["avoid_owner_ids"] = avoid_owner_ids

    audit.update({
        "status": "refined",
        "changed": selected_source != original_source,
        "selected_source_node_id": selected["source_node_id"],
        "selected_tree_depth": selected["tree_depth"],
        "final_source": copy.deepcopy(selected_source),
        "effective_avoid_owner_ids": list(avoid_owner_ids),
        "effective_failure_protected_owner_ids": list(
            failure_owner_ids
        ),
        "rejection_reasons": [],
    })
    return audit


def _session_node_object_ids(
    session: Any,
    node: Any,
) -> list[str]:
    reader = getattr(session, "_node_object_ids", None)
    if callable(reader):
        try:
            return _unique_text(reader(node))
        except Exception:
            pass

    spec = getattr(node, "spec", None)
    params = _mapping(getattr(spec, "params", None))
    values = _unique_text(params.get("object_ids"))
    participants = _mapping(params.get("participants"))
    for role in ("manipuland", "object", "subject"):
        values = _unique_text(values + _unique_text(
            participants.get(role)
        ))
    object_ref = getattr(spec, "object_ref", None)
    if isinstance(object_ref, Mapping):
        values = _unique_text(values + _unique_text(
            object_ref.get("entity_id")
        ))
    return values


def _session_node_destination_ids(
    session: Any,
    node: Any,
) -> list[str]:
    runtime = getattr(node, "runtime", None)
    failure = _mapping(getattr(runtime, "failure", None))
    reader = getattr(session, "_node_destination_owner_ids", None)
    if callable(reader):
        try:
            return _unique_text(reader(node, failure=failure))
        except Exception:
            pass

    values = _unique_text(failure.get("destination_id"))
    failed_participants = _mapping(
        failure.get("failed_participants")
    )
    for role in ("destination", "region_owner", "support"):
        values = _unique_text(values + _unique_text(
            failed_participants.get(role)
        ))
    spec = getattr(node, "spec", None)
    params = _mapping(getattr(spec, "params", None))
    values = _unique_text(
        values + _unique_text(params.get("destination_ids"))
    )
    participants = _mapping(params.get("participants"))
    for role in ("destination", "region_owner", "support"):
        values = _unique_text(values + _unique_text(
            participants.get(role)
        ))
    return values


def _failure_branch_evidence_node_ids(
    session: Any,
    tree: Any,
    branch_root: Any,
) -> list[str]:
    root_id = str(
        getattr(branch_root, "node_id", None)
        or getattr(getattr(branch_root, "spec", None), "node_id", "")
    )
    evidence: list[tuple[int, str]] = []
    for node in getattr(tree, "nodes", {}).values():
        node_id = str(
            getattr(node, "node_id", None)
            or getattr(getattr(node, "spec", None), "node_id", "")
        )
        if (
            not node_id
            or not _node_descends_from(tree, node_id, root_id)
            or not _session_node_has_failure_evidence(session, node)
        ):
            continue
        depth = _session_node_depth(session, tree, node_id)
        evidence.append((
            int(depth) if depth is not None else -1,
            node_id,
        ))
    return [
        node_id
        for _depth, node_id in sorted(
            evidence,
            key=lambda item: (item[0], item[1]),
        )
    ]


def _session_node_has_failure_evidence(
    session: Any,
    node: Any,
) -> bool:
    reader = getattr(
        session,
        "_node_has_recovery_failure_evidence",
        None,
    )
    if callable(reader):
        try:
            return bool(reader(node))
        except Exception:
            pass

    runtime = getattr(node, "runtime", None)
    failure = _mapping(getattr(runtime, "failure", None))
    result = _mapping(getattr(runtime, "last_result", None))
    status = str(getattr(node, "status", "")).upper()
    if not status:
        status = str(getattr(runtime, "status", "")).upper()
    failed_result = (
        result.get("failure_code") is not None
        and str(result.get("termination") or "").casefold()
        != "canceled"
    )
    terminal_failure = status in {
        "FAILED",
        "BLOCKED",
        "BLOCKED_WITH_PROGRESS",
        "PARTIAL_SUCCESS",
    }
    return bool(failure) or failed_result or terminal_failure


def _node_descends_from(
    tree: Any,
    node_id: str,
    ancestor_id: str,
) -> bool:
    current_id = str(node_id)
    visited: set[str] = set()
    while current_id and current_id not in visited:
        if current_id == ancestor_id:
            return True
        visited.add(current_id)
        node = getattr(tree, "nodes", {}).get(current_id)
        if node is None:
            return False
        parent_id = getattr(getattr(node, "spec", None), "parent_id", None)
        if parent_id is None:
            return False
        current_id = str(parent_id)
    return False


def _session_node_depth(
    session: Any,
    tree: Any,
    node_id: str,
) -> int | None:
    reader = getattr(session, "_task_tree_node_depth", None)
    if callable(reader):
        try:
            depth = reader(tree, node_id)
            return int(depth) if depth is not None else None
        except Exception:
            pass

    depth = 0
    current_id = str(node_id)
    visited: set[str] = set()
    while current_id not in visited:
        visited.add(current_id)
        node = getattr(tree, "nodes", {}).get(current_id)
        if node is None:
            return None
        parent_id = getattr(getattr(node, "spec", None), "parent_id", None)
        if parent_id is None:
            return depth
        current_id = str(parent_id)
        depth += 1
    return None


def _session_staging_source_record(
    session: Any,
    node: Any,
    *,
    tree_depth: int,
    destination_ids: list[str],
    failure_evidence_node_ids: list[str],
) -> dict[str, Any]:
    runtime = getattr(node, "runtime", None)
    failure = _mapping(getattr(runtime, "failure", None))
    reader = getattr(session, "_staging_source_record", None)
    record: dict[str, Any] = {}
    if callable(reader):
        try:
            raw_record = reader(
                node,
                source_kind="failure",
                stack_depth=None,
                failure=failure,
            )
            if isinstance(raw_record, Mapping):
                record = dict(raw_record)
        except Exception:
            record = {}

    spec = getattr(node, "spec", None)
    result = _mapping(getattr(runtime, "last_result", None))
    node_id = str(
        getattr(node, "node_id", None)
        or getattr(spec, "node_id", "")
    )
    children = list(getattr(spec, "children", ()) or ())
    record.update({
        "source_node_id": node_id,
        "source_kind": "failure",
        "stack_depth": None,
        "node_status": (
            getattr(node, "status", None)
            or getattr(runtime, "status", None)
        ),
        "task_type": getattr(spec, "task_type", None),
        "action_ref": getattr(spec, "action_ref", None),
        "is_atomic": bool(
            getattr(spec, "action_ref", None) and not children
        ),
        "child_count": len(children),
        "object_ids": _session_node_object_ids(session, node),
        "destination_ids": list(destination_ids),
        "runtime_transaction_id": getattr(
            runtime,
            "transaction_id",
            None,
        ),
        "result_transaction_id": result.get("transaction_id"),
        "termination": result.get("termination"),
        "effect_state": result.get("effect_state"),
        "verification": result.get("verification"),
        "failure_code": result.get("failure_code"),
        "safe_checkpoint": _mapping(
            result.get("residual_state")
        ).get("safe_checkpoint"),
        "phase": _mapping(
            result.get("residual_state")
        ).get("phase"),
        "has_failure_diagnostic": True,
        "finished_at": getattr(runtime, "finished_at", None),
        "tree_depth": int(tree_depth),
        "failure_evidence_node_ids": list(
            failure_evidence_node_ids
        ),
    })
    return record


def _allow_failed_original_floor_staging(
    active: dict[str, Any],
    gripper: Any,
    *,
    perception: Any,
    capabilities: Any,
) -> None:
    source = _mapping(active.get("staging_avoidance_source"))
    if (
        str(active.get("intent") or "") != "finalize_primary"
        or str(source.get("source_kind") or "") != "failure"
    ):
        return

    avoided = _unique_text(active.get("avoid_owner_ids"))
    protected = _unique_text(active.get("failure_protected_owner_ids"))
    object_ids = _unique_text(source.get("object_ids"))
    destination_ids = _unique_text(source.get("destination_ids"))
    held_entity_id = _optional_text(
        getattr(gripper, "held_entity_id", None)
    )
    reasons: list[str] = []

    _require(
        reasons,
        str(source.get("task_type") or "").casefold() == "place",
        "source_not_place_task",
    )
    _require(
        reasons,
        source.get("has_failure_diagnostic") is True,
        "failure_evidence_missing",
    )
    _require(
        reasons,
        str(getattr(gripper, "occupancy", "")).casefold() == "holding",
        "gripper_not_holding",
    )
    _require(
        reasons,
        str(
            getattr(gripper, "attachment_state", "")
        ).casefold() == "secure",
        "held_object_not_secure",
    )
    _require(
        reasons,
        len(object_ids) == 1
        and held_entity_id is not None
        and object_ids[0] == held_entity_id,
        "held_object_mismatch",
    )
    _require(
        reasons,
        len(destination_ids) == 1,
        "original_destination_not_singular",
    )

    destination_id = (
        destination_ids[0] if len(destination_ids) == 1 else None
    )
    if destination_id is not None:
        _require(
            reasons,
            destination_id in avoided,
            "original_destination_not_avoided",
        )
        _require(
            reasons,
            destination_id in protected,
            "original_destination_not_failure_protected",
        )

    region_ref: str | None = None
    if not reasons and destination_id is not None:
        region_ref, rejection = _trusted_floor_region(
            destination_id,
            perception=perception,
            capabilities=capabilities,
        )
        if rejection is not None:
            reasons.append(rejection)

    audit = {
        "allowed": not reasons,
        "owner_id": destination_id,
        "region_ref": region_ref,
        "held_entity_id": held_entity_id,
        "original_failure_protected_owner_ids": protected,
        "rejection_reasons": reasons,
        "basis": (
            "held_original_destination_is_trusted_open_floor"
            if not reasons
            else None
        ),
    }
    decision = active.get("staging_avoidance_decision")
    if not isinstance(decision, dict):
        decision = {}
        active["staging_avoidance_decision"] = decision
    decision["failed_floor_recovery"] = audit
    if reasons or destination_id is None:
        return

    active["avoid_owner_ids"] = [
        owner_id for owner_id in avoided if owner_id != destination_id
    ]
    active["failure_protected_owner_ids"] = [
        owner_id for owner_id in protected if owner_id != destination_id
    ]
    audit["effective_failure_protected_owner_ids"] = list(
        active["failure_protected_owner_ids"]
    )
    decision["relaxed"] = True
    decision["relaxation_mode"] = "failed_original_floor_staging"
    decision["reused_floor_owner_ids"] = list(
        dict.fromkeys(
            _unique_text(decision.get("reused_floor_owner_ids"))
            + [destination_id]
        )
    )
    decision.setdefault("original_avoid_owner_ids", avoided)
    decision["effective_avoid_owner_ids"] = list(
        active["avoid_owner_ids"]
    )
    decision["failure_protected_owner_ids"] = list(
        active["failure_protected_owner_ids"]
    )


def _trusted_floor_region(
    owner_id: str,
    *,
    perception: Any,
    capabilities: Any,
) -> tuple[str | None, str | None]:
    if perception is None:
        return None, "perception_unavailable"
    try:
        snapshot = capabilities.build_scene_capability_snapshot(perception)
        entities = capabilities.entity_index(snapshot)
        regions = capabilities.region_index(snapshot)
    except Exception:
        return None, "scene_capability_snapshot_failed"

    entity = entities.get(owner_id)
    if not isinstance(entity, Mapping):
        return None, "destination_missing_from_scene_snapshot"
    if str(entity.get("category") or "").casefold() != "floor":
        return None, "destination_not_floor"

    policy = (
        _mapping(snapshot.get("policies"))
        .get("clear_support_region")
    )
    if not isinstance(policy, Mapping):
        return None, "clear_support_policy_unavailable"
    candidate_refs = _unique_text(
        [
            policy.get("default_destination_region_ref"),
            *list(policy.get("destination_region_refs") or ()),
        ]
    )
    for region_ref in candidate_refs:
        region = regions.get(region_ref)
        if not isinstance(region, Mapping):
            continue
        if str(region.get("owner_ref") or "") != owner_id:
            continue
        if str(region.get("selector") or "") != "support":
            continue
        if not bool(region.get("available", True)):
            continue
        if str(region.get("capacity_mode") or "") != "multi_object":
            continue
        allowed_relations = _unique_text(
            region.get("allowed_relations")
        )
        if "on_support" not in allowed_relations:
            continue
        return region_ref, None
    return None, "destination_not_trusted_open_floor"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _unique_text(value: Any) -> list[str]:
    if value is None:
        return []
    values = (
        value
        if isinstance(value, (list, tuple, set, frozenset))
        else [value]
    )
    return list(
        dict.fromkeys(
            str(item)
            for item in values
            if item is not None and str(item).strip()
        )
    )


def _optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value)


def _require(
    reasons: list[str],
    condition: bool,
    reason: str,
) -> None:
    if not condition:
        reasons.append(reason)


__all__ = ["adapt_continuous_session_class"]
