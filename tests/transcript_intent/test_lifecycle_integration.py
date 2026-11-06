import asyncio
import json
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from tests.voice_ai.test_local_voice import care_db, qapp, wait_for
from features.voice_assistant.transcript_intent.backend import TranscriptIntentEndpoint
from features.voice_assistant.transcript_intent.context import read_state, version
from features.voice_assistant.transcript_intent.coordinator import VoiceRequestCoordinator
from features.voice_assistant.transcript_intent.config import BackendConfig
from features.voice_assistant.transcript_intent.budget import RoutingUnavailable, ProjectBudget
from features.voice_assistant.transcript_intent.registry import Intent
from features.voice_assistant.transcript_intent.schema import Decision, IntentRequest, proposal
from features.voice_assistant.transcript_intent.service import GeminiIntentService
from features.voice_assistant.local_voice.dispatcher import CommandDispatcher

TEXT = 'Cho tôi quay về màn hình ban đầu'


def ctx():
    return dict(patient_id='synthetic', tab=2, doses=[], plans=[], sessions=[], running=False, paused=False)


def result():
    return dict(session_id='synthetic_session', transcript=TEXT)


class SlowClient:
    enabled = True
    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.thread_id = None
        self.calls = 0
    def classify(self, *args):
        self.calls += 1
        self.thread_id = threading.get_ident()
        self.started.set()
        assert self.release.wait(3)
        return dict(proposal=proposal(Intent.HOME, Decision.ROUTE, TEXT).model_dump(mode='json'))


@pytest.mark.parametrize('invalidation', ['cancel', 'patient', 'epoch', 'timeout', 'shutdown'])
def test_background_request_invalidation_and_no_overlap(qapp, invalidation):
    current = ctx()
    client = SlowClient()
    coordinator = VoiceRequestCoordinator(lambda: current.copy(), client=client)
    outputs = []
    coordinator.resolved.connect(lambda *args: outputs.append(args))
    try:
        coordinator.route(result(), current.copy())
        assert client.started.wait(1)
        assert client.thread_id != threading.get_ident()
        assert coordinator.busy
        coordinator.route(result(), current.copy())
        assert client.calls == 1
        if invalidation == 'cancel':
            coordinator.cancel()
        elif invalidation == 'patient':
            current['patient_id'] = 'another_synthetic'
        elif invalidation == 'epoch':
            current['epoch'] = 2
        elif invalidation == 'timeout':
            request_id, res, captured, started = coordinator.active
            coordinator.active = (request_id, res, captured, started - 9)
        else:
            coordinator.shutdown()
        coordinator.poll()
        client.release.set()
        coordinator.thread.join(1)
        coordinator.poll()
        assert outputs == []
        assert not coordinator.busy
    finally:
        client.release.set()
        coordinator.shutdown()


def test_one_result_duplicate_rejected_and_timer_runs(qapp):
    client, current = SlowClient(), ctx()
    coordinator = VoiceRequestCoordinator(lambda: current.copy(), client=client)
    outputs, ticks = [], []
    from PyQt5.QtCore import QTimer
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: ticks.append(1))
    heartbeat.start(5)
    coordinator.resolved.connect(lambda *args: outputs.append(args))
    try:
        coordinator.route(result(), current.copy())
        request_id = coordinator.active[0]
        wait_for(qapp, lambda: len(ticks) >= 3)
        assert not outputs
        client.release.set()
        wait_for(qapp, lambda: bool(outputs))
        coordinator.queue.put((request_id, {'proposal': proposal(Intent.HOME, Decision.ROUTE, TEXT).model_dump(mode='json')}))
        coordinator.poll()
        assert len(outputs) == 1
        assert outputs[0][2].intent == Intent.HOME
    finally:
        heartbeat.stop()
        client.release.set()
        coordinator.shutdown()


@pytest.mark.parametrize('text', ['Tôi đã uống rồi', 'Tôi chưa uống', 'tôi đau ngực', 'ừ'])
def test_local_and_sensitive_text_never_go_to_http(qapp, text):
    client = Mock(enabled=True)
    current = ctx()
    current.update(tab=0, doses=[{'state': 'REMINDING'}])
    c = VoiceRequestCoordinator(lambda: current.copy(), client=client)
    outputs = []
    c.resolved.connect(lambda *r: outputs.append(r))
    try:
        c.route({**result(), 'transcript': text}, current.copy())
        assert len(outputs) == 1
        assert not client.classify.called
    finally:
        c.shutdown()


