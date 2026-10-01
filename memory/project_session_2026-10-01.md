---
name: VoiceType Studio session state — 2026-10-01
description: v1.0.9 — окно по центру рабочей области, История/Настройки выезжающими панелями, маленькое обновление (~50 МБ) по отпечатку deps.txt.
type: project
---
## Что сделано (v1.0.9)
- **Транскриптор** ставится по центру рабочей области (`SPI_GETWORKAREA`, без панели задач) — `_place_centered` в `desktop/transcriptor_window.py`. geometry(): размер в логических px, позиция — в физических.
- **История** и **Настройки** — панели `place()` поверх ленты (`_slide`, ease-out 8 кадров × 15 мс), окно не меняет размер. `SettingsWindow` (CTkToplevel) → `SettingsPanel` (CTkFrame), `open_settings_window` удалён, `main._open_settings` открывает транскриптор и зовёт `open_settings(...)`. Открытие строки истории закрывает панель.
- Грабли: у CTkFrame с упакованными детьми `pack_propagate(False)` надо звать ДО упаковки детей (в конструкторе) — иначе reqwidth уже посчитан по содержимому и ширина из конструктора игнорируется. CTk `place()` не принимает width/height.
- **Маленькое обновление**: `tools/make_release.py` пишет `deps.txt` (sha256 по всем файлам dist кроме exe, `_internal/base_library.zip`, `_internal/tools/*.bat`), `VoiceTypeStudio_update.zip` (~50 МБ) и полный zip. `Update.bat` качает `latest/download/deps.txt`, `fc /b` с `%DIR%\deps.txt` → совпал — маленький zip, иначе полный. В каждый релиз класть все три файла.
- Почему 50, а не 1 МБ: в exe лежит PYZ со всем Python-кодом (вкл. torch). Вынести код приложения из PYZ — отдельная задача, если захочется ~1 МБ.

## Грабли релиза
- На remote был коммит 558dc7e (Update.bat в **CP866** + `chcp 65001`→`866`: под UTF-8 cmd сбивался и обновление 1.0.8 зависало). Перед сборкой релиза — `git fetch` и сверка с origin/main! .bat редактировать только через Python с `encode('cp866')` и CRLF.
- Python в heredoc с обычными строками ест `\f` (formfeed) в путях вида `%SYS%\fc.exe` — писать скрипт файлом с raw-строками.

## Релиз
[v1.0.9](https://github.com/maslovserg-hub/voicetype-studio/releases/tag/v1.0.9): release.zip 219 663 308 б, update.zip 49 836 717 б, deps.txt, Update.bat (CP866), Setup.exe (из dist_release, без изменений). Отпечаток совпал с установленной 1.0.8. Локально поставлено маленьким zip, затем новый Update.bat прогнан целиком: выбрал «примерно 50 МБ», скачал 47.5 МБ, распаковал, запустил.
- Пользователи 1.0.8: старый .bat всё ещё качает 220 МБ (у них нет deps.txt) — в заметках к релизу инструкция распаковать update.zip руками.

## Защита от трат на SpeechKit (в коде, в релиз ещё НЕ вошло → v1.0.10)
- Повод: счёт Яндекса 131,76 ₽ за 13 046 с аудио (~36 ₽/час) — язык «Другой» без субтитров слал в платный SpeechKit всё подряд.
- Коммит `1633042` (запушен в main): `_confirm_speechkit` в `desktop/transcriptor_window.py` — оценка > 5 ₽ (`SPEECHKIT_CONFIRM_RUB`, ~8 мин) → окно «Отправить?»; запись > 30 мин (`SPEECHKIT_MAX_S`) → отказ с ошибкой. Цена — `core/speechkit.py` `RUB_PER_SECOND` (калибровка по счёту, не прайс).
- Логи: `main.py` пишет `C:\VoiceTypeStudio\data\logs\app.log` (rotating 2 МБ × 3); искать `speechkit` и `LLM PAID` (`core/summarizer.py`).
- Окно подтверждения вживую не открывали, только тесты с подменой ответа. 205 тестов зелёные.
- **В следующую сборку (v1.0.10) обязательно включить это изменение** — собрать релиз через `tools/make_release.py` (все три файла: полный zip, update.zip, deps.txt), перед релизом `git fetch`.
