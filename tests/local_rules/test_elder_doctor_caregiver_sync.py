"""
End-to-End Verification Test:
Doctor and Caregiver synchronization with elderly patients,
ensuring voice and button commands are correctly routed and dispatched
to the assigned doctor and caregiver without errors.
"""

from types import SimpleNamespace
import pytest
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.voice_assistant.local_rules.requests import RequestService
from features.voice_assistant.local_rules.engine import IntentEngine


class RecordingTransport:
    def __init__(self):
        self.dispatched = []

    def send_voice_request(self, recipient_email, intent, transcript, created_at, event_id, priority='normal', context_refs=None):
        self.dispatched.append({
            'recipient': recipient_email,
            'intent': intent,
            'transcript': transcript,
            'priority': priority,
            'event_id': event_id,
        })
        return SimpleNamespace(success=True, status='SMTP_ACCEPTED')


@pytest.fixture
def synchronized_db(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / 'unified_care.sqlite3')
    Migrator(db).apply_all()
    with db.connect() as conn:
        # Create Users
        conn.execute("INSERT INTO users(user_id, email, phone, full_name, role, is_active, created_at) VALUES ('u_doc', 'contact-22dad1d8@example.com', '0912345678', 'bac sy a', 'clinician', 1, '2026-01-01')")
        conn.execute("INSERT INTO users(user_id, email, phone, full_name, role, is_active, created_at) VALUES ('u_cg1', 'contact-9e2e85e7@example.com', '0923456789', 'nguyen van b', 'caregiver', 1, '2026-01-01')")
        conn.execute("INSERT INTO users(user_id, email, phone, full_name, role, is_active, created_at) VALUES ('u_cg2', 'contact-927f84f8@example.com', '0934567890', 'Hiu Caregiver', 'caregiver', 1, '2026-01-01')")
        conn.execute("INSERT INTO users(user_id, email, phone, full_name, role, is_active, created_at) VALUES ('u_pat1', 'contact-8aea1a1c@example.com', '0945678901', 'nguyen van a', 'patient', 1, '2026-01-01')")
        conn.execute("INSERT INTO users(user_id, email, phone, full_name, role, is_active, created_at) VALUES ('u_pat2', 'contact-af9cad0c@example.com', '0956789012', 'Trần Văn Nam', 'patient', 1, '2026-01-01')")

        # Create Clinicians & Caregivers
        conn.execute("INSERT INTO clinicians(clinician_id, user_id, workplace, specialty) VALUES ('clinician_377885de', 'u_doc', 'BV Lão khoa', 'Lão khoa')")
        conn.execute("INSERT INTO caregivers(caregiver_id, user_id, relationship_desc) VALUES ('cg_1', 'u_cg1', 'Con cái')")
        conn.execute("INSERT INTO caregivers(caregiver_id, user_id, relationship_desc) VALUES ('cg_31cb8681', 'u_cg2', 'Người thân')")

        # Create Patients
        conn.execute("INSERT INTO patients(patient_id, user_id, full_name, status) VALUES ('patient_001', 'u_pat1', 'nguyen van a', 'ACTIVE')")
        conn.execute("INSERT INTO patients(patient_id, user_id, full_name, status) VALUES ('patient_002', 'u_pat2', 'Trần Văn Nam', 'ACTIVE')")

        # Synchronize Care Team (Doctor)
        for pid in ['patient_001', 'patient_002']:
            conn.execute("INSERT INTO care_team(care_team_id, patient_id, clinician_id, role_description, valid_from, valid_to, consent_status, is_active) VALUES (?, ?, 'clinician_377885de', 'Bác sĩ điều trị phụ trách', '2024-01-01', '2099-01-01', 'GRANTED', 1)", (f'ct_{pid}', pid))

        # Synchronize Patient Caregivers (Primary: nvb, Secondary: hiu)
        for pid in ['patient_001', 'patient_002']:
            conn.execute("INSERT INTO patient_caregivers(patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, notification_priority, consent_status, valid_from, valid_to, is_active) VALUES (?, ?, 'cg_1', 'Con cái', 1, 1, 'GRANTED', '2024-01-01', '2099-01-01', 1)", (f'pc1_{pid}', pid))
            conn.execute("INSERT INTO patient_caregivers(patient_caregiver_id, patient_id, caregiver_id, relationship_type, is_primary, notification_priority, consent_status, valid_from, valid_to, is_active) VALUES (?, ?, 'cg_31cb8681', 'Người thân', 0, 2, 'GRANTED', '2024-01-01', '2099-01-01', 1)", (f'pc2_{pid}', pid))

    transport = RecordingTransport()
    service = RequestService(db, transport)
    return db, transport, service


