from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event
from types import SimpleNamespace

import pytest

from task_recursive_tree.integrations.gemini_er2.physical_runtime import (
    HarnessPhysicalGateway,
    PhysicalActionRuntime,
    normalize_physical_result,
)
from task_recursive_tree.integrations.gemini_er2.paths import (
    import_harness_module,
)
from task_recursive_tree.task.contracts import (
    PhysicalActionOutcome,
    PhysicalExecutionState,
)


@dataclass
class Request:
    name: str
    request_id: str
    arguments: dict = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()


@dataclass
class Result:
    request_id: str
    transaction_id: str
    name: str
    termination: str
    effect_state: str
    verification: str
    failure_code: str | None = None
    effects: list[dict] = field(default_factory=list)
    residual_state: dict = field(default_factory=dict)
    cancellation: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "request_id": self.request_id,
            "transaction_id": self.transaction_id,
            "name": self.name,
            "termination": self.termination,
            "effect_state": self.effect_state,
            "verification": self.verification,
            "failure_code": self.failure_code,
            "effects": self.effects,
            "residual_state": self.residual_state,
            "cancellation": self.cancellation,
        }


class FakeHarnessRuntime:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls = []
        self.cancel_calls = 0
        self.clear_calls = 0
        self.active_transaction = None
        self.recent_outcomes = []
        self.transactions = {}

    def execute_physical(self, request, allowed=None):
        allowed_copy = None if allowed is None else list(allowed)
        self.calls.append((request, allowed_copy))
        if isinstance(self.result, BaseException):
            raise self.result
        if callable(self.result):
            return self.result(request, allowed_copy)
        return self.result

    def request_cancel(self):
        self.cancel_calls += 1
        return (
            self.active_transaction.id
            if self.active_transaction is not None
            else None
        )

    def clear_cancel_request(self):
        self.clear_calls += 1


class LegacyHarnessRuntime:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls = []

    def execute_physical(self, request, executor, allowed):
        self.calls.append((request, executor, list(allowed)))
        if callable(self.result):
            return self.result(request, executor, allowed)
        return self.result


class FakeToolRegistry:
    def __init__(self, definitions) -> None:
        self.definitions = dict(definitions)

    def definition(self, name):
        return self.definitions.get(name)


class SystemAwareHarnessRuntime(FakeHarnessRuntime):
    def __init__(self, result=None) -> None:
        super().__init__(result)
        self.tools = FakeToolRegistry(
            {
                "move_to_transport_posture": {
                    "system_only": True,
                },
                "pick_object": {
                    "system_only": False,
                },
            }
        )
        self.system_calls = []

    def execute_system_physical(self, request):
        self.system_calls.append(request)
        return self.result


class RecordingArtifactBridge:
    def __init__(self) -> None:
        self.invalidated = []

    def invalidate(self, artifact_refs) -> None:
        self.invalidated.append(tuple(artifact_refs))


class LayoutReconciliationBridge:
    def __init__(self) -> None:
        self.fulfilled = []
        self.reconciled = []

    def fulfill_layout_reservation(self, artifact_ref, entity_id):
        self.fulfilled.append((artifact_ref, entity_id))
        return True

    def reconcile_layout_reservations(self, **kwargs):
        self.reconciled.append(dict(kwargs))
        return ()


def successful_result(request_id="request-1", transaction_id="txn-1"):
    return Result(
        request_id=request_id,
        transaction_id=transaction_id,
        name="pick_object",
        termination="succeeded",
        effect_state="confirmed",
        verification="true",
        effects=[
            {
                "required": True,
                "state": "confirmed",
                "evidence_refs": ["evidence/grasp"],
            }
        ],
    )


def test_gateway_calls_exact_harness_physical_entrypoint() -> None:
    runtime = FakeHarnessRuntime(successful_result())
    request = Request(
        "pick_object",
        "request-1",
        {"object_id": "apple_1"},
        artifact_refs=("artifact/pick-plan",),
    )
    gateway = HarnessPhysicalGateway(
        runtime=runtime,
        allowed=lambda value: [value.name, "capture_observation"],
    )

    outcome = gateway.execute(request)

    assert outcome.state is PhysicalExecutionState.SUCCEEDED
    assert outcome.transaction_id == "txn-1"
    assert outcome.artifact_refs == (
        "artifact/pick-plan",
        "evidence/grasp",
    )
    assert runtime.calls == [
        (
            request,
            ["pick_object", "capture_observation"],
        )
    ]
    assert isinstance(
        PhysicalActionRuntime(runtime=runtime),
        HarnessPhysicalGateway,
    )


