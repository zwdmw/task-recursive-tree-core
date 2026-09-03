from __future__ import annotations

import errno
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from task_recursive_tree.runtime.process_lock import (
    ExclusiveProcessFileLock,
    _is_lock_conflict,
)
from task_recursive_tree.session import continuous as session_module
from task_recursive_tree.session import (
    ContinuousTaskSession,
    SessionBusyError,
    SessionInputError,
)


def entity(state, entity_id: str):
    return next(
        item
        for item in state["world"]["entities"]
        if item["entity_id"] == entity_id
    )


def test_continuous_session_preserves_world_across_fresh_trees(
    tmp_path,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)

    first = session.submit_task("cup-red", "drop-zone")
    first_state = session.wait()
    assert first["task_id"] == "task-0001"
    assert first_state["status"] == "succeeded"
    assert first_state["tree"]["root_id"] == "program/task-0001"
    assert first_state["last_result"]["transactions"] == 5
    assert entity(first_state, "cup-red")["pose"]["x"] == 7.5
    assert entity(first_state, "cup-red")["pose"]["y"] == 5.5

    second = session.submit_task("cup-red", "parking-zone")
    second_state = session.wait()
    assert second["task_id"] == "task-0002"
    assert second_state["status"] == "succeeded"
    assert second_state["tree"]["root_id"] == "program/task-0002"
    assert second_state["world"]["snapshot_ref"] == "world:14"
    assert second_state["transactions"] == 10
    assert len(second_state["history"]) == 2
    assert entity(second_state, "cup-red")["pose"]["x"] == 1.5
    assert entity(second_state, "cup-red")["pose"]["y"] == 6.5
    assert (tmp_path / "task-0001.json").is_file()
    assert (tmp_path / "task-0002.json").is_file()
    assert (tmp_path / "latest-tree.json").is_file()


def test_continuous_session_exposes_recursive_blocker_repair(
    tmp_path,
) -> None:
    session = ContinuousTaskSession(
        blocked=True,
        artifacts_dir=tmp_path,
    )
    session.submit_task("cup-red", "drop-zone")
    state = session.wait()

    assert state["status"] == "succeeded"
    assert state["last_result"]["transactions"] == 10
    assert any(
        node["task_type"] == "RouteBlockedRepair"
        for node in state["tree"]["nodes"]
    )
    assert any(
        edge["kind"] == "repair"
        for edge in state["tree"]["edges"]
    )
    assert entity(state, "movable-crate")["pose"]["x"] == 1.5
    assert entity(state, "movable-crate")["pose"]["y"] == 6.5


def test_continuous_session_validates_typed_entity_roles(
    tmp_path,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)

    try:
        session.submit_task("drop-zone", "parking-zone")
    except SessionInputError as exc:
        assert "not an object" in str(exc)
    else:
        raise AssertionError("A region must not be accepted as an object")


def test_step_budget_exhaustion_requires_reset_without_staying_running(
    tmp_path,
) -> None:
    session = ContinuousTaskSession(
        artifacts_dir=tmp_path,
        max_steps=1,
    )

    session.submit_task("cup-red", "drop-zone")
    state = session.wait()

    assert state["status"] == "incomplete"
    assert state["worker_ready"] is False
    assert state["requires_reset"] is True
    assert (
        state["last_result"]["error_code"]
        == "STEP_BUDGET_EXHAUSTED"
    )
    assert state["last_result"]["kernel_status"] in {
        "pending",
        "running",
    }
    try:
        session.submit_task("cup-red", "parking-zone")
    except SessionBusyError as exc:
        assert "reset" in str(exc)
    else:
        raise AssertionError("Incomplete execution must require reset")

    reset_state = session.reset()
    assert reset_state["status"] == "idle"
    assert reset_state["worker_ready"] is True
    assert reset_state["requires_reset"] is False


def test_reset_and_restart_do_not_overwrite_task_artifacts(tmp_path) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    first = session.submit_task("cup-red", "drop-zone")
    session.wait()
    first_path = tmp_path / f"{first['task_id']}.json"
    first_payload = first_path.read_text(encoding="utf-8")
    journal_path = tmp_path / "physical-effects.sqlite3"
    with sqlite3.connect(journal_path) as connection:
        journal_rows = connection.execute(
            "SELECT COUNT(*) FROM physical_effect_journal"
        ).fetchone()[0]

    old_effect_runner = session._effect_runner
    session.reset()
    assert old_effect_runner._closed is True
    assert session._effect_runner is not old_effect_runner
    with sqlite3.connect(journal_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM physical_effect_journal"
        ).fetchone()[0] == journal_rows
    second = session.submit_task("cup-red", "parking-zone")
    session.wait()

    assert first["task_id"] == "task-0001"
    assert second["task_id"] == "task-0002"
    assert first_path.read_text(encoding="utf-8") == first_payload
    assert (tmp_path / "task-0002.json").is_file()

    session.close()
    restarted = ContinuousTaskSession(artifacts_dir=tmp_path)
    third = restarted.submit_task("cup-red", "drop-zone")
    restarted.wait()
    assert third["task_id"] == "task-0003"
    restarted.close()


