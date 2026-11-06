import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import queue
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from features.voice_assistant.local_voice.intents import resolve_intent
from features.voice_assistant.local_voice.controller import VoiceSessionController
from features.voice_assistant.local_voice.dispatcher import CommandDispatcher
from features.voice_assistant.local_voice.engine import LocalSttEngine
from features.voice_assistant.local_voice.config import VoiceConfig


@pytest.mark.parametrize('text', [
    'chưa uống', 'đừng đánh dấu đã uống', 'không nhớ đã uống chưa',
    'tôi không cần hỗ trợ', 'tôi đã uống rồi và nhắc lại sau', 'tập xong rồi',
    'tôi đã uống rồi?', 'đã uống?', 'uống thuốc', 'hình như tôi đã uống rồi', 'Tôi đã uống rồi, chưa?',
])
def test_unsafe_or_unknown_is_no_action(text):
    assert resolve_intent(text) is None


@pytest.mark.parametrize('text,intent', [('Tôi đã uống rồi.', 'taken'),
    ('Nhắc lại sau', 'snooze'), ('Tôi cần hỗ trợ', 'help'),
    ('Bắt đầu bài tập', 'start'), ('Tạm dừng', 'pause'), ('Về trang chính', 'home')])
def test_allowlist(text, intent):
    assert resolve_intent(text) == intent


def test_missing_model_never_downloads(tmp_path):
    with pytest.raises(RuntimeError, match='Chưa có dữ liệu'):
        LocalSttEngine(VoiceConfig(str(tmp_path)))


@pytest.fixture
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def fake_worker(config, commands, events):
    import time
    events.put(('ready', None, {}))
    while True:
        kind, token = commands.get()
        if kind == 'shutdown':
            return
        if kind == 'record':
            events.put(('recording', token, None))
        elif kind == 'stop':
            events.put(('transcribing', token, None))
            time.sleep(.15)
            events.put(('result', token, {'session_id': token, 'transcript': 'tôi đã uống rồi', 'status': 'completed'}))
        elif kind == 'cancel':
            events.put(('idle', token, None))


def wait_for(app, predicate):
    import time
    end = time.monotonic() + 5
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for worker')


def test_cancel_and_context_change_discard_late_results_and_shutdown(qapp):
    context = {'patient_id': 'p1', 'version': 1}
    c = VoiceSessionController(VoiceConfig('unused'), lambda: context.copy(), worker_target=fake_worker)
    outputs = []
    c.transcript_ready.connect(lambda *r: outputs.append(r))
    try:
        wait_for(qapp, lambda: c.state == 'idle')
        c.start()
        wait_for(qapp, lambda: c.state == 'recording')
        token = c.session
        c.start()
        assert c.session == token
        c.stop()
        context['version'] = 2
        wait_for(qapp, lambda: c.state == 'idle')
        assert outputs == []
        c.start()
        wait_for(qapp, lambda: c.state == 'recording')
        c.stop()
        wait_for(qapp, lambda: len(outputs) == 1)
        assert outputs[0][1]['version'] == 2
    finally:
        c.shutdown()
    assert not c.process.is_alive()


def test_dispatch_confirmation_change_and_dedup():
    client = Mock()
    client.confirm_dose_taken.return_value = {'status': 'processed'}
    todo = SimpleNamespace(sync_client=client, refresh_data=Mock())
    window = SimpleNamespace(today_todo_widget=todo)
    d = CommandDispatcher(window)
    captured = {'patient_id': 'p', 'tab': 0, 'doses': [{
        'occurrence_id': 'dose1', 'medication_name_snapshot': 'A', 'dose_snapshot': '1 viên',
        'local_scheduled_time': '2026-10-09T08:00', 'revision': 1}]}
    d.snapshot = lambda: captured.copy()
    result = {'session_id': 'one', 'transcript': 'Tôi đã uống rồi'}
    assert 'Chưa ghi' in d.dispatch(result, captured, lambda _: False)
    assert not client.confirm_dose_taken.called
    assert 'Đã ghi' in d.dispatch(result, captured, lambda _: True)
    d.dispatch(result, captured, lambda _: True)
    assert client.confirm_dose_taken.call_count == 1
    result['session_id'] = 'two'
    def change_context(_):
        d.epoch += 1
        d.snapshot = lambda: None
        return True
    assert 'đã đổi' in d.dispatch(result, captured, change_context)
    assert client.confirm_dose_taken.call_count == 1


def test_multiple_doses_never_guessed():
    d = CommandDispatcher(SimpleNamespace())
    context = {'tab': 0, 'doses': [{}, {}]}
    d.snapshot = lambda: context
    assert 'không thể đoán' in d.dispatch({'session_id': 'x', 'transcript': 'Tôi đã uống rồi'}, context, Mock())


def test_transcript_cannot_complete_rehabilitation():
    d = CommandDispatcher(SimpleNamespace())
    d.snapshot = lambda: {}
    assert 'Chưa rõ' in d.dispatch({'session_id': 'x', 'transcript': 'tập xong rồi'}, {}, Mock())

