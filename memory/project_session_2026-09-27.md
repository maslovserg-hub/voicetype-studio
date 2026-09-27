---
name: VoiceType Studio session state — 2026-09-27
description: v1.0.8 — упрощение (без бота, озвучки и cookies.txt), клик по трею, починка иконки на панели задач.
type: project
---
## Что сделано (v1.0.8)
- **Telegram-бот удалён целиком**: `bot/`, блок в Настройках, `_start_bot/_stop_bot` в main.py, `aiogram`/`pydantic` из spec и requirements, `assets/bot.png`. Поля `bot_enabled/bot_token/whitelist_ids` в старом settings.json игнорируются (`settings.load` отбрасывает неизвестные ключи). История: scope только `("desktop",)`, `owner_scope/is_owner` удалены; старые строки бота лежат в history.db, но не показываются.
- **Озвучка удалена**: кнопка в окне и в боте, раздел Настроек, `core/tts.py`, `config.silero_dir`, `Settings.tts_speaker`.
- **YouTube cookies в Настройках удалены** вместе с `youtube_cookies_file` и `Downloader.set_cookies_file`. Пользователь не понял, какой файл выбирать («одна кнопка максимум, или убрать»). Автоматическое чтение из браузеров (`cookies_extractor`) осталось.
- **Клик ЛКМ по трею** → Транскриптор (`default=True` у пункта меню pystray).
- `apply_app_icon` в `desktop/_icons.py`: CTkToplevel через 200 мс ставит свою .ico — через 250 мс ставим свою обратно.
- `asyncio`-петля в main.py по-прежнему называется `bot_loop` — ей пользуются окна, не переименовывали.

## Иконка на панели задач — белый листок
- НЕ кэш иконок и НЕ код окон (проверено: WM_GETICON окна и иконки exe во всех размерах — робот).
- Причина: ярлык `%APPDATA%\Microsoft\Windows\Start Menu\Programs\VoiceType Studio.lnk` хранил IconLocation по 8.3-пути `C:\Users\D899~1\AppData\Roaming\VOICET~1\VOICET~1.EXE`, которого больше нет. Windows берёт иконку запущенной программы из её ярлыка.
- Починено: переписан IconLocation на длинный путь (IShellLinkW). В launcher.py `create_start_menu_shortcut` теперь пишет `win32api.GetLongPathName(EXE_PATH)`. Существующие битые ярлыки у других не чинятся (shortcut_exists() → return True).
- PowerShell `WScript.Shell` читает этот .lnk как пустой из-за кириллицы — смотреть через pythoncom/IShellLinkW.

## Хвосты
- Предложено удалить `C:\VoiceTypeStudio\data\silero\`, `data\tts\` и пустые `bot\__pycache__` в репо — пользователь не ответил.
- Tk-тесты окна настроек (новые) в этом окружении скипаются (init.tcl по кириллическому пути).

## Релиз
[v1.0.8](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.8): zip 219 670 247 б (плоский, без aiogram), новый Setup.exe (фикс ярлыка), Update.bat. `releases/latest` → v1.0.8 проверено. Локально установлен (robocopy /MIR `_internal` + exe) и запущен; иконку на панели задач пользователь подтвердил.

## Архив бота
По просьбе пользователя бот сохранён в `archive/bot-v1.0.7/` (bot/, тесты, bot.png, README с тем, что ещё подключить обратно). `pyproject.toml`: `testpaths = ["tests"]`, чтобы pytest не собирал архивные тесты.
