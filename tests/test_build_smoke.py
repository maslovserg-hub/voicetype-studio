"""Sanity checks for the PyInstaller specs and the launcher.

We don't run PyInstaller — that needs torch/gigaam installed and 10+ minutes.
Instead we validate that:

* both .spec files parse as Python (PyInstaller evaluates them at build time);
* the main spec mentions the right entry point + bundle name;
* the launcher's pure helpers work in isolation;
* `EXE_PATH` lines up with the install layout produced by the main spec.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_MAIN = PROJECT_ROOT / "VoiceTypeStudio.spec"
SPEC_LAUNCHER = PROJECT_ROOT / "launcher" / "Launcher.spec"
LAUNCHER_PY = PROJECT_ROOT / "launcher" / "launcher.py"


# ---- spec syntax --------------------------------------------------------


def _parse_spec(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_main_spec_parses() -> None:
    _parse_spec(SPEC_MAIN)


def test_launcher_spec_parses() -> None:
    _parse_spec(SPEC_LAUNCHER)


# ---- main spec content --------------------------------------------------


def test_main_spec_targets_main_py() -> None:
    text = SPEC_MAIN.read_text(encoding="utf-8")
    assert "'main.py'" in text
    assert "name='VoiceTypeStudio'" in text


def test_main_spec_collects_required_packages() -> None:
    """If we forget one of these, PyInstaller silently ships a broken bundle."""
    text = SPEC_MAIN.read_text(encoding="utf-8")
    for pkg in (
        "gigaam",
        "torch",
        "torchaudio",
        "customtkinter",
        "tkinterdnd2",
        "aiogram",
        "pydantic",
        "yt_dlp",
        "pydub",
    ):
        assert f"_collect('{pkg}')" in text, f"main spec missing collect for {pkg!r}"


def test_main_spec_excludes_unused_ml_packages() -> None:
    text = SPEC_MAIN.read_text(encoding="utf-8")
    for excluded in ("torchvision", "torchtext", "torchrec", "PyQt5"):
        assert f"'{excluded}'" in text, f"main spec missing exclude for {excluded!r}"


def test_main_spec_console_disabled() -> None:
    """Tray apps must not flash a console window on launch."""
    text = SPEC_MAIN.read_text(encoding="utf-8")
    assert "console=False" in text


def test_main_spec_uses_onedir_not_onefile() -> None:
    """``--onefile`` would unpack ~280 MB to %TEMP% on every launch."""
    text = SPEC_MAIN.read_text(encoding="utf-8")
    assert "COLLECT(" in text, "main spec must use COLLECT (onedir), not onefile EXE"


# ---- launcher spec content ----------------------------------------------


def test_launcher_spec_produces_setup_exe() -> None:
    text = SPEC_LAUNCHER.read_text(encoding="utf-8")
    assert "name='VoiceTypeStudio-Setup'" in text
    assert "console=False" in text
    # No COLLECT — single-file build.
    assert "COLLECT(" not in text


def test_launcher_spec_targets_launcher_py() -> None:
    text = SPEC_LAUNCHER.read_text(encoding="utf-8")
    assert "'launcher.py'" in text


# ---- launcher.py logic --------------------------------------------------


def test_launcher_module_imports_and_has_helpers() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher_mod", LAUNCHER_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for name in (
        "already_installed",
        "launch",
        "run_installer",
        "model_present",
        "model_resolved",
        "create_start_menu_shortcut",
        "shortcut_exists",
    ):
        assert callable(getattr(mod, name)), f"launcher.{name} missing or not callable"
    assert mod.INSTALL_DIR
    assert mod.EXE_PATH.endswith("VoiceTypeStudio.exe")
    assert mod.POINTER_FILE.endswith("gigaam_cache_path.txt")
    assert mod.MODEL_NAME == "v3_e2e_ctc"


def test_launcher_pointer_file_lives_next_to_exe() -> None:
    """``core.config._resolve_gigaam_cache`` reads ``gigaam_cache_path.txt``
    from the directory holding the running exe. The launcher must drop it
    in exactly that directory."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher_mod", LAUNCHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert os.path.dirname(mod.POINTER_FILE) == os.path.dirname(mod.EXE_PATH)


def test_launcher_model_present_requires_both_files(tmp_path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher_mod", LAUNCHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    folder = tmp_path / "gigaam"
    folder.mkdir()
    # Tokenizer only — should still report False.
    (folder / "v3_e2e_ctc_tokenizer.model").write_bytes(b"x" * 2_000_000)
    assert mod.model_present(str(folder)) is False

    # Add the ckpt at plausible size — now True.
    (folder / "v3_e2e_ctc.ckpt").write_bytes(b"x" * 2_000_000)
    assert mod.model_present(str(folder)) is True

    # Truncate the ckpt below the 1 MB threshold — half-download case.
    (folder / "v3_e2e_ctc.ckpt").write_bytes(b"x" * 1024)
    assert mod.model_present(str(folder)) is False


def test_launcher_start_menu_shortcut_path() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher_mod", LAUNCHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    path = mod._start_menu_shortcut_path()
    assert path.endswith("VoiceType Studio.lnk")
    # Must land inside the per-user Start menu, not all-users.
    assert "Start Menu" in path


def test_launcher_install_dir_matches_main_bundle_name() -> None:
    """Launcher unpacks the zip into INSTALL_DIR; the zip is expected to
    contain a top-level ``VoiceTypeStudio/`` folder produced by the main
    spec's ``COLLECT(name='VoiceTypeStudio')``. EXE_PATH must therefore
    point at ``<INSTALL_DIR>/VoiceTypeStudio/VoiceTypeStudio.exe``."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("launcher_mod", LAUNCHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.EXE_PATH.startswith(mod.INSTALL_DIR)
    rel = mod.EXE_PATH[len(mod.INSTALL_DIR):].lstrip("\\/").replace("\\", "/")
    assert rel == "VoiceTypeStudio/VoiceTypeStudio.exe", rel


def test_launcher_app_url_is_https_release() -> None:
    text = LAUNCHER_PY.read_text(encoding="utf-8")
    # We expect a real https URL — a plain "TODO" or empty string would
    # silently brick the launcher.
    assert 'APP_URL = (' in text or 'APP_URL =' in text
    # Pin: must be HTTPS.
    assert "https://" in text


# ---- requirements.txt covers the bundled stack ---------------------------


def test_requirements_txt_lists_studio_only_deps() -> None:
    """customtkinter and tkinterdnd2 are Studio-only; if they're missing
    here, ``pip install -r requirements.txt`` won't seed a build env."""
    req = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for pkg in ("customtkinter", "tkinterdnd2", "aiogram"):
        assert pkg in req, f"requirements.txt missing {pkg!r}"
