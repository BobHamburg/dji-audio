@echo off
rem Startet audio_info.py (Argumente werden durchgereicht)
call "%~dp0_start.bat" audio_info.py %*
set "RC=%errorlevel%"
rem Bei Doppelklick im Explorer das Fenster offen lassen
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
