---
name: VoiceType Studio session state — end of 2026-05-11
description: Конец дня 11 мая. UX-итерация по живому окну + 24k TTS-фикс. Что осталось на следующую сессию.
type: project
originSessionId: 17bb6009-4152-439d-87e2-9cbc23b90eb7
---
## Где остановились (вечер 11 мая 2026)

**Тестов:** 183 passed, 4 skipped (вчера было 165 → +18 новых сегодня).
**Приложение:** последний рестарт прошёл, pythonw.exe жив с применёнными правками. TTS звучит ОК на 24 kHz (xenia подтверждена пользователем).

**Why:** сегодняшняя сессия была не про новые этапы плана, а про устранение UX-замечаний пользователя по живому Транскриптору + одна большая фича (окно «История»).

**How to apply:** на следующую сессию — продолжить с пунктов «Что осталось». Главное — пересобрать PyInstaller (текущий `dist/` устарел на 2 дня и НЕ содержит ничего из сегодняшних правок).

## Что сделано сегодня (по областям)

### Транскриптор UX
- `MessageWidget` теперь умеет collapse/expand. Header — кнопка-стрелка ▼/▶ (32×28, font 16 bold) + clickable label.
- Новые карточки появляются СВЕРХУ (`pack(before=...)` + scroll-to-top).
- При появлении новой карточки старые автоматически сворачиваются.

### Окно «История»
- Новый `desktop/history_window.py` — список последних 50 desktop-транскрипций, клик восстанавливает segments из `history.db` без перетранскрипции.
- В `TranscriptorWindow` появился `restore_from_history(source_label, source, segments)`.
- В трей-меню — пункт «История…».
- Найден и пофикшен TclError на focus после destroy (CTkToplevel internal `after()` race) — все `lift/focus/destroy` теперь deferred + winfo_exists guards.

### Downloader (RuTube / HLS)
- Format-селектор: `bestaudio/worstaudio/worstvideo[height<=480]+bestaudio/best[height<=480]/worst`. Для RuTube ~1 GB → ~210 MiB.
- `concurrent_fragment_downloads=8` + `fragment_retries=10`. Для 507-фрагментных HLS-стримов — x4-x8 ускорение.
- Реальный прогресс-бар во время yt-dlp скачивания (через `progress_hooks` → callback в UI).

### TTS
- `TTSService._sample_rate` снижен с 48000 → 24000. Silero v4_ru обучен на 24 kHz, при 48k делает linear-interpolation upsample → металлические артефакты. См. `feedback_silero_24khz_invariant.md`.

## Что осталось / что проверить ЗАВТРА

- [ ] **Ярлыки для запуска** — пользователь явно попросил: ярлык в меню Пуск (чтобы запускать приложение после выхода из трея) и опционально на рабочем столе. План обсуждён: новый `desktop/shortcuts.py` через `pywin32 + WScript.Shell` (Dispatch), `.lnk` в `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` и `%USERPROFILE%\Desktop\`. В трее два toggle-пункта рядом с «Запускать с Windows»: «Ярлык в меню Пуск», «Ярлык на рабочем столе». Авто-создание Start Menu shortcut при первом запуске bundled exe (через `getattr(sys, "frozen", False)` guard, чтобы из source НЕ создавал автоматически). ~30-40 мин работы. Пользователь не дал ответ КАК хочет последовательность (до сборки или после) — переспросить в начале сессии.
- [ ] **PyInstaller пересборка** — текущий `dist/VoiceTypeStudio/` собран до сегодняшних правок. Запустить `pyinstaller VoiceTypeStudio.spec` (~10 мин на CPU). После сборки smoke-test: запустить `dist/VoiceTypeStudio/VoiceTypeStudio.exe`, проверить что трей и Транскриптор поднимаются.
- [ ] **«История…» вживую** — после последнего рестарта пользователь ещё не открыл окно. Должны быть видны и вчерашние, и сегодняшние записи. Проверить, что клик действительно восстанавливает карточку с рабочими кнопками форматов.
- [ ] **Другие URL-платформы** — проверить YouTube / VK / Я.Диск / Google Drive с новым format+concurrent_fragments. Особенно YouTube — auto-cookies + node + concurrent fragments вместе должны давать быструю и надёжную скачку.
- [ ] **Опционально**: если silero 24k всё ещё не нравится — пользователь обозначил 4 варианта на выбор (kseniya/aidar спикеры; Edge TTS бесплатно; OpenAI TTS платно; ElevenLabs топ). Решение пока отложено.

## Запуск

```powershell
cd c:\Projects\voicetype-studio
.\venv\Scripts\pythonw.exe main.py    # без console flash
# или
.\venv\Scripts\python.exe main.py     # с консолью (для дебага)
```

## Прогон тестов

```powershell
.\venv\Scripts\python.exe -m pytest tests/ -q
```

Должно быть 183 passed, 4 skipped.
