"""Verify that pre-downloaded models are reused, not re-downloaded.

The user's machine already has GigaAM v3_e2e_ctc (422 MB) under
``C:\\gigaam_cache\\`` and silero v4_ru (38 MB) under
``~/.cache/silero/``. A fresh install on a different machine wouldn't have
either, but on this machine our code MUST pick them up.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---- GigaAM ------------------------------------------------------------


def test_transcriber_uses_config_cache_dir(tmp_path, monkeypatch) -> None:
    """``Transcriber._load_model`` must hand ``config.gigaam_cache_dir`` to
    ``gigaam.load_model(download_root=...)`` — not ``os.getenv`` directly,
    not the gigaam default."""
    from core import Transcriber, config

    monkeypatch.setattr(config, "gigaam_cache_dir", tmp_path / "fake_cache")
    monkeypatch.setattr(Transcriber, "_model", None)
    monkeypatch.setattr(Transcriber, "_use_longform", None)

    fake_load = MagicMock(return_value=MagicMock())
    fake_torch = MagicMock()
    fake_gigaam = MagicMock()
    fake_gigaam.load_model = fake_load

    with patch.dict(sys.modules, {"gigaam": fake_gigaam, "torch": fake_torch}):
        Transcriber._load_model()

    fake_load.assert_called_once()
    kwargs = fake_load.call_args.kwargs
    assert kwargs["download_root"] == str(tmp_path / "fake_cache")
    assert kwargs["device"] == "cpu"


def test_transcriber_default_cache_is_real_disk_location() -> None:
    """The factory default ``C:/gigaam_cache`` is what's already on disk;
    if someone changes it they must update the existing-models setup too."""
    from core import config

    assert str(config.gigaam_cache_dir).replace("\\", "/").lower() == "c:/gigaam_cache"


def test_resolve_gigaam_cache_reads_pointer_file(tmp_path, monkeypatch) -> None:
    """The launcher drops ``gigaam_cache_path.txt`` next to the exe when
    the user points at a pre-existing model folder. ``_resolve_gigaam_cache``
    must pick that up so the app doesn't re-download."""
    import importlib
    cfg_mod = importlib.import_module("core.config")

    monkeypatch.delenv("GIGAAM_CACHE_DIR", raising=False)
    user_folder = tmp_path / "my_gigaam"
    user_folder.mkdir()
    pointer = tmp_path / "exe_dir" / "gigaam_cache_path.txt"
    pointer.parent.mkdir()
    pointer.write_text(str(user_folder), encoding="utf-8")

    # Pretend we're running as a frozen exe out of ``exe_dir``.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(pointer.parent / "VoiceTypeStudio.exe"))

    resolved = cfg_mod._resolve_gigaam_cache()
    assert resolved == user_folder


def test_resolve_gigaam_cache_env_var_wins_over_pointer(tmp_path, monkeypatch) -> None:
    """Env var beats pointer file beats default — the override order
    matters when a CI run wants to redirect cache and there's a stray
    pointer file lying around in the repo."""
    import importlib
    cfg_mod = importlib.import_module("core.config")

    user_folder = tmp_path / "from_pointer"
    user_folder.mkdir()
    env_folder = tmp_path / "from_env"
    env_folder.mkdir()
    pointer = tmp_path / "exe" / "gigaam_cache_path.txt"
    pointer.parent.mkdir()
    pointer.write_text(str(user_folder), encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(pointer.parent / "x.exe"))
    monkeypatch.setenv("GIGAAM_CACHE_DIR", str(env_folder))

    resolved = cfg_mod._resolve_gigaam_cache()
    assert resolved == env_folder


def test_transcriber_uses_punctuated_model() -> None:
    """Regression — earlier the Transcriber had a hardcoded
    ``DEFAULT_MODEL = os.getenv("GIGAAM_MODEL", "v3_ctc")`` that ignored
    config and downloaded the unpunctuated ``v3_ctc`` checkpoint (so
    output had no punctuation and the Formatter couldn't split sentences).

    The contract: ``Transcriber._resolve_model_name()`` must agree with
    ``config.gigaam_model`` (= ``v3_e2e_ctc``)."""
    from core import Transcriber, config

    # The dataclass default is the punctuated model.
    assert config.gigaam_model == "v3_e2e_ctc"

    # Reset any prior resolution.
    Transcriber._model_name = None
    assert Transcriber._resolve_model_name() == "v3_e2e_ctc"


