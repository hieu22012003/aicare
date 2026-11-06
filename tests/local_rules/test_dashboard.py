import pytest
from test_requests import setup,create
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from features.web_portal.sse_hub import sse_hub
from shared.services.outbox_relay import OutboxRelayService


def test_api_recipient_filter_real_ack_and_resolve(setup):
    db,t,service=setup;auth=AuthService(db);router=ApiRouter(db,auth)
    rid=create(service,'Gọi bác sĩ');token=auth.create_session(db.fetchone("SELECT * FROM users WHERE user_id='d'"))
    cg=auth.create_session(db.fetchone("SELECT * FROM users WHERE user_id='c'"))
    def api(method,suffix='',token=token):return router.handle_request(method,'/api/patients/p1/voice-requests'+suffix,{}, {},token)
    assert api('GET')[1]['requests'][0]['status']=='open'
    assert api('GET',token=cg)[1]['requests']==[]
    assert api('POST','/'+rid+'/acknowledged',token=cg)[0]==403
    assert api('POST','/'+rid+'/resolved')[0]==409
    assert api('POST','/'+rid+'/acknowledged')[0]==200
    assert service.status(rid)['request']['status']=='acknowledged'
    assert api('POST','/'+rid+'/resolved')[0]==200
    assert service.status(rid)['request']['status']=='resolved'
    assert api('GET',token=None)[0]==401
    db.execute("UPDATE care_team SET consent_status='REVOKED'")
    assert api('GET')[0]==403


def test_cancelled_request_not_acknowledged(setup):
    db,t,s=setup;rid=create(s);s.cancel(rid,'p1')
    with pytest.raises(ValueError):s.transition(rid,'p1','d','acknowledged')


def test_existing_outbox_sse_dashboard_reconnect_signal(setup):
    db,t,s=setup;rid=create(s);auth=AuthService(db)
    doctor=sse_hub.register_client('d','clinician');cg=sse_hub.register_client('c','caregiver')
    try:
        relay=OutboxRelayService(db,auth,email_service=None)
        assert relay.process_pending_events()==1
        assert doctor.queue.get_nowait()['type']=='voice.request_requested'
        assert cg.queue.get_nowait()['type']=='voice.request_requested'
        # SSE is an ID-only invalidation signal. Recipient API guards transcript access.
        assert s.list_for_recipient('p1','d')[0]['request_id']==rid
        s.deliver();relay.process_pending_events()
        s.transition(rid,'p1','d','acknowledged');relay.process_pending_events()
        types=[]
        while not doctor.queue.empty():types.append(doctor.queue.get_nowait()['type'])
        assert 'voice.request_acknowledged' in types
        assert not t.fail
    finally:
        sse_hub.unregister_client(doctor.client_id);sse_hub.unregister_client(cg.client_id)
