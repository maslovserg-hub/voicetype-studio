"""Project-wide configuration.

In V1 most settings come from ``settings.json`` (which is written by
``desktop/settings_window``). Heavy paths (``data_dir``, ``gigaam_cache_dir``)
can also be overridden via environment variables, mostly to make tests
runnable from a temp dir.

The GigaAM cache directory has one extra override path on top of the env
var: a ``gigaam_cache_path.txt`` file dropped next to the launcher-installed
``VoiceTypeStudio.exe``. The launcher writes it when the user points to a
pre-existing model folder, so the app picks it up without copying 440 МБ
or asking again on every launch.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_GIGAAM_POINTER_FILE = "gigaam_cache_path.txt"


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    return Path(raw) if raw else default


def _read_gigaam_pointer() -> Path | None:
    """Return the path stored in the launcher-written pointer file, or
    ``None`` if no usable pointer is present.

    Looked up next to the running executable (``sys.executable`` when
    frozen) and next to this module otherwise — that second branch lets
    dev runs from source pick up a pointer without bundling.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / _GIGAAM_POINTER_FILE)
    candidates.append(Path(__file__).resolve().parent.parent / _GIGAAM_POINTER_FILE)

    for c in candidates:
        if not c.is_file():
            continue
        try:
            raw = c.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw and Path(raw).is_dir():
            return Path(raw)
    return None


def _resolve_gigaam_cache() -> Path:
    raw_env = os.getenv("GIGAAM_CACHE_DIR", "").strip()
    if raw_env:
        return Path(raw_env)
    pointer = _read_gigaam_pointer()
    if pointer is not None:
        return pointer
    return Path("C:/gigaam_cache")


@dataclass
class AppConfig:
    # Persistent state
    data_dir: Path = field(
        default_factory=lambda: _env_path("DATA_DIR", Path("C:/VoiceTypeStudio/data"))
    )
    gigaam_cache_dir: Path = field(
        default_factory=_resolve_gigaam_cache
    )

    # Hard-coded model. Settings UI does NOT expose this — all users use the
    # punctuated v3_e2e_ctc to keep saved transcripts comparable.
    gigaam_model: str = "v3_e2e_ctc"

    # Limits (for the transcriptor window)
    max_duration_hours: int = 4
    max_file_size_mb: int = 2048

    # Timeouts
    download_timeout_s: int = 3600
    processing_timeout_s: int = 21600

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def history_db(self) -> Path:
        return self.data_dir / "history.db"

    @property
    def silero_dir(self) -> Path:
        return self.data_dir / "silero"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.temp_dir, self.silero_dir, self.gigaam_cache_dir):
            p.mkdir(parents=True, exist_ok=True)


# Singleton — imported throughout core/
config = AppConfig()
