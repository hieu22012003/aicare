import copy
import time
from types import SimpleNamespace
import pytest
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from features.voice_assistant.local_rules.desktop import LocalRulesCoordinator
from features.voice_assistant.local_rules.requests import RequestService
from test_requests import setup

APP=QApplication.instance() or QApplication([])


def wait(c):
    until=time.monotonic()+3
    while c.busy and time.monotonic()<until:
        APP.processEvents();time.sleep(.002)
    c.poll();APP.processEvents()
    assert not c.busy


@pytest.fixture
def desktop(setup):
    db,t,service=setup
    ctx={'patient_id':'p1','epoch':0,'tab':0,'running':False,'paused':False,'doses':[],'plans':[],'sessions':[]}
    events=[]
    def pause():ctx['paused']=True;events.append('stop')
    def tab(value):ctx['tab']=value;events.append('tab')
    todo=SimpleNamespace(db=db,sync_client=SimpleNamespace(is_online=True),refresh_data=lambda:events.append('refresh'))
    window=SimpleNamespace(today_todo_widget=todo,_on_pause_exercise=pause,medication_tabs=SimpleNamespace(setCurrentIndex=tab))
    dispatcher=SimpleNamespace(snapshot=lambda:copy.deepcopy(ctx),window=window)
    c=LocalRulesCoordinator(dispatcher,request_service=service);messages=[]
    c.changed.connect(lambda state,msg:messages.append((state,msg)))
    wait(c)
    yield c,ctx,events,messages,t,db
    c.shutdown()


def utterance(c,text,uid='u1',partial=False):
    c.route(dict(transcript=text,session_id=uid,status='completed',is_final=not partial),c.context_provider())
    wait(c)


def test_partial_never_executes(desktop):
    c,ctx,e,m,t,db=desktop;utterance(c,'Cứu tôi',partial=True)
    assert not db.fetchall('SELECT * FROM voice_requests');assert not t.calls


def test_final_duplicate_and_urgent_no_confirmation(desktop):
    c,ctx,e,m,t,db=desktop;utterance(c,'Cứu tôi');utterance(c,'Cứu tôi')
    assert len(t.calls)==2;assert len(db.fetchall('SELECT * FROM voice_requests'))==1
    assert c.dialogue.pending is None


@pytest.mark.parametrize('text,intent,recipient',[('Gọi bác sĩ giúp tôi','doctor_contact','d'),('Gọi con giúp tôi','caregiver_contact','c'),('Lấy nước giúp tôi','daily_assistance','c')])
def test_notification_requires_real_approval(desktop,text,intent,recipient):
    c,ctx,e,m,t,db=desktop;utterance(c,text)
    assert c.dialogue.pending;assert not t.calls;assert not db.fetchall('SELECT * FROM voice_requests')
    c.confirm();wait(c)
    assert len(t.calls)==1
    assert db.fetchone('SELECT * FROM voice_requests')['intent']==intent
    assert db.fetchone('SELECT * FROM voice_deliveries')['recipient_id']==recipient


def test_cancel_timeout_patient_change_no_mutation(desktop):
    c,ctx,e,m,t,db=desktop
    utterance(c,'Gọi con');utterance(c,'Hủy','u2');c.confirm();wait(c);assert not t.calls
    utterance(c,'Gọi con','u3');c.dialogue.clock=lambda:float('inf');c.confirm();assert not t.calls
    c.dialogue.clock=time.monotonic
    utterance(c,'Gọi con','u4');ctx['patient_id']='p2';c.confirm();assert not t.calls
    assert not db.fetchall('SELECT * FROM voice_requests')


def test_stop_then_contact_and_no_camera_completion(desktop):
    c,ctx,e,m,t,db=desktop;ctx['running']=True
    utterance(c,'Dừng bài tập, gọi con')
    assert e==['stop'];assert c.dialogue.pending;assert not t.calls
    c.confirm();wait(c);assert len(t.calls)==1
    utterance(c,'Xong rồi','u2');assert e==['stop']
    assert not db.fetchall('SELECT * FROM rehab_events')


def test_negation_replacement_and_explicit_recipient(desktop):
    c,ctx,e,m,t,db=desktop;utterance(c,'Gọi con');utterance(c,'Không, tôi cần bác sĩ','u2')
    c.confirm();wait(c);assert t.calls[0][0]=='contact-138ceb6a@example.com'
    utterance(c,'Tôi chóng mặt, gọi con','u3');c.confirm();wait(c)
    assert [r['intent'] for r in db.fetchall('SELECT * FROM voice_requests ORDER BY created_at')]==['doctor_contact','caregiver_contact']


