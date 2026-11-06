"""
Reset Demo Utility for AI Care & Rehabilitation System.
Creates an automatic backup before resetting, re-applies migrations, and seeds standard demo actors.
Requires interactive confirmation unless --yes / --force is passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import timezone, datetime
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

# Ensure project root in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from shared.database.backup_restore import BackupRestoreService


def reset_demo(confirm: bool = False, with_demo_patients: bool = False) -> int:
    print("==================================================================")
    print(" ⚠️  ĐẶT LẠI DỮ LIỆU HỆ THỐNG (RESET SYSTEM) — HỆ THỐNG AI CARE")
    print("==================================================================")
    print("Thao tác này sẽ:")
    print(" 1. Tự động sao lưu toàn bộ dữ liệu hiện tại vào thư mục runtime/backups/")
    print(" 2. Đặt lại toàn bộ cơ sở dữ liệu về trạng thái mẫu ban đầu:")
    print("    - Cụ Hoàng Anh (patient_001), Cụ Mai Hương (patient_002)")
    print("    - Người nhà: Nguyễn Văn Tuấn (tuan.caregiver@example.com / demo)")
    print("    - Người nhà: Lê Thị Lan (lan.caregiver@example.com / demo)")
    print("    - Bác sĩ: BS. Nguyễn Văn An (dr.an@laokhoa.example.com / demo)")
    print("    - Quản trị viên: admin (admin / admin123)")
    print(" 3. Không xóa file cấu hình (.env) hoặc mô hình AI đã tải.")
    print("==================================================================")

    if not confirm:
        try:
            choice = input("\nBạn có chắc chắn muốn tiếp tục không? (nhập 'yes' hoặc 'y' để xác nhận): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n❌ Thao tác đã bị hủy bởi người dùng.")
            return 1
        if choice not in ("y", "yes"):
            print("❌ Đã hủy bỏ thao tác đặt lại dữ liệu.")
            return 1

    db = UnifiedDatabaseManager()
    db_file = Path(db.db_path)
    
    # 1. Tạo backup tự động
    backup_dir = REPO_ROOT / "runtime" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"care_demo_backup_{timestamp}.sql"

    print(f"\n[1/3] Đang sao lưu cơ sở dữ liệu hiện tại...")
    try:
        lines = BackupRestoreService.create_sql_dump(db, backup_file)
        print(f"      ✅ Đã lưu {lines} dòng SQL tại: {backup_file}")
    except Exception as e:
        print(f"      ⚠️ Không thể tạo SQL dump ({e}). Sao lưu trực tiếp tệp SQLite...")
        if db_file.exists():
            raw_backup = backup_dir / f"care_demo_{timestamp}.sqlite3"
            shutil.copy2(str(db_file), str(raw_backup))
            print(f"      ✅ Đã lưu tệp nhị phân tại: {raw_backup}")

    # 2. Xóa và áp dụng lại Migrations
    print("\n[2/3] Đang áp dụng lại cấu trúc bảng chuẩn 3NF...")
    # Xóa file cũ để cấu trúc hoàn toàn sạch sẽ
    if db_file.exists() and str(db.db_path) != ":memory:":
        try:
            db_file.unlink()
        except Exception:
            pass

    migrator = Migrator(db)
    applied = migrator.apply_all()
    print(f"      ✅ Đã thực thi {len(applied)} tệp migration thành công.")

    # 3. Nạp tài khoản quản trị viên (hoặc dữ liệu mẫu nếu yêu cầu)
    if with_demo_patients:
        print("\n[3/3] Đang nạp dữ liệu mẫu thử nghiệm (Demo Patients)...")
        importer = DataImporter(db)
        stats = importer.import_all(seed_sample_data=True)
        print("      ✅ Hoàn tất nạp dữ liệu mẫu:")
        print(f"         - Bệnh nhân (patients): {stats.get('patients', 0)} hồ sơ")
        print(f"         - Người nhà (caregivers): {stats.get('caregivers', 0)} tài khoản")
        print(f"         - Bác sĩ (clinicians): {stats.get('clinicians', 0)} tài khoản")
    else:
        print("\n[3/3] Đang khởi tạo tài khoản Quản trị viên duy nhất...")
        importer = DataImporter(db)
        stats = importer.import_all(seed_sample_data=False)
        from scripts.clean_test_accounts import clean_test_accounts
        clean_test_accounts()
        print("      ✅ Đã khởi tạo thành công tài khoản Quản trị viên duy nhất:")
        print("         - Tài khoản: admin (hoặc admin@unifiedcare.local)")
        print("         - Mật khẩu: admin123")
        print("         - Hồ sơ người già / người nhà: Đã dọn dẹp sạch để sẵn sàng đăng ký mới")

    print("\n==================================================================")
    print("🎉 ĐẶT LẠI DỮ LIỆU THÀNH CÔNG!")
    print(f"📁 Tệp sao lưu khôi phục: {backup_file}")
    print("🚀 Bạn có thể khởi động lại hệ thống bằng deploy/windows/Start Demo.cmd")
    print("==================================================================\n")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset AI Care Demo to pristine state with automated backup")
    parser.add_argument("-y", "--yes", action="store_true", help="Confirm reset non-interactively")
    parser.add_argument("-f", "--force", action="store_true", help="Force reset non-interactively")
    parser.add_argument("--with-demo-patients", action="store_true", help="Seed demo test patients and caregivers")
    args = parser.parse_args()

    sys.exit(reset_demo(confirm=args.yes or args.force, with_demo_patients=args.with_demo_patients))

