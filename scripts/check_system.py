"""
Pre-Flight Diagnostic and System Integrity Verification Script
AI Rehabilitation & Safety Assistant (Windows Portable Edition)
"""
from __future__ import annotations

import os
import sys
import platform
import tempfile
import time
from pathlib import Path

# Fix environment variables before importing ML frameworks
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Pre-initialize via config to prevent Windows C-runtime DLL collision
from shared import config

# Force UTF-8 stdout
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_checks() -> int:
    critical_errors = []
    warnings = []

    print("============================================================")
    print("      AI REHABILITATION & SAFETY ASSISTANT — PRE-FLIGHT CHECK")
    print("============================================================")
    print()

    # 1. SYSTEM ENVIRONMENT
    print("[1/5] KIỂM TRA MÔI TRƯỜNG HỆ THỐNG (SYSTEM)")
    os_name = platform.system()
    os_release = platform.release()
    bitness = platform.architecture()[0]

    if os_name == "Windows":
        print(f"  • Hệ điều hành        : Windows {os_release} ({bitness}) [OK]")
    else:
        print(f"  • Hệ điều hành        : {os_name} {os_release} ({bitness}) [CẢNH BÁO: Yêu cầu Windows]")
        warnings.append(f"Không chạy trên hệ điều hành Windows ({os_name})")

    py_version = sys.version_info
    py_ver_str = f"{py_version.major}.{py_version.minor}.{py_version.micro}"
    if py_version.major == 3 and py_version.minor in (11, 12):
        print(f"  • Phiên bản Python    : Python {py_ver_str} (64-bit) [OK]")
    elif py_version.major == 3 and py_version.minor == 10:
        print(f"  • Phiên bản Python    : Python {py_ver_str} [TƯƠNG THÍCH]")
    else:
        print(f"  • Phiên bản Python    : Python {py_ver_str} [LỖI: Cần Python 3.11 hoặc 3.12 64-bit]")
        critical_errors.append(f"Python {py_ver_str} không tương thích (cần Python 3.11 hoặc 3.12 64-bit)")

    # Test writable runtime directory
    runtime_dir = PROJECT_ROOT / "runtime"
    if not runtime_dir.exists():
        runtime_dir = PROJECT_ROOT
    test_file = runtime_dir / ".write_test.tmp"
    try:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("test")
        if test_file.exists():
            test_file.unlink()
        print(f"  • Thư mục ghi dữ liệu : {runtime_dir} [OK]")
    except Exception as e:
        print(f"  • Thư mục ghi dữ liệu : {runtime_dir} [LỖI: Không thể ghi - {e}]")
        critical_errors.append(f"Thư mục ghi dữ liệu không có quyền ghi: {e}")

    print()

    # 2. RUNTIME DEPENDENCIES
    print("[2/5] KIỂM TRA THƯ VIỆN RUNTIME (DEPENDENCIES)")
    
    # PyTorch
    try:
        import torch
        print(f"  • PyTorch             : v{torch.__version__} (Device: {'CUDA' if torch.cuda.is_available() else 'CPU'}) [OK]")
    except Exception as e:
        print(f"  • PyTorch             : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện PyTorch")

    # MediaPipe
    try:
        import mediapipe as mp
        print(f"  • MediaPipe           : v{mp.__version__} [OK]")
    except Exception as e:
        print(f"  • MediaPipe           : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện MediaPipe")

    # OpenCV
    try:
        import cv2
        has_face_module = hasattr(cv2, "face")
        if has_face_module:
            print(f"  • OpenCV Contrib      : v{cv2.__version__} (cv2.face included) [OK]")
        else:
            print(f"  • OpenCV              : v{cv2.__version__} [LỖI: Thiếu module opencv-contrib-python (cv2.face)]")
            critical_errors.append("Cài đặt opencv-python thay vì opencv-contrib-python (thiếu cv2.face)")
    except Exception as e:
        print(f"  • OpenCV              : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện OpenCV")

    # NumPy
    try:
        import numpy as np
        print(f"  • NumPy               : v{np.__version__} [OK]")
    except Exception as e:
        print(f"  • NumPy               : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện NumPy")

    # PyQt5
    try:
        import PyQt5.QtCore
        import PyQt5.QtWidgets
        from PyQt5.QtCore import QT_VERSION_STR
        print(f"  • PyQt5               : v{QT_VERSION_STR} [OK]")
    except Exception as e:
        print(f"  • PyQt5               : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện PyQt5")

    # Pillow
    try:
        import PIL
        print(f"  • Pillow              : v{PIL.__version__} [OK]")
    except Exception as e:
        print(f"  • Pillow              : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện Pillow")

    # python-dotenv & psutil
    try:
        import dotenv
        import psutil
        print(f"  • Dotenv & Psutil     : [OK]")
    except Exception as e:
        print(f"  • Dotenv / Psutil     : [LỖI: {e}]")
        critical_errors.append("Thiếu thư viện python-dotenv hoặc psutil")

    # SQLite3 & Cơ sở dữ liệu quan hệ (Unified Database)
    try:
        from shared.database.connection import UnifiedDatabaseManager
        from shared.database.migrator import Migrator
        import sqlite3
        sqlite_ver = getattr(sqlite3, "sqlite_version", "unknown")
        db = UnifiedDatabaseManager()
        migrator = Migrator(db)
        migrator.apply_all()
        with db.connect() as conn:
            p_cnt = conn.fetchone("SELECT COUNT(*) as cnt FROM patients")["cnt"]
            m_cnt = conn.fetchone("SELECT COUNT(*) as cnt FROM medications")["cnt"]
            u_cnt = conn.fetchone("SELECT COUNT(*) as cnt FROM users")["cnt"]
        print(f"  • SQLite3 & CSDL      : v{sqlite_ver} (Bệnh nhân: {p_cnt}, Thuốc: {m_cnt}, Tài khoản: {u_cnt}) [OK]")
    except Exception as e:
        print(f"  • SQLite3 & CSDL      : [LỖI: {e}]")
        critical_errors.append(f"Lỗi cơ sở dữ liệu SQLite3: {e}")

    print()

    # 3. AI MODELS & STATIC ASSETS
    print("[3/5] KIỂM TRA MÔ HÌNH AI & TÀI NGUYÊN (MODELS & ASSETS)")
    models_to_check = [
        ("Face Detector (PB)", PROJECT_ROOT / "features" / "face_monitoring" / "standalone" / "models" / "opencv_face_detector_uint8.pb", True),
        ("Face Detector (TXT)", PROJECT_ROOT / "features" / "face_monitoring" / "standalone" / "models" / "opencv_face_detector.pbtxt", True),
        ("Emotion ExpressionNet", PROJECT_ROOT / "features" / "emotion" / "model" / "models" / "expression_net.pth", True),
        ("Age Net Caffe", PROJECT_ROOT / "features" / "face_monitoring" / "standalone" / "models" / "age_net.caffemodel", False),
        ("Gender Net Caffe", PROJECT_ROOT / "features" / "face_monitoring" / "standalone" / "models" / "gender_net.caffemodel", False),
        ("OpenCV Asset Logo", PROJECT_ROOT / "features" / "face_monitoring" / "standalone" / "open-cv.png", False),
    ]

    for label, path, is_required in models_to_check:
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB" if size_mb >= 1.0 else f"{path.stat().st_size / 1024:.1f} KB"
            print(f"  • {label:<22}: {size_str:<10} [OK]")
        else:
            if is_required:
                print(f"  • {label:<22}: [LỖI: Không tìm thấy tại {path}]")
                critical_errors.append(f"Thiếu file mô hình bắt buộc: {path.name}")
            else:
                print(f"  • {label:<22}: [CẢNH BÁO: Tùy chọn, không tìm thấy]")

    print()

    # 4. HARDWARE CAMERA CHECK
    print("[4/5] KIỂM TRA PHẦN CỨNG CAMERA (HARDWARE)")
    camera_found = False
    camera_details = "Không phát hiện camera phần cứng"
    cap = None
    try:
        # Test DirectShow first on Windows, then default
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(0)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(0)

        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                h, w = frame.shape[:2]
                camera_found = True
                camera_details = f"Đã kết nối Webcam 0 ({w}x{h})"
            else:
                camera_details = "Webcam 0 mở được nhưng không đọc được frame"
    except Exception as e:
        camera_details = f"Lỗi truy cập camera: {e}"
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    if camera_found:
        print(f"  • Camera              : {camera_details} [OK]")
    else:
        print(f"  • Camera              : [CẢNH BÁO: {camera_details}]")
        print(f"                          (Hệ thống sẽ tự động dùng chế độ Camera Mô Phỏng khi chạy)")
        warnings.append(f"Camera: {camera_details} (Chế độ mô phỏng sẽ kích hoạt)")

    print()

    # 5. GMAIL & CONFIGURATION
    print("[5/5] KIỂM TRA CẤU HÌNH GMAIL CẢNH BÁO (CONFIG)")
    env_file = PROJECT_ROOT / "runtime" / ".env"
    if not env_file.exists():
        env_file = PROJECT_ROOT / ".env"

    gmail_configured = False
    gmail_enabled = False
    email_user = ""

    if env_file.exists():
        try:
            from dotenv import dotenv_values
            env_vals = dotenv_values(str(env_file))
            gmail_enabled = env_vals.get("EMAIL_ENABLED", "false").lower() in ("true", "1", "yes")
            email_user = env_vals.get("EMAIL_USERNAME", "").strip()
            email_pwd = env_vals.get("EMAIL_PASSWORD", "").strip()
            if email_user and email_pwd:
                gmail_configured = True
        except Exception:
            pass

    if gmail_configured:
        status_str = f"Sẵn sàng gửi từ {email_user}" if gmail_enabled else f"Đã cấu hình nhưng đang tắt (EMAIL_ENABLED=false)"
        print(f"  • Gmail Alert         : {status_str} [OK]")
    else:
        print("  • Gmail Alert         : [CẢNH BÁO: Chưa cấu hình thông tin gửi Gmail trong .env]")
        print("                          (Cảnh báo ngã vẫn ghi nhận bình thường, chỉ bỏ qua gửi email)")
        warnings.append("Gmail chưa cấu hình tài khoản gửi (EMAIL_USERNAME / EMAIL_PASSWORD)")

    print()
    print("============================================================")
    if not critical_errors:
        print("                   KẾT QUẢ: HỆ THỐNG ĐÃ SẴN SÀNG!")
        print("                   READY TO START — RUN START.bat")
        print("============================================================")
        if warnings:
            print()
            print("CÁC LƯU Ý KHÔNG ẢNH HƯỞNG:")
            for w in warnings:
                print(f"  [i] {w}")
        return 0
    else:
        print("                   KẾT QUẢ: PHÁT HIỆN LỖI NGHIÊM TRỌNG!")
        print("============================================================")
        print()
        print("DANH SÁCH LỖI CẦN KHẮC PHỤC TRƯỚC KHI KHỞI CHẠY:")
        for err in critical_errors:
            print(f"  [X] {err}")
        print()
        print("Vui lòng chạy lại SETUP.bat để tự động khắc phục các phụ thuộc.")
        return 1


if __name__ == "__main__":
    sys.exit(run_checks())
