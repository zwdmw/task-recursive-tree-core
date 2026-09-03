from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping, Protocol


PHYSICAL_REQUEST_HASH_SCHEMA = (
    "task-recursive-tree/physical-request-hash/v2"
)


class PhysicalActionRuntime(Protocol):
    def execute(self, request: object) -> object: ...


class EffectRunner(Protocol):
    def run(
        self,
        runtime: PhysicalActionRuntime,
        effect: PhysicalEffect,
    ) -> object: ...


class PhysicalEffectError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
        diagnostic: object | None = None,
    ) -> None:
        super().__init__(message)
        self.details = MappingProxyType(dict(details or {}))
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class PhysicalEffect:
    request_id: str
    request_hash: str
    request: object

    def __post_init__(self) -> None:
        expected_hash = _effect_request_hash(
            self.request_id,
            self.request,
        )
        if self.request_hash != expected_hash:
            raise PhysicalEffectError(
                "Physical effect hash does not match its request payload",
                details={
                    "request_id": self.request_id,
                    "request_hash": self.request_hash,
                    "expected_request_hash": expected_hash,
                    "dispatch_stage": "not_started",
                },
            )

    def validated_snapshot(self) -> PhysicalEffect:
        try:
            request = _snapshot_value(self.request)
        except Exception as exc:
            raise PhysicalEffectError(
                "Physical request cannot be snapshotted safely",
                details={
                    "request_id": self.request_id,
                    "request_hash": self.request_hash,
                    "dispatch_stage": "not_started",
                    "snapshot_error": f"{type(exc).__name__}: {exc}",
                },
            ) from exc
        return PhysicalEffect(
            request_id=self.request_id,
            request_hash=self.request_hash,
            request=request,
        )

    @classmethod
    def from_request(
        cls,
        request_id: str,
        request: object,
    ) -> PhysicalEffect:
        return cls(
            request_id=str(request_id),
            request_hash=_effect_request_hash(request_id, request),
            request=request,
        )


@dataclass(frozen=True)
class EffectJournalEntry:
    request_id: str
    request_hash: str
    request_hash_schema: str
    state: str
    request_payload: Any
    result_payload: Any = None
    error: str | None = None


@dataclass(frozen=True)
class EffectReservation:
    entry: EffectJournalEntry
    created: bool


class PhysicalEffectJournal(Protocol):
    def reserve(self, effect: PhysicalEffect) -> EffectReservation: ...

    def mark_dispatching(
        self,
        request_id: str,
        request_hash: str,
    ) -> EffectJournalEntry: ...

    def mark_completed(
        self,
        request_id: str,
        request_hash: str,
        result_payload: Any,
    ) -> EffectJournalEntry: ...

    def mark_not_dispatched(
        self,
        request_id: str,
        request_hash: str,
        error: str,
    ) -> EffectJournalEntry: ...

    def mark_outcome_unknown(
        self,
        request_id: str,
        request_hash: str,
        error: str,
    ) -> EffectJournalEntry: ...

    def entry(self, request_id: str) -> EffectJournalEntry | None: ...

    def close(self) -> None: ...


@dataclass
class _JournalRecord:
    request_hash: str
    state: str
    result: object | None = None
    error: str | None = None


