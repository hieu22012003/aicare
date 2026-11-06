"""
Idempotent Configuration Generator and Validator for Windows Demo Release.
Ensures runtime/.env is initialized with valid defaults on first run without overwriting custom modifications.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def ensure_config(target_dir: Path | None = None) -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    if target_dir is None:
        target_dir = base_dir / "runtime"
    
    target_dir.mkdir(parents=True, exist_ok=True)
    target_env = target_dir / ".env"
    example_env = base_dir / "config" / "demo.example.env"

    if not example_env.exists():
        example_env = base_dir / ".env.example"

    if not target_env.exists():
        if example_env.exists():
            shutil.copy2(str(example_env), str(target_env))
            print(f"✅ Đã khởi tạo cấu hình ban đầu: {target_env}")
        else:
            # Fallback inline default config
            target_env.write_text(
                "DEMO_LOCAL=1\nPORTABLE_MODE=1\nWEB_HOST=127.0.0.1\nWEB_PORT=8080\n"
                "CAMERA_INDEX=0\nVOICE_SEMANTIC_BACKEND=local_rules\nEMAIL_ENABLED=false\n",
                encoding="utf-8"
            )
            print(f"✅ Đã tạo cấu hình mặc định: {target_env}")
    else:
        print(f"ℹ️ Cấu hình đã tồn tại, bảo toàn nội dung người dùng: {target_env}")

    return target_env


def validate_config(env_path: Path) -> bool:
    if not env_path.exists():
        print(f"❌ Không tìm thấy tệp cấu hình: {env_path}")
        return False
    
    # Simple validation of critical keys
    content = env_path.read_text(encoding="utf-8", errors="ignore")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    settings = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            settings[k.strip()] = v.strip()
    
    # Check web port
    port = settings.get("WEB_PORT", "8080")
    try:
        port_num = int(port)
        if not (1024 <= port_num <= 65535):
            print(f"⚠️ Cảnh báo: WEB_PORT ({port_num}) ngoài phạm vi khuyến nghị (1024-65535).")
    except ValueError:
        print(f"❌ Lỗi cấu hình: WEB_PORT không phải là số hợp lệ ({port}).")
        return False
    
    print(f"✅ Cấu hình hợp lệ: Host={settings.get('WEB_HOST', '127.0.0.1')}, Port={port}, Backend={settings.get('VOICE_SEMANTIC_BACKEND', 'local_rules')}")
    return True


if __name__ == "__main__":
    env_file = ensure_config()
    is_valid = validate_config(env_file)
    sys.exit(0 if is_valid else 1)

