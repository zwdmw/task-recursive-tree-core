from __future__ import annotations

import argparse
import copy
import errno
import json
import os
import socket
import sys
import threading
import time
import uuid
import webbrowser
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .compiler import KernelCompilerBridge
from .continuous_recovery import adapt_continuous_session_class
from .executor import KernelExecutorBridge
from .harness_safety import install_harness_safety
from .paths import ensure_harness_importable, import_harness_module
from .planner import HarnessTaskProgramPlannerAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ARTIFACTS_ROOT = PROJECT_ROOT / ".artifacts" / "gemini-er2-sessions"
DEFAULT_PID_PATH = PROJECT_ROOT / ".artifacts" / ".task-tree-server.pid.json"
DEFAULT_HARNESS_ROOT = os.environ.get(
    "GEMINI_ER2_HARNESS_ROOT",
    r"D:\GeminiER2Harness",
)


def _harness_server(harness_root: str | Path | None = None) -> Any:
    return import_harness_module(
        "scripts.run_tree_server",
        harness_root=harness_root,
    )


def _preparse_harness_root(
    argv: list[str] | None = None,
) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--harness-root",
        default=DEFAULT_HARNESS_ROOT,
    )
    preliminary, _unknown = parser.parse_known_args(argv)
    return ensure_harness_importable(preliminary.harness_root)


def build_argument_parser(
    argv: list[str] | None = None,
) -> argparse.ArgumentParser:
    harness_root = _preparse_harness_root(argv)
    module = _harness_server(harness_root)
    parser = module.build_argument_parser()
    parser.description = (
        "Run the GeminiER2 MuJoCo console with TaskTreeKernel as the "
        "only task-tree executor."
    )
    parser.add_argument(
        "--harness-root",
        default=str(harness_root),
    )
    parser.set_defaults(
        out=str(DEFAULT_ARTIFACTS_ROOT),
        pid_file=str(DEFAULT_PID_PATH),
    )
    return parser


def make_handler(
    session: Any,
    *,
    voice_control: bool = False,
    voice_transcriber: Any = None,
    harness_server: Any = None,
) -> type:
    module = harness_server or _harness_server()
    base = module.make_handler(
        session,
        voice_control=voice_control,
        voice_transcriber=voice_transcriber,
    )

    class KernelSessionHandler(base):
        server_version = "TaskRecursiveTreeGeminiER2/1.0"

        def do_GET(self) -> None:
            if urlsplit(self.path).path != "/api/health":
                super().do_GET()
                return
            state = session.state()
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": state.get("status") not in {"error", "stopped"},
                    "server": "task-recursive-tree",
                    "runtime": "gemini-er2-kernel",
                    "process_id": os.getpid(),
                    "project_root": str(PROJECT_ROOT),
                    "status": state.get("status"),
                    "worker_ready": state.get("worker_ready"),
                },
            )

    return KernelSessionHandler


def write_pid_file(path: Path, *, host: str, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process_id": os.getpid(),
        "project_root": str(PROJECT_ROOT),
        "server_script": str(Path(__file__).resolve()),
        "host": str(host),
        "port": int(port),
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


def bind_server(
    harness_server: Any,
    host: str,
    preferred_port: int,
    attempts: int,
    handler: type,
) -> Any:
    base_server = harness_server.SessionHTTPServer

    class ExclusiveSessionHTTPServer(base_server):
        allow_reuse_address = False

        def server_bind(self) -> None:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_EXCLUSIVEADDRUSE,
                    1,
                )
            super().server_bind()

    last_error: OSError | None = None
    for offset in range(max(1, attempts)):
        port = preferred_port + offset
        if port > 65535:
            break
        if _port_is_listening(host, port):
            last_error = OSError(
                errno.EADDRINUSE,
                f"Port {host}:{port} already has a listener",
            )
            continue
        try:
            return ExclusiveSessionHTTPServer((host, port), handler)
        except OSError as exc:
            last_error = exc
    if last_error is None:
        last_error = OSError(
            errno.EADDRINUSE,
            "No valid TCP port remained in the configured attempt range",
        )
    raise last_error


def _port_is_listening(host: str, port: int) -> bool:
    probe_host = (
        "::1"
        if host == "::"
        else "127.0.0.1"
        if host in {"0.0.0.0", ""}
        else host
    )
    try:
        with socket.create_connection((probe_host, port), timeout=0.2):
            return True
    except OSError:
        return False