def test_ambiguous_two_turns_and_unknown_no_mutation(desktop):
    c,ctx,e,m,t,db=desktop
    for i in range(3):utterance(c,'Tôi thấy khó chịu',str(i))
    assert 'Bác sĩ / Người nhà / Hủy' in m[-1][1]
    utterance(c,'Con tôi nói cứu tôi','other');assert not t.calls
    utterance(c,'Nếu tôi không thở được thì sao','hypo');assert not t.calls
    utterance(c,'Hôm qua tôi bị ngã, giờ ổn rồi','past');assert not t.calls
    assert not db.fetchall('SELECT * FROM voice_requests')


def test_offline_real_queue_and_false_alarm(desktop):
    c,ctx,e,m,t,db=desktop;c.dispatcher.window.today_todo_widget.sync_client.is_online=False
    utterance(c,'Cứu tôi');assert not t.calls;assert 'Chờ gửi' in m[-1][1]
    c.dispatcher.window.today_todo_widget.sync_client.is_online=True
    c._last_retry=0;c.poll();wait(c);assert len(t.calls)==2
    utterance(c,'Báo nhầm','u2');assert db.fetchone('SELECT status FROM voice_requests')['status']=='cancelled'
    assert len(t.calls)==2


def test_gui_timer_runs_during_transport(desktop):
    c,ctx,e,m,t,db=desktop
    original=t.send_voice_request
    def slow(*args):time.sleep(.05);return original(*args)
    t.send_voice_request=slow
    ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start(5)
    utterance(c,'Cứu tôi');timer.stop();assert len(ticks)>=5


