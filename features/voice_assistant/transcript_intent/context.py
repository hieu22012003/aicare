"""Read-only authoritative context shared by desktop and internal backend."""
import hashlib
import json
from datetime import datetime, timezone


def read_state(db, patient):
    with db.connect() as conn:
        if not conn.fetchone('SELECT patient_id FROM patients WHERE patient_id=?', (patient,)):
            return None
        now = datetime.now(timezone.utc).isoformat()
        doses = conn.fetchall('''
            SELECT d.*, p.version AS prescription_version, p.status AS prescription_status
            FROM dose_occurrences d
            JOIN medication_schedules s ON d.schedule_id=s.schedule_id AND d.revision=s.revision
            LEFT JOIN prescription_items pi ON s.prescription_item_id=pi.prescription_item_id
            LEFT JOIN prescriptions p ON pi.prescription_id=p.prescription_id
            WHERE d.patient_id=? AND d.state IN ('REMINDING','SNOOZED','OVERDUE')
              AND d.scheduled_at_utc<=? AND s.is_active=1
              AND (p.status IS NULL OR p.status='ACTIVE')
              AND (pi.status IS NULL OR pi.status='ACTIVE')
            ORDER BY d.scheduled_at_utc
        ''', (patient, now))
        sessions = conn.fetchall('SELECT session_id,plan_id,plan_version,local_date,status,target_count_snapshot FROM rehab_sessions WHERE patient_id=? ORDER BY session_id', (patient,))
        plans = conn.fetchall('SELECT plan_id,exercise_id,version,target_count,is_active FROM rehab_plans WHERE patient_id=? ORDER BY plan_id', (patient,))
    return dict(patient_id=patient, doses=doses, sessions=sessions, plans=plans)


def version(state):
    payload = {k: state[k] for k in ('patient_id', 'doses', 'sessions', 'plans')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def context_type(captured):
    return {0: 'medication', 1: 'rehabilitation'}.get(captured['tab'], 'home_navigation')