def envelope(state):
    return IntentRequest(session_id='synthetic_session', request_id='synthetic_request',
                         patient_id=state['patient_id'], context_version=version(state),
                         context_type='home_navigation', exercise='', running=False, paused=False,
                         utterance=TEXT).model_dump()


def auth_mock():
    auth = Mock()
    auth.check_patient_access.return_value = (True, '')
    auth.get_session.return_value = {'user_id': 'synthetic_user', 'role': 'caregiver'}
    return auth


def service_result():
    return dict(proposal=proposal(Intent.HOME, Decision.ROUTE, TEXT).model_dump(mode='json'), outcome='gemini')


@pytest.mark.parametrize('invalidation', ['permission_before', 'permission_after', 'token', 'version_before', 'version_after', 'extra_role', 'synthetic_bypass'])
def test_backend_rechecks_auth_and_version(care_db, invalidation):
    db, _, _ = care_db
    state = read_state(db, 'patient_001')
    auth, svc = auth_mock(), Mock()
    svc.classify.return_value = service_result()
    endpoint = TranscriptIntentEndpoint(db, auth, svc)
    body = envelope(state)
    if invalidation == 'permission_before':
        auth.check_patient_access.return_value = (False, '')
    elif invalidation == 'permission_after':
        auth.check_patient_access.side_effect = [(True, ''), (False, '')]
    elif invalidation == 'token':
        auth.get_session.return_value = None
    elif invalidation == 'version_before':
        body['context_version'] = '0' * 64
    elif invalidation == 'version_after':
        def mutate(*_):
            with db.connect() as conn:
                conn.execute("UPDATE rehab_plans SET version=version+1 WHERE patient_id='patient_001'")
            return service_result()
        svc.classify.side_effect = mutate
    elif invalidation == 'extra_role':
        body['role'] = 'admin'
    else:
        body['synthetic'] = True
    code, response = endpoint.handle(body, 'synthetic_user', 'synthetic_token')
    assert code in (400, 403, 409)
    assert 'proposal' not in response
    if invalidation in ('permission_before', 'version_before', 'extra_role', 'synthetic_bypass'):
        assert not svc.classify.called


def test_authenticated_classification_is_read_only(care_db):
    db, _, _ = care_db
    auth, svc = auth_mock(), Mock()
    svc.classify.return_value = service_result()
    before = read_state(db, 'patient_001')
    endpoint = TranscriptIntentEndpoint(db, auth, svc)
    code, response = endpoint.handle(envelope(before), 'synthetic_user', 'synthetic_token')
    assert code == 200 and response['proposal']['intent'] == Intent.HOME
    assert before == read_state(db, 'patient_001')
    assert auth.check_patient_access.call_count == 2
    from features.web_portal.api_router import ApiRouter
    router = ApiRouter(db, auth)
    router.transcript_intent_endpoint = endpoint
    auth.get_session.return_value = None
    code, _ = router.handle_request('POST', '/api/transcript-intent', {}, envelope(before))
    assert code == 401


def test_proposed_medication_confirmation_same_db_and_web(care_db, qapp):
    db, client, snapshot = care_db
    dose = snapshot()
    # Exactly one due dose is the explicit current UI context.
    captured = dict(patient_id='patient_001', tab=0, doses=[dose], plans=[], sessions=[], running=False)
    todo = SimpleNamespace(sync_client=client, refresh_data=Mock())
    dispatcher = CommandDispatcher(SimpleNamespace(today_todo_widget=todo))
    dispatcher.snapshot = lambda: captured.copy()
    res = {'session_id': 'intent_integration', 'transcript': 'Tôi đã uống rồi'}
    value = proposal(Intent.TAKEN, Decision.ROUTE, res['transcript'])
    confirms = []
    def confirm(text):
        confirms.append(text)
        return True
    assert 'Chưa ghi' in dispatcher.dispatch(res, captured, lambda _: False, value)
    assert snapshot()['state'] == 'REMINDING'
    assert 'Đã ghi' in dispatcher.dispatch(res, captured, confirm, value)
    assert dose['medication_name_snapshot'] in confirms[0]
    assert dose['local_scheduled_time'] in confirms[0]
    dispatcher.dispatch(res, captured, confirm, value)
    assert len(confirms) == 1
    with db.connect() as conn:
        assert conn.fetchone("SELECT COUNT(*) n FROM dose_events WHERE idempotency_key='voice_intent_integration'")['n'] == 1
        assert conn.fetchone("SELECT COUNT(*) n FROM outbox_events WHERE source='voice'")['n'] == 1
    from features.web_portal.api_router import ApiRouter
    router = ApiRouter(db, auth_mock())
    code, body = router._get_medication_doses('patient_001', {'date': dose['local_scheduled_time'][:10]})
    assert code == 200
    assert next(x for x in body['doses'] if x['occurrence_id'] == dose['occurrence_id'])['state'] == 'CONFIRMED'


