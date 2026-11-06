import http.client
from unittest.mock import MagicMock, patch

import pytest

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.patient_sync_service import sync_single_patient
from shared.services.outbox_relay import OutboxRelayService
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import SSEHub
from features.web_portal.server import HealthcareHttpHandler, HealthcareWebServer


@pytest.fixture
def db(tmp_path):
    database = UnifiedDatabaseManager(tmp_path / 'care.sqlite3')
    Migrator(database).apply_all()
    return database


def profile():
    return {'patient_id': '987', 'name': 'Cụ Hoàng Anh', 'caregiver': {
        'name': 'Người thân', 'email': 'family@example.test', 'password': 'test123'}}


def test_sync_preserves_revocation_and_account_lock(db):
    pid = sync_single_patient(db, profile(), emit_events=False)
    with db.connect() as conn:
        conn.execute("UPDATE patient_caregivers SET is_active=0, consent_status='REVOKED', can_view_health=0, can_edit_schedule=0 WHERE patient_id=?", (pid,))
        conn.execute("UPDATE users SET is_active=0 WHERE email='family@example.test'")
        conn.commit()
    sync_single_patient(db, profile(), emit_events=False)
    with db.connect() as conn:
        rel = conn.fetchone('SELECT * FROM patient_caregivers WHERE patient_id=?', (pid,))
        assert (rel['is_active'], rel['consent_status'], rel['can_view_health'], rel['can_edit_schedule']) == (0, 'REVOKED', 0, 0)
        assert conn.fetchone("SELECT is_active FROM users WHERE email='family@example.test'")['is_active'] == 0


def test_registration_relay_delivers_only_to_authorized_clients(db):
    hub = SSEHub()
    admin = hub.register_client('user_admin_001', 'admin')
    unrelated = hub.register_client('unrelated-user', 'caregiver')
    with patch('features.web_portal.sse_hub.sse_hub', hub):
        pid = sync_single_patient(db, profile())
    assert admin.queue.empty() and unrelated.queue.empty()
    owner = hub.register_client('user_care_987', 'caregiver')
    relay = OutboxRelayService(db, AuthService(db))
    with patch('shared.services.outbox_relay.sse_hub', hub):
        assert relay.process_pending_events() >= 2
    assert not owner.queue.empty() and not admin.queue.empty()
    assert unrelated.queue.empty()
    assert owner.queue.get_nowait()['patient_id'] == pid
    with db.connect() as conn:
        assert conn.fetchone('SELECT full_name FROM patients WHERE patient_id=?', (pid,))['full_name'] == 'Cụ Hoàng Anh'


def test_ephemeral_port_is_usable(db):
    with patch('shared.services.patient_sync_service.sync_all_patient_profiles'):
        server = HealthcareWebServer(db, port=0)
    server.start()
    try:
        assert server.port == server.httpd.server_port and server.port > 0
        conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
        conn.request('GET', '/health')
        response = conn.getresponse()
        assert response.status == 200
        response.read()
        conn.close()
    finally:
        server.stop()


@pytest.mark.parametrize('error', [ConnectionAbortedError(10053, 'abort'), ConnectionResetError(), BrokenPipeError()])
def test_expected_disconnect_does_not_escape_handler(error):
    handler = object.__new__(HealthcareHttpHandler)
    with patch('http.server.BaseHTTPRequestHandler.handle', side_effect=error):
        handler.handle()


def test_unexpected_http_failure_still_surfaces():
    handler = object.__new__(HealthcareHttpHandler)
    with patch('http.server.BaseHTTPRequestHandler.handle', side_effect=RuntimeError('bug')):
        with pytest.raises(RuntimeError):
            handler.handle()


def test_windows_busy_port_falls_back(db):
    with patch('shared.services.patient_sync_service.sync_all_patient_profiles'):
        server = HealthcareWebServer(db, port=8080)
    server.outbox_relay = MagicMock()
    httpd = MagicMock(server_port=8081)
    with patch('features.web_portal.server.ThreadingHTTPServer', side_effect=[OSError(10048, 'busy'), httpd]) as factory:
        server.start()
        assert [call.args[0] for call in factory.call_args_list] == [('127.0.0.1', 8080), ('127.0.0.1', 8081)]
        assert server.port == 8081
        server.stop()


def test_sync_does_not_expand_existing_active_permissions(db):
    pid = sync_single_patient(db, profile(), emit_events=False)
    with db.connect() as conn:
        conn.execute('UPDATE patient_caregivers SET can_view_health=0, can_edit_schedule=0, is_primary=0 WHERE patient_id=?', (pid,))
        conn.commit()
    sync_single_patient(db, profile(), emit_events=False)
    with db.connect() as conn:
        rel = conn.fetchone('SELECT * FROM patient_caregivers WHERE patient_id=?', (pid,))
        assert (rel['is_active'], rel['consent_status'], rel['can_view_health'], rel['can_edit_schedule'], rel['is_primary']) == (1, 'GRANTED', 0, 0, 0)
