"""Settings dataclass + JSON load/save roundtrip."""

from __future__ import annotations

import json

from core import Settings, config, settings_io


def test_defaults_sensible() -> None:
    s = Settings()
    assert s.default_provider == "perplexity"
    assert "openai" in s.favorites
    assert s.bot_enabled is False
    assert s.api_keys == {}


def test_load_missing_returns_defaults(tmp_path) -> None:
    s = settings_io.load(tmp_path / "nope.json")
    assert s == Settings()


def test_save_then_load_roundtrip(tmp_path) -> None:
    target = tmp_path / "settings.json"
    original = Settings(
        default_provider="openai",
        favorites=["anthropic", "gemini"],
        api_keys={"openai": "sk-test", "anthropic": "ant-test"},
        tts_speaker="aidar",
        bot_enabled=True,
        bot_token="123:abc",
        whitelist_ids=[42, 100],
    )
    settings_io.save(original, target)

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["default_provider"] == "openai"
    assert raw["api_keys"]["openai"] == "sk-test"

    restored = settings_io.load(target)
    assert restored == original


def test_unknown_field_in_file_does_not_crash(tmp_path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(
        json.dumps({"default_provider": "openai", "future_flag": "ignored"}),
        encoding="utf-8",
    )
    s = settings_io.load(target)
    assert s.default_provider == "openai"
    # Other fields fall back to defaults.
    assert s.bot_enabled is False


def test_corrupt_json_returns_defaults(tmp_path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("not json at all { [ ", encoding="utf-8")
    assert settings_io.load(target) == Settings()


def test_default_path_uses_data_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "data_dir", tmp_path)
    s = Settings(default_provider="anthropic")
    settings_io.save(s)  # no path → default
    expected = tmp_path / "settings.json"
    assert expected.exists()
    assert settings_io.load() == s


def test_api_key_helpers() -> None:
    s = Settings(api_keys={"openai": "  sk-xyz  ", "perplexity": ""})
    assert s.api_key_for("openai") == "sk-xyz"
    assert s.api_key_for("perplexity") == ""
    assert s.api_key_for("gemini") == ""
    assert s.has_api_key("openai") is True
    assert s.has_api_key("perplexity") is False
    assert s.has_api_key("gemini") is False
