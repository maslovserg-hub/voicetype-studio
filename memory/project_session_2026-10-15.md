---
name: VoiceType Studio session state — 2026-10-15
description: v1.0.11 — «Живые субтитры» в трее (перенос live-subtitles), ключи Deepgram/DeepSeek в Настройках, трей без заголовка, Update.bat не виснет
type: project
---
## Что сделано (v1.0.11)
- **Живые субтитры** перенесены из `C:\Projects\live-subtitles` одним модулем `desktop/live_subtitles.py` (Toplevel от корня, своя иконка). Ключи `deepgram_api_key` / `deepseek_api_key` вводит пользователь в Настройках — вшитых ключей нет. live-subtitles заморожен (README).
- Решение: перевод в субтитрах — DeepSeek внутри модуля, провайдеры `core/llm/` не трогали. Альтернатива «всё на Яндексе» (потоковый SpeechKit + Yandex Translate) отложена: нужен gRPC и переотладка распознавания.
- Меню трея без заголовка «VoiceType Studio».
- `Update.bat`: `--speed-limit 1024 --speed-time 30 --retry 3` — закрывает зависание на 85 % из бэклога.
- Вошли коммиты с другой машины: прогрев GigaAM, окно «О программе».

## Грабли сессии
- Собрал первую сборку от локального `main`, отставшего от `origin/main` на 6 коммитов. **Перед сборкой — `git fetch` и сверка `main..origin/main`**, а не только перед релизом.
- Пробный запуск окна субтитров из скрипта пишет `live_subtitles.log` в настоящий `C:\VoiceTypeStudio\data\` — для таких проверок подменять `config.data_dir`.

## Релиз
Полный (новые библиотеки soundcard/deepgram/cffi → `deps.txt` сменился, обновление ~220 МБ, а не 50).
