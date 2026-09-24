---
name: VoiceType Studio session state — 2026-09-23/24
description: v1.0.5 — язык «Другой» (субтитры YouTube → Yandex SpeechKit), кнопка «Перевод». Gemini из России заблокирован.
type: project
---
## Что сделано

- **Переключатель RU | Другой** в строке ввода Транскриптора. Язык берётся в момент «Старт»/перетаскивания, поэтому выбирать его надо ДО того, как бросить файл.
- **«Другой»:** ссылка YouTube → сначала оригинальные субтитры через yt-dlp (`Downloader.fetch_subtitles`, json3; авторские в языке ролика, иначе `<lang>-orig` авто). Нет субтитров или файл → `core/speechkit.py`.
- **SpeechKit (API v3 async):** OGG Opus 32k, аудио inline в `recognizeFileAsync` (бакет не нужен, лимит 60 МБ ≈ 3 ч по замеру 0,35 МБ/мин), `languageCode: ["auto"]`, опрос `operation.api.cloud.yandex.net/operations/{id}`, потом `getRecognition`. Ключ: `Settings.speechkit_api_key`, заголовок `Api-Key`, сервисный аккаунт с ролью `ai.speechkit-stt.user`, scope `yc.ai.speechkitStt.execute`.
- **Кнопка 🌐 Перевод** — `SummaryMode.TRANSLATE`, на русский с таймкодами, через провайдер по умолчанию. Стоит в первом ряду кнопок.
- Gemini: модель `gemini-1.5-flash` (удалена Google, 404) → `gemini-3.8-flash`.

## Грабли

1. **Gemini API из России: 400 «User location is not supported».** OpenAI тоже блокирует. Для аудио — только SpeechKit или локальная модель.
2. **SpeechKit отдаёт всю запись одним высказыванием** — таймкоды только из `words[]` (передаём в `Segment.words`, Formatter режет по 6 с).
3. **Пунктуация SpeechKit — только русский**, а в `auto` нормализации нет вообще. Английский приходит без точек/апострофов. Пользователь решил оставить так («Перевод»/«Конспект» дают нормальный текст).
4. Голос Windows SAPI по умолчанию русский — для теста английского распознавания не годится (SpeechKit честно выдал транслит).
5. Перед сборкой — `git fetch`: пользователь коммитит с другой машины (v1.0.4 пришла, пока я работал).

## Релиз
[v1.0.5](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.5) опубликован: zip 223 588 710 б (плоский), Setup.exe переиспользован из v1.0.4, Update.bat. `releases/latest` → v1.0.5 проверено. У пользователя установлена эта же сборка, «Другой» + «Перевод» проверены им вживую.

Update.bat годится для любой старой версии (install dir `%APPDATA%\VoiceTypeStudio` не менялся с v1.0) — отправлять его тем, у кого нет кнопки «Обновить».