def main(argv: list[str] | None = None) -> None:
    parser = build_argument_parser(argv)
    args = parser.parse_args(argv)
    if not (1 <= args.port <= 65535):
        parser.error("--port must be in 1..65535")
    if args.port_attempts < 1:
        parser.error("--port-attempts must be positive")
    if args.max_ticks < 1:
        parser.error("--max-ticks must be positive")
    if args.realtime_speed <= 0:
        parser.error("--realtime-speed must be positive")

    harness_root = Path(args.harness_root).resolve()
    harness_server = _harness_server(harness_root)
    install_harness_safety(harness_root=str(harness_root))
    try:
        args.seed = harness_server.normalize_scene_seed(args.seed)
        args.template_seed = harness_server.normalize_scene_seed(
            args.template_seed
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.realtime:
        args.view = True

    voice_transcriber = None
    if args.voice_control:
        voice_transcriber = harness_server.IsolatedVoiceTranscriber(
            model_name=args.voice_model,
        )

    stamp = time.strftime("%Y%m%d_%H%M%S")
    pid_file_path = Path(args.pid_file).resolve()
    session_dir = (
        Path(args.out)
        / f"continuous_{stamp}_{uuid.uuid4().hex[:6]}"
    ).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)

    args.task = "task_a" if args.start_far else "task_b"
    args.clarify = dict(args.clarify)
    compiler_factory = partial(
        KernelCompilerBridge,
        harness_root=str(harness_root),
    )
    executor_factory = partial(
        KernelExecutorBridge,
        harness_root=str(harness_root),
    )
    continuous_session_type = adapt_continuous_session_class(
        harness_server.ContinuousTaskSession,
        harness_root=harness_root,
    )

    def session_factory(
        *,
        scene_definition: Any,
        scene_seed: int,
        scene_epoch: int,
        artifacts_dir: Path,
    ) -> Any:
        session_args = copy.copy(args)
        session_args.scene = scene_definition.scene_id
        session_args.scene_definition = scene_definition
        session_args.scene_seed = scene_seed
        session_args.scene_epoch = scene_epoch
        session_args.seed = scene_seed
        (
            scene,
            world,
            runtime,
            logger,
            _loop,
            planner,
            perception,
        ) = harness_server.build_runtime(
            session_args,
            str(artifacts_dir),
        )
        planner = HarnessTaskProgramPlannerAdapter(planner)
        return continuous_session_type(
            scene=scene,
            world=world,
            runtime=runtime,
            planner=planner,
            perception=perception,
            logger=logger,
            artifacts_dir=artifacts_dir,
            executor_config={
                "max_ticks": args.max_ticks,
                "max_depth": args.max_depth,
                "max_repairs": args.max_repairs,
                "max_node_attempts": args.max_node_attempts,
                "max_reconciliations": args.max_reconciliations,
                "max_nodes": args.max_nodes,
                "max_repair_depth": args.max_repair_depth,
                "max_no_progress_cycles": args.max_no_progress_cycles,
            },
            record=args.record,
            record_fps=args.record_fps,
            compiler_factory=compiler_factory,
            executor_factory=executor_factory,
            recovery_executor_factory=executor_factory,
        )

    session = harness_server.SceneSessionManager(
        registry=harness_server.SCENE_REGISTRY,
        session_factory=session_factory,
        artifacts_root=session_dir,
        initial_scene_id=(
            None
            if args.scene == harness_server.AUTO_SCENE_ID
            else args.scene
        ),
        initial_seed=args.seed,
        initial_template_seed=(
            args.template_seed
            if args.scene == harness_server.AUTO_SCENE_ID
            else None
        ),
    )

    server = None
    pid_file_written = False
    try:
        server = bind_server(
            harness_server,
            args.host,
            args.port,
            args.port_attempts,
            make_handler(
                session,
                voice_control=args.voice_control,
                voice_transcriber=voice_transcriber,
                harness_server=harness_server,
            ),
        )
        host, port = server.server_address[:2]
        write_pid_file(pid_file_path, host=str(host), port=int(port))
        pid_file_written = True
        display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        url = f"http://{display_host}:{port}/"
        print()
        print("===== Task Recursive Tree + GeminiER2 =====")
        print(f"Server:    {url}")
        print(f"Artifacts: {session_dir}")
        print(f"Kernel:    TaskTreeKernel (PID {os.getpid()})")
        active_scene = session.state().get("scene") or {}
        print(
            "Scene:     "
            f"{active_scene.get('scene_id', args.scene)} "
            f"(template-seed={active_scene.get('template_seed')}, "
            f"scene-seed={active_scene.get('scene_seed', args.seed)})"
        )
        print(
            "Stop cancels the current physical action; resume restarts "
            "from a live goal check."
        )
        print("Press Ctrl+C to close the server and MuJoCo session.")
        print()
        if args.open_browser:
            timer = threading.Timer(0.5, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nClosing the continuous task-tree session...")
    finally:
        if server is not None:
            server.server_close()
        if voice_transcriber is not None:
            voice_transcriber.close()
        stopped = session.shutdown(
            timeout=15.0,
            reason="server shutdown",
        )
        if pid_file_written:
            remove_owned_pid_file(pid_file_path)
        if not stopped:
            print(
                "[WARN] The session worker did not stop before timeout.",
                file=sys.stderr,
            )


__all__ = [
    "bind_server",
    "build_argument_parser",
    "main",
    "make_handler",
    "remove_owned_pid_file",
    "write_pid_file",
]


if __name__ == "__main__":
    main()
