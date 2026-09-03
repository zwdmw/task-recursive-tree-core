from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from threading import Barrier, Event, Thread

import pytest

from task_recursive_tree.core.model import Diagnostic
from task_recursive_tree.runtime.effect_journal import (
    SQLitePhysicalEffectJournal,
)
from task_recursive_tree.runtime.effects import (
    PHYSICAL_REQUEST_HASH_SCHEMA,
    PhysicalEffect,
    PhysicalEffectError,
    SynchronousEffectRunner,
    canonical_request_hash,
)


@dataclass(frozen=True)
class Request:
    request_id: str
    arguments: dict


@dataclass(frozen=True)
class AlternateRequest:
    request_id: str
    arguments: dict


class MixedSlotRequest:
    __slots__ = ("slot_value", "__dict__")

    def __init__(self) -> None:
        self.request_id = "request-1"
        self.arguments = {"target": "zone-a"}
        self.slot_value = "execution-relevant"


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return {"request_id": request.request_id, "ok": True}


class BlockingRuntime(RecordingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def execute(self, request):
        self.calls.append(request)
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release physical execution")
        return {"request_id": request.request_id, "ok": True}


def test_canonical_request_hash_ignores_mapping_order() -> None:
    first = Request("request-1", {"b": 2, "a": 1})
    second = Request("request-1", {"a": 1, "b": 2})

    assert canonical_request_hash(first) == canonical_request_hash(second)


def test_canonical_request_hash_preserves_value_types() -> None:
    request = Request("request-1", {"target": "zone-a"})
    alternate = AlternateRequest("request-1", {"target": "zone-a"})

    assert canonical_request_hash(request) != canonical_request_hash(
        alternate
    )
    assert canonical_request_hash([1, 2]) != canonical_request_hash(
        (1, 2)
    )
    assert canonical_request_hash(b"\x00") != canonical_request_hash(
        {"bytes": "00"}
    )


def test_physical_effect_rejects_non_string_mapping_keys() -> None:
    request = Request("request-1", {1: "zone-a"})

    with pytest.raises(
        PhysicalEffectError,
        match="cannot be canonicalized",
    ) as raised:
        PhysicalEffect.from_request(request.request_id, request)

    assert raised.value.details["dispatch_stage"] == "not_started"


def test_physical_effect_rejects_unmodeled_slot_state() -> None:
    request = MixedSlotRequest()

    with pytest.raises(
        PhysicalEffectError,
        match="cannot be canonicalized",
    ) as raised:
        PhysicalEffect.from_request(request.request_id, request)

    assert raised.value.details["dispatch_stage"] == "not_started"
    assert "slot state" in raised.value.details["canonicalization_error"]


def test_synchronous_effect_runner_deduplicates_exact_request() -> None:
    runtime = RecordingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)

    first = runner.run(runtime, effect)
    second = runner.run(runtime, effect)

    assert first is second
    assert runtime.calls == [request]
    assert runner.records()["request-1"]["state"] == "completed"


def test_synchronous_effect_runner_executes_an_isolated_snapshot() -> None:
    runtime = RecordingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)

    runner.run(runtime, effect)

    executed = runtime.calls[0]
    assert executed == request
    assert executed is not request
    assert executed.arguments is not request.arguments


def test_synchronous_effect_runner_rejects_mutated_request() -> None:
    runtime = RecordingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    request.arguments["target"] = "zone-b"

    with pytest.raises(
        PhysicalEffectError,
        match="hash does not match",
    ) as raised:
        runner.run(runtime, effect)

    assert raised.value.details["dispatch_stage"] == "not_started"
    assert runtime.calls == []


def test_synchronous_effect_runner_rejects_request_id_hash_conflict() -> None:
    runtime = RecordingRuntime()
    runner = SynchronousEffectRunner()
    first = Request("request-1", {"target": "zone-a"})
    second = Request("request-1", {"target": "zone-b"})

    runner.run(runtime, PhysicalEffect.from_request("request-1", first))

    with pytest.raises(PhysicalEffectError, match="different payload"):
        runner.run(
            runtime,
            PhysicalEffect.from_request("request-1", second),
        )

    assert runtime.calls == [first]


def test_synchronous_effect_runner_serializes_duplicate_callers() -> None:
    runtime = BlockingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    results = []
    errors = []

    def invoke() -> None:
        try:
            results.append(runner.run(runtime, effect))
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=invoke)
    second = Thread(target=invoke)
    first.start()
    assert runtime.entered.wait(timeout=5)
    second.start()
    runtime.release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert runtime.calls == [request]
    assert len(results) == 2
    assert results[0] is results[1]


