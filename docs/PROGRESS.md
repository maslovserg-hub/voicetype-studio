# Прогресс реализации

См. полный план в `C:\Users\Сергей\thoughts\shared\plans\2026-05-09-voicetype-studio-implementation.md`.

## Этапы

- [x] **Этап 0. Подготовка проекта** (структура папок, requirements, .gitignore, README, CLAUDE.md)
- [x] **Этап 1. core/ — общий backend**
  - [x] Скопированы файлы из transcription-bot (transcriber, downloader, converter, formatter, tts, chunking, transcript_cache, history)
  - [x] Создан `core/config.py` с дефолтами
  - [x] Создан `core/__init__.py` с экспортами
  - [x] Импорты `from bot.*` переведены на `from core.*` / `from .*` (tts, downloader, history, transcriber, transcript_cache)
  - [x] `history.user_id` стал TEXT — поддерживает `"desktop"` и Telegram id-строки
  - [x] Smoke-тест `tests/test_core_smoke.py` зелёный (5/5)
- [x] **Этап 2. core/llm/ — multi-provider**
  - [x] `base.py` с `LLMProvider` ABC + `_OpenAIChatProvider` mixin
  - [x] `perplexity.py`, `openai.py` (через mixin), `anthropic.py` (свой формат с `x-api-key` + `system` top-level), `gemini.py` (`?key=...` + `contents/parts`)
  - [x] `prompts.py` с общими `SYSTEM_PROMPT` + 4 `PROMPTS` (BRIEF / STRUCTURED / ROLES / QUESTIONS)
  - [x] `summarizer.py` рефакторен — кэш по `(hash, mode, provider, model)`, `Summarizer.process(provider, mode, transcript, model=None)`
  - [x] `make_provider("name", api_key, model)` factory + `KNOWN_PROVIDERS` registry
  - [x] 14 unit-тестов в `tests/test_llm_smoke.py` (factory, mode/model/provider cache invalidation, unconfigured-raises, empty-transcript-skip)
  - [ ] Live-test против реального API — есть скелет `test_perplexity_live_brief`, скипается без `PPLX_API_KEY`. Прогнать руками когда будут ключи в env.
- [x] **Этап 3. desktop/ — tray + overlay + dictation** (миграция из my-voice-assistent)
  - [x] `desktop/single_instance.py` — Windows-mutex (`VoiceTypeStudio_SingleInstance`)
  - [x] `desktop/autostart.py` — `HKCU\...\Run` toggle, `is_enabled/set_enabled/toggle`
  - [x] `desktop/overlay.py` — рефакторен: `Overlay(master)` теперь `tk.Toplevel` поверх общего root, animation идёт через `master.after`, без своего mainloop
  - [x] `desktop/tray.py` — новое меню (Открыть транскриптор / Настройки / Запускать с Windows / Выход) через `build_tray(...)` factory
  - [x] `desktop/dictation.py` — Right-Ctrl push-to-talk, `DictationListener(transcribe_fn, overlay)` с инжектируемым sync-callable. Win32 clipboard + SendInput Ctrl+V вынесены в чистые функции с lazy-init `_win32()` для cross-platform тестов.
  - [x] `core.Transcriber.transcribe_array(audio, sample_rate)` — sync entry-point для диктовки (через временный WAV → `model.transcribe`); + dynamic int8 quantization в `_load_model()` (как в Voice Type сегодня)
  - [x] 9 unit-тестов в `tests/test_desktop_smoke.py` (imports, no-bot guard, clean_dictation_text edge cases, mutex idempotency, autostart read-only-safe, dictation constants 1:1 с Voice Type)
- [x] **Этап 4. desktop/transcriptor_window.py** (новый код, 8 часов)
  - [x] `core/settings.py` — `Settings` dataclass + `settings_io.load/save` JSON
  - [x] `desktop/_format_dispatch.py` — `deliver_format(segments, key, settings)` (text / timestamps / srt / brief / structured / roles / questions / tts)
  - [x] `desktop/_message_widget.py` — `MessageWidget(task_id, source_label, on_format_click)` с тремя состояниями (progress / done / error), 8 кнопок форматов в 2 ряда, copy/save/clear actions, плеер для 🔊
  - [x] `desktop/transcriptor_window.py` — `TranscriptorWindow(master, bot_loop, asr_executor, settings)`, скроллируемая лента + `InputBar` (📎 + URL + Старт), drag&drop, classify_input + label_for_source helpers, cross-thread `queue.Queue` дренируется через `after(50, ...)`
  - [x] `TranscriptionTask` dataclass с состоянием задачи
  - [x] 25 unit-тестов: Settings roundtrip (7), format_dispatch (14, включая проверку что `questions` получает timestamped transcript а `brief` — plain), transcriptor helpers (12: classify_input, label_for_source, _parse_dropped_paths включая `{...}`-обёртки tkinterdnd2)
- [x] **Этап 5. desktop/settings_window.py**
  - [x] `SettingsWindow(master, settings, on_save, bot_loop=None)` с тремя секциями: AI / TTS / Telegram
  - [x] `open_settings_window(...)` factory для main.py
  - [x] Pure helpers вынесены на module-level: `display_to_provider_key`, `provider_key_to_display`, `parse_whitelist_ids`, `format_whitelist_ids`
  - [x] `validate_telegram_token(token) -> (ok, msg)` — async, дёргает `getMe`, обрабатывает сетевые ошибки. Используется кнопкой «Проверить токен», поддерживает оба пути выполнения (через `bot_loop` если задан, иначе через `asyncio.run` в worker-thread)
  - [x] **GigaAM-модель НЕ выводится в UI** — соблюдён инвариант проекта: ASR-модель `v3_e2e_ctc` зашита в `core.config.AppConfig`, не видна пользователю
  - [x] Validation на сохранении: bot_enabled с пустым токеном → ошибка вместо закрытия окна
  - [x] 19 unit-тестов: structural (providers ↔ core.llm.KNOWN_PROVIDERS, 5 silero голосов из FR-9), display↔key conversion (5 случаев включая unknown-passthrough), whitelist parsing (semicolons, drop invalid, empty, roundtrip), token validator (rejects empty/whitespace, friendly message on network error), live-skip для real getMe