def test_restart_journal_prevents_duplicate_physical_dispatch(
    tmp_path,
) -> None:
    first_session = ContinuousTaskSession(artifacts_dir=tmp_path)
    first_session.submit_task("cup-red", "drop-zone")
    first_state = first_session.wait()
    assert first_state["status"] == "succeeded"
    assert first_state["transactions"] == 5
    journal_path = tmp_path / "physical-effects.sqlite3"
    with sqlite3.connect(journal_path) as connection:
        row_count = connection.execute(
            "SELECT COUNT(*) FROM physical_effect_journal"
        ).fetchone()[0]
    first_session.close()

    (tmp_path / "task-0001.json").unlink()
    restarted = ContinuousTaskSession(artifacts_dir=tmp_path)
    repeated = restarted.submit_task("cup-red", "drop-zone")
    repeated_state = restarted.wait()

    assert repeated["task_id"] == "task-0001"
    assert repeated_state["status"] == "blocked"
    assert repeated_state["transactions"] == 0
    diagnostics = [
        node["diagnostic"]
        for node in repeated_state["tree"]["nodes"]
        if node["diagnostic"] is not None
    ]
    assert any(
        diagnostic["code"] == "OUTCOME_UNKNOWN"
        and diagnostic["details"]["journal_state"] == "completed"
        for diagnostic in diagnostics
    )
    with sqlite3.connect(journal_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM physical_effect_journal"
        ).fetchone()[0] == row_count
    restarted.close()


def test_session_close_is_idempotent_and_blocks_new_work(tmp_path) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)

    session.close()
    session.close()

    state = session.state()
    assert state["closed"] is True
    assert state["worker_ready"] is False
    with pytest.raises(RuntimeError, match="closed"):
        session.submit_task("cup-red", "drop-zone")
    with pytest.raises(RuntimeError, match="closed"):
        session.reset()


def test_artifacts_directory_allows_only_one_active_session(
    tmp_path,
) -> None:
    first = ContinuousTaskSession(artifacts_dir=tmp_path)
    try:
        with pytest.raises(
            SessionBusyError,
            match="already has an active session",
        ):
            ContinuousTaskSession(artifacts_dir=tmp_path)
    finally:
        first.close()

    replacement = ContinuousTaskSession(artifacts_dir=tmp_path)
    replacement.close()


def test_process_lock_close_is_idempotent(tmp_path) -> None:
    path = tmp_path / "physical-effects.lock"
    lock = ExclusiveProcessFileLock(path)

    lock.close()
    lock.close()

    replacement = ExclusiveProcessFileLock(path)
    replacement.close()


def test_process_lock_distinguishes_conflict_from_io_failure() -> None:
    assert _is_lock_conflict(OSError(errno.EACCES, "locked")) is True
    assert _is_lock_conflict(OSError(errno.EAGAIN, "locked")) is True
    assert _is_lock_conflict(OSError(errno.EIO, "io failure")) is False


def test_process_lock_blocks_a_second_process(tmp_path) -> None:
    path = tmp_path / "physical-effects.lock"
    lock = ExclusiveProcessFileLock(path)
    script = "\n".join(
        (
            "import sys",
            "from task_recursive_tree.runtime.process_lock import (",
            "    ExclusiveProcessFileLock, ProcessFileLockError,",
            ")",
            "try:",
            "    lock = ExclusiveProcessFileLock(sys.argv[1])",
            "except ProcessFileLockError:",
            "    raise SystemExit(17)",
            "lock.close()",
        )
    )
    environment = dict(os.environ)
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(
            None,
            (
                str(source_root),
                environment.get("PYTHONPATH", ""),
            ),
        )
    )

    try:
        blocked = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=source_root.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert blocked.returncode == 17, (
            blocked.stdout,
            blocked.stderr,
        )
    finally:
        lock.close()

    acquired = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=source_root.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert acquired.returncode == 0, (
        acquired.stdout,
        acquired.stderr,
    )