def test_no_cloud_import_or_call_default(desktop,monkeypatch):
    import sys
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('isolated_voice_widget',Path('apps/desktop/local_voice_widget.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    LocalVoiceWidget=module.LocalVoiceWidget
    from PyQt5.QtCore import QObject,pyqtSignal
    class Controller(QObject):
        changed=pyqtSignal(str,str);transcript_ready=pyqtSignal(object,object)
        state='idle'
    c,ctx,e,m,t,db=desktop
    # Attach with fake business adapter so construction cannot reach real notification targets.
    from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
    monkeypatch.setattr(DoctorAssistanceAdapter,'local_request_service',lambda *args:RequestService(db,t))
    monkeypatch.delenv('VOICE_SEMANTIC_BACKEND',raising=False)
    before=set(sys.modules)
    widget=LocalVoiceWidget(Controller(),c.dispatcher)
    assert isinstance(widget.request_coordinator,LocalRulesCoordinator)
    assert 'features.voice_assistant.transcript_intent.gemini_adapter' not in (set(sys.modules)-before)
    widget.shutdown()


def test_content_repeat_requires_explicit_user_resend(desktop):
    c,ctx,e,m,t,db=desktop;utterance(c,'Cứu tôi');utterance(c,'Cứu tôi','u2');assert len(t.calls)==2
    c.route(dict(transcript='Cứu tôi',session_id='u3',status='completed',is_final=True,allow_repeat=True),c.context_provider());wait(c)
    assert len(t.calls)==4


def test_stop_runs_during_background_delivery(desktop):
    import threading
    c,ctx,e,m,t,db=desktop;ctx['running']=True
    release=threading.Event();started=threading.Event()
    def blocked():started.set();release.wait(2);return 'done'
    c._work(blocked,c.context_provider());assert started.wait(1)
    c.route(dict(transcript='Dừng lại',session_id='stop',is_final=True,status='completed'),c.context_provider())
    assert ctx['paused'];assert e==['stop']
    release.set();wait(c)


def test_unknown_identity_never_uses_default_profile(desktop):
    c,ctx,e,m,t,db=desktop;c.context_provider=lambda:None
    c.route(dict(transcript='Cứu tôi',session_id='missing',is_final=True,status='completed'),None);wait(c)
    assert not t.calls;assert not db.fetchall('SELECT * FROM voice_requests')
    assert 'chọn hồ sơ' in m[-1][1]


def test_voice_snapshot_requires_real_identity_mapping(setup):
    from features.voice_assistant.local_voice.dispatcher import CommandDispatcher
    db,t,s=setup
    todo=SimpleNamespace(db=db,patient_id='p1',sync_client=SimpleNamespace(patient_id='p1'))
    window=SimpleNamespace(today_todo_widget=todo,current_patient_id='1',current_patient_name='Known',
        router=SimpleNamespace(face_adapter=SimpleNamespace(get_latest_recognition=lambda:{'status':'recognized'}),
                               rehab_adapter=SimpleNamespace(engine=SimpleNamespace(is_paused=False))),
        medication_tabs=SimpleNamespace(currentIndex=lambda:0),active_exercise_id='arm_raise',current_source_mode='camera',
        btn_stop=SimpleNamespace(isEnabled=lambda:False),_last_rehab_plans_signature='')
    d=CommandDispatcher(window);assert d.snapshot() is None
    db.execute("INSERT INTO legacy_patient_map VALUES ('1','camera_face','p1')")
    assert d.snapshot()['patient_id']=='p1'
    window.current_patient_id='unknown';assert d.snapshot() is None


def test_pending_preview_not_overwritten_by_background_status(desktop):
    c,ctx,e,m,t,db=desktop;utterance(c,'Gọi con')
    preview=c.pending_message
    c.results.put((c.context_provider(),'Kết quả nền của yêu cầu trước',False))
    c._last_retry=0;c.poll()
    assert not c.busy;assert c.dialogue.pending
    assert m[-1]==('pending',preview)
    assert not t.calls


def test_final_and_confirmation_are_not_dropped_during_background_retry(desktop):
    import threading
    c,ctx,e,m,t,db=desktop
    started=threading.Event();release=threading.Event()
    def background():started.set();release.wait(2);return 'Trạng thái cũ'
    c._work(background,c.context_provider(),quiet=True);assert started.wait(1)
    c.route(dict(transcript='Gọi con',session_id='new',is_final=True,status='completed'),c.context_provider())
    assert c.dialogue.pending;assert not t.calls
    c.confirm()
    release.set();wait(c)
    assert len(t.calls)==1


def test_controller_idle_keeps_recipient_preview_visible(desktop,monkeypatch):
    import importlib.util
    from pathlib import Path
    from PyQt5.QtCore import QObject,pyqtSignal
    from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
    c,ctx,e,m,t,db=desktop
    spec=importlib.util.spec_from_file_location('preview_voice_widget',Path('apps/desktop/local_voice_widget.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class Controller(QObject):
        changed=pyqtSignal(str,str);transcript_ready=pyqtSignal(object,object)
        state='idle'
    monkeypatch.setenv('VOICE_SEMANTIC_BACKEND','local_rules')
    monkeypatch.setattr(DoctorAssistanceAdapter,'local_request_service',lambda *args:RequestService(db,t))
    controller=Controller();widget=module.LocalVoiceWidget(controller,c.dispatcher)
    try:
        wait(widget.request_coordinator)
        controller.state='review';controller.changed.emit('review','Gọi con')
        controller.transcript_ready.emit(dict(transcript='Gọi con',session_id='visible',is_final=True,status='completed'),c.context_provider())
        controller.state='idle';controller.changed.emit('idle','Gọi con')
        assert 'Gửi nguyên văn tới c?' in widget.status.text()
        assert widget.confirm_button.isEnabled() and widget.micro.isEnabled()
        assert not t.calls
        widget.request_coordinator.cancel()
        controller.state='unavailable';controller.changed.emit('unavailable','MODEL_UNAVAILABLE')
        assert 'MODEL_UNAVAILABLE' in widget.status.text()
    finally:
        widget.shutdown();widget.close()


def test_explicit_resend_preserves_request_after_voice_confirmation(desktop):
    c,ctx,e,m,t,db=desktop
    utterance(c,'Gọi con');utterance(c,'Đồng ý','approve')
    assert len(t.calls)==1;assert c.last_transcript=='Gọi con'
    c.route(dict(transcript=c.last_transcript,session_id='resend',is_final=True,status='completed',allow_repeat=True),c.context_provider())
    assert c.dialogue.pending;c.confirm();wait(c)
    assert len(t.calls)==2;assert len(db.fetchall('SELECT * FROM voice_requests'))==2


def test_both_doctor_and_caregiver_notification(desktop):
    c, ctx, e, m, t, db = desktop
    utterance(c, 'Gọi bác sĩ và người nhà')
    assert c.dialogue.pending
    c.confirm(); wait(c)
    assert len(t.calls) == 2
    recipients = {call[0] for call in t.calls}
    assert recipients == {'contact-138ceb6a@example.com', 'contact-95b1b191@example.com'}


@pytest.mark.parametrize('phrase', ['Tôi mệt mỏi', 'Tôi đau bụng', 'Tôi đau đầu', 'Tôi đau chân', 'Tôi đau tay'])
def test_symptom_report_requires_approval_and_dispatches_to_both(desktop, phrase):
    c, ctx, e, m, t, db = desktop
    utterance(c, phrase, uid=f'sym_{phrase}')
    assert c.dialogue.pending
    assert not t.calls
    c.confirm(); wait(c)
    assert len(t.calls) == 2
    recipients = {call[0] for call in t.calls}
    assert recipients == {'contact-138ceb6a@example.com', 'contact-95b1b191@example.com'}


