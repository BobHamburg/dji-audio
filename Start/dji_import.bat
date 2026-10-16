@echo off
rem Startet dji_import.py (Argumente werden durchgereicht)
call "%~dp0_start.bat" dji_import.py %*
set "RC=%errorlevel%"
rem Bei Doppelklick im Explorer das Fenster offen lassen
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
