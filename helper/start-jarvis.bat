@echo off
title MELYVN JARVIS helper
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 jarvis_helper.py
) else (
  python jarvis_helper.py
)
pause
