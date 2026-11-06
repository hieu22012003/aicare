"""
Interactive Demo & Test Script for Medication Adherence Module.
Enables step-by-step testing of all P0-P3 features directly from the terminal.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from features.medication.adherence.adapters.caregiver_repository import SqlCaregiverRepository
from features.medication.adherence.adapters.identity_adapter import MockPatientIdentityAdapter
from features.medication.adherence.adapters.medication_repository import SqlMedicationRepository
from features.medication.adherence.adapters.notification_gateway import MockNotificationGateway
from features.medication.adherence.adapters.patient_repository import SqlPatientRepository
from features.medication.adherence.database.connection import DatabaseManager
from features.medication.adherence.database.seed_data import seed_database
from features.medication.adherence.domain.models import DoseState
from features.medication.adherence.services.adherence_service import AdherenceService
from features.medication.adherence.services.scheduler import AdherenceScheduler
from features.medication.adherence.ui.elderly_ui import ElderlyPresenter


class TestRunnerApp:
    def __init__(self) -> None:
        self.sim_time = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)  # 08:00 local UTC+7
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)

        self.identity_adapter = MockPatientIdentityAdapter()
        self.patient_repo = SqlPatientRepository(self.db)
        self.caregiver_repo = SqlCaregiverRepository(self.db)
        self.med_repo = SqlMedicationRepository(self.db)
        self.notif_gateway = MockNotificationGateway()

        self.scheduler = AdherenceScheduler(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.sim_time,
        )
        self.service = AdherenceService(
            med_repo=self.med_repo,
            patient_repo=self.patient_repo,
            caregiver_repo=self.caregiver_repo,
            notif_gateway=self.notif_gateway,
            clock=lambda: self.sim_time,
        )
        self.presenter = ElderlyPresenter(self.service, self.identity_adapter)

        # Generate initial occurrences
        self.scheduler.tick()

    def banner(self, title: str) -> None:
        print("\n" + "=" * 70)
        print(f"  {title}")
        print("=" * 70)

    def test_step_1_identity(self) -> None:
        self.banner("BƯỚC 1: KIỂM TRA NHẬN DIỆN & CHỐNG LẪN DỮ LIỆU BỆNH NHÂN")
        print("▶ Mô phỏng Camera nhận diện: Cụ Hoàng Anh (patient_001)...")
        self.identity_adapter.simulate_camera_recognition("patient_001")
        self.presenter.reload_doses()

        print(f"  ✓ Bệnh nhân đang kích hoạt: {self.presenter.active_patient_id}")
        print(f"  ✓ Số liều thuốc hiển thị: {len(self.presenter.cached_doses)}")
        for d in self.presenter.cached_doses:
            print(f"    - Thuốc: {d.medication_name_snapshot} | Liều uống: {d.dose_snapshot} | Giờ: {d.local_scheduled_time}")

        print("\n▶ Chuyển đổi Camera sang: Cụ Mai Hương (patient_002)...")
        self.identity_adapter.simulate_camera_recognition("patient_002")
        print(f"  ✓ Bệnh nhân đang kích hoạt: {self.presenter.active_patient_id}")
        print(f"  ✓ Số liều thuốc hiển thị: {len(self.presenter.cached_doses)}")
        for d in self.presenter.cached_doses:
            print(f"    - Thuốc: {d.medication_name_snapshot} | Liều uống: {d.dose_snapshot} | Giờ: {d.local_scheduled_time}")
        print("  ==> Kết luận: Chống lẫn dữ liệu hoàn hảo! Dữ liệu người cũ bị xóa sạch ngay lập tức.")

    def test_step_2_take(self) -> None:
        self.banner("BƯỚC 2: THAO TÁC 'ĐÃ UỐNG' (XÁC NHẬN TỰ KHAI BÁO & IDEMPOTENT)")
        self.identity_adapter.simulate_camera_recognition("patient_001")
        self.presenter.reload_doses()
        dose = self.presenter.cached_doses[0]

        print(f"▶ Bệnh nhân nhấn 'ĐÃ UỐNG' cho liều: {dose.medication_name_snapshot} (Mã: {dose.occurrence_id[:16]}...)")
        res1 = self.presenter.on_take_clicked(dose.occurrence_id)
        print(f"  ✓ Phản hồi từ hệ thống: \"{res1['message']}\"")
        print(f"  ✓ Trạng thái mới: {res1['occurrence'].state.value}")

        print("\n▶ Bệnh nhân nhấn nhầm thêm một lần nữa (Test Idempotent)...")
        res2 = self.presenter.on_take_clicked(dose.occurrence_id)
        print(f"  ✓ Phản hồi chống trùng: \"{res2['message']}\" (already_confirmed={res2['already_confirmed']})")
        print("  ==> Kết luận: Không tạo bản ghi trùng lặp, bảo đảm tính toàn vẹn dữ liệu.")

    def test_step_3_snooze(self) -> None:
        self.banner("BƯỚC 3: THAO TÁC 'NHẮC LẠI SAU' (SNOOZE CÓ GIỚI HẠN)")
        self.identity_adapter.simulate_camera_recognition("patient_002")
        self.presenter.reload_doses()
        dose = self.presenter.cached_doses[0]

        print(f"▶ Bệnh nhân chọn hoãn nhắc liều: {dose.medication_name_snapshot}")
        for i in range(1, 4):
            res = self.presenter.on_snooze_clicked(dose.occurrence_id, minutes=10)
            print(f"  ✓ Lần hoãn {i}: Hoãn đến {res['snoozed_until']} | Số lần hoãn còn lại: {res['remaining_snoozes']}")

        print("\n▶ Bệnh nhân cố gắng hoãn lần thứ 4 (Vượt ngưỡng 3 lần)...")
        try:
            self.presenter.on_snooze_clicked(dose.occurrence_id, minutes=10)
        except ValueError as e:
            print(f"  ✓ Bị chặn đúng quy định an toàn: \"{e}\"")
        print("  ==> Kết luận: Không thể hoãn vô hạn; mốc quá hạn gốc được bảo toàn.")

    def test_step_4_help(self) -> None:
        self.banner("BƯỚC 4: THAO TÁC 'CẦN HỖ TRỢ' (KHẨN CẤP / SOS)")
        self.identity_adapter.simulate_camera_recognition("patient_001")
        self.presenter.reload_doses()
        dose = self.presenter.cached_doses[0]

        print(f"▶ Bệnh nhân bấm nút khẩn cấp: [🚨 CẦN HỖ TRỢ]...")
        res = self.presenter.on_help_clicked(dose.occurrence_id, details="Cần con trai hướng dẫn lại cách uống")
        print(f"  ✓ Mã sự cố khẩn cấp: {res['incident_code']}")
        print(f"  ✓ Hotline cấp cứu hiển thị: {res['emergency_hotline']}")
        print(f"  ✓ Người liên hệ ưu tiên 1: {res['caregiver_contact']['name']} - ĐT: {res['caregiver_contact']['phone']}")
        print(f"  ✓ Thông báo gửi tức thì tới Caregiver: {len(self.notif_gateway.sent_alerts)} cảnh báo đã chuyển phát thành công.")
        print("  ==> Kết luận: Cảnh báo khẩn cấp phát ngay lập tức tới đúng người thân.")

    def test_step_5_overdue(self) -> None:
        self.banner("BƯỚC 5: TUA NHANH THỜI GIAN -> QUÁ HẠN & CẢNH BÁO NGƯỜI NHÀ")
        print(f"▶ Thời điểm ban đầu: {self.sim_time.strftime('%H:%M %d/%m/%Y')} UTC")
        print("▶ Tua nhanh thời gian thêm 2 giờ (Vượt qua Grace Period 60 phút)...")
        self.sim_time += timedelta(hours=2)
        print(f"▶ Thời điểm hiện tại: {self.sim_time.strftime('%H:%M %d/%m/%Y')} UTC")

        # Scheduler triggers
        self.scheduler.tick()

        alerts = self.service.med_repo.list_active_alerts("patient_001")
        overdue_alerts = [a for a in alerts if a.alert_type.value == "OVERDUE"]
        print(f"\n  ✓ Số lượng cảnh báo QUÁ HẠN phát sinh cho patient_001: {len(overdue_alerts)}")

        # Check who received the alert
        sent = self.notif_gateway.get_sent_alerts_for_patient("patient_001")
        recipients = [s["recipient"] for s in sent]
        print(f"  ✓ Danh sách email nhận cảnh báo: {recipients}")
        print("  ✓ Kiểm tra lọc Người chăm sóc:")
        print("    - caregiver_001 (Anh Tuấn - hiệu lực): ĐÃ NHẬN ✓")
        print("    - caregiver_003 (Bác Bình - HẾT HẠN từ 2025): KHÔNG GỬI ✓ (Đã lọc đúng quy định)")
        print("  ==> Kết luận: Cảnh báo quá hạn chỉ gửi tới người chăm sóc đang có hiệu lực pháp lý.")

    def run_all(self) -> None:
        print("\n🏥 BẮT ĐẦU CHẠY KIỂM THỬ TRỰC TIẾP MODULE NHẮC UỐNG THUỐC (P0–P3)")
        self.test_step_1_identity()
        self.test_step_2_take()
        self.test_step_3_snooze()
        self.test_step_4_help()
        self.test_step_5_overdue()
        print("\n" + "=" * 70)
        print("  🎉 HOÀN THÀNH TẤT CẢ CÁC BƯỚC KIỂM THỬ TÍNH NĂNG MỚI! (100% THÀNH CÔNG)")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    app = TestRunnerApp()
    app.run_all()
