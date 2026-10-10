---
name: VoiceType Studio session state — 2026-10-10
description: v1.0.10 — фильтр звуков-заминок в диктовке + защита от трат SpeechKit (из 1633042) вышли в релиз.
type: project
---
## Что сделано (v1.0.10)
- **Фильтр заминок** в `clean_dictation_text` (`desktop/dictation.py`, `_HESITATION`): «э», «э-э-э», «э-э-.», «эм», «мм», «м-м», «а-а». Одиночное «а», «ну», «вот», «как бы» НЕ трогаем — настоящие слова. Только диктовка; Транскриптор (файлы/ссылки) не фильтруется. Тест `test_clean_dictation_text_drops_hesitations`.
- Защита от трат SpeechKit (коммит 1633042) вошла в этот релиз.

## Релиз
[v1.0.10](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.10): release.zip 219 666 932 б, update.zip 49 840 341 б, deps.txt (совпал с 1.0.9 → обновление ~50 МБ), Update.bat, Setup.exe из dist_release.
- Собранный exe вживую не запускали (в трее работает установленная 1.0.9); проверка — обновиться кнопкой и надиктовать «э-э-э».