@pytest.fixture
def care_db(tmp_path):
    from shared.database.connection import UnifiedDatabaseManager
    from shared.database.migrator import Migrator
    from shared.database.importer import DataImporter
    from shared.services.elder_sync_client import ElderSyncClient
    db = UnifiedDatabaseManager(':memory:')
    Migrator(db).apply_all()
    DataImporter(db).import_all()
    client = ElderSyncClient(db, queue_dir=tmp_path, patient_id='patient_001')
    with db.connect() as conn:
        # The portable release does not ship the old standalone medication DB.
        # Keep this test independent from that optional production data file.
        if not conn.fetchone("SELECT occurrence_id FROM dose_occurrences WHERE patient_id='patient_001' LIMIT 1"):
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO medications (medication_id,brand_name,product_strength) VALUES ('voice_med','Thuốc thử nghiệm','1')")
            conn.execute("INSERT INTO prescriptions (prescription_id,patient_id,entered_by_user_id,prescribed_date,valid_from) VALUES ('voice_rx','patient_001','user_care_001',?,?)",(now[:10],now))
            conn.execute("INSERT INTO prescription_items (prescription_item_id,prescription_id,medication_id,dose_amount,original_instructions,start_date) VALUES ('voice_item','voice_rx','voice_med',1,'Sau ăn',?)",(now[:10],))
            conn.execute("INSERT INTO medication_schedules (schedule_id,prescription_item_id,local_times,days_of_week,effective_from) VALUES ('voice_schedule','voice_item','[\"08:00\"]','[0,1,2,3,4,5,6]',?)",(now,))
            conn.execute("INSERT INTO dose_occurrences (occurrence_id,schedule_id,revision,scheduled_at_utc,local_scheduled_time,grace_deadline_utc,medication_name_snapshot,dose_snapshot,instructions_snapshot,patient_id) VALUES ('voice_dose','voice_schedule',1,?,?,?,'Thuốc thử nghiệm','1 viên','Sau ăn','patient_001')",(now,now,now))
        dose = conn.fetchone("SELECT * FROM dose_occurrences WHERE patient_id='patient_001' LIMIT 1")
        assert dose
        conn.execute("UPDATE dose_occurrences SET state='REMINDING' WHERE occurrence_id=?", (dose['occurrence_id'],))
    def snapshot():
        with db.connect() as conn:
            return conn.fetchone('''SELECT d.*, p.version AS prescription_version, p.status AS prescription_status
                FROM dose_occurrences d
                JOIN medication_schedules s ON d.schedule_id=s.schedule_id AND d.revision=s.revision
                LEFT JOIN prescription_items pi ON s.prescription_item_id=pi.prescription_item_id
                LEFT JOIN prescriptions p ON pi.prescription_id=p.prescription_id
                WHERE d.occurrence_id=?''', (dose['occurrence_id'],))
    yield db, client, snapshot


def test_voice_shared_db_transaction_outbox_and_dedup(care_db):
    db, client, snapshot = care_db
    dose = snapshot()
    args = dict(occurrence_id=dose['occurrence_id'], action='self_reported_taken',
                client_event_id='voice_test1', expected_context=dose, source='voice')
    assert client.confirm_dose_taken(**args)['status'] == 'processed'
    assert client.confirm_dose_taken(**args)['status'] == 'processed'
    with db.connect() as conn:
        assert conn.fetchone('SELECT state FROM dose_occurrences WHERE occurrence_id=?', (dose['occurrence_id'],))['state'] == 'CONFIRMED'
        assert conn.fetchone("SELECT COUNT(*) n FROM dose_events WHERE idempotency_key='voice_test1'")['n'] == 1
        assert conn.fetchone("SELECT COUNT(*) n FROM outbox_events WHERE source='voice'")['n'] == 1
    from features.web_portal.api_router import ApiRouter
    from features.web_portal.auth import AuthService
    router = ApiRouter(db, AuthService(db))
    code, body = router._get_medication_doses('patient_001', {'date': dose['local_scheduled_time'][:10]})
    assert code == 200
    assert next(d for d in body['doses'] if d['occurrence_id'] == dose['occurrence_id'])['state'] == 'CONFIRMED' 


def test_voice_version_conflict_offline_and_restart_never_replay(care_db):
    db, client, snapshot = care_db
    dose = snapshot()
    with db.connect() as conn:
        conn.execute("UPDATE dose_occurrences SET state='CANCELLED' WHERE occurrence_id=?", (dose['occurrence_id'],))
    result = client.confirm_dose_taken(dose['occurrence_id'], 'self_reported_taken', 'voice_stale', dose, 'voice')
    assert result['status'] == 'failed'
    assert client.flush_offline_queue() == 0
    client.is_online = False
    assert client.confirm_dose_taken(dose['occurrence_id'], source='voice', expected_context=dose)['status'] == 'failed'
    client._save_queue([{'client_event_id': 'voice_crashed', 'status': 'pending',
                        'payload': {'source': 'voice'}, 'event_type': 'medication.confirm'}])
    client.is_online = True
    assert client.flush_offline_queue() == 0
    assert client._load_queue()[0]['status'] == 'failed'


