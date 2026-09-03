from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from threading import Event, RLock
from typing import Any

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.task.contracts import (
    PhysicalActionOutcome,
    PhysicalExecutionState,
    normalize_cancellation_outcome,
)

from .composite_payload import bind_composite_payload_context
from .paths import import_harness_module


AllowedProvider = Iterable[str] | Callable[[Any], Iterable[str]]
ExecutorRegistry = Mapping[str, Callable[..., Any]]


@dataclass
class _RequestRecord:
    request_id: str
    request_name: str
    completed: Event
    outcome: PhysicalActionOutcome | None = None
    cancel_requested: bool = False


class HarnessPhysicalGateway:
    """PhysicalActionRuntime backed by GeminiER2Harness macro actions.

    A request ID is dispatched at most once by this gateway. Duplicate callers
    wait for and receive the first dispatch result, including cancellation and
    outcome-unknown results.
    """

    def __init__(
        self,
        *,
        runtime: Any,
        allowed: AllowedProvider | None = None,
        artifact_bridge: Any = None,
        executors: ExecutorRegistry | None = None,
        harness_root: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.harness_root = harness_root
        self._artifact_bridge = artifact_bridge
        self._allowed = (
            allowed
            if callable(allowed) or allowed is None
            else tuple(str(value) for value in allowed)
        )
        self._executors = executors
        self._dispatch_contract = _detect_dispatch_contract(runtime)
        self._records: dict[str, _RequestRecord] = {}
        self._lock = RLock()
        bind_composite_payload_context(
            getattr(runtime, "scene", None),
            getattr(runtime, "perception", None),
        )

    def execute(self, request: object) -> PhysicalActionOutcome:
        request_id = _request_id(request)
        request_name = _request_name(request)
        if request_id is None:
            return _failed_outcome(
                "INVALID_PHYSICAL_REQUEST",
                "Physical request does not define a non-empty request_id",
            )
        if request_name is None:
            return _failed_outcome(
                "INVALID_PHYSICAL_REQUEST",
                "Physical request does not define a non-empty name",
                result={"request_id": request_id},
            )

        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                record = _RequestRecord(
                    request_id=request_id,
                    request_name=request_name,
                    completed=Event(),
                )
                self._records[request_id] = record
                dispatch = True
            else:
                dispatch = False

        if not dispatch:
            record.completed.wait()
            with self._lock:
                if record.outcome is not None:
                    return record.outcome
            return self._unknown_outcome(
                request_id,
                RuntimeError("Idempotent dispatch completed without a result"),
            )

        try:
            outcome = self._dispatch(request, request_id, request_name)
        except Exception as exc:
            outcome = self._unknown_outcome(request_id, exc)
        with self._lock:
            record.outcome = outcome
            record.completed.set()
        return outcome

    def poll(self, request_id: str) -> PhysicalActionOutcome | None:
        """Return a terminal result, or ``None`` while the request is active."""

        key = str(request_id)
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                return record.outcome

        recovered = self._recover_runtime_outcome(key)
        if recovered is None:
            return None
        self._store_recovered(key, recovered)
        return recovered

    def cancel(self, request_id: str | None = None) -> bool:
        """Request cooperative cancellation for the active physical action."""

        key = str(request_id) if request_id is not None else None
        accepted, _transaction_id = self._request_cancel(key)
        return accepted

    def request_cancel(self, request_id: str | None = None) -> str | None:
        """Mirror HarnessRuntime.request_cancel for session compatibility."""

        key = str(request_id) if request_id is not None else None
        _accepted, transaction_id = self._request_cancel(key)
        return transaction_id

    def _request_cancel(
        self, request_id: str | None
    ) -> tuple[bool, str | None]:
        if request_id is not None and not self._can_cancel(request_id):
            return False, None
        with self._lock:
            if request_id is not None:
                record = self._records.get(request_id)
                if record is not None:
                    record.cancel_requested = True
            else:
                for record in self._records.values():
                    if record.outcome is None:
                        record.cancel_requested = True
        cancel = getattr(self.runtime, "request_cancel", None)
        if not callable(cancel):
            return True, None
        transaction_id = cancel()
        return (
            True,
            str(transaction_id) if transaction_id is not None else None,
        )

    def clear_cancel_request(self) -> None:
        clear = getattr(self.runtime, "clear_cancel_request", None)
        if callable(clear):
            clear()

    def _dispatch(
        self,
        request: object,
        request_id: str,
        request_name: str,
    ) -> PhysicalActionOutcome:
        allowed = self._allowed_for(request, request_name)
        if self._is_system_only(request_name):
            execute_system = getattr(
                self.runtime,
                "execute_system_physical",
                None,
            )
            if not callable(execute_system):
                raise TypeError(
                    f"Harness runtime does not expose "
                    f"execute_system_physical for system-only action "
                    f"{request_name!r}"
                )
            raw_result = execute_system(request)
        elif self._dispatch_contract == "canonical":
            raw_result = self.runtime.execute_physical(request, allowed)
        elif self._dispatch_contract == "legacy":
            executor = self._executor_for(request_name)
            raw_result = self.runtime.execute_physical(
                request,
                executor,
                allowed if allowed is not None else [request_name],
            )
        else:
            raise TypeError(
                "Harness runtime does not expose a supported "
                "execute_physical(request, allowed=None) entrypoint"
            )
        outcome = normalize_physical_result(
            raw_result,
            artifact_refs=_request_artifact_refs(request),
        )
        outcome = self._enrich_failure_diagnostic(
            request,
            request_name,
            outcome,
        )
        outcome = self._invalidate_failed_route(
            request,
            request_name,
            outcome,
        )
        self._record_layout_outcome(request, request_name, outcome)
        return outcome

    def _enrich_failure_diagnostic(
        self,
        request: object,
        request_name: str,
        outcome: PhysicalActionOutcome,
    ) -> PhysicalActionOutcome:
        diagnostic = outcome.diagnostic
        if diagnostic is None:
            return outcome
        details = dict(diagnostic.details)
        residual = details.get("residual_state")
        residual = residual if isinstance(residual, Mapping) else {}
        control_error = residual.get("control_error")
        control_error = (
            control_error if isinstance(control_error, Mapping) else {}
        )
        control_details = control_error.get("details")
        control_details = (
            control_details
            if isinstance(control_details, Mapping)
            else {}
        )
        raw_failure_code = _optional_text(
            control_details.get("raw_failure_code")
        )
        route_attempts = control_details.get("route_attempts")
        route_attempts = (
            route_attempts
            if isinstance(route_attempts, (list, tuple))
            else ()
        )
        goal_reason_code = None
        blocking_entity_ids: list[str] = []
        goal_blocking_entity_ids: list[str] = []
        relaxation_attempted = False
        for attempt in route_attempts:
            if not isinstance(attempt, Mapping):
                continue
            if isinstance(attempt.get("goal_relaxation"), Mapping):
                relaxation_attempted = True
            plan = attempt.get("plan")
            if not isinstance(plan, Mapping):
                continue
            reason_code = _optional_text(plan.get("reason_code"))
            if goal_reason_code is None and reason_code is not None:
                goal_reason_code = reason_code
            blockers = [
                str(value)
                for value in (
                    plan.get("direct_blocking_entity_ids") or ()
                )
                if value is not None and str(value)
            ]
            blocking_entity_ids.extend(blockers)
            if reason_code == "GOAL_POSE_IN_COLLISION":
                goal_blocking_entity_ids.extend(blockers)
        control_blockers = control_details.get("blocking_entity_ids")
        if isinstance(control_blockers, (list, tuple)):
            blocking_entity_ids.extend(
                str(value)
                for value in control_blockers
                if value is not None and str(value)
            )
        collision_pair = control_details.get("collision_pair")
        if isinstance(collision_pair, (list, tuple)):
            blocking_entity_ids.extend(
                str(value)
                for value in collision_pair
                if value is not None and str(value)
            )
        held_entity_id = _optional_text(
            control_details.get("held_entity_id")
        )
        if held_entity_id is not None:
            blocking_entity_ids = [
                value
                for value in blocking_entity_ids
                if value != held_entity_id
            ]
        arguments = _request_arguments(request)
        target_ref = _optional_text(arguments.get("target_ref"))
        object_id = _request_participant(
            request,
            "manipuland",
            "object",
        )
        destination_id = _request_participant(request, "destination")
        if (
            request_name == "place_object"
            and diagnostic.code == "PATH_BLOCKED"
            and raw_failure_code is None
            and str(control_error.get("code") or "").upper()
            == "PATH_BLOCKED"
            and object_id is not None
            and _result_confirms_attachment(details, object_id)
        ):
            raw_failure_code = "HELD_BASE_PATH_COLLISION"
            details["failure_mode"] = "HELD_BASE_PATH_COLLISION"
            details["recovery_kind"] = "reposition_held_base"
            details["held_path_evidence"] = "confirmed_attachment"

        prepared_layout_collision = (
            request_name == "place_object"
            and diagnostic.code
            in {"COLLISION", "INTERACTION_POSE_BLOCKED"}
            and str(control_error.get("code") or "").upper()
            in {"COLLISION", "INTERACTION_POSE_BLOCKED"}
            and arguments.get("placement_prepared") is True
            and target_ref is not None
            and object_id is not None
            and _result_confirms_attachment(details, object_id)
        )
        if prepared_layout_collision:
            raw_failure_code = "PREPARED_PLACEMENT_COLLISION"
            details["failure_mode"] = "PREPARED_PLACEMENT_COLLISION"
            details["recovery_kind"] = "replan_placement_layout"
            details["held_path_evidence"] = "confirmed_attachment"

        if raw_failure_code is not None:
            details["raw_failure_code"] = raw_failure_code
        if goal_reason_code is not None:
            details["goal_reason_code"] = goal_reason_code
        if blocking_entity_ids:
            details["blocking_entity_ids"] = list(
                dict.fromkeys(blocking_entity_ids)
            )
        if goal_blocking_entity_ids:
            details["goal_blocking_entity_ids"] = list(
                dict.fromkeys(goal_blocking_entity_ids)
            )
            details["verified_route_blocking_entity_ids"] = list(
                dict.fromkeys(goal_blocking_entity_ids)
            )
        if target_ref is not None:
            details["target_ref"] = target_ref
        if object_id is not None:
            details["placement_object_id"] = object_id
            details.setdefault("object_id", object_id)
        if destination_id is not None:
            details["placement_destination_id"] = destination_id
            details.setdefault("destination_id", destination_id)
        if request_name == "place_object" and raw_failure_code == (
            "HELD_PATH_COLLISION"
        ):
            near_pure_rotation = bool(
                control_details.get("near_pure_rotation", False)
            )
            details["failure_mode"] = (
                "ROUTE_ENDPOINT_IN_COLLISION"
                if goal_reason_code == "GOAL_POSE_IN_COLLISION"
                else "HELD_PATH_COLLISION"
            )
            details["goal_relaxation_attempted"] = relaxation_attempted
            details["goal_relaxation_applied"] = False
            details["recovery_kind"] = (
                "reposition_held_rotation"
                if near_pure_rotation
                else "replan_placement_layout"
            )
        return PhysicalActionOutcome(
            state=outcome.state,
            result=outcome.result,
            diagnostic=Diagnostic(
                code=diagnostic.code,
                message=diagnostic.message,
                details=details,
                retryable=diagnostic.retryable,
                repairable=(
                    diagnostic.repairable
                    or details.get("recovery_kind")
                    == "replan_placement_layout"
                ),
            ),
            artifact_refs=outcome.artifact_refs,
            transaction_id=outcome.transaction_id,
        )

    def _is_system_only(self, request_name: str) -> bool:
        tools = getattr(self.runtime, "tools", None)
        definition = getattr(tools, "definition", None)
        if callable(definition):
            tool_definition = definition(request_name)
            if isinstance(tool_definition, Mapping):
                return bool(tool_definition.get("system_only", False))
        return False

    def _invalidate_failed_route(
        self,
        request: object,
        request_name: str,
        outcome: PhysicalActionOutcome,
    ) -> PhysicalActionOutcome:
        if request_name not in {
            "follow_path",
            "reposition_for_interaction",
        }:
            return outcome
        if outcome.state is not PhysicalExecutionState.FAILED:
            return outcome
        diagnostic = outcome.diagnostic
        if diagnostic is None or diagnostic.code not in {
            "PATH_BLOCKED",
            "STALE_ARTIFACT",
            "NO_SAFE_DETOUR",
        }:
            return outcome
        path_ref = _request_path_ref(request)
        invalidator = getattr(self._artifact_bridge, "invalidate", None)
        if path_ref is None or not callable(invalidator):
            return outcome

        invalidator((path_ref,))
        details = dict(diagnostic.details)
        affected = [
            str(value)
            for value in details.get("affected_refs", ()) or ()
        ]
        affected.append(path_ref)
        details["affected_refs"] = list(dict.fromkeys(affected))
        details["invalidated_artifact_refs"] = [path_ref]
        result = dict(outcome.result)
        result["invalidated_artifact_refs"] = [path_ref]
        return PhysicalActionOutcome(
            state=outcome.state,
            result=result,
            diagnostic=Diagnostic(
                code=diagnostic.code,
                message=diagnostic.message,
                details=details,
                retryable=diagnostic.retryable,
            ),
            artifact_refs=outcome.artifact_refs,
            transaction_id=outcome.transaction_id,
        )

    def _record_layout_outcome(
        self,
        request: object,
        request_name: str,
        outcome: PhysicalActionOutcome,
    ) -> None:
        if request_name != "place_object":
            return
        arguments = _request_arguments(request)
        target_ref = _optional_text(arguments.get("target_ref"))
        entity_id = _request_participant(
            request,
            "manipuland",
            "object",
        )
        fulfill = getattr(
            self._artifact_bridge,
            "fulfill_layout_reservation",
            None,
        )
        if (
            outcome.state is PhysicalExecutionState.SUCCEEDED
            and target_ref is not None
            and entity_id is not None
            and callable(fulfill)
        ):
            if fulfill(target_ref, entity_id):
                return

        reconcile = getattr(
            self._artifact_bridge,
            "reconcile_layout_reservations",
            None,
        )
        if (
            target_ref is not None
            and entity_id is not None
            and callable(reconcile)
        ):
            reconcile(
                entity_ids=(entity_id,),
                artifact_refs=(target_ref,),
            )

    def _allowed_for(
        self,
        request: object,
        request_name: str,
    ) -> list[str] | None:
        del request_name
        source = self._allowed
        if source is None:
            return None
        values = source(request) if callable(source) else source
        return list(dict.fromkeys(str(value) for value in values))

    def _executor_for(self, request_name: str) -> Callable[..., Any]:
        executors = self._executors
        if executors is None:
            module = import_harness_module(
                "er2sim.macro_actions",
                harness_root=self.harness_root,
            )
            executors = getattr(module, "EXECUTORS", None)
            if not isinstance(executors, Mapping):
                raise TypeError(
                    "er2sim.macro_actions.EXECUTORS is not a mapping"
                )
            self._executors = executors
        executor = executors.get(request_name)
        if executor is None:
            raise TypeError(
                f"No Harness macro executor is registered for {request_name}"
            )
        return executor

    def _can_cancel(self, request_id: str) -> bool:
        with self._lock:
            record = self._records.get(request_id)
            if record is not None:
                return record.outcome is None
        active = getattr(self.runtime, "active_transaction", None)
        return (
            active is not None
            and str(getattr(active, "request_id", "")) == request_id
        )

    def _recover_runtime_outcome(
        self, request_id: str
    ) -> PhysicalActionOutcome | None:
        recent = getattr(self.runtime, "recent_outcomes", ())
        if callable(recent):
            recent = recent()
        for result in reversed(list(recent or ())):
            payload = _result_dict(result)
            if str(payload.get("request_id", "")) == request_id:
                return normalize_physical_result(payload)

        transactions = getattr(self.runtime, "transactions", {})
        values = (
            transactions.values()
            if isinstance(transactions, Mapping)
            else transactions or ()
        )
        transaction = next(
            (
                value
                for value in values
                if str(getattr(value, "request_id", "")) == request_id
            ),
            None,
        )
        if transaction is None:
            return None
        state = str(getattr(transaction, "state", "")).upper()
        if state not in {"TERMINATED", "CANCELED", "CANCELLED", "OUTCOME_UNKNOWN"}:
            return None
        result = _result_dict(getattr(transaction, "outcome", {}))
        result.update(
            {
                "request_id": request_id,
                "transaction_id": str(getattr(transaction, "id", "")),
            }
        )
        if state in {"CANCELED", "CANCELLED"}:
            result["termination"] = "canceled"
        elif state == "OUTCOME_UNKNOWN":
            result["termination"] = "outcome_unknown"
            result.setdefault("effect_state", "unknown")
            result.setdefault("verification", "unknown")
        return normalize_physical_result(result)

    def _store_recovered(
        self,
        request_id: str,
        outcome: PhysicalActionOutcome,
    ) -> None:
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                result = dict(outcome.result)
                record = _RequestRecord(
                    request_id=request_id,
                    request_name=str(result.get("name", "")),
                    completed=Event(),
                    outcome=outcome,
                )
                record.completed.set()
                self._records[request_id] = record

    @staticmethod
    def _unknown_outcome(
        request_id: str,
        error: BaseException,
    ) -> PhysicalActionOutcome:
        result = {
            "request_id": request_id,
            "termination": "outcome_unknown",
            "effect_state": "unknown",
            "verification": "unknown",
            "failure_code": "OUTCOME_UNKNOWN",
            "exception_type": type(error).__name__,
            "exception": str(error),
        }
        return PhysicalActionOutcome(
            PhysicalExecutionState.OUTCOME_UNKNOWN,
            result=result,
            diagnostic=Diagnostic(
                "OUTCOME_UNKNOWN",
                f"Harness physical dispatch raised: {error}",
                details=result,
                retryable=False,
            ),
        )


class PhysicalActionRuntime(HarnessPhysicalGateway):
    """Concrete GeminiER2 implementation of the kernel runtime protocol."""


def _detect_dispatch_contract(runtime: object) -> str:
    execute = getattr(runtime, "execute_physical", None)
    if not callable(execute):
        return "unsupported"
    try:
        signature = inspect.signature(execute)
    except (TypeError, ValueError):
        return "unsupported"
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if len(positional) == 2:
        return "canonical"
    if len(positional) == 3:
        return "legacy"
    return "unsupported"


def normalize_physical_result(
    raw_result: object,
    *,
    artifact_refs: tuple[str, ...] = (),
) -> PhysicalActionOutcome:
    """Normalize Harness MacroActionResult into the kernel four-state model."""

    if isinstance(raw_result, PhysicalActionOutcome):
        if raw_result.state is PhysicalExecutionState.CANCELLED:
            return normalize_cancellation_outcome(raw_result)
        return raw_result
    result = _result_dict(raw_result)
    termination = _normalized_text(result.get("termination"))
    effect_state = _normalized_text(result.get("effect_state"))
    verification = _normalized_text(result.get("verification"))
    transaction_id = _optional_text(result.get("transaction_id"))
    combined_artifacts = _artifact_refs(result, artifact_refs)

    if termination in {"canceled", "cancelled"}:
        return normalize_cancellation_outcome(
            PhysicalActionOutcome(
                PhysicalExecutionState.CANCELLED,
                result=result,
                artifact_refs=combined_artifacts,
                transaction_id=transaction_id,
            )
        )

    required_states = tuple(
        _normalized_text(effect.get("state"))
        for effect in result.get("effects", ())
        if isinstance(effect, Mapping)
        and bool(effect.get("required", True))
    )
    has_unknown_effect = any(
        state in {"", "unknown", "partial"}
        for state in required_states
    )
    has_failed_effect = any(
        state in {"false", "failed", "rejected", "refuted"}
        for state in required_states
    )
    if termination in {"failed", "rejected"}:
        diagnostic = _diagnostic(
            result,
            default_code="PHYSICAL_EXECUTION_FAILED",
            default_message="Harness physical action failed",
            retryable=None,
        )
        return PhysicalActionOutcome(
            PhysicalExecutionState.FAILED,
            result=result,
            diagnostic=diagnostic,
            artifact_refs=combined_artifacts,
            transaction_id=transaction_id,
        )

    if (
        termination in {"", "outcome_unknown", "timed_out"}
        or effect_state in {"unknown", "partial"}
        or verification == "unknown"
        or has_unknown_effect
    ):
        diagnostic = _diagnostic(
            result,
            default_code="OUTCOME_UNKNOWN",
            default_message="Physical action outcome requires reconciliation",
            retryable=False,
            prefer_result_code=False,
        )
        return PhysicalActionOutcome(
            PhysicalExecutionState.OUTCOME_UNKNOWN,
            result=result,
            diagnostic=diagnostic,
            artifact_refs=combined_artifacts,
            transaction_id=transaction_id,
        )

    succeeded = (
        termination == "succeeded"
        and not has_failed_effect
        and effect_state not in {"false", "failed", "rejected", "refuted"}
        and verification not in {"false", "failed", "refuted"}
    )
    if succeeded:
        return PhysicalActionOutcome.succeeded(
            result=result,
            artifact_refs=combined_artifacts,
            transaction_id=transaction_id,
        )

    diagnostic = _diagnostic(
        result,
        default_code="PHYSICAL_EXECUTION_FAILED",
        default_message="Harness physical action was not confirmed",
        retryable=None,
    )
    return PhysicalActionOutcome(
        PhysicalExecutionState.FAILED,
        result=result,
        diagnostic=diagnostic,
        artifact_refs=combined_artifacts,
        transaction_id=transaction_id,
    )


def _failed_outcome(
    code: str,
    message: str,
    *,
    result: Mapping[str, Any] | None = None,
) -> PhysicalActionOutcome:
    payload = dict(result or {})
    payload.setdefault("termination", "failed")
    payload.setdefault("failure_code", code)
    return PhysicalActionOutcome(
        PhysicalExecutionState.FAILED,
        result=payload,
        diagnostic=Diagnostic(code, message, details=payload, retryable=False),
        transaction_id=_optional_text(payload.get("transaction_id")),
    )


def _diagnostic(
    result: Mapping[str, Any],
    *,
    default_code: str,
    default_message: str,
    retryable: bool | None,
    prefer_result_code: bool = True,
) -> Diagnostic:
    code = str(
        result.get("failure_code") or default_code
        if prefer_result_code
        else default_code
    )
    if retryable is None:
        retryable = code in {"RESOURCE_BUSY", "STALE_RELEVANT_STATE"}
    details = dict(result)
    physical_failure_code = result.get("failure_code")
    if (
        not prefer_result_code
        and physical_failure_code
        and str(physical_failure_code) != code
    ):
        details.setdefault(
            "physical_failure_code",
            str(physical_failure_code),
        )
    return Diagnostic(
        code=code,
        message=_result_message(result, default_message),
        details=details,
        retryable=retryable,
    )


def _result_message(result: Mapping[str, Any], default: str) -> str:
    residual = result.get("residual_state")
    if isinstance(residual, Mapping):
        for key in ("rejection_reason", "reason", "message"):
            if residual.get(key):
                return str(residual[key])
    for key in ("message", "reason", "failure_message"):
        if result.get(key):
            return str(result[key])
    return default


def _artifact_refs(
    result: Mapping[str, Any],
    initial: tuple[str, ...],
) -> tuple[str, ...]:
    refs = [str(value) for value in initial]
    refs.extend(str(value) for value in result.get("artifact_refs", ()) or ())
    for effect in result.get("effects", ()) or ():
        if isinstance(effect, Mapping):
            refs.extend(
                str(value)
                for value in effect.get("evidence_refs", ()) or ()
            )
    return tuple(dict.fromkeys(refs))


def _request_artifact_refs(request: object) -> tuple[str, ...]:
    values = getattr(request, "artifact_refs", ()) or ()
    return tuple(str(value) for value in values)


def _request_path_ref(request: object) -> str | None:
    value = _request_arguments(request).get("path_ref")
    return _optional_text(value)


def _request_arguments(request: object) -> Mapping[str, Any]:
    arguments = (
        request.get("arguments")
        if isinstance(request, Mapping)
        else getattr(request, "arguments", None)
    )
    return arguments if isinstance(arguments, Mapping) else {}


def _request_participant(
    request: object,
    *roles: str,
) -> str | None:
    participants = None
    resolver = getattr(request, "participants", None)
    if callable(resolver):
        try:
            participants = resolver()
        except Exception:
            participants = None
    if participants is None:
        participants = (
            request.get("participants")
            if isinstance(request, Mapping)
            else getattr(request, "participant_bindings", None)
        )
    if not isinstance(participants, Mapping):
        return None
    for role in roles:
        value = participants.get(role)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
    return None


def _result_confirms_attachment(
    details: Mapping[str, Any],
    object_id: str,
) -> bool:
    raw_effects = details.get("effects")
    if not isinstance(raw_effects, (list, tuple)):
        nested_result = details.get("result")
        raw_effects = (
            nested_result.get("effects")
            if isinstance(nested_result, Mapping)
            else None
        )
    if not isinstance(raw_effects, (list, tuple)):
        return False

    expected = str(object_id)
    for effect in raw_effects:
        if not isinstance(effect, Mapping):
            continue
        if str(effect.get("predicate") or "").casefold() not in {
            "attached_to",
            "attached_to_any_end_effector",
        }:
            continue
        if str(effect.get("state") or "").casefold() != "confirmed":
            continue
        participants = effect.get("participants")
        participants = (
            participants if isinstance(participants, Mapping) else {}
        )
        effect_objects = {
            str(value)
            for role in ("object", "subject", "manipuland")
            for value in (
                participants.get(role)
                if isinstance(participants.get(role), (list, tuple))
                else (participants.get(role),)
            )
            if value is not None and str(value)
        }
        predicate_details = effect.get("predicate_details")
        predicate_details = (
            predicate_details
            if isinstance(predicate_details, Mapping)
            else {}
        )
        held_entity_ids = {
            str(value)
            for value in (
                predicate_details.get("held_entity_id"),
                predicate_details.get("observed_held_entity_id"),
            )
            if value is not None and str(value)
        }
        if expected in effect_objects or expected in held_entity_ids:
            return True
    return False


def _request_id(request: object) -> str | None:
    value = (
        request.get("request_id")
        if isinstance(request, Mapping)
        else getattr(request, "request_id", None)
    )
    return _optional_text(value)


def _request_name(request: object) -> str | None:
    value = (
        request.get("name")
        if isinstance(request, Mapping)
        else getattr(request, "name", None)
    )
    return _optional_text(value)


def _result_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain_mapping(to_dict())
    if is_dataclass(value):
        return {
            item.name: _plain_value(getattr(value, item.name))
            for item in fields(value)
        }
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return _plain_mapping(attributes)
    return {"value": _plain_value(value)}


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _plain_value(item)
        for key, item in value.items()
    }


def _plain_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _plain_value(value.value)
    if isinstance(value, Mapping):
        return _plain_mapping(value)
    if is_dataclass(value):
        return {
            item.name: _plain_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain_value(item) for item in value), key=repr)
    return copy.deepcopy(value)


def _normalized_text(value: Any) -> str:
    raw = value.value if isinstance(value, Enum) else value
    return str(raw or "").strip().lower()


def _optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value)


__all__ = [
    "HarnessPhysicalGateway",
    "PhysicalActionRuntime",
    "normalize_physical_result",
]
