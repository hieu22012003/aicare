import pytest
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.emotion.emotion_summary_service import EmotionSummaryService

P='patient_001'
T=1800000000

@pytest.fixture
def env():
    db=UnifiedDatabaseManager(':memory:')
    Migrator(db).apply_all()
    return db,EmotionSummaryService(db)

def feed(s,label,a,b):
    for t in range(a,b,5): s.record(P,label,T+t)
    s.record(P,None,T+b)

def test_majority_and_alert_independent(env):
    db,s=env
    feed(s,'Sad',0,600)
    feed(s,'Happy',600,7200)
    r=s.list_summaries(P)[0]
    assert r['label']=='Happy'
    assert r['observed_seconds']==7200
    assert len(s.list_alerts(P))==1
    assert r['sadness_override']==0

def test_mixed(env):
    _,s=env
    feed(s,'Happy',0,3600);feed(s,'Neutral',3600,7200)
    assert s.list_summaries(P)[0]['label']=='Mixed'

def test_exact_sixty_percent(env):
    _,s=env
    feed(s,'Happy',0,4320);feed(s,'Neutral',4320,7200)
    assert s.list_summaries(P)[0]['label']=='Happy'

def test_insufficient_and_no_downtime(env):
    _,s=env
    feed(s,'Neutral',0,900)
    s.flush_due(T+7200)
    r=s.list_summaries(P)[0]
    assert r['label']=='Insufficient'
    assert r['observed_seconds']==900
    assert s.list_alerts(P)==[]

def test_neutral_does_not_make_sad(env):
    _,s=env
    feed(s,'Sad',0,5);feed(s,'Neutral',5,7200)
    assert s.list_summaries(P)[0]['label']=='Neutral'
    assert not s.list_alerts(P)

def test_rolling_alert(env):
    _,s=env
    for a in range(0,1200,240):
        feed(s,'Sad',a,a+120);feed(s,'Neutral',a+120,a+240)
    assert s.list_alerts(P)[0]['reason']=='sad_accumulated'
    assert not s.list_summaries(P)

def test_rolling_expires(env):
    _,s=env
    feed(s,'Sad',0,240);feed(s,'Neutral',240,2100)
    feed(s,'Sad',2100,2340)
    assert not s.list_alerts(P)

def test_smoothing_removes_flicker(env):
    _,s=env
    for a in range(0,1800,30):
        feed(s,'Sad',a,a+5);feed(s,'Neutral',a+5,a+30)
    s.flush_due(T+7200)
    assert s.list_summaries(P)[0]['durations_seconds']['Sad']==0

def test_restart_and_duplicate_flush(env):
    db,s=env
    feed(s,'Sad',0,300)
    s=EmotionSummaryService(db)
    feed(s,'Sad',300,600)
    s.flush_due(T+7200);s.flush_due(T+7200)
    assert len(s.list_alerts(P))==1
    assert len(s.list_summaries(P))==1

def test_gap_breaks_continuous_sadness(env):
    _,s=env
    feed(s,'Sad',0,180);feed(s,'Sad',200,380)
    assert not s.list_alerts(P)

def test_empty_windows_and_isolation(env):
    _,s=env
    s.record(P,'Happy',T);s.flush_due(T+72000)
    assert len(s.list_summaries(P))==1
    assert s.list_summaries('patient_002')==[]

def test_api_and_outbox_separate_alerts(env):
    from features.web_portal.api_router import ApiRouter
    from features.web_portal.auth import AuthService
    db,s=env
    feed(s,'Sad',0,300)
    auth=AuthService(db)
    token=auth.create_session(db.fetchone("SELECT * FROM users WHERE user_id='user_care_001'"))
    status,data=ApiRouter(db,auth).handle_request('GET',f'/api/patients/{P}/mood',{},{},token)
    assert status==200
    assert data['camera_emotion_summaries']==[]
    assert len(data['camera_emotion_alerts'])==1
    assert db.fetchone("SELECT COUNT(*) AS n FROM outbox_events WHERE event_type='emotion.summary_updated'")['n']==1

def test_atomic_publication(env,monkeypatch):
    db,s=env
    feed(s,'Sad',0,295)
    s.record(P,'Sad',T+295)
    before=db.fetchone('SELECT state_json FROM emotion_monitor_state_v2')['state_json']
    original=s._publish
    def fail(*args):
        original(*args)
        raise RuntimeError('write failure')
    monkeypatch.setattr(s,'_publish',fail)
    with pytest.raises(RuntimeError): s.record(P,None,T+300)
    assert db.fetchone('SELECT state_json FROM emotion_monitor_state_v2')['state_json']==before
    assert not s.list_alerts(P)
