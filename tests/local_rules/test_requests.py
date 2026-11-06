from types import SimpleNamespace
import pytest
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.voice_assistant.local_rules.requests import RequestService
from features.voice_assistant.local_rules.engine import IntentEngine


class FakeTransport:
    def __init__(self): self.calls=[];self.fail=set();self.unknown=set()
    def send_voice_request(self,recipient,*args):
        self.calls.append((recipient,args))
        code='DELIVERY_UNKNOWN' if recipient in self.unknown else 'FAILED_NETWORK' if recipient in self.fail else 'SMTP_ACCEPTED'
        return SimpleNamespace(success=code=='SMTP_ACCEPTED',status=code)


@pytest.fixture
def setup(tmp_path):
    db=UnifiedDatabaseManager(tmp_path/'care.sqlite3');Migrator(db).apply_all()
    with db.connect() as c:
        for uid,role in [('d','clinician'),('c','caregiver'),('p','patient')]:
            c.execute('INSERT INTO users(user_id,email,phone,full_name,role,created_at) VALUES (?,?,?, ?,?,?)',(uid,uid+'@fake.invalid','0',uid,role,'2026-01-01'))
        for p in ['p1','p2']:c.execute('INSERT INTO patients(patient_id,full_name) VALUES (?,?)',(p,p))
        c.execute("INSERT INTO caregivers(caregiver_id,user_id) VALUES ('cg','c')")
        c.execute("INSERT INTO clinicians(clinician_id,user_id) VALUES ('dr','d')")
        c.execute("INSERT INTO patient_caregivers(patient_caregiver_id,patient_id,caregiver_id,valid_from,valid_to) VALUES ('pc','p1','cg','2000-01-01','2099-01-01')")
        c.execute("INSERT INTO care_team(care_team_id,patient_id,clinician_id,valid_from,valid_to) VALUES ('ct','p1','dr','2000-01-01','2099-01-01')")
    transport=FakeTransport();return db,transport,RequestService(db,transport)


def create(service, text='Cứu tôi', uid='f1'):
    parsed=IntentEngine().parse(text,{'patient_id':'p1'},uid);a=parsed['actions'][0]
    recipients=service.resolve('p1',a['roles'])
    return service.create(parsed,a,recipients)


def test_confirmation_dedup_and_real_status(setup):
    db,t,s=setup;rid=create(s);assert create(s)==rid
    assert len(s.status(rid)['deliveries'])==2
    assert not t.calls
    s.deliver();s.deliver();assert len(t.calls)==2
    assert {d['status'] for d in s.status(rid)['deliveries']}=={'sent'}
    assert s.status(rid)['request']['status']=='open'
    assert len(db.fetchall("SELECT * FROM outbox_events WHERE event_type='voice.request_requested'"))==1


def test_offline_restart_reconnect(setup):
    db,t,s=setup;rid=create(s);s.deliver(online=False);assert not t.calls
    restarted=RequestService(UnifiedDatabaseManager(db.db_path),t)
    assert {d['status'] for d in restarted.status(rid)['deliveries']}=={'queued'}
    restarted.deliver();restarted.deliver();assert len(t.calls)==2


def test_cancel_before_send_and_correction_after(setup):
    db,t,s=setup;rid=create(s);s.cancel(rid,'p1');s.deliver();assert not t.calls
    rid2=create(s,uid='f2');s.deliver();s.cancel(rid2,'p1');s.deliver()
    assert len(t.calls)==2
    state=s.status(rid2);assert state['request']['status']=='cancelled'
    assert {d['status'] for d in state['deliveries']}=={'sent'}
    assert state['request']['original_text']=='Cứu tôi'
    assert db.fetchone("SELECT * FROM outbox_events WHERE aggregate_id=? AND event_type='voice.request_cancelled'",(rid2,))


def test_partial_failure_retry_only_failed(setup):
    db,t,s=setup;t.fail={'contact-95b1b191@example.com'};rid=create(s);s.deliver()
    assert {d['status'] for d in s.status(rid)['deliveries']}=={'sent','failed'}
    s.deliver();assert len(t.calls)==2
    t.fail.clear();s.deliver(retry_failed=True,force_retry=True);assert len(t.calls)==3
    assert t.calls[-1][0]=='contact-95b1b191@example.com'


def test_unknown_never_retried(setup):
    db,t,s=setup;t.unknown={'contact-138ceb6a@example.com'};rid=create(s);s.deliver();s.deliver(retry_failed=True)
    assert len(t.calls)==2
    assert 'unknown' in {d['status'] for d in s.status(rid)['deliveries']}


def test_interrupted_claim_never_resent(setup):
    db,t,s=setup;rid=create(s)
    db.execute("UPDATE voice_deliveries SET status='sending' WHERE recipient_id='d'")
    s.deliver();assert len(t.calls)==1
    assert 'unknown' in {d['status'] for d in s.status(rid)['deliveries']}


def test_same_recipient_two_roles(setup):
    db,t,s=setup;db.execute("UPDATE caregivers SET user_id='d'")
    rid=create(s);s.deliver();assert len(t.calls)==1
    assert len(s.status(rid)['deliveries'])==1


