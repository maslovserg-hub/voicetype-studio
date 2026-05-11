# VoiceType Studio

Windows-приложение для распознавания и обработки русской речи. Объединяет:

- 🎤 **Голосовая клавиатура** — Right Ctrl → говоришь → текст вставляется в активное окно (как Voice Type сегодня).
- 🪟 **Окно «Транскриптор»** — drag&drop файлов / вставка ссылок (Я.Диск, YouTube, RuTube, VK, Google Drive). 8 форматов вывода: текст, таймкоды, SRT, AI-тезисы, по разделам, по ролям, вопросы с таймкодами, озвучка.
- 🤖 **Telegram-бот** (опционально) — то же самое через свой Telegram, для использования с телефона.

Один процесс, одна модель GigaAM в RAM.

## Документы проекта

- [Спецификация](C:/Users/Сергей/thoughts/shared/specs/2026-05-09-voicetype-studio.md) — что и почему.
- [Implementation Plan](C:/Users/Сергей/thoughts/shared/plans/2026-05-09-voicetype-studio-implementation.md) — как и в каком порядке.
- [docs/PROGRESS.md](docs/PROGRESS.md) — что уже сделано.

## Статус

🚧 **В разработке** (этап 0–1 из 8). Не для продакшена.

## Стек

- Python 3.12
- aiogram 3 (Telegram)
- customtkinter (UI)
- pystray + pynput (tray + hotkey)
- GigaAM v3_e2e_ctc (ASR, локально)
- silero v4_ru (TTS, локально)
- Multi-provider LLM: Perplexity / OpenAI / Anthropic / Gemini

## Запуск (для разработчика)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Лицензия

MIT.
