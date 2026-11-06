@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
title AI REHABILITATION ASSISTANT — ĐÓNG GÓI BẢN PORTABLE WINDOWS
color 0B

echo ============================================================
echo   BUILD WINDOWS PORTABLE AUTO-SETUP PACKAGE
echo   Đóng gói ứng dụng sang thư mục release/ và file ZIP
echo ============================================================
echo.

set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    for %%P in (python3.12 python3.11 py python) do (
        if not defined PYTHON_EXE (
            %%P -c "import sys; sys.exit(0 if sys.version_info.major==3 else 1)" >nul 2>nul
            if not errorlevel 1 set "PYTHON_EXE=%%P"
        )
    )
)

if not defined PYTHON_EXE (
    echo [LỖI] Không tìm thấy Python để chạy script đóng gói!
    pause
    exit /b 1
)

"%PYTHON_EXE%" deploy\windows\packaging\portable\build_portable.py

if errorlevel 1 (
    echo.
    echo [LỖI] Quá trình đóng gói thất bại!
    pause
    exit /b 1
)

echo.
echo ============================================================
echo ĐÓNG GÓI THÀNH CÔNG!
echo Kiểm tra sản phẩm tại thư mục: release\
echo ============================================================
echo.
pause

