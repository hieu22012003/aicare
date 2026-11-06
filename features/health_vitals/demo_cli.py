"""
Interactive CLI Runner and Scenario Demonstrator for Health Monitoring Module (P0-P2).
Allows exploring all patient/clinician features, real database queries, ASCII charts,
time filters, day/week aggregations with n sample count, and permission tests in terminal.
"""

from __future__ import annotations

import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from datetime import datetime, timezone
from typing import List

from features.health_vitals.adapters.care_team_repository import SqlCareTeamRepository
from features.health_vitals.adapters.identity_adapter import MockPatientIdentityAdapter
from features.health_vitals.adapters.observation_repository import SqlObservationRepository
from features.health_vitals.adapters.patient_access_repository import SqlPatientAccessRepository
from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.database.seed_data import seed_database
from features.health_vitals.domain.models import (
    BloodPressureReading,
    MeasurementComponent,
    Resolution,
    SourceType,
)
from features.health_vitals.services.monitoring_service import MonitoringService


def render_ascii_chart(title: str, points: List[tuple], unit: str) -> None:
    """Simple, clean terminal ASCII sparkline chart."""
    if not points:
        print(f"\n[Biểu đồ {title}]: Chưa có số đo được gửi trong khoảng thời gian này.")
        return

    print(f"\n--- BIỂU ĐỒ XU HƯỚNG: {title} ({unit}) ---")
    vals = [p[1] for p in points if p[1] is not None]
    if not vals:
        print("Không có điểm dữ liệu hợp lệ.")
        return

    min_v = min(vals)
    max_v = max(vals)
    span = max_v - min_v if max_v > min_v else 1.0

    print(f"Cao nhất: {max_v:.1f} {unit} | Thấp nhất: {min_v:.1f} {unit} | Số điểm đo: {len(points)}")
    print("-" * 65)

    for time_str, val, extra in points[-15:]:  # Show up to last 15 points
        dt_str = time_str[:16].replace("T", " ")
        if val is None:
            bar = "[Thiếu thành phần]"
        else:
            bar_len = int(((val - min_v) / span) * 25)
            bar = "█" * max(1, bar_len)
            bar = f"{bar:<26} {val:.1f} {unit}"
        extra_str = f" ({extra})" if extra else ""
        print(f"{dt_str} | {bar}{extra_str}")
    print("-" * 65)


def render_ascii_bp_dual_chart(readings: List[BloodPressureReading]) -> None:
    """Render dual-line Blood Pressure chart in ASCII."""
    if not readings:
        print("\n[Biểu đồ Huyết áp]: Chưa có số đo được gửi trong khoảng thời gian này.")
        return

    print("\n" + "=" * 70)
    print("BIỂU ĐỒ HUYẾT ÁP HAI THÀNH PHẦN (TÂM THU [SYS] & TÂM TRƯƠNG [DIA] - mmHg)")
    print("=" * 70)
    print(f"{'Thời điểm đo':<18} | {'Tâm thu (SYS)':<16} | {'Tâm trương (DIA)':<16} | {'Nguồn đo / Ghi chú'}")
    print("-" * 70)

    # Show chronologically
    sorted_r = sorted(readings, key=lambda r: r.measured_at)[-15:]
    for r in sorted_r:
        dt_str = r.measured_at[:16].replace("T", " ")
        sys_str = f"{r.systolic:.0f} mmHg" if r.systolic else "Thiếu"
        dia_str = f"{r.diastolic:.0f} mmHg" if r.diastolic else "Thiếu"
        src = f"{r.device_label} ({r.entered_by_name})"
        print(f"{dt_str:<18} | {sys_str:<16} | {dia_str:<16} | {src}")
    print("-" * 70)