def test_no_identity_mapping_or_stale_recipient(setup):
    db,t,s=setup
    for patient in [None,'p2','patient_001']:
        with pytest.raises(ValueError):s.resolve(patient,['doctor'])
    p=IntentEngine().parse('Gọi bác sĩ',{'patient_id':'p1'},'f1');r=s.resolve('p1',['doctor'])
    db.execute("UPDATE care_team SET consent_status='REVOKED'")
    with pytest.raises(ValueError):s.create(p,p['actions'][0],r)
    assert not db.fetchall('SELECT * FROM voice_requests')


def test_expiry_and_revoked_mapping_not_sent(setup):
    db,t,s=setup;rid=create(s)
    db.execute("UPDATE voice_requests SET expires_at='2000-01-01'")
    s.deliver();assert not t.calls
    rid=create(s,uid='f2');db.execute("UPDATE patient_caregivers SET is_active=0")
    s.deliver();assert len(t.calls)==1  # Doctor remains valid; revoked caregiver never receives.


def test_patient_bound_id_and_cancel(setup):
    db,t,s=setup;rid=create(s)
    with pytest.raises(ValueError):s.cancel(rid,'p2')
    assert s.status(rid)['request']['status']=='open'


def test_retention_no_transcript_in_sse(setup):
    db,t,s=setup;rid=create(s);db.execute("UPDATE voice_requests SET created_at='2000-01-01'")
    s.prune();assert s.status(rid)['request']['original_text']=='[đã hết hạn lưu]'
    assert 'Cứu tôi' not in str(db.fetchall('SELECT payload_minimal FROM outbox_events'))


def test_partial_configuration_urgent_uses_only_known_recipient(setup):
    db,t,s=setup;db.execute("UPDATE care_team SET is_active=0")
    rid=create(s);s.deliver();assert len(t.calls)==1;assert t.calls[0][0]=='contact-95b1b191@example.com'
    assert s.status(rid)['request']['status']=='open'


def test_retry_backoff_and_attempt_cap_persist(setup):
    db,t,s=setup;t.fail={'contact-138ceb6a@example.com','contact-95b1b191@example.com'};rid=create(s);s.deliver()
    s.deliver(retry_failed=True);assert len(t.calls)==2
    for _ in range(5):s.deliver(retry_failed=True,force_retry=True)
    assert len(t.calls)==6
    assert {r['attempts'] for r in db.fetchall('SELECT * FROM voice_deliveries')}=={3}


def test_manual_contacts_are_verified_current_duty_only(setup,monkeypatch):
    from datetime import datetime,timezone
    monkeypatch.setattr('features.voice_assistant.local_rules.requests.now',lambda:datetime(2026,10,10,6,tzinfo=timezone.utc))
    db,t,s=setup
    with db.connect() as c:
        for cid,verified,start,end in [('all',1,'00:00','23:59'),('demo',0,'00:00','23:59'),('none',1,'00:00','00:00')]:
            c.execute("INSERT INTO emergency_contacts(contact_id,patient_id,contact_type,contact_name,phone,on_duty_start,on_duty_end,is_verified) VALUES (?,'p1','caregiver_primary',?,'000',?,?,?)",(cid,cid,start,end,verified))
    assert [r['contact_name'] for r in s.manual_contacts('p1')]==['all']
    with pytest.raises(ValueError):s.manual_contacts(None)
    assert not t.calls


def test_multiple_doctors_and_caregiver_priority_tie(setup):
    db, t, s = setup
    with db.connect() as c:
        # Add a secondary doctor
        c.execute("INSERT INTO users(user_id,email,phone,full_name,role,created_at) VALUES ('d2','contact-b0a12748@example.com','0','Dr Two','clinician','2026-01-01')")
        c.execute("INSERT INTO clinicians(clinician_id,user_id) VALUES ('dr2','d2')")
        c.execute("INSERT INTO care_team(care_team_id,patient_id,clinician_id,role_description,valid_from,valid_to) VALUES ('ct2','p1','dr2','Bác sĩ tư vấn phụ','2000-01-01','2099-01-01')")
        # Update first doctor as primary
        c.execute("UPDATE care_team SET role_description='Bác sĩ điều trị phụ trách' WHERE care_team_id='ct'")
        
        # Add a secondary caregiver with equal notification_priority (previously caused ValueError)
        c.execute("INSERT INTO users(user_id,email,phone,full_name,role,created_at) VALUES ('c2','contact-cdb71a90@example.com','0','Caregiver Two','caregiver','2026-01-01')")
        c.execute("INSERT INTO caregivers(caregiver_id,user_id) VALUES ('cg2','c2')")
        c.execute("INSERT INTO patient_caregivers(patient_caregiver_id,patient_id,caregiver_id,is_primary,notification_priority,valid_from,valid_to) VALUES ('pc2','p1','cg2',0,1,'2000-01-01','2099-01-01')")

    # Doctor resolves primary doctor without error
    docs = s.resolve('p1', ['doctor'])
    assert len(docs) == 1
    assert docs[0]['user_id'] == 'd'

    # Caregiver resolves primary caregiver without error despite priority tie
    cgs = s.resolve('p1', ['caregiver'])
    assert len(cgs) == 1
    assert cgs[0]['user_id'] == 'c'

    # Both resolve together cleanly
    both = s.resolve('p1', ['doctor', 'caregiver'])
    assert len(both) == 2
    assert {r['user_id'] for r in both} == {'d', 'c'}

