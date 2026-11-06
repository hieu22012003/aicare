@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."
title "AI Care System - Start Demo"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\start_windows.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo ==================================================================
    echo [THONG BAO] He thong gap loi khi khoi dong!
    echo Vui long kiem tra nhat ky loi tai:
    echo  - Web Server: runtime\logs\web_server.log
    echo  - Ung dung  : runtime\logs\app.log
    echo ==================================================================
    echo.
    if not "%AI_DEMO_NO_PAUSE%"=="1" pause
)
exit /b %EXIT_CODE%