class HealthMonitoringCliDemo:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db = DatabaseManager(db_path)
        seed_database(self.db)

        self.obs_repo = SqlObservationRepository(self.db)
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.patient_access_repo = SqlPatientAccessRepository(self.db)
        self.identity_adapter = MockPatientIdentityAdapter("pat-001")

        self.service = MonitoringService(
            obs_repo=self.obs_repo,
            care_team_repo=self.care_team_repo,
            patient_access_repo=self.patient_access_repo,
        )

        self.current_role = "clinician"
        self.current_clinician_id = "doc-001"  # Dr. Quang
        self.current_patient_id = "pat-001"    # Cụ An
        self.current_days_filter = 30
        self.current_resolution = Resolution.RAW

    def run(self) -> None:
        while True:
            print("\n" + "=" * 70)
            print("  AI HEALTHCARE — MODULE NHẬT KÝ SỨC KHỎE & BÁC SĨ THEO DÕI TỪ XA")
            print("=" * 70)
            print(f"Vai trò hiện tại: [{self.current_role.upper()}]")
            if self.current_role == "clinician":
                cl = self.care_team_repo.get_clinician(self.current_clinician_id)
                cl_name = cl.full_name if cl else self.current_clinician_id
                print(f"Bác sĩ đang đăng nhập: {cl_name} ({cl.specialty if cl else ''} - {cl.workplace if cl else ''})")
            else:
                pat = self.patient_access_repo.get_patient(self.current_patient_id)
                pat_name = pat.full_name if pat else self.current_patient_id
                camera_st = "BẬT" if self.identity_adapter.is_camera_active() else "TẮT (Dùng phiên xác thực)"
                print(f"Bệnh nhân đang chọn: {pat_name} (ID: {self.current_patient_id}) | Camera: {camera_st}")

            print("\n[MENU CHÍNH]:")
            if self.current_role == "clinician":
                print(" 1. [Tầng 1] Xem danh sách bệnh nhân được phân công (Care Team)")
                print(" 2. [Tầng 2] Mở hồ sơ chi tiết bệnh nhân (Thẻ mới nhất, Biểu đồ, Bảng gốc)")
                print(" 3. [Tầng 2] Đổi bộ lọc thời gian (7 ngày / 30 ngày / 90 ngày)")
                print(" 4. [Tầng 2] Đổi độ phân giải biểu đồ (Gốc RAW / Gom theo Ngày DAY / Gom theo Tuần WEEK)")
                print(" 5. [Bác sĩ] Đánh dấu đã xem cho kỳ hiện tại ('Xác nhận đã xem')")
                print(" 6. [Bác sĩ] Gửi phản hồi / Yêu cầu đo lại cho bệnh nhân")
                print(" 7. [Bác sĩ] Đổi tài khoản Bác sĩ (BS. Lê Quang <-> BS. Phạm Mai [Hết hạn])")
                print(" 8. Chuyển sang giao diện Người bệnh / Người thân nhập số đo")
            else:
                print(" 1. Nhập huyết áp (Tâm thu & Tâm trương - 1 nhóm đo)")
                print(" 2. Nhập nhịp tim / SpO2 / Thân nhiệt / Đường huyết")
                print(" 3. Ghi nhật ký triệu chứng tự khai báo")
                print(" 4. Xem lịch sử đo gần đây và phản hồi của bác sĩ")
                print(" 5. Đổi bệnh nhân (Cụ An <-> Cụ Bình) hoặc Giả lập tắt/bật Camera")
                print(" 6. Chuyển sang giao diện Dashboard Bác sĩ")

            print(" 9. Chạy tự động kiểm thử 23 kịch bản nghiệm thu (100% Pass)")
            print(" 0. Thoát chương trình")
            print("-" * 70)

            choice = input("Chọn thao tác (0-9): ").strip()
            if choice == "0":
                print("\nTạm biệt!")
                break
            elif choice == "9":
                from features.health_vitals.tests.run_all_tests import run_all
                run_all()
            elif self.current_role == "clinician":
                self._handle_clinician_choice(choice)
            else:
                self._handle_patient_choice(choice)

    def _handle_clinician_choice(self, choice: str) -> None:
        if choice == "1":
            self._show_clinician_tier1_patients()
        elif choice == "2":
            self._show_clinician_tier2_detail()
        elif choice == "3":
            print("\nChọn bộ lọc thời gian:")
            print(" 1. 7 ngày gần đây")
            print(" 2. 30 ngày gần đây")
            print(" 3. 90 ngày gần đây")
            sub = input("Chọn (1-3): ").strip()
            if sub == "1":
                self.current_days_filter = 7
            elif sub == "2":
                self.current_days_filter = 30
            elif sub == "3":
                self.current_days_filter = 90
            print(f"-> Đã áp dụng bộ lọc: {self.current_days_filter} ngày.")
        elif choice == "4":
            print("\nChọn độ phân giải dữ liệu:")
            print(" 1. Điểm đo gốc (RAW) - Không làm phẳng, không vẽ đường nối qua khoảng trống")
            print(" 2. Tổng hợp theo ngày (DAY) - Kèm n số mẫu và giá trị mean/median")
            print(" 3. Tổng hợp theo tuần (WEEK) - Kèm n số mẫu")
            sub = input("Chọn (1-3): ").strip()
            if sub == "1":
                self.current_resolution = Resolution.RAW
            elif sub == "2":
                self.current_resolution = Resolution.DAY
            elif sub == "3":
                self.current_resolution = Resolution.WEEK
            print(f"-> Đã chuyển độ phân giải sang: {self.current_resolution.value.upper()}.")
        elif choice == "5":
            self._mark_reviewed()
        elif choice == "6":
            self._send_feedback()
        elif choice == "7":
            if self.current_clinician_id == "doc-001":
                self.current_clinician_id = "doc-002"
            else:
                self.current_clinician_id = "doc-001"
            print(f"-> Đã chuyển sang bác sĩ: {self.current_clinician_id}")
        elif choice == "8":
            self.current_role = "patient"
            print("-> Đã chuyển sang chế độ Người bệnh / Người thân.")

    def _handle_patient_choice(self, choice: str) -> None:
        if choice == "1":
            self._input_blood_pressure()
        elif choice == "2":
            self._input_other_vital()
        elif choice == "3":
            self._input_symptom()
        elif choice == "4":
            self._view_patient_history_and_feedback()
        elif choice == "5":
            self._toggle_patient_or_camera()
        elif choice == "6":
            self.current_role = "clinician"
            print("-> Đã chuyển sang chế độ Bác sĩ theo dõi từ xa.")

    # --------------------------------------------------------------------------
    # CLINICIAN VIEWS
    # --------------------------------------------------------------------------
    def _show_clinician_tier1_patients(self) -> None:
        print("\n" + "=" * 80)
        print("TẦNG 1: DANH SÁCH BỆNH NHÂN ĐƯỢC PHÂN CÔNG (CARE TEAM ĐANG HIỆU LỰC)")
        print("=" * 80)
        try:
            patients = self.service.get_clinician_patients_list(self.current_clinician_id)
        except PermissionError as e:
            print(f"LỖI PHÂN QUYỀN: {e}")
            return

        if not patients:
            print("Không có bệnh nhân nào được phân công hoặc quyền đã hết hạn.")
            return

        print(f"{'Mã BN':<10} | {'Họ và tên':<20} | {'Số đo mới':<12} | {'Trạng thái':<14} | {'Huyết áp gần nhất':<16} | {'Lần đo cuối'}")
        print("-" * 80)
        for p in patients:
            unrev = f"+{p.unreviewed_records_count} bản ghi" if p.unreviewed_records_count > 0 else "0 mới"
            last_m = p.last_measured_at[:16].replace("T", " ") if p.last_measured_at else "Chưa có"
            latest_bp = p.latest_bp or "---"
            print(f"{p.patient_id:<10} | {p.full_name:<20} | {unrev:<12} | {p.review_status:<14} | {latest_bp:<16} | {last_m}")
        print("-" * 80)

        sel = input("\nNhập mã bệnh nhân để mở Tầng 2 chi tiết (hoặc Enter để bỏ qua): ").strip()
        if sel:
            self.current_patient_id = sel
            self._show_clinician_tier2_detail()

    def _show_clinician_tier2_detail(self) -> None:
        print("\n" + "=" * 80)
        print(f"TẦNG 2: HỒ SƠ CHI TIẾT BỆNH NHÂN [{self.current_patient_id}] (BỘ LỌC: {self.current_days_filter} NGÀY)")
        print("=" * 80)

        # 1. Access verification
        try:
            prof = self.service.get_patient_profile(self.current_clinician_id, self.current_patient_id)
        except PermissionError as e:
            print(f"\n[TỪ CHỐI TRUY CẬP]: {e}")
            print("-> Bác sĩ không có tên trong care_team hoặc hợp đồng đã hết hiệu lực!")
            return

        pat = prof["patient"]
        latest_bp = prof["latest_bp"]
        latest_by_type = prof["latest_by_type"]
        last_rev = prof["latest_review"]

        print(f"Họ tên: {pat.full_name} | Giới tính: {pat.gender} | Múi giờ: {pat.timezone}")
        print(f"Tiền sử / Ghi chú: {pat.notes}")
        if last_rev:
            print(f"Lần bác sĩ xem cuối: {last_rev.reviewed_at[:16].replace('T', ' ')} (Kỳ: {last_rev.period_start[:10]} đến {last_rev.period_end[:10]})")
        else:
            print("Lần bác sĩ xem cuối: Chưa có lần xác nhận xem nào.")

        # Latest Cards
        print("\n[CÁC THẺ CHỈ SỐ MỚI NHẤT]:")
        if latest_bp and latest_bp.systolic and latest_bp.diastolic:
            print(f"  • Huyết áp: {int(latest_bp.systolic)}/{int(latest_bp.diastolic)} mmHg (Đo lúc: {latest_bp.measured_at[:16].replace('T', ' ')} | Nguồn: {latest_bp.device_label})")
        else:
            print("  • Huyết áp: Chưa có số đo được gửi")

        for code, label in [("heart_rate", "Nhịp tim"), ("spo2", "SpO2"), ("temperature", "Thân nhiệt"), ("blood_glucose", "Đường huyết")]:
            obs = latest_by_type.get(code)
            if obs:
                print(f"  • {label}: {obs.value_numeric} {obs.unit_code} (Đo lúc: {obs.measured_at[:16].replace('T', ' ')} | Nguồn: {obs.device_label})")
            else:
                print(f"  • {label}: Chưa có số đo được gửi")

        # 2. Blood Pressure Trend Chart
        bp_trend = self.service.get_blood_pressure_trend(
            clinician_id=self.current_clinician_id,
            patient_id=self.current_patient_id,
            days_filter=self.current_days_filter,
            resolution=self.current_resolution,
        )

        if self.current_resolution == Resolution.RAW:
            render_ascii_bp_dual_chart(bp_trend["readings"])
        else:
            print(f"\n[TỔNG HỢP HUYẾT ÁP THEO {self.current_resolution.value.upper()} (CÓ SỐ MẪU N)]:")
            for pt in bp_trend["aggregated_points"]:
                print(f"  Kỳ: {pt['period_key']} | n={pt['sample_count_n']} lần đo | Tâm thu TB: {pt['systolic_mean']} mmHg | Tâm trương TB: {pt['diastolic_mean']} mmHg")

        if bp_trend["data_gaps"]:
            print(f"\n[CHÚ Ý GIÁN ĐOẠN DỮ LIỆU]: Phát hiện {len(bp_trend['data_gaps'])} khoảng trống >= 48 giờ không có dữ liệu. Không nối liền đường biểu đồ.")

        # 3. Heart Rate Trend Chart
        hr_trend = self.service.get_observations_trend(
            clinician_id=self.current_clinician_id,
            patient_id=self.current_patient_id,
            type_code="heart_rate",
            days_filter=self.current_days_filter,
            resolution=self.current_resolution,
        )
        if self.current_resolution == Resolution.RAW:
            hr_points = [(o.measured_at, o.value_numeric, f"{o.device_label} - {o.entered_by_name}") for o in hr_trend["raw_observations"]]
            render_ascii_chart("NHỊP TIM", hr_points, "bpm")
        else:
            print(f"\n[TỔNG HỢP NHỊP TIM THEO {self.current_resolution.value.upper()} (CÓ SỐ MẪU N)]:")
            for pt in hr_trend["aggregated_points"]:
                print(f"  Kỳ: {pt.period_key} | n={pt.sample_count_n} | Trung bình: {pt.mean_value} {pt.unit_code} | Trung vị: {pt.median_value} {pt.unit_code}")

    def _mark_reviewed(self) -> None:
        try:
            self.service.assert_clinician_access(self.current_clinician_id, self.current_patient_id)
        except PermissionError as e:
            print(f"[TỪ CHỐI]: {e}")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        notes = input("Nhập ghi chú nhận xét của bác sĩ (tuỳ chọn): ").strip()
        rev = self.service.mark_as_reviewed(
            clinician_id=self.current_clinician_id,
            patient_id=self.current_patient_id,
            period_start="2026-09-01T00:00:00Z",
            period_end=now_iso,
            notes=notes,
        )
        print(f"\n[THÀNH CÔNG]: Đã tạo biên bản xác nhận xem ID: {rev.review_id}")
        print("Tất cả các bản ghi trước thời điểm này đã được đánh dấu là 'ĐÃ XEM'.")

    def _send_feedback(self) -> None:
        try:
            self.service.assert_clinician_access(self.current_clinician_id, self.current_patient_id)
        except PermissionError as e:
            print(f"[TỪ CHỐI]: {e}")
            return

        body = input("Nhập nội dung lời dặn / yêu cầu đo lại gửi cho người bệnh: ").strip()
        if not body:
            print("Hủy thao tác.")
            return

        msg = self.service.send_clinical_message(
            clinician_id=self.current_clinician_id,
            patient_id=self.current_patient_id,
            body=body,
        )
        print(f"\n[THÀNH CÔNG]: Đã gửi tin nhắn chuyên môn (ID: {msg.message_id}, Trạng thái: {msg.status}).")
        print("Trạng thái hiển thị là 'ĐÃ GỬI' (chưa chuyển thành 'ĐÃ ĐỌC' cho tới khi người bệnh xem).")

    # --------------------------------------------------------------------------
    # PATIENT / CAREGIVER ACTIONS
    # --------------------------------------------------------------------------
    def _input_blood_pressure(self) -> None:
        print(f"\n--- NHẬP SỐ ĐO HUYẾT ÁP CHO BỆNH NHÂN [{self.current_patient_id}] ---")
        try:
            sys_val = float(input("Huyết áp tâm thu (SYS, mmHg, vd: 125): ").strip())
            dia_val = float(input("Huyết áp tâm trương (DIA, mmHg, vd: 78): ").strip())
        except ValueError:
            print("Giá trị không hợp lệ. Vui lòng nhập số.")
            return

        dev_label = input("Tên máy đo (Enter để mặc định 'Người dùng nhập'): ").strip()
        context = input("Hoàn cảnh đo (vd: 'Nghỉ ngơi 15p', 'Sau ăn sáng'): ").strip()

        try:
            grp = self.service.record_blood_pressure(
                patient_id=self.current_patient_id,
                entered_by_user_id="usr-pat-001" if self.current_patient_id == "pat-001" else "usr-cg-002",
                systolic=sys_val,
                diastolic=dia_val,
                device_label=dev_label,
                context=context,
            )
            print(f"\n[THÀNH CÔNG]: Đã lưu lần đo huyết áp (Mã nhóm: {grp.group_id})")
            print(f"  • Tâm thu: {sys_val} mmHg")
            print(f"  • Tâm trương: {dia_val} mmHg")
            print(f"  • Nguồn đo: {grp.device_label} ({grp.source_type})")
            print(f"  • Giờ đo thực tế: {grp.measured_at} | Giờ ghi nhận: {grp.recorded_at}")
        except Exception as e:
            print(f"[LỖI XỬ LÝ]: {e}")

    def _input_other_vital(self) -> None:
        print("\nChọn loại chỉ số:")
        print(" 1. Nhịp tim (bpm)")
        print(" 2. SpO2 (%)")
        print(" 3. Thân nhiệt (°C)")
        print(" 4. Đường huyết (mg/dL)")
        sel = input("Chọn (1-4): ").strip()
        type_map = {
            "1": ("heart_rate", "bpm"),
            "2": ("spo2", "%"),
            "3": ("temperature", "°C"),
            "4": ("blood_glucose", "mg/dL"),
        }
        if sel not in type_map:
            print("Lựa chọn không hợp lệ.")
            return

        code, unit = type_map[sel]
        try:
            val = float(input(f"Nhập giá trị ({unit}): ").strip())
        except ValueError:
            print("Giá trị số không hợp lệ.")
            return

        dev_label = input("Tên thiết bị (Enter để mặc định 'Người dùng nhập'): ").strip()
        try:
            comp = MeasurementComponent(type_code=code, value_numeric=val, unit_code=unit)
            grp = self.service.record_measurement(
                patient_id=self.current_patient_id,
                entered_by_user_id="usr-pat-001" if self.current_patient_id == "pat-001" else "usr-cg-002",
                components=[comp],
                device_label=dev_label,
            )
            print(f"\n[THÀNH CÔNG]: Đã lưu chỉ số {code} = {val} {unit} vào nhóm {grp.group_id}")
        except Exception as e:
            print(f"[LỖI XỬ LÝ]: {e}")

    def _input_symptom(self) -> None:
        text = input("\nMô tả cảm giác / triệu chứng của người cao tuổi: ").strip()
        if not text:
            print("Nội dung không được để trống.")
            return
        try:
            lvl = int(input("Mức độ khó chịu (1: nhẹ đến 5: rất khó chịu, mặc định 1): ").strip() or "1")
        except ValueError:
            lvl = 1

        sym = self.service.record_symptom(
            patient_id=self.current_patient_id,
            reported_by_user_id="usr-pat-001" if self.current_patient_id == "pat-001" else "usr-cg-002",
            text=text,
            discomfort_level=lvl,
        )
        print(f"\n[THÀNH CÔNG]: Đã ghi nhận triệu chứng tự khai báo (ID: {sym.symptom_id}).")

    def _view_patient_history_and_feedback(self) -> None:
        print(f"\n--- LỊCH SỬ SỐ ĐO GẦN ĐÂY CỦA BỆNH NHÂN [{self.current_patient_id}] ---")
        obs_list = self.obs_repo.get_observations(self.current_patient_id, limit=10)
        for o in obs_list:
            dt_s = o.measured_at[:16].replace("T", " ")
            print(f"{dt_s} | {o.type_name}: {o.value_numeric} {o.unit_code} | Nguồn: {o.device_label} | Trạng thái: {o.status}")

        print("\n--- PHẢN HỒI & LỜI DẶN CỦA BÁC SĨ ---")
        msgs = self.service.get_patient_messages("usr-pat-001", self.current_patient_id)
        if not msgs:
            print("Chưa có phản hồi nào từ bác sĩ.")
        else:
            for m in msgs:
                st = "ĐÃ ĐỌC" if m.is_read_by_patient else "CHƯA ĐỌC"
                print(f"[{m.created_at[:16].replace('T', ' ')}] {m.clinician_name}: \"{m.body}\" (Trạng thái: {st})")
                # Mark as read
                if not m.is_read_by_patient:
                    self.service.mark_message_as_read(m.message_id, "usr-pat-001")
                    print("  -> Đã cập nhật biên nhận: Bệnh nhân đã đọc.")

    def _toggle_patient_or_camera(self) -> None:
        print("\n1. Đổi bệnh nhân (pat-001: Cụ An <-> pat-002: Cụ Bình)")
        print("2. Bật / Tắt mô phỏng nhận diện khuôn mặt Camera")
        sel = input("Chọn (1-2): ").strip()
        if sel == "1":
            if self.current_patient_id == "pat-001":
                self.current_patient_id = "pat-002"
            else:
                self.current_patient_id = "pat-001"
            self.identity_adapter.set_patient_override(self.current_patient_id)
            print(f"-> Đã chuyển sang bệnh nhân: {self.current_patient_id}")
        elif sel == "2":
            new_st = not self.identity_adapter.is_camera_active()
            self.identity_adapter.set_camera_active(new_st)
            print(f"-> Trạng thái camera: {'BẬT' if new_st else 'TẮT (Dùng phiên xác thực dự phòng)'}")


if __name__ == "__main__":
    demo = HealthMonitoringCliDemo()
    demo.run()
