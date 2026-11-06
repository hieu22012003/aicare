"""Release databases must accept and expose vitals without the legacy importer."""
import json
from datetime import datetime, timezone

import pytest

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.elder_sync_client import ElderSyncClient
from shared.services.outbox_relay import OutboxRelayService
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import sse_hub


@pytest.fixture
def env(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / 'care.sqlite3')
    Migrator(db).apply_all()
    auth = AuthService(db)
    # Sessions use actors from migrations; no legacy seed/import path.
    tokens = {}
    for role, uid in [('caregiver', 'user_care_001'), ('clinician', 'user_doc_001')]:
        user = db.fetchone('SELECT * FROM users WHERE user_id = ?', (uid,))
        tokens[role] = auth.create_session(user)
    return db, ApiRouter(db, auth), tokens, tmp_path


def payload():
    return {
        'client_request_id': 'vitals-regression',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'source_type': 'manual_caregiver',
        'items': [
            {'code': code, 'value': val}
            for code, val in [('blood_pressure_systolic', 128),
                              ('blood_pressure_diastolic', 84), ('heart_rate', 76),
                              ('spo2', 98), ('blood_glucose', 108), ('temperature', 36.7)]
        ],
    }


def test_migration_only_save_history_charts_overviews_and_elder_snapshot(env):
    db, router, tokens, tmp_path = env
    body = payload()
    path = '/api/patients/patient_001/observations'
    status, saved = router.handle_request('POST', path, {}, body, tokens['caregiver'])
    assert status == 201, saved
    for token in tokens.values():
        status, history = router.handle_request('GET', path, {'type': 'all'}, {}, token)
        assert status == 200
        row = history['measurements'][0]
        assert [row[k] for k in ('systolic', 'diastolic', 'heart_rate', 'spo2', 'blood_glucose', 'temperature')] == [128, 84, 76, 98, 108, 36.7]
        for code, expected in [('blood_pressure', 128), ('heart_rate', 76), ('spo2', 98), ('blood_glucose', 108), ('temperature', 36.7)]:
            status, chart = router.handle_request('GET', path, {'type': code, 'days': '30'}, {}, token)
            assert status == 200
            assert chart['readings'][-1]['systolic' if code == 'blood_pressure' else 'value_numeric'] == expected
        status, overview = router.handle_request('GET', '/api/patients/patient_001/overview', {}, {}, token)
        assert status == 200
        assert overview['latest_blood_pressure']['systolic'] == 128
        assert overview['latest_heart_rate']['value_numeric'] == 76
    snapshot = ElderSyncClient(db, queue_dir=tmp_path / 'queue').get_today_snapshot()
    assert snapshot['latest_blood_pressure']['systolic'] == 128
    events = db.fetchall("SELECT payload_minimal FROM outbox_events WHERE event_type = 'health.observation_created'")
    assert len(events) == 1
    assert json.loads(events[0]['payload_minimal'])['item_count'] == 6
    assert router.handle_request('POST', path, {}, body, tokens['caregiver'])[0] == 200
    assert db.fetchone('SELECT COUNT(*) AS n FROM health_observations')['n'] == 6


@pytest.mark.parametrize('bad_item', [
    {'code': 'unknown', 'value': 120},
    {'code': 'heart_rate'},
    {'code': 'heart_rate', 'value': 'invalid'},
    {'code': 'heart_rate', 'value': float('nan')},
    {'code': 'heart_rate', 'value': float('inf')},
    {'code': 'heart_rate', 'value': True},
    {'code': 'blood_pressure_systolic', 'value': 120},
    'invalid',
])
def test_invalid_batch_has_no_partial_group_observations_or_events(env, bad_item):
    db, router, tokens, _ = env
    body = payload()
    body['items'] = [body['items'][0], bad_item]
    status, _ = router.handle_request('POST', '/api/patients/patient_001/observations', {}, body, tokens['caregiver'])
    assert status == 400
    for table in ('measurement_groups', 'health_observations', 'outbox_events'):
        assert db.fetchone(f'SELECT COUNT(*) AS n FROM {table}')['n'] == 0


def test_missing_catalog_returns_error_without_empty_measurement(env):
    db, router, tokens, _ = env
    db.execute('DELETE FROM observation_types')
    status, _ = router.handle_request('POST', '/api/patients/patient_001/observations', {}, payload(), tokens['caregiver'])
    assert status == 400
    assert db.fetchone('SELECT COUNT(*) AS n FROM measurement_groups')['n'] == 0


def test_catalog_migration_preserves_existing_type_ids(env):
    db, _, _, _ = env
    db.execute("UPDATE observation_types SET observation_type_id = 'existing-sys' WHERE code = 'blood_pressure_systolic'")
    db.execute("DELETE FROM schema_migrations WHERE version = '008_seed_observation_types'")
    Migrator(db).apply_all()
    assert db.fetchone("SELECT observation_type_id FROM observation_types WHERE code = 'blood_pressure_systolic'")['observation_type_id'] == 'existing-sys'
    assert db.fetchone('SELECT COUNT(*) AS n FROM observation_types')['n'] == 7


def test_saved_vitals_emit_refresh_signal_to_both_authorized_portals(env):
    db, router, tokens, _ = env
    clients = [sse_hub.register_client('user_care_001', 'caregiver'),
               sse_hub.register_client('user_doc_001', 'clinician')]
    try:
        assert router.handle_request('POST', '/api/patients/patient_001/observations', {}, payload(), tokens['caregiver'])[0] == 201
        relay = OutboxRelayService(db)
        assert relay.process_pending_events() == 1
        for client in clients:
            signal = client.queue.get_nowait()
            assert signal['type'] == 'health.observation_created'
            assert signal['patient_id'] == 'patient_001'
            assert signal['payload']['item_count'] == 6
    finally:
        for client in clients:
            sse_hub.unregister_client(client.client_id)


def test_elder_widget_refresh_renders_new_measurement(env):
    from PyQt5.QtWidgets import QApplication
    from apps.desktop.today_todo_widget import TodayTodoWidget
    db, router, tokens, tmp_path = env
    app = QApplication.instance() or QApplication([])
    widget = TodayTodoWidget(db, sync_client=ElderSyncClient(db, queue_dir=tmp_path / 'queue'))
    try:
        assert router.handle_request('POST', '/api/patients/patient_001/observations', {}, payload(), tokens['caregiver'])[0] == 201
        widget.refresh_data()
        layout = widget.vitals_content_layout
        text = ' '.join(layout.itemAt(i).widget().text() for i in range(layout.count()) if layout.itemAt(i).widget())
        assert '128' in text and '84' in text and 'mmHg' in text
    finally:
        widget._refresh_timer.stop()
        widget.close()