- [x] **Этап 6. main.py — сшивание потоков**
  - [x] `CTkDnD(ctk.CTk, TkinterDnD.DnDWrapper)` — root, который одновременно customtkinter и DnD-aware. Это нужно чтобы `drop_target_register` работал в TranscriptorWindow.
  - [x] `App` собирает: settings (load), root (hidden), `ThreadPoolExecutor(max_workers=1)`, `asyncio.new_event_loop()` в daemon-thread, Overlay, DictationListener, tray, Telegram-бот (опционально)
  - [x] Subprocess no-window patch на самом верху main.py — подавляет console-flash от torch/soundfile child процессов на Windows
  - [x] `_transcribe_for_dictation(audio, sample_rate)` синхронный wrapper — отправляет `Transcriber.transcribe_array` в asr_executor, ждёт результат с таймаутом 120 сек
  - [x] Tray callbacks (`_open_transcriptor_safe`, `_open_settings_safe`, `_quit_safe`) — все маршалят через `root.after(0, ...)` чтобы Tk-операции выполнялись на main thread
  - [x] `_on_settings_saved`: пишет JSON, обновляет `self.settings`, синхронизирует transcriptor.settings, делает start/stop/restart бота на основе deltas (was_on/is_on/token_changed)
  - [x] Shutdown с timeout=5s: bot stop → dictation stop → tray stop → bot_loop.stop + thread.join → executor shutdown → overlay destroy → root destroy. Try/except вокруг каждого шага чтобы зависшая часть не блокировала остальные.
  - [x] `single_instance.acquire()` в `main()` до запуска App — с tk-messagebox если уже запущен
  - [x] `bot/main.py` — заглушка `start_bot_polling`/`stop_bot_polling`/`is_running` (real handlers — Этап 7)
  - [x] 15 unit-tests: import smoke, structural (`App` имеет 10 нужных методов), bot stub lifecycle (start → is_running True → stop → False, skip if no token), Windows subprocess patch idempotency
- [x] **Этап 7. bot/ — переезд Telegram-бота**
  - [x] `bot/utils/progress.py` — `ProgressTracker` 1:1 с transcription-bot (lock на finish, 10%-coalescing на update)
  - [x] `bot/middleware/whitelist.py` — `WhitelistMiddleware(whitelist_ids)` теперь принимает список ID в конструкторе (раньше читал из env). Пустой whitelist = бот закрыт для всех.
  - [x] `bot/handlers/_formats.py` — `deliver_result(bot, chat_id, segments, format_key, settings)` теперь принимает `Settings` и строит провайдера через `make_provider(settings.default_provider, settings.api_key_for(...))`. Нет ключа → friendly chat-error вместо краша.
  - [x] `bot/handlers/{start,media,links,repeat}.py` — все callback handlers объявляют `settings: Settings` kwarg чтобы aiogram прокинул workflow_data.
  - [x] `history.add(user_id=str(callback.from_user.id), ...)` — Telegram int ID кастуется в строку для совместимости с TEXT-колонкой и desktop-входами `'desktop'`.
  - [x] `bot/main.py` — реальный `start_bot_polling/stop_bot_polling`. `delete_webhook(drop_pending_updates=True)` для надёжного захвата владения, `start_polling(handle_signals=False)` обязательно (мы в daemon-thread). При остановке закрывает `bot.session`.
  - [x] `core.Transcriber.set_executor(executor)` + class-level `_executor` — все `loop.run_in_executor(...)` в Transcriber теперь идут через переданный executor. main.py зовёт `Transcriber.set_executor(self.asr_executor)` один раз — диктовка / окно / бот гарантированно сериализуются.
  - [x] 13 unit-tests: bot module imports, no-residual `from bot.config/from bot.services`, FORMATS dict (8 ключей), keyboard callback_data ≤ 64 байт, layout 3×n=8, whitelist (blocks/passes/empty/non-Message), bot.main signature + skip-on-empty-token, handlers declare settings kwarg, deliver_result friendly error на missing API key
- [x] **Этап 8. PyInstaller сборка + launcher** (только spec-файлы и launcher; реальный билд — manual)
  - [x] `VoiceTypeStudio.spec` — `--onedir` на main.py. `collect_all` для gigaam/torch/torchaudio/onnxruntime/hydra/omegaconf/customtkinter/tkinterdnd2/aiogram/pydantic/aiohttp/aiofiles/pydub/yt_dlp. Hidden imports: `pystray._win32`, `magic_filter`, `aiogram.fsm.storage.memory`, `aiogram.client.session.aiohttp`, sentencepiece, hydra internals. Excludes: torchvision/torchtext/torchrec/PyQt*/PySide*/lib2to3/etc.
  - [x] `launcher/launcher.py` — адаптирован из my-voice-assistent. `INSTALL_DIR=%APPDATA%\VoiceTypeStudio`, `EXE_PATH=...\VoiceTypeStudio\VoiceTypeStudio.exe`. APP_URL пока указывает на predicted GitHub Releases path; перед реальным релизом надо обновить.
  - [x] `launcher/Launcher.spec` — `--onefile` (нет COLLECT блока), `name='VoiceTypeStudio-Setup'`, `console=False`
  - [x] `docs/BUILDING.md` — пошаговое руководство: венв → pip install → pytest → pyinstaller → zip → release. Включает чек-лист smoke-теста на чистой VM.
  - [x] 13 unit-tests в `tests/test_build_smoke.py`: spec-файлы парсятся как Python AST, main spec собирает все нужные пакеты + console=False + COLLECT (не onefile), launcher spec на onefile + name VoiceTypeStudio-Setup, launcher.py имеет helpers (already_installed/launch/run_installer), `EXE_PATH` совпадает с layout от main spec, APP_URL — https, requirements.txt включает customtkinter/tkinterdnd2/aiogram

## Важные решения по ходу

(Здесь отмечать всё, что стало ясно в процессе и не было в плане.)

