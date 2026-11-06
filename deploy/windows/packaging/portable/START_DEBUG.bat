@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI REHABILITATION ^& SAFETY ASSISTANT - DEBUG MODE

echo ============================================================
echo   AI REHABILITATION ^& SAFETY ASSISTANT - DEBUG MODE
echo   Cua so dong lenh se giu nguyen de theo doi log chi tiet
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua tim thay moi truong Python ao .venv!
    echo Vui long chay SETUP.bat truoc.
    echo.
    pause
    exit /b 1
)

set PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
set MPLCONFIGDIR=%TEMP%\matplotlib-cache
set PYTHONUNBUFFERED=1
set PORTABLE_MODE=1

echo Dang khoi chay apps/desktop/main.py voi day du luong log ra man hinh...
echo.

".venv\Scripts\python.exe" apps/desktop/main.py

echo.
echo ============================================================
echo Ung dung da dong. Ma ket thuc: %errorlevel%
echo Xem them nhat ky tai: runtime\logs\app.log
echo ============================================================
echo.
pause
