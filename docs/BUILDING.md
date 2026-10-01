# Building VoiceType Studio

Этот файл описывает как собрать VoiceType Studio в дистрибутив (один exe + zip + launcher) и опубликовать релиз.

## Требования

- Windows 10/11 x64
- Python 3.12 (та же что в venv проекта)
- ~10 ГБ свободного места (PyInstaller-кэш + dist + временные)
- Все зависимости из `requirements.txt` установлены в активный venv

## Полный процесс

```powershell
# 1. Активировать venv
cd c:\Projects\voicetype-studio
.\venv\Scripts\Activate.ps1

# 2. Установить ВСЕ зависимости (включая heavy ML)
pip install -r requirements.txt
pip install pyinstaller

# 3. Прогнать тесты
pytest tests/ -v

# 4. Собрать main app (~10-20 мин на холодную)
pyinstaller VoiceTypeStudio.spec

# 5. Собрать launcher (~30 сек)
cd launcher
pyinstaller Launcher.spec
cd ..
```

После сборки:

```
dist/
├── VoiceTypeStudio/                    # ~280 МБ — пакуется в zip для GitHub Releases
│   ├── VoiceTypeStudio.exe
│   ├── _internal/                      # все .pyd, .dll, торчевые файлы
│   └── ...
└── launcher/
    └── VoiceTypeStudio-Setup.exe       # ~5 МБ — единый файл для распространения
```

## Что бандлится, что нет

**В zip входит:**
- Python 3.12 runtime
- torch + torchaudio CPU (~150 МБ)
- gigaam (без модели — она качается отдельно)
- aiohttp
- customtkinter + tkinterdnd2
- pydub, yt-dlp, sounddevice, pynput, pystray, Pillow
- onnxruntime (используется gigaam как backend)

**НЕ входит:**
- GigaAM v3_e2e_ctc модель (~440 МБ) — лежит в `C:\gigaam_cache\v3_e2e_ctc.ckpt`. Если файл уже там — переиспользуется, не качается. На свежей машине `gigaam.load_model` скачает её автоматически при первой транскрипции.
- API-ключи провайдеров — пользователь вводит их в Настройках.

## Релиз

```powershell
# 1. Запаковать dist\VoiceTypeStudio (плоско, без папки верхнего уровня)
python tools\make_release.py
```

Скрипт кладёт в корень репо:
- `VoiceTypeStudio_release.zip` — полная установка (~220 МБ);
- `VoiceTypeStudio_update.zip` — только exe, `base_library.zip` и
  `Update.bat` (~50 МБ);
- `deps.txt` — отпечаток остальных файлов `_internal` (torch и прочие
  библиотеки). Он же лежит внутри обоих zip.

`VoiceTypeStudio-Update.bat` (и кнопка «Обновить») скачивает `deps.txt`
последнего релиза и сравнивает с установленным: совпал — качает
маленький zip, нет — полный. Поэтому в каждый релиз выкладывать **все
три файла**, даже если библиотеки менялись.

```powershell
# 2. Тег, пуш, релиз
git tag v1.0.9
git push origin main v1.0.9
gh release create v1.0.9 --title v1.0.9 --notes-file notes.md `
  VoiceTypeStudio_release.zip VoiceTypeStudio_update.zip deps.txt `
  tools\VoiceTypeStudio-Update.bat dist_release\VoiceTypeStudio-Setup.exe
```

Launcher (`VoiceTypeStudio-Setup.exe`) качает
`releases/latest/download/VoiceTypeStudio_release.zip` — пересобирать его
нужно только когда меняется сам `launcher/launcher.py`.

## Размер итогового exe

Целимся в ≤ 300 МБ. Если превысит:
- проверить excludes в `VoiceTypeStudio.spec` (torchvision/torchtext должны быть выключены)
- посмотреть `dist/VoiceTypeStudio/_internal/` через PowerShell:
  ```
  Get-ChildItem .\dist\VoiceTypeStudio\_internal -Recurse | Measure-Object -Property Length -Sum
  ```
- крупнейшие виновники обычно: `torch/lib/`, `mkl_*.dll`. Их вычистка — отдельная задача.

## Smoke-тест на чистой машине

В идеале — VirtualBox с чистой Windows 10:

1. Скопировать `VoiceTypeStudio-Setup.exe` → запустить
2. Должен скачать zip и распаковать в `%APPDATA%\VoiceTypeStudio\`
3. Появляется tray-иконка
4. Right Ctrl → диктовка работает (первый раз качает GigaAM)
5. ПКМ в трей → «Транскриптор» → drag&drop mp3 → транскрипция работает
6. Настройки → ввести Perplexity ключ → нажать «📋 Тезисы» в окне → ответ от Perplexity

Если на чистой машине не запускается — обычно не хватает Visual C++ Redistributable. Документировать в `README.md`.

## Известные грабли

- **GigaAM на не-x64 Windows** — не поддерживается, gigaam требует AVX2.
- **Anthropic / Gemini из российских IP** — могут не отвечать. Это пользовательская проблема, проверить смежно с Perplexity (всегда работает).
- **ffmpeg в PATH** — обязателен на хост-машине; PyInstaller его не бандлит. Если у пользователя нет ffmpeg — конвертация падает с понятной ошибкой.
- **ANTIVIRUS false-positives на launcher** — частая боль для PyInstaller-onefile. На некоторых машинах Defender помечает unsigned exe как PUA. Решается code-signing (платная подпись) либо инструкцией «добавьте в исключения».
