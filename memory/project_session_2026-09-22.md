---
name: VoiceType Studio session state — end of 2026-09-22
description: Релиз v1.0.3 — Perplexity SSL, история панелью в окне, папка скачивания, кнопка обновления. Три PATH/кодировочные ловушки Windows.
type: project
---
## Где остановились (вечер 22 сентября 2026)

**Тестов:** 217 passed, 3–4 skipped (было 210). Пропуски — живые тесты без ключей (`PPLX_API_KEY`, `TG_BOT_TOKEN`), non-ASCII `tmp_path`, плюс иногда флакает Tk-root в `test_message_widget` при полном прогоне (отдельно файл проходит).

**Релиз:** [v1.0.3](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.3) опубликован, ассеты: `VoiceTypeStudio_release.zip` (223 571 500 б, плоская структура), `VoiceTypeStudio-Setup.exe` (переиспользован из v1.0.2, лаунчер не менялся), `VoiceTypeStudio-Update.bat`.

**Установлено и проверено:** у пользователя 1.0.3 работает, «Конспект» через Perplexity отвечает на реальном ключе. У Марины обновление прошло успешно (подтвердил пользователь) — первый настоящий прогон .bat на чужой машине.

**Why:** день начался с баги «certificate has expired» на Конспекте, дальше пользователь добавил UX-правки, и в конце попросил кнопку обновления, чтобы обновляться без ручной возни.

**How to apply:** следующий релиз — поднять `__version__` в `core/version.py`, собрать, `Compress-Archive -Path dist\VoiceTypeStudio\*`, `gh release create vX.Y.Z`. Тег обязан совпадать с версией, иначе кнопка обновления не увидит новое.

## Что сделано

### Баг Perplexity (корень — не наш код)
- В хранилище промежуточных CA Windows лежит кросс-сертификат «ISRG Root X2 ← X1», просроченный 15.09.2025. OpenSSL предпочитает его свежему из цепочки сервера → `CERTIFICATE_VERIFY_FAILED`. Perplexity в августе 2026 переехал на новую цепочку LE, отсюда и вылезло.
- Новый `core/http.py` — `client_session()` с `ssl=certifi`. На него переведены все `aiohttp.ClientSession`: `core/llm/{base,anthropic,gemini}.py`, `core/downloader.py`, проверка Telegram-токена в `desktop/settings_window.py`. `certifi` в requirements; в exe кладёт штатный `hook-certifi`.

### UI
- Трей: один пункт «Транскриптор».
- `desktop/history_window.py` → `desktop/history_panel.py`: панель внутри окна, вкладки «Расшифровки» / «Скачанное», карточки (не CTkButton — он центрирует многострочный текст).
- Скачивания пишутся в новую таблицу `downloads` (`add_download`/`recent_downloads`); `transcriptions` и бот не тронуты.
- `Settings.download_dir` + блок «Скачивание»; нет папки → фолбэк в «Загрузки» с тостом.
- Настройки теперь дочернее окно Транскриптора (раньше открывались ЗА ним).

### Обновление
- `core/version.py`, `core/updater.py` (GitHub API `/releases/latest`, числовое сравнение), блок «Обновление» в настройках. Автопроверки при старте нет — сознательно, по просьбе пользователя.
- Вся работа в `tools/VoiceTypeStudio-Update.bat`: и кнопка, и двойной клик. Кнопка копирует .bat в `%TEMP%` (cmd читает .bat построчно по ходу, а скрипт перезаписывает папку, где лежит) и запускает через `os.startfile` (не `Popen` — `main.py` вешает `CREATE_NO_WINDOW` на все Popen).

## Грабли Windows (все пойманы живыми прогонами, не тестами)

1. **Git затеняет системные утилиты.** `find` из Git не понимает `/I` → проверка «программа ещё жива?» всегда отвечала «нет», распаковка шла при запущенном exe и половину файлов заменила, половину нет. `tar` из Git вообще не читает zip. Лечение: всё через `%SystemRoot%\System32\`, в тесте запрет на голые `find`/`taskkill`/`timeout`.
2. **Системный `tar` калечит кириллицу в аргументах** (`C:\Users\Сергей\…` → `??????`). Лечение: архив в `C:\ProgramData` (латиница), распаковка через `cd /d "%DIR%"` без передачи пути tar'у.
3. **`timeout` умирает без консоли** («input redirection is not supported») → пауза через `ping -n`.
4. **.bat обязан быть CRLF**, иначе cmd путается в метках. `.gitattributes` + проверка в тесте. Python при записи трижды ломал это (`\r\r\n`, потом LF) — писать `newline=""` и сразу проверять байты.
5. **CTkScrollableFrame пакует внутреннюю обёртку** → `pack(before=self._feed)` падает «isn't packed». Тело окна на `grid`.
6. **CTk `geometry()` в логических px, `winfo_*` в физических** → пересчёт через `_reverse_window_scaling`, иначе окно пухнет на HiDPI.
7. Приложение не закрывается по мягкому `taskkill`: на WM_CLOSE оно прячется в трей. Всегда нужен `/F`.

## Что осталось
- [ ] Ярлык в Пуск-меню (`create_start_menu_shortcut()`) — почему молча падает, так и не разобрались (висит с 06.09).
- [ ] Переименовать ассет `_tmp_setup_from_v1.0.0.exe` в релизе v1.0.1 (косметика).
- [ ] Кнопки «История» / «Настройки» проверены скриншотами из исходников; в собранном exe их живьём не кликали.
