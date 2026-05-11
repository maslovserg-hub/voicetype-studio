"""Tests for the YouTube bot-check fallback in core.Downloader.

We don't actually call YouTube — we mock ``yt_dlp.YoutubeDL`` to simulate
the "Sign in to confirm you're not a bot" error and verify our fallback
loop tries each browser's cookies before giving up.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.downloader import (
    Downloader,
    _looks_like_bot_check,
    _strip_ansi,
)


# ---- pure helpers --------------------------------------------------------


def test_strip_ansi_removes_color_codes() -> None:
    raw = "\x1b[0;31mERROR:\x1b[0m [youtube] xxx: Sign in to confirm"
    cleaned = _strip_ansi(raw)
    assert "\x1b" not in cleaned
    assert "ERROR:" in cleaned
    assert "[0;31m" not in cleaned


def test_strip_ansi_passes_clean_text() -> None:
    assert _strip_ansi("plain") == "plain"
    assert _strip_ansi("") == ""
    assert _strip_ansi(None) == ""  # type: ignore[arg-type]


def test_bot_check_marker_detection() -> None:
    assert _looks_like_bot_check(
        "ERROR: [youtube] xxx: Sign in to confirm you're not a bot"
    )
    assert _looks_like_bot_check("Use --cookies-from-browser ...")
    assert not _looks_like_bot_check("HTTP 404")
    assert not _looks_like_bot_check("")


# ---- fallback loop -------------------------------------------------------


def test_no_cookies_succeeds_no_browser_attempts(tmp_path, monkeypatch) -> None:
    """Happy path — vanilla URL works on first try, no browsers consulted."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    expected = tmp_path / "tmp" / "abc.mp3"
    captured_opts: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            captured_opts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            return {"id": "abc", "ext": "mp3"}

        def prepare_filename(self, info):
            return str(expected)

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        result = asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))

    assert result == expected
    assert len(captured_opts) == 1
    assert "cookiesfrombrowser" not in captured_opts[0]


def test_bot_check_triggers_harvest_then_browser_fallback(tmp_path, monkeypatch) -> None:
    """After bot-check, the new flow goes:
        1. ``_harvest_browser_cookies`` — our own Chromium decryption
        2. only if harvest returned 0 rows / failed: ``_try_browser`` chain.
    """
    from core import config
    from core import downloader as dl_mod

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    expected = tmp_path / "tmp" / "abc.mp3"
    captured: list = []

    class FakeYDL:
        def __init__(self, opts):
            captured.append({
                "browser": opts.get("cookiesfrombrowser"),
                "cookiefile": opts.get("cookiefile"),
            })

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            opts = captured[-1]
            if opts["browser"] is None and opts["cookiefile"] is None:
                # First call — vanilla → bot check.
                raise RuntimeError(
                    "ERROR: [youtube] xxx: Sign in to confirm you're not a bot."
                )
            return {"id": "abc", "ext": "mp3"}

        def prepare_filename(self, info):
            return str(expected)

    # Stub the harvester to return a fake cookies.txt — pretends we read
    # from Chromium.
    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text("# Netscape HTTP Cookie File\n")

    def fake_export(domains, output):
        Path(output).write_text("# Netscape HTTP Cookie File\nfake row\n")
        return {"total_rows": 1, "browsers_tried": ["yandex"], "rows_per_browser": {"yandex": 1}, "output": output}

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL), \
         patch("core.downloader.export_cookies_for_domains", side_effect=fake_export):
        result = asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))

    assert result == expected
    # First attempt: vanilla. Second: cookies.txt from harvest (NOT browser).
    assert captured[0]["browser"] is None and captured[0]["cookiefile"] is None
    assert captured[1]["cookiefile"] is not None
    assert captured[1]["browser"] is None


def test_ydl_opts_enable_node_runtime(tmp_path, monkeypatch) -> None:
    """yt-dlp 2026.3+ requires explicit ``js_runtimes`` to use Node.js — by
    default only ``deno`` is enabled, and without ANY runtime the n-sig
    challenge fails and only storyboard images are returned. We pass node
    so the user gets real audio/video formats."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    captured: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            return {"id": "x", "ext": "mp3"}

        def prepare_filename(self, info):
            return str(tmp_path / "tmp" / "x.mp3")

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        asyncio.run(Downloader._download_ytdlp("https://www.youtube.com/watch?v=abc"))

    assert captured
    runtimes = captured[0].get("js_runtimes", {})
    assert "node" in runtimes, "node runtime must be enabled for n-sig challenge"


def test_browser_order_prefers_firefox_over_chrome() -> None:
    """Firefox first — doesn't have app-bound encryption, yt-dlp reads
    its cookies reliably. Yandex is intentionally NOT in this list because
    yt-dlp emits ``unsupported browser`` for it; instead our own
    ``cookies_extractor`` handles Yandex via direct DB read."""
    order = Downloader._COOKIE_BROWSERS
    assert order[0] == "firefox"
    assert "yandex" not in order, "Yandex goes through cookies_extractor, not yt-dlp"
    assert order.index("firefox") < order.index("chrome")


def test_non_bot_error_is_not_retried(tmp_path, monkeypatch) -> None:
    """A 404 / network error must NOT trigger the cookie sweep — that would
    delay the user's real error feedback by 6× ``ydl.extract_info`` calls."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    call_count = 0

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("ERROR: HTTP 404 Not Found")

        def prepare_filename(self, info):
            raise AssertionError("should not be reached")

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        with pytest.raises(RuntimeError, match="404"):
            asyncio.run(Downloader._download_ytdlp("https://youtu.be/x"))

    assert call_count == 1, "non-bot errors must not be retried with cookies"


def test_all_browsers_fail_yields_friendly_error(tmp_path, monkeypatch) -> None:
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            raise RuntimeError(
                "ERROR: Sign in to confirm you're not a bot."
            )

        def prepare_filename(self, info):
            raise AssertionError("never")

    # Harvest finds nothing.
    def fake_export(domains, output):
        Path(output).write_text("# Netscape HTTP Cookie File\n")
        return {"total_rows": 0, "browsers_tried": [], "rows_per_browser": {}, "output": output}

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL), \
         patch("core.downloader.export_cookies_for_domains", side_effect=fake_export):
        with pytest.raises(RuntimeError, match="cookies"):
            asyncio.run(Downloader._download_ytdlp("https://youtu.be/x"))
