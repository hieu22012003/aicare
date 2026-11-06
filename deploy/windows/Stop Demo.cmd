@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
title "AI Care System - Stop Demo"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\stop_windows.ps1"

timeout /t 2 >nul
exit /b 0