def test_transcriber_resolve_model_honours_monkeypatched_config(monkeypatch) -> None:
    """If a developer overrides config.gigaam_model in a test, Transcriber
    follows. (Real production code should NEVER override this — Settings UI
    has no model picker — but the wiring must be honest.)"""
    from core import Transcriber, config

    Transcriber._model_name = None
    monkeypatch.setattr(config, "gigaam_model", "alt-model-id")
    assert Transcriber._resolve_model_name() == "alt-model-id"
    # Reset so other tests aren't affected.
    Transcriber._model_name = None


# ---- silero -----------------------------------------------------------


def test_tts_reuses_existing_project_copy(tmp_path, monkeypatch) -> None:
    from core import config
    from core.tts import TTSService

    silero_dir = tmp_path / "silero"
    silero_dir.mkdir()
    target = silero_dir / "v4_ru.pt"
    target.write_bytes(b"x" * 2_000_000)  # > 1 MB threshold

    monkeypatch.setattr(config, "data_dir", tmp_path)

    resolved = TTSService._ensure_model_downloaded()
    assert resolved == target


def test_tts_reuses_torch_hub_copy_when_ascii(tmp_path, monkeypatch) -> None:
    """If the project cache is empty but ``~/.cache/silero/v4_ru.pt`` exists
    AND the home path is ASCII, point at it directly — no copy, no download."""
    try:
        str(tmp_path).encode("ascii")
    except UnicodeEncodeError:
        pytest.skip("tmp_path itself is non-ASCII on this machine")

    from core import config
    from core.tts import TTSService

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")

    home = tmp_path / "ascii_home"  # all-ASCII
    silero_cache = home / ".cache" / "silero"
    silero_cache.mkdir(parents=True)
    cached = silero_cache / "v4_ru.pt"
    cached.write_bytes(b"y" * 2_000_000)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    resolved = TTSService._ensure_model_downloaded()
    assert resolved == cached
    # Project copy was NOT created — we just reused the cached one.
    assert not (tmp_path / "data" / "silero" / "v4_ru.pt").exists()


def test_tts_copies_when_home_path_has_non_ascii(tmp_path, monkeypatch) -> None:
    """Mirrors the real machine: ``C:\\Users\\Сергей\\.cache\\silero\\v4_ru.pt``.
    PackageImporter rejects non-ASCII paths, so we copy to the project dir."""
    from core import config
    from core.tts import TTSService

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")

    home = tmp_path / "Сергей_home"  # Cyrillic — non-ASCII
    silero_cache = home / ".cache" / "silero"
    silero_cache.mkdir(parents=True)
    cached = silero_cache / "v4_ru.pt"
    cached.write_bytes(b"z" * 2_000_000)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    resolved = TTSService._ensure_model_downloaded()
    project_target = tmp_path / "data" / "silero" / "v4_ru.pt"
    assert resolved == project_target
    assert project_target.exists()
    assert project_target.read_bytes() == cached.read_bytes()


def test_tts_downloads_when_nothing_cached(tmp_path, monkeypatch) -> None:
    from core import config
    from core.tts import TTSService

    monkeypatch.setattr(config, "data_dir", tmp_path / "data")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty_home"))

    download_calls = []

    def fake_urlretrieve(url, dest):
        download_calls.append((url, dest))
        Path(dest).write_bytes(b"q" * 2_000_000)

    monkeypatch.setattr("urllib.request.urlretrieve", fake_urlretrieve)

    resolved = TTSService._ensure_model_downloaded()
    expected = tmp_path / "data" / "silero" / "v4_ru.pt"
    assert resolved == expected
    assert len(download_calls) == 1
    assert download_calls[0][0] == TTSService._MODEL_URL


def test_is_ascii_path_helper() -> None:
    from core.tts import _is_ascii_path

    assert _is_ascii_path(Path("C:/foo/bar.pt")) is True
    assert _is_ascii_path(Path("C:/Users/Сергей/file.pt")) is False
