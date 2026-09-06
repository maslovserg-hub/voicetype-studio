"""Tests for ``Downloader.download_original`` — the Скачать button's path.

Nothing hits the network: ``yt_dlp.YoutubeDL`` is faked the same way
``test_downloader_youtube`` does it. What matters here is that the user
gets a real video file in their own folder, and that the transcription
path's options are left exactly as they were.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from core.downloader import Downloader


def _fake_ydl(captured: list, *, merged: Path | None, prepared: Path):
    class FakeYDL:
        def __init__(self, opts):
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            info = {"id": "abc", "ext": "mp4", "title": "Видео"}
            if merged is not None:
                info["requested_downloads"] = [{"filepath": str(merged)}]
            return info

        def prepare_filename(self, info):
            return str(prepared)

    return FakeYDL


def test_original_asks_for_video_into_dest_dir(tmp_path) -> None:
    dest = tmp_path / "Downloads"
    merged = dest / "Видео [abc].mp4"
    captured: list[dict] = []

    with patch("core.downloader._has_ffmpeg", return_value=True):
        with patch(
            "core.downloader.yt_dlp.YoutubeDL",
            _fake_ydl(captured, merged=merged, prepared=dest / "Видео [abc].webm"),
        ):
            result = asyncio.run(Downloader.download_original(
                "https://youtu.be/abc", dest,
            ))

    # The merged file wins over prepare_filename's stale extension.
    assert result == merged
    assert dest.is_dir()
    opts = captured[0]
    assert opts["format"] == "bestvideo+bestaudio/best"
    assert opts["merge_output_format"] == "mp4"
    assert str(dest) in opts["outtmpl"]
    # Truncated title + id suffix: no path-length blowups, no collisions.
    assert "%(title).120B [%(id)s].%(ext)s" in opts["outtmpl"]


def test_original_falls_back_to_muxed_stream_without_ffmpeg(tmp_path) -> None:
    dest = tmp_path / "Downloads"
    captured: list[dict] = []

    with patch("core.downloader._has_ffmpeg", return_value=False):
        with patch(
            "core.downloader.yt_dlp.YoutubeDL",
            _fake_ydl(captured, merged=None, prepared=dest / "v.mp4"),
        ):
            asyncio.run(Downloader.download_original("https://youtu.be/abc", dest))

    # Without ffmpeg yt-dlp can't mux, so never ask it to.
    assert captured[0]["format"] == "best"
    assert "merge_output_format" not in captured[0]


def test_transcription_path_options_unchanged(tmp_path, monkeypatch) -> None:
    """The default (transcription) call must not inherit the video opts."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []

    with patch(
        "core.downloader.yt_dlp.YoutubeDL",
        _fake_ydl(captured, merged=None, prepared=config.temp_dir / "abc.mp3"),
    ):
        asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))

    opts = captured[0]
    assert opts["format"].startswith("bestaudio/")
    assert "merge_output_format" not in opts
    assert opts["outtmpl"] == str(config.temp_dir / "%(id)s.%(ext)s")


def test_direct_link_is_moved_out_of_temp(tmp_path, monkeypatch) -> None:
    """Я.Диск / direct links download to temp — they must not stay there."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_path / "Downloads"

    staged = config.temp_dir / "lecture.mp4"
    staged.write_bytes(b"video")

    async def fake_direct(cls, url):  # noqa: ARG001
        return staged

    monkeypatch.setattr(
        Downloader, "_download_direct", classmethod(fake_direct),
    )

    result = asyncio.run(Downloader.download_original(
        "https://example.com/lecture.mp4", dest,
    ))

    assert result == dest / "lecture.mp4"
    assert result.read_bytes() == b"video"
    # The invariant the Транскриптор relies on: temp stays empty.
    assert list(config.temp_dir.iterdir()) == []


def test_direct_link_does_not_overwrite_existing_file(tmp_path, monkeypatch) -> None:
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_path / "Downloads"
    dest.mkdir()
    (dest / "lecture.mp4").write_bytes(b"older")

    staged = config.temp_dir / "lecture.mp4"
    staged.write_bytes(b"newer")

    async def fake_direct(cls, url):  # noqa: ARG001
        return staged

    monkeypatch.setattr(
        Downloader, "_download_direct", classmethod(fake_direct),
    )

    result = asyncio.run(Downloader.download_original(
        "https://example.com/lecture.mp4", dest,
    ))

    assert result == dest / "lecture (2).mp4"
    assert (dest / "lecture.mp4").read_bytes() == b"older"


def test_403_retries_with_mweb_client(tmp_path, monkeypatch) -> None:
    """A 403 is not a bot check — cookies won't help, a new client will."""
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    expected = config.temp_dir / "abc.mp4"
    captured: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            captured.append(opts)
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            client = (
                self._opts.get("extractor_args", {})
                .get("youtube", {})
                .get("player_client", [])
            )
            if "mweb" not in client:
                raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return {"id": "abc", "ext": "mp4"}

        def prepare_filename(self, info):
            return str(expected)

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        result = asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))

    assert result == expected
    # Exactly two attempts: default client, then mweb. No cookie detour.
    assert len(captured) == 2
    assert "extractor_args" not in captured[0]
    assert captured[1]["extractor_args"]["youtube"]["player_client"] == ["mweb"]
    assert "cookiesfrombrowser" not in captured[1]


def test_403_that_mweb_cannot_fix_reports_the_original_error(tmp_path, monkeypatch) -> None:
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
            raise Exception("ERROR: HTTP Error 403: Forbidden")

        def prepare_filename(self, info):
            return "unused"

    import pytest

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        with pytest.raises(RuntimeError, match="403"):
            asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))


def test_403_mid_download_is_retried_and_resumes(tmp_path, monkeypatch) -> None:
    """A URL dying halfway must not surface as an error on the first try.

    yt-dlp resumes from the ``.part`` file, so re-running the extraction
    picks up where it stopped instead of restarting the whole video.
    """
    from core import config

    monkeypatch.setattr(config, "data_dir", tmp_path)
    config.temp_dir.mkdir(parents=True, exist_ok=True)
    expected = config.temp_dir / "abc.mp4"
    attempts: list[dict] = []

    class FakeYDL:
        def __init__(self, opts):
            attempts.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def extract_info(self, url, download):
            # 1st: default client 403s. 2nd: mweb starts but dies
            # mid-stream. 3rd: mweb resumes and finishes.
            if len(attempts) < 3:
                raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return {"id": "abc", "ext": "mp4"}

        def prepare_filename(self, info):
            return str(expected)

    with patch("core.downloader.yt_dlp.YoutubeDL", FakeYDL):
        result = asyncio.run(Downloader._download_ytdlp("https://youtu.be/abc"))

    assert result == expected
    assert len(attempts) == 3
    # Ranged requests keep each GET short-lived — that's what stops the
    # 403 in the first place.
    assert attempts[0]["http_chunk_size"] == 10 * 1024 * 1024