def test_doctor_resolution_and_dispatch(synchronized_db):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse('Gọi bác sĩ giúp tôi', {'patient_id': 'patient_001'}, 'utt_01')
    action = parsed['actions'][0]
    recipients = service.resolve('patient_001', action['roles'])

    assert len(recipients) == 1
    assert recipients[0]['full_name'] == 'bac sy a'
    assert recipients[0]['email'] == 'contact-22dad1d8@example.com'

    req_id = service.create(parsed, action, recipients)
    service.deliver(online=True)

    assert len(transport.dispatched) == 1
    assert transport.dispatched[0]['recipient'] == 'contact-22dad1d8@example.com'
    assert transport.dispatched[0]['intent'] == 'doctor_contact'


def test_caregiver_resolution_and_dispatch(synchronized_db):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse('Gọi con giúp tôi', {'patient_id': 'patient_001'}, 'utt_02')
    action = parsed['actions'][0]
    recipients = service.resolve('patient_001', action['roles'])

    assert len(recipients) == 1
    assert recipients[0]['full_name'] == 'nguyen van b'
    assert recipients[0]['email'] == 'contact-9e2e85e7@example.com'

    req_id = service.create(parsed, action, recipients)
    service.deliver(online=True)

    assert len(transport.dispatched) == 1
    assert transport.dispatched[0]['recipient'] == 'contact-9e2e85e7@example.com'
    assert transport.dispatched[0]['intent'] == 'caregiver_contact'


def test_both_doctor_and_caregiver_spoken_command(synchronized_db):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse('Gọi bác sĩ và người nhà', {'patient_id': 'patient_001'}, 'utt_03')
    assert len(parsed['actions']) == 2
    intents = {a['intent'] for a in parsed['actions']}
    assert intents == {'doctor_contact', 'caregiver_contact'}

    for action in parsed['actions']:
        recipients = service.resolve('patient_001', action['roles'])
        service.create(parsed, action, recipients)

    service.deliver(online=True)

    assert len(transport.dispatched) == 2
    emails = {d['recipient'] for d in transport.dispatched}
    assert emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}


def test_urgent_help_dispatches_to_both(synchronized_db):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse('Cứu tôi với', {'patient_id': 'patient_001'}, 'utt_04')
    action = parsed['actions'][0]
    assert action['intent'] == 'urgent_help'
    assert set(action['roles']) == {'doctor', 'caregiver'}

    recipients = service.resolve('patient_001', action['roles'])
    assert len(recipients) == 2
    recipient_emails = {r['email'] for r in recipients}
    assert recipient_emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}

    service.create(parsed, action, recipients)
    service.deliver(online=True)

    assert len(transport.dispatched) == 2
    sent_emails = {d['recipient'] for d in transport.dispatched}
    assert sent_emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}


def test_patient_002_also_synchronized(synchronized_db):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse('Gọi bác sĩ và người nhà', {'patient_id': 'patient_002'}, 'utt_05')
    for action in parsed['actions']:
        recipients = service.resolve('patient_002', action['roles'])
        service.create(parsed, action, recipients)

    service.deliver(online=True)

    assert len(transport.dispatched) == 2
    emails = {d['recipient'] for d in transport.dispatched}
    assert emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}


@pytest.mark.parametrize('symptom_phrase', [
    'Tôi mệt mỏi',
    'Tôi đau bụng',
    'Tôi đau đầu',
    'Tôi đau chân',
    'Tôi đau tay',
    'Tôi đau chân tay',
    'Tôi thấy mệt quá',
])
def test_health_report_symptoms_dispatch_to_both_doctor_and_caregiver(synchronized_db, symptom_phrase):
    db, transport, service = synchronized_db
    engine = IntentEngine()

    parsed = engine.parse(symptom_phrase, {'patient_id': 'patient_001'}, f'utt_{symptom_phrase}')
    assert len(parsed['actions']) == 1
    action = parsed['actions'][0]
    assert action['intent'] == 'health_report'
    assert set(action['roles']) == {'doctor', 'caregiver'}
    assert action['needs_confirmation'] is True

    recipients = service.resolve('patient_001', action['roles'])
    assert len(recipients) == 2
    recipient_names = {r['full_name'] for r in recipients}
    assert recipient_names == {'bac sy a', 'nguyen van b'}
    recipient_emails = {r['email'] for r in recipients}
    assert recipient_emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}

    req_id = service.create(parsed, action, recipients)
    service.deliver(online=True)

    # Verify both doctor and caregiver received the dispatch
    dispatched_to_symptom = [d for d in transport.dispatched if d['transcript'] == symptom_phrase]
    assert len(dispatched_to_symptom) == 2
    sent_emails = {d['recipient'] for d in dispatched_to_symptom}
    assert sent_emails == {'contact-22dad1d8@example.com', 'contact-9e2e85e7@example.com'}
    for d in dispatched_to_symptom:
        assert d['intent'] == 'health_report'


