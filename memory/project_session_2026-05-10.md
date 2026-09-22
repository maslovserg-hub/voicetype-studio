---
name: VoiceType Studio session state — end of 2026-05-10
description: Where we stopped today + immediate next steps for tomorrow's session
type: project
originSessionId: fe63cb54-151d-4c19-9782-ed89599a2d68
---
## Где остановились (вечер 10 мая 2026)

Все 8 этапов плана закрыты. Проект работает end-to-end на живой машине. **165 unit-tests passed, 3 skipped.** Сейчас запущен `python main.py` (PID 16504), tray-иконка должна быть в трее.

**Why:** провели длинный отладочный сессион — нашли и починили цепочку реальных багов в производственном коде (не в плане).

**How to apply:** завтра завершить начатое тестирование UX и при необходимости пересобрать через PyInstaller.

## Что было пофикшено сегодня (по порядку)

1. **Обнаружен и пофикшен wiring-баг с GigaAM моделью** — Transcriber имел хардкод `os.getenv("GIGAAM_MODEL", "v3_ctc")`, игнорировал `config.gigaam_model="v3_e2e_ctc"`. Скачивалась версия БЕЗ пунктуации, отсюда «текст без форматирования». Сейчас читается из config. Удалён лишний `v3_ctc.ckpt` (421 МБ) из `C:\gigaam_cache\`.

2. **Whitelist hot-reload в боте** — `App._on_settings_saved` рестартил бота только если поменялся `bot_token`. Whitelist'у изменения не доставлялись (middleware кэширует frozenset в конструкторе). Теперь рестарт также при `whitelist_changed`.

3. **ПКМ-меню «Вставить» в полях ввода** — `event_generate("<<Paste>>")` уходил в CTk-обёртку, не во внутренний `tk.Entry`. Добавлен `_inner_text_widget()` который разворачивает `._entry` / `._textbox`. Шрифт меню увеличен с системного 9pt до 11pt Segoe UI.

4. **Иконка «вставить файл»** — заменил мутный emoji на PIL-нарисованный значок документа с «+». Теперь это `desktop/_icons.py` + кнопка `📁 Файл` с иконкой.

5. **YouTube auto-cookies** (большой кусок работы):
   - Свой декриптор Chromium-cookies в `core/cookies_extractor.py` — читает SQLite, расшифровывает AES-GCM v10/v11 с master-ключом из Local State (через DPAPI).
   - В `core/downloader.py` при bot-check: `_harvest_browser_cookies()` достаёт cookies для `youtube.com / google.com` из всех Chromium-браузеров.
   - **Критичный нюанс**: после AES-GCM нужно отрезать первые 32 байта (SHA-256 integrity hash который Chromium префиксует к cookie value). Без этого получалась байтовая каша.
   - Добавлен `_shared_copy()` через Win32 CreateFile с FILE_SHARE_READ|WRITE|DELETE — обходит большинство file-locks (Yandex Browser исключение — он лочит EXCLUSIVELY без шаринга).
   - Установлены `pywin32`, `pycryptodome`, `yt-dlp-ejs`.
   - **КЛЮЧЕВОЕ открытие**: yt-dlp 2026.3+ по умолчанию включает только `deno` runtime. Нужно `js_runtimes={"node": {"path": None}}` чтобы yt-dlp использовал Node.js (который у пользователя установлен) для решения n-sig challenge. Без этого только storyboard-картинки, не аудио.
   - Установлен Node.js (`C:\Program Files\nodejs\node.exe`) — он уже был.

6. **Chunking robustness** — `split_for_short_asr` мог в конце создать микро-чанк (16ms = 256 сэмплов), GigaAM падал на STFT (`n_fft=320`). Добавлен фильтр на `_MIN_CHUNK_MS = 100`. Плюс per-chunk try/except в `Transcriber._transcribe_chunked` — при крахе одного чанка, остальные доходят до результата.

7. **Auto-cleanup временных файлов** — раньше ничего не чистилось, копились гигабайты в `data\tmp\`. Теперь:
   - `transcriptor_window._run_task` finally-блок удаляет скачанный файл, конвертированный WAV и папку `_short_chunks`. Локальный input (drag&drop) НЕ удаляется.
   - То же в `bot/handlers/links.py` и `media.py`.
   - В tray-меню добавлены пункты «Папка с данными» и «Очистить временные файлы».

8. **TTS UX**:
   - Кнопка «Готовлю…» заменена на конкретные тексты per-format (`🔊 Озвучиваю… (10–30 сек)`, `📋 Считаю тезисы…`, и т.д.).
   - Результат озвучки теперь показывает крупную **▶ Воспроизвести** кнопку справа от заголовка, плюс «Сохранить как…» и «Открыть папку».
   - **Последний фикс перед концом сессии**: TTS speaker ИЗ Settings UI не работал — `TTSService._resolve_speaker()` читал `os.getenv("TTS_SPEAKER")`, игнорируя `settings.tts_speaker`. Тот же класс бага что был с GigaAM model. Теперь `synthesize(text, path, speaker=settings.tts_speaker)` — параметр прокидывается из обоих callers (desktop + bot).

## Что осталось / что проверить ЗАВТРА

- [ ] **UX-тест озвучки**: открыть Транскриптор, перетащить файл, дождаться транскрипции, нажать 🔊, **проверить что переключение голосов в Настройках теперь реально работает** (попробовать aidar / kseniya / xenia / baya / eugene и услышать разницу).
- [ ] **UX-тест видео-ссылок** на разных типах (RuTube, VK, Я.Диск, обычный YouTube) — все ли работает после auto-cookies + js_runtimes=node.
- [ ] **Проверить что временные файлы реально чистятся** — после транскрипции глянуть `data\tmp\` через tray → «Папка с данными».
- [ ] **PyInstaller пересборка** — тек. dist собран до сегодняшних правок, надо `pyinstaller VoiceTypeStudio.spec` пересобрать. Но это manual + долго (~10 мин).
- [ ] **Возможно** — добавить настройку для disabled bot mode где приложение работает без бота (если флажок выключен — все функции бота недоступны).

## Архитектурные решения / инварианты

- **Нет Whisper API** — выбран GigaAM v3_e2e_ctc для русского.
- **GigaAM-модель НЕ выводится в settings UI** — зашита в `core.config.gigaam_model = "v3_e2e_ctc"`. См. `feedback_no_giga_model_in_settings.md`.
- **Single executor** для GigaAM — `Transcriber.set_executor(asr_executor)` в `App.__init__`, max_workers=1, шарится между диктовкой / окном / ботом.
- **Yandex Browser** — реально работающий браузер для extract'а cookies, но **только когда полностью закрыт** (включая фоновые `browser.exe`). Чтобы его реально завершить: Меню → «Закрыть Яндекс.Браузер», ИЛИ выключить настройку «Продолжать работу в фоне».
- **Chrome 127+ / Edge** — extract cookies НЕ РАБОТАЕТ (app-bound encryption v20). Это технологическое ограничение, не баг.
- **Главный pipeline для YouTube**: Download → AudioConverter → split_for_short_asr → Transcriber → Formatter/Summarizer → результат.

## Запуск завтра

```powershell
cd c:\Projects\voicetype-studio
.\venv\Scripts\python.exe main.py
```

Все зависимости в venv установлены. Никаких `pip install` не нужно.

## Полезные команды

- Прогон тестов: `.\venv\Scripts\python.exe -m pytest tests/`
- Чистка `data\tmp\`: ПКМ по tray-иконке → «Очистить временные файлы»
- Открыть data folder: ПКМ по tray → «Папка с данными»

## Файлы где жили основные правки сегодня

- [core/cookies_extractor.py](c:\Projects\voicetype-studio\core\cookies_extractor.py) — новый файл
- [core/downloader.py](c:\Projects\voicetype-studio\core\downloader.py) — переработан
- [core/transcriber.py](c:\Projects\voicetype-studio\core\transcriber.py) — gigaam_model wiring + per-chunk error tolerance
- [core/tts.py](c:\Projects\voicetype-studio\core\tts.py) — speaker parameter
- [core/chunking.py](c:\Projects\voicetype-studio\core\chunking.py) — _MIN_CHUNK_MS filter
- [desktop/_clipboard_menu.py](c:\Projects\voicetype-studio\desktop\_clipboard_menu.py) — новый
- [desktop/_icons.py](c:\Projects\voicetype-studio\desktop\_icons.py) — новый
- [desktop/_message_widget.py](c:\Projects\voicetype-studio\desktop\_message_widget.py) — TTS UX
- [desktop/transcriptor_window.py](c:\Projects\voicetype-studio\desktop\transcriptor_window.py) — temp cleanup
- [desktop/tray.py](c:\Projects\voicetype-studio\desktop\tray.py) — два новых пункта меню
- [main.py](c:\Projects\voicetype-studio\main.py) — handlers для новых меню + whitelist hot-reload
