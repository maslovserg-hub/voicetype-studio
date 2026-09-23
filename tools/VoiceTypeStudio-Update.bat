@echo off
chcp 65001 >nul
title Обновление VoiceType Studio
setlocal

rem Both the «Обновить» button in Настройки and a double-click on this
rem file run this script. System32 paths on purpose: Git ships its own
rem tar/curl that shadow the Windows ones and can't read a zip.
set "SYS=%SystemRoot%\System32"
set "TAR=%SYS%\tar.exe"
set "CURL=%SYS%\curl.exe"
set "DIR=%APPDATA%\VoiceTypeStudio"
rem ASCII-only path: tar mangles Cyrillic in arguments (C:\Users\Сергей\...).
set "ZIP=%SystemDrive%\ProgramData\VoiceTypeStudio_update.zip"
set "URL=https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/VoiceTypeStudio_release.zip"

echo.
echo   Обновление VoiceType Studio
echo   ===========================
echo.

rem Some installs were unpacked by hand with Explorer's «Extract all»,
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
rem unpack dies halfway. ping, not timeout — timeout needs a console
rem ("input redirection is not supported") and Git's find/tar/curl
rem shadow the Windows ones, hence %SYS% everywhere.
echo   Закрываю программу...
"%SYS%\taskkill.exe" /IM VoiceTypeStudio.exe >nul 2>&1
"%SYS%\ping.exe" -n 4 127.0.0.1 >nul
"%SYS%\taskkill.exe" /F /IM VoiceTypeStudio.exe >nul 2>&1
"%SYS%\ping.exe" -n 3 127.0.0.1 >nul

echo   Скачиваю новую версию, примерно 220 МБ...
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
