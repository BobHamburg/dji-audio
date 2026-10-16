@echo off
rem Startet mov_extract.py (Argumente werden durchgereicht)
call "%~dp0_start.bat" mov_extract.py %*
set "RC=%errorlevel%"
rem Bei Doppelklick im Explorer das Fenster offen lassen
echo %cmdcmdline% | find /i "%~0" >nul && pause
exit /b %RC%
