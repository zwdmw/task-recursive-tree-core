from __future__ import annotations

import argparse
import json
import os
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from task_recursive_tree.session import (
    ContinuousTaskSession,
    SessionBusyError,
    SessionInputError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
HTML_PATH = Path(__file__).with_name("console.html")
DEFAULT_PID_PATH = (
    PROJECT_ROOT / ".artifacts" / ".task-tree-server.pid.json"
)


class TaskTreeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(
    session: ContinuousTaskSession,
) -> type[BaseHTTPRequestHandler]:
    class TaskTreeHandler(BaseHTTPRequestHandler):
        server_version = "TaskRecursiveTree/0.1"

        def do_HEAD(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/", "/index.html"}:
                self._send_bytes(
                    HTTPStatus.OK,
                    b"",
                    "text/html; charset=utf-8",
                    content_length=HTML_PATH.stat().st_size,
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found"},
            )

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in {"/", "/index.html"}:
                self._send_bytes(
                    HTTPStatus.OK,
                    HTML_PATH.read_bytes(),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/api/state":
                self._send_json(HTTPStatus.OK, session.state())
                return
            if path == "/api/health":
                state = session.state()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "server": "task-recursive-tree",
                        "process_id": os.getpid(),
                        "project_root": str(PROJECT_ROOT),
                        "status": state["status"],
                        "worker_ready": state["worker_ready"],
                    },
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found"},
            )

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                if path == "/api/tasks":
                    payload = self._read_json()
                    accepted = session.submit_task(
                        payload.get("object_id", ""),
                        payload.get("destination_id", ""),
                    )
                    self._send_json(
                        HTTPStatus.ACCEPTED,
                        {"accepted": dict(accepted)},
                    )
                    return
                if path == "/api/reset":
                    payload = self._read_json(optional=True)
                    scenario = payload.get("scenario")
                    if scenario is None:
                        blocked = None
                    elif scenario == "normal":
                        blocked = False
                    elif scenario == "blocked":
                        blocked = True
                    else:
                        raise SessionInputError(
                            "scenario must be 'normal' or 'blocked'"
                        )
                    self._send_json(
                        HTTPStatus.OK,
                        session.reset(blocked=blocked),
                    )
                    return
                if path == "/api/shutdown":
                    self._read_json(optional=True)
                    if self.client_address[0] not in {
                        "127.0.0.1",
                        "::1",
                    }:
                        self._send_json(
                            HTTPStatus.FORBIDDEN,
                            {"error": "local_request_required"},
                        )
                        return
                    session.begin_shutdown()
                    self._send_json(
                        HTTPStatus.ACCEPTED,
                        {"status": "shutting_down"},
                    )
                    threading.Thread(
                        target=self.server.shutdown,
                        name="task-tree-server-shutdown",
                        daemon=True,
                    ).start()
                    return
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "not_found"},
                )
            except SessionInputError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_task", "message": str(exc)},
                )
            except SessionBusyError as exc:
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "session_busy", "message": str(exc)},
                )
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "bad_request", "message": str(exc)},
                )
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": "internal_error",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )

        def _read_json(
            self,
            *,
            optional: bool = False,
        ) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return {} if optional else {}
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Invalid Content-Length") from exc
            if length < 0 or length > 65536:
                raise ValueError("Request body is too large")
            body = self.rfile.read(length)
            if not body:
                return {}
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    "Request body must be a UTF-8 JSON object"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError("Request body must be a JSON object")
            return value

        def _send_json(
            self,
            status: HTTPStatus,
            value: Any,
        ) -> None:
            body = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            self._send_bytes(
                status,
                body,
                "application/json; charset=utf-8",
            )

        def _send_bytes(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            *,
            content_length: int | None = None,
        ) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Length",
                str(
                    len(body)
                    if content_length is None
                    else content_length
                ),
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'",
            )
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            if args and str(args[1]).startswith(("4", "5")):
                super().log_message(format, *args)

    return TaskTreeHandler


def bind_server(
    host: str,
    preferred_port: int,
    attempts: int,
    handler: type[BaseHTTPRequestHandler],
) -> TaskTreeHTTPServer:
    last_error: OSError | None = None
    for offset in range(max(1, attempts)):
        try:
            return TaskTreeHTTPServer(
                (host, preferred_port + offset),
                handler,
            )
        except OSError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def write_pid_file(
    path: Path,
    *,
    host: str,
    port: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process_id": os.getpid(),
        "project_root": str(PROJECT_ROOT),
        "server_script": str(Path(__file__).resolve()),
        "host": host,
        "port": port,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def remove_owned_pid_file(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return
    if int(payload.get("process_id", -1)) == os.getpid():
        path.unlink(missing_ok=True)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the persistent recursive task-tree web console."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--port-attempts", type=int, default=20)
    parser.add_argument("--blocked", action="store_true")
    parser.add_argument(
        "--open-browser",
        dest="open_browser",
        action="store_true",
        default=True,
    )
    parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=DEFAULT_PID_PATH,
    )
    return parser


def main() -> None:
    parser = build_argument_parser()
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be in 1..65535")
    if arguments.port_attempts < 1:
        parser.error("--port-attempts must be positive")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    artifacts_dir = (
        arguments.artifacts
        if arguments.artifacts is not None
        else PROJECT_ROOT
        / ".artifacts"
        / "sessions"
        / f"continuous_{stamp}_{os.getpid()}"
    ).resolve()
    pid_path = arguments.pid_file.resolve()
    session = ContinuousTaskSession(
        blocked=arguments.blocked,
        artifacts_dir=artifacts_dir,
    )
    server: TaskTreeHTTPServer | None = None
    pid_written = False
    try:
        server = bind_server(
            arguments.host,
            arguments.port,
            arguments.port_attempts,
            make_handler(session),
        )
        host, port = server.server_address[:2]
        write_pid_file(pid_path, host=str(host), port=int(port))
        pid_written = True
        display_host = (
            "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        )
        url = f"http://{display_host}:{port}/"
        print()
        print("===== Task Recursive Tree Continuous Session =====")
        print(f"Server:    {url}")
        print(f"Artifacts: {artifacts_dir}")
        print(
            "Scenario:  "
            f"{'blocked' if arguments.blocked else 'normal'}"
        )
        print("Press Ctrl+C to close the server.")
        print()
        if arguments.open_browser:
            timer = threading.Timer(
                0.5,
                webbrowser.open,
                args=(url,),
            )
            timer.daemon = True
            timer.start()
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nClosing the task-tree session...")
    finally:
        session.begin_shutdown()
        try:
            if server is not None:
                server.server_close()
        finally:
            try:
                if session.state()["status"] == "running":
                    session.wait(timeout=5.0)
            except TimeoutError:
                print(
                    "[WARN] A task worker was still running during shutdown."
                )
            else:
                session.close()
            finally:
                if pid_written:
                    remove_owned_pid_file(pid_path)


if __name__ == "__main__":
    main()
