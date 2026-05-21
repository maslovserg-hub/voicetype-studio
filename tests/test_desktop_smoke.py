"""Smoke tests for the desktop UI layer.

Most desktop modules need a real Windows GUI environment to actually run
(tk root, system tray, audio device, global hotkey). These tests stay at
the import + pure-function level — anything heavier is verified manually
against the running app per the plan's Этап 3 acceptance.
"""

from __future__ import annotations

import sys

import pytest


def test_desktop_modules_import() -> None:
    """All desktop submodules import without instantiating GUI objects."""
    from desktop import autostart, dictation, overlay, single_instance, tray

    # Public symbols exist.
    assert callable(single_instance.acquire)
    assert callable(autostart.is_enabled)
    assert callable(autostart.set_enabled)
    assert callable(autostart.toggle)
    assert callable(tray.build_tray)
    assert callable(dictation.clean_dictation_text)
    assert overlay.Overlay is not None
    assert dictation.DictationListener is not None


def test_no_residual_bot_imports() -> None:
    """Same guard rail as core/: no leftover ``from bot.*``."""
    import desktop  # noqa: F401

    # Touch each submodule so it shows up in sys.modules.
    from desktop import autostart, dictation, overlay, single_instance, tray  # noqa: F401

    for name, module in list(sys.modules.items()):
        if not name.startswith("desktop"):
            continue
        src = getattr(module, "__file__", None)
        if not src:
            continue
        with open(src, "r", encoding="utf-8") as f:
            text = f.read()
        for forbidden in ("from bot.", "import bot."):
            assert forbidden not in text, f"{name} still references {forbidden!r}"


def test_clean_dictation_text_drops_short_latin() -> None:
    """Latin tokens shorter than 3 chars or vowel-poor are dropped.

    Behaviour copied verbatim from Voice Type — anything with fewer than 2
    vowels (like ``world`` or ``xyz``) is treated as ASR noise, while
    whitelist words (``and``, ``ok`` …) survive.
    """
    from desktop.dictation import clean_dictation_text

    # ``xyz`` (no vowels) dropped; ``and`` (whitelist) kept.
    assert clean_dictation_text("привет xyz и and мир") == "привет и and мир"
    # ``audio`` (4 vowels) and ``video`` (3 vowels) survive.
    assert clean_dictation_text("audio video") == "audio video"
    # Phantom 'ы' glued to the start of a Russian word gets stripped.
    # (The regex requires no space — ``"ы привет"`` would survive.)
    assert clean_dictation_text("ыпривет") == "привет"
    # Standalone 'ы' separated by space is left alone.
    assert clean_dictation_text("ну ы привет") == "ну ы привет"


def test_clean_dictation_text_collapses_whitespace() -> None:
    from desktop.dictation import clean_dictation_text

    assert clean_dictation_text("слово   ещё    одно") == "слово ещё одно"


def test_single_instance_idempotent() -> None:
    """Calling acquire() twice from the same process yields True both times."""
    from desktop import single_instance

    assert single_instance.acquire("VoiceTypeStudio_test_idempotent") is True
    assert single_instance.acquire("VoiceTypeStudio_test_idempotent") is True


def test_autostart_is_enabled_returns_bool() -> None:
    """Read-only check shouldn't raise even if the registry value is absent."""
    from desktop import autostart

    result = autostart.is_enabled()
    assert isinstance(result, bool)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke")
def test_dictation_constants_match_voice_type() -> None:
    """Audio-pipeline tunables must stay 1:1 with the standalone Voice Type."""
    from desktop import dictation

    assert dictation.SAMPLE_RATE == 16000
    assert dictation.BLOCKSIZE == 1600
    assert dictation.SILENCE_BLK == 15
    assert dictation.MAX_BLOCKS == 150


def test_overlay_class_attributes() -> None:
    """Overlay's geometry/colour constants survived the refactor."""
    from desktop.overlay import Overlay

    assert Overlay.W == 72 and Overlay.H == 38
    assert Overlay.N_BARS == 5
    assert Overlay.ACCENT.startswith("#")


def test_transcriber_has_transcribe_array() -> None:
    """The dictation path needs a sync entry into core.Transcriber."""
    from core import Transcriber

    assert callable(Transcriber.transcribe_array)


def test_build_tray_accepts_history_kwarg() -> None:
    """Tray menu now wires the History window — keep the contract stable."""
    import inspect

    from desktop.tray import build_tray

    sig = inspect.signature(build_tray)
    assert "on_open_history" in sig.parameters
    # Optional — older callers (and tests that mock tray) may omit it.
    assert sig.parameters["on_open_history"].default is None


def test_dictation_watchdog_reopens_dead_stream() -> None:
    """Watchdog must reopen a stream whose ``.active`` flipped to False.

    Real failure mode this guards: Game Bar mic privacy toggle aborts the
    PortAudio stream silently — the app stays alive in tray with a dead
    stream until manually restarted. The watchdog should detect this and
    call ``_open_mic_stream`` to get audio flowing again.
    """
    from desktop.dictation import DictationListener

    class _FakeStream:
        def __init__(self) -> None:
            self.active = True

        def stop(self) -> None:
            self.active = False

        def close(self) -> None:
            self.active = False

    listener = DictationListener(transcribe_fn=lambda a, sr: "", overlay=None)
    listener._mic_stream = _FakeStream()

    # Liveness check matches what watchdog calls.
    assert listener._stream_alive() is True

    # Simulate PortAudio aborting the stream.
    listener._mic_stream.active = False
    assert listener._stream_alive() is False

    # _open_mic_stream is what the watchdog calls. Patch sounddevice import
    # so we don't touch real hardware in the test.
    import sys
    import types

    fake_sd = types.ModuleType("sounddevice")
    opened: list[_FakeStream] = []

    def _InputStream(**kwargs):  # noqa: ARG001 — match real signature loosely
        s = _FakeStream()
        opened.append(s)
        # Real sounddevice streams expose start() too.
        s.start = lambda: None  # type: ignore[attr-defined]
        return s

    fake_sd.InputStream = _InputStream  # type: ignore[attr-defined]
    saved = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake_sd
    try:
        assert listener._open_mic_stream() is True
    finally:
        if saved is not None:
            sys.modules["sounddevice"] = saved
        else:
            sys.modules.pop("sounddevice", None)

    assert opened, "watchdog reopen path must construct a fresh InputStream"
    assert listener._stream_alive() is True


def test_dictation_open_mic_stream_swallows_failure() -> None:
    """If sounddevice raises (no device, blocked by privacy), return False.

    The watchdog backs off and keeps trying — but only if ``_open_mic_stream``
    doesn't propagate the exception out of the watchdog thread.
    """
    import sys
    import types

    from desktop.dictation import DictationListener

    fake_sd = types.ModuleType("sounddevice")

    def _InputStream(**kwargs):  # noqa: ARG001
        raise RuntimeError("Error opening InputStream: Device unavailable")

    fake_sd.InputStream = _InputStream  # type: ignore[attr-defined]

    listener = DictationListener(transcribe_fn=lambda a, sr: "", overlay=None)
    saved = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake_sd
    try:
        assert listener._open_mic_stream() is False
    finally:
        if saved is not None:
            sys.modules["sounddevice"] = saved
        else:
            sys.modules.pop("sounddevice", None)
    assert listener._mic_stream is None