def test_installed_harness_physical_entrypoint_contract() -> None:
    runtime_module = import_harness_module("er2sim.runtime")
    signature = inspect.signature(
        runtime_module.HarnessRuntime.execute_physical
    )

    assert tuple(signature.parameters) == ("self", "request", "allowed")
    assert signature.parameters["allowed"].default is None


def test_legacy_three_argument_runtime_is_supported_explicitly() -> None:
    handler = object()
    runtime = LegacyHarnessRuntime(successful_result())
    gateway = HarnessPhysicalGateway(
        runtime=runtime,
        executors={"pick_object": handler},
    )

    outcome = gateway.execute(Request("pick_object", "legacy-request"))

    assert outcome.state is PhysicalExecutionState.SUCCEEDED
    assert runtime.calls == [
        (
            Request("pick_object", "legacy-request"),
            handler,
            ["pick_object"],
        )
    ]


def test_system_only_physical_action_uses_trusted_harness_entrypoint() -> None:
    runtime = SystemAwareHarnessRuntime(successful_result())
    gateway = HarnessPhysicalGateway(runtime=runtime)
    request = Request(
        "move_to_transport_posture",
        "system-request",
    )

    outcome = gateway.execute(request)

    assert outcome.state is PhysicalExecutionState.SUCCEEDED
    assert runtime.system_calls == [request]
    assert runtime.calls == []


@pytest.mark.parametrize(
    ("result", "state", "code"),
    [
        (
            Result(
                "failed",
                "txn-failed",
                "place_object",
                "failed",
                "refuted",
                "false",
                failure_code="PLACEMENT_UNSTABLE",
                residual_state={"rejection_reason": "object tipped"},
            ),
            PhysicalExecutionState.FAILED,
            "PLACEMENT_UNSTABLE",
        ),
        (
            Result(
                "rejected",
                "",
                "place_object",
                "rejected",
                "unknown",
                "not_run",
                failure_code="NOT_ALLOWED",
            ),
            PhysicalExecutionState.FAILED,
            "NOT_ALLOWED",
        ),
        (
            Result(
                "canceled",
                "txn-canceled",
                "place_object",
                "canceled",
                "refuted",
                "false",
            ),
            PhysicalExecutionState.OUTCOME_UNKNOWN,
            "OUTCOME_UNKNOWN",
        ),
        (
            Result(
                "unknown",
                "txn-unknown",
                "place_object",
                "succeeded",
                "unknown",
                "unknown",
                effects=[{"required": True, "state": "unknown"}],
            ),
            PhysicalExecutionState.OUTCOME_UNKNOWN,
            "OUTCOME_UNKNOWN",
        ),
    ],
)
def test_result_normalization(result, state, code) -> None:
    outcome = normalize_physical_result(result)

    assert outcome.state is state
    assert outcome.transaction_id == (result.transaction_id or None)
    if code is None:
        assert outcome.diagnostic is None
    else:
        assert outcome.diagnostic.code == code
    if result.request_id == "failed":
        assert outcome.diagnostic.message == "object tipped"


def test_same_request_id_is_dispatched_once_and_replays_first_result() -> None:
    runtime = FakeHarnessRuntime(successful_result("stable-id", "txn-first"))
    gateway = HarnessPhysicalGateway(runtime=runtime)

    first = gateway.execute(
        Request("pick_object", "stable-id", {"object_id": "apple_1"})
    )
    second = gateway.execute(
        Request("pick_object", "stable-id", {"object_id": "apple_2"})
    )

    assert first is second
    assert first.transaction_id == "txn-first"
    assert runtime.calls == [
        (
            Request(
                "pick_object",
                "stable-id",
                {"object_id": "apple_1"},
            ),
            None,
        )
    ]


