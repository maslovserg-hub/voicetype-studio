"""Non-Russian transcription: YouTube subtitles and Yandex SpeechKit.

No network: yt-dlp payloads are fed in as dicts/strings, the SpeechKit HTTP
session is replaced with a fake.
"""

from __future__ import annotations

import asyncio
import json

from core import speechkit
from core.downloader import _pick_subtitle_track, parse_json3_subtitles
from desktop._format_dispatch import ALL_FORMATS
from desktop._message_widget import _BUTTON_ROWS


# --- SpeechKit ----------------------------------------------------------


def _utt(kind: str, text: str, start: int, end: int, channel: str = "0") -> dict:
    alt = {"alternatives": [{"text": text, "startTimeMs": str(start), "endTimeMs": str(end)}]}
    body = {"normalizedText": alt} if kind == "finalRefinement" else alt
    return {"result": {"channelTag": channel, kind: body}}


def test_parse_recognition_prefers_refined_text() -> None:
    body = "\n".join(json.dumps(o) for o in [
        _utt("final", "hello there", 0, 1500),
        _utt("finalRefinement", "Hello there.", 0, 1500),
        {"result": {"channelTag": "0", "partial": {"alternatives": []}}},
        _utt("final", "how are you", 2000, 3000),
        _utt("finalRefinement", "How are you?", 2000, 3000),
        _utt("finalRefinement", "other channel", 0, 1, channel="1"),
    ])
    segs = speechkit.parse_recognition(body)
    assert [(s.start, s.end, s.text) for s in segs] == [
        (0.0, 1.5, "Hello there."),
        (2.0, 3.0, "How are you?"),
    ]


def test_parse_recognition_keeps_word_timings() -> None:
    """One long utterance must still yield short timestamped lines."""
    from core import Formatter, OutputFormat

    words = [
        {"text": f"w{i}", "startTimeMs": str(i * 1000), "endTimeMs": str(i * 1000 + 500)}
        for i in range(20)
    ]
    alt = {"text": " ".join(w["text"] for w in words), "startTimeMs": "0",
           "endTimeMs": "19500", "words": words}
    body = json.dumps({"result": {"channelTag": "0", "final": {"alternatives": [alt]}}})
    segs = speechkit.parse_recognition(body)
    assert len(segs) == 1 and len(segs[0].words) == 20
    assert segs[0].words[3].start == 3.0
    lines = Formatter.format(segs, OutputFormat.TIMESTAMPS).splitlines()
    assert len(lines) > 1 and lines[1].startswith("[00:0")


def test_parse_recognition_falls_back_to_final_and_accepts_array() -> None:
    body = json.dumps([_utt("final", "raw words", 500, 900)])
    segs = speechkit.parse_recognition(body)
    assert [(s.start, s.text) for s in segs] == [(0.5, "raw words")]


class _FakeResp:
    def __init__(self, data, status: int = 200):
        self.status = status
        self._data = data

    async def text(self):
        return self._data if isinstance(self._data, str) else json.dumps(self._data)

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, result_body: str):
        self.posted: list[dict] = []
        self.headers: list[dict] = []
        self.op_polls = 0
        self.result_body = result_body

    def post(self, url, headers=None, json=None):
        self.posted.append(json)
        self.headers.append(headers)
        return _FakeResp({"id": "op1"})

    def get(self, url, headers=None, params=None):
        if "operations/op1" in url:
            self.op_polls += 1
            return _FakeResp({"done": self.op_polls >= 2})
        assert params == {"operationId": "op1"}
        return _FakeResp(self.result_body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_speechkit_transcribe_end_to_end(tmp_path, monkeypatch) -> None:
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")

    async def fake_ogg(_src, dst):
        dst.write_bytes(b"OggS-fake")

    session = _FakeSession(json.dumps(_utt("finalRefinement", "Hi.", 0, 700)))
    monkeypatch.setattr(speechkit, "_to_ogg_opus", fake_ogg)
    monkeypatch.setattr(speechkit, "client_session", lambda timeout=None: session)
    monkeypatch.setattr(speechkit, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(speechkit.config, "data_dir", tmp_path)
    (tmp_path / "tmp").mkdir()

    statuses: list[str] = []
    segs = asyncio.run(speechkit.transcribe(src, "KEY", statuses.append))

    assert [(s.start, s.end, s.text) for s in segs] == [(0.0, 0.7, "Hi.")]
    assert session.op_polls == 2 and statuses
    assert session.headers[0] == {"Authorization": "Api-Key KEY"}
    model = session.posted[0]["recognitionModel"]
    assert model["languageRestriction"]["languageCode"] == ["auto"]
    assert model["audioFormat"]["containerAudio"]["containerAudioType"] == "OGG_OPUS"
    assert not list((tmp_path / "tmp").glob("speechkit_*.ogg")), "temp ogg must be removed"


def test_speechkit_requires_key(tmp_path) -> None:
    import pytest
    with pytest.raises(RuntimeError, match="SpeechKit"):
        asyncio.run(speechkit.transcribe(tmp_path / "a.wav", "  "))


# --- YouTube subtitles ---------------------------------------------------


def _fmt(url: str) -> list[dict]:
    return [{"ext": "vtt", "url": url + ".vtt"}, {"ext": "json3", "url": url}]


def test_pick_prefers_manual_in_video_language() -> None:
    info = {
        "language": "en",
        "subtitles": {"en-US": _fmt("manual-en"), "de": _fmt("manual-de")},
        "automatic_captions": {"en-orig": _fmt("auto-orig")},
    }
    assert _pick_subtitle_track(info)["url"] == "manual-en"


def test_pick_falls_back_to_orig_auto_captions() -> None:
    info = {
        "language": "en",
        "subtitles": {},
        "automatic_captions": {"ru": _fmt("auto-ru"), "en-orig": _fmt("auto-orig")},
    }
    assert _pick_subtitle_track(info)["url"] == "auto-orig"


def test_pick_returns_none_without_tracks() -> None:
    assert _pick_subtitle_track({"automatic_captions": {"ru": _fmt("x")}}) is None


def test_parse_json3_subtitles() -> None:
    raw = json.dumps({"events": [
        {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello"}, {"utf8": " world"}]},
        {"tStartMs": 1500, "dDurationMs": 10, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2000},
        {"tStartMs": 3000, "dDurationMs": 2000, "segs": [{"utf8": "Second\nline"}]},
    ]})
    segments = parse_json3_subtitles(raw)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 1.5, "Hello world"),
        (3.0, 5.0, "Second line"),
    ]


# --- UI wiring -----------------------------------------------------------


def test_every_format_has_a_button() -> None:
    flat = [k for row in _BUTTON_ROWS for k in row]
    assert sorted(flat) == sorted(ALL_FORMATS)
