from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.contracts import SemanticCheck, SemanticState
from task_recursive_tree.task.model import TaskNodeRuntime, TaskNodeSpec

from .relocatability import relocatable_by_grasp


GEMINI_ER2_METADATA_KEY = "__gemini_er2__"
SlotBindingsProvider = Callable[
    [TaskNodeSpec, TaskNodeRuntime], Mapping[str, list[str]]
]


@dataclass(frozen=True)
class _ConditionIssue:
    formula: Mapping[str, Any]
    state: SemanticState
    result: Any


class GeminiER2NodeSemantics:
    """Evaluate translated Harness formulas against the live Harness runtime."""

    def __init__(
        self,
        *,
        runtime: Any,
        slot_bindings_provider: SlotBindingsProvider | None = None,
    ) -> None:
        self.runtime = runtime
        self._slot_bindings_provider = slot_bindings_provider
        self._baseline_request_ids = {
            request_id
            for result in _runtime_outcomes(runtime)
            for request_id in (_optional_text(result.get("request_id")),)
            if request_id is not None
        }

    def evaluate_goal(
        self,
        node: TaskNodeSpec,
        runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        confirmed = self._confirmed_recent_goal(node, runtime)
        if confirmed is not None:
            return confirmed
        return self._evaluate("goal", node, runtime)

    def evaluate_preconditions(
        self,
        node: TaskNodeSpec,
        runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        return self._evaluate("preconditions", node, runtime)

    def evaluate_postconditions(
        self,
        node: TaskNodeSpec,
        runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        current_result = runtime.adapter_state.get("last_result")
        confirmed = (
            self._confirmed_transaction_postconditions(
                node,
                runtime,
            )
            if isinstance(current_result, Mapping)
            else self._confirmed_recent_goal(node, runtime)
        )
        if confirmed is not None:
            return confirmed
        return self._evaluate("goal", node, runtime)

    def _confirmed_transaction_postconditions(
        self,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        if not isinstance(source, Mapping):
            return None
        formula = source.get("goal")
        if not isinstance(formula, Mapping) or not formula:
            return None

        adapter_state = node_runtime.adapter_state
        result = adapter_state.get("last_result")
        if not isinstance(result, Mapping):
            return None
        last_request_id = _optional_text(
            adapter_state.get("last_request_id")
        )
        return self._confirmed_result_goal(
            node,
            node_runtime,
            result,
            expected_request_id=last_request_id,
            evidence_source="current_node_transaction",
        )

    def _confirmed_recent_goal(
        self,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        result = self._latest_runtime_result()
        if result is None:
            return None
        return self._confirmed_result_goal(
            node,
            node_runtime,
            result,
            expected_request_id=None,
            evidence_source="latest_task_transaction",
        )

    def _latest_runtime_result(self) -> Mapping[str, Any] | None:
        for result in reversed(_runtime_outcomes(self.runtime)):
            request_id = _optional_text(result.get("request_id"))
            if (
                request_id is not None
                and request_id not in self._baseline_request_ids
            ):
                return result
        return None

    def _confirmed_result_goal(
        self,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
        result: Mapping[str, Any],
        *,
        expected_request_id: str | None,
        evidence_source: str,
    ) -> SemanticCheck | None:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        if not isinstance(source, Mapping):
            return None
        formula = source.get("goal")
        if not isinstance(formula, Mapping) or not formula:
            return None
        if not _confirmed_physical_result(result):
            return None

        result_request_id = _optional_text(result.get("request_id"))
        if (
            expected_request_id is not None
            and result_request_id != expected_request_id
        ):
            return None
        effects = tuple(
            effect
            for effect in (result.get("effects") or ())
            if isinstance(effect, Mapping)
        )
        if not effects:
            return None
        try:
            slot_bindings = self._slot_bindings(node, node_runtime)
            resolved = _resolve_formula(formula, slot_bindings)
            residual_formula, matched_effect_ids = (
                _residual_formula_after_confirmed_effects(
                    resolved,
                    effects,
                )
            )
        except Exception:
            return None
        if not matched_effect_ids:
            return None

        transaction_evidence = {
            "request_id": result_request_id,
            "transaction_id": _optional_text(
                result.get("transaction_id")
            ),
            "source": evidence_source,
            "matched_effect_ids": list(matched_effect_ids),
            "evidence_refs": list(
                dict.fromkeys(
                    str(ref)
                    for effect in effects
                    for ref in (effect.get("evidence_refs") or ())
                )
            ),
        }
        if residual_formula is None:
            return SemanticCheck(
                state=SemanticState.SATISFIED,
                evidence={
                    "value": "true",
                    "confidence": 1.0,
                    "evidence_refs": transaction_evidence[
                        "evidence_refs"
                    ],
                    "derivation": (
                        "confirmed_physical_transaction_effect"
                    ),
                    "formula_kind": "postconditions",
                    **transaction_evidence,
                    "slot_bindings": {
                        slot_id: list(entity_ids)
                        for slot_id, entity_ids in slot_bindings.items()
                    },
                },
            )

        live_check = self._evaluate_formula(
            "goal",
            node,
            node_runtime,
            residual_formula,
        )
        if live_check is None:
            return None
        return SemanticCheck(
            state=live_check.state,
            diagnostic=live_check.diagnostic,
            evidence={
                **dict(live_check.evidence),
                "derivation": (
                    "confirmed_physical_transaction_effect_plus_live"
                ),
                "transaction_effects": transaction_evidence,
                "matched_effect_ids": list(matched_effect_ids),
                "residual_formula": residual_formula,
            },
        )

    def evaluate_obligations(
        self,
        node: TaskNodeSpec,
        runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        obligations = self.collect_obligations(node, runtime)
        if not obligations:
            return None

        first_unknown: SemanticCheck | None = None
        for index, formula in enumerate(obligations):
            check = self._evaluate_formula(
                "obligations",
                node,
                runtime,
                formula,
            )
            if check is None:
                continue
            evidence = {
                **dict(check.evidence),
                "obligation_index": index,
                "obligation_count": len(obligations),
            }
            check = SemanticCheck(
                state=check.state,
                diagnostic=check.diagnostic,
                evidence=evidence,
            )
            if check.state is SemanticState.UNSATISFIED:
                return check
            if (
                check.state is SemanticState.UNKNOWN
                and first_unknown is None
            ):
                first_unknown = check
        if first_unknown is not None:
            return first_unknown
        return SemanticCheck(
            SemanticState.SATISFIED,
            evidence={"obligation_count": len(obligations)},
        )

    def collect_obligations(
        self,
        node: TaskNodeSpec,
        runtime: TaskNodeRuntime,
    ) -> tuple[Mapping[str, Any], ...]:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        static_values = (
            source.get("obligations", ())
            if isinstance(source, Mapping)
            else ()
        )
        if isinstance(static_values, Mapping):
            static_values = (static_values,)
        if not isinstance(static_values, (list, tuple)):
            static_values = ()

        result: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for value in (*static_values, *runtime.active_obligations):
            if not isinstance(value, Mapping):
                continue
            plain = copy.deepcopy(dict(value))
            key = json.dumps(
                plain,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(plain)
        return tuple(result)

    def _evaluate(
        self,
        formula_kind: str,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
    ) -> SemanticCheck | None:
        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        if not isinstance(source, Mapping):
            return None
        formula = source.get(formula_kind)
        if formula is None or formula == {}:
            return None
        return self._evaluate_formula(
            formula_kind,
            node,
            node_runtime,
            formula,
        )

    def _evaluate_formula(
        self,
        formula_kind: str,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
        formula: Any,
    ) -> SemanticCheck | None:
        if not isinstance(formula, Mapping):
            return _unknown_check(
                code="GEMINI_ER2_FORMULA_INVALID",
                message=(
                    f"Harness {formula_kind} formula is not a mapping "
                    f"for {node.node_id}"
                ),
                evidence={
                    "formula_kind": formula_kind,
                    "formula_type": type(formula).__name__,
                },
                retryable=False,
            )

        try:
            slot_bindings = self._slot_bindings(node, node_runtime)
            result = self._evaluate_raw(formula, slot_bindings)
        except Exception as exc:
            return _unknown_check(
                code="GEMINI_ER2_SEMANTIC_EVALUATION_ERROR",
                message=(
                    f"Harness {formula_kind} evaluation failed "
                    f"for {node.node_id}: {exc}"
                ),
                evidence={
                    "formula_kind": formula_kind,
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                },
                retryable=True,
            )

        state = _semantic_state(getattr(result, "value", None))
        issue = None
        if state is not SemanticState.SATISFIED:
            try:
                issue = self._first_condition_issue(
                    formula,
                    slot_bindings,
                    result=result,
                )
            except Exception as exc:
                return _unknown_check(
                    code="GEMINI_ER2_SEMANTIC_EVALUATION_ERROR",
                    message=(
                        f"Harness {formula_kind} failure localization "
                        f"failed for {node.node_id}: {exc}"
                    ),
                    evidence={
                        "formula_kind": formula_kind,
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                    },
                    retryable=True,
                )

        evidence_result = issue.result if issue is not None else result
        evidence = _result_evidence(evidence_result)
        evidence.update(
            {
                "formula_kind": formula_kind,
                "slot_bindings": {
                    slot_id: list(entity_ids)
                    for slot_id, entity_ids in slot_bindings.items()
                },
            }
        )
        occupancy_override = self._held_target_occupancy_override(
            node,
            formula_kind,
            issue,
            slot_bindings,
        )
        if occupancy_override is not None:
            evidence["region_occupancy_override"] = occupancy_override
            return SemanticCheck(
                state=SemanticState.SATISFIED,
                evidence=evidence,
            )
        diagnostic = (
            self._diagnostic_for_issue(
                node,
                formula_kind,
                issue,
                slot_bindings,
                evidence,
            )
            if issue is not None
            else None
        )
        return SemanticCheck(
            state=state,
            diagnostic=diagnostic,
            evidence=evidence,
        )

    def _held_target_occupancy_override(
        self,
        node: TaskNodeSpec,
        formula_kind: str,
        issue: _ConditionIssue | None,
        slot_bindings: Mapping[str, list[str]],
    ) -> dict[str, Any] | None:
        if (
            issue is None
            or issue.state is not SemanticState.UNSATISFIED
            or formula_kind != "preconditions"
        ):
            return None
        formula = _resolve_formula(issue.formula, slot_bindings)
        if str(formula.get("predicate") or "") != "support_region_empty":
            return None
        parameters = _mapping(formula.get("parameters"))
        if str(parameters.get("semantic_role") or "") == "source_state":
            return None
        participants = _participants(formula.get("participants"))
        owner_id = _first_participant(participants, "region_owner")
        if owner_id is None:
            return None
        target_ids = _node_entity_ids(
            node,
            "manipuland",
            "object",
            "placement_object",
        )
        destination_ids = _node_entity_ids(node, "destination")
        held_id = _held_entity_id(getattr(self.runtime, "scene", None))
        if (
            held_id is None
            or held_id not in target_ids
            or destination_ids
            and owner_id not in destination_ids
        ):
            return None
        blockers = self._support_occupants(owner_id)
        if not blockers or set(blockers) - {held_id}:
            return None
        return {
            "reason": "held_placement_target_is_not_an_external_blocker",
            "target_id": held_id,
            "region_owner_id": owner_id,
            "blocking_entity_ids": blockers,
        }

    def _evaluate_raw(
        self,
        formula: Mapping[str, Any],
        slot_bindings: Mapping[str, list[str]],
    ) -> Any:
        return self.runtime.predicates.evaluate_formula(
            copy.deepcopy(dict(formula)),
            self.runtime.world,
            self.runtime.scene,
            self.runtime.perception,
            dict(slot_bindings),
        )

    def _first_condition_issue(
        self,
        formula: Mapping[str, Any],
        slot_bindings: Mapping[str, list[str]],
        *,
        result: Any | None = None,
    ) -> _ConditionIssue | None:
        current_result = (
            result
            if result is not None
            else self._evaluate_raw(formula, slot_bindings)
        )
        state = _semantic_state(getattr(current_result, "value", None))
        if state is SemanticState.SATISFIED:
            return None

        if str(formula.get("op") or "").lower() == "and":
            issues: list[_ConditionIssue] = []
            for value in formula.get("args", ()) or ():
                if not isinstance(value, Mapping):
                    continue
                issue = self._first_condition_issue(
                    value,
                    slot_bindings,
                )
                if issue is not None:
                    issues.append(issue)
            false_issues = [
                issue
                for issue in issues
                if issue.state is SemanticState.UNSATISFIED
            ]
            if false_issues:
                return false_issues[0]
            if issues:
                return issues[0]
        return _ConditionIssue(
            formula=copy.deepcopy(dict(formula)),
            state=state,
            result=current_result,
        )

    def _diagnostic_for_issue(
        self,
        node: TaskNodeSpec,
        formula_kind: str,
        issue: _ConditionIssue,
        slot_bindings: Mapping[str, list[str]],
        evidence: Mapping[str, Any],
    ) -> Diagnostic:
        formula = _resolve_formula(issue.formula, slot_bindings)
        predicate = str(formula.get("predicate") or "")
        parameters = _mapping(formula.get("parameters"))
        participants = _participants(formula.get("participants"))
        desired_value = str(formula.get("desired_value", "true"))
        actual_value = _truth_text(issue.state)
        source_state = (
            predicate == "support_region_empty"
            and parameters.get("semantic_role") == "source_state"
        )
        negative_occupancy = (
            predicate == "occupies_support_region"
            and desired_value.casefold() == "false"
        )

        if issue.state is SemanticState.UNKNOWN:
            code = (
                "PERCEPTION_INSUFFICIENT"
                if predicate in {
                    "support_region_empty",
                    "occupies_support_region",
                }
                else "SEMANTIC_EVIDENCE_INSUFFICIENT"
            )
            kind = "evidence_unknown"
        else:
            kind = (
                "precondition_false"
                if formula_kind == "preconditions"
                else "obligation_refuted"
                if formula_kind == "obligations"
                else "goal_refuted"
            )
            code = {
                "reachable_by": "OBJECT_UNREACHABLE",
                "interaction_pose_achieved": "OBJECT_UNREACHABLE",
                "placement_pose_reachable": "NO_REACHABLE_POSE",
                "placement_execution_ready": "INTERACTION_POSE_BLOCKED",
                "robot_clear_of_placement_candidate": (
                    "SELF_OCCUPANCY_BLOCKS_PLACEMENT"
                ),
                "graspable_by": "NOT_INTERACTABLE",
                "attached_to_any_end_effector": "GRASP_FAILED",
                "not_attached_to_other_effector": "END_EFFECTOR_OCCUPIED",
                "end_effector_available": "END_EFFECTOR_OCCUPIED",
                "fresh_geometry": "STALE_ARTIFACT",
                "support_region_empty": "REGION_OCCUPIED",
                "occupies_support_region": "REGION_OCCUPIED",
                "path_completed": "PATH_BLOCKED",
                "safety_clear": "SAFETY_REJECTED",
            }.get(
                predicate,
                "GOAL_REFUTED"
                if formula_kind == "goal"
                else "CONDITION_FALSE",
            )
            if source_state:
                code = "SOURCE_REGION_NOT_EMPTY"
            elif negative_occupancy:
                code = "REGION_OCCUPIED"

        refs = _formula_entity_ids(formula)
        blockers: list[str] = []
        params: dict[str, Any] = {}
        owner = _first_participant(participants, "region_owner")
        if predicate == "support_region_empty" and owner:
            if issue.state is SemanticState.UNSATISFIED:
                blockers = self._support_occupants(owner)
            params[
                "source_id" if source_state else "destination_id"
            ] = owner
        if negative_occupancy:
            subject = _first_participant(participants, "subject")
            if owner:
                params["destination_id"] = owner
            if subject:
                blockers = [subject]
        for key in ("semantic_role", "repair_policy"):
            if parameters.get(key) is not None:
                params[key] = copy.deepcopy(parameters[key])

        repair_policy = str(parameters.get("repair_policy") or "")
        repairable = (
            issue.state is SemanticState.UNSATISFIED
            and repair_policy != "user_confirmation"
            and code in {
                "SOURCE_REGION_NOT_EMPTY",
                "REGION_OCCUPIED",
                "OBJECT_UNREACHABLE",
                "NO_REACHABLE_POSE",
                "INTERACTION_POSE_BLOCKED",
                "END_EFFECTOR_OCCUPIED",
                "PATH_BLOCKED",
                "STALE_ARTIFACT",
                "SELF_OCCUPANCY_BLOCKS_PLACEMENT",
            }
        )
        world_revision = int(
            getattr(self.runtime.world, "revision", 0) or 0
        )
        relevant_read_set = self._read_set(refs)
        state_fingerprint = _fingerprint(
            "semantic-state",
            {
                "formula": formula,
                "actual_value": actual_value,
                "blocking_entity_ids": blockers,
                "world_revision": world_revision,
            },
        )
        problem_signature = _fingerprint(
            "semantic-problem",
            {
                "node_id": node.node_id,
                "code": code,
                "predicate": predicate,
                "participants": participants,
                "desired_value": desired_value,
                "blocking_entity_ids": blockers,
            },
        )
        cause = f"{predicate or 'formula'}={actual_value}"
        if source_state and blockers:
            cause = f"source support region is occupied by {blockers}"
        elif negative_occupancy and blockers:
            cause = (
                "persistent support-region exclusion was violated by "
                f"{blockers}"
            )

        evidence_refs = [
            str(value)
            for value in evidence.get("evidence_refs", ()) or ()
        ]
        predicate_details = _mapping(evidence.get("details"))
        details = {
            "kind": kind,
            "phase": formula_kind,
            "failed_predicate": predicate or None,
            "failed_participants": participants,
            "participants": participants,
            "expected_value": desired_value,
            "actual_value": actual_value,
            "witness_entity_ids": refs,
            "blocking_entity_ids": blockers,
            "affected_refs": refs,
            "world_revision": world_revision,
            "relevant_read_set": relevant_read_set,
            "repair_scope_id": node.node_id,
            "problem_signature": problem_signature,
            "semantic_state_fingerprint": state_fingerprint,
            "evidence_refs": evidence_refs,
            "derivation": str(
                evidence.get("derivation") or "gemini_er2_semantics"
            ),
            "cause": cause,
            "recoverability": (
                "repairable"
                if repairable
                else "retryable"
                if issue.state is SemanticState.UNKNOWN
                else "human_required"
                if repair_policy == "user_confirmation"
                else "impossible"
            ),
            "recommended_repairs": (
                ["clear_support_region"]
                if code in {
                    "SOURCE_REGION_NOT_EMPTY",
                    "REGION_OCCUPIED",
                }
                else []
            ),
            "params": params,
            "failed_formula": formula,
        }
        if predicate_details:
            details["predicate_details"] = predicate_details
        return Diagnostic(
            code=code,
            message=cause,
            details=details,
            retryable=issue.state is SemanticState.UNKNOWN,
            repairable=repairable,
        )

    def _support_occupants(self, owner_id: str) -> list[str]:
        perception = self.runtime.perception
        catalog = getattr(perception, "catalog", {})
        if not isinstance(catalog, Mapping):
            return []

        blockers: list[str] = []
        support_of = getattr(perception, "support_of", None)
        evaluate = getattr(self.runtime.predicates, "evaluate", None)
        scene_bodies = getattr(getattr(perception, "scene", None), "body", {})
        for entity_id, metadata in sorted(
            catalog.items(),
            key=lambda item: str(item[0]),
        ):
            entity_id = str(entity_id)
            meta = metadata if isinstance(metadata, Mapping) else {}
            if (
                entity_id == owner_id
                or str(meta.get("kind") or "") == "workspace"
            ):
                continue
            body = meta.get("body")
            if (
                body
                and isinstance(scene_bodies, Mapping)
                and body not in scene_bodies
            ):
                continue
            if not relocatable_by_grasp(self.runtime, entity_id):
                continue

            directly_supported = False
            if callable(support_of):
                try:
                    directly_supported = (
                        str(support_of(entity_id)) == owner_id
                    )
                except Exception:
                    directly_supported = False
            if directly_supported:
                blockers.append(entity_id)
                continue

            if not callable(evaluate):
                continue
            try:
                result = evaluate(
                    "occupies_support_region",
                    self.runtime.world,
                    self.runtime.scene,
                    perception,
                    {
                        "subject": [entity_id],
                        "region_owner": [owner_id],
                    },
                    {},
                )
            except Exception:
                continue
            if _semantic_state(getattr(result, "value", None)) is (
                SemanticState.SATISFIED
            ):
                blockers.append(entity_id)
        return list(dict.fromkeys(blockers))

    def _read_set(self, entity_ids: list[str]) -> dict[str, int]:
        reader = getattr(self.runtime.world, "read_set_for", None)
        if not callable(reader):
            return {}
        try:
            return {
                str(key): int(value)
                for key, value in dict(
                    reader(
                        entities=entity_ids,
                        relations=[],
                        tasks=[],
                        interaction=True,
                        safety=True,
                        frame_graph=True,
                    )
                ).items()
            }
        except Exception:
            return {}

    def _slot_bindings(
        self,
        node: TaskNodeSpec,
        node_runtime: TaskNodeRuntime,
    ) -> dict[str, list[str]]:
        if self._slot_bindings_provider is not None:
            supplied = self._slot_bindings_provider(node, node_runtime)
            return _normalize_slot_bindings(supplied)

        source = node.parameters.get(GEMINI_ER2_METADATA_KEY)
        tree_metadata = (
            source.get("tree")
            if isinstance(source, Mapping)
            and isinstance(source.get("tree"), Mapping)
            else {}
        )
        task_id = tree_metadata.get("task_id")
        if task_id is None:
            task_id = getattr(self.runtime, "_current_task_id", None)

        world = self.runtime.world
        tasks = getattr(world, "tasks", {})
        task = (
            tasks.get(str(task_id))
            if task_id is not None and isinstance(tasks, Mapping)
            else None
        )
        if task is None and isinstance(tasks, Mapping) and len(tasks) == 1:
            task = next(iter(tasks.values()))
        slots = getattr(task, "slots", {}) if task is not None else {}
        if not isinstance(slots, Mapping):
            return {}

        bindings: dict[str, list[str]] = {}
        for slot_id, slot in slots.items():
            if isinstance(slot, Mapping):
                values = slot.get("bound_entity_ids", ())
            else:
                values = getattr(slot, "bound_entity_ids", ())
            bindings[str(slot_id)] = [
                str(entity_id) for entity_id in (values or ())
            ]
        return bindings


def _semantic_state(value: Any) -> SemanticState:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return (
            SemanticState.SATISFIED
            if value
            else SemanticState.UNSATISFIED
        )
    text = str(value).strip().lower() if value is not None else "unknown"
    if text in {"true", "1", "confirmed", "satisfied"}:
        return SemanticState.SATISFIED
    if text in {"false", "0", "refuted", "unsatisfied"}:
        return SemanticState.UNSATISFIED
    return SemanticState.UNKNOWN


def _truth_text(state: SemanticState) -> str:
    return {
        SemanticState.SATISFIED: "true",
        SemanticState.UNSATISFIED: "false",
        SemanticState.UNKNOWN: "unknown",
    }[state]


def _optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value)


def _result_evidence(result: Any) -> dict[str, Any]:
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return copy.deepcopy(dict(payload))

    value = getattr(result, "value", None)
    if isinstance(value, Enum):
        value = value.value
    return {
        "value": value,
        "confidence": getattr(result, "confidence", None),
        "evidence_refs": list(getattr(result, "evidence", ()) or ()),
        "derivation": getattr(result, "derivation", None),
        "details": copy.deepcopy(
            dict(getattr(result, "details", {}) or {})
        ),
    }


def _resolve_formula(
    value: Mapping[str, Any],
    slot_bindings: Mapping[str, list[str]],
) -> dict[str, Any]:
    formula = copy.deepcopy(dict(value))
    args = formula.get("args")
    if isinstance(args, list):
        formula["args"] = [
            _resolve_formula(item, slot_bindings)
            if isinstance(item, Mapping)
            else copy.deepcopy(item)
            for item in args
        ]
        return formula

    participants = _participants(formula.get("participants"))
    slot_participants = _participants(formula.get("slot_participants"))
    for role, slot_ids in slot_participants.items():
        participants.setdefault(role, [])
        for slot_id in slot_ids:
            participants[role].extend(
                str(entity_id)
                for entity_id in slot_bindings.get(slot_id, ())
            )
        participants[role] = list(dict.fromkeys(participants[role]))
    formula["participants"] = participants
    return formula


def _confirmed_physical_result(result: Mapping[str, Any]) -> bool:
    return (
        str(result.get("termination") or "").casefold() == "succeeded"
        and str(result.get("effect_state") or "").casefold()
        == "confirmed"
        and str(result.get("verification") or "").casefold()
        in {"true", "confirmed"}
    )


def _runtime_outcomes(runtime: Any) -> tuple[Mapping[str, Any], ...]:
    raw = getattr(runtime, "recent_outcomes", ())
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            return ()
    results: list[Mapping[str, Any]] = []
    for value in raw or ():
        if isinstance(value, Mapping):
            results.append(copy.deepcopy(dict(value)))
            continue
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            continue
        try:
            payload = to_dict()
        except Exception:
            continue
        if isinstance(payload, Mapping):
            results.append(copy.deepcopy(dict(payload)))
    return tuple(results)


def _residual_formula_after_confirmed_effects(
    formula: Mapping[str, Any],
    effects: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    operation = str(formula.get("op") or "").casefold()
    arguments = formula.get("args")
    if operation in {"and", "or"}:
        if not isinstance(arguments, list) or not arguments:
            return copy.deepcopy(dict(formula)), ()
        if operation == "or":
            residual_arguments: list[dict[str, Any]] = []
            matched_ids: list[str] = []
            for argument in arguments:
                if not isinstance(argument, Mapping):
                    return copy.deepcopy(dict(formula)), ()
                residual, matched = (
                    _residual_formula_after_confirmed_effects(
                        argument,
                        effects,
                    )
                )
                if residual is None and matched:
                    return None, matched
                residual_arguments.append(
                    residual or copy.deepcopy(dict(argument))
                )
                matched_ids.extend(matched)
            if not matched_ids:
                return copy.deepcopy(dict(formula)), ()
            residual_formula = copy.deepcopy(dict(formula))
            residual_formula["args"] = residual_arguments
            return residual_formula, tuple(dict.fromkeys(matched_ids))

        residual_arguments = []
        matched_ids = []
        for argument in arguments:
            if not isinstance(argument, Mapping):
                return copy.deepcopy(dict(formula)), ()
            residual, matched = (
                _residual_formula_after_confirmed_effects(
                    argument,
                    effects,
                )
            )
            if residual is not None:
                residual_arguments.append(residual)
            matched_ids.extend(matched)
        if not matched_ids:
            return copy.deepcopy(dict(formula)), ()
        if not residual_arguments:
            return None, tuple(dict.fromkeys(matched_ids))
        if len(residual_arguments) == 1:
            return (
                residual_arguments[0],
                tuple(dict.fromkeys(matched_ids)),
            )
        residual_formula = copy.deepcopy(dict(formula))
        residual_formula["args"] = residual_arguments
        return residual_formula, tuple(dict.fromkeys(matched_ids))
    if operation:
        return copy.deepcopy(dict(formula)), ()

    for effect in effects:
        if _effect_confirms_formula(effect, formula):
            effect_id = _optional_text(effect.get("effect_id"))
            return None, (
                effect_id
                or str(effect.get("predicate") or "confirmed_effect"),
            )
    return copy.deepcopy(dict(formula)), ()


def _effect_confirms_formula(
    effect: Mapping[str, Any],
    formula: Mapping[str, Any],
) -> bool:
    if _semantic_state(formula.get("desired_value", "true")) is not (
        SemanticState.SATISFIED
    ):
        return False
    if not bool(effect.get("required", True)):
        return False
    if _semantic_state(effect.get("state")) is not SemanticState.SATISFIED:
        return False
    predicate = str(formula.get("predicate") or "")
    if not predicate or str(effect.get("predicate") or "") != predicate:
        return False

    expected_participants = _participants(formula.get("participants"))
    actual_participants = _participants(effect.get("participants"))
    for role, expected_ids in expected_participants.items():
        if not expected_ids:
            return False
        if (
            role == "robot"
            and set(expected_ids) == {"robot_1"}
            and not actual_participants.get(role)
        ):
            continue
        if set(actual_participants.get(role, ())) != set(expected_ids):
            return False

    expected_parameters = formula.get("parameters")
    actual_parameters = effect.get("parameters")
    if isinstance(expected_parameters, Mapping):
        actual = (
            actual_parameters
            if isinstance(actual_parameters, Mapping)
            else {}
        )
        for key, expected in expected_parameters.items():
            if key in {"repair_policy", "semantic_role"}:
                continue
            if key not in actual or actual[key] != expected:
                return False
    return True


def _participants(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, list[str]] = {}
    for role, raw in value.items():
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple, set, frozenset)):
            values = [str(item) for item in raw]
        else:
            values = []
        result[str(role)] = list(dict.fromkeys(values))
    return result


def _first_participant(
    participants: Mapping[str, list[str]],
    role: str,
) -> str | None:
    values = participants.get(role, ())
    return str(values[0]) if values else None


def _node_entity_ids(
    node: TaskNodeSpec,
    *roles: str,
) -> set[str]:
    parameters = node.parameters
    result: list[str] = []
    participants = parameters.get("participants")
    if isinstance(participants, Mapping):
        for role in roles:
            raw = participants.get(role)
            if isinstance(raw, Mapping):
                raw = raw.get("entity_ids") or raw.get("entity_id")
            if isinstance(raw, str):
                result.append(raw)
            elif isinstance(raw, (list, tuple, set, frozenset)):
                result.extend(str(value) for value in raw)
    for role in roles:
        for key in (f"{role}_id", f"{role}_ids"):
            raw = parameters.get(key)
            if isinstance(raw, str):
                result.append(raw)
            elif isinstance(raw, (list, tuple, set, frozenset)):
                result.extend(str(value) for value in raw)
    return set(result)


def _held_entity_id(scene: Any) -> str | None:
    if scene is None:
        return None
    for name in (
        "securely_attached_entity",
        "gripper_holding_entity",
        "attached_entity",
    ):
        reader = getattr(scene, name, None)
        if not callable(reader):
            continue
        try:
            value = reader()
        except Exception:
            continue
        if value is not None and str(value):
            return str(value)
    return None


def _formula_entity_ids(formula: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    participants = formula.get("participants")
    if isinstance(participants, Mapping):
        for values in participants.values():
            if isinstance(values, str):
                result.append(values)
            elif isinstance(values, (list, tuple, set, frozenset)):
                result.extend(str(value) for value in values)
    for value in formula.get("args", ()) or ():
        if isinstance(value, Mapping):
            result.extend(_formula_entity_ids(value))
    return list(dict.fromkeys(result))


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _fingerprint(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _normalize_slot_bindings(
    value: Mapping[str, list[str]],
) -> dict[str, list[str]]:
    return {
        str(slot_id): [str(entity_id) for entity_id in (entity_ids or ())]
        for slot_id, entity_ids in value.items()
    }


def _unknown_check(
    *,
    code: str,
    message: str,
    evidence: Mapping[str, Any],
    retryable: bool,
) -> SemanticCheck:
    return SemanticCheck(
        state=SemanticState.UNKNOWN,
        diagnostic=Diagnostic(
            code=code,
            message=message,
            details=evidence,
            retryable=retryable,
            repairable=False,
        ),
        evidence=evidence,
    )