def test_blocked_route_invalidates_consumed_path_artifact() -> None:
    bridge = RecordingArtifactBridge()
    runtime = FakeHarnessRuntime(
        Result(
            "route-request",
            "txn-route",
            "follow_path",
            "failed",
            "refuted",
            "false",
            failure_code="PATH_BLOCKED",
        )
    )
    request = Request(
        "follow_path",
        "route-request",
        {"path_ref": "path/stale"},
        artifact_refs=("path/stale",),
    )
    gateway = HarnessPhysicalGateway(
        runtime=runtime,
        artifact_bridge=bridge,
    )

    outcome = gateway.execute(request)

    assert outcome.state is PhysicalExecutionState.FAILED
    assert bridge.invalidated == [("path/stale",)]
    assert outcome.result["invalidated_artifact_refs"] == ["path/stale"]
    assert outcome.diagnostic.details["affected_refs"] == ["path/stale"]


def test_failed_place_reconciles_layout_from_live_physical_evidence() -> None:
    bridge = LayoutReconciliationBridge()
    runtime = FakeHarnessRuntime(
        Result(
            "place-request",
            "txn-place",
            "place_object",
            "failed",
            "refuted",
            "false",
            failure_code="COLLISION",
        )
    )
    request = Request(
        "place_object",
        "place-request",
        {"target_ref": "layout/floor"},
        artifact_refs=("layout/floor",),
    )
    request.participant_bindings = {"manipuland": ["can_1"]}
    gateway = HarnessPhysicalGateway(
        runtime=runtime,
        artifact_bridge=bridge,
    )

    outcome = gateway.execute(request)

    assert outcome.state is PhysicalExecutionState.FAILED
    assert bridge.fulfilled == []
    assert bridge.reconciled == [
        {
            "entity_ids": ("can_1",),
            "artifact_refs": ("layout/floor",),
        }
    ]


def test_place_path_collision_promotes_repair_evidence_to_diagnostic() -> None:
    runtime = FakeHarnessRuntime(
        Result(
            "place-path-request",
            "txn-place-path",
            "place_object",
            "failed",
            "refuted",
            "false",
            failure_code="PATH_BLOCKED",
            residual_state={
                "control_error": {
                    "details": {
                        "raw_failure_code": "HELD_PATH_COLLISION",
                        "near_pure_rotation": False,
                        "held_entity_id": "cup_1",
                        "collision_pair": ["cup_1", "table_1"],
                        "route_attempts": [
                            {
                                "goal_relaxation": {
                                    "position_tolerance_m": 0.04,
                                },
                                "plan": {
                                    "reason_code": (
                                        "GOAL_POSE_IN_COLLISION"
                                    ),
                                    "direct_blocking_entity_ids": [
                                        "table_1"
                                    ],
                                },
                            }
                        ],
                    }
                }
            },
        )
    )
    request = Request(
        "place_object",
        "place-path-request",
        {"target_ref": "layout/floor"},
        artifact_refs=("layout/floor",),
    )
    request.participant_bindings = {
        "manipuland": ["cup_1"],
        "destination": ["floor_1"],
    }

    outcome = HarnessPhysicalGateway(runtime=runtime).execute(request)

    assert outcome.state is PhysicalExecutionState.FAILED
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.code == "PATH_BLOCKED"
    assert outcome.diagnostic.repairable is True
    details = outcome.diagnostic.details
    assert details["raw_failure_code"] == "HELD_PATH_COLLISION"
    assert details["goal_reason_code"] == "GOAL_POSE_IN_COLLISION"
    assert details["blocking_entity_ids"] == ["table_1"]
    assert details["goal_blocking_entity_ids"] == ["table_1"]
    assert details["verified_route_blocking_entity_ids"] == ["table_1"]
    assert details["target_ref"] == "layout/floor"
    assert details["object_id"] == "cup_1"
    assert details["placement_object_id"] == "cup_1"
    assert details["destination_id"] == "floor_1"
    assert details["placement_destination_id"] == "floor_1"
    assert details["failure_mode"] == "ROUTE_ENDPOINT_IN_COLLISION"
    assert details["goal_relaxation_attempted"] is True
    assert details["goal_relaxation_applied"] is False
    assert details["recovery_kind"] == "replan_placement_layout"


