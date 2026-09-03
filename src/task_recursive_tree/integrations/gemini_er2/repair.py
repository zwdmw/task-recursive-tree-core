from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from math import isfinite
from types import SimpleNamespace
from typing import Any

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.contracts import RepairContext
from task_recursive_tree.task.model import (
    EdgeKind,
    FailureResolutionKind,
    GraphDelta,
    RepairProposal,
    TaskEdge,
    TaskNodeSpec,
)

from .decomposition import to_harness_node
from .paths import import_harness_module
from .relocatability import (
    non_relocatable_entity_ids,
    relocatable_by_grasp,
    relocatable_entity_ids,
)
from .region_space import (
    PROTECTED_RELOCATION_ENTITY_IDS_KEY,
    RELOCATION_CONTINUATION_CONTEXT_KEYS,
)
from .translation import (
    GEMINI_ER2_METADATA_KEY,
    translate_harness_spec,
)


_REPAIR_ARTIFACT_OUTPUTS = {
    "plan_detour": ("detour_path", "path_ref"),
    "plan_transport_posture": (
        "transport_posture",
        "posture_ref",
    ),
}

_ARTIFACT_KIND_BY_REF_KEY = {
    "path_ref": "detour_path",
    "posture_ref": "transport_posture",
    "target_ref": "layout_targets",
}

_LAYOUT_CONTEXT_PARAMETER_KEYS = (
    "layout_continuation_scope_id",
    "reservation_group",
    "reservation_index",
)

_RELOCATION_CONTINUATION_GOAL_PHASES = frozenset({
    "astar_after_held_departure",
    "base_path",
    "combined_held_path",
    "held_base_path",
    "held_departure_prefix",
    "placement_approach",
})

_GENERIC_CONTINUATION_REPAIR_TASK_TYPES = frozenset({
    "plan_detour",
    "reposition_for_interaction",
    "vacate_placement_region",
})