def test_physical_effect_rejects_mismatched_hash() -> None:
    request = Request("request-1", {"target": "zone-a"})

    with pytest.raises(PhysicalEffectError, match="hash does not match"):
        PhysicalEffect(
            request_id=request.request_id,
            request_hash="not-the-request-hash",
            request=request,
        )


def test_sqlite_effect_journal_uses_wal(tmp_path) -> None:
    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    try:
        assert journal.journal_mode == "wal"
    finally:
        journal.close()
        journal.close()


def test_completed_request_survives_restart_without_redispatch(
    tmp_path,
) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    first_runtime = RecordingRuntime()
    first_runner = SynchronousEffectRunner(
        journal=SQLitePhysicalEffectJournal(path)
    )

    first_runner.run(first_runtime, effect)
    first_runner.close()

    second_runtime = RecordingRuntime()
    second_journal = SQLitePhysicalEffectJournal(path)
    second_runner = SynchronousEffectRunner(journal=second_journal)
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="durable prior dispatch",
        ) as raised:
            second_runner.run(second_runtime, effect)

        assert raised.value.details["journal_state"] == "completed"
        assert second_runtime.calls == []
        assert second_journal.entry("request-1").result_payload == {
            "ok": True,
            "request_id": "request-1",
        }
    finally:
        second_runner.close()


def test_pending_request_can_dispatch_after_restart(tmp_path) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    journal = SQLitePhysicalEffectJournal(path)
    journal.reserve(effect)
    journal.close()

    runtime = RecordingRuntime()
    restarted_journal = SQLitePhysicalEffectJournal(path)
    runner = SynchronousEffectRunner(journal=restarted_journal)
    try:
        result = runner.run(runtime, effect)

        assert result["ok"] is True
        assert runtime.calls == [request]
        assert restarted_journal.entry("request-1").state == "completed"
    finally:
        runner.close()


def test_matching_pending_hash_with_missing_schema_is_relabelled(
    tmp_path,
) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    journal = SQLitePhysicalEffectJournal(path)
    journal.reserve(effect)
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE physical_effect_journal
            SET request_hash_schema = 'legacy/unknown'
            WHERE request_id = ?
            """,
            (effect.request_id,),
        )

    runtime = RecordingRuntime()
    restarted_journal = SQLitePhysicalEffectJournal(path)
    runner = SynchronousEffectRunner(journal=restarted_journal)
    try:
        runner.run(runtime, effect)

        entry = restarted_journal.entry(effect.request_id)
        assert entry.request_hash_schema == PHYSICAL_REQUEST_HASH_SCHEMA
        assert entry.state == "completed"
        assert len(runtime.calls) == 1
    finally:
        runner.close()


def test_legacy_pending_hash_is_not_migrated_from_lossy_payload(
    tmp_path,
) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    request = Request("request-1", {"sequence": [1, 2]})
    effect = PhysicalEffect.from_request(request.request_id, request)
    journal = SQLitePhysicalEffectJournal(path)
    journal.reserve(effect)
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE physical_effect_journal
            SET request_hash = 'legacy-v1-hash',
                request_hash_schema = 'legacy/unknown'
            WHERE request_id = ?
            """,
            (effect.request_id,),
        )

    runtime = RecordingRuntime()
    restarted = SynchronousEffectRunner(
        journal=SQLitePhysicalEffectJournal(path)
    )
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="hash schema differs",
        ) as raised:
            restarted.run(runtime, effect)

        assert raised.value.details["dispatch_stage"] == "reservation"
        assert (
            raised.value.details["existing_request_hash_schema"]
            == "legacy/unknown"
        )
        assert runtime.calls == []
    finally:
        restarted.close()


def test_interrupted_dispatch_requires_explicit_owner_recovery(
    tmp_path,
) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    journal = SQLitePhysicalEffectJournal(path)
    journal.reserve(effect)
    journal.mark_dispatching(effect.request_id, effect.request_hash)
    journal.close()

    runtime = RecordingRuntime()
    restarted_journal = SQLitePhysicalEffectJournal(path)
    runner = SynchronousEffectRunner(journal=restarted_journal)
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="durable prior dispatch",
        ) as before_recovery:
            runner.run(runtime, effect)

        entry = restarted_journal.entry("request-1")
        assert (
            before_recovery.value.details["journal_state"]
            == "dispatching"
        )
        assert entry.state == "dispatching"
        assert runtime.calls == []

        recovered = restarted_journal.recover_interrupted_dispatches()
        assert [item.request_id for item in recovered] == ["request-1"]

        with pytest.raises(
            PhysicalEffectError,
            match="durable prior dispatch",
        ) as after_recovery:
            runner.run(runtime, effect)

        entry = restarted_journal.entry("request-1")
        assert (
            after_recovery.value.details["journal_state"]
            == "outcome_unknown"
        )
        assert entry.state == "outcome_unknown"
        assert "interrupted dispatch" in entry.error
        assert runtime.calls == []
    finally:
        runner.close()


