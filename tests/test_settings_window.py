"""Tests for ``desktop.settings_window`` — pure helpers + window smoke.

The Tk widget code is not exercised here. We rely on the form helpers being
extracted to module-level so each one is a one-liner to test.
"""

from __future__ import annotations

import pytest

from desktop.settings_window import (
    PROVIDER_DISPLAY,
    PROVIDER_KEYS,
    display_to_provider_key,
    provider_key_to_display,
)


# --- structural ---------------------------------------------------------


def test_known_providers_match_llm_module() -> None:
    """Settings UI options must stay in sync with ``core.llm`` registry."""
    from core.llm.base import KNOWN_PROVIDERS

    # Allow the test fake-provider — strip any non-built-in keys.
    real = {k for k in KNOWN_PROVIDERS if k in {"perplexity", "openai", "anthropic", "gemini"}}
    assert set(PROVIDER_KEYS) == real


# --- provider display ↔ key conversion ----------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("OpenAI", "openai"),
        ("openai", "openai"),
        ("  Anthropic  ", "anthropic"),
        ("PERPLEXITY", "perplexity"),
        ("Gemini", "gemini"),
    ],
)
def test_display_to_provider_key(given: str, expected: str) -> None:
    assert display_to_provider_key(given) == expected


def test_display_to_provider_key_passes_unknown_through() -> None:
    """Unknown values are lower-cased but otherwise preserved so user data
    isn't silently lost on save/reopen."""
    assert display_to_provider_key("Llama-Local") == "llama-local"


def test_provider_key_to_display_known() -> None:
    for key, label in PROVIDER_DISPLAY.items():
        assert provider_key_to_display(key) == label


def test_provider_key_to_display_unknown() -> None:
    assert provider_key_to_display("ollama") == "ollama"


# --- window construction (needs a real Tk root) --------------------------


@pytest.fixture
def ctk_root():
    """Hidden CTk root, torn down after the test. Skips if Tk can't be
    constructed in this environment (headless CI, no display driver)."""
    try:
        import customtkinter as ctk
    except Exception as e:  # pragma: no cover
        pytest.skip(f"customtkinter unavailable: {e}")
    try:
        root = ctk.CTk()
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Cannot create Tk root in this env: {e}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def _all_widget_texts(widget) -> list[str]:
    """Recursively collect every ``cget("text")`` in the widget tree — used
    to assert a section header / label is present or absent."""
    out: list[str] = []
    try:
        out.append(widget.cget("text"))
    except Exception:
        pass
    for child in widget.winfo_children():
        out.extend(_all_widget_texts(child))
    return out


def _make_settings(**overrides):
    from core import Settings

    return Settings(**overrides)


def test_settings_window_has_no_update_section(ctk_root) -> None:
    """The «Обновление» section moved to the About window entirely."""
    from desktop.settings_window import SettingsWindow

    win = SettingsWindow(
        ctk_root, settings=_make_settings(), on_save=lambda s: None,
    )
    texts = _all_widget_texts(win)
    assert "Обновление" not in texts
    assert not hasattr(win, "_build_update_section")
    assert "Обслуживание" in texts


def test_settings_window_maintenance_buttons_trigger_callbacks(ctk_root) -> None:
    """«Открыть папку с данными» / «Очистить временные файлы» fire their
    callbacks immediately — no Save/Cancel round-trip, no Save/Cancel round-trip."""
    from desktop.settings_window import SettingsWindow

    opened = []
    cleaned = []

    win = SettingsWindow(
        ctk_root,
        settings=_make_settings(),
        on_save=lambda s: None,
        on_open_data_folder=lambda: opened.append(True),
        on_clean_temp=lambda: cleaned.append(True),
    )

    win._on_open_data_folder_clicked()
    assert opened == [True]

    win._on_clean_temp_clicked()
    assert cleaned == [True]
    assert "удалены" in win._maintenance_status.cget("text").lower()


def test_settings_window_maintenance_clean_temp_reports_error(ctk_root) -> None:
    from desktop.settings_window import SettingsWindow

    def _boom():
        raise RuntimeError("disk locked")

    win = SettingsWindow(
        ctk_root,
        settings=_make_settings(),
        on_save=lambda s: None,
        on_clean_temp=_boom,
    )
    win._on_clean_temp_clicked()
    status = win._maintenance_status.cget("text")
    assert "не удалось" in status.lower()
    assert "disk locked" in status


def test_settings_window_has_no_telegram_section(ctk_root) -> None:
    from desktop.settings_window import SettingsWindow

    win = SettingsWindow(
        ctk_root, settings=_make_settings(), on_save=lambda s: None,
    )
    texts = _all_widget_texts(win)
    assert "Telegram-бот" not in texts
    assert "YouTube" not in texts
    assert not any("Озвучка" in t for t in texts if isinstance(t, str))
