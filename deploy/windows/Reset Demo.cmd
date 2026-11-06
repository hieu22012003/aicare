@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
title "AI Care System - Reset Demo"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=.venv\Scripts\python.exe"
) else (
    set "PY_EXE=python"
)

"%PY_EXE%" "%~dp0..\..\scripts\reset_demo.py"

echo.
if not "%AI_RESET_NO_PAUSE%"=="1" pause
exit /b %ERRORLEVEL%