@pytest.mark.parametrize('text,intent', [('Tôi chưa uống', Intent.TAKEN), ('Tôi tập xong rồi', Intent.PAUSE)])
def test_model_proposal_does_not_override_negation_or_camera(text, intent):
    window = Mock()
    d = CommandDispatcher(window)
    captured = ctx()
    d.snapshot = lambda: captured.copy()
    assert 'Chưa rõ' in d.dispatch({'session_id': 'unsafe', 'transcript': text}, captured, Mock(), proposal(intent, Decision.ROUTE, text))
    assert not window.mock_calls


def test_sdk_contract_and_no_implicit_tools():
    from features.voice_assistant.transcript_intent.gemini_adapter import GeminiIntentAdapter
    client = MagicMock()
    aio = Mock()
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value='STOP'))],
                               text=proposal(Intent.HOME, Decision.ROUTE, TEXT).model_dump_json())
    aio.models.generate_content = AsyncMock(return_value=response)
    client.aio.__aenter__ = AsyncMock(return_value=aio)
    client.aio.__aexit__ = AsyncMock(return_value=False)
    cfg = BackendConfig(True, True, 'fake')
    value = GeminiIntentAdapter(cfg, client=client).generate(TEXT, 'home_navigation', {Intent.HOME, Intent.UNKNOWN}, .5)
    assert value.intent == Intent.HOME
    kwargs = aio.models.generate_content.call_args.kwargs
    assert kwargs['config'].tools == []
    assert kwargs['config'].automatic_function_calling.disable
    assert kwargs['config'].response_json_schema['additionalProperties'] is False
    assert kwargs['model'] == cfg.model
    assert 'patient_id' not in kwargs['contents'] and 'fake' not in kwargs['contents']


def test_sdk_async_timeout_cancels_pending_request():
    from features.voice_assistant.transcript_intent.gemini_adapter import GeminiIntentAdapter
    from unittest.mock import MagicMock
    cancelled = []
    async def slow(**_):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    client, aio = MagicMock(), Mock()
    aio.models.generate_content = slow
    client.aio.__aenter__ = AsyncMock(return_value=aio)
    client.aio.__aexit__ = AsyncMock(return_value=False)
    started = time.monotonic()
    with pytest.raises(RoutingUnavailable, match='timeout'):
        GeminiIntentAdapter(BackendConfig(), client).generate(TEXT, 'home_navigation', {Intent.HOME}, .02)
    assert time.monotonic() - started < .5 and cancelled


def test_tpm_reservation_and_secret_repr(tmp_path):
    cfg = BackendConfig(True, True, 'DO_NOT_EXPOSE', project='synthetic', rpm=10, tpm=1200, quota_path=str(tmp_path / 'quota.json'))
    assert 'DO_NOT_EXPOSE' not in repr(cfg)
    budget = ProjectBudget(cfg)
    budget.reserve(); budget.finish()
    with pytest.raises(RoutingUnavailable, match='local_tpm_limit'):
        budget.reserve()


def test_process_local_lock_and_offline_clear_commands(tmp_path):
    cfg = BackendConfig(True, True, 'fake', project='synthetic', rpm=10, quota_path=str(tmp_path / 'quota.json'))
    svc = GeminiIntentService(cfg, adapter=Mock())
    svc._inflight.acquire()
    try:
        assert svc.classify(TEXT, ctx())['outcome'] == 'provider_busy'
        current = ctx(); current.update(tab=0, doses=[{'state': 'REMINDING'}])
        assert svc.classify('Tôi đã uống rồi', current)['proposal']['intent'] == Intent.TAKEN
        assert not svc.adapter.generate.called
    finally:
        svc._inflight.release()


