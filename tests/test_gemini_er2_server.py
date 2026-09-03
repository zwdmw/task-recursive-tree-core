from __future__ import annotations

import io
import json
import os
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from task_recursive_tree.integrations.gemini_er2 import server as server_module
from task_recursive_tree.integrations.gemini_er2.server import make_handler


class FakeSession:
    def state(self):
        return {
            "status": "idle",
            "worker_ready": True,
        }

    def scenes(self):
        return {"scenes": []}


class FakeVoiceTranscriber:
    def __init__(self):
        self.calls = []

    def status(self):
        return {
            "enabled": True,
            "state": "ready",
            "ready": True,
            "loading": False,
            "model": "small",
            "device": "cpu",
        }

    def transcribe_wav(self, audio_bytes, *, language):
        self.calls.append((audio_bytes, language))
        return {
            "text": "把圆柱都放在篮子里",
            "language": "zh",
            "model": "small",
            "device": "cpu",
        }


class FakeHarnessServer:
    SessionHTTPServer = ThreadingHTTPServer

    @staticmethod
    def make_handler(
        session,
        *,
        voice_control=False,
        voice_transcriber=None,
    ):
        del session

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status, payload):
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, payload):
                body = payload.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    voice_assets = (
                        '<link href="/assets/voice-control.css">'
                        '<script src="/assets/voice-control.js"></script>'
                        if voice_control
                        else ""
                    )
                    self._send_html(
                        "<html>"
                        f"{voice_assets}"
                        '<input placeholder="请输入任务指令">'
                        '<script>fetch("/api/instructions");'
                        'fetch("/api/scenes/switch");</script>'
                        "</html>"
                    )
                    return
                if (
                    path == "/api/voice/status"
                    and voice_control
                    and voice_transcriber is not None
                ):
                    self._send_json(
                        HTTPStatus.OK,
                        voice_transcriber.status(),
                    )
                    return
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "not found"},
                )

            def do_POST(self):
                path = urlsplit(self.path).path
                if (
                    path != "/api/voice/transcribe"
                    or not voice_control
                    or voice_transcriber is None
                ):
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "not found"},
                    )
                    return
                length = int(self.headers.get("Content-Length", "0"))
                audio = self.rfile.read(length)
                language = parse_qs(
                    urlsplit(self.path).query
                ).get("language", [None])[0]
                self._send_json(
                    HTTPStatus.OK,
                    voice_transcriber.transcribe_wav(
                        audio,
                        language=language,
                    ),
                )

            def log_message(self, format, *args):
                del format, args

        return Handler


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


def test_harness_ui_is_reused_and_health_identifies_new_project() -> None:
    harness_server = FakeHarnessServer
    server = harness_server.SessionHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            FakeSession(),
            harness_server=harness_server,
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        with urlopen(f"{base_url}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200
            assert "/api/instructions" in html
            assert "/api/scenes/switch" in html
            assert 'placeholder="请输入任务指令"' in html
            assert "maybePrefillRecommendedInstruction" not in html

        with urlopen(f"{base_url}/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            assert health["server"] == "task-recursive-tree"
            assert health["runtime"] == "gemini-er2-kernel"
            assert health["process_id"] == os.getpid()
            assert (
                Path(health["project_root"]).resolve()
                == server_module.PROJECT_ROOT.resolve()
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_harness_voice_ui_and_transcription_are_reused() -> None:
    harness_server = FakeHarnessServer
    transcriber = FakeVoiceTranscriber()
    server = harness_server.SessionHTTPServer(
        ("127.0.0.1", 0),
        make_handler(
            FakeSession(),
            voice_control=True,
            voice_transcriber=transcriber,
            harness_server=harness_server,
        ),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        with urlopen(f"{base_url}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert "/assets/voice-control.css" in html
            assert "/assets/voice-control.js" in html

        with urlopen(
            f"{base_url}/api/voice/status",
            timeout=5,
        ) as response:
            status = json.loads(response.read().decode("utf-8"))
            assert status["ready"] is True
            assert status["device"] == "cpu"

        audio = wav_bytes()
        request = Request(
            f"{base_url}/api/voice/transcribe?language=zh-CN",
            data=audio,
            method="POST",
            headers={"Content-Type": "audio/wav"},
        )
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            assert result["text"] == "把圆柱都放在篮子里"
        assert transcriber.calls == [(audio, "zh-CN")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_windows_launcher_enables_realtime_voice_by_default() -> None:
    launcher = Path(__file__).resolve().parents[1] / "启动任务递归树.cmd"
    payload = launcher.read_bytes()
    source = launcher.read_text(encoding="utf-8")

    assert b"\r\n" in payload
    assert b"\n" not in payload.replace(b"\r\n", b"")
    assert "--voice-control" in source
    assert "faster_whisper, opencc" in source
    assert "voice_control.js" in source
    assert "voice_control.css" in source
    assert "--scene recursive_recovery_showcase" in source
    assert "--viewer-camera free" in source
    assert "--viewer-camera overview" not in source
    assert source.index("--scene recursive_recovery_showcase") < \
        source.index("%*")
    assert source.index("--viewer-camera free") < source.index("%*")


def test_bind_server_skips_an_active_reusable_port() -> None:
    harness_server = FakeHarnessServer
    handler = make_handler(
        FakeSession(),
        harness_server=harness_server,
    )
    occupied = harness_server.SessionHTTPServer(
        ("127.0.0.1", 0),
        handler,
    )
    occupied_thread = Thread(
        target=occupied.serve_forever,
        daemon=True,
    )
    occupied_thread.start()
    selected = None

    try:
        occupied_port = int(occupied.server_address[1])
        selected = server_module.bind_server(
            harness_server,
            "127.0.0.1",
            occupied_port,
            20,
            handler,
        )

        assert selected.server_address[1] != occupied_port
    finally:
        if selected is not None:
            selected.server_close()
        occupied.shutdown()
        occupied.server_close()
        occupied_thread.join(timeout=5)


def test_harness_root_is_resolved_before_harness_server_import(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    harness_root = tmp_path / "alternate-harness"
    harness_root.mkdir()

    def ensure(value):
        calls.append(("ensure", Path(value)))
        return Path(value)

    class FakeHarnessServer:
        @staticmethod
        def build_argument_parser():
            import argparse

            return argparse.ArgumentParser()

    def import_module(name, *, harness_root=None):
        calls.append(("import", name, Path(harness_root)))
        return FakeHarnessServer

    monkeypatch.setattr(server_module, "ensure_harness_importable", ensure)
    monkeypatch.setattr(server_module, "import_harness_module", import_module)

    parser = server_module.build_argument_parser(
        ["--harness-root", str(harness_root)]
    )
    args = parser.parse_args(["--harness-root", str(harness_root)])

    assert args.harness_root == str(harness_root)
    assert calls == [
        ("ensure", harness_root),
        ("import", "scripts.run_tree_server", harness_root),
    ]
