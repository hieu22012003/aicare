@echo off
setlocal
cd /d "%~dp0"
title AI Rehabilitation Assistant - Setup v3
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\portable\setup.ps1"
set "SETUP_EXIT=%ERRORLEVEL%"
if not "%SETUP_EXIT%"=="0" echo Setup failed. See runtime\logs\setup.log for details.
if not "%AI_SETUP_NO_PAUSE%"=="1" pause
exit /b %SETUP_EXIT%
