@echo off
chcp 65001 >nul
cd /d "%~dp0"
title AI REHABILITATION ^& SAFETY ASSISTANT

echo ============================================================
echo   AI REHABILITATION ^& SAFETY ASSISTANT
echo   Tro Ly Phuc Hoi Chuc Nang ^& An Toan Nguoi Cao Tuoi
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua tim thay moi truong Python ao .venv!
    echo.
    echo Vui long chay file SETUP.bat truoc de he thong tu dong cai dat.
    echo.
    pause
    exit /b 1
)

set PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
set MPLCONFIGDIR=%TEMP%\matplotlib-cache
set PORTABLE_MODE=1

echo Dang khoi dong giao dien ung dung...
echo Nhat ky hoat dong duoc luu tai: runtime\logs\app.log
echo.

".venv\Scripts\python.exe" apps/desktop/main.py

if errorlevel 1 (
    echo.
    echo ============================================================
    echo [THONG BAO] Ung dung da ket thuc voi ma loi: %errorlevel%
    echo.
    echo Vui long kiem tra nhat ky loi tai: runtime\logs\app.log
    echo Hoac chay START_DEBUG.bat de xem chi tiet thong bao loi.
    echo ============================================================
    echo.
    pause
)