class SynchronousEffectRunner:
    """Serialized runner with optional durable request idempotency."""

    def __init__(
        self,
        *,
        journal: PhysicalEffectJournal | None = None,
    ) -> None:
        self._lock = RLock()
        self._dispatch_lock = RLock()
        self._records: dict[str, _JournalRecord] = {}
        self._journal = journal
        self._closed = False

    def run(
        self,
        runtime: PhysicalActionRuntime,
        effect: PhysicalEffect,
    ) -> object:
        with self._dispatch_lock:
            if self._closed:
                raise PhysicalEffectError(
                    "Physical effect runner is closed",
                    details={
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "dispatch_stage": "not_started",
                    },
                )
            return self._run_serialized(runtime, effect)

    def _run_serialized(
        self,
        runtime: PhysicalActionRuntime,
        effect: PhysicalEffect,
    ) -> object:
        try:
            effect = effect.validated_snapshot()
        except PhysicalEffectError as exc:
            raise _with_effect_details(
                exc,
                dispatch_stage="not_started",
            ) from exc
        with self._lock:
            existing = self._records.get(effect.request_id)
            if existing is not None:
                if existing.request_hash != effect.request_hash:
                    raise PhysicalEffectError(
                        "Physical request ID was reused with a different "
                        f"payload: {effect.request_id}",
                        details={
                            "request_id": effect.request_id,
                            "request_hash": effect.request_hash,
                            "existing_request_hash": existing.request_hash,
                            "journal_state": existing.state,
                            "dispatch_stage": "prior_dispatch",
                        },
                    )
                if existing.state == "completed":
                    return existing.result
                raise PhysicalEffectError(
                    "Physical request has an unresolved prior dispatch: "
                    f"{effect.request_id}",
                    details={
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "journal_state": existing.state,
                        "dispatch_stage": "prior_dispatch",
                    },
                )

        journal_dispatching = False
        if self._journal is not None:
            try:
                reservation = self._journal.reserve(effect)
            except PhysicalEffectError as exc:
                journal_state = str(
                    exc.details.get("journal_state") or ""
                )
                stage = (
                    "prior_dispatch"
                    if journal_state in {
                        "dispatching",
                        "completed",
                        "outcome_unknown",
                    }
                    else "reservation"
                )
                raise _with_effect_details(
                    exc,
                    dispatch_stage=stage,
                ) from exc
            except Exception as exc:
                raise PhysicalEffectError(
                    "Physical effect reservation failed",
                    details={
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "dispatch_stage": "reservation",
                        "journal_error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                    },
                ) from exc
            if not reservation.created:
                journal_state = reservation.entry.state
                if journal_state != "pending":
                    raise PhysicalEffectError(
                        "Physical request has a durable prior dispatch: "
                        f"{effect.request_id}",
                        details={
                            "request_id": effect.request_id,
                            "request_hash": effect.request_hash,
                            "journal_state": journal_state,
                            "journal_result": (
                                reservation.entry.result_payload
                            ),
                            "journal_error": reservation.entry.error,
                            "dispatch_stage": "prior_dispatch",
                        },
                    )
            try:
                self._journal.mark_dispatching(
                    effect.request_id,
                    effect.request_hash,
                )
            except PhysicalEffectError as exc:
                journal_state = str(
                    exc.details.get("journal_state") or ""
                )
                stage = (
                    "prior_dispatch"
                    if journal_state in {
                        "dispatching",
                        "completed",
                        "outcome_unknown",
                    }
                    else "reservation"
                )
                raise _with_effect_details(
                    exc,
                    dispatch_stage=stage,
                ) from exc
            except Exception as exc:
                raise PhysicalEffectError(
                    "Physical effect dispatch reservation failed",
                    details={
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "dispatch_stage": "reservation",
                        "journal_error": (
                            f"{type(exc).__name__}: {exc}"
                        ),
                    },
                ) from exc
            journal_dispatching = True

        with self._lock:
            self._records[effect.request_id] = _JournalRecord(
                request_hash=effect.request_hash,
                state="dispatching",
            )

        try:
            result = runtime.execute(effect.request)
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            source_details = _exception_effect_details(exc)
            if _exception_proves_not_dispatched(source_details):
                journal_error: str | None = None
                journal_state = "pending"
                if journal_dispatching and self._journal is not None:
                    try:
                        entry = self._journal.mark_not_dispatched(
                            effect.request_id,
                            effect.request_hash,
                            error,
                        )
                    except BaseException as journal_exc:
                        journal_error = (
                            f"{type(journal_exc).__name__}: "
                            f"{journal_exc}"
                        )
                        journal_state = "commit_ambiguous"
                    else:
                        journal_state = entry.state
                with self._lock:
                    if journal_error is None:
                        self._records.pop(effect.request_id, None)
                    else:
                        record = self._records[effect.request_id]
                        record.state = "not_dispatched_commit_ambiguous"
                        record.error = (
                            f"{error}; durable update failed: "
                            f"{journal_error}"
                        )
                if isinstance(exc, Exception):
                    stage = str(
                        source_details.get("dispatch_stage")
                        or "not_started"
                    ).casefold()
                    details = dict(source_details)
                    details.update(
                        {
                            "request_id": effect.request_id,
                            "request_hash": effect.request_hash,
                            "journal_state": journal_state,
                            "dispatch_stage": stage,
                            "physical_dispatch_started": False,
                            "physical_outcome_known": True,
                        }
                    )
                    if journal_error is not None:
                        details["journal_error"] = journal_error
                    raise PhysicalEffectError(
                        "Physical effect was rejected before dispatch",
                        details=details,
                        diagnostic=getattr(exc, "diagnostic", None),
                    ) from exc
                if journal_error is not None:
                    add_note = getattr(exc, "add_note", None)
                    if callable(add_note):
                        add_note(
                            "Durable pre-dispatch update failed: "
                            f"{journal_error}"
                        )
                raise

            journal_error: str | None = None
            with self._lock:
                record = self._records[effect.request_id]
                record.state = "outcome_unknown"
                record.error = error
            if journal_dispatching and self._journal is not None:
                try:
                    self._journal.mark_outcome_unknown(
                        effect.request_id,
                        effect.request_hash,
                        error,
                    )
                except BaseException as journal_exc:
                    journal_error = (
                        f"{type(journal_exc).__name__}: {journal_exc}"
                    )
                    with self._lock:
                        record = self._records[effect.request_id]
                        record.error = (
                            f"{error}; durable update failed: "
                            f"{journal_error}"
                        )
            if isinstance(exc, Exception):
                details = dict(source_details)
                source_stage = details.get("dispatch_stage")
                if source_stage:
                    details["source_dispatch_stage"] = source_stage
                details.update(
                    {
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "journal_state": (
                            "commit_ambiguous"
                            if journal_error is not None
                            else "outcome_unknown"
                        ),
                        "dispatch_stage": "execution",
                        "physical_dispatch_started": True,
                        "physical_outcome_known": False,
                        "requires_reconciliation": True,
                        "effect_exception": error,
                    }
                )
                if journal_error is not None:
                    details["journal_error"] = journal_error
                raise PhysicalEffectError(
                    (
                        "Physical effect raised and its durable outcome "
                        "could not be committed"
                        if journal_error is not None
                        else "Physical effect execution raised"
                    ),
                    details=details,
                    diagnostic=getattr(exc, "diagnostic", None),
                ) from exc
            if journal_error is not None:
                add_note = getattr(exc, "add_note", None)
                if callable(add_note):
                    add_note(
                        "Durable physical-effect update failed: "
                        f"{journal_error}"
                    )
            raise

        if journal_dispatching and self._journal is not None:
            try:
                self._journal.mark_completed(
                    effect.request_id,
                    effect.request_hash,
                    canonical_request_payload(result),
                )
            except BaseException as exc:
                journal_error = f"{type(exc).__name__}: {exc}"
                journal_state = "commit_ambiguous"
                journal_result = None
                entry_reader = getattr(self._journal, "entry", None)
                if callable(entry_reader):
                    try:
                        entry = entry_reader(effect.request_id)
                    except BaseException as read_exc:
                        journal_error = (
                            f"{journal_error}; journal read failed: "
                            f"{type(read_exc).__name__}: {read_exc}"
                        )
                    else:
                        if entry is not None:
                            journal_state = entry.state
                            journal_result = entry.result_payload
                if journal_state == "dispatching":
                    try:
                        entry = self._journal.mark_outcome_unknown(
                            effect.request_id,
                            effect.request_hash,
                            (
                                "Physical effect returned but durable "
                                f"completion failed: {journal_error}"
                            ),
                        )
                    except BaseException as mark_exc:
                        journal_error = (
                            f"{journal_error}; outcome-unknown update "
                            f"failed: {type(mark_exc).__name__}: {mark_exc}"
                        )
                    else:
                        journal_state = entry.state
                with self._lock:
                    record = self._records[effect.request_id]
                    record.state = "outcome_unknown"
                    record.error = (
                        "Physical effect returned but journal commit failed: "
                        f"{journal_error}"
                    )
                raise PhysicalEffectError(
                    "Physical effect returned but its durable result "
                    "could not be committed",
                    details={
                        "request_id": effect.request_id,
                        "request_hash": effect.request_hash,
                        "journal_state": journal_state,
                        "journal_result": journal_result,
                        "journal_error": journal_error,
                        "dispatch_stage": "completion",
                        "physical_dispatch_started": True,
                        "physical_outcome_known": False,
                        "requires_reconciliation": True,
                    },
                ) from exc

        with self._lock:
            record = self._records[effect.request_id]
            record.state = "completed"
            record.result = result
        return result

    def records(self) -> Mapping[str, Mapping[str, Any]]:
        with self._lock:
            payload = {
                request_id: MappingProxyType(
                    {
                        "request_hash": record.request_hash,
                        "state": record.state,
                        "error": record.error,
                    }
                )
                for request_id, record in self._records.items()
            }
        return MappingProxyType(payload)

    def close(self) -> None:
        with self._dispatch_lock:
            if self._closed:
                return
            if self._journal is not None:
                self._journal.close()
            self._closed = True


def canonical_request_hash(request: object) -> str:
    payload = {
        "$schema": PHYSICAL_REQUEST_HASH_SCHEMA,
        "$value": _canonical_hash_value(request),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def canonical_request_payload(value: object) -> Any:
    return _canonical_value(value)


def _canonical_hash_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return {
            "$kind": "enum",
            "$type": _type_name(value),
            "$value": _canonical_hash_value(value.value),
        }
    if is_dataclass(value):
        return {
            "$kind": "dataclass",
            "$type": _type_name(value),
            "$fields": {
                item.name: _canonical_hash_value(
                    getattr(value, item.name)
                )
                for item in fields(value)
            },
        }
    if isinstance(value, Mapping):
        items = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "Physical request mappings require string keys"
                )
            items[key] = _canonical_hash_value(item)
        return {"$kind": "mapping", "$items": items}
    if isinstance(value, tuple):
        return {
            "$kind": "tuple",
            "$items": [_canonical_hash_value(item) for item in value],
        }
    if isinstance(value, list):
        return {
            "$kind": "list",
            "$items": [_canonical_hash_value(item) for item in value],
        }
    if isinstance(value, frozenset):
        return {
            "$kind": "frozenset",
            "$items": _sorted_hash_items(value),
        }
    if isinstance(value, set):
        return {
            "$kind": "set",
            "$items": _sorted_hash_items(value),
        }
    if isinstance(value, bytes):
        return {"$kind": "bytes", "$value": value.hex()}
    if value is None:
        return {"$kind": "none"}
    if isinstance(value, bool):
        return {"$kind": "bool", "$value": value}
    if isinstance(value, int):
        return {"$kind": "int", "$value": str(value)}
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(
                "Physical requests require finite floating-point values"
            )
        return {"$kind": "float", "$value": value.hex()}
    if isinstance(value, str):
        return {"$kind": "str", "$value": value}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return {
            "$kind": "to_dict",
            "$type": _type_name(value),
            "$value": _canonical_hash_value(to_dict()),
        }
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        slots = _declared_state_slots(value)
        if slots:
            raise TypeError(
                "Physical request objects with slot state require an "
                f"explicit dataclass schema: {_type_name(value)} "
                f"({', '.join(slots)})"
            )
        return {
            "$kind": "object",
            "$type": _type_name(value),
            "$attributes": _canonical_hash_value(attributes),
        }
    raise TypeError(
        "Unsupported physical request value type: "
        f"{_type_name(value)}"
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return {
            "$enum": _type_name(value),
            "value": _canonical_value(value.value),
        }
    if is_dataclass(value):
        return {
            "$type": _type_name(value),
            "$fields": {
                item.name: _canonical_value(getattr(value, item.name))
                for item in fields(value)
            },
        }
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "Physical request mappings require string keys"
                )
            result[key] = _canonical_value(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _canonical_value(to_dict())
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return {
            "$type": _type_name(value),
            "$attributes": _canonical_value(attributes),
        }
    raise TypeError(
        "Unsupported physical request value type: "
        f"{_type_name(value)}"
    )


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value
    if is_dataclass(value):
        payload = {
            item.name: _snapshot_value(getattr(value, item.name))
            for item in fields(value)
        }
        try:
            return type(value)(**payload)
        except TypeError as exc:
            raise TypeError(
                f"Cannot reconstruct dataclass {_type_name(value)}"
            ) from exc
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    "Physical request mappings require string keys"
                )
            result[key] = _snapshot_value(item)
        return result
    if isinstance(value, tuple):
        return tuple(_snapshot_value(item) for item in value)
    if isinstance(value, list):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, frozenset):
        return frozenset(_snapshot_value(item) for item in value)
    if isinstance(value, set):
        return {_snapshot_value(item) for item in value}
    if isinstance(value, bytes):
        return bytes(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        slots = _declared_state_slots(value)
        if slots:
            raise TypeError(
                "Physical request objects with slot state require an "
                f"explicit dataclass schema: {_type_name(value)} "
                f"({', '.join(slots)})"
            )
        try:
            snapshot = type(value).__new__(type(value))
            for key, item in attributes.items():
                setattr(snapshot, key, _snapshot_value(item))
        except (AttributeError, TypeError) as exc:
            raise TypeError(
                f"Cannot snapshot request object {_type_name(value)}"
            ) from exc
        return snapshot
    raise TypeError(
        "Unsupported physical request value type: "
        f"{_type_name(value)}"
    )


def _with_effect_details(
    error: PhysicalEffectError,
    **details: Any,
) -> PhysicalEffectError:
    payload = dict(error.details)
    payload.update(details)
    return PhysicalEffectError(
        str(error),
        details=payload,
        diagnostic=error.diagnostic,
    )


def _effect_request_hash(
    request_id: object,
    request: object,
) -> str:
    try:
        return canonical_request_hash(request)
    except Exception as exc:
        raise PhysicalEffectError(
            "Physical request cannot be canonicalized safely",
            details={
                "request_id": str(request_id),
                "dispatch_stage": "not_started",
                "canonicalization_error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            },
        ) from exc


def _sorted_hash_items(values: object) -> list[Any]:
    items = [_canonical_hash_value(item) for item in values]
    return sorted(
        items,
        key=lambda item: json.dumps(
            item,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def _exception_effect_details(error: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {}
    diagnostic = getattr(error, "diagnostic", None)
    diagnostic_details = getattr(diagnostic, "details", None)
    if isinstance(diagnostic_details, Mapping):
        details.update(diagnostic_details)
    if isinstance(error, PhysicalEffectError):
        details.update(error.details)
    return details


def _exception_proves_not_dispatched(
    details: Mapping[str, Any],
) -> bool:
    stage = str(details.get("dispatch_stage") or "").casefold()
    return (
        stage in {"not_started", "reservation"}
        and details.get("physical_dispatch_started") is False
        and details.get("physical_outcome_known") is True
        and details.get("requires_reconciliation") is False
    )


def _declared_state_slots(value: object) -> tuple[str, ...]:
    result: list[str] = []
    for cls in type(value).__mro__:
        raw_slots = getattr(cls, "__slots__", ())
        slots = (raw_slots,) if isinstance(raw_slots, str) else raw_slots
        for slot in slots:
            name = str(slot)
            if name not in {"__dict__", "__weakref__"}:
                result.append(name)
    return tuple(dict.fromkeys(result))


def _type_name(value: object) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"
