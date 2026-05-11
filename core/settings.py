"""User preferences persisted as JSON.

Structure is shared between the desktop window and the Telegram bot — both
read the same file. The settings UI in :mod:`desktop.settings_window` writes
it; everyone else only reads.

Defaults are conservative: bot disabled, no API keys, Perplexity as the
preferred LLM. The file lives in :attr:`core.config.config.data_dir` so
tests can redirect it via ``monkeypatch.setattr(config, "data_dir", ...)``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .config import config

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """Application-wide user preferences."""

    default_provider: str = "perplexity"
    favorites: list[str] = field(
        default_factory=lambda: ["openai", "anthropic"]
    )
    api_keys: dict[str, str] = field(default_factory=dict)
    tts_speaker: str = "eugene"
    bot_enabled: bool = False
    bot_token: str = ""
    whitelist_ids: list[int] = field(default_factory=list)
    # Path to a Netscape-format cookies.txt for YouTube (and other gated
    # sources). Optional — leave empty for browser auto-detect. Set this if
    # you're on Yandex Browser (yt-dlp doesn't support it directly) or your
    # Chrome version is bitten by yt-dlp issue #10927.
    youtube_cookies_file: str = ""

    def api_key_for(self, provider: str) -> str:
        return (self.api_keys.get(provider) or "").strip()

    def has_api_key(self, provider: str) -> bool:
        return bool(self.api_key_for(provider))


def _default_path() -> Path:
    return config.data_dir / "settings.json"


def load(path: Optional[Path] = None) -> Settings:
    """Load settings from JSON, returning defaults if the file is missing or
    unreadable. Unknown fields are dropped so old files don't crash a newer
    build."""
    target = path or _default_path()
    if not target.exists():
        return Settings()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read %s; using defaults", target)
        return Settings()

    fields = {f.name for f in Settings.__dataclass_fields__.values()}
    clean = {k: v for k, v in raw.items() if k in fields}
    try:
        return Settings(**clean)
    except TypeError:
        logger.exception("Settings file %s has bad shape; using defaults", target)
        return Settings()


def save(settings: Settings, path: Optional[Path] = None) -> None:
    target = path or _default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False)
    target.write_text(payload, encoding="utf-8")
    logger.info("Settings saved to %s", target)
