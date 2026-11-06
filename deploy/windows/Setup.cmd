@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title "AI Care System - Setup"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ==================================================================
echo   AI REHABILITATION ^& SAFETY ASSISTANT - THIET LAP HE THONG
echo ==================================================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows.ps1"
set "SETUP_EXIT=%ERRORLEVEL%"

if not "%SETUP_EXIT%"=="0" (
    echo.
    echo ==================================================================
    echo [LOI] Qua trinh thiet lap chua hoan tat!
    echo Vui long kiem tra nhat ky chi tiet tai: runtime\logs\setup.log
    echo ==================================================================
)

echo.
if not "%AI_SETUP_NO_PAUSE%"=="1" pause
exit /b %SETUP_EXIT%
