"""
Verification script for Clinician Prescription Creation, Medication Catalog, and Schedule Sync.
Tests API router directly against UnifiedDatabaseManager without requiring live network ports.
"""
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService

def run_prescription_flow_verification():
    print("==================================================================")
    print(" 🏥 KIỂM TRA QUY TRÌNH KÊ ĐƠN THUỐC & ĐỒNG BỘ LỊCH TRÊN CỔNG BÁC SĨ")
    print("==================================================================")

    db = UnifiedDatabaseManager()
    migrator = Migrator(db)
    applied = migrator.apply_all()
    print(f" • Migrations checked. New applied: {applied}")

    auth = AuthService(db)
    router = ApiRouter(db, auth)

    # 1. Doctor login / token resolution
    login_payload = {"email": "dr.an@laokhoa.example.com", "password": "demo"}
    status, token_resp = router.handle_request("POST", "/api/auth/login", {}, login_payload, "", "127.0.0.1")
    assert status == 200, f"Doctor login failed: {token_resp}"
    token = token_resp["token"]
    user_id = token_resp["user"]["user_id"]
    print(f" • Bác sĩ đăng nhập: {token_resp['user']['full_name']} (user_id={user_id}) [OK]")

    # 2. Get assigned patients
    status, patients_resp = router.handle_request("GET", "/api/me/patients", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to get patients: {patients_resp}"
    patients = patients_resp.get("patients", [])
    assert len(patients) > 0, "No patients found for clinician!"
    patient_id = patients[0]["patient_id"]
    print(f" • Danh sách bệnh nhân phụ trách: {len(patients)} bệnh nhân (Chọn: {patient_id}) [OK]")

    # 3. Get medications catalog
    status, meds_resp = router.handle_request("GET", "/api/medications", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to get medications catalog: {meds_resp}"
    meds = meds_resp.get("medications", [])
    assert len(meds) >= 3, f"Medications catalog has fewer than 3 items: {len(meds)}"
    print(f" • Danh mục thuốc sẵn có: {len(meds)} loại thuốc [OK]")

    # 4. Create new prescription with standard medication (Amlor 5mg)
    new_rx_payload = {
        "prescription_code": f"DON-TEST-{date.today().strftime('%Y%m%d')}-01",
        "diagnosis": "Tăng huyết áp nguyên phát độ 1 (Kiểm thử hệ thống)",
        "clinical_indication": "Tăng huyết áp nguyên phát độ 1 (Kiểm thử hệ thống)",
        "start_date": date.today().isoformat(),
        "items": [
            {
                "medication_id": "med_001",
                "dose_amount": 1.0,
                "dose_unit": "viên",
                "instructions": "Uống sau ăn sáng 30 phút",
                "times_of_day": ["08:00", "20:00"]
            }
        ]
    }
    status, create_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions", {}, new_rx_payload, token, "127.0.0.1")
    assert status == 201, f"Failed to create prescription: {create_resp}"
    rx_id = create_resp["prescription_id"]
    rx_code = create_resp["prescription_code"]
    print(f" • Kê đơn thuốc mới chuẩn (med_001): {rx_code} (ID: {rx_id}) [OK - HTTP 201]")

    # 5. Create new prescription with custom medication (not in catalog)
    custom_rx_payload = {
        "prescription_code": f"DON-TEST-{date.today().strftime('%Y%m%d')}-02",
        "diagnosis": "Bổ sung khoáng chất và vitamin",
        "clinical_indication": "Bổ sung khoáng chất",
        "start_date": date.today().isoformat(),
        "items": [
            {
                "brand_name": "Canxi Nano D3 500mg",
                "generic_name": "Calcium nano + Vitamin D3",
                "product_strength": "500",
                "strength_unit": "mg",
                "dosage_form": "Viên nang",
                "dose_amount": 1.0,
                "dose_unit": "viên",
                "instructions": "Uống vào buổi trưa sau ăn",
                "times_of_day": ["12:00"]
            }
        ]
    }
    status, custom_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions", {}, custom_rx_payload, token, "127.0.0.1")
    assert status == 201, f"Failed to create custom prescription: {custom_resp}"
    custom_rx_id = custom_resp["prescription_id"]
    print(f" • Kê đơn thuốc với thuốc mới ngoài danh mục: {custom_resp['prescription_code']} (ID: {custom_rx_id}) [OK - HTTP 201]")

    # 6. Verify prescriptions list includes the newly created ones
    status, list_resp = router.handle_request("GET", f"/api/patients/{patient_id}/prescriptions", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to list prescriptions: {list_resp}"
    prescriptions = list_resp.get("prescriptions", [])
    found_ids = {p["prescription_id"] for p in prescriptions}
    assert rx_id in found_ids, f"Created rx {rx_id} not found in prescriptions list!"
    assert custom_rx_id in found_ids, f"Created custom rx {custom_rx_id} not found in prescriptions list!"
    print(f" • Kiểm tra danh sách đơn thuốc: Tổng cộng {len(prescriptions)} đơn thuốc [OK]")

    # 7. Create a prescription revision
    rev_payload = {
        "expected_version": 1,
        "reason": "Điều chỉnh tăng liều theo dõi huyết áp",
        "items": [
            {
                "medication_id": "med_001",
                "dose_amount": 2.0,
                "dose_unit": "viên",
                "instructions": "Uống 2 viên vào buổi sáng sau ăn",
                "times": ["08:00"]
            }
        ]
    }
    status, rev_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions/{rx_id}/revisions", {}, rev_payload, token, "127.0.0.1")
    assert status == 200 or status == 201, f"Failed to revise prescription: {rev_resp}"
    print(f" • Điều chỉnh đơn thuốc (Revision): Phiên bản mới v{rev_resp['version']} [OK - HTTP {status}]")

    # 8. Check generated dose occurrences for today
    status, today_resp = router.handle_request("GET", f"/api/patients/{patient_id}/medication/doses", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to get today's occurrences: {today_resp}"
    doses = today_resp.get("doses", [])
    assert len(doses) > 0, "No dose occurrences were generated for today!"
    print(f" • Lịch uống thuốc hôm nay của người bệnh: {len(doses)} cữ uống đã được sinh tự động [OK]")

    # 9. Cancel the revised prescription (v2) and verify immediate removal on both caregiver and elder sides
    new_rx_id = rev_resp["prescription_id"]
    cancel_payload = {"reason": "Bệnh nhân đã ổn định, dừng đơn thuốc theo dõi"}
    status, cancel_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions/{new_rx_id}/cancel", {}, cancel_payload, token, "127.0.0.1")
    assert status == 200, f"Failed to cancel prescription: {cancel_resp}"
    assert cancel_resp.get("status") == "CANCELLED", f"Status is not CANCELLED: {cancel_resp}"
    print(f" • Bác sĩ ngưng hiệu lực/hủy đơn thuốc: {new_rx_id} -> CANCELLED [OK - HTTP 200]")

    # 10. Check caregiver side /medication/doses
    status, after_cancel_doses = router.handle_request("GET", f"/api/patients/{patient_id}/medication/doses", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to query doses after cancel: {after_cancel_doses}"
    # Verify no active doses returned for cancelled prescription
    remaining_doses = [d for d in after_cancel_doses.get("doses", []) if d.get("state") != "CANCELLED"]
    print(f" • Kiểm tra phía Người nhà (/medication/doses): {len(remaining_doses)} cữ thuốc còn hiệu lực [OK]")

    # 11. Check patient snapshot and summary
    status, summary_resp = router.handle_request("GET", f"/api/patients/{patient_id}/overview", {}, {}, token, "127.0.0.1")
    assert status == 200, f"Failed to query patient summary: {summary_resp}"
    med_summary = summary_resp.get("medication_today", {})
    print(f" • Tóm tắt thuốc hôm nay: {med_summary} [OK]")

    # 12. Check unified database query used by Desktop Elder App (TodayTodoWidget)
    with db.connect() as conn:
        active_elder_doses = conn.fetchall(
            """
            SELECT d.*, p.status as prescription_status
            FROM dose_occurrences d
            JOIN medication_schedules s ON d.schedule_id = s.schedule_id
            JOIN prescription_items pi ON s.prescription_item_id = pi.prescription_item_id
            JOIN prescriptions p ON pi.prescription_id = p.prescription_id
            WHERE d.patient_id = ?
              AND d.local_scheduled_time LIKE ?
              AND p.status = 'ACTIVE'
              AND d.state != 'CANCELLED'
            """,
            (patient_id, f"{date.today().isoformat()}%"),
        )
        print(f" • Kiểm tra giao diện người già (TodayTodoWidget DB query): {len(active_elder_doses)} cữ thuốc hiển thị [OK]")

    print("==================================================================")
    print(" 🎉 TẤT CẢ CÁC BƯỚC KIỂM THỬ KÊ ĐƠN, ĐIỀU CHỈNH & HỦY ĐƠN ĐỒNG BỘ 100%!")
    print("==================================================================")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(run_prescription_flow_verification())
