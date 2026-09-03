from __future__ import annotations

import json
from threading import Thread
from urllib.request import Request, urlopen

from task_recursive_tree.session import ContinuousTaskSession
from task_recursive_tree.web.server import (
    TaskTreeHTTPServer,
    make_handler,
)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
):
    body = (
        json.dumps(payload).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_web_console_api_runs_and_resets_session(tmp_path) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    server = TaskTreeHTTPServer(
        ("127.0.0.1", 0),
        make_handler(session),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        with urlopen(f"{base_url}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert 'id="world-canvas"' in html
            assert 'id="tree-list"' in html

        status, initial = request_json(f"{base_url}/api/state")
        assert status == 200
        assert initial["status"] == "idle"
        assert initial["tree"]["root_id"] is None

        status, accepted = request_json(
            f"{base_url}/api/tasks",
            method="POST",
            payload={
                "object_id": "cup-red",
                "destination_id": "drop-zone",
            },
        )
        assert status == 202
        assert accepted["accepted"]["task_id"] == "task-0001"
        session.wait()

        _, completed = request_json(f"{base_url}/api/state")
        assert completed["status"] == "succeeded"
        assert completed["tree"]["root_id"] == "program/task-0001"

        status, reset = request_json(
            f"{base_url}/api/reset",
            method="POST",
            payload={"scenario": "blocked"},
        )
        assert status == 200
        assert reset["scenario"] == "blocked"
        assert reset["tree"]["root_id"] is None
        assert any(
            item["entity_id"] == "movable-crate"
            for item in reset["world"]["entities"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        session.close()


def test_shutdown_endpoint_stops_accepting_work_before_server_exit(
    tmp_path,
) -> None:
    session = ContinuousTaskSession(artifacts_dir=tmp_path)
    server = TaskTreeHTTPServer(
        ("127.0.0.1", 0),
        make_handler(session),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        status, payload = request_json(
            f"{base_url}/api/shutdown",
            method="POST",
            payload={},
        )

        assert status == 202
        assert payload == {"status": "shutting_down"}
        thread.join(timeout=5)
        assert not thread.is_alive()
        state = session.state()
        assert state["shutting_down"] is True
        assert state["worker_ready"] is False
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        server.server_close()
        session.close()