@pytest.mark.parametrize('finish,text,error', [
    ('SAFETY', '', 'blocked_or_truncated'), ('MAX_TOKENS', '{}', 'blocked_or_truncated'),
    ('STOP', '', 'empty_response'), ('STOP', '{invalid', 'invalid_response_or_transport'),
    ('STOP', '{"intent":"complete_rehabilitation"}', 'invalid_response_or_transport'),
])
def test_sdk_blocked_truncated_and_invalid_json(finish, text, error):
    from features.voice_assistant.transcript_intent.gemini_adapter import GeminiIntentAdapter
    client, aio = MagicMock(), Mock()
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=SimpleNamespace(value=finish))], text=text)
    aio.models.generate_content = AsyncMock(return_value=response)
    client.aio.__aenter__ = AsyncMock(return_value=aio)
    client.aio.__aexit__ = AsyncMock(return_value=False)
    with pytest.raises(RoutingUnavailable, match=error):
        GeminiIntentAdapter(BackendConfig(), client).generate(TEXT, 'home_navigation', {Intent.HOME}, .5)


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:password@example.com', 'https://example.com?key=bad', 'https://example.com#bad'])
def test_client_no_credentials_or_insecure_external_transport(url):
    from features.voice_assistant.transcript_intent.client import GeminiIntentClient
    with pytest.raises(ValueError):
        GeminiIntentClient(url=url)


def test_client_binding_rejects_stale_or_duplicate_response(monkeypatch):
    from features.voice_assistant.transcript_intent.client import GeminiIntentClient
    import requests
    session, response = MagicMock(), Mock()
    session.__enter__.return_value = session
    session.post.return_value = response
    response.status_code = 200
    response.json.return_value = dict(request_id='other_request', session_id='synthetic_session', context_version=version(ctx()))
    monkeypatch.setattr(requests, 'Session', lambda: session)
    client = GeminiIntentClient('http://127.0.0.1:8080', 'synthetic_token', True)
    with pytest.raises(RoutingUnavailable, match='response_context_mismatch'):
        client.classify(result(), ctx(), 'synthetic_request')
    assert session.post.call_args.kwargs['allow_redirects'] is False
    assert session.trust_env is False
    assert 'GEMINI_API_KEY' not in session.post.call_args.kwargs['headers']


def test_dataset_split_groups_and_no_sensitive_false_positive():
    from features.voice_assistant.transcript_intent.synthetic_dataset import load_cases
    from features.voice_assistant.transcript_intent.policy import normalize, local_proposal, validate_proposal
    cases = load_cases()
    assert len(cases) == len({c['id'] for c in cases}) == 150
    grouped = {}
    for case in cases:
        phrase = normalize(case['text'])
        assert grouped.setdefault(phrase, case['split']) == case['split']
        value = local_proposal(case['text'])
        if case['expected'] == 'unknown' and value:
            assert validate_proposal(value, case['text'], case['context']).intent == Intent.UNKNOWN


def test_real_auth_relationship_revocation(care_db):
    from features.web_portal.auth import AuthService
    from features.web_portal.api_router import ApiRouter
    db, _, _ = care_db
    with db.connect() as conn:
        caregiver = conn.fetchone("SELECT c.user_id, pc.caregiver_id FROM caregivers c JOIN patient_caregivers pc ON pc.caregiver_id=c.caregiver_id WHERE pc.patient_id='patient_001' AND pc.is_active=1 LIMIT 1")
        assert caregiver
        user = conn.fetchone('SELECT * FROM users WHERE user_id=?', (caregiver['user_id'],))
    auth = AuthService(db)
    token = auth.create_session(user)
    router = ApiRouter(db, auth)
    svc = Mock()
    def revoke(*_):
        with db.connect() as conn:
            conn.execute("UPDATE patient_caregivers SET is_active=0 WHERE patient_id='patient_001' AND caregiver_id=?", (caregiver['caregiver_id'],))
        return service_result()
    svc.classify.side_effect = revoke
    router.transcript_intent_endpoint.service = svc
    body = envelope(read_state(db, 'patient_001'))
    code, response = router.handle_request('POST', '/api/transcript-intent', {}, body, token)
    assert code == 403 and response['error'] == 'access_changed'