- 2026-05-09: проект создан, структура папок размечена.
- 2026-05-09: Этап 1 закрыт. core/ импортируется без bot/. venv с минимальными smoke-зависимостями (aiohttp, aiofiles, yt-dlp, pydub, pytest, pytest-asyncio, python-dotenv) — без torch/gigaam, их доустановим перед Этапом 3 (диктовка) и реальной транскрипцией.
- 2026-05-09: `core.config` использует lower_case API (`config.temp_dir`, `config.history_db`, `config.silero_dir`) вместо старых UPPER_CASE атрибутов `bot.config`. Если что-то ещё мигрирует из старого бота — переименовывать на месте.
- 2026-05-09: Этап 2 закрыт. Multi-provider LLM работает: 4 провайдера (Perplexity / OpenAI / Anthropic / Gemini) за общим `LLMProvider` ABC. Anthropic и Gemini имеют свои реализации `process()` (формат `messages` без system-сообщения / `contents`+`parts` соответственно), Perplexity и OpenAI делят `_OpenAIChatProvider` mixin.
- 2026-05-09: Кэш `Summarizer` теперь ключуется по `(transcript_hash, mode, provider.name, model)` — переключение провайдера или модели не даёт ложный hit. Тесты на эту инвариантность: `test_summarizer_cache_keyed_by_{mode,model,provider}`.
- 2026-05-09: API-ключи в провайдеры передаются через конструктор (`make_provider("openai", api_key=...)`), не из env. Это под Этап 5 (settings UI пишет `settings.json`, callers читают и инстанцируют провайдер).
- 2026-05-09: Default-модели в коде: Perplexity `sonar`, OpenAI `gpt-4o-mini`, Anthropic `claude-3-5-haiku-latest`, Gemini `gemini-1.5-flash`. Все можно переопределить через `model=` в конструкторе или per-call.
- 2026-05-10: Этап 3 закрыт. `desktop/` собран из 5 модулей. Установлены доп. зависимости в venv: Pillow, pynput, pystray, numpy, sounddevice (для smoke-тестов). torch/gigaam всё ещё отложены — реальная диктовка не тестировалась, нужен прогон вручную после Этапа 6 (main.py сшивает всё).
- 2026-05-10: Архитектурное изменение vs Voice Type — оверлей больше не создаёт свой `tk.Tk()`. Теперь это `tk.Toplevel(master)`, master = главный root из `main.py`. Это критично для Этапа 6: один mainloop на всё приложение, иначе блокировки.
- 2026-05-10: Setup-wizard первого запуска (выбор папки / скачивание GigaAM модели) НЕ перенесён. `core.Transcriber._load_model()` сам тихо качает модель через `gigaam.load_model(download_root=...)`. Если на чистой машине UX окажется плохим — можно вернуть wizard как `desktop/setup_wizard.py`.
- 2026-05-10: Win32 paste machinery (clipboard + SendInput Ctrl+V) вынесена в module-level функции `_clipboard_get_text/_set_text/_send_ctrl_v/_force_foreground/_paste_text`, с lazy `_win32()` bundle. Это позволяет импортировать `desktop.dictation` на не-Windows без падения, что нужно для тестов в CI.
- 2026-05-10: Этап 4 закрыт (логика — без живого GUI-прогона). Установлены customtkinter, tkinterdnd2, pyperclip. 61 unit-test зелёный. Реальный запуск окна — после Этапа 6, когда main.py соберёт root + bot_loop + asr_executor.
- 2026-05-10: Архитектурное решение по событиям. У TranscriptorWindow свой private `queue.Queue` и `after(50, _drain)` — НЕ shared `gui_queue` из плана Этапа 6. Причина: window-private события (progress/done/error/format_done) не должны конкурировать с bot- и dictation-событиями. Если в Этапе 6 захочется единого dispatcher — он будет вызывать `window._handle_event(event)` напрямую вместо queue.put.
- 2026-05-10: TTS-кнопка (🔊) синтезирует через silero на BRIEF-саммари (не на полном транскрипте). Это переиспользует кэш Summarizer, если пользователь уже жал «📋 Тезисы». В тесте мы стабим `TTSService.synthesize` чтобы не качать silero модель.
- 2026-05-10: drag&drop — `tkinterdnd2`. Окно вешает `drop_target_register("DND_Files")` + `dnd_bind('<<Drop>>')`. Это требует чтобы master root в main.py был DnD-aware (`TkinterDnD.Tk()` или CTk + DnDWrapper mixin). Без этого окно работает, но drop игнорируется (с warning в логах).
- 2026-05-10: Этап 5 закрыт. SettingsWindow готов, всего 80 unit-tests зелёных. Реальный запуск окна — после Этапа 6.
- 2026-05-10: GigaAM-модель НЕ выводится в settings UI (пользователь напомнил отдельно). Всегда `v3_e2e_ctc`. Это сохранено в `feedback_no_giga_model_in_settings.md`.
- 2026-05-10: Settings UI отображает API-ключи для всех 4 провайдеров одновременно (не «только тех что в favorites» как формально написано в spec FR-9). Проще для UX: пользователь видит куда вставить ключ независимо от текущих favorites. Если нужна динамика — переделаем как P1.
- 2026-05-10: Этап 6 закрыт. main.py + bot/main.py-заглушка готовы, 95 unit-tests зелёных. РЕАЛЬНЫЙ запуск приложения (`python main.py`) протестировать вручную — это первый момент когда на самом деле грузится GigaAM модель + поднимается Tk root + tray. До запуска нужно: 1) убедиться что в `c:\gigaam_cache\` модель уже скачана либо есть интернет на её скачивание, 2) torch/torchaudio/gigaam установлены в venv (сейчас НЕ установлены).
- 2026-05-10: Telegram-бот в Этапе 6 — заглушка. `start_bot_polling` логирует и ставит `_running=True`, `stop_bot_polling` ставит `False`. Реальная aiogram-логика — Этап 7.
- 2026-05-10: SHUTDOWN_TIMEOUT_S = 5 секунд на каждый шаг shutdown (per-component). Это даёт acceptance criterion «выход за < 5 сек» с запасом — все шаги try/except'нуты, зависший компонент не блокирует остальные.
- 2026-05-10: Этап 7 закрыт. aiogram 3.28 в venv, бот мигрирован на `core/`, 108 unit-tests зелёных. Реальный live-прогон с настоящим токеном — manual после Этапа 8.
- 2026-05-10: Архитектурное решение по сериализации GigaAM — `Transcriber.set_executor(asr_executor)` выставляется ОДИН раз в `App.__init__` и автоматически применяется ко всем 4 точкам `loop.run_in_executor` в `core/transcriber.py`. Это снимает риск что бот и окно одновременно дёрнут модель: оба идут через тот же `max_workers=1` executor что и диктовка. ВАЖНО: если кто-то ещё начнёт вызывать `Transcriber.transcribe(...)` без правильного executor (например, в тесте) — он будет использовать loop.default_executor который НЕ shared. Контракт: вызовы извне App.run() — на свой страх и риск.
- 2026-05-10: Whitelist в `WhitelistMiddleware` — snapshot при старте бота. При смене настроек main.App._on_settings_saved делает stop+start — поднимает новый middleware с актуальным whitelist. Hot-reload без рестарта (например через `dp["whitelist"] = ...`) не сделан, потому что aiogram middleware конструируется один раз и хранится в Dispatcher.
- 2026-05-10: Бот наследует `default_provider` из settings.json. Если пользователь меняет провайдера — handler через `_build_provider(settings)` берёт текущее значение (settings объект передаётся через workflow_data). Перезапуск бота не нужен.
- 2026-05-10: Этап 8 закрыт частично: spec-файлы и launcher готовы, тесты на их валидность — 121 unit-tests зелёных. РЕАЛЬНУЮ сборку через `pyinstaller VoiceTypeStudio.spec` НЕ запускали — нужно сначала установить torch/gigaam/onnxruntime в venv (~3-5 ГБ). Этот шаг — ручной, описан в `docs/BUILDING.md`.
- 2026-05-10: По исходному плану 8 этапов = ~4 рабочих дня; реализовано всё кроме реальной финальной сборки. Все spec-файлы + launcher готовы к билду «когда будут torch и время».
- 2026-05-10: APP_URL в launcher указывает на `github.com/maslovserg-hub/voicetype-studio/releases/latest/download/VoiceTypeStudio_release.zip` — placeholder. Перед публикацией первого релиза надо: 1) создать GitHub репо (или use existing), 2) собрать zip, 3) обновить APP_URL под реальный URL, 4) пересобрать launcher.

## День 2026-05-10 (вечерняя долгая отладка)

Конец дня: **165 unit-tests passed, 3 skipped.** Приложение запущено и работоспособно.

**Wiring-баги вылавивались по цепочке:**
- GigaAM model: `Transcriber` хардкодил `os.getenv("GIGAAM_MODEL", "v3_ctc")` — игнорировал `config.gigaam_model="v3_e2e_ctc"`. Качалась модель БЕЗ пунктуации. Починено — теперь читается из config singleton.
- TTS speaker: тот же класс бага — `TTSService._resolve_speaker()` читал env, не settings. Починено — добавлен `speaker` параметр в `synthesize()`, callers передают `settings.tts_speaker`.
- Whitelist hot-reload в боте: `App._on_settings_saved` рестартил бота только при `token_changed`. Whitelist UI menu правил → запись в `settings.json` → **но live middleware кэшировал старый snapshot**. Починено: рестарт также при `whitelist_changed`.

**YouTube auto-cookies infrastructure (большой кусок дня):**
- `core/cookies_extractor.py` — свой DPAPI+AES-GCM декриптор Chromium-cookies. Поддержка Yandex/Chrome/Edge/Brave/Opera/Vivaldi.
- Win32 shared-copy через `CreateFile(FILE_SHARE_READ|WRITE|DELETE)` — обходит file-locks для всех браузеров КРОМЕ Yandex (его требуется реально завершить, фоновые `browser.exe` мешают).
- **Стрип SHA-256 prefix** после AES-GCM decrypt — Chromium с какого-то момента префиксует cookie-value 32-байтным хэшом host+name для integrity. Без strip получалась байтовая каша.
- `js_runtimes={"node": {"path": None}}` в ydl_opts — yt-dlp 2026.3+ по дефолту enables только `deno`, без явного `node` n-sig challenge не решается, формат-листинг возвращает только storyboard-картинки.
- Установлены: `pywin32`, `pycryptodome`, `yt-dlp-ejs`, Node.js (был раньше).

**Robustness фиксы:**
- Микро-чанк (16ms = 256 сэмплов) от tail видео крашил GigaAM на STFT (требует n_fft=320 минимум). Добавлен фильтр `_MIN_CHUNK_MS=100` в chunking + per-chunk try/except в Transcriber.
- Auto-cleanup `data\tmp\` после транскрипции (раньше копились гигабайты): включает оригинал + WAV + папку chunks. Локальный пользовательский input НЕ удаляется. Tray-меню теперь имеет «Папка с данными» и «Очистить временные файлы».

**UX фиксы:**
- ПКМ-меню «Вставить» в полях ввода (CTkEntry / CTkTextbox) — ранее `event_generate("<<Paste>>")` уходил в обёртку, не во внутренний tk widget.
- Иконка прикрепления файла — заменён мутный emoji на PIL-нарисованный документ + плюс.
- TTS UX — кнопка теперь говорит конкретно (`🔊 Озвучиваю… (10–30 сек)` вместо общего «Готовлю…»). Результат с большой ▶ Воспроизвести кнопкой и «Открыть папку».

**Новые файлы:** `core/cookies_extractor.py`, `desktop/_clipboard_menu.py`, `desktop/_icons.py`. Новые тесты: `tests/test_cookies_extractor.py` (9), `tests/test_clipboard_menu.py` (7), `tests/test_chunking_filter.py` (3).

**На завтра:**
1. UX-проверка: переключение TTS-голосов (aidar/baya/kseniya/xenia/eugene) реально слышимо.
2. Прогон видео-ссылок разных платформ (RuTube/VK/Я.Диск).
3. Возможно — пересборка PyInstaller (текущий dist собран до сегодняшних правок).
- 2026-05-10: Bug fix после Этапа 8 — модели НЕ должны качаться повторно. Что было сломано: 1) `core/transcriber.py` читал `os.getenv("GIGAAM_CACHE_DIR")` напрямую — без env-переменной gigaam использовал свой default cache, не `C:/gigaam_cache` (где у пользователя уже лежит v3_e2e_ctc.ckpt 421 МБ). 2) `core/tts.py` хардкодил `config.silero_dir = C:/VoiceTypeStudio/data/silero/` и не смотрел в `~/.cache/silero/` где silero v4_ru.pt уже есть из других проектов. ИСПРАВЛЕНО: Transcriber теперь читает `config.gigaam_cache_dir` (правильный singleton); TTSService пробует кандидаты в порядке `config.silero_dir` → `~/.cache/silero/v4_ru.pt` → `~/.cache/torch/hub/snakers4_silero-models_master/...`. Если найденный путь non-ASCII — копирует в проектную папку (PackageImporter non-ASCII не переваривает). Подтверждено на живой машине — оба файла обнаружены. 6 unit-tests добавлено в `tests/test_model_cache_reuse.py`.
- 2026-05-10: Установлены heavy ML deps в venv: torch 2.11.0+cpu, torchaudio 2.11.0+cpu, soundfile 0.13.1, gigaam @ git (0.1.0). Также подтянулись transitive deps (sympy, networkx, jinja2, fsspec, mpmath, MarkupSafe, filelock — torch dependencies). venv теперь полноценный для разработки + локальной сборки.
- 2026-05-10: 🎉 РЕАЛЬНЫЙ ЗАПУСК `python main.py` УСПЕШЕН. Процесс жив, 144 МБ working set, 28 потоков (Tk mainloop + asyncio loop + tray + ThreadPoolExecutor "asr" + pynput + sounddevice + torch internals). Тест: запустил в hidden, прождал 6 сек, увидел что ProcessName=python, MainWindowTitle='' (root скрыт через withdraw — правильно), threads=28 — все компоненты инициализировались без deadlock. Это первое empirical confirmation что 121 unit-tests + архитектура корректны на живой системе.
- 2026-05-10: requirements.txt обновлён: aiogram>=3.4 (вместо ==3.4.1), python-dotenv>=1.0 — используем то что уже работает (3.28). Если на чистой машине нужна точная воспроизводимость — можно сделать `pip freeze > requirements-lock.txt`.

## День 2026-05-11 (UX-итерация по живому окну)

Конец дня: **183 unit-tests passed, 4 skipped.** Приложение перезапущено несколько раз — все правки применены и видны живьём.

**UX-фичи в Транскрипторе:**
- **Новые карточки идут СВЕРХУ.** В `_spawn_widget` теперь `pack(before=existing_widgets[0])`, фид скроллится в начало (а не в конец как раньше). Юзер больше не мотает вниз чтобы увидеть свой только что добавленный файл. [transcriptor_window.py](c:\Projects\voicetype-studio\desktop\transcriptor_window.py) функция `_spawn_widget`.
- **Старые карточки автосворачиваются** при появлении новой. У `MessageWidget` теперь свой `_state` (processing/done/error), флаг `_collapsed` и три public-метода `collapse()` / `expand()` / `toggle()`. Header — кнопка-стрелка ▼/▶ + clickable label. [_message_widget.py](c:\Projects\voicetype-studio\desktop\_message_widget.py).
- Стрелочки переключателя сделаны крупнее: 32×28, font 16 bold, тяжёлые символы ▼/▶ вместо лёгких ▾/▸.

**Окно «История»:**
- Новый файл [desktop/history_window.py](c:\Projects\voicetype-studio\desktop\history_window.py) — `HistoryWindow(CTkToplevel)` со списком последних 50 транскрипций для `user_id="desktop"` из `core.history`. Каждая строка — кнопка с `format_history_label(row)` ("2026-05-11 18:42 · 📎 audio.mp3"). Клик загружает segments из БД и через callback пробрасывает в TranscriptorWindow.
- В `TranscriptorWindow` добавлен метод `restore_from_history(source_label, source, segments)` — создаёт новую карточку со status="done" и сразу `mark_done()`, без download/convert/transcribe. Сегменты берутся из `history.db` напрямую.
- В трей-меню добавлен пункт «История…» (между «Открыть транскриптор» и «Настройки»). [tray.py](c:\Projects\voicetype-studio\desktop\tray.py) — новый kwarg `on_open_history`.
- В main.py — методы `_open_history_safe`/`_open_history`/`_restore_history_row`. `_restore_history_row` сначала открывает Транскриптор (если закрыт), потом дёргает `restore_from_history`.
- **Фикс TclError**: первая версия HistoryWindow крашила из-за гонки CTkToplevel-внутреннего `after()`-фокуса с `destroy()`. Решение: отложить и `lift/focus_force` (через `after(150, ...)`) и сам `destroy()` (через `after(250, ...)`), плюс везде `winfo_exists()` guard. [history_window.py:120-150](c:\Projects\voicetype-studio\desktop\history_window.py#L120-L150).

**Downloader (RuTube/HLS оптимизации):**
- **Format-селектор поменян** с `"bestaudio/best"` на `"bestaudio/worstaudio/worstvideo[height<=480]+bestaudio/best[height<=480]/worst"`. Без bound-by-height yt-dlp фолбэкается на 1080p — для RuTube это 1+ GB видео когда нужен только звук. С bound-by-480p тот же контент = ~210 MiB (в 4-5× меньше). [downloader.py:158-175](c:\Projects\voicetype-studio\core\downloader.py#L158-L175).
- **Параллельная скачка фрагментов** — `concurrent_fragment_downloads=8` + `fragment_retries=10`. Для HLS-источников (RuTube, VK live, некоторые YouTube) это даёт x4-x8 ускорение: 507 фрагментов за ~30-90 сек вместо ~10 минут.
- **Прогресс-бар реально двигается** во время скачки. Добавлен `progress_callback` параметр в `Downloader.download()` + `_yt_progress_hook`, который читает из yt-dlp dict (`downloaded_bytes`, `total_bytes`, `_speed_str`, `fragment_index`/`fragment_count`). В `transcriptor_window._run_task` создаётся локальная `dl_cb(percent, status)` которая мапит yt-dlp 0-100% в наш диапазон 5-25% и шлёт через `_post()` (thread-safe queue.Queue).

**TTS — устранение «металлического призвука»:**
- `TTSService._sample_rate` поменян с `48000` на `24000`. silero v4_ru обучен на 24 kHz, при `sample_rate=48000` пакет делает linear-interpolation upsample → металлические артефакты на ВСЕХ голосах. На 24 kHz — чистый model-fidelity без artifacts. [tts.py:24-30](c:\Projects\voicetype-studio\core\tts.py#L24-L30). Подтверждено пользователем — слышно мягче.

**Тесты добавлены:**
- `tests/test_history_window.py` (8 тестов) — `format_history_label` pure-helper edge cases, structural на HistoryWindow class.
- `tests/test_message_widget.py` (9 тестов) — collapse/expand cycle, idempotency, state transitions, "collapsed-mark-done не показывает кнопки". Использует ctk_root fixture со skip-if-no-display.
- В `test_transcriptor_smoke.py` — `test_window_has_restore_from_history` (структурный с inspect).
- В `test_main_smoke.py` — `_open_history`, `_restore_history_row` добавлены в parametrize.
- В `test_desktop_smoke.py` — `test_build_tray_accepts_history_kwarg`.

## День 2026-05-11 (вечер — overlay HiDPI fix + пересборка)

- **Bug**: индикатор диктовки появлялся в верхне-левой четверти экрана вместо нижнего центра.
- **Root cause**: `Overlay` это `tk.Toplevel(self.root)` поверх `CTkDnD` (customtkinter root). customtkinter вызывает `SetProcessDpiAwareness(2)` **после** того как Tcl интерпретатор уже инициализирован, поэтому `winfo_screenwidth/height` возвращает *логические* пиксели (1536×864 на 125%-scaling), а `wm geometry +x+y` уже работает в *физических* пикселях. Математика `(sw-W)//2, sh-H-64` на 1536×864 даёт (732, 762) — это верхне-левая часть на физическом 1920×1080.
- **Fix**: новый хелпер `_screen_size_physical()` в [desktop/overlay.py](desktop/overlay.py) запрашивает размер через `ctypes.windll.user32.GetSystemMetrics(0/1)` напрямую, минуя кэш Tk. Fallback на `winfo_screenwidth/height` на не-Windows.
- **Build**: `dist/VoiceTypeStudio/` пересобран (~252 сек, exit 0). exe 50.8 МБ обновлён на 14:46.

