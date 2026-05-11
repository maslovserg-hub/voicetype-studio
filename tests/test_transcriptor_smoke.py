"""Smoke tests for the transcriptor window: imports + pure helpers.

Tk widgets and DnD bindings need a real display + DnD-aware root, so we
don't instantiate the window here. The pure-function helpers
(``classify_input``, ``label_for_source``, ``_parse_dropped_paths``) cover
enough of the user-input path to catch regressions.
"""

from __future__ import annotations

import inspect

from desktop.transcriptor_window import (
    InputBar,
    TranscriptionTask,
    TranscriptorWindow,
    _parse_dropped_paths,
    classify_input,
    label_for_source,
)


def test_module_exports() -> None:
    assert TranscriptorWindow is not None
    assert InputBar is not None
    assert TranscriptionTask is not None


def test_classify_input_url() -> None:
    assert classify_input("https://youtube.com/watch?v=abc")[0] == "url"
    assert classify_input("HTTPS://disk.yandex.ru/i/xyz")[0] == "url"
    assert classify_input("  http://example.com  ")[0] == "url"


def test_classify_input_invalid() -> None:
    assert classify_input("")[0] == "invalid"
    assert classify_input("   ")[0] == "invalid"
    assert classify_input("/path/that/does/not/exist.mp4")[0] == "invalid"


def test_classify_input_file(tmp_path) -> None:
    f = tmp_path / "sample.mp3"
    f.write_bytes(b"\x00")
    kind, normalized = classify_input(str(f))
    assert kind == "file"
    assert normalized == str(f)


def test_classify_input_strips_quotes(tmp_path) -> None:
    """Windows often pastes paths quoted — handle both forms."""
    f = tmp_path / "x.mp3"
    f.write_bytes(b"\x00")
    kind, _ = classify_input(f'"{f}"')
    assert kind == "file"


def test_label_for_source_url_known_hosts() -> None:
    assert "Я.Диск" in label_for_source("url", "https://disk.yandex.ru/i/abc")
    assert "YouTube" in label_for_source("url", "https://youtube.com/watch?v=x")
    assert "RuTube" in label_for_source("url", "https://rutube.ru/video/zzz")
    assert "VK" in label_for_source("url", "https://vk.com/video1")
    assert "Google Drive" in label_for_source(
        "url", "https://drive.google.com/file/d/abc"
    )


def test_label_for_source_url_unknown_host() -> None:
    label = label_for_source("url", "https://example.org/file.mp3")
    assert "example.org" in label


def test_label_for_source_file() -> None:
    label = label_for_source("file", "C:/data/recording.mp4")
    assert "📎" in label
    assert "recording.mp4" in label


def test_parse_dropped_paths_simple() -> None:
    assert _parse_dropped_paths("C:/x.mp3") == ["C:/x.mp3"]


def test_parse_dropped_paths_multiple() -> None:
    assert _parse_dropped_paths("C:/x.mp3 C:/y.mp4") == ["C:/x.mp3", "C:/y.mp4"]


def test_parse_dropped_paths_with_spaces_in_filename() -> None:
    """tkinterdnd2 wraps space-containing paths in ``{...}``."""
    raw = "{C:/With Spaces/file one.mp3} {C:/two.mp4}"
    assert _parse_dropped_paths(raw) == [
        "C:/With Spaces/file one.mp3",
        "C:/two.mp4",
    ]


def test_parse_dropped_paths_empty() -> None:
    assert _parse_dropped_paths("") == []
    assert _parse_dropped_paths(None) == []  # type: ignore[arg-type]


def test_window_has_restore_from_history() -> None:
    """History window relies on this entry-point — keep the contract stable."""
    assert hasattr(TranscriptorWindow, "restore_from_history")
    sig = inspect.signature(TranscriptorWindow.restore_from_history)
    for required in ("source_label", "source", "segments"):
        assert required in sig.parameters, f"missing kwarg {required}"
        assert (
            sig.parameters[required].kind == inspect.Parameter.KEYWORD_ONLY
        ), f"{required} must be keyword-only"
