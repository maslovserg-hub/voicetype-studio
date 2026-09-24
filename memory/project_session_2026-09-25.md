---
name: VoiceType Studio session state — 2026-09-25
description: v1.0.6 — кнопка «⏹ Стоп» в карточке задачи транскриптора.
type: project
---
## Что сделано
- **⏹ Стоп** в карточке задачи (`MessageWidget(on_stop=...)`, `set_stopping`, `mark_stopped`). Окно хранит `task.future` от `run_coroutine_threadsafe` и зовёт `future.cancel()`; `_run_task`/`_run_download` ловят `CancelledError` → событие `("stopped", id)` → re-raise; `finally` чистит temp.
- Прогресс от потока yt-dlp после стопа игнорируется (`task.status == "stopped"`).

## Ограничения (сказано пользователю)
- SpeechKit доделывает уже отправленный файл и, вероятно, берёт деньги.
- GigaAM дорабатывает текущий кусок; yt-dlp и ffmpeg идут в фоне до конца, закачка остаётся в `data/tmp`. Чтобы обрывать yt-dlp, нужно чтобы `_yt_progress_hook` в `core/downloader.py` не глушил исключение из колбэка — не делали.

## Релиз
[v1.0.6](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.6): zip 223 588 732 б (плоский), Setup.exe и Update.bat из `dist_release/`. `releases/latest` → v1.0.6 проверено. Локально поставлено копированием `dist/VoiceTypeStudio/.` в `%APPDATA%\VoiceTypeStudio` после `taskkill /F`; программа запущена. Кнопку пользователь вживую ещё не проверял.

## Грабли
- Пользователь пишет по-русски — отвечать ТОЛЬКО по-русски (ответил по-английски — был скандал).
- Tk в тестах не стартует: init.tcl по пути с «Сергей» не читается, и `TCL_LIBRARY` с 8.3-путём (`D899~1`) не помогает. Тесты с `ctk_root` скипаются.
