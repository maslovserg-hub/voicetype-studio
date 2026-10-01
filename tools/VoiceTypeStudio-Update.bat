@echo off
chcp 866 >nul
title Обновление VoiceType Studio
setlocal

rem Saved in CP866, not UTF-8: with the UTF-8 code page cmd loses its place
rem in a UTF-8 file and runs fragments of lines (the v1.0.8 update hung after the
rem unpack). A single-byte code page keeps byte and character offsets equal.

rem Both the "Обновить" button in Настройки and a double-click on this
rem file run this script. System32 paths on purpose: Git ships its own
rem tar/curl that shadow the Windows ones and can't read a zip.
set "SYS=%SystemRoot%\System32"
set "TAR=%SYS%\tar.exe"
set "CURL=%SYS%\curl.exe"
set "DIR=%APPDATA%\VoiceTypeStudio"
rem ASCII-only path: tar mangles Cyrillic in arguments (C:\Users\Сергей\...).
set "ZIP=%SystemDrive%\ProgramData\VoiceTypeStudio_update.zip"
set "DEPS=%SystemDrive%\ProgramData\VoiceTypeStudio_deps.txt"
set "URL=https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/VoiceTypeStudio_release.zip"
set "SMALL_URL=https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/VoiceTypeStudio_update.zip"
set "DEPS_URL=https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/deps.txt"
set "SIZE=примерно 220 МБ"

echo.
echo   Обновление VoiceType Studio
echo   ===========================
echo.

rem Some installs were unpacked by hand with Explorer's "Extract all",
rem which adds one more VoiceTypeStudio\ level. Update them in place.
if not exist "%DIR%\VoiceTypeStudio.exe" if exist "%DIR%\VoiceTypeStudio\VoiceTypeStudio.exe" set "DIR=%DIR%\VoiceTypeStudio"

if not exist "%DIR%\VoiceTypeStudio.exe" (
    echo   Программа не найдена в папке:
    echo   %DIR%
    echo   Сначала установите её через VoiceTypeStudio-Setup.exe.
    goto fail
)

rem Ask nicely, then force: on WM_CLOSE the app hides to the tray instead
rem of exiting, so a soft taskkill alone leaves its files locked and the
rem unpack dies halfway. ping, not timeout - timeout needs a console
rem ("input redirection is not supported") and Git's find/tar/curl
rem shadow the Windows ones, hence %SYS% everywhere.
echo   Закрываю программу...
"%SYS%\taskkill.exe" /IM VoiceTypeStudio.exe >nul 2>&1
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
"%SYS%\taskkill.exe" /F /IM VoiceTypeStudio.exe >nul 2>&1
"%SYS%\ping.exe" -n 3 127.0.0.1 >nul

rem deps.txt fingerprints the libraries in _internal. If the new release
rem has the same ones as this install, only the small zip (exe + what
rem changes with it) is needed, not the full 220 MB.
"%CURL%" -L --fail -s -o "%DEPS%" "%DEPS_URL%"
if not errorlevel 1 if exist "%DIR%\deps.txt" (
    "%SYS%\fc.exe" /b "%DEPS%" "%DIR%\deps.txt" >nul 2>&1 && (
        set "URL=%SMALL_URL%"
        set "SIZE=примерно 50 МБ"
    )
)
del "%DEPS%" >nul 2>&1

echo   Скачиваю новую версию, %SIZE%...
echo.
"%CURL%" -L --fail --retry 2 -o "%ZIP%" "%URL%"
if errorlevel 1 (
    echo.
    echo   Не удалось скачать. Проверьте интернет и попробуйте снова.
    goto fail
)

echo.
echo   Распаковываю...
cd /d "%DIR%" || goto fail
"%TAR%" -xf "%ZIP%"
if errorlevel 1 (
    echo   Не удалось распаковать архив.
    goto fail
)
del "%ZIP%" >nul 2>&1

echo   Готово. Запускаю программу...
start "" "%DIR%\VoiceTypeStudio.exe"
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo   Обновление не выполнено. Ваши настройки и история не пострадали.
echo.
pause
exit /b 1
