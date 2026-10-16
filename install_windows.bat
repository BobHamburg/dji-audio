@echo off
rem Einrichtung fuer Windows: Python 3.9+ und FFmpeg (falls nicht vorhanden),
rem dann .venv anlegen und requirements.txt installieren.
rem Start per Doppelklick im Explorer oder in cmd: install_windows.bat

setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PROJEKT=%~dp0"
set "PY_EXE_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "PY_EXE_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe"

echo == Python suchen
call :finde_python
if not defined PY (
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo Python nicht gefunden, installiere mit winget ...
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    ) else (
        echo Python nicht gefunden, lade Installer von python.org ...
        powershell -NoProfile -Command "Invoke-WebRequest -Uri '%PY_EXE_URL%' -OutFile '%TEMP%\python-setup.exe'"
        "%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1
        del "%TEMP%\python-setup.exe" >nul 2>&1
    )
    call :finde_python
)
if not defined PY (
    echo FEHLER: Python 3.9+ konnte nicht installiert werden.
    goto :fehler
)
echo Python: !PY!

echo == FFmpeg suchen
where ffprobe >nul 2>&1
if !errorlevel! equ 0 (
    echo FFmpeg gefunden.
) else (
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        echo FFmpeg nicht gefunden, installiere mit winget ...
        winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
        echo Hinweis: FFmpeg ist erst in einem neuen Fenster im PATH.
    ) else (
        echo WARNUNG: FFmpeg fehlt. Download: https://www.gyan.dev/ffmpeg/builds/
        echo          und den bin-Ordner in den PATH aufnehmen.
    )
)

echo == Virtuelle Umgebung .venv
if not exist "%PROJEKT%.venv\Scripts\python.exe" (
    !PY! -m venv "%PROJEKT%.venv" || goto :fehler
)
"%PROJEKT%.venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fehler
"%PROJEKT%.venv\Scripts\python.exe" -m pip install -r "%PROJEKT%requirements.txt" || goto :fehler

echo.
echo Fertig. Startscripte liegen in Start\ (Doppelklick auf *.bat).
pause
exit /b 0

:fehler
echo.
echo Einrichtung abgebrochen.
pause
exit /b 1

rem Sucht Python 3.9+: erst den Launcher py, dann python im PATH,
rem dann den Standardort der Benutzerinstallation (PATH ist nach einer
rem Installation in diesem Fenster noch nicht aktualisiert).
:finde_python
set "PY="
set "PRUEF=import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
py -3 -c "%PRUEF%" >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python -c "%PRUEF%" >nul 2>&1 && (set "PY=python" & exit /b 0)
for %%P in ("%LOCALAPPDATA%\Programs\Python\Launcher\py.exe") do (
    if exist "%%~P" "%%~P" -3 -c "%PRUEF%" >nul 2>&1 && (set PY="%%~P" -3& exit /b 0)
)
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python312\python.exe") do (
    if exist "%%~P" set PY="%%~P"
)
exit /b 0