def test_hash_conflict_is_rejected_across_restart(tmp_path) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    first = Request("request-1", {"target": "zone-a"})
    second = Request("request-1", {"target": "zone-b"})
    first_runner = SynchronousEffectRunner(
        journal=SQLitePhysicalEffectJournal(path)
    )
    first_runner.run(
        RecordingRuntime(),
        PhysicalEffect.from_request("request-1", first),
    )
    first_runner.close()

    runtime = RecordingRuntime()
    restarted = SynchronousEffectRunner(
        journal=SQLitePhysicalEffectJournal(path)
    )
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="different payload",
        ) as raised:
            restarted.run(
                runtime,
                PhysicalEffect.from_request("request-1", second),
            )

        assert raised.value.details["journal_state"] == "completed"
        assert runtime.calls == []
    finally:
        restarted.close()


def test_closed_runner_rejects_new_dispatch() -> None:
    runtime = RecordingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)

    runner.close()
    runner.close()

    with pytest.raises(
        PhysicalEffectError,
        match="runner is closed",
    ) as raised:
        runner.run(runtime, effect)
    assert raised.value.details["dispatch_stage"] == "not_started"
    assert runtime.calls == []


def test_reservation_sqlite_error_is_pre_dispatch_failure(
    tmp_path,
    monkeypatch,
) -> None:
    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    runner = SynchronousEffectRunner(journal=journal)
    runtime = RecordingRuntime()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)

    def fail_reservation(_effect):
        raise sqlite3.OperationalError("injected reservation failure")

    monkeypatch.setattr(journal, "reserve", fail_reservation)
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="reservation failed",
        ) as raised:
            runner.run(runtime, effect)

        assert raised.value.details["dispatch_stage"] == "reservation"
        assert "OperationalError" in raised.value.details["journal_error"]
        assert runtime.calls == []
    finally:
        runner.close()


def test_predispatch_runtime_rejection_returns_journal_to_pending(
    tmp_path,
) -> None:
    class PredispatchError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("guard rejected")
            self.diagnostic = Diagnostic(
                "GUARD_FAILED",
                "guard rejected",
                details={
                    "dispatch_stage": "not_started",
                    "physical_dispatch_started": False,
                    "physical_outcome_known": True,
                    "requires_reconciliation": False,
                },
            )

    class RejectingRuntime(RecordingRuntime):
        def execute(self, request):
            self.calls.append(request)
            raise PredispatchError()

    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    runner = SynchronousEffectRunner(journal=journal)
    runtime = RejectingRuntime()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    try:
        for _ in range(2):
            with pytest.raises(
                PhysicalEffectError,
                match="rejected before dispatch",
            ) as raised:
                runner.run(runtime, effect)
            assert (
                raised.value.details["physical_dispatch_started"]
                is False
            )
            assert journal.entry(effect.request_id).state == "pending"
            assert runner.records() == {}

        assert len(runtime.calls) == 2
    finally:
        runner.close()


def test_incomplete_predispatch_evidence_remains_outcome_unknown(
    tmp_path,
) -> None:
    class ContradictoryError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("untrusted adapter evidence")
            self.diagnostic = Diagnostic(
                "GUARD_FAILED",
                "untrusted adapter evidence",
                details={
                    "dispatch_stage": "not_started",
                    "physical_dispatch_started": False,
                    "physical_outcome_known": False,
                    "requires_reconciliation": False,
                },
            )

    class ContradictoryRuntime(RecordingRuntime):
        def execute(self, request):
            self.calls.append(request)
            raise ContradictoryError()

    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    runner = SynchronousEffectRunner(journal=journal)
    runtime = ContradictoryRuntime()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="execution raised",
        ) as raised:
            runner.run(runtime, effect)

        assert raised.value.details["dispatch_stage"] == "execution"
        assert (
            raised.value.details["source_dispatch_stage"]
            == "not_started"
        )
        assert (
            raised.value.details["physical_dispatch_started"] is True
        )
        assert (
            raised.value.details["requires_reconciliation"] is True
        )
        assert journal.entry(effect.request_id).state == "outcome_unknown"
        assert len(runtime.calls) == 1
    finally:
        runner.close()


