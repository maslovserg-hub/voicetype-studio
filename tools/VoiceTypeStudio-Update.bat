@echo off
chcp 65001 >nul
title Обновление VoiceType Studio
setlocal

rem Both the «Обновить» button in Настройки and a double-click on this
rem file run this script. System32 paths on purpose: Git ships its own
rem tar/curl that shadow the Windows ones and can't read a zip.
set "TAR=%SystemRoot%\System32\tar.exe"
set "CURL=%SystemRoot%\System32\curl.exe"
set "DIR=%APPDATA%\VoiceTypeStudio"
rem ASCII-only path: tar mangles Cyrillic in arguments (C:\Users\Сергей\...).
set "ZIP=%SystemDrive%\ProgramData\VoiceTypeStudio_update.zip"
set "URL=https://github.com/maslovserg-hub/voicetype-studio/releases/latest/download/VoiceTypeStudio_release.zip"

echo.
echo   Обновление VoiceType Studio
echo   ===========================
echo.

if not exist "%DIR%\VoiceTypeStudio.exe" (
    echo   Программа не найдена в папке:
    echo   %DIR%
    echo   Сначала установите её через VoiceTypeStudio-Setup.exe.
    goto fail
)

echo   Закрываю программу...
taskkill /IM VoiceTypeStudio.exe >nul 2>&1
set /a tries=0
:wait
tasklist /FI "IMAGENAME eq VoiceTypeStudio.exe" | find /I "VoiceTypeStudio.exe" >nul || goto stopped
set /a tries+=1
if %tries% geq 10 (
    taskkill /F /IM VoiceTypeStudio.exe >nul 2>&1
    goto stopped
)
timeout /t 1 /nobreak >nul
goto wait
:stopped
timeout /t 2 /nobreak >nul

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
timeout /t 3 /nobreak >nul
exit /b 0

:fail
echo.
echo   Обновление не выполнено. Ваши настройки и история не пострадали.
echo.
pause
exit /b 1