def test_local_button_handlers_are_shared(qapp):
    captured = ctx()
    captured.update(tab=1, source='camera', exercise='arm_raise', plans=[{'exercise_id':'arm_raise','is_active':1,'version':1}])
    window = Mock()
    dispatcher = CommandDispatcher(window)
    dispatcher.snapshot = lambda: captured.copy()
    res = {'session_id':'start_once','transcript':'Cho tôi bắt đầu tập'}
    assert 'Đã bắt đầu' in dispatcher.dispatch(res, captured, Mock(), proposal(Intent.START, Decision.ROUTE, res['transcript']))
    window._on_start_exercise.assert_called_once_with(expected_plan=captured['plans'][0])
    window.reset_mock()
    captured['running'] = True
    res = {'session_id':'pause_once','transcript':'Cho tôi nghỉ một chút'}
    assert 'Đã tạm dừng' in dispatcher.dispatch(res, captured, Mock(), proposal(Intent.PAUSE, Decision.ROUTE, res['transcript']))
    window._on_pause_exercise.assert_called_once_with()
    window.reset_mock()
    captured['running'] = False
    res = result()
    assert 'Đã về' in dispatcher.dispatch(res, captured, Mock(), proposal(Intent.HOME, Decision.ROUTE, TEXT))
    window.medication_tabs.setCurrentIndex.assert_called_once_with(0)


def test_voice_ui_routing_controls_and_cancel_keep_manual_buttons(qapp, monkeypatch):
    monkeypatch.setenv('VOICE_SEMANTIC_BACKEND','gemini')
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", Mock())  # No physical audio in widget tests.
    from PyQt5.QtCore import QObject, pyqtSignal
    from PyQt5.QtWidgets import QPushButton
    from apps.desktop.local_voice_widget import LocalVoiceWidget
    class Controller(QObject):
        changed = pyqtSignal(str, str)
        transcript_ready = pyqtSignal(object, object)
        state = 'idle'
        review_message = ''
        def cancel(self):
            self.changed.emit('idle', 'Đã hủy')
    current = ctx()
    dispatcher, controller, client = Mock(), Controller(), SlowClient()
    dispatcher.snapshot.side_effect = lambda: current.copy()
    dispatcher.dispatch.return_value = 'Đã về trang chính.'
    widget = LocalVoiceWidget(controller, dispatcher)
    widget.request_coordinator.client = client
    manual = QPushButton('Đã uống')
    widget._review_text = 'Kết quả của phiên trước'
    try:
        controller.transcript_ready.emit(result(), current.copy())
        assert client.started.wait(1)
        assert not widget.micro.isEnabled() and widget.cancel.isEnabled()
        assert manual.isEnabled()
        widget.cancel.click()
        assert not widget.micro.isEnabled()
        client.release.set()
        wait_for(qapp, lambda: widget.micro.isEnabled())
        assert not dispatcher.dispatch.called
        assert 'phiên trước' not in widget.status.text()
        assert manual.isEnabled()
    finally:
        client.release.set()
        widget.shutdown()
        widget.close()


def test_voice_ui_single_transcript_callback_reviews_validated_proposal(qapp, monkeypatch):
    monkeypatch.setenv('VOICE_SEMANTIC_BACKEND','gemini')
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", Mock())
    from PyQt5.QtCore import QObject, pyqtSignal
    from apps.desktop.local_voice_widget import LocalVoiceWidget
    class Controller(QObject):
        changed = pyqtSignal(str, str)
        transcript_ready = pyqtSignal(object, object)
        state = 'idle'
        review_message = ''
    current = ctx()
    current.update(tab=0, doses=[{'state':'REMINDING'}])
    dispatcher, controller = Mock(), Controller()
    dispatcher.snapshot.side_effect = lambda: current.copy()
    dispatcher.dispatch.return_value = 'Chưa ghi nhận uống thuốc.'
    widget = LocalVoiceWidget(controller, dispatcher)
    try:
        controller.transcript_ready.emit({'session_id':'ui_session','transcript':'Tôi đã uống rồi'}, current.copy())
        assert dispatcher.dispatch.call_count == 1
        assert dispatcher.dispatch.call_args.kwargs['proposed'].intent == Intent.TAKEN
        assert 'Tôi đã uống rồi' in widget.status.text()
    finally:
        widget.shutdown()
        widget.close()