def test_two_runners_classify_journal_race_as_prior_dispatch(
    tmp_path,
) -> None:
    path = tmp_path / "physical-effects.sqlite3"
    barrier = Barrier(2)

    class CoordinatedJournal:
        def __init__(self) -> None:
            self.delegate = SQLitePhysicalEffectJournal(path)

        def reserve(self, effect):
            reservation = self.delegate.reserve(effect)
            barrier.wait(timeout=5)
            return reservation

        def mark_dispatching(self, *args):
            return self.delegate.mark_dispatching(*args)

        def mark_completed(self, *args):
            return self.delegate.mark_completed(*args)

        def mark_not_dispatched(self, *args):
            return self.delegate.mark_not_dispatched(*args)

        def mark_outcome_unknown(self, *args):
            return self.delegate.mark_outcome_unknown(*args)

        def entry(self, *args):
            return self.delegate.entry(*args)

        def close(self):
            self.delegate.close()

    first_runner = SynchronousEffectRunner(
        journal=CoordinatedJournal()
    )
    second_runner = SynchronousEffectRunner(
        journal=CoordinatedJournal()
    )
    runtime = RecordingRuntime()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    results = []
    errors = []

    def invoke(runner) -> None:
        try:
            results.append(runner.run(runtime, effect))
        except BaseException as exc:
            errors.append(exc)

    first = Thread(target=invoke, args=(first_runner,))
    second = Thread(target=invoke, args=(second_runner,))
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)
    try:
        assert not first.is_alive()
        assert not second.is_alive()
        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], PhysicalEffectError)
        assert errors[0].details["dispatch_stage"] == "prior_dispatch"
        assert errors[0].details["journal_state"] in {
            "dispatching",
            "completed",
        }
        assert len(runtime.calls) == 1
    finally:
        first_runner.close()
        second_runner.close()


def test_rollback_failure_poisons_journal_connection(tmp_path) -> None:
    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    delegate = journal._connection

    class RollbackFailingConnection:
        @property
        def in_transaction(self):
            return delegate.in_transaction

        def execute(self, statement, *args):
            if statement == "ROLLBACK":
                raise sqlite3.OperationalError(
                    "injected rollback failure"
                )
            return delegate.execute(statement, *args)

        def close(self):
            delegate.close()

    journal._connection = RollbackFailingConnection()
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="Unknown physical request",
        ):
            journal.mark_dispatching("missing", "hash")

        with pytest.raises(
            PhysicalEffectError,
            match="connection is poisoned",
        ) as raised:
            journal.entry("missing")
        assert "rollback failed" in raised.value.details["journal_error"]
    finally:
        journal.close()


def test_runtime_exception_is_recorded_as_execution_unknown() -> None:
    class FailingRuntime(RecordingRuntime):
        def execute(self, request):
            self.calls.append(request)
            raise RuntimeError("injected physical failure")

    runtime = FailingRuntime()
    runner = SynchronousEffectRunner()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)

    with pytest.raises(
        PhysicalEffectError,
        match="execution raised",
    ) as raised:
        runner.run(runtime, effect)

    assert raised.value.details["dispatch_stage"] == "execution"
    assert raised.value.details["journal_state"] == "outcome_unknown"
    assert runner.records()["request-1"]["state"] == "outcome_unknown"
    assert len(runtime.calls) == 1


def test_completed_effect_is_unknown_when_journal_commit_fails(
    tmp_path,
) -> None:
    journal = SQLitePhysicalEffectJournal(
        tmp_path / "physical-effects.sqlite3"
    )
    original_mark_completed = journal.mark_completed

    def fail_completion(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("simulated completion commit failure")

    journal.mark_completed = fail_completion
    runner = SynchronousEffectRunner(journal=journal)
    runtime = RecordingRuntime()
    request = Request("request-1", {"target": "zone-a"})
    effect = PhysicalEffect.from_request(request.request_id, request)
    try:
        with pytest.raises(
            PhysicalEffectError,
            match="could not be committed",
        ) as raised:
            runner.run(runtime, effect)

        assert runtime.calls == [request]
        assert raised.value.details["journal_state"] == "outcome_unknown"
        assert journal.entry("request-1").state == "outcome_unknown"
        assert runner.records()["request-1"]["state"] == "outcome_unknown"
    finally:
        journal.mark_completed = original_mark_completed
        runner.close()