@pytest.mark.parametrize(
    "failure_code",
    ["COLLISION", "INTERACTION_POSE_BLOCKED"],
)
def test_prepared_place_collision_requests_layout_reselection(
    failure_code,
) -> None:
    runtime = FakeHarnessRuntime(
        Result(
            "prepared-place-request",
            "txn-prepared-place",
            "place_object",
            "failed",
            "refuted",
            "false",
            failure_code=failure_code,
            effects=[
                {
                    "predicate": "attached_to_any_end_effector",
                    "participants": {"object": ["cup_1"]},
                    "state": "confirmed",
                    "required": False,
                    "predicate_details": {
                        "held_entity_id": "cup_1",
                        "attachment_state": "secure",
                    },
                }
            ],
            residual_state={
                "control_error": {
                    "code": failure_code,
                    "message": (
                        "prepared placement approach is now in collision"
                    ),
                    "details": {
                        "collision_pair": ["cup_1", "table_1"],
                    },
                }
            },
        )
    )
    request = Request(
        "place_object",
        "prepared-place-request",
        {
            "target_ref": "layout/floor",
            "placement_prepared": True,
            "base_prepositioned": True,
        },
        artifact_refs=("layout/floor",),
    )
    request.participant_bindings = {
        "manipuland": ["cup_1"],
        "destination": ["floor_1"],
    }

    outcome = HarnessPhysicalGateway(runtime=runtime).execute(request)

    assert outcome.state is PhysicalExecutionState.FAILED
    assert outcome.diagnostic is not None
    assert outcome.diagnostic.repairable is True
    details = outcome.diagnostic.details
    assert details["raw_failure_code"] == (
        "PREPARED_PLACEMENT_COLLISION"
    )
    assert details["failure_mode"] == "PREPARED_PLACEMENT_COLLISION"
    assert details["recovery_kind"] == "replan_placement_layout"
    assert details["held_path_evidence"] == "confirmed_attachment"
    assert details["target_ref"] == "layout/floor"
    assert details["placement_object_id"] == "cup_1"
    assert details["placement_destination_id"] == "floor_1"


def test_place_base_path_failure_infers_confirmed_held_evidence() -> None:
    runtime = FakeHarnessRuntime(
        Result(
            "place-base-path-request",
            "txn-place-base-path",
            "place_object",
            "failed",
            "refuted",
            "false",
            failure_code="PATH_BLOCKED",
            effects=[
                {
                    "predicate": "attached_to_any_end_effector",
                    "participants": {"object": ["cup_1"]},
                    "state": "confirmed",
                    "required": False,
                    "predicate_details": {
                        "held_entity_id": "cup_1",
                        "attachment_state": "secure",
                    },
                }
            ],
            residual_state={
                "control_error": {
                    "code": "PATH_BLOCKED",
                    "message": (
                        "base footprint sweep blocked by obstacle table_1"
                    ),
                }
            },
        )
    )
    request = Request(
        "place_object",
        "place-base-path-request",
        {"target_ref": "layout/floor"},
        artifact_refs=("layout/floor",),
    )
    request.participant_bindings = {
        "manipuland": ["cup_1"],
        "destination": ["floor_1"],
    }

    outcome = HarnessPhysicalGateway(runtime=runtime).execute(request)

    assert outcome.state is PhysicalExecutionState.FAILED
    assert outcome.diagnostic is not None
    details = outcome.diagnostic.details
    assert details["raw_failure_code"] == "HELD_BASE_PATH_COLLISION"
    assert details["failure_mode"] == "HELD_BASE_PATH_COLLISION"
    assert details["recovery_kind"] == "reposition_held_base"
    assert details["held_path_evidence"] == "confirmed_attachment"
    assert details["placement_object_id"] == "cup_1"


def test_concurrent_duplicate_request_waits_for_first_dispatch() -> None:
    started = Event()
    release = Event()

    def blocking_result(request, allowed):
        started.set()
        assert release.wait(2.0)
        return successful_result(request.request_id, "txn-concurrent")

    runtime = FakeHarnessRuntime(blocking_result)
    gateway = HarnessPhysicalGateway(runtime=runtime)
    request = Request("pick_object", "concurrent-id")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(gateway.execute, request)
        assert started.wait(2.0)
        second = pool.submit(gateway.execute, request)
        release.set()
        first_result = first.result(timeout=2.0)
        second_result = second.result(timeout=2.0)

    assert first_result is second_result
    assert first_result.state is PhysicalExecutionState.SUCCEEDED
    assert len(runtime.calls) == 1


