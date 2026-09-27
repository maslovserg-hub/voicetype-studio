# Telegram-бот (архив)

Бот убран из программы в v1.0.8 — им никто не пользовался. Здесь его код в том виде, как он работал в v1.0.7: папка `bot/`, картинка для `/start` и тесты.

Программа эти файлы не использует и в сборку не берёт.

## Как вернуть

Проще всего — взять всё из git. Коммит `464a10e` — последний с рабочим ботом (код бота тот же, что в теге `v1.0.7`), коммит `a4a149f` — его удаление.

```
git show a4a149f --stat     # какие файлы менялись при удалении
```

Кроме этой папки, бот подключался в нескольких местах — их надо вернуть из `464a10e`:

- `main.py` — `from bot.main import ...`, запуск при старте, `_start_bot` / `_stop_bot`, перезапуск при смене токена в `_on_settings_saved`, остановка в `shutdown`.
- `core/settings.py` — поля `bot_enabled`, `bot_token`, `whitelist_ids`.
- `core/history.py` — `owner_scope`, `is_owner`; `desktop/history_panel.py` — параметр `get_settings` и `owner_scope` вместо `("desktop",)`.
- `desktop/settings_window.py` — блок «Telegram-бот», `validate_telegram_token`, `parse_whitelist_ids`.
- `core/assets.py` — `bot_welcome_photo_path`.
- `VoiceTypeStudio.spec` — `assets/bot.png`, `_collect('aiogram')`, `_collect('pydantic')`, скрытые импорты `aiogram.*` и `magic_filter`.
- `requirements.txt` — `aiogram>=3.4`, `python-dotenv>=1.0`.

Кнопка «🔊 Озвучка» в `bot/handlers/_formats.py` зовёт `core.TTSService`, а озвучка тоже удалена. Либо убрать кнопку (`"tts"` из `FORMATS` и `_EXTRA_ROW`, `_deliver_tts`), либо вернуть `core/tts.py` из `464a10e`.