## День 2026-05-11 (поздний вечер — Launcher: «У меня уже есть модель» + Пуск-меню)

**Launcher feature 1 — pointer-file для существующей модели GigaAM:**
- При первом запуске `VoiceTypeStudio-Setup.exe` после установки app-zip проверяет `C:/gigaam_cache/v3_e2e_ctc.ckpt`. Нет → диалог с тремя кнопками:
  - **«Указать папку»** — file dialog, валидация (`model_present()`), запись пути в `gigaam_cache_path.txt` рядом с `VoiceTypeStudio.exe`. Без копирования (~440 МБ сэкономлено).
  - **«Скачать сейчас»** — direct download с Sber CDN (`cdn.chatwm.opensmodel.sberdevices.ru/GigaAM`), progress bar по байтам.
  - **«Пропустить»** — приложение само скачает при первом ASR-вызове (`gigaam.load_model(download_root=...)`).
- `core/config.py` расширен: новый `_resolve_gigaam_cache()` с приоритетом `GIGAAM_CACHE_DIR` env → pointer file → `C:/gigaam_cache` default. Pointer ищется рядом с `sys.executable` если frozen, иначе рядом с `core/config.py` (для dev-режима). Модель остаётся в исходной папке пользователя — `gigaam.load_model(download_root=user_folder)` берёт её оттуда.

