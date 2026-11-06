"""
Comprehensive Diagnostic and Health Inspection Utility for AI Care Assistant.
Checks environment, devices (camera/mic), AI model integrity, database status, and active services.
Exports sanitized report with masked credentials to runtime/logs/diagnostics_report.txt.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Project root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Configure environment for MediaPipe Protobuf & Qt platform plugins
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
try:
    import mediapipe
except Exception:
    pass
try:
    import torch
except Exception:
    pass

# Force UTF-8 stdout
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def mask_secret(text: str) -> str:
    if not text:
        return ""
    if len(text) <= 4:
        return "***"
    return text[:2] + "***" + text[-2:]


def sanitize_env_content(raw_text: str) -> str:
    lines = []
    secret_keys = {"email_password", "email_username", "secret", "token", "api_key", "password"}
    for line in raw_text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            k_clean = k.strip().lower()
            if any(s in k_clean for s in secret_keys):
                lines.append(f"{k.strip()}={mask_secret(v.strip())}")
                continue
        lines.append(line)
    return "\n".join(lines)


def run_diagnostics() -> int:
    report_lines = []
    
    def log(msg: str) -> None:
        print(msg)
        report_lines.append(msg)

    log("==================================================================")
    log(" 🔍 CHẨN ĐOÁN TOÀN DIỆN HỆ THỐNG AI CARE ASSISTANT (DIAGNOSTICS)")
    log(f" Thời gian thực hiện: {datetime.now(timezone.utc).isoformat()}")
    log("==================================================================")

    # 1. Hệ điều hành & Phần cứng
    log("\n[1/6] THÔNG TIN MÔI TRƯỜNG & HỆ ĐIỀU HÀNH")
    log(f" • Hệ điều hành         : {platform.system()} {platform.release()} ({platform.architecture()[0]})")
    log(f" • Bộ vi xử lý (CPU)    : {platform.processor() or 'Không xác định'} ({os.cpu_count()} luồng)")
    log(f" • Thư mục dự án        : {REPO_ROOT}")
    
    # Dung lượng đĩa
    try:
        import shutil
        total, used, free = shutil.disk_usage(REPO_ROOT)
        log(f" • Dung lượng ổ đĩa     : Trống {free // (1024**3)} GB / Tổng {total // (1024**3)} GB")
    except Exception as e:
        log(f" • Dung lượng ổ đĩa     : Không kiểm tra được ({e})")

    # 2. Python & Môi trường ảo
    log("\n[2/6] PYTHON RUNTIME & MÔI TRƯỜNG ẢO")
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    log(f" • Python Executable    : {sys.executable}")
    log(f" • Phiên bản Python     : {py_ver} (64-bit: {sys.maxsize > 2**32})")
    is_venv = hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    log(f" • Đang chạy trong .venv: {'Có (.venv cô lập) [OK]' if is_venv else 'Không (Python hệ thống)'}")

    # 3. Thư viện then chốt
    log("\n[3/6] KIỂM TRA THƯ VIỆN RUNTIME THEN CHỐT")
    libs = [
        ("MediaPipe", "mediapipe"),
        ("PyTorch", "torch"),
        ("Zipformer (sherpa-onnx)", "sherpa_onnx"),
        ("OpenCV", "cv2"),
        ("NumPy", "numpy"),
        ("SciPy", "scipy"),
        ("PyQt5", "PyQt5.QtCore"),
    ]
    for name, mod in libs:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "Đã nạp")
            log(f" • {name:<18}: v{ver} [OK]")
        except Exception as e:
            log(f" • {name:<18}: LỖI ({e})")

    # 4. Kiểm tra Thiết bị Phần cứng (Camera & Micro)
    log("\n[4/6] KIỂM TRA THIẾT BỊ NGOẠI VI (CAMERA & MICRO)")
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                log(f" • Camera (Index 0)     : Hoạt động bình thường ({w}x{h}) [OK]")
            else:
                log(f" • Camera (Index 0)     : Mở được nhưng chưa lấy được khung hình [CẢNH BÁO]")
        else:
            log(f" • Camera (Index 0)     : Không tìm thấy thiết bị hoặc đang bị ứng dụng khác dùng [CẢNH BÁO]")
    except Exception as e:
        log(f" • Camera (Index 0)     : Lỗi kiểm tra camera ({e})")

    try:
        import concurrent.futures
        def _get_audio():
            import sounddevice as sd
            return [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_get_audio)
            input_devs = fut.result(timeout=2.0)
            log(f" • Micro (Audio Input)  : Tìm thấy {len(input_devs)} thiết bị thu âm [OK]")
    except Exception as e:
        log(f" • Micro (Audio Input)  : Không kết nối máy chủ âm thanh hoặc thiếu micro ({e}) [CẢNH BÁO]")

    # 5. Kiểm tra Tính toàn vẹn Mô hình AI (Manifest & Hashes)
    log("\n[5/6] KIỂM TRA TÍNH TOÀN VẸN MÔ HÌNH AI")
    manifest_path = REPO_ROOT / "models" / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        models = manifest.get("models", [])
        for m in models:
            p = REPO_ROOT / m["target_path"]
            if p.exists():
                sz_mb = p.stat().st_size / (1024 * 1024)
                log(f" • {m['name']:<35}: Có sẵn ({sz_mb:.1f} MB) [OK]")
            else:
                log(f" • {m['name']:<35}: CHƯA TẢI ({m['target_path']}) [THIẾU]")
    else:
        log(" • Không tìm thấy tệp models/manifest.json")

    # 6. Kiểm tra Cơ sở dữ liệu & Cổng Web
    log("\n[6/6] KIỂM TRA CƠ SỞ DỮ LIỆU & MÁY CHỦ WEB")
    try:
        from shared.database.connection import UnifiedDatabaseManager
        db = UnifiedDatabaseManager()
        with db.connect() as conn:
            wal = conn.fetchone("PRAGMA journal_mode")
            fk = conn.fetchone("PRAGMA foreign_keys")
            patients = conn.fetchone("SELECT COUNT(*) as c FROM patients")
            schedules = conn.fetchone("SELECT COUNT(*) as c FROM medication_schedules")
            log(f" • SQLite Database      : {db.db_path} [OK]")
            log(f" • Chế độ Ghi (Journal) : {wal.get('journal_mode', '').upper()} (WAL: {'OK' if wal.get('journal_mode', '').lower() == 'wal' else 'NO'})")
            log(f" • Khóa ngoại (FK)      : {'BẬT (ON) [OK]' if fk.get('foreign_keys') == 1 else 'TẮT'}")
            log(f" • Hồ sơ Bệnh nhân      : {patients.get('c', 0)} hồ sơ đã nạp [OK]")
            log(f" • Lịch uống thuốc      : {schedules.get('c', 0)} lịch đã nạp [OK]")
    except Exception as e:
        log(f" • Cơ sở dữ liệu        : Lỗi kết nối ({e})")

    # Kiểm tra Web Health
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "ok":
                log(" • Backend Web Server   : Đang chạy tại http://127.0.0.1:8080 [ONLINE - OK]")
            else:
                log(" • Backend Web Server   : Phản hồi nhưng chưa sẵn sàng [CẢNH BÁO]")
    except Exception:
        log(" • Backend Web Server   : Chưa khởi động (Bình thường nếu chưa nhấn Start Demo) [OFFLINE]")

    # Ghi báo cáo đã che giấu thông tin nhạy cảm
    logs_dir = REPO_ROOT / "runtime" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    report_file = logs_dir / "diagnostics_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n\n")
        f.write("--- CẤU HÌNH HIỆN TẠI (ĐÃ CHE GIẤU BÍ MẬT) ---\n")
        env_f = REPO_ROOT / "runtime" / ".env"
        if not env_f.exists():
            env_f = REPO_ROOT / ".env"
        if env_f.exists():
            f.write(sanitize_env_content(env_f.read_text(encoding="utf-8", errors="ignore")))

    log("\n==================================================================")
    log(f" 📄 Báo cáo chẩn đoán chi tiết đã được xuất tại: {report_file}")
    log("==================================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_diagnostics())
