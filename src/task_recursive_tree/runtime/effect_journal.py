from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator

from task_recursive_tree.runtime.effects import (
    EffectJournalEntry,
    EffectReservation,
    PHYSICAL_REQUEST_HASH_SCHEMA,
    PhysicalEffect,
    PhysicalEffectError,
    canonical_request_payload,
)


class SQLitePhysicalEffectJournal:
    """Durable request journal for at-most-once physical dispatch."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._closed = False
        self._poisoned_reason: str | None = None
        self._connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            with self._lock:
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute("PRAGMA synchronous=FULL")
                self._connection.execute("PRAGMA foreign_keys=ON")
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS physical_effect_journal (
                        request_id TEXT PRIMARY KEY,
                        request_hash TEXT NOT NULL,
                        request_hash_schema TEXT NOT NULL DEFAULT
                            'legacy/unknown',
                        state TEXT NOT NULL CHECK (
                            state IN (
                                'pending',
                                'dispatching',
                                'completed',
                                'outcome_unknown'
                            )
                        ),
                        request_json TEXT NOT NULL,
                        result_json TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                self._ensure_hash_schema_column()
        except BaseException:
            self._connection.close()
            self._closed = True
            raise

    @property
    def journal_mode(self) -> str:
        with self._lock:
            self._require_usable()
            row = self._connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()
        return str(row[0]).casefold()

    def reserve(self, effect: PhysicalEffect) -> EffectReservation:
        self._require_usable()
        request_payload = canonical_request_payload(effect.request)
        request_json = _json_dump(request_payload)
        now = _timestamp()
        with self._transaction():
            row = self._select(effect.request_id)
            if row is None:
                self._connection.execute(
                    """
                    INSERT INTO physical_effect_journal (
                        request_id,
                        request_hash,
                        request_hash_schema,
                        state,
                        request_json,
                        result_json,
                        error,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
                    """,
                    (
                        effect.request_id,
                        effect.request_hash,
                        PHYSICAL_REQUEST_HASH_SCHEMA,
                        request_json,
                        now,
                        now,
                    ),
                )
                row = self._select(effect.request_id)
                return EffectReservation(
                    entry=self._entry(row),
                    created=True,
                )
            row = self._migrate_pending_hash_schema(
                row,
                effect,
                now,
            )
            self._require_hash(row, effect.request_hash)
            return EffectReservation(
                entry=self._entry(row),
                created=False,
            )

    def mark_dispatching(
        self,
        request_id: str,
        request_hash: str,
    ) -> EffectJournalEntry:
        self._require_usable()
        return self._transition(
            request_id,
            request_hash,
            expected_states={"pending"},
            state="dispatching",
        )

    def mark_completed(
        self,
        request_id: str,
        request_hash: str,
        result_payload: Any,
    ) -> EffectJournalEntry:
        self._require_usable()
        return self._transition(
            request_id,
            request_hash,
            expected_states={"dispatching"},
            state="completed",
            result_json=_json_dump(result_payload),
        )

    def mark_outcome_unknown(
        self,
        request_id: str,
        request_hash: str,
        error: str,
    ) -> EffectJournalEntry:
        self._require_usable()
        return self._transition(
            request_id,
            request_hash,
            expected_states={"dispatching", "outcome_unknown"},
            state="outcome_unknown",
            error=str(error),
        )

    def mark_not_dispatched(
        self,
        request_id: str,
        request_hash: str,
        error: str,
    ) -> EffectJournalEntry:
        self._require_usable()
        return self._transition(
            request_id,
            request_hash,
            expected_states={"dispatching"},
            state="pending",
            error=str(error),
        )

    def entry(self, request_id: str) -> EffectJournalEntry | None:
        with self._lock:
            self._require_usable()
            row = self._select(str(request_id))
            return self._entry(row) if row is not None else None

    def recover_interrupted_dispatches(
        self,
        error: str = "Recovered an interrupted dispatch",
    ) -> tuple[EffectJournalEntry, ...]:
        """Mark dispatches left by a previous exclusive owner as unknown."""

        self._require_usable()
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT request_id
                FROM physical_effect_journal
                WHERE state = 'dispatching'
                ORDER BY request_id
                """
            ).fetchall()
            if not rows:
                return ()
            now = _timestamp()
            self._connection.execute(
                """
                UPDATE physical_effect_journal
                SET state = 'outcome_unknown',
                    error = ?,
                    updated_at = ?
                WHERE state = 'dispatching'
                """,
                (str(error), now),
            )
            return tuple(
                self._entry(self._select(str(row["request_id"])))
                for row in rows
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def _transition(
        self,
        request_id: str,
        request_hash: str,
        *,
        expected_states: set[str],
        state: str,
        result_json: str | None = None,
        error: str | None = None,
    ) -> EffectJournalEntry:
        with self._transaction():
            row = self._select(request_id)
            if row is None:
                raise PhysicalEffectError(
                    f"Unknown physical request: {request_id}",
                    details={
                        "request_id": request_id,
                        "request_hash": request_hash,
                    },
                )
            self._require_hash(row, request_hash)
            current_state = str(row["state"])
            if current_state not in expected_states:
                raise PhysicalEffectError(
                    "Invalid durable physical effect transition "
                    f"{current_state!r} -> {state!r}",
                    details={
                        "request_id": request_id,
                        "request_hash": request_hash,
                        "journal_state": current_state,
                        "requested_state": state,
                    },
                )
            self._connection.execute(
                """
                UPDATE physical_effect_journal
                SET state = ?,
                    result_json = COALESCE(?, result_json),
                    error = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (
                    state,
                    result_json,
                    error,
                    _timestamp(),
                    request_id,
                ),
            )
            updated = self._select(request_id)
            return self._entry(updated)

    def _select(self, request_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT request_id,
                   request_hash,
                   request_hash_schema,
                   state,
                   request_json,
                   result_json,
                   error
            FROM physical_effect_journal
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

    @staticmethod
    def _entry(row: sqlite3.Row) -> EffectJournalEntry:
        return EffectJournalEntry(
            request_id=str(row["request_id"]),
            request_hash=str(row["request_hash"]),
            request_hash_schema=str(row["request_hash_schema"]),
            state=str(row["state"]),
            request_payload=json.loads(str(row["request_json"])),
            result_payload=(
                json.loads(str(row["result_json"]))
                if row["result_json"] is not None
                else None
            ),
            error=(
                str(row["error"])
                if row["error"] is not None
                else None
            ),
        )

    @staticmethod
    def _require_hash(
        row: sqlite3.Row,
        request_hash: str,
    ) -> None:
        existing_schema = str(row["request_hash_schema"])
        if existing_schema != PHYSICAL_REQUEST_HASH_SCHEMA:
            raise PhysicalEffectError(
                "Physical request hash schema differs from the journal",
                details={
                    "request_id": str(row["request_id"]),
                    "request_hash": request_hash,
                    "existing_request_hash": str(row["request_hash"]),
                    "request_hash_schema": (
                        PHYSICAL_REQUEST_HASH_SCHEMA
                    ),
                    "existing_request_hash_schema": existing_schema,
                    "journal_state": str(row["state"]),
                },
            )
        existing_hash = str(row["request_hash"])
        if existing_hash != request_hash:
            raise PhysicalEffectError(
                "Physical request ID was reused with a different payload: "
                f"{row['request_id']}",
                details={
                    "request_id": str(row["request_id"]),
                    "request_hash": request_hash,
                    "existing_request_hash": existing_hash,
                    "journal_state": str(row["state"]),
                },
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._require_usable()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException as exc:
                try:
                    self._connection.execute("ROLLBACK")
                except BaseException as rollback_exc:
                    self._poison(
                        "rollback failed after journal operation error: "
                        f"{type(rollback_exc).__name__}: {rollback_exc}"
                    )
                    add_note = getattr(exc, "add_note", None)
                    if callable(add_note):
                        add_note(
                            "SQLite physical-effect rollback failed: "
                            f"{type(rollback_exc).__name__}: "
                            f"{rollback_exc}"
                        )
                raise
            else:
                try:
                    self._connection.execute("COMMIT")
                except BaseException:
                    if self._connection.in_transaction:
                        try:
                            self._connection.execute("ROLLBACK")
                        except BaseException as rollback_exc:
                            self._poison(
                                "rollback failed after commit error: "
                                f"{type(rollback_exc).__name__}: "
                                f"{rollback_exc}"
                            )
                    raise

    def _require_usable(self) -> None:
        if self._poisoned_reason is not None:
            raise PhysicalEffectError(
                "Physical effect journal connection is poisoned",
                details={
                    "journal_path": str(self.path),
                    "journal_error": self._poisoned_reason,
                },
            )
        if self._closed:
            raise PhysicalEffectError(
                "Physical effect journal is closed",
                details={"journal_path": str(self.path)},
            )

    def _poison(self, reason: str) -> None:
        self._poisoned_reason = str(reason)
        try:
            self._connection.close()
        except BaseException as close_exc:
            self._poisoned_reason = (
                f"{self._poisoned_reason}; connection close failed: "
                f"{type(close_exc).__name__}: {close_exc}"
            )

    def _ensure_hash_schema_column(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(physical_effect_journal)"
            ).fetchall()
        }
        if "request_hash_schema" not in columns:
            self._connection.execute(
                """
                ALTER TABLE physical_effect_journal
                ADD COLUMN request_hash_schema TEXT NOT NULL
                    DEFAULT 'legacy/unknown'
                """
            )

    def _migrate_pending_hash_schema(
        self,
        row: sqlite3.Row,
        effect: PhysicalEffect,
        now: str,
    ) -> sqlite3.Row:
        existing_schema = str(row["request_hash_schema"])
        if existing_schema == PHYSICAL_REQUEST_HASH_SCHEMA:
            return row
        if (
            str(row["state"]) == "pending"
            and str(row["request_hash"]) == effect.request_hash
        ):
            self._connection.execute(
                """
                UPDATE physical_effect_journal
                SET request_hash_schema = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (
                    PHYSICAL_REQUEST_HASH_SCHEMA,
                    now,
                    effect.request_id,
                ),
            )
            updated = self._select(effect.request_id)
            assert updated is not None
            return updated
        raise PhysicalEffectError(
            "Physical request hash schema differs from the journal",
            details={
                "request_id": effect.request_id,
                "request_hash": effect.request_hash,
                "existing_request_hash": str(row["request_hash"]),
                "request_hash_schema": PHYSICAL_REQUEST_HASH_SCHEMA,
                "existing_request_hash_schema": existing_schema,
                "journal_state": str(row["state"]),
            },
        )


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