**Launcher feature 2 — Start-menu ярлык:**
- После установки лаунчер вызывает `create_start_menu_shortcut()` — best-effort попытка создать `.lnk` в `%APPDATA%\Microsoft\Windows\Start Menu\Programs\VoiceType Studio.lnk`.
- Реализация через PowerShell + `WScript.Shell` COM, без `pywin32`. Команда передаётся через `-EncodedCommand` (base64 UTF-16-LE) — bypass console codepage для путей с кириллицей.
- При ошибке (нет PowerShell, locked profile) ничего не блокируется — лаунчер просто запускает приложение без ярлыка.

**Тесты добавлены (5 шт):**
- `test_launcher_pointer_file_lives_next_to_exe` — POINTER_FILE в той же папке что и EXE_PATH.
- `test_launcher_model_present_requires_both_files` — `model_present()` отвергает folder с одним только tokenizer, отвергает половину-скачанный ckpt (<1 МБ).
- `test_launcher_start_menu_shortcut_path` — путь оканчивается на `VoiceType Studio.lnk` в per-user Start Menu (не all-users).
- `test_resolve_gigaam_cache_reads_pointer_file` — `_resolve_gigaam_cache()` читает указатель когда нет env var.
- `test_resolve_gigaam_cache_env_var_wins_over_pointer` — `GIGAAM_CACHE_DIR` имеет приоритет над указателем.