class HarnessRepairResolver:
    """Adapt Harness repair proposals into kernel-owned graph deltas."""

    def __init__(
        self,
        *,
        runtime: Any,
        tree: Any = None,
        task_id: str | None = None,
        config: Mapping[str, Any] | None = None,
        planner: Any = None,
        harness_root: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.tree = tree
        self.task_id = task_id
        self.config = dict(config or {})
        self._planner = planner
        self.harness_root = harness_root

    def propose(
        self,
        node: TaskNodeSpec,
        diagnostic: Diagnostic,
        context: RepairContext,
        repair_index: int,
    ) -> RepairProposal | None:
        protected_layout_reselection_failure = (
            _is_place_layout_reselection_failure(node, diagnostic)
        )
        protected_stale_layout_failure = (
            _is_stale_layout_artifact_failure(diagnostic)
        )
        harness_diagnostic = self._diagnostic(node, diagnostic)
        is_layout_reselection_failure = (
            protected_layout_reselection_failure
            or _is_place_layout_reselection_failure(
                node,
                harness_diagnostic,
            )
        )
        is_stale_layout_failure = (
            protected_stale_layout_failure
            or _is_stale_layout_artifact_failure(
                harness_diagnostic,
            )
        )
        repair_node = _placement_layout_repair_node(
            node,
            harness_diagnostic,
            context,
            self.tree,
            repair_index,
        )
        if repair_node is not None:
            proposal = None
            rule_ref = (
                "kernel_adapter:replan_placement_layout@1.0"
            )
            max_attempts = 1
            invalidates_artifacts: tuple[str, ...] = ()
            proposal_obligations: tuple[Mapping[str, Any], ...] = ()
            repair_params = _mapping(
                getattr(repair_node, "params", {})
            )
            if (
                repair_params.get("repair_reason")
                == "stale_layout_artifact"
            ):
                rationale = (
                    "The declared placement layout is stale under the "
                    "current region state; republish its original producer "
                    "contract without invalidating the existing reservation "
                    "before replacement succeeds."
                )
            else:
                failed_local_xy = list(
                    repair_params.get("failed_layout_local_xy", ())
                )
                rationale = (
                    "The reserved placement target is physically infeasible "
                    "for the carried object; republish the same layout "
                    "contract while excluding failed local target "
                    f"{failed_local_xy}."
                )
        else:
            if is_layout_reselection_failure or is_stale_layout_failure:
                return None
            proposal = self._repair_planner().propose(
                parent=to_harness_node(node),
                diagnostic=harness_diagnostic,
                context=self._execution_context(
                    node,
                    harness_diagnostic,
                    context,
                ),
            )
            repair_node = getattr(proposal, "repair_node", None)
            if proposal is None or repair_node is None:
                return None
            repair_node = copy.deepcopy(repair_node)
            rule_ref = str(
                getattr(proposal, "rule_ref", "") or "builtin"
            )
            max_attempts = getattr(proposal, "max_attempts", None)
            invalidates_artifacts = tuple(
                str(value)
                for value in (
                    getattr(
                        proposal,
                        "invalidates_artifacts",
                        (),
                    )
                    or ()
                )
                if str(value)
            )
            proposal_obligations = tuple(
                copy.deepcopy(dict(value))
                for value in (
                    getattr(
                        proposal,
                        "persistent_obligations",
                        (),
                    )
                    or ()
                )
                if isinstance(value, Mapping)
            )
            rationale = str(
                getattr(proposal, "rationale", "")
                or f"Harness repair for {diagnostic.code}"
            )

        repair_node = copy.deepcopy(repair_node)
        _inject_relocation_blocker_set_context(
            repair_node,
            harness_diagnostic,
        )
        _inject_relocation_continuation_context(
            repair_node,
            harness_diagnostic,
        )
        _inject_relocation_protection_context(
            repair_node,
            harness_diagnostic,
            node,
        )
        _inherit_generic_repair_continuation_context(
            repair_node,
            node,
        )
        if not _repair_manipulation_targets_are_admissible(
            repair_node,
            self.runtime,
        ):
            return None
        _ensure_repair_world_entities(
            self.runtime,
            repair_node,
            harness_diagnostic,
            harness_root=self.harness_root,
        )
        invalidates_artifacts = _bind_repair_artifact_replacement(
            node,
            repair_node,
            context.artifacts,
            invalidates_artifacts,
        )
        if invalidates_artifacts is None:
            return None
        generated_obligations = _normalize_region_repair(
            repair_node,
            harness_diagnostic,
            self.runtime,
        )
        if generated_obligations is None:
            return None

        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        root_id = (
            str(source.get("root_id"))
            if isinstance(source, Mapping) and source.get("root_id")
            else node.node_id
        )
        stable_node_id = _repair_node_id(
            node,
            repair_node,
            rule_ref,
            repair_index,
        )
        if not _bind_repair_artifact_consumers(
            node,
            repair_node,
            continuation_node_id=stable_node_id,
        ):
            return None
        translated = translate_harness_spec(
            repair_node,
            root_id=root_id,
            node_id=stable_node_id,
            parent_id=node.node_id,
        )
        if max_attempts is not None:
            translated = replace(
                translated,
                max_attempts=max(1, int(max_attempts)),
            )
        delta = GraphDelta.from_specs(
            (translated,),
            (
                TaskEdge(
                    parent_id=node.node_id,
                    child_id=stable_node_id,
                    kind=EdgeKind.REPAIR,
                    order=max(0, int(repair_index)),
                ),
            ),
        )
        node_kind = str(getattr(repair_node, "node_kind", "")).lower()
        resolution_kind = (
            FailureResolutionKind.RECONCILIATION
            if node_kind == "reconciliation"
            or translated.task_type == "reconcile"
            else FailureResolutionKind.REPAIR
        )
        persistent_obligations = _merge_mapping_values(
            proposal_obligations,
            generated_obligations,
        )
        return RepairProposal(
            delta=delta,
            entry_node_id=stable_node_id,
            rationale=rationale,
            kind=resolution_kind,
            invalidates_artifacts=invalidates_artifacts,
            persistent_obligations=persistent_obligations,
        )

    def _repair_planner(self) -> Any:
        if self._planner is not None:
            return self._planner
        module = import_harness_module(
            "er2sim.repair_planner",
            harness_root=self.harness_root,
        )
        self._planner = module.RepairPlanner()
        return self._planner

    def _diagnostic(
        self,
        node: TaskNodeSpec,
        diagnostic: Diagnostic,
    ) -> Any:
        module = import_harness_module(
            "er2sim.task_diagnostics",
            harness_root=self.harness_root,
        )
        details = copy.deepcopy(dict(diagnostic.details))
        diagnostic_code = diagnostic.code
        if _requires_reconciliation(diagnostic, details):
            if diagnostic.code != "OUTCOME_UNKNOWN":
                details.setdefault(
                    "physical_failure_code",
                    diagnostic.code,
                )
            diagnostic_code = "OUTCOME_UNKNOWN"
        parameters = {
            str(key): copy.deepcopy(value)
            for key, value in node.parameters.items()
            if key != GEMINI_ER2_METADATA_KEY
        }
        details.setdefault("params", parameters)
        residual_state = _mapping(details.get("residual_state"))
        failed_participants = _participants(
            details.get("failed_participants")
            or details.get("participants")
            or parameters.get("participants")
        )
        object_id = _first_text(
            details.get("placement_object_id"),
            details.get("placement_object_ids"),
            parameters.get("placement_object"),
            parameters.get("placement_object_id"),
            parameters.get("placement_object_ids"),
            failed_participants.get("placement_object"),
            details.get("object_id"),
            parameters.get("object_id"),
            parameters.get("object_ids"),
            failed_participants.get("manipuland"),
            failed_participants.get("object"),
        )
        destination_id = _first_text(
            details.get("destination_id"),
            details.get("placement_destination_id"),
            parameters.get("destination_id"),
            parameters.get("destination_ids"),
            failed_participants.get("destination"),
        )
        world = getattr(self.runtime, "world", None)
        world_revision = details.get(
            "world_revision",
            getattr(world, "revision", 0),
        )
        diagnostic_id = _diagnostic_id(node, diagnostic, details)
        return module.FailureDiagnostic(
            diagnostic_id=diagnostic_id,
            source_node_id=node.node_id,
            kind=str(details.get("kind") or "action_failed"),
            code=diagnostic_code,
            failure_mode=_optional_text(
                details.get("failure_mode")
                or residual_state.get("failure_mode")
            ),
            phase=str(details.get("phase") or ""),
            failed_predicate=_optional_text(
                details.get("failed_predicate")
            ),
            failed_participants=failed_participants,
            target_role=_optional_text(details.get("target_role")),
            object_id=object_id,
            destination_id=destination_id,
            expected_value=_optional_text(details.get("expected_value")),
            actual_value=_optional_text(details.get("actual_value")),
            witness_entity_ids=_text_list(
                details.get("witness_entity_ids")
            ),
            blocking_entity_ids=_text_list(
                details.get("blocking_entity_ids")
                or details.get("blocker_ids")
            ),
            affected_refs=_text_list(details.get("affected_refs")),
            world_revision=int(world_revision or 0),
            relevant_read_set={
                str(key): int(value)
                for key, value in _mapping(
                    details.get("relevant_read_set")
                    or details.get("read_set")
                ).items()
            },
            repair_scope_id=_optional_text(
                details.get("repair_scope_id")
            ),
            problem_signature=_optional_text(
                details.get("problem_signature")
            ),
            semantic_state_fingerprint=_optional_text(
                details.get("semantic_state_fingerprint")
            ),
            attempted_rule_refs=_text_list(
                details.get("attempted_rule_refs")
            ),
            evidence_refs=_text_list(details.get("evidence_refs")),
            derivation=str(details.get("derivation") or "kernel_diagnostic"),
            cause=str(details.get("cause") or diagnostic.message),
            recoverability=str(
                details.get("recoverability")
                or (
                    "repairable"
                    if diagnostic.repairable
                    else "retryable"
                    if diagnostic.retryable
                    else "impossible"
                )
            ),
            recommended_repairs=_text_list(
                details.get("recommended_repairs")
            ),
            retryable=bool(diagnostic.retryable),
            requires_user_confirmation=bool(
                details.get("requires_user_confirmation", False)
            ),
            transaction_id=_optional_text(
                details.get("transaction_id")
            ),
            residual_state=residual_state,
            effects=copy.deepcopy(list(details.get("effects") or ())),
            result=_mapping(details.get("result")),
            details=details,
        )

    def _execution_context(
        self,
        node: TaskNodeSpec,
        diagnostic: Any,
        context: RepairContext,
    ) -> SimpleNamespace:
        task_id = (
            self.task_id
            or getattr(self.runtime, "_current_task_id", None)
            or "kernel-task"
        )
        details = _mapping(getattr(diagnostic, "details", {}))
        return SimpleNamespace(
            runtime=self.runtime,
            tree=self.tree,
            task_id=str(task_id),
            config=dict(self.config),
            variables={
                key: copy.deepcopy(value)
                for key, value in node.parameters.items()
                if key != GEMINI_ER2_METADATA_KEY
            },
            artifacts=context.artifacts,
            world=context.world,
            repair_scope_id=(
                getattr(diagnostic, "repair_scope_id", None)
                or node.node_id
            ),
            problem_signature=(
                getattr(diagnostic, "problem_signature", None)
                or getattr(diagnostic, "diagnostic_id", None)
            ),
            semantic_state_fingerprint=(
                getattr(diagnostic, "semantic_state_fingerprint", None)
                or details.get("semantic_state_fingerprint")
            ),
            repair_excluded_rule_refs=set(
                getattr(diagnostic, "attempted_rule_refs", ()) or ()
            ),
        )


def _ensure_repair_world_entities(
    runtime: Any,
    repair_node: Any,
    diagnostic: Any,
    *,
    harness_root: str | None,
) -> tuple[str, ...]:
    world = getattr(runtime, "world", None)
    perception = getattr(runtime, "perception", None)
    entities = getattr(world, "entities", None)
    catalog = getattr(perception, "catalog", None)
    if not isinstance(entities, Mapping) or not isinstance(catalog, Mapping):
        return ()

    catalog_ids = {str(entity_id) for entity_id in catalog}
    referenced: list[str] = []
    seen: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            if value in catalog_ids and value not in seen:
                seen.add(value)
                referenced.append(value)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                collect(item)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                collect(item)

    for attribute in (
        "blocking_entity_ids",
        "witness_entity_ids",
        "affected_refs",
        "object_id",
        "destination_id",
    ):
        collect(getattr(diagnostic, attribute, None))
    for attribute in (
        "object_ref",
        "actor_ref",
        "preconditions",
        "goal",
        "obligations",
        "params",
    ):
        collect(getattr(repair_node, attribute, None))

    missing = [
        entity_id
        for entity_id in referenced
        if entity_id not in entities
    ]
    if not missing:
        return ()
    ensure_entity = getattr(
        import_harness_module(
            "er2sim.task_decomposer",
            harness_root=harness_root,
        ),
        "_ensure_world_entity",
        None,
    )
    if not callable(ensure_entity):
        return ()
    for entity_id in missing:
        ensure_entity(runtime, entity_id)
    return tuple(
        entity_id
        for entity_id in missing
        if entity_id in getattr(world, "entities", {})
    )


def _bind_repair_artifact_replacement(
    node: TaskNodeSpec,
    repair_node: Any,
    artifacts: Any,
    invalidates_artifacts: tuple[str, ...],
) -> tuple[str, ...] | None:
    replacement = _REPAIR_ARTIFACT_OUTPUTS.get(
        str(getattr(repair_node, "task_type", "") or "").casefold()
    )
    if replacement is None:
        return invalidates_artifacts

    artifact_kind, ref_key = replacement
    contract = _node_artifact_contract(
        node,
        "artifact_consumes",
        artifact_kind=artifact_kind,
        ref_key=ref_key,
    )
    producer_node_id = _first_text(contract.get("producer_node_id"))
    continuation_node_id = _first_text(
        contract.get("continuation_node_id")
    )
    if (
        producer_node_id is None
        or continuation_node_id != node.node_id
        or str(contract.get("ref_key") or "") != ref_key
        or contract.get("required") is not True
    ):
        return None

    metadata = _mapping(getattr(repair_node, "metadata", {}))
    rebound = {
        "schema": str(contract.get("schema") or "task_artifact/1.0"),
        "artifact_kind": artifact_kind,
        "producer_node_id": producer_node_id,
        "continuation_node_id": continuation_node_id,
        "ref_key": ref_key,
        "required": True,
    }
    metadata = _replace_producer_contract(
        metadata,
        rebound,
    )
    if isinstance(repair_node, dict):
        repair_node["metadata"] = metadata
    else:
        repair_node.metadata = metadata

    stale_refs = list(invalidates_artifacts)
    direct_ref = node.parameters.get(ref_key)
    if isinstance(direct_ref, str) and direct_ref:
        stale_refs.append(direct_ref)
    consumed = getattr(artifacts, "consumed", None)
    if callable(consumed):
        try:
            publications = consumed(node.node_id)
        except Exception:
            publications = ()
        for publication in publications or ():
            if not isinstance(publication, Mapping):
                continue
            if (
                str(publication.get("artifact_kind") or "")
                != artifact_kind
                or str(publication.get("producer_node_id") or "")
                != producer_node_id
                or str(publication.get("continuation_node_id") or "")
                != continuation_node_id
            ):
                continue
            artifact_ref = publication.get("artifact_ref")
            if isinstance(artifact_ref, str) and artifact_ref:
                stale_refs.append(artifact_ref)
    return tuple(dict.fromkeys(stale_refs))


def _replace_producer_contract(
    metadata: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    return _replace_artifact_contract(
        metadata,
        "artifact_produces",
        contract,
    )


def _replace_artifact_contract(
    metadata: Mapping[str, Any],
    contract_key: str,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(metadata))
    values = list(_metadata_artifact_contracts(result, contract_key))
    artifact_kind = str(contract.get("artifact_kind") or "")
    ref_key = str(contract.get("ref_key") or "")
    values = [
        value
        for value in values
        if not (
            str(value.get("artifact_kind") or "") == artifact_kind
            and str(value.get("ref_key") or "") == ref_key
        )
    ]
    values.append(copy.deepcopy(dict(contract)))
    result[contract_key] = values
    return result


def _bind_repair_artifact_consumers(
    node: TaskNodeSpec,
    repair_node: Any,
    *,
    continuation_node_id: str,
) -> bool:
    params = _mapping(
        repair_node.get("params")
        if isinstance(repair_node, Mapping)
        else getattr(repair_node, "params", {})
    )
    metadata = _mapping(
        repair_node.get("metadata")
        if isinstance(repair_node, Mapping)
        else getattr(repair_node, "metadata", {})
    )
    produced = {
        (
            str(contract.get("artifact_kind") or ""),
            str(contract.get("ref_key") or ""),
        )
        for contract in _metadata_artifact_contracts(
            metadata,
            "artifact_produces",
        )
    }
    parent_contracts = {
        (
            str(contract.get("artifact_kind") or ""),
            str(contract.get("ref_key") or ""),
        ): contract
        for contract in _node_artifact_contracts(
            node,
            "artifact_consumes",
        )
    }

    for ref_key, artifact_kind in _ARTIFACT_KIND_BY_REF_KEY.items():
        repair_ref = params.get(ref_key)
        if repair_ref is None:
            continue
        if (artifact_kind, ref_key) in produced:
            continue
        if not isinstance(repair_ref, str) or not repair_ref:
            return False

        contract = parent_contracts.get((artifact_kind, ref_key))
        producer_node_id = _first_text(
            contract.get("producer_node_id")
            if contract is not None
            else None
        )
        required = (
            contract.get("required")
            if contract is not None
            else None
        )
        if (
            contract is None
            or producer_node_id is None
            or str(contract.get("continuation_node_id") or "")
            != node.node_id
            or str(contract.get("ref_key") or "") != ref_key
            or not isinstance(required, bool)
            or str(node.parameters.get(ref_key) or "") != repair_ref
        ):
            return False

        if artifact_kind == "layout_targets":
            parent_producer = node.parameters.get(
                "layout_producer_node_id"
            )
            repair_producer = params.get("layout_producer_node_id")
            if (
                parent_producer is not None
                and str(parent_producer) != producer_node_id
            ) or (
                repair_producer is not None
                and str(repair_producer) != producer_node_id
            ):
                return False
            params["layout_producer_node_id"] = producer_node_id
            for key in _LAYOUT_CONTEXT_PARAMETER_KEYS:
                inherited = node.parameters.get(key)
                existing = params.get(key)
                if (
                    inherited is not None
                    and existing is not None
                    and existing != inherited
                ):
                    return False
                if inherited is not None:
                    params[key] = copy.deepcopy(inherited)

        rebound = copy.deepcopy(dict(contract))
        rebound.update(
            {
                "schema": str(
                    contract.get("schema") or "task_artifact/1.0"
                ),
                "artifact_kind": artifact_kind,
                "producer_node_id": producer_node_id,
                "continuation_node_id": str(continuation_node_id),
                "ref_key": ref_key,
                "required": required,
            }
        )
        metadata = _replace_artifact_contract(
            metadata,
            "artifact_consumes",
            rebound,
        )

    if isinstance(repair_node, dict):
        repair_node["params"] = params
        repair_node["metadata"] = metadata
    else:
        repair_node.params = params
        repair_node.metadata = metadata
    return True


def _metadata_artifact_contracts(
    metadata: Mapping[str, Any],
    contract_key: str,
) -> tuple[dict[str, Any], ...]:
    raw = metadata.get(contract_key)
    if isinstance(raw, Mapping):
        values = (raw,)
    elif isinstance(raw, (list, tuple)):
        values = tuple(
            value for value in raw if isinstance(value, Mapping)
        )
    else:
        values = ()
    return tuple(copy.deepcopy(dict(value)) for value in values)


def _node_artifact_contracts(
    node: TaskNodeSpec,
    contract_key: str,
) -> tuple[dict[str, Any], ...]:
    source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
    metadata = (
        source.get("metadata")
        if isinstance(source, Mapping)
        else None
    )
    return _metadata_artifact_contracts(
        _mapping(metadata),
        contract_key,
    )


def _node_artifact_contract(
    node: TaskNodeSpec,
    contract_key: str,
    *,
    artifact_kind: str,
    ref_key: str,
) -> dict[str, Any]:
    for value in _node_artifact_contracts(node, contract_key):
        if (
            str(value.get("artifact_kind") or "") == artifact_kind
            and str(value.get("ref_key") or "") == ref_key
        ):
            return value
    return {}


def _is_place_held_path_failure(
    node: TaskNodeSpec,
    diagnostic: Any,
) -> bool:
    if str(node.task_type).casefold() != "place_object":
        return False
    if str(getattr(diagnostic, "code", "")).upper() != "PATH_BLOCKED":
        return False
    details = _mapping(getattr(diagnostic, "details", {}))
    residual = _mapping(details.get("residual_state"))
    control_error = _mapping(residual.get("control_error"))
    control_details = _mapping(control_error.get("details"))
    raw_failure_code = _first_text(
        details.get("raw_failure_code"),
        control_details.get("raw_failure_code"),
    )
    if str(raw_failure_code or "").upper() in {
        "HELD_PATH_COLLISION",
        "HELD_BASE_PATH_COLLISION",
        "HELD_START_POSE_COLLISION",
    }:
        return True
    if str(control_error.get("code") or "").upper() != "PATH_BLOCKED":
        return False
    return _diagnostic_confirms_placement_hold(node, details)


def _is_prepared_placement_collision(
    node: TaskNodeSpec,
    diagnostic: Any,
) -> bool:
    if str(node.task_type).casefold() != "place_object":
        return False
    if str(getattr(diagnostic, "code", "")).upper() not in {
        "COLLISION",
        "INTERACTION_POSE_BLOCKED",
    }:
        return False
    details = _mapping(getattr(diagnostic, "details", {}))
    residual = _mapping(details.get("residual_state"))
    control_error = _mapping(residual.get("control_error"))
    control_details = _mapping(control_error.get("details"))
    raw_failure_code = _first_text(
        details.get("raw_failure_code"),
        control_details.get("raw_failure_code"),
    )
    if str(raw_failure_code or "").upper() != (
        "PREPARED_PLACEMENT_COLLISION"
    ):
        return False
    if str(details.get("recovery_kind") or "").casefold() != (
        "replan_placement_layout"
    ):
        return False
    return _diagnostic_confirms_placement_hold(node, details)


def _is_post_placement_continuation_failure(
    node: TaskNodeSpec,
    diagnostic: Any,
) -> bool:
    if str(node.task_type).casefold() not in {
        "build_obstacle_map",
        "plan_detour",
        "reposition_for_interaction",
    }:
        return False
    if str(getattr(diagnostic, "code", "")).upper() != (
        "INTERACTION_POSE_BLOCKED"
    ):
        return False
    details = _mapping(getattr(diagnostic, "details", {}))
    if str(details.get("failure_mode") or "").upper() != (
        "POST_PLACEMENT_CONTINUATION_UNREACHABLE"
    ):
        return False
    if str(details.get("recovery_kind") or "").casefold() != (
        "replan_placement_layout"
    ):
        return False
    params = {
        str(key): copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    diagnostic_params = _mapping(details.get("params"))
    return (
        _first_text(
            details.get("target_ref"),
            diagnostic_params.get("target_ref"),
            params.get("target_ref"),
        )
        is not None
        and _first_text(
            details.get("placement_object_id"),
            diagnostic_params.get("placement_object_id"),
            diagnostic_params.get("placement_object_ids"),
            params.get("placement_object_id"),
            params.get("placement_object_ids"),
            _participants(params.get("participants")).get(
                "placement_object"
            ),
        )
        is not None
    )


def _is_place_layout_reselection_failure(
    node: TaskNodeSpec,
    diagnostic: Any,
) -> bool:
    return _is_place_held_path_failure(
        node,
        diagnostic,
    ) or _is_prepared_placement_collision(
        node,
        diagnostic,
    ) or _is_post_placement_continuation_failure(
        node,
        diagnostic,
    ) or _is_placeability_layout_failure(
        node,
        diagnostic,
    )


def _is_placeability_layout_failure(
    node: TaskNodeSpec,
    diagnostic: Any,
) -> bool:
    if str(node.task_type).casefold() != "assess_placeability":
        return False
    code = str(getattr(diagnostic, "code", "")).upper()
    if code not in {
        "REGION_OCCUPIED",
        "PLACEMENT_COLLISION",
        "SELF_OCCUPANCY_BLOCKS_PLACEMENT",
    }:
        return False
    details = _mapping(getattr(diagnostic, "details", {}))
    result = _mapping(details.get("result"))
    if (
        code == "SELF_OCCUPANCY_BLOCKS_PLACEMENT"
        and not _placeability_external_blocking_ids(
            node,
            details,
            result,
        )
    ):
        return False
    params = {
        str(key): copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    return _first_text(
        details.get("target_ref"),
        details.get("layout_target_ref"),
        result.get("target_ref"),
        result.get("layout_target_ref"),
        params.get("target_ref"),
    ) is not None


def _placeability_external_blocking_ids(
    node: TaskNodeSpec,
    details: Mapping[str, Any],
    result: Mapping[str, Any],
) -> tuple[str, ...]:
    params = {
        str(key): copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    participants = _participants(params.get("participants"))
    assessment = _mapping(
        details.get("assessment")
        or result.get("assessment")
    )
    assessment_details = _mapping(assessment.get("details"))
    robot_clearance = _mapping(
        assessment_details.get("robot_clearance")
    )
    ignored = {
        "robot_1",
        *_text_list(participants.get("robot")),
        *_text_list(robot_clearance.get("robot_id")),
        *_text_list(
            _first_text(
                details.get("placement_object_id"),
                details.get("object_id"),
                params.get("placement_object_id"),
                params.get("object_id"),
                params.get("object_ids"),
                participants.get("placement_object"),
                participants.get("manipuland"),
                participants.get("object"),
            )
        ),
        *_text_list(
            _first_text(
                details.get("placement_destination_id"),
                details.get("destination_id"),
                params.get("destination_id"),
                params.get("destination_ids"),
                participants.get("destination"),
            )
        ),
    }
    candidates: list[str] = []
    for value in (
        details.get("blocking_entity_ids"),
        result.get("blocking_entity_ids"),
        assessment.get("blocking_entity_ids"),
        details.get("occupant_ids"),
        result.get("occupant_ids"),
        assessment.get("occupant_ids"),
    ):
        candidates.extend(_text_list(value))
    return tuple(
        entity_id
        for entity_id in dict.fromkeys(candidates)
        if entity_id and entity_id not in ignored
    )


def _diagnostic_confirms_placement_hold(
    node: TaskNodeSpec,
    details: Mapping[str, Any],
) -> bool:
    params = {
        str(key): copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    participants = _participants(params.get("participants"))
    object_id = _first_text(
        details.get("placement_object_id"),
        details.get("placement_object_ids"),
        params.get("placement_object"),
        params.get("placement_object_id"),
        params.get("placement_object_ids"),
        participants.get("placement_object"),
        params.get("object_id"),
        params.get("object_ids"),
        participants.get("manipuland"),
        participants.get("object"),
    )
    if object_id is None:
        return False
    raw_effects = details.get("effects")
    if not isinstance(raw_effects, (list, tuple)):
        raw_effects = _mapping(details.get("result")).get("effects")
    if not isinstance(raw_effects, (list, tuple)):
        return False
    for effect in raw_effects:
        if not isinstance(effect, Mapping):
            continue
        if str(effect.get("predicate") or "").casefold() != (
            "attached_to_any_end_effector"
        ):
            continue
        if str(effect.get("state") or "").casefold() not in {
            "confirmed",
            "true",
            "succeeded",
        }:
            continue
        effect_participants = _participants(effect.get("participants"))
        predicate_details = _mapping(effect.get("predicate_details"))
        held_entity_id = _first_text(
            predicate_details.get("held_entity_id"),
            predicate_details.get("observed_held_entity_id"),
        )
        effect_objects = {
            *effect_participants.get("object", ()),
            *effect_participants.get("subject", ()),
        }
        if object_id in effect_objects or held_entity_id == object_id:
            return True
    return False


def _is_stale_layout_artifact_failure(diagnostic: Any) -> bool:
    code = str(getattr(diagnostic, "code", "")).upper()
    details = _mapping(getattr(diagnostic, "details", {}))
    if code == "STALE_ARTIFACT":
        return str(details.get("artifact_kind") or "").casefold() == (
            "layout_targets"
        )
    if code != "TARGET_REGION_CHANGED":
        return False
    params = _mapping(details.get("params"))
    target_kind = _first_text(
        details.get("interaction_target_kind"),
        params.get("interaction_target_kind"),
    )
    target_ref = _first_text(
        details.get("target_ref"),
        details.get("artifact_ref"),
        params.get("target_ref"),
    )
    return (
        str(target_kind or "").casefold() == "placement_pose"
        and target_ref is not None
    )


def _placement_layout_repair_node(
    node: TaskNodeSpec,
    diagnostic: Any,
    context: RepairContext,
    tree: Any,
    repair_index: int,
) -> SimpleNamespace | None:
    held_path_failure = _is_place_held_path_failure(node, diagnostic)
    prepared_placement_collision = _is_prepared_placement_collision(
        node,
        diagnostic,
    )
    post_placement_continuation_failure = (
        _is_post_placement_continuation_failure(
            node,
            diagnostic,
        )
    )
    placeability_layout_failure = _is_placeability_layout_failure(
        node,
        diagnostic,
    )
    layout_reselection_failure = (
        held_path_failure
        or prepared_placement_collision
        or post_placement_continuation_failure
        or placeability_layout_failure
    )
    stale_layout_failure = _is_stale_layout_artifact_failure(diagnostic)
    if not layout_reselection_failure and not stale_layout_failure:
        return None

    details = _mapping(getattr(diagnostic, "details", {}))
    residual = _mapping(details.get("residual_state"))
    control_error = _mapping(residual.get("control_error"))
    control_details = _mapping(control_error.get("details"))
    if held_path_failure:
        raw_failure_code = _first_text(
            details.get("raw_failure_code"),
            control_details.get("raw_failure_code"),
        )
        if str(raw_failure_code or "").upper() != "HELD_PATH_COLLISION":
            return None
        recovery_kind = str(
            details.get("recovery_kind") or ""
        ).casefold()
        near_pure_rotation = bool(
            details.get("near_pure_rotation")
            or control_details.get("near_pure_rotation")
            or recovery_kind == "reposition_held_rotation"
        )
        if near_pure_rotation:
            return None
        if recovery_kind and recovery_kind != "replan_placement_layout":
            return None

    params = {
        str(key): copy.deepcopy(value)
        for key, value in node.parameters.items()
        if key != GEMINI_ER2_METADATA_KEY
    }
    target_ref = _first_text(
        details.get("target_ref"),
        details.get("artifact_ref"),
        params.get("target_ref"),
    )
    parameter_participants = _participants(params.get("participants"))
    object_id = _first_text(
        details.get("placement_object_id"),
        details.get("placement_object_ids"),
        params.get("placement_object"),
        params.get("placement_object_id"),
        params.get("placement_object_ids"),
        parameter_participants.get("placement_object"),
        getattr(diagnostic, "object_id", None),
        details.get("object_id"),
        params.get("object_id"),
        params.get("object_ids"),
        parameter_participants.get("manipuland"),
        parameter_participants.get("object"),
    )
    consumer_contract = _node_layout_contract(node, "artifact_consumes")
    contract_producer_id = _first_text(
        consumer_contract.get("producer_node_id")
    )
    producer_id = _first_text(
        params.get("layout_producer_node_id"),
        contract_producer_id,
    )
    if (
        target_ref is None
        or object_id is None
        or producer_id is None
        or not _layout_contract_is_complete(
            consumer_contract,
            producer_node_id=producer_id,
            continuation_node_id=node.node_id,
        )
        or str(params.get("target_ref") or "") != target_ref
        or (
            params.get("layout_producer_node_id") is not None
            and str(params.get("layout_producer_node_id"))
            != producer_id
        )
    ):
        return None

    artifact = _repair_artifact(context.artifacts, target_ref)
    if (
        artifact is None
        or str(artifact.get("kind") or "") != "layout_targets"
        or str(artifact.get("source") or "")
        != "task_recursive_tree_batch_allocator/1.0"
    ):
        return None
    targets = artifact.get("targets")
    target = (
        targets.get(object_id)
        if isinstance(targets, Mapping)
        else None
    )
    failed_local_xy: tuple[float, float] | None = None
    if isinstance(target, Mapping):
        failed_points = _local_xy_values((target.get("local_xy"),))
        failed_local_xy = failed_points[0] if failed_points else None
    if layout_reselection_failure and failed_local_xy is None:
        return None

    template = _tree_spec_payload(tree, producer_id)
    producer_contract = _layout_contract(
        _mapping(template.get("metadata")),
        "artifact_produces",
        producer_node_id=producer_id,
    )
    producer_continuation_id = _first_text(
        producer_contract.get("continuation_node_id")
    )
    if (
        not template
        or producer_continuation_id is None
        or not _layout_contract_is_complete(
            producer_contract,
            producer_node_id=producer_id,
            continuation_node_id=producer_continuation_id,
        )
    ):
        return None
    repair_params = _mapping(template.get("params"))
    producer_ref_key = str(
        producer_contract.get("ref_key") or "target_ref"
    )
    if str(repair_params.get(producer_ref_key) or "") != target_ref:
        return None
    repair_params.update(
        {
            "object_ids": [object_id],
            "batch_object_ids": [object_id],
            "target_ref": target_ref,
            "layout_producer_node_id": producer_id,
            "repair_reason": (
                "held_path_collision"
                if held_path_failure
                else "prepared_placement_collision"
                if prepared_placement_collision
                else "post_placement_continuation_unreachable"
                if post_placement_continuation_failure
                else "placement_assessment_collision"
                if placeability_layout_failure
                else "stale_layout_artifact"
            ),
            "layout_reselection_attempt": max(
                1,
                int(repair_index) + 1,
            ),
        }
    )
    if layout_reselection_failure and failed_local_xy is not None:
        repair_params["failed_layout_local_xy"] = list(
            failed_local_xy
        )
    if stale_layout_failure:
        repair_params["stale_artifact_ref"] = target_ref
        policy_reason = _first_text(details.get("policy_reason"))
        if policy_reason is not None:
            repair_params["stale_artifact_reason"] = policy_reason

    destination_id = _first_text(
        *(
            (
                repair_params.get("destination_ids"),
                params.get("destination_ids"),
                parameter_participants.get("destination"),
                artifact.get("anchor_id"),
                getattr(diagnostic, "destination_id", None),
                details.get("placement_destination_id"),
                details.get("destination_id"),
            )
            if stale_layout_failure
            else (
                artifact.get("anchor_id"),
                getattr(diagnostic, "destination_id", None),
                details.get("placement_destination_id"),
                details.get("destination_id"),
                params.get("destination_ids"),
                parameter_participants.get("destination"),
            )
        ),
    )
    if destination_id is None:
        return None
    diagnostic_destination = _first_text(
        getattr(diagnostic, "destination_id", None),
        details.get("placement_destination_id"),
        details.get("destination_id"),
    )
    if (
        diagnostic_destination is not None
        and artifact.get("anchor_id") is not None
        and str(artifact.get("anchor_id")) != diagnostic_destination
    ):
        return None

    region_ref = _first_text(
        repair_params.get("staging_region_ref"),
        repair_params.get("region_ref"),
        params.get("staging_region_ref"),
        params.get("region_ref"),
        artifact.get("region_ref"),
    )
    if region_ref is None:
        return None
    reservation_group = _first_text(
        repair_params.get("reservation_group"),
        params.get("reservation_group"),
        artifact.get("reservation_group"),
    )
    if reservation_group is None:
        return None

    participants = _participants(repair_params.get("participants"))
    participants["object"] = [object_id]
    participants["destination"] = [destination_id]
    repair_params.update(
        {
            "destination_ids": [destination_id],
            "participants": participants,
            "relation": str(
                repair_params.get("relation")
                or params.get("relation")
                or "on_support"
            ),
            "region_ref": region_ref,
            "reservation_group": reservation_group,
            "lock_region_selection": True,
        }
    )
    if repair_params.get("staging_region_ref") is not None:
        repair_params["staging_region_ref"] = region_ref
    if repair_params.get("staging_id") is not None:
        repair_params["staging_id"] = destination_id

    continuation_scope_id = _first_text(
        producer_continuation_id,
        params.get("layout_continuation_scope_id"),
        repair_params.get("layout_continuation_scope_id"),
        artifact.get("continuation_scope_id"),
        artifact.get("continuation_node_id"),
        node.node_id,
    )
    assert continuation_scope_id is not None
    repair_params["layout_continuation_scope_id"] = (
        continuation_scope_id
    )

    exclusions = _unique_local_xy(
        _local_xy_values(params.get("excluded_local_xy")),
        _local_xy_values(repair_params.get("excluded_local_xy")),
        _local_xy_values(artifact.get("excluded_local_xy")),
        _previous_layout_exclusions(
            tree,
            parent_node_id=node.node_id,
            target_ref=target_ref,
        ),
        (
            (failed_local_xy,)
            if layout_reselection_failure and failed_local_xy is not None
            else ()
        ),
    )
    repair_params["excluded_local_xy"] = [
        [float(x), float(y)] for x, y in exclusions
    ]

    system_check = str(
        repair_params.get("system_check") or ""
    ).casefold()
    if system_check not in {"select_staging", "plan_region_layout"}:
        system_check = (
            "select_staging"
            if (
                repair_params.get("staging_region_ref") is not None
                or params.get("staging_region_ref") is not None
            )
            else "plan_region_layout"
        )
    repair_params["system_check"] = system_check
    task_type = str(template.get("task_type") or "")
    if not task_type:
        task_type = (
            "select_staging_region"
            if system_check == "select_staging"
            else "select_placement_space"
        )

    metadata = _mapping(template.get("metadata"))
    metadata.update(
        {
            "adapter_injected": True,
            "role": (
                "placement_layout_reselection_repair"
                if layout_reselection_failure
                else "stale_layout_republication_repair"
            ),
            "failed_target_ref": target_ref,
        }
    )
    if layout_reselection_failure and failed_local_xy is not None:
        metadata["failed_local_xy"] = list(failed_local_xy)
    if stale_layout_failure:
        metadata["stale_artifact_reason"] = repair_params.get(
            "stale_artifact_reason"
        )
    metadata = _ensure_layout_producer_contract(
        metadata,
        producer_node_id=producer_id,
        continuation_node_id=continuation_scope_id,
    )
    source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
    root_id = (
        str(source.get("root_id"))
        if isinstance(source, Mapping) and source.get("root_id")
        else node.node_id
    )
    return SimpleNamespace(
        node_id=f"{node.node_id}:reselect-placement-layout",
        parent_id=node.node_id,
        root_id=root_id,
        node_kind="repair",
        task_type=task_type,
        object_ref=copy.deepcopy(template.get("object_ref")),
        actor_ref=copy.deepcopy(template.get("actor_ref")),
        from_state=copy.deepcopy(template.get("from_state")),
        to_state=copy.deepcopy(template.get("to_state")),
        preconditions={},
        goal={},
        goal_scope="world",
        obligations=[],
        params=repair_params,
        children=[],
        child_policy="sequence",
        decomposer_ref=None,
        tool_ref=None,
        action_ref=None,
        retry_policy={
            "max_attempts": 1,
            "max_repairs": 0,
            "retry_on": [],
            "requires_new_world_revision": True,
        },
        resource_policy=copy.deepcopy(
            template.get("resource_policy")
            or {
                "claims": [],
                "exclusive": True,
                "allow_parallel": False,
            }
        ),
        origin="kernel_adapter_repair",
        metadata=metadata,
    )


def _repair_artifact(
    artifacts: Any,
    artifact_ref: str,
) -> dict[str, Any] | None:
    reader = getattr(artifacts, "artifact", None)
    if callable(reader):
        try:
            value = reader(str(artifact_ref))
        except Exception:
            value = None
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    task_reader = getattr(artifacts, "task", None)
    task = task_reader() if callable(task_reader) else None
    values = getattr(task, "artifacts", None)
    value = (
        values.get(str(artifact_ref))
        if isinstance(values, Mapping)
        else None
    )
    return (
        copy.deepcopy(dict(value))
        if isinstance(value, Mapping)
        else None
    )


def _tree_spec_payload(
    tree: Any,
    node_id: str,
) -> dict[str, Any]:
    nodes = getattr(tree, "nodes", None)
    if not isinstance(nodes, Mapping):
        return {}
    try:
        node = nodes[str(node_id)]
    except (KeyError, TypeError):
        return {}
    spec = getattr(node, "spec", node)
    to_dict = getattr(spec, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
        except Exception:
            value = None
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
    if isinstance(spec, Mapping):
        return copy.deepcopy(dict(spec))
    names = (
        "node_id",
        "parent_id",
        "root_id",
        "node_kind",
        "task_type",
        "object_ref",
        "actor_ref",
        "from_state",
        "to_state",
        "preconditions",
        "goal",
        "goal_scope",
        "obligations",
        "params",
        "children",
        "child_policy",
        "decomposer_ref",
        "tool_ref",
        "action_ref",
        "retry_policy",
        "resource_policy",
        "origin",
        "metadata",
    )
    return {
        name: copy.deepcopy(getattr(spec, name))
        for name in names
        if hasattr(spec, name)
    }


def _previous_layout_exclusions(
    tree: Any,
    *,
    parent_node_id: str,
    target_ref: str,
) -> tuple[tuple[float, float], ...]:
    nodes = getattr(tree, "nodes", None)
    if not isinstance(nodes, Mapping):
        return ()
    values: list[tuple[float, float]] = []
    for node in nodes.values():
        spec = getattr(node, "spec", node)
        parent_id = (
            spec.get("parent_id")
            if isinstance(spec, Mapping)
            else getattr(spec, "parent_id", None)
        )
        if str(parent_id or "") != str(parent_node_id):
            continue
        raw_params = (
            spec.get("params")
            if isinstance(spec, Mapping)
            else getattr(spec, "params", None)
        )
        if not isinstance(raw_params, Mapping):
            continue
        if str(raw_params.get("target_ref") or "") != str(target_ref):
            continue
        values.extend(
            _local_xy_values(raw_params.get("excluded_local_xy"))
        )
    return tuple(values)


def _ensure_layout_producer_contract(
    metadata: Mapping[str, Any],
    *,
    producer_node_id: str,
    continuation_node_id: str,
) -> dict[str, Any]:
    result = copy.deepcopy(dict(metadata))
    raw = result.get("artifact_produces")
    if isinstance(raw, Mapping):
        values = [copy.deepcopy(dict(raw))]
    elif isinstance(raw, list):
        values = [
            copy.deepcopy(dict(value))
            for value in raw
            if isinstance(value, Mapping)
        ]
    else:
        values = []
    has_layout_contract = any(
        str(value.get("artifact_kind") or "") == "layout_targets"
        and str(value.get("producer_node_id") or "")
        == str(producer_node_id)
        for value in values
    )
    if not has_layout_contract:
        values.append(
            {
                "schema": "task_artifact/1.0",
                "artifact_kind": "layout_targets",
                "producer_node_id": str(producer_node_id),
                "continuation_node_id": str(continuation_node_id),
                "ref_key": "target_ref",
                "required": True,
            }
        )
    result["artifact_produces"] = values
    return result


def _node_layout_contract(
    node: TaskNodeSpec,
    contract_key: str,
) -> dict[str, Any]:
    source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
    metadata = (
        source.get("metadata")
        if isinstance(source, Mapping)
        else None
    )
    return _layout_contract(
        _mapping(metadata),
        contract_key,
    )


def _layout_contract(
    metadata: Mapping[str, Any],
    contract_key: str,
    *,
    producer_node_id: str | None = None,
) -> dict[str, Any]:
    raw = metadata.get(contract_key)
    if isinstance(raw, Mapping):
        values = (raw,)
    elif isinstance(raw, (list, tuple)):
        values = tuple(
            value for value in raw if isinstance(value, Mapping)
        )
    else:
        values = ()
    for value in values:
        if str(value.get("artifact_kind") or "") != "layout_targets":
            continue
        if (
            producer_node_id is not None
            and str(value.get("producer_node_id") or "")
            != str(producer_node_id)
        ):
            continue
        return copy.deepcopy(dict(value))
    return {}


def _layout_contract_is_complete(
    contract: Mapping[str, Any],
    *,
    producer_node_id: str,
    continuation_node_id: str,
) -> bool:
    return (
        str(contract.get("artifact_kind") or "") == "layout_targets"
        and str(contract.get("producer_node_id") or "")
        == str(producer_node_id)
        and str(contract.get("continuation_node_id") or "")
        == str(continuation_node_id)
        and str(contract.get("ref_key") or "") == "target_ref"
        and contract.get("required") is True
    )


def _local_xy_values(
    value: Any,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    candidates = (
        (value,)
        if (
            len(value) == 2
            and not isinstance(value[0], (list, tuple, Mapping))
            and not isinstance(value[1], (list, tuple, Mapping))
        )
        else value
    )
    result: list[tuple[float, float]] = []
    for raw in candidates:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        try:
            point = (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError):
            continue
        if not all(isfinite(item) for item in point):
            continue
        result.append(point)
    return tuple(dict.fromkeys(result))


def _unique_local_xy(
    *groups: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], ...]:
    return tuple(
        dict.fromkeys(
            point
            for group in groups
            for point in group
        )
    )


def _repair_node_id(
    parent: TaskNodeSpec,
    repair_node: Any,
    rule_ref: str,
    repair_index: int,
) -> str:
    task_type = str(getattr(repair_node, "task_type", "repair") or "repair")
    payload = {
        "parent_node_id": parent.node_id,
        "repair_index": max(0, int(repair_index)),
        "rule_ref": str(rule_ref or "builtin"),
        "task_type": task_type,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", task_type.casefold()).strip("-")
    return (
        f"{parent.node_id}/repair-{max(0, int(repair_index))}/"
        f"{slug or 'repair'}-{digest}"
    )


def _diagnostic_id(
    node: TaskNodeSpec,
    diagnostic: Diagnostic,
    details: Mapping[str, Any],
) -> str:
    payload = {
        "node_id": node.node_id,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "details": _json_value(details),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"kernel-diag-{digest}"


def _participants(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(role): _text_list(
            spec.get("entity_ids")
            if isinstance(spec, Mapping)
            else spec
        )
        for role, spec in value.items()
    }


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        value = value.get("entity_ids") or value.get("entity_id")
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return list(dict.fromkeys(str(item) for item in value))


def _first_text(*values: Any) -> str | None:
    for value in values:
        items = _text_list(value)
        if items:
            return items[0]
    return None


def _optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=repr)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def _inject_relocation_continuation_context(
    repair_node: Any,
    diagnostic: Any,
) -> None:
    if str(getattr(repair_node, "task_type", "")).casefold() != (
        "relocate_blocker"
    ):
        return
    details = _mapping(getattr(diagnostic, "details", {}))
    result_details = _mapping(
        _mapping(details.get("result")).get("details")
    )
    anchor = _finite_pose3(
        details.get("baseline_pose")
        or result_details.get("baseline_pose")
    )
    raw_rejections = (
        details.get("candidate_rejections")
        or result_details.get("candidate_rejections")
    )
    raw_params = getattr(repair_node, "params", None)
    if not isinstance(raw_params, dict):
        raw_params = _mapping(raw_params)
        setattr(repair_node, "params", raw_params)
    anchor_key, goals_key = RELOCATION_CONTINUATION_CONTEXT_KEYS
    raw_params.pop(anchor_key, None)
    raw_params.pop(goals_key, None)
    relocated_ids = set(
        _text_list(raw_params.get("object_ids"))
        + _text_list(raw_params.get("object_id"))
        + _participants(raw_params.get("participants")).get(
            "object",
            [],
        )
    )
    object_ref = getattr(repair_node, "object_ref", None)
    if isinstance(object_ref, Mapping):
        relocated_ids.update(_text_list(object_ref.get("entity_id")))

    goals: list[tuple[float, float, float]] = []
    if relocated_ids and isinstance(raw_rejections, (list, tuple)):
        for rejection in raw_rejections:
            if not isinstance(rejection, Mapping):
                continue
            if str(rejection.get("phase") or "").casefold() not in (
                _RELOCATION_CONTINUATION_GOAL_PHASES
            ):
                continue
            if not relocated_ids.intersection(
                _rejection_blocking_entity_ids(rejection)
            ):
                continue
            goal = _finite_pose3(rejection.get("goal"))
            if goal is not None and goal not in goals:
                goals.append(goal)
    if anchor is None or not goals:
        return

    raw_params[anchor_key] = list(anchor)
    raw_params[goals_key] = [list(goal) for goal in goals]


def _inject_relocation_blocker_set_context(
    repair_node: Any,
    diagnostic: Any,
) -> None:
    if str(getattr(repair_node, "task_type", "")).casefold() != (
        "relocate_blocker"
    ):
        return
    details = _mapping(getattr(diagnostic, "details", {}))
    result_details = _mapping(
        _mapping(details.get("result")).get("details")
    )
    blocker_set = _text_list(
        details.get("selected_sufficient_blocker_set")
        or details.get("selected_minimal_blocker_set")
        or result_details.get("selected_sufficient_blocker_set")
        or result_details.get("selected_minimal_blocker_set")
    )
    if len(blocker_set) <= 1:
        return

    raw_params = getattr(repair_node, "params", None)
    if not isinstance(raw_params, dict):
        raw_params = _mapping(raw_params)
        setattr(repair_node, "params", raw_params)
    relocated_ids = _text_list(
        raw_params.get("object_ids")
        or raw_params.get("object_id")
        or _participants(raw_params.get("participants")).get(
            "manipuland"
        )
        or getattr(repair_node, "object_ref", None)
    )
    selected_member = next(
        (
            entity_id
            for entity_id in blocker_set
            if entity_id in relocated_ids
        ),
        None,
    )
    if selected_member is None:
        return

    remaining_ids = [
        entity_id
        for entity_id in blocker_set
        if entity_id != selected_member
    ]
    witness = _mapping(
        details.get("selected_blocker_set_witness")
        or result_details.get("selected_blocker_set_witness")
    )
    signature_payload = {
        "entity_ids": blocker_set,
        "goal": witness.get("goal"),
        "code": witness.get("code"),
    }
    blocker_set_id = hashlib.sha256(
        json.dumps(
            signature_payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    raw_params.update(
        {
            "blocker_set_id": (
                f"counterfactual-blocker-set-{blocker_set_id}"
            ),
            "blocker_set_entity_ids": list(blocker_set),
            "selected_blocker_set_member_id": selected_member,
            "remaining_blocker_entity_ids": remaining_ids,
            "blocker_set_recovery_semantics": (
                "partial_relocation_then_replan"
            ),
            "blocker_set_minimality_proven": bool(
                details.get("minimality_proven")
                or result_details.get("minimality_proven")
            ),
            "blocker_set_witness": copy.deepcopy(witness),
        }
    )


def _inject_relocation_protection_context(
    repair_node: Any,
    diagnostic: Any,
    failed_node: TaskNodeSpec,
) -> None:
    if str(getattr(repair_node, "task_type", "")).casefold() != (
        "relocate_blocker"
    ):
        return

    raw_params = getattr(repair_node, "params", None)
    if not isinstance(raw_params, dict):
        raw_params = _mapping(raw_params)
        setattr(repair_node, "params", raw_params)

    details = _mapping(getattr(diagnostic, "details", {}))
    result_details = _mapping(
        _mapping(details.get("result")).get("details")
    )
    diagnostic_params = _mapping(details.get("params"))
    result_params = _mapping(result_details.get("params"))
    failed_params = failed_node.parameters

    protected_ids: list[str] = []
    for params in (failed_params, diagnostic_params, result_params):
        protected_ids.extend(
            _text_list(params.get(PROTECTED_RELOCATION_ENTITY_IDS_KEY))
        )
    for params in (diagnostic_params, result_params, failed_params):
        for key in (
            "target_id",
            "reference_id",
            "reference_ids",
            "object_id",
            "object_ids",
        ):
            protected_ids.extend(_text_list(params.get(key)))
        participants = _participants(params.get("participants"))
        for role in ("reference", "object", "manipuland"):
            protected_ids.extend(participants.get(role, []))

    protected_ids.extend(_text_list(raw_params.get("object_ids")))
    protected_ids.extend(_text_list(raw_params.get("object_id")))
    protected_ids.extend(
        _participants(raw_params.get("participants")).get(
            "manipuland",
            [],
        )
    )
    protected_ids.extend(
        _participants(raw_params.get("participants")).get("object", [])
    )
    object_ref = getattr(repair_node, "object_ref", None)
    if isinstance(object_ref, Mapping):
        protected_ids.extend(_text_list(object_ref.get("entity_id")))

    normalized = [
        entity_id
        for entity_id in dict.fromkeys(protected_ids)
        if entity_id and entity_id != "robot_1"
    ]
    if normalized:
        raw_params[PROTECTED_RELOCATION_ENTITY_IDS_KEY] = normalized


def _inherit_generic_repair_continuation_context(
    repair_node: Any,
    failed_node: TaskNodeSpec,
) -> None:
    task_type = str(
        getattr(repair_node, "task_type", "")
    ).casefold()
    if task_type not in _GENERIC_CONTINUATION_REPAIR_TASK_TYPES:
        return

    source_params = failed_node.parameters
    if not all(
        source_params.get(key) is not None
        for key in RELOCATION_CONTINUATION_CONTEXT_KEYS
    ):
        return

    raw_params = getattr(repair_node, "params", None)
    if not isinstance(raw_params, dict):
        raw_params = _mapping(raw_params)
        setattr(repair_node, "params", raw_params)
    for key in RELOCATION_CONTINUATION_CONTEXT_KEYS:
        raw_params[key] = copy.deepcopy(source_params[key])


def _rejection_blocking_entity_ids(
    rejection: Mapping[str, Any],
) -> tuple[str, ...]:
    blocker_keys = {
        "blocker_id",
        "blocker_ids",
        "blocking_entity_ids",
        "colliding_entity_ids",
        "direct_blocking_entity_ids",
        "verified_route_blocking_entity_ids",
    }
    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key) in blocker_keys:
                    values.extend(_text_list(item))
                elif isinstance(item, (Mapping, list, tuple)):
                    visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, (Mapping, list, tuple)):
                    visit(item)

    visit(rejection)
    return tuple(dict.fromkeys(values))


def _finite_pose3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        pose = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, OverflowError):
        return None
    return pose if all(isfinite(component) for component in pose) else None


def _repair_manipulation_targets_are_admissible(
    repair_node: Any,
    runtime: Any,
) -> bool:
    task_type = str(
        getattr(repair_node, "task_type", "")
    ).casefold()
    if task_type not in {"relocate_blocker", "transport"}:
        return True
    params = _mapping(getattr(repair_node, "params", {}))
    participants = _participants(params.get("participants"))
    object_ref = getattr(repair_node, "object_ref", None)
    entity_ids = _text_list(
        params.get("object_ids")
        or params.get("object_id")
        or participants.get("manipuland")
        or participants.get("object")
        or object_ref
    )
    return bool(entity_ids) and all(
        relocatable_by_grasp(runtime, entity_id)
        for entity_id in entity_ids
    )


def _normalize_region_repair(
    repair_node: Any,
    diagnostic: Any,
    runtime: Any,
) -> tuple[Mapping[str, Any], ...] | None:
    if str(getattr(repair_node, "task_type", "")).casefold() not in {
        "clear_support_region",
        "clear_placement_region",
    }:
        return ()
    code = str(getattr(diagnostic, "code", "")).upper()
    if code not in {
        "REGION_OCCUPIED",
        "PLACEMENT_COLLISION",
        "SOURCE_REGION_NOT_EMPTY",
    }:
        return ()

    raw_params = getattr(repair_node, "params", None)
    if not isinstance(raw_params, dict):
        raw_params = (
            copy.deepcopy(dict(raw_params))
            if isinstance(raw_params, Mapping)
            else {}
        )
        setattr(repair_node, "params", raw_params)
    owner_id = _first_text(
        raw_params.get("region_owner_id"),
        raw_params.get("destination_id"),
        raw_params.get("destination_ids"),
        getattr(diagnostic, "destination_id", None),
    )
    target_id = _first_text(
        raw_params.get("target_object_id"),
        getattr(diagnostic, "object_id", None),
    )
    details = _mapping(getattr(diagnostic, "details", {}))
    result = _mapping(details.get("result"))
    assessment = _mapping(
        details.get("assessment") or result.get("assessment")
    )
    blockers = tuple(
        dict.fromkeys(
            str(value)
            for value in (
                getattr(diagnostic, "blocking_entity_ids", ())
                or details.get("blocking_entity_ids")
                or details.get("occupant_ids")
                or assessment.get("blocking_entity_ids")
                or assessment.get("occupant_ids")
                or raw_params.get("include_entity_ids")
                or ()
            )
            if str(value)
            and str(value)
            not in {
                str(owner_id or ""),
                str(target_id or ""),
                "robot_1",
            }
        )
    )
    if owner_id is None or not blockers:
        return None

    clearable_blockers = relocatable_entity_ids(runtime, blockers)
    if not clearable_blockers:
        return None
    rejected_blockers = non_relocatable_entity_ids(runtime, blockers)

    raw_params["include_entity_ids"] = list(clearable_blockers)
    if rejected_blockers:
        raw_params["non_relocatable_blocking_entity_ids"] = list(
            rejected_blockers
        )
    exclusions = _text_list(raw_params.get("exclude_entity_ids"))
    if target_id is not None:
        exclusions.append(target_id)
    raw_params["exclude_entity_ids"] = list(dict.fromkeys(exclusions))
    obligations = tuple(
        {
            "predicate": "occupies_support_region",
            "participants": {
                "subject": [blocker_id],
                "region_owner": [owner_id],
            },
            "desired_value": "false",
        }
        for blocker_id in clearable_blockers
    )
    setattr(
        repair_node,
        "goal",
        copy.deepcopy(
            obligations[0]
            if len(obligations) == 1
            else {"op": "and", "args": list(obligations)}
        ),
    )
    return obligations


def _merge_mapping_values(
    *groups: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for value in (item for group in groups for item in group):
        plain = copy.deepcopy(dict(value))
        key = json.dumps(
            _json_value(plain),
            sort_keys=True,
            separators=(",", ":"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(plain)
    return tuple(result)


__all__ = ["HarnessRepairResolver"]


def _requires_reconciliation(
    diagnostic: Diagnostic,
    details: Mapping[str, Any],
) -> bool:
    if diagnostic.code == "OUTCOME_UNKNOWN":
        return True
    requires_reconciliation = details.get(
        "requires_reconciliation"
    )
    if requires_reconciliation is True:
        return True
    if details.get("physical_outcome_known") is False:
        return True
    if details.get("physical_outcome_known") is True:
        return False
    if details.get("physical_dispatch_started") is True:
        return True
    dispatch_stage = str(
        details.get("dispatch_stage") or ""
    ).casefold()
    if dispatch_stage in {
        "prior_dispatch",
        "execution",
        "completion",
        "dispatch_unknown",
    }:
        return True
    if requires_reconciliation is False:
        return False
    if details.get("physical_dispatch_started") is False:
        return False
    if dispatch_stage in {"not_started", "reservation"}:
        return False
    termination = str(details.get("termination") or "").lower()
    effect_state = str(details.get("effect_state") or "").lower()
    verification = str(details.get("verification") or "").lower()
    return (
        termination in {"outcome_unknown", "timed_out"}
        or effect_state == "unknown"
        or verification == "unknown"
    )
