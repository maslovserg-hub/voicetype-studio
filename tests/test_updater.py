"""Version comparison + the update script that does the actual work."""

from __future__ import annotations

import inspect
from pathlib import Path

from core import updater
from core.version import __version__


def test_parse_version_strips_v_and_suffix() -> None:
    assert updater.parse_version("v1.0.3") == (1, 0, 3)
    assert updater.parse_version("1.0.3") == (1, 0, 3)
    assert updater.parse_version("v1.2.10-beta") == (1, 2, 10)


def test_is_newer_compares_numerically() -> None:
    """``1.0.10`` beats ``1.0.9`` — a string compare would say otherwise."""
    assert updater.is_newer("1.0.10", "1.0.9")
    assert updater.is_newer("v1.1.0", "1.0.3")
    assert not updater.is_newer("1.0.3", "1.0.3")
    assert not updater.is_newer("1.0.2", "1.0.3")


def test_current_version_parses() -> None:
    assert len(updater.parse_version(__version__)) == 3


def test_update_script_shipped_and_sane() -> None:
    """The .bat must keep CRLF (cmd mis-parses labels otherwise) and must
    reach for the System32 tools — Git's tar can't read a zip."""
    script = Path("tools") / updater.SCRIPT_NAME
    assert script.is_file()
    raw = script.read_bytes()
    assert b"\r\n" in raw and raw.count(b"\r\n") == raw.count(b"\n")
    text = raw.decode("utf-8")
    assert "releases/latest/download/VoiceTypeStudio_release.zip" in text
    # Every external tool by absolute path. Git puts its own find/tar/curl
    # ahead of the Windows ones on PATH, and Git's `find` made the
    # "is it still running?" check always answer "no" — the unpack then
    # started while the exe was still locked and half-replaced the install.
    assert 'set "SYS=%SystemRoot%\\System32"' in text
    for tool in ("tar.exe", "curl.exe", "taskkill.exe", "ping.exe"):
        assert "%SYS%\\" + tool in text
    for bare in ("\nfind ", "\ntaskkill ", "\ntimeout "):
        assert bare not in text


def test_spec_bundles_update_script() -> None:
    spec = Path("VoiceTypeStudio.spec").read_text(encoding="utf-8")
    assert f"tools/{updater.SCRIPT_NAME}" in spec


def test_settings_window_takes_update_callback() -> None:
    from desktop.settings_window import SettingsWindow

    params = inspect.signature(SettingsWindow.__init__).parameters
    assert params["on_start_update"].default is None
