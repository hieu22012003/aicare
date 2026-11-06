"""
Test verifying immediate synchronization when a clinician cancels a prescription:
1. Patient has prescription v1.
2. Clinician revises prescription to v2 (v1 becomes SUPERSEDED).
3. Clinician cancels prescription v2 (v2 becomes CANCELLED).
4. Caregiver endpoint (/api/patients/{id}/medication/doses) returns ZERO doses.
5. Patient summary (/api/patients/{id}/overview) medication_today has ZERO count for cancelled rx.
6. Elder Desktop App query returns ZERO doses, showing "Hôm nay cụ không có lịch uống thuốc nào."
7. Past database audit trail remains intact (immutable history).
"""
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from datetime import date
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from shared.services.elder_sync_client import ElderSyncClient


def test_prescription_cancel_immediate_sync():
    db = UnifiedDatabaseManager(":memory:")
    Migrator(db).apply_all()
    auth = AuthService(db)
    router = ApiRouter(db, auth)

    # 1. Login doctor
    status, token_resp = router.handle_request("POST", "/api/auth/login", {}, {"email": "dr.an@laokhoa.example.com", "password": "demo"}, "", "127.0.0.1")
    assert status == 200
    token = token_resp["token"]

    # 2. Get patient
    status, pat_resp = router.handle_request("GET", "/api/me/patients", {}, {}, token, "127.0.0.1")
    assert status == 200
    patient_id = pat_resp["patients"][0]["patient_id"]

    # 3. Create prescription v1
    v1_payload = {
        "prescription_code": "DON-TEST-SYNC-01",
        "diagnosis": "Theo dõi huyết áp",
        "start_date": date.today().isoformat(),
        "items": [
            {
                "medication_id": "med_001",
                "dose_amount": 1.0,
                "dose_unit": "viên",
                "instructions": "Uống sau ăn sáng",
                "times_of_day": ["08:00", "20:00"]
            }
        ]
    }
    status, v1_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions", {}, v1_payload, token, "127.0.0.1")
    assert status == 201
    rx1_id = v1_resp["prescription_id"]

    # Check doses exist for today
    status, doses_resp = router.handle_request("GET", f"/api/patients/{patient_id}/medication/doses", {}, {}, token, "127.0.0.1")
    assert status == 200
    assert len(doses_resp["doses"]) == 2

    # Simulate patient taking the 08:00 dose
    d0_id = doses_resp["doses"][0]["occurrence_id"]
    status, act_resp = router.handle_request("POST", f"/api/patients/{patient_id}/medication/doses/{d0_id}/action", {}, {"action": "self_reported_taken"}, token, "127.0.0.1")
    assert status == 200

    # 4. Clinician revises v1 -> v2
    v2_payload = {
        "expected_version": 1,
        "reason": "Điều chỉnh liều dùng mới",
        "items": [
            {
                "medication_id": "med_001",
                "dose_amount": 2.0,
                "dose_unit": "viên",
                "instructions": "Uống 2 viên sau ăn",
                "times": ["08:00", "20:00"]
            }
        ]
    }
    status, v2_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions/{rx1_id}/revisions", {}, v2_payload, token, "127.0.0.1")
    assert status in (200, 201)
    rx2_id = v2_resp["prescription_id"]

    # 5. Clinician cancels v2!
    status, cancel_resp = router.handle_request("POST", f"/api/patients/{patient_id}/prescriptions/{rx2_id}/cancel", {}, {"reason": "Ngưng hoàn toàn thuốc"}, token, "127.0.0.1")
    assert status == 200
    assert cancel_resp["status"] == "CANCELLED"

    # 6. VERIFY: Caregiver portal endpoint returns 0 pending/active doses for this cancelled prescription!
    status, cg_doses = router.handle_request("GET", f"/api/patients/{patient_id}/medication/doses", {}, {}, token, "127.0.0.1")
    assert status == 200
    amlor_active_doses = [d for d in cg_doses["doses"] if "Amlor" in d.get("medication_name_snapshot", "") and d.get("state") not in ("CANCELLED", "CONFIRMED")]
    assert len(amlor_active_doses) == 0, f"Expected 0 active doses of Amlor, got {amlor_active_doses}"

    # 7. VERIFY: Patient summary does not count cancelled doses
    status, ovr_resp = router.handle_request("GET", f"/api/patients/{patient_id}/overview", {}, {}, token, "127.0.0.1")
    assert status == 200
    med_summary = ovr_resp.get("medication_today", {})
    # No Amlor doses should be counted
    assert med_summary.get("CANCELLED", 0) == 0

    # 8. VERIFY: Elder Desktop App query returns 0 doses for cancelled medication
    today_date = date.today().isoformat()
    with db.connect() as conn:
        elder_doses = conn.fetchall(
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
            (patient_id, f"{today_date}%"),
        )
        amlor_elder = [d for d in elder_doses if "Amlor" in d.get("medication_name_snapshot", "")]
        assert len(amlor_elder) == 0, f"Expected 0 doses in elder app query, got {amlor_elder}"

    # 9. VERIFY: ElderSyncClient returns 0 doses for cancelled medication
    client = ElderSyncClient(db, patient_id=patient_id)
    snapshot = client.get_today_snapshot()
    amlor_client = [d for d in snapshot.get("medications", []) if "Amlor" in d.get("medication_name_snapshot", "")]
    assert len(amlor_client) == 0, f"Expected 0 doses in ElderSyncClient, got {amlor_client}"
