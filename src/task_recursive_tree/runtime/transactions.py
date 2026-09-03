from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from uuid import uuid4


class TransactionStatus(str, Enum):
    STARTED = "started"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED_RECONCILED = "failed_reconciled"


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    request_id: str
    status: TransactionStatus
    error: str | None = None


class TransactionLedger:
    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, TransactionRecord] = {}

    def begin(self, request_id: str) -> TransactionRecord:
        record = TransactionRecord(
            transaction_id=f"tx-{uuid4().hex}",
            request_id=request_id,
            status=TransactionStatus.STARTED,
        )
        with self._lock:
            self._records[record.transaction_id] = record
        return record

    def commit(self, transaction_id: str) -> TransactionRecord:
        return self._update(transaction_id, TransactionStatus.COMMITTED)

    def rollback(
        self, transaction_id: str, error: str
    ) -> TransactionRecord:
        return self._update(
            transaction_id, TransactionStatus.ROLLED_BACK, error
        )

    def fail_reconciled(
        self, transaction_id: str, error: str
    ) -> TransactionRecord:
        return self._update(
            transaction_id, TransactionStatus.FAILED_RECONCILED, error
        )

    def records(self) -> tuple[TransactionRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def _update(
        self,
        transaction_id: str,
        status: TransactionStatus,
        error: str | None = None,
    ) -> TransactionRecord:
        with self._lock:
            current = self._records[transaction_id]
            updated = replace(current, status=status, error=error)
            self._records[transaction_id] = updated
            return updated
