"""
Verification Test Suite:
Synchronized health data and vital trends between Caregiver and Clinician portals
for elderly patient nguyen van a (patient_001).
"""

import json
from datetime import datetime, timezone
import pytest
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter


@pytest.fixture
def sync_env(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / 'unified_care.sqlite3')
    Migrator(db).apply_all()
    DataImporter(db).import_all()
    auth = AuthService(db)
    router = ApiRouter(db, auth)

    doc_session = auth.authenticate_user('contact-22dad1d8@example.com') or auth.authenticate_user('dr.an@laokhoa.example.com')
    doc_token = auth.create_session(doc_session)

    cg_session = auth.authenticate_user('contact-9e2e85e7@example.com') or auth.authenticate_user('tuan.caregiver@example.com')
    cg_token = auth.create_session(cg_session)

    return db, auth, router, doc_token, cg_token


def test_doctor_and_caregiver_overview_perfectly_synchronized(sync_env):
    db, auth, router, doc_token, cg_token = sync_env

    # 1. Fetch overview from Caregiver Portal
    cg_code, cg_data = router.handle_request('GET', '/api/patients/patient_001/overview', {}, {}, cg_token)
    assert cg_code == 200, f"Caregiver overview failed: {cg_data}"

    # 2. Fetch overview from Clinician Portal
    doc_code, doc_data = router.handle_request('GET', '/api/patients/patient_001/overview', {}, {}, doc_token)
    assert doc_code == 200, f"Clinician overview failed: {doc_data}"

    # 3. Assert vital signs exist and are NOT null / empty
    assert cg_data.get('latest_blood_pressure') is not None
    assert doc_data.get('latest_blood_pressure') is not None
    assert cg_data['latest_blood_pressure']['systolic'] == doc_data['latest_blood_pressure']['systolic']
    assert cg_data['latest_blood_pressure']['diastolic'] == doc_data['latest_blood_pressure']['diastolic']
    assert cg_data['latest_blood_pressure']['measured_at'] == doc_data['latest_blood_pressure']['measured_at']

    assert cg_data.get('latest_heart_rate') is not None
    assert doc_data.get('latest_heart_rate') is not None
    assert cg_data['latest_heart_rate']['value_numeric'] == doc_data['latest_heart_rate']['value_numeric']

    assert cg_data.get('latest_blood_glucose') is not None
    assert doc_data.get('latest_blood_glucose') is not None
    assert cg_data['latest_blood_glucose']['value_numeric'] == doc_data['latest_blood_glucose']['value_numeric']

    assert cg_data.get('latest_spo2') is not None
    assert doc_data.get('latest_spo2') is not None
    assert cg_data['latest_spo2']['value_numeric'] == doc_data['latest_spo2']['value_numeric']

    # 4. Assert Care Team links exist and match
    assert cg_data.get('assigned_doctor') is not None
    assert doc_data.get('assigned_doctor') is not None
    assert cg_data['assigned_doctor']['doctor_name'] == doc_data['assigned_doctor']['doctor_name']

    # 5. Medication adherence and rehab session sync
    assert cg_data.get('medication_today') is not None
    assert doc_data.get('medication_today') is not None
    assert cg_data.get('latest_rehab_session') is not None
    assert doc_data.get('latest_rehab_session') is not None


def test_doctor_and_caregiver_observations_chart_data_identical(sync_env):
    db, auth, router, doc_token, cg_token = sync_env

    # Both request 30 days blood pressure in raw resolution (default mode)
    cg_code, cg_obs = router.handle_request(
        'GET', '/api/patients/patient_001/observations',
        {'type': 'blood_pressure', 'days': '30', 'resolution': 'raw'}, {}, cg_token
    )
    doc_code, doc_obs = router.handle_request(
        'GET', '/api/patients/patient_001/observations',
        {'type': 'blood_pressure', 'days': '30', 'resolution': 'raw'}, {}, doc_token
    )

    assert cg_code == 200
    assert doc_code == 200

    cg_readings = cg_obs.get('readings', [])
    doc_readings = doc_obs.get('readings', [])

    assert len(cg_readings) > 0
    assert len(cg_readings) == len(doc_readings)

    # Validate identical values, timestamps and order
    for cg_r, doc_r in zip(cg_readings, doc_readings):
        assert cg_r['measured_at'] == doc_r['measured_at']
        assert cg_r['systolic'] == doc_r['systolic']
        assert cg_r['diastolic'] == doc_r['diastolic']


def test_realtime_vital_entry_instantly_synchronizes_both_portals(sync_env):
    db, auth, router, doc_token, cg_token = sync_env

    # Caregiver inputs a new measurement for patient_001
    now_iso = datetime.now(timezone.utc).isoformat()
    new_entry = {
        'client_request_id': 'test_req_sync_1',
        'measured_at': now_iso,
        'source_type': 'manual_caregiver',
        'device_label': 'Máy đo Omron nhà riêng',
        'items': [
            {'code': 'blood_pressure_systolic', 'value': 128.0, 'unit': 'mmHg'},
            {'code': 'blood_pressure_diastolic', 'value': 84.0, 'unit': 'mmHg'},
            {'code': 'heart_rate', 'value': 76.0, 'unit': 'bpm'},
        ]
    }
    add_code, add_res = router.handle_request('POST', '/api/patients/patient_001/observations', {}, new_entry, cg_token)
    assert add_code in (200, 201), f"Add observation failed: {add_res}"

    # Immediately check Doctor overview: must reflect the new 128/84 reading!
    doc_code, doc_data = router.handle_request('GET', '/api/patients/patient_001/overview', {}, {}, doc_token)
    assert doc_code == 200
    assert doc_data['latest_blood_pressure']['systolic'] == 128.0
    assert doc_data['latest_blood_pressure']['diastolic'] == 84.0
    assert doc_data['latest_heart_rate']['value_numeric'] == 76.0

    # Immediately check Caregiver overview: must also reflect the new 128/84 reading!
    cg_code, cg_data = router.handle_request('GET', '/api/patients/patient_001/overview', {}, {}, cg_token)
    assert cg_code == 200
    assert cg_data['latest_blood_pressure']['systolic'] == 128.0
    assert cg_data['latest_blood_pressure']['diastolic'] == 84.0
    assert cg_data['latest_heart_rate']['value_numeric'] == 76.0

