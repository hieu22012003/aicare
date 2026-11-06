@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI REHABILITATION - SYSTEM CHECK

echo ============================================================
echo   KIEM TRA HE THONG - AI REHABILITATION ^& SAFETY ASSISTANT
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua tim thay moi truong ao .venv!
    echo Vui long chay SETUP.bat truoc de thiet lap moi truong tu dong.
    echo.
    pause
    exit /b 1
)

set PORTABLE_MODE=1
".venv\Scripts\python.exe" scripts/check_system.py

echo.
pause
