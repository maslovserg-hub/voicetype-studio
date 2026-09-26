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

## v1.0.7 (25–26.09) — «Стоп» обрывает всё
- yt-dlp: `threading.Event` + хук бросает `_DownloadAborted(BaseException)` — BaseException, иначе ретраи в `_download` (`except Exception`) начинают следующую попытку. Поток сам чистит `.part`/`-Frag`/готовый файл (`_remove_partials`, по префиксу имени).
- ffmpeg убивается при `CancelledError` (converter, speechkit). SpeechKit: `POST operations/{id}:cancel` best effort — поддержку Яндексом не проверяли.
- GigaAM: кусок/нарезку не прервать; уборка — `asr_executor.submit(...)` (один воркер → сразу после текущей работы).
- [v1.0.7](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.7) опубликован (zip 223 589 815 б), `releases/latest` → v1.0.7; локально установлен и запущен.

## Грабли тестов
- `test_main_smoke` делает `importlib.reload(main)` → подмена `Popen.__init__` в main.py вызывала сама себя (RecursionError во всех следующих тестах с подпроцессами). Сделана идемпотентной (флаг `_no_window`).
- В тестах не запускать `sys.executable` как «долгий процесс»: venv python.exe — лаунчер с дочерним python, после kill дочерний держит пайпы. Брать `C:\Windows\System32\ping.exe`.
- Прогон, показавший 70 минут, — сон компьютера, не тест. Полный набор ~4 мин (`test_bot_stub_lifecycle` ~87 с).
- Tk-тест кнопки в полном прогоне иногда проходит, иногда скипается (init.tcl по кириллическому пути).
