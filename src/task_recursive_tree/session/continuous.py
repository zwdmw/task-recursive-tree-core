from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock, Thread
from time import monotonic, sleep
from types import MappingProxyType
from typing import Any, Mapping

from task_recursive_tree.bootstrap import (
    TaskTreeApplication,
    build_application,
)
from task_recursive_tree.demo import demo_world
from task_recursive_tree.runtime.effect_journal import (
    SQLitePhysicalEffectJournal,
)
from task_recursive_tree.runtime.effects import SynchronousEffectRunner
from task_recursive_tree.runtime.process_lock import (
    ExclusiveProcessFileLock,
    ProcessFileLockError,
)
from task_recursive_tree.selection.model import SpatialSelector
from task_recursive_tree.task.ir import TaskProgram
from task_recursive_tree.task.model import EdgeKind, NodeStatus


class SessionBusyError(RuntimeError):
    pass


class SessionInputError(ValueError):
    pass


class ContinuousTaskSession:
    """Serializes task submissions over one persistent observed world."""

    def __init__(
        self,
        *,
        blocked: bool = False,
        artifacts_dir: str | Path = ".artifacts/live",
        max_steps: int = 10000,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least one")
        self._lock = RLock()
        self._artifacts_dir = Path(artifacts_dir).resolve()
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._process_lock = ExclusiveProcessFileLock(
                self._artifacts_dir / "physical-effects.lock"
            )
        except ProcessFileLockError as exc:
            raise SessionBusyError(
                "The artifacts directory already has an active session: "
                f"{self._artifacts_dir}"
            ) from exc
        self._max_steps = int(max_steps)
        self._blocked = bool(blocked)
        self._closed = False
        self._shutting_down = False
        effect_runner: SynchronousEffectRunner | None = None
        try:
            (
                application,
                effect_runner,
            ) = self._new_runtime(self._blocked)
            sequence = _latest_task_sequence(self._artifacts_dir)
        except BaseException as exc:
            if effect_runner is not None:
                try:
                    effect_runner.close()
                except BaseException as cleanup_exc:
                    _add_exception_note(
                        exc,
                        "Runtime cleanup failed during session "
                        f"initialization: {type(cleanup_exc).__name__}: "
                        f"{cleanup_exc}",
                    )
            try:
                self._process_lock.close()
            except BaseException as cleanup_exc:
                _add_exception_note(
                    exc,
                    "Process lock cleanup failed during session "
                    f"initialization: {type(cleanup_exc).__name__}: "
                    f"{cleanup_exc}",
                )
            raise
        assert effect_runner is not None
        self._application = application
        self._effect_runner = effect_runner
        self._status = "idle"
        self._message = "Ready"
        self._sequence = sequence
        self._active_task: dict[str, Any] | None = None
        self._last_result: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []
        self._worker: Thread | None = None

    def submit_task(
        self,
        object_id: str,
        destination_id: str,
    ) -> Mapping[str, Any]:
        object_id = str(object_id).strip()
        destination_id = str(destination_id).strip()
        if not object_id or not destination_id:
            raise SessionInputError(
                "object_id and destination_id are required"
            )

        with self._lock:
            self._require_accepting()
            if self._status == "running":
                raise SessionBusyError("A task is already running")
            if self._status == "incomplete":
                raise SessionBusyError(
                    "The previous task did not reach a terminal state; "
                    "reset the session before submitting another task"
                )
            snapshot = self._application.world.snapshot()
            self._validate_task(snapshot, object_id, destination_id)
            previous_application = self._application
            previous_status = self._status
            previous_message = self._message
            previous_active_task = self._active_task
            previous_worker = self._worker
            previous_sequence = self._sequence
            next_sequence = previous_sequence + 1
            task_id = f"task-{next_sequence:04d}"
            application = self._application.new_execution()
            transaction_offset = len(
                application.transaction_records()
            )
            started_at = _timestamp()
            active_task = {
                "task_id": task_id,
                "object_id": object_id,
                "destination_id": destination_id,
                "status": "running",
                "started_at": started_at,
            }
            worker = Thread(
                target=self._run_task,
                args=(
                    application,
                    dict(active_task),
                    transaction_offset,
                ),
                name=f"task-tree-{task_id}",
                daemon=True,
            )
            self._application = application
            self._sequence = next_sequence
            self._active_task = active_task
            self._status = "running"
            self._message = (
                f"Executing {object_id} -> {destination_id}"
            )
            self._worker = worker
            try:
                worker.start()
            except BaseException:
                self._application = previous_application
                self._sequence = previous_sequence
                self._active_task = previous_active_task
                self._status = previous_status
                self._message = previous_message
                self._worker = previous_worker
                raise
            return MappingProxyType(dict(active_task))

    def reset(self, *, blocked: bool | None = None) -> Mapping[str, Any]:
        with self._lock:
            self._require_accepting()
            if self._status == "running":
                raise SessionBusyError(
                    "Cannot reset while a task is running"
                )
            next_blocked = (
                self._blocked
                if blocked is None
                else bool(blocked)
            )
            application, effect_runner = self._new_runtime(next_blocked)
            old_effect_runner = self._effect_runner
            try:
                old_effect_runner.close()
            except BaseException as exc:
                self._shutting_down = True
                self._message = (
                    "Runtime shutdown failed; close the session before "
                    "continuing"
                )
                try:
                    effect_runner.close()
                except BaseException as cleanup_exc:
                    _add_exception_note(
                        exc,
                        "Replacement runtime cleanup failed: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                    )
                raise
            self._application = application
            self._effect_runner = effect_runner
            self._blocked = next_blocked
            self._status = "idle"
            self._message = "Ready"
            self._active_task = None
            self._last_result = None
            self._history = []
            self._worker = None
        return self.state()

    def begin_shutdown(self) -> None:
        with self._lock:
            if not self._closed:
                self._shutting_down = True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._shutting_down = True
            if self._status == "running":
                raise SessionBusyError(
                    "Cannot close while a task is running"
                )
            self._effect_runner.close()
            self._process_lock.close()
            self._closed = True

    def wait(self, timeout: float = 10.0) -> Mapping[str, Any]:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            with self._lock:
                worker = self._worker
            if worker is None or not worker.is_alive():
                return self.state()
            sleep(0.01)
        raise TimeoutError("Task session did not become idle in time")

    def state(self) -> Mapping[str, Any]:
        with self._lock:
            application = self._application
            status = self._status
            message = self._message
            active_task = (
                dict(self._active_task)
                if self._active_task is not None
                else None
            )
            last_result = (
                dict(self._last_result)
                if self._last_result is not None
                else None
            )
            history = [dict(item) for item in self._history]
            blocked = self._blocked
            closed = self._closed
            shutting_down = self._shutting_down

        snapshot = application.world.snapshot()
        return {
            "status": status,
            "closed": closed,
            "shutting_down": shutting_down,
            "worker_ready": (
                not closed
                and not shutting_down
                and status not in {"running", "incomplete"}
            ),
            "requires_reset": status == "incomplete",
            "message": message,
            "scenario": "blocked" if blocked else "normal",
            "active_task": active_task,
            "last_result": last_result,
            "history": history,
            "artifacts_dir": str(self._artifacts_dir),
            "transactions": len(application.transaction_records()),
            "world": _world_payload(snapshot),
            "tree": _tree_payload(application),
        }

    def _run_task(
        self,
        application: TaskTreeApplication,
        task: dict[str, Any],
        transaction_offset: int,
    ) -> None:
        try:
            result = self._task_result(
                application,
                task,
                transaction_offset,
            )
        except BaseException as exc:
            transactions = _transaction_delta(
                application,
                transaction_offset,
            )
            result = {
                **task,
                "status": "incomplete",
                "kernel_status": None,
                "error_code": "SESSION_WORKER_ERROR",
                "finished_at": _timestamp(),
                "transactions": transactions,
                "tree_path": str(
                    self._artifacts_dir / f"{task['task_id']}.json"
                ),
                "error": (
                    f"Worker failure: {type(exc).__name__}: {exc}"
                ),
            }
        self._finish_task(result)

    def _task_result(
        self,
        application: TaskTreeApplication,
        task: dict[str, Any],
        transaction_offset: int,
    ) -> dict[str, Any]:
        status: NodeStatus | None = None
        error: str | None = None
        error_code: str | None = None
        try:
            program = TaskProgram.place(
                SpatialSelector.exact(task["object_id"], "object"),
                SpatialSelector.exact(
                    task["destination_id"], "region"
                ),
                program_id=task["task_id"],
            )
            status = application.execute(
                program,
                max_steps=self._max_steps,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        finished_at = _timestamp()
        transactions = (
            len(application.transaction_records()) - transaction_offset
        )
        tree_path = self._artifacts_dir / f"{task['task_id']}.json"
        latest_path = self._artifacts_dir / "latest-tree.json"
        try:
            application.inspector.save(tree_path)
            application.inspector.save(latest_path)
        except Exception as exc:
            if error is None:
                error = f"Tree export failed: {exc}"

        kernel_status = status.value if status is not None else None
        if status is not None and not status.terminal:
            result_status = "incomplete"
            if application.kernel.paused:
                error_code = "KERNEL_PAUSED"
            elif any(
                event.event_type == "step_budget_exhausted"
                for event in application.store.events()
            ):
                error_code = "STEP_BUDGET_EXHAUSTED"
            else:
                error_code = "NONTERMINAL_KERNEL_EXIT"
            error = (
                error
                or "Task execution stopped before reaching a terminal state"
            )
        elif status is None:
            result_status = "incomplete"
            error_code = "EXECUTION_ERROR"
            error = error or (
                "Task execution raised before reaching a terminal state"
            )
        else:
            result_status = kernel_status
        result = {
            **task,
            "status": result_status,
            "kernel_status": kernel_status,
            "error_code": error_code,
            "finished_at": finished_at,
            "transactions": transactions,
            "tree_path": str(tree_path),
            "error": error,
        }
        return result

    def _finish_task(self, result: dict[str, Any]) -> None:
        result_status = str(result.get("status") or "error")
        error = result.get("error")
        task_id = str(result.get("task_id") or "unknown")
        with self._lock:
            self._active_task = None
            self._last_result = result
            self._history.append(result)
            self._status = result_status
            self._worker = None
            self._message = (
                str(error)
                if error is not None
                else f"Task {task_id} {result_status}"
            )

    @staticmethod
    def _validate_task(snapshot, object_id: str, destination_id: str) -> None:
        try:
            object_state = snapshot.entity(object_id)
        except KeyError as exc:
            raise SessionInputError(
                f"Unknown object: {object_id}"
            ) from exc
        if object_state.kind != "object":
            raise SessionInputError(
                f"Entity {object_id} is not an object"
            )
        if not bool(object_state.property("movable", False)):
            raise SessionInputError(
                f"Object {object_id} is not movable"
            )
        try:
            destination = snapshot.entity(destination_id)
        except KeyError as exc:
            raise SessionInputError(
                f"Unknown destination: {destination_id}"
            ) from exc
        if destination.kind != "region":
            raise SessionInputError(
                f"Entity {destination_id} is not a region"
            )

    def _new_runtime(
        self,
        blocked: bool,
    ) -> tuple[TaskTreeApplication, SynchronousEffectRunner]:
        grid, observation = demo_world(blocked=blocked)
        journal = SQLitePhysicalEffectJournal(
            self._artifacts_dir / "physical-effects.sqlite3"
        )
        effect_runner: SynchronousEffectRunner | None = None
        try:
            journal.recover_interrupted_dispatches()
            effect_runner = SynchronousEffectRunner(journal=journal)
            application = build_application(
                grid,
                observation,
                effect_runner=effect_runner,
            )
        except BaseException:
            if effect_runner is None:
                journal.close()
            else:
                effect_runner.close()
            raise
        assert effect_runner is not None
        return application, effect_runner

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Task session is closed")

    def _require_accepting(self) -> None:
        self._require_open()
        if self._shutting_down:
            raise SessionBusyError("Task session is shutting down")


def _tree_payload(application: TaskTreeApplication) -> dict[str, Any]:
    store = application.store
    nodes = []
    for node_id, spec in store.specs().items():
        runtime = store.runtime(node_id)
        diagnostic = runtime.last_diagnostic
        nodes.append(
            {
                "node_id": node_id,
                "task_type": spec.task_type,
                "operation_kind": spec.operation_kind.value,
                "control_kind": spec.control_kind.value,
                "origin": spec.origin.value,
                "status": runtime.status.value,
                "attempts": runtime.attempts,
                "repairs": runtime.repairs,
                "diagnostic": (
                    {
                        "code": diagnostic.code,
                        "message": diagnostic.message,
                        "details": _jsonable(diagnostic.details),
                        "retryable": diagnostic.retryable,
                        "repairable": diagnostic.repairable,
                    }
                    if diagnostic is not None
                    else None
                ),
                "active_obligations": _jsonable(
                    runtime.active_obligations
                ),
            }
        )
    edges = [
        {
            "parent_id": edge.parent_id,
            "child_id": edge.child_id,
            "kind": edge.kind.value,
            "order": edge.order,
        }
        for edge in store.edges()
    ]
    events = [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "node_id": event.node_id,
            "data": _jsonable(event.data),
        }
        for event in store.events()
    ]
    stack = [
        {
            "node_id": frame.node_id,
            "phase": frame.phase.value,
        }
        for frame in store.stack()
    ]
    return {
        "root_id": store.root_id,
        "nodes": nodes,
        "edges": edges,
        "execution_stack": stack,
        "events": events,
        "ascii": application.inspector.render_ascii(),
    }


def _world_payload(snapshot) -> dict[str, Any]:
    grid = snapshot.grid
    return {
        "revision": snapshot.revision,
        "snapshot_ref": snapshot.snapshot_ref,
        "observation": {
            "source_epoch": snapshot.source_epoch,
            "sequence": snapshot.observation_sequence,
            "observed_at": snapshot.observed_at,
            "complete": snapshot.observation_complete,
            "confidence": snapshot.observation_confidence,
        },
        "grid": {
            "width": grid.width,
            "height": grid.height,
            "resolution": grid.resolution,
            "origin_x": grid.origin_x,
            "origin_y": grid.origin_y,
            "static_occupied": [
                list(cell) for cell in sorted(grid.static_occupied)
            ],
        },
        "robot": {
            "base_pose": _pose_payload(snapshot.robot.base_pose),
            "joints": list(snapshot.robot.joints),
            "gripper_open": snapshot.robot.gripper_open,
            "held_object_id": snapshot.robot.held_object_id,
            "state_epoch": snapshot.robot.state_epoch,
        },
        "entities": [
            {
                "entity_id": entity.entity_id,
                "kind": entity.kind,
                "pose": _pose_payload(entity.pose),
                "radius": entity.radius,
                "tags": sorted(entity.tags),
                "properties": _jsonable(entity.properties),
                "version": entity.version,
                "generation": entity.generation,
            }
            for entity in sorted(
                snapshot.entities.values(),
                key=lambda item: item.entity_id,
            )
        ],
    }


def _pose_payload(pose) -> dict[str, Any]:
    return {
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "yaw": pose.yaw,
        "frame": pose.frame,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_task_sequence(artifacts_dir: Path) -> int:
    latest = 0
    for path in artifacts_dir.glob("task-*.json"):
        stem = path.stem
        prefix, separator, raw_sequence = stem.partition("-")
        if prefix != "task" or not separator or not raw_sequence.isdigit():
            continue
        latest = max(latest, int(raw_sequence))
    return latest


def _add_exception_note(error: BaseException, note: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(str(note))


def _transaction_delta(
    application: TaskTreeApplication,
    offset: int,
) -> int | None:
    try:
        return max(
            0,
            len(application.transaction_records()) - int(offset),
        )
    except BaseException:
        return None