def test_thread_construction_failure_does_not_change_session_state(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    original_application = session._application

    class ConstructFailingThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise RuntimeError("injected thread construction failure")

    with monkeypatch.context() as patch:
        patch.setattr(session_module, "Thread", ConstructFailingThread)
        with pytest.raises(
            RuntimeError,
            match="thread construction failure",
        ):
            session.submit_task("cup-red", "drop-zone")

    state = session.state()
    assert state["status"] == "idle"
    assert session._sequence == 0
    assert session._application is original_application
    assert session._worker is None
    session.close()


def test_thread_start_failure_rolls_back_session_state(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    original_application = session._application

    class StartFailingThread:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def start(self) -> None:
            raise RuntimeError("injected thread start failure")

    with monkeypatch.context() as patch:
        patch.setattr(session_module, "Thread", StartFailingThread)
        with pytest.raises(
            RuntimeError,
            match="thread start failure",
        ):
            session.submit_task("cup-red", "drop-zone")

    state = session.state()
    assert state["status"] == "idle"
    assert state["active_task"] is None
    assert state["history"] == []
    assert session._sequence == 0
    assert session._application is original_application
    assert session._worker is None

    accepted = session.submit_task("cup-red", "drop-zone")
    session.wait()
    assert accepted["task_id"] == "task-0001"
    session.close()


def test_worker_base_exception_reaches_terminal_error_state(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)

    def fail_execute(self, *args, **kwargs):
        del self, args, kwargs
        raise SystemExit("injected worker failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            session_module.TaskTreeApplication,
            "execute",
            fail_execute,
        )
        session.submit_task("cup-red", "drop-zone")
        state = session.wait()

    assert state["status"] == "incomplete"
    assert state["worker_ready"] is False
    assert state["requires_reset"] is True
    assert state["last_result"]["error_code"] == "SESSION_WORKER_ERROR"
    assert "SystemExit" in state["last_result"]["error"]
    assert session._worker is None
    session.close()


def test_worker_audit_failure_reports_unknown_transaction_count(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    original = session_module.TaskTreeApplication.transaction_records
    calls = 0

    def flaky_transaction_records(self):
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("injected transaction audit failure")
        return original(self)

    with monkeypatch.context() as patch:
        patch.setattr(
            session_module.TaskTreeApplication,
            "transaction_records",
            flaky_transaction_records,
        )
        session.submit_task("cup-red", "drop-zone")
        state = session.wait()

    assert state["status"] == "incomplete"
    assert state["requires_reset"] is True
    assert state["last_result"]["error_code"] == "SESSION_WORKER_ERROR"
    assert state["last_result"]["transactions"] is None
    assert "transaction audit failure" in state["last_result"]["error"]
    session.close()


def test_reset_failure_keeps_existing_runtime_and_scenario(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    original_application = session._application
    original_runner = session._effect_runner

    def fail_build(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected runtime build failure")

    with monkeypatch.context() as patch:
        patch.setattr(session_module, "build_application", fail_build)
        with pytest.raises(
            RuntimeError,
            match="runtime build failure",
        ):
            session.reset(blocked=True)

    state = session.state()
    assert state["scenario"] == "normal"
    assert state["status"] == "idle"
    assert session._application is original_application
    assert session._effect_runner is original_runner
    assert original_runner._closed is False
    session.close()


def test_initial_sequence_failure_closes_runtime_and_process_lock(
    tmp_path,
    monkeypatch,
) -> None:
    runners = []

    def fake_new_runtime(self, blocked):
        del self
        grid, observation = session_module.demo_world(blocked=blocked)
        runner = session_module.SynchronousEffectRunner()
        runners.append(runner)
        return (
            session_module.build_application(
                grid,
                observation,
                effect_runner=runner,
            ),
            runner,
        )

    def fail_sequence(_artifacts_dir):
        raise OSError("injected sequence scan failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            session_module.ContinuousTaskSession,
            "_new_runtime",
            fake_new_runtime,
        )
        patch.setattr(
            session_module,
            "_latest_task_sequence",
            fail_sequence,
        )
        with pytest.raises(OSError, match="sequence scan failure"):
            ContinuousTaskSession(artifacts_dir=tmp_path)

    assert len(runners) == 1
    assert runners[0]._closed is True
    replacement = ContinuousTaskSession(artifacts_dir=tmp_path)
    replacement.close()


def test_old_runner_close_failure_stops_new_work(
    tmp_path,
    monkeypatch,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    old_runner = session._effect_runner

    def fail_close():
        raise RuntimeError("injected old runner close failure")

    with monkeypatch.context() as patch:
        patch.setattr(old_runner, "close", fail_close)
        with pytest.raises(
            RuntimeError,
            match="old runner close failure",
        ):
            session.reset(blocked=True)

        state = session.state()
        assert state["scenario"] == "normal"
        assert state["shutting_down"] is True
        assert state["worker_ready"] is False
        with pytest.raises(SessionBusyError, match="shutting down"):
            session.submit_task("cup-red", "drop-zone")

    session.close()


def test_runtime_recovery_failure_closes_journal_and_process_lock(
    tmp_path,
    monkeypatch,
) -> None:
    journals = []

    class FailingRecoveryJournal:
        def __init__(self, path) -> None:
            self.path = path
            self.closed = False
            journals.append(self)

        def recover_interrupted_dispatches(self):
            raise RuntimeError("injected recovery failure")

        def close(self) -> None:
            self.closed = True

    with monkeypatch.context() as patch:
        patch.setattr(
            session_module,
            "SQLitePhysicalEffectJournal",
            FailingRecoveryJournal,
        )
        with pytest.raises(RuntimeError, match="recovery failure"):
            ContinuousTaskSession(artifacts_dir=tmp_path)

    assert len(journals) == 1
    assert journals[0].closed is True
    replacement = ContinuousTaskSession(artifacts_dir=tmp_path)
    replacement.close()


def test_begin_shutdown_rejects_submit_and_reset(tmp_path) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)

    session.begin_shutdown()
    state = session.state()

    assert state["shutting_down"] is True
    assert state["worker_ready"] is False
    with pytest.raises(SessionBusyError, match="shutting down"):
        session.submit_task("cup-red", "drop-zone")
    with pytest.raises(SessionBusyError, match="shutting down"):
        session.reset()
    session.close()
