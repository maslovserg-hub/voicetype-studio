# CLAUDE.md — VoiceType Studio

Этот файл загружается автоматически при работе в этой директории.

## Контекст

**VoiceType Studio** — Windows-приложение для распознавания и обработки русской речи. Объединяет три способа использования (диктовка через Right Ctrl, окно-транскриптор для файлов/ссылок, опциональный Telegram-бот) на общем backend (GigaAM v3_e2e_ctc + multi-provider LLM + silero TTS).

## Главные документы (читай первыми каждую новую сессию!)

1. **Спецификация** — `C:\Users\Сергей\thoughts\shared\specs\2026-05-09-voicetype-studio.md`
   Что делаем, почему, для кого, какие user journey, P0/P1/P2 функционал, технические решения, acceptance criteria. ~12 страниц.

2. **Implementation Plan** — `C:\Users\Сергей\thoughts\shared\plans\2026-05-09-voicetype-studio-implementation.md`
   8 этапов реализации с пошаговым разбором: что копировать из старых проектов, что писать нового, какие тесты, зависимости между этапами. ~10 страниц.

## Источники для миграции (НЕ ПИШЕМ С НУЛЯ)

- **Backend / handlers:** `c:\Projects\transcription-bot\` — рабочий Telegram-бот со всеми сервисами (Transcriber, Downloader, Summarizer, TTS, Formatter, history, transcript_cache). 90% его кода переезжает в `core/`.
- **Tray + диктовка:** `f:\AI\AI 360 (Ледовских)\ВАЙБКОДИНГ 2.0\Projects\my-voice-assistent\` — текущий Voice Type. Его `main.py` разбираем на куски в `desktop/`.

## Текущий статус

Этап 0 (подготовка) — почти готов: структура папок и базовые файлы созданы. Этап 1 (core/) — частично: первые файлы скопированы. Дальше — продолжать по плану.

**Где смотреть прогресс:** в `docs/PROGRESS.md` после каждого этапа отмечать что сделано.

## Default workflow для каждой сессии

1. Прочитать **спеку** и **implementation plan** (см. выше).
2. Прочитать `docs/PROGRESS.md` — что уже сделано.
3. Найти в плане первый незавершённый этап → продолжить с него.
4. После завершения этапа — обновить `docs/PROGRESS.md`, закоммитить.

## Технические инварианты

- **Один процесс, одна модель GigaAM в RAM** (главный архитектурный принцип).
- customtkinter в main thread, asyncio в daemon-thread, `ThreadPoolExecutor(max_workers=1)` для GigaAM (shared между всеми интерфейсами).
- aiogram запускается с `handle_signals=False`.
- Cross-thread: `asyncio.run_coroutine_threadsafe` для GUI→bot, `queue.Queue + root.after(50)` для bot→GUI.
- Данные в `C:\VoiceTypeStudio\data\`, модель в `C:\gigaam_cache\` (как сегодня в Voice Type).

## Что НЕ делать

- Не переписывать с нуля код, который рабочий в transcription-bot или my-voice-assistent.
- Не добавлять backwards-compatibility со старыми проектами — они отправляются в архив.
- Не делать локальные LLM (Ollama) в V1 — это P2.
- Не делать перевод между языками — это out of scope.
- Не использовать Whisper API — выбран GigaAM-v3 для русского.