def test_cancel_poll_and_clear_delegate_to_harness_runtime() -> None:
    started = Event()
    canceled = Event()

    def cancellable_result(request, allowed):
        runtime.active_transaction = SimpleNamespace(
            id="txn-active",
            request_id=request.request_id,
        )
        started.set()
        assert canceled.wait(2.0)
        runtime.active_transaction = None
        return Result(
            request.request_id,
            "txn-active",
            request.name,
            "canceled",
            "none",
            "not_run",
            cancellation={
                "dispatch_state": "stopped",
                "transaction_status": "closed",
                "quiescent": True,
                "no_side_effects_verified": True,
                "evidence_revision": "world:cancelled",
            },
        )

    runtime = FakeHarnessRuntime(cancellable_result)
    gateway = HarnessPhysicalGateway(runtime=runtime)
    request = Request("place_object", "cancel-me")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(gateway.execute, request)
        assert started.wait(2.0)
        assert gateway.poll("cancel-me") is None
        assert gateway.cancel("different-request") is False
        assert gateway.cancel("cancel-me") is True
        assert runtime.cancel_calls == 1
        canceled.set()
        outcome = future.result(timeout=2.0)

    assert outcome.state is PhysicalExecutionState.CANCELLED
    assert gateway.poll("cancel-me") is outcome
    assert gateway.cancel("cancel-me") is False
    gateway.clear_cancel_request()
    assert runtime.clear_calls == 1


def test_typed_cancelled_outcome_cannot_bypass_safety_normalization() -> None:
    raw = PhysicalActionOutcome(
        PhysicalExecutionState.CANCELLED,
        result={
            "termination": "canceled",
            "transaction_id": "txn-ambiguous",
        },
        transaction_id="txn-ambiguous",
    )

    outcome = normalize_physical_result(raw)

    assert outcome.state is PhysicalExecutionState.OUTCOME_UNKNOWN
    assert outcome.diagnostic.code == "OUTCOME_UNKNOWN"
    assert outcome.result["cancellation_requires_reconciliation"] is True


def test_partial_effect_state_requires_reconciliation() -> None:
    outcome = normalize_physical_result(
        {
            "termination": "succeeded",
            "effect_state": "partial",
            "verification": "true",
            "transaction_id": "txn-partial",
        }
    )

    assert outcome.state is PhysicalExecutionState.OUTCOME_UNKNOWN
    assert outcome.diagnostic.code == "OUTCOME_UNKNOWN"


def test_dispatch_exception_becomes_cached_outcome_unknown() -> None:
    runtime = FakeHarnessRuntime(RuntimeError("transport disconnected"))
    gateway = HarnessPhysicalGateway(runtime=runtime)
    request = Request("follow_path", "unknown-id")

    first = gateway.execute(request)
    second = gateway.execute(request)

    assert first is second
    assert first.state is PhysicalExecutionState.OUTCOME_UNKNOWN
    assert first.diagnostic.code == "OUTCOME_UNKNOWN"
    assert first.result["exception_type"] == "RuntimeError"
    assert len(runtime.calls) == 1


def test_poll_recovers_persisted_unknown_transaction() -> None:
    runtime = FakeHarnessRuntime()
    runtime.transactions["txn-persisted"] = SimpleNamespace(
        id="txn-persisted",
        request_id="persisted-request",
        state="OUTCOME_UNKNOWN",
        outcome={
            "termination": "outcome_unknown",
            "effect_state": "unknown",
            "verification": "unknown",
            "failure_code": "TOOL_TIMEOUT",
        },
    )
    gateway = HarnessPhysicalGateway(runtime=runtime)

    outcome = gateway.poll("persisted-request")

    assert outcome.state is PhysicalExecutionState.OUTCOME_UNKNOWN
    assert outcome.transaction_id == "txn-persisted"
    assert outcome.diagnostic.code == "OUTCOME_UNKNOWN"
    assert outcome.diagnostic.details["physical_failure_code"] == "TOOL_TIMEOUT"
    assert gateway.poll("persisted-request") is outcome
