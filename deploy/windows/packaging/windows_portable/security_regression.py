"""Exercise authorization, Unicode storage and ephemeral HTTP on the bundled runtime."""
import http.client
from pathlib import Path
import tempfile
import sys
from unittest.mock import patch

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.patient_sync_service import sync_single_patient
from shared.services.outbox_relay import OutboxRelayService
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import SSEHub
from features.web_portal.server import HealthcareHttpHandler, HealthcareWebServer


def run():
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    with tempfile.TemporaryDirectory() as folder:
        db = UnifiedDatabaseManager(Path(folder) / 'regression.sqlite3')
        Migrator(db).apply_all()
        profile = {'patient_id': '987', 'name': 'Cụ Hoàng Anh', 'caregiver': {
            'name': 'Người thân', 'email': 'family@example.test', 'password': 'test123'}}
        hub = SSEHub()
        admin = hub.register_client('user_admin_001', 'admin')
        unrelated = hub.register_client('unrelated-user', 'caregiver')
        with patch('features.web_portal.sse_hub.sse_hub', hub):
            pid = sync_single_patient(db, profile)
        assert admin.queue.empty() and unrelated.queue.empty(), 'Unfiltered immediate SSE'
        owner = hub.register_client('user_care_987', 'caregiver')
        auth = AuthService(db)
        with patch('shared.services.outbox_relay.sse_hub', hub):
            assert OutboxRelayService(db, auth).process_pending_events() >= 2
        assert not admin.queue.empty() and not owner.queue.empty()
        assert unrelated.queue.empty(), 'Unauthorized patient event delivery'
        with db.connect() as conn:
            assert conn.fetchone('SELECT full_name FROM patients WHERE patient_id=?', (pid,))['full_name'] == profile['name']
            conn.execute("UPDATE patient_caregivers SET is_active=0, consent_status='REVOKED', can_view_health=0, can_edit_schedule=0 WHERE patient_id=?", (pid,))
            conn.execute("UPDATE users SET is_active=0 WHERE email='family@example.test'")
            conn.commit()
        sync_single_patient(db, profile, emit_events=False)
        with db.connect() as conn:
            rel = conn.fetchone('SELECT * FROM patient_caregivers WHERE patient_id=?', (pid,))
            assert (rel['is_active'], rel['consent_status'], rel['can_view_health'], rel['can_edit_schedule']) == (0, 'REVOKED', 0, 0)
            assert conn.fetchone("SELECT is_active FROM users WHERE email='family@example.test'")['is_active'] == 0
        assert not auth.check_patient_access('user_care_987', pid)[0]
        with patch('shared.services.patient_sync_service.sync_all_patient_profiles'):
            server = HealthcareWebServer(db, port=0)
        server.start()
        try:
            assert server.port > 0 and server.port == server.httpd.server_port
            connection = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
            connection.request('GET', '/health')
            response = connection.getresponse()
            assert response.status == 200
            response.read()
            connection.close()
        finally:
            server.stop()
        for error in (ConnectionAbortedError(), ConnectionResetError(), BrokenPipeError()):
            with patch('http.server.BaseHTTPRequestHandler.handle', side_effect=error):
                object.__new__(HealthcareHttpHandler).handle()
        try:
            with patch('http.server.BaseHTTPRequestHandler.handle', side_effect=RuntimeError('unexpected')):
                object.__new__(HealthcareHttpHandler).handle()
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unexpected HTTP errors were suppressed')
    print('PASS: authorization, revocation, account lock, Unicode, ephemeral port, HTTP disconnects')


if __name__ == '__main__':
    run()