def test_tts_cannot_restart_during_capture(monkeypatch):
    monkeypatch.delenv('AI_CARE_DISABLE_TTS', raising=False)
    from features.voice_assistant.voice_ai.audio.tts_player import TTSPlaybackCoordinator
    from unittest.mock import patch
    tts = TTSPlaybackCoordinator()
    with patch('subprocess.Popen') as popen:
        tts.suspend_for_recording(True)
        tts.speak('Nhắc thuốc')
        assert not popen.called
        tts.suspend_for_recording(False)
        tts.speak('Nhắc thuốc')
        popen.assert_called_once()


def test_recorder_hardware_sample_rate_resampling_bounded_and_close(monkeypatch):
    import sys
    import numpy as np
    from features.voice_assistant.local_voice.recorder import AudioRecorder
    streams = []
    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
            self.closed = False
            streams.append(self)
        def start(self):
            self.callback(np.full((48000, 1), .02, dtype=np.float32), 48000, None, None)
            self.callback(np.full((48000, 1), .02, dtype=np.float32), 48000, None, None)
        def stop(self):
            pass
        def close(self):
            self.closed = True
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(
        query_devices=lambda **_: {'default_samplerate': 48000},
        check_input_settings=lambda **_: None, InputStream=Stream))
    recorder = AudioRecorder(max_seconds=1)
    recorder.start()
    assert recorder.frames == 48000
    audio = recorder.stop()
    assert len(audio) == 16000
    assert streams[0].closed and recorder.stream is None
    assert audio.dtype == np.float32


def test_recorder_no_device_fails_without_synthetic_audio(monkeypatch):
    import sys
    from features.voice_assistant.local_voice.recorder import AudioRecorder
    def fail(**_):
        raise RuntimeError('No microphone')
    monkeypatch.setitem(sys.modules, 'sounddevice', SimpleNamespace(query_devices=fail))
    recorder = AudioRecorder()
    with pytest.raises(RuntimeError, match='No microphone'):
        recorder.start()
    with pytest.raises(ValueError, match='Không thu'):
        recorder.stop()


def test_legacy_web_voice_disabled_even_when_local_flag_enabled(monkeypatch):
    monkeypatch.setenv('VOICE_AI_ENABLED', 'true')
    from features.web_portal.api_router import ApiRouter
    from features.web_portal.auth import AuthService
    from shared.database.connection import UnifiedDatabaseManager
    db = UnifiedDatabaseManager(':memory:')
    router = ApiRouter(db, AuthService(db))
    assert router.orchestrator is None


def test_rehab_service_rechecks_plan_version_without_voice_completion(care_db):
    from features.exercise_rehab.rehab_session_adapter import RehabSessionAdapter
    db, _, _ = care_db
    adapter = RehabSessionAdapter(db)
    with db.connect() as conn:
        plan = conn.fetchone("SELECT * FROM rehab_plans WHERE patient_id='patient_001' AND exercise_id='arm_raise' AND is_active=1")
        conn.execute('UPDATE rehab_plans SET version=version+1 WHERE plan_id=?', (plan['plan_id'],))
    with pytest.raises(ValueError, match='đã đổi'):
        adapter.get_or_create_daily_session('patient_001', 'arm_raise', expected_plan=plan)


def test_frozen_tts_uses_explicit_child_entrypoint(monkeypatch):
    import sys
    from unittest.mock import patch
    from features.voice_assistant.voice_ai.audio.tts_player import TTSPlaybackCoordinator
    monkeypatch.delenv('AI_CARE_DISABLE_TTS', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    with patch('subprocess.Popen') as popen:
        TTSPlaybackCoordinator().speak('Xin chào')
        assert popen.call_args.args[0] == [sys.executable, '--local-tts', 'Xin chào']

def test_first_click_while_loading_starts_when_ready(qapp):
    c = VoiceSessionController(VoiceConfig('unused'), lambda: {'patient_id': 'p1'}, worker_target=fake_worker)
    try:
        c.start()
        wait_for(qapp, lambda: c.state == 'recording')
        assert c.session
    finally:
        c.shutdown()


def test_refresh_keeps_microphone_but_never_routes_stale_context(qapp):
    context = {'patient_id': 'p1', 'version': 1}
    c = VoiceSessionController(VoiceConfig('unused'), lambda: context.copy(), worker_target=fake_worker)
    routed, captured = [], []
    c.transcript_ready.connect(lambda *args: routed.append(args))
    c.transcript_captured.connect(lambda *args: captured.append(args))
    try:
        wait_for(qapp, lambda: c.state == 'idle')
        c.start()
        wait_for(qapp, lambda: c.state == 'recording')
        token = c.session
        context['version'] = 2
        c.poll()
        assert c.state == 'recording' and c.session == token
        c.stop()
        wait_for(qapp, lambda: c.state == 'idle')
        assert len(captured) == 1
        assert not routed
        assert 'ngữ cảnh đã đổi' in c.review_message
        c.start()
        wait_for(qapp, lambda: c.state == 'recording')
        context['patient_id'] = 'p2'
        c.poll()
        assert c.session is None
        wait_for(qapp, lambda: c.state == 'idle')
    finally:
        c.shutdown()
