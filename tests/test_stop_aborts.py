"""⏹ Стоп must really stop the work, not just stop waiting for it.

Covers the pieces that run outside the asyncio task: the yt-dlp worker
thread, the ffmpeg process, and the SpeechKit operation on Yandex's side.
"""

from __future__ import annotations

import asyncio
import http.server
import threading
import time

from core import converter, speechkit
from core.downloader import Downloader


def _slow_server():
    """Local HTTP server serving an endless-ish .mp4 at ~1 MB/s."""
    state = {"disconnected": threading.Event()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self._headers()

        def do_GET(self):
            self._headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b"\0" * 64 * 1024)
                    time.sleep(0.05)
            except OSError:
                state["disconnected"].set()

        def _headers(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(1000 * 64 * 1024))
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def test_stop_aborts_ytdlp_and_removes_partial(tmp_path) -> None:
    srv, state = _slow_server()
    url = f"http://127.0.0.1:{srv.server_address[1]}/talk.mp4"
    started = threading.Event()

    async def scenario():
        job = asyncio.create_task(Downloader._download_ytdlp(
            url,
            progress_callback=lambda *_: started.set(),
            dest_dir=tmp_path,
            want_video=True,
        ))
        while not started.is_set():
            await asyncio.sleep(0.05)
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(scenario())
        # The worker thread must drop the connection and clean up on its own.
        assert state["disconnected"].wait(10), "yt-dlp kept downloading"
        deadline = time.monotonic() + 10
        while any(tmp_path.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not any(tmp_path.iterdir()), list(tmp_path.iterdir())
    finally:
        srv.shutdown()


def test_stop_kills_ffmpeg(tmp_path, monkeypatch) -> None:
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*_cmd, **kw):
        # Stand-in for a long ffmpeg run: one process, like ffmpeg. (The
        # venv python.exe is a launcher with a child that would keep the
        # pipes open after the kill.)
        p = await real_exec(
            r"C:\Windows\System32\ping.exe", "-n", "30", "127.0.0.1", **kw,
        )
        procs.append(p)
        return p

    monkeypatch.setattr(converter.asyncio, "create_subprocess_exec", fake_exec)
    src = tmp_path / "talk.mp4"
    src.write_bytes(b"x")

    async def scenario():
        job = asyncio.create_task(converter.AudioConverter.to_wav(src))
        while not procs and not job.done():
            await asyncio.sleep(0.05)
        job.cancel()
        try:
            await asyncio.wait_for(job, 10)
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert procs[0].returncode is not None, "ffmpeg still running"


class _Resp:
    def __init__(self, data):
        self.status = 200
        self._data = data

    async def text(self):
        return self._data if isinstance(self._data, str) else __import__("json").dumps(self._data)

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _NeverDoneSession:
    def __init__(self):
        self.post_urls: list[str] = []
        self.polls = 0

    def post(self, url, headers=None, json=None):
        self.post_urls.append(url)
        return _Resp({"id": "op1"})

    def get(self, url, headers=None, params=None):
        self.polls += 1
        return _Resp({"done": False})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_stop_cancels_speechkit_operation(tmp_path, monkeypatch) -> None:
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")

    async def fake_ogg(_src, dst):
        dst.write_bytes(b"OggS-fake")

    session = _NeverDoneSession()
    monkeypatch.setattr(speechkit, "_to_ogg_opus", fake_ogg)
    monkeypatch.setattr(speechkit, "client_session", lambda timeout=None: session)
    monkeypatch.setattr(speechkit, "POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(speechkit.config, "data_dir", tmp_path)
    (tmp_path / "tmp").mkdir()

    async def scenario():
        job = asyncio.create_task(speechkit.transcribe(src, "KEY"))
        while session.polls < 2:
            await asyncio.sleep(0.01)
        job.cancel()
        try:
            await job
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert session.post_urls[-1].endswith("/operations/op1:cancel")
