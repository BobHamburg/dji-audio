@echo off
rem Gemeinsamer Starter fuer Windows: _start.bat <script.py> [Argumente ...]
rem Das Arbeitsverzeichnis bleibt, relative Pfade in Argumenten gelten also wie gewohnt.
rem Nimmt .venv\Scripts\python.exe, falls vorhanden, sonst py -3 bzw. python.

setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
for %%D in ("%~dp0..") do set "PROJEKT=%%~fD"
set "SCRIPT=%~1"
shift
set "ARGS="
:args
if "%~1"=="" goto :start
set ARGS=%ARGS% %1
shift
goto :args

:start
set "PY="
if exist "%PROJEKT%\.venv\Scripts\python.exe" (
    set PY="%PROJEKT%\.venv\Scripts\python.exe"
) else if /i "%SCRIPT%"=="transkript.py" (
    echo FEHLER: .venv fehlt. Bitte zuerst install_windows.bat ausfuehren.
    exit /b 1
) else (
    where py >nul 2>&1 && set "PY=py -3"
    if not defined PY set "PY=python"
)

%PY% "%PROJEKT%\%SCRIPT%"%ARGS%
exit /b %errorlevel%