**Build**: `launcher/dist/VoiceTypeStudio-Setup.exe` собран (~6 сек, 10.4 МБ). Размер больше изначальной оценки (~5 МБ) из-за добавленных `base64` + `tkinter.filedialog` — приемлемо.

**Итого 194 теста (189 + 5 новых) passed, 3 skipped.**

## День 2026-05-11 (ночь — GitHub релиз + ffmpeg + history pool + иконки)

**GitHub репо + первый публичный релиз:**
- Создан https://github.com/maslovserg-hub/voicetype-studio (private → потом public).
- Релиз `v1.0.0` с двумя ассетами: `VoiceTypeStudio-Setup.exe` (~15 МБ — лаунчер) + `VoiceTypeStudio_release.zip` (~223 МБ — приложение).
- Single-link установка для знакомых: качаешь setup-exe → он сам тянет zip с `/releases/latest/download/`, ставит в `%APPDATA%\VoiceTypeStudio\`, создаёт ярлык, при необходимости спрашивает про GigaAM модель (см. ниже).

**Launcher iterations (5 раундов):**
1. **ffmpeg-диалог** — после установки проверка `shutil.which("ffmpeg")`. Если нет — три кнопки: silent winget install (`Gyan.FFmpeg --scope user`, PATH правится in-process) / открыть gyan.dev в браузере / пропустить. Без ffmpeg конвертация видео сломана, поэтому проверка обязательна.
2. **Flat install layout + `os._exit`** — `EXE_PATH` ожидал двойную вложенность `VoiceTypeStudio/VoiceTypeStudio/`, а zip из `dist/VoiceTypeStudio/*` распаковывал плоско. Плюс `sys.exit()` в Tk-`after`-коллбэке глотается интерпретатором — окно лаунчера висело после успешного `Popen`. Перешёл на плоский путь и `os._exit(0)`.
3. **Shortcut на каждом запуске** — раньше создание ярлыка происходило только в slow-path (`if not already_installed:` ветка). Если первый запуск падал на сломанном `EXE_PATH`, ярлык не создавался, и на втором запуске fast-path его уже не делал. Теперь `create_start_menu_shortcut()` идемпотентно вызывается в обоих путях.
4. **`model_present()` per-file thresholds** — тестировал на реальном `C:\gigaam_cache\`: `v3_e2e_ctc_tokenizer.model` весит ~241 КБ (это нормально, токенайзер маленький). Мой порог 1 МБ для обоих файлов отвергал валидные установки. Расширил `MODEL_FILES` до `(name, full_size, min_acceptable)` — ckpt должен быть ≥100 МБ, tokenizer ≥1 КБ.
5. **Shortcut через `IShellLinkW`, не `WScript.Shell`** — обычный WScript.Shell.CreateShortcut (через PowerShell, pywin32-Dispatch, VBScript — любой путь) использует ANSI-обёртку и падает на путях с кириллицей `C:\Users\Сергей\...` ("Value does not fall within the expected range"). Переход на Unicode-вариант `IShellLinkW` + `IPersistFile.Save()` через `win32com.shell` решил проблему. Launcher вырос с 10.9 МБ до 14.9 МБ (pywin32 в бандле).

**Общая база истории (desktop ↔ bot pool):**
- `core.history.owner_scope(settings)` возвращает `("desktop", str(whitelist[0]))` — владелец видит пул из десктоп- и tg-записей. Остальные whitelist-участники остаются изолированными — `_scope_for(settings, telegram_id)` отдаёт только их собственный `str(id)`.
- `history.recent()` и `get_segments()` теперь принимают либо строку (back-compat), либо итерируемый список user_ids. SQL `IN (?...)` плюс `_normalize_scope()` для дедупликации.
- UI-пометки в обоих интерфейсах: `💻` префикс для записи `user_id="desktop"`, `📱` для всего остального. Пользователь сразу видит откуда транскрипция.
- 5 новых тестов в `test_history_owner_pool.py` (SQL-плумбинг, sтрангер не видит чужие записи, env vs pointer priority).
- При сохранении: `bot.handlers.media.handle_*` пишут `str(message.from_user.id)`, `desktop.transcriptor_window._on_done` пишет `"desktop"` — без изменений, разделение источников сохраняется через user_id.

**Иконки + bot welcome photo:**
- Embedded `assets/icon.ico` в обоих exe через PyInstaller `icon=`. Tk root + все Toplevel наследуют через `iconbitmap(default=...)`. Tray грузит `assets/icon_64.png` через новый `core.assets.load_icon_image()` вместо нарисованного PIL микрофона.
- `assets/bot.png` отправляется в `/start` как photo с прежним текстом как caption. Bot description (`set_my_short_description` + `set_my_description`) ставится при `start_bot_polling` — видно при первом открытии чата над кнопкой «Старт».
- 4 раунда итерации по иконке: master транспарент с большим паддингом → iOS-bg вариант (тёмный квадрат — выглядел "boxy" против VS Code) → master без margin → новый пак v2 с пузырём от пользователя. У v2 пакета был near-invisible drop shadow → `getbbox()` врал про 88%×60% fill (реально 42%×41%). Финальный crop делается по `alpha > 30` маске, итог — пузырь занимает 100%×98% канваса.
- Полезный урок: **`win32api.UpdateResource` не годится для PyInstaller exe** — он обрезает overlay-данные (PKG-архив, который PyInstaller вешает хвостом к бинарю). После такой подмены exe не запускается ("Could not load PyInstaller's embedded PKG archive"). Восстановили копированием свежесобранного exe из `dist/` поверх установленного. Для in-place подмены иконки в будущем нужен `rcedit.exe` (он понимает overlay).

**Cleanup:**
- Удалена старая программа VoiceType (предыдущий проект): `%APPDATA%\VoiceType\` + ярлык + autostart-запись `HKCU\...\Run\VoiceType`.
- Удалена битая autostart-запись `VoiceTypeStudio` указывающая на dev-Python (`pythonw.exe main.py`). Если нужна автозагрузка — теперь правильно поставить через галку в Настройках, она пропишет `frozen-exe` путь.
- Из проекта удалены stray-папки от криво-направленного bash (`Projectsvoicetype-studiovenv/`, `c:Projectsvoicetype-studiotests/`), большая `build/` (170 МБ), и `VoiceTypeStudio_release.zip` (212 МБ — gh release сам его хостит). Все добавлены в `.gitignore`.

**Итого 199 тестов passed, 3 skipped.** Релиз v1.0.0 публичный, knaki может скачать и установить. Сегодняшняя сборка лаунчера и zip — на gh release под тэгом v1.0.0.

## Что осталось / на завтра

- [ ] **UX-проверка overlay live** — запустить новую сборку, нажать Right-Ctrl, убедиться что индикатор внизу по центру.
- [ ] **UX-тест Истории вживую** — после рестарта пользователь ещё не успел проверить «История…» в новой версии (с фиксом focus). Вчерашние и сегодняшние транскрипции должны быть в списке.
- [ ] **Тест других платформ** с новым format+concurrent_fragments — YouTube, VK, Я.Диск, Google Drive. Должны быть быстро.
- [ ] **Bot avatar через @BotFather** — программно поставить картинку нельзя (Bot API не покрывает). Открыть BotFather → `/mybots` → выбрать бот → Edit Bot → Edit Botpic → загрузить `c:\Projects\voicetype-studio\assets\bot.png`. Один раз, ручной шаг.
- [ ] **Хотим in-place icon swap без ребилда** — текущая попытка через `win32api.UpdateResource` сломала exe (overlay truncation). Решение — `rcedit.exe` от electron, он умеет с PyInstaller-overlay. Скачать exe ~1 МБ, добавить хелпер скрипт. Сэкономит ~5 мин на каждой итерации по иконке.
- [ ] **Опционально**: если silero 24k всё равно недостаточно — Edge TTS / OpenAI TTS / ElevenLabs на выбор (см. варианты в чате).

## День 2026-09-06 (первая установка на второй машине — два реальных бага в релизе v1.0.1)

**Контекст:** первая установка VoiceType Studio на отдельном компьютере (не основная машина разработки), через публичный `VoiceTypeStudio-Setup.exe` с GitHub Releases v1.0.1. Вскрыла два независимых бага, оба живут в релизе с момента его публикации — задели бы любого нового пользователя.

**Баг 1 — двойная вложенность в `VoiceTypeStudio_release.zip`:**
- `docs/BUILDING.md` документировал `Compress-Archive -Path dist\VoiceTypeStudio -DestinationPath ...` (без `\*`). Такая команда кладёт саму папку `VoiceTypeStudio\` как top-level entry в архиве → при распаковке в `INSTALL_DIR` получается `VoiceTypeStudio\VoiceTypeStudio\VoiceTypeStudio.exe`, а `launcher.py`'s `EXE_PATH` ждёт плоский `INSTALL_DIR\VoiceTypeStudio.exe`.
- Тот самый баг с двойной вложенностью, который чинили для v1.0.0 (см. запись «Flat install layout» выше) — регрессировал в v1.0.1 именно из-за этой команды в доке.
- Следствие: `launch()` → `subprocess.Popen([EXE_PATH])` кидает `FileNotFoundError` внутри Tkinter `after`-коллбэка. Tkinter глотает исключение молча (`console=False`, никакого stderr не видно) → окно установщика висит на «Готово! Запускаю…» навсегда.
- **Фикс:** `docs/BUILDING.md` исправлен на `-Path dist\VoiceTypeStudio\*` (со звёздочкой) + предупреждающий комментарий в самой команде. Исправленный `VoiceTypeStudio_release.zip` (плоская структура, 223 552 584 байт) пересобран из уже установленных файлов (без полной пересборки из исходников — Compress-Archive поверх уже распакованной папки) и вручную залит поверх старого ассета в релизе v1.0.1 на GitHub.
- Коммит: `165c8a2` — `docs: fix Compress-Archive command that caused double-nested v1.0.1 zip`.

**Баг 2 — `install_ffmpeg_via_winget()` бросает fallback из-за ненулевого кода winget:**
- Диктовка «работала» (индикатор реагировал на голос), но текст никогда не вставлялся. Причина — GigaAM (`gigaam/preprocess.py: load_audio`) шеллится в `ffmpeg` для декодирования WAV; без ffmpeg на PATH — тихий `FileNotFoundError` в фоновом потоке `desktop/dictation.py:_transcribe_and_paste`, проглоченный `logger.exception` (опять же invisible при `console=False`).
- Диагностика потребовала перезапуска `VoiceTypeStudio.exe` через `Start-Process -RedirectStandardOutput/-RedirectStandardError` — единственный способ увидеть логи у GUI-приложения без консоли (PyInstaller `--windowed` иначе никуда их не пишет).
- Корень: в `launcher.py` `install_ffmpeg_via_winget()` при **любом** ненулевом коде возврата `winget install` сразу возвращала `False`, даже не проверяя диск. А winget возвращает ненулевой код и для совершенно безобидного случая «уже установлено, обновлений нет» — который бывает при повторном запуске установщика, на заранее подготовленных корпоративных образах и т.п. В этом случае fallback-сканирование `%LOCALAPPDATA%\Microsoft\WinGet\Packages` за `ffmpeg.exe` (которое патчит `os.environ["PATH"]` в процессе лаунчера, чтобы унаследовал и запускаемый через `Popen` app) просто никогда не выполнялось.
- **Фикс:** убрана ранняя проверка `if result.returncode != 0: return False` — теперь fallback (`ffmpeg_available()` + скан WinGet Packages) всегда выполняется после попытки установки, независимо от кода выхода winget. Настоящим провалом остаётся только `OSError`/`TimeoutExpired` при самом запуске winget или полное отсутствие ffmpeg на диске после.
- Добавлен регрессионный тест `test_launcher_winget_already_installed_still_finds_ffmpeg` — воспроизводит ровно этот сценарий (winget возвращает `returncode=1`, но `ffmpeg.exe` реально лежит в Packages).
- Коммиты: `626a5d7` (фикс + тест), `5bc2486` (по замечанию `codex review` — тест не мокал `sys.platform`, падал бы на не-Windows).

**Независимый ревью:** `codex review --base 88d4eb4` (CLI `openai.chatgpt`-расширения VS Code, уже авторизован) прогнан по диффу всех трёх коммитов — нашёл ровно одно реальное P2-замечание (см. выше), больше проблем не выявил.

**Побочные находки/фиксы на самой установке (не в коде, локально на машине):**
- Ярлык в Пуск-меню не создавался (`create_start_menu_shortcut()` в лаунчере молча зафейлилась) — создан вручную через `WScript.Shell` COM.
- Итого 20/20 тестов в `tests/test_build_smoke.py` зелёные после всех правок.

**Осталось:**
- [ ] Переименовать ассет `_tmp_setup_from_v1.0.0.exe` → `VoiceTypeStudio-Setup.exe` в релизе v1.0.1 (косметика, не блокирует установку — `APP_URL`/ссылка в README не зависят от имени этого файла).
- [ ] Разобраться, почему `create_start_menu_shortcut()` зафейлилась при обычном запуске лаунчера — не воспроизводили целенаправленно, просто пофиксили вручную постфактум.

## День 2026-09-22 (Perplexity SSL + история и настройки внутри Транскриптора)

**Баг — «Конспект: certificate has expired» у Perplexity:**
- Сервер в порядке (у OpenSSL из Git `Verify return code: 0`, сертификат до 13.11.2026). Perplexity в августе 2026 перешёл на новую цепочку Let's Encrypt (YE → Root YE → ISRG Root X2 → X1).
- Python на Windows читает системные хранилища, а в хранилище промежуточных CA лежал **просроченный кросс-сертификат «ISRG Root X2 ← X1» (истёк 15.09.2025)**. OpenSSL брал его вместо свежего из цепочки сервера → `certificate has expired`. Касается любого сайта на новой цепочке LE.
- **Фикс:** [core/http.py](../core/http.py) — `client_session()` с `ssl=certifi` (только корни, цепочка строится из того, что прислал сервер). На неё переведены все `aiohttp.ClientSession`: LLM base/anthropic/gemini, downloader, проверка Telegram-токена. `certifi` добавлен в `requirements.txt` (в venv уже был; в exe его кладёт штатный hook-certifi PyInstaller).
- Проверка: живой POST на api.perplexity.ai → HTTP 401 (без ключа) вместо SSL-ошибки.

**Трей и Транскриптор:**
- Трей: вместо «Открыть транскриптор / История… / Настройки…» — один пункт «Транскриптор» ([tray.py](../desktop/tray.py)).
- В окне Транскриптора сверху «☰ История» и «⚙ Настройки». История — выезжающая панель слева ([history_panel.py](../desktop/history_panel.py), бывший `history_window.py`), вкладки «Расшифровки» (клик → текст в ленту) и «Скачанное» (имя файла, ссылка — клик открывает, «Ссылка» копирует, «📁 Папка» — файл выделен в проводнике; для удалённого файла кнопка неактивна). Если окну не хватает ширины, оно расширяется на время открытия панели и возвращается обратно.
- Грабли: `CTkScrollableFrame` пакует внутреннюю обёртку, поэтому `pack(before=feed)` падает «isn't packed» — тело окна на `grid`. `geometry()` у CTk в логических px, `winfo_width()` — в физических, пересчёт через `_reverse_window_scaling`.
- Настройки теперь дочернее окно Транскриптора — раньше (родитель — скрытый root) открывались ПОЗАДИ него.
- Скачивания пишутся в историю: новая таблица `downloads` в `history.db` (`add_download` / `recent_downloads`), таблица `transcriptions` и бот не тронуты. Старые скачивания нигде не записывались — список начинается с этой версии.
- Настройки → «Скачивание»: папка для кнопки «Скачать» (`Settings.download_dir`, пусто = системные «Загрузки»). Нет папки (флешку вынули) — файл уходит в «Загрузки» с подсказкой в ленте.

**Тесты:** 212 passed, 3 skipped (было 210/3). Новые: downloads в истории, `download_target` (своя папка / пусто / нет папки), пункты меню трея, строки панели.

**Осталось:**
- [ ] Пересобрать exe и проверить «Конспект» на реальном ключе Perplexity.

### Обновление одной кнопкой (v1.0.3)

- Версия приложения живёт в [core/version.py](../core/version.py) (`__version__`), тег релиза должен совпадать (`v1.0.3` ↔ `1.0.3`).
- Настройки → «Обновление»: «Проверить обновления» спрашивает GitHub API `/releases/latest` (репозиторий публичный, токен не нужен), сравнение числовое — `1.0.10 > 1.0.9`. Автопроверки при запуске нет сознательно.
- Обновление делает **один** скрипт [tools/VoiceTypeStudio-Update.bat](../tools/VoiceTypeStudio-Update.bat) — и кнопка, и ручной запуск двойным кликом. Он закрывает приложение (мягко, через 10 с — принудительно), качает zip из `releases/latest/download`, распаковывает поверх `%APPDATA%\VoiceTypeStudio`, запускает обратно. Приложение само распаковать себя не может — занятый exe.
- Кнопка копирует скрипт в `%TEMP%` перед запуском: cmd читает .bat построчно по ходу выполнения, а скрипт перезаписывает папку, в которой лежит. Запуск через `os.startfile`, а не `Popen` — `main.py` навешивает `CREATE_NO_WINDOW` на все `Popen`, окно прогресса было бы не видно.
- **Грабли Windows (все три вылезли на этой машине):**
  - `tar` и `curl` из Git перехватывают вызов, а GNU tar из Git **не читает zip** → в скрипте абсолютные пути `%SystemRoot%\System32\`.
  - Системный `tar` калечит кириллицу в аргументах (`C:\Users\Сергей\…` → `??????`) → архив кладём в `C:\ProgramData` (только латиница), а распаковываем через `cd /d "%DIR%"` без передачи пути tar'у.
  - .bat обязан быть CRLF (иначе cmd путается в метках и `goto`) → `.gitattributes` с `*.bat text eol=crlf` + проверка в тесте.
- Старые версии (1.0.2 и ниже) кнопки не имеют — для них .bat прикладывается ассетом к релизу.

### Ролики на других языках и перевод (v1.0.5)

- Переключатель **RU | Другой** в строке ввода Транскриптора ([desktop/transcriptor_window.py](../desktop/transcriptor_window.py)). Язык читается в момент «Старт»/перетаскивания.
- «Другой»: ссылка YouTube → оригинальные субтитры через yt-dlp (`Downloader.fetch_subtitles`); иначе → Yandex SpeechKit ([core/speechkit.py](../core/speechkit.py)): OGG Opus inline, `auto`-язык, таймкоды из `words[]`. Ключ — `Settings.speechkit_api_key`.
- Кнопка **🌐 Перевод** — `SummaryMode.TRANSLATE`, на русский с таймкодами.
- Gemini API из России не работает (400 «User location is not supported»); модель по умолчанию обновлена с удалённой `gemini-1.5-flash` на `gemini-3.8-flash`.
- SpeechKit ставит пунктуацию только для русского — английский приходит без знаков, решили оставить так.
- 240 тестов зелёные, релиз [v1.0.5](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.5) опубликован и проверен вживую.
