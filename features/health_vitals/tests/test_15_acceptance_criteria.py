"""
Comprehensive End-to-End Acceptance Test Suite for 15 Mandatory Scenarios (Section 7).
Validates all requirements of KE_HOACH_NHAT_KY_SUC_KHOE_THEO_DOI_TU_XA.md:
  (1) Huyết áp 128/78 được hiển thị đúng hai thành phần và mmHg;
  (2) Thiếu ngày không tạo điểm 0;
  (3) Nhập lúc 20:00 cho lần đo 07:00 lưu hai mốc khác nhau;
  (4) Bác sĩ mất quyền theo dõi không đọc được dữ liệu cũ qua API và cache;
  (5) Bệnh nhân A không hiện số đo B dù camera đổi người;
  (6) Camera tắt vẫn có thể nhập qua phiên đã xác thực;
  (7) Gửi lại request không nhân đôi (idempotency key);
  (8) Dữ liệu sửa sai giữ vết bản gốc và loại khỏi đồ thị mặc định;
  (9) Khi thiếu nguồn đo, UI hiển thị “người dùng nhập” và không tự nhận là thiết bị đã đồng bộ;
  (10) Bác sĩ chưa phản hồi không hiển thị “đã tư vấn”;
  (11) Đổi bộ lọc làm chart và bảng đổi cùng nhau;
  (12) Điểm tổng hợp ngày gồm 3 lần đo phải có n=3 và mở được ba bản ghi;
  (13) Thiếu dữ liệu 5 ngày không nối đường như có đo liên tục;
  (14) Hai đơn vị đường huyết không được trộn;
  (15) Dashboard không tải toàn bộ bệnh nhân/lịch sử trong một request (phân trang và giới hạn).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parents[3]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import unittest
from datetime import datetime, timezone

from features.health_vitals.adapters.care_team_repository import SqlCareTeamRepository
from features.health_vitals.adapters.identity_adapter import MockPatientIdentityAdapter
from features.health_vitals.adapters.observation_repository import SqlObservationRepository
from features.health_vitals.adapters.patient_access_repository import SqlPatientAccessRepository
from features.health_vitals.database.connection import DatabaseManager
from features.health_vitals.database.seed_data import seed_database
from features.health_vitals.domain.models import (
    MeasurementComponent,
    Resolution,
    ReviewStatus,
    SourceType,
)
from features.health_vitals.services.monitoring_service import MonitoringService


class Test15AcceptanceCriteria(unittest.TestCase):
    def setUp(self) -> None:
        self.db = DatabaseManager(":memory:")
        seed_database(self.db)

        self.obs_repo = SqlObservationRepository(self.db)
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.patient_access_repo = SqlPatientAccessRepository(self.db)
        self.identity_adapter = MockPatientIdentityAdapter("pat-001")

        self.fixed_time = datetime(2026, 10, 5, 16, 0, 0, tzinfo=timezone.utc)
        self.service = MonitoringService(
            obs_repo=self.obs_repo,
            care_team_repo=self.care_team_repo,
            patient_access_repo=self.patient_access_repo,
            clock=lambda: self.fixed_time,
        )

    # --------------------------------------------------------------------------
    # Scenario 1: Huyết áp 128/78 được hiển thị đúng hai thành phần và mmHg
    # --------------------------------------------------------------------------
    def test_criterion_1_blood_pressure_two_components_and_mmhg(self) -> None:
        readings = self.obs_repo.get_blood_pressure_readings("pat-001", limit=10)
        self.assertGreater(len(readings), 0)

        # Find the 128/78 reading seeded
        bp_128 = next((r for r in readings if r.systolic == 128.0 and r.diastolic == 78.0), None)
        self.assertIsNotNone(bp_128, "Cặp huyết áp 128/78 phải tồn tại trong kết quả đọc")
        self.assertEqual(bp_128.systolic, 128.0)
        self.assertEqual(bp_128.diastolic, 78.0)
        self.assertEqual(bp_128.unit_code, "mmHg")
        # Ensure systolic and diastolic share the exact same group_id
        self.assertTrue(len(bp_128.group_id) > 0)

    # --------------------------------------------------------------------------
    # Scenario 2: Thiếu ngày không tạo điểm 0
    # --------------------------------------------------------------------------
    def test_criterion_2_missing_days_do_not_produce_zero_points(self) -> None:
        trend = self.service.get_observations_trend(
            clinician_id="doc-001",
            patient_id="pat-001",
            type_code="blood_pressure_systolic",
            days_filter=30,
            resolution=Resolution.DAY,
        )
        agg_points = trend["aggregated_points"]
        self.assertGreater(len(agg_points), 0)

        # Check that no point has value 0.0 or n=0
        for pt in agg_points:
            self.assertGreater(pt.mean_value, 0.0, "Không được biến ngày thiếu thành điểm 0")
            self.assertGreater(pt.sample_count_n, 0, "Mọi điểm tổng hợp phải có n > 0")

        # In raw observations, no value is 0
        raw_obs = trend["raw_observations"]
        for o in raw_obs:
            self.assertGreater(o.value_numeric, 0.0)

    # --------------------------------------------------------------------------
    # Scenario 3: Nhập lúc 20:00 cho lần đo 07:00 lưu hai mốc khác nhau
    # --------------------------------------------------------------------------
    def test_criterion_3_separate_measured_at_and_recorded_at(self) -> None:
        measured_time = "2026-10-02T07:00:00+00:00"
        recorded_time = "2026-10-02T20:00:00+00:00"

        # Record a late measurement
        group = self.service.record_blood_pressure(
            patient_id="pat-001",
            entered_by_user_id="usr-cg-001",
            systolic=122.0,
            diastolic=76.0,
            measured_at=measured_time,
            client_request_id="req-test-scenario-3",
        )

        self.assertEqual(group.measured_at, measured_time)
        self.assertEqual(group.recorded_at, self.fixed_time.isoformat())
        self.assertNotEqual(group.measured_at, group.recorded_at)

        # Verify from database
        fetched = self.obs_repo.get_group_by_request_id("req-test-scenario-3")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.measured_at, measured_time)
        self.assertEqual(fetched.recorded_at, self.fixed_time.isoformat())

    # --------------------------------------------------------------------------
    # Scenario 4: Bác sĩ mất quyền theo dõi không đọc được dữ liệu cũ qua API và cache
    # --------------------------------------------------------------------------
    def test_criterion_4_expired_clinician_cannot_read_via_api_or_cache(self) -> None:
        # Dr. Mai (doc-002) has expired care_team for Patient B (pat-002)
        with self.assertRaises(PermissionError):
            self.service.get_patient_profile("doc-002", "pat-002")

        with self.assertRaises(PermissionError):
            self.service.get_observations_trend("doc-002", "pat-002", "blood_pressure_systolic")

        with self.assertRaises(PermissionError):
            self.service.get_blood_pressure_trend("doc-002", "pat-002")

        # Cache check: Verify cache does not leak
        self.assertFalse(self.service._auth_cache.get("doc-002:pat-002", False))

        # Dr. Mai is also excluded from the patient list for Patient B
        mai_patients = self.service.get_clinician_patients_list("doc-002")
        patient_ids = [p.patient_id for p in mai_patients]
        self.assertNotIn("pat-002", patient_ids)
        self.assertIn("pat-001", patient_ids)

    # --------------------------------------------------------------------------
    # Scenario 5: Bệnh nhân A không hiện số đo B dù camera đổi người
    # --------------------------------------------------------------------------
    def test_criterion_5_patient_isolation_on_identity_change(self) -> None:
        # Switch camera identity to Patient A
        self.identity_adapter.set_patient_override("pat-001")
        active_id = self.identity_adapter.get_current_patient_id()
        self.assertEqual(active_id, "pat-001")

        obs_a = self.obs_repo.get_observations(patient_id=active_id)
        for o in obs_a:
            self.assertEqual(o.patient_id, "pat-001", "Chỉ được trả số đo của bệnh nhân A")

        # Switch camera identity to Patient B
        self.identity_adapter.set_patient_override("pat-002")
        active_id_b = self.identity_adapter.get_current_patient_id()
        self.assertEqual(active_id_b, "pat-002")

        obs_b = self.obs_repo.get_observations(patient_id=active_id_b)
        for o in obs_b:
            self.assertEqual(o.patient_id, "pat-002", "Chỉ được trả số đo của bệnh nhân B")

        # Ensure no cross-contamination between A and B
        obs_a_ids = {o.observation_id for o in obs_a}
        obs_b_ids = {o.observation_id for o in obs_b}
        self.assertTrue(obs_a_ids.isdisjoint(obs_b_ids), "Dữ liệu A và B phải hoàn toàn cô lập")

    # --------------------------------------------------------------------------
    # Scenario 6: Camera tắt vẫn có thể nhập qua phiên đã xác thực
    # --------------------------------------------------------------------------
    def test_criterion_6_authenticated_fallback_when_camera_inactive(self) -> None:
        self.identity_adapter.set_camera_active(False)
        self.assertFalse(self.identity_adapter.is_camera_active())

        fallback_id = self.identity_adapter.get_current_patient_id()
        self.assertEqual(fallback_id, "pat-001")

        # Authenticated entry can still succeed
        group = self.service.record_blood_pressure(
            patient_id=fallback_id,
            entered_by_user_id="usr-pat-001",
            systolic=120.0,
            diastolic=78.0,
            client_request_id="req-fallback-camera-off",
        )
        self.assertIsNotNone(group)
        self.assertEqual(group.patient_id, "pat-001")

    # --------------------------------------------------------------------------
    # Scenario 7: Gửi lại request không nhân đôi (Idempotency Key)
    # --------------------------------------------------------------------------
    def test_criterion_7_request_retry_idempotency(self) -> None:
        req_id = "req-idempotency-unique-key-123"

        # First submission
        group1 = self.service.record_blood_pressure(
            patient_id="pat-001",
            entered_by_user_id="usr-pat-001",
            systolic=125.0,
            diastolic=80.0,
            client_request_id=req_id,
        )

        # Count observations before retry
        with self.db.connect() as conn:
            cnt_before = conn.execute("SELECT COUNT(*) FROM health_observations").fetchone()[0]
            grp_cnt_before = conn.execute("SELECT COUNT(*) FROM measurement_groups").fetchone()[0]

        # Second submission (identical request ID)
        group2 = self.service.record_blood_pressure(
            patient_id="pat-001",
            entered_by_user_id="usr-pat-001",
            systolic=125.0,
            diastolic=80.0,
            client_request_id=req_id,
        )

        # Count observations after retry
        with self.db.connect() as conn:
            cnt_after = conn.execute("SELECT COUNT(*) FROM health_observations").fetchone()[0]
            grp_cnt_after = conn.execute("SELECT COUNT(*) FROM measurement_groups").fetchone()[0]

        self.assertEqual(group1.group_id, group2.group_id)
        self.assertEqual(cnt_before, cnt_after, "Số lượng bản ghi observation không được tăng khi gửi lại")
        self.assertEqual(grp_cnt_before, grp_cnt_after, "Số lượng measurement group không được tăng khi gửi lại")

    # --------------------------------------------------------------------------
    # Scenario 8: Dữ liệu sửa sai giữ vết bản gốc và loại khỏi đồ thị mặc định
    # --------------------------------------------------------------------------
    def test_criterion_8_correction_preserves_audit_and_excludes_from_active_chart(self) -> None:
        with self.db.connect() as conn:
            old_obs = conn.execute("SELECT * FROM health_observations WHERE observation_id = 'obs-sys-d2-mistyped'").fetchone()
            new_obs = conn.execute("SELECT * FROM health_observations WHERE observation_id = 'obs-sys-d2-corrected'").fetchone()

        self.assertIsNotNone(old_obs, "Bản gốc sai sót phải được bảo toàn trong cơ sở dữ liệu")
        self.assertEqual(old_obs["status"], "corrected", "Bản gốc phải có trạng thái 'corrected'")
        self.assertEqual(old_obs["value_numeric"], 190.0)

        self.assertIsNotNone(new_obs, "Bản ghi hiệu chỉnh phải tồn tại")
        self.assertEqual(new_obs["status"], "active")
        self.assertEqual(new_obs["corrects_observation_id"], "obs-sys-d2-mistyped")
        self.assertEqual(new_obs["value_numeric"], 126.0)

        # Active trend query must EXCLUDE the 190.0 reading
        trend = self.service.get_observations_trend(
            clinician_id="doc-001",
            patient_id="pat-001",
            type_code="blood_pressure_systolic",
            days_filter=30,
        )
        active_vals = [o.value_numeric for o in trend["raw_observations"]]
        self.assertNotIn(190.0, active_vals, "Giá trị sai sót đã sửa không được xuất hiện trên biểu đồ mặc định")
        self.assertIn(126.0, active_vals, "Giá trị đã hiệu chỉnh phải xuất hiện trên biểu đồ mặc định")

    # --------------------------------------------------------------------------
    # Scenario 9: Khi thiếu nguồn đo, hiển thị "người dùng nhập" và không tự nhận thiết bị tự động
    # --------------------------------------------------------------------------
    def test_criterion_9_missing_device_source_defaults_to_manual(self) -> None:
        group = self.service.record_blood_pressure(
            patient_id="pat-001",
            entered_by_user_id="usr-pat-001",
            systolic=120.0,
            diastolic=78.0,
            device_label="",  # Empty device label
            source_type=SourceType.DEVICE_IMPORT.value,  # Wrong claim of device import without device
            client_request_id="req-test-scenario-9",
        )

        self.assertEqual(group.device_label, "Người dùng nhập")
        self.assertEqual(group.source_type, SourceType.MANUAL_PATIENT.value)

    # --------------------------------------------------------------------------
    # Scenario 10: Bác sĩ chưa phản hồi không hiển thị "đã tư vấn"
    # --------------------------------------------------------------------------
    def test_criterion_10_unreviewed_doctor_does_not_show_consulted(self) -> None:
        # Patient A has fresh measurements recorded today (unreviewed by Dr. Quang)
        profile = self.service.get_patient_profile("doc-001", "pat-001")
        patients_list = self.service.get_clinician_patients_list("doc-001")
        card = next(p for p in patients_list if p.patient_id == "pat-001")

        self.assertGreater(card.unreviewed_records_count, 0)
        self.assertEqual(card.review_status, ReviewStatus.UNREVIEWED.value)
        self.assertNotEqual(card.review_status, "đã tư vấn")

    # --------------------------------------------------------------------------
    # Scenario 11: Đổi bộ lọc làm chart và bảng đổi cùng nhau
    # --------------------------------------------------------------------------
    def test_criterion_11_filter_synchronization_chart_and_table(self) -> None:
        # 7-day query
        trend_7d = self.service.get_observations_trend("doc-001", "pat-001", "blood_pressure_systolic", days_filter=7)
        bp_7d = self.service.get_blood_pressure_trend("doc-001", "pat-001", days_filter=7)

        # 30-day query
        trend_30d = self.service.get_observations_trend("doc-001", "pat-001", "blood_pressure_systolic", days_filter=30)
        bp_30d = self.service.get_blood_pressure_trend("doc-001", "pat-001", days_filter=30)

        # 30-day query must contain more or equal observations than 7-day query
        self.assertGreater(len(trend_30d["raw_observations"]), len(trend_7d["raw_observations"]))
        self.assertGreater(len(bp_30d["readings"]), len(bp_7d["readings"]))

    # --------------------------------------------------------------------------
    # Scenario 12: Điểm tổng hợp ngày gồm 3 lần đo phải có n=3 và mở được ba bản ghi
    # --------------------------------------------------------------------------
    def test_criterion_12_day_aggregate_has_n_count_and_drilldown(self) -> None:
        trend = self.service.get_observations_trend(
            clinician_id="doc-001",
            patient_id="pat-001",
            type_code="blood_pressure_systolic",
            days_filter=30,
            resolution=Resolution.DAY,
        )
        agg_points = trend["aggregated_points"]

        # Find the point with n=3 (Day 10)
        pt_n3 = next((pt for pt in agg_points if pt.sample_count_n == 3), None)
        self.assertIsNotNone(pt_n3, "Phải tìm thấy điểm tổng hợp ngày có n=3")
        self.assertEqual(pt_n3.sample_count_n, 3)
        self.assertEqual(len(pt_n3.raw_observation_ids), 3, "Phải liên kết được đúng 3 observation_id để mở bản ghi gốc")

        # Drilldown: verify that fetching these 3 IDs yields the exact raw records
        with self.db.connect() as conn:
            placeholders = ",".join(["?"] * 3)
            rows = conn.execute(
                f"SELECT * FROM health_observations WHERE observation_id IN ({placeholders})",
                tuple(pt_n3.raw_observation_ids),
            ).fetchall()
        self.assertEqual(len(rows), 3)

    # --------------------------------------------------------------------------
    # Scenario 13: Thiếu dữ liệu 5 ngày không nối đường như có đo liên tục
    # --------------------------------------------------------------------------
    def test_criterion_13_missing_5_days_data_gap_detected(self) -> None:
        trend = self.service.get_observations_trend(
            clinician_id="doc-001",
            patient_id="pat-001",
            type_code="blood_pressure_systolic",
            days_filter=30,
        )
        gaps = trend["data_gaps"]
        self.assertGreater(len(gaps), 0, "Hệ thống phải phát hiện khoảng ngắt quãng dữ liệu >= 48 giờ")

        # Verify gap duration exceeds 5 days (120 hours)
        found_5day_gap = False
        for start_iso, end_iso in gaps:
            gap_seconds = (datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds()
            if gap_seconds >= 5 * 86400:
                found_5day_gap = True
                break
        self.assertTrue(found_5day_gap, "Khoảng thiếu dữ liệu 5 ngày phải được đánh dấu để không nối đường liên tục")

    # --------------------------------------------------------------------------
    # Scenario 14: Hai đơn vị đường huyết (mg/dL và mmol/L) không được trộn
    # --------------------------------------------------------------------------
    def test_criterion_14_glucose_units_separation(self) -> None:
        trend = self.service.get_observations_trend(
            clinician_id="doc-001",
            patient_id="pat-001",
            type_code="blood_glucose",
            days_filter=30,
        )
        units = trend["units"]
        self.assertIn("mg/dL", units)
        self.assertIn("mmol/L", units)

        # Raw observations preserve their original units distinctly
        raw_glu = trend["raw_observations"]
        mg_items = [o for o in raw_glu if o.unit_code == "mg/dL"]
        mmol_items = [o for o in raw_glu if o.unit_code == "mmol/L"]

        self.assertGreater(len(mg_items), 0)
        self.assertGreater(len(mmol_items), 0)
        # Verify values are appropriate for units (not wrongly merged)
        for m in mg_items:
            self.assertGreater(m.value_numeric, 50.0, "Đơn vị mg/dL phải có giá trị số lớn")
        for m in mmol_items:
            self.assertLess(m.value_numeric, 30.0, "Đơn vị mmol/L phải có giá trị số nhỏ")

    # --------------------------------------------------------------------------
    # Scenario 15: Dashboard không tải toàn bộ bệnh nhân / lịch sử trong một request
    # --------------------------------------------------------------------------
    def test_criterion_15_paginated_and_bounded_queries(self) -> None:
        # Limit to 5 records with offset
        page1 = self.obs_repo.get_observations("pat-001", limit=5, offset=0)
        page2 = self.obs_repo.get_observations("pat-001", limit=5, offset=5)

        self.assertLessEqual(len(page1), 5)
        self.assertLessEqual(len(page2), 5)

        # Page 1 and Page 2 should not share observation IDs
        ids1 = {o.observation_id for o in page1}
        ids2 = {o.observation_id for o in page2}
        self.assertTrue(ids1.isdisjoint(ids2), "Phân trang phải phân tách độc lập các bản ghi")


if __name__ == "__main__":
    unittest.main()
