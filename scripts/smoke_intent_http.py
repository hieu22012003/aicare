"""Loopback E2E after transcript: real HTTP/auth/Qt + mocked Gemini, fictional in-memory DB."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['EMAIL_ENABLED'] = 'false'
from PyQt5.QtWidgets import QApplication
from http.server import ThreadingHTTPServer
import requests
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.web_portal.server import HealthcareHttpHandler
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from features.voice_assistant.transcript_intent.context import read_state
from features.voice_assistant.transcript_intent.client import GeminiIntentClient
from features.voice_assistant.transcript_intent.coordinator import VoiceRequestCoordinator
from features.voice_assistant.transcript_intent.schema import proposal, Decision
from features.voice_assistant.transcript_intent.registry import Intent
from features.voice_assistant.local_voice.dispatcher import CommandDispatcher


def main():
    db = UnifiedDatabaseManager(':memory:')
    Migrator(db).apply_all()
    with db.connect() as conn:
        conn.execute("INSERT INTO users(user_id,email,phone,full_name,role,created_at) VALUES ('synthetic_admin','contact-12b0be49@example.com','','Synthetic admin','admin','2026-01-01')")
        conn.execute("INSERT INTO patients(patient_id,full_name) VALUES ('synthetic_patient','Synthetic patient')")
        user = conn.fetchone("SELECT * FROM users WHERE user_id='synthetic_admin'")
    auth = AuthService(db)
    token = auth.create_session(user)
    router = ApiRouter(db, auth)
    text = 'Cho tôi quay về màn hình ban đầu'
    service = Mock()
    service.classify.return_value = dict(proposal=proposal(Intent.HOME, Decision.ROUTE, text).model_dump(mode='json'), outcome='mock')
    router.transcript_intent_endpoint.service = service
    HealthcareHttpHandler.router, HealthcareHttpHandler.auth = router, auth
    server = ThreadingHTTPServer(('127.0.0.1', 0), HealthcareHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app = QApplication.instance() or QApplication([])
    current = read_state(db, 'synthetic_patient')
    current.update(tab=2, running=False, paused=False, source='camera', exercise='', epoch=1)
    tabs = Mock()
    dispatcher = CommandDispatcher(SimpleNamespace(medication_tabs=tabs))
    dispatcher.snapshot = lambda: current.copy()
    client = GeminiIntentClient('http://127.0.0.1:' + str(server.server_port), token, True)
    coordinator = VoiceRequestCoordinator(dispatcher.snapshot, client=client)
    outcomes = []
    coordinator.resolved.connect(lambda result, captured, value: outcomes.append(dispatcher.dispatch(result, captured, lambda _: False, value)))
    started = time.monotonic()
    try:
        coordinator.route(dict(session_id='synthetic_session',transcript=text), current.copy())
        while not outcomes and time.monotonic()-started < 3:
            app.processEvents()
            time.sleep(.005)
        assert outcomes == ['Đã về trang chính.']
        tabs.setCurrentIndex.assert_called_once_with(0)
        service.classify.assert_called_once()
        assert read_state(db, 'synthetic_patient')['doses'] == []
        headers = {'Authorization':'Bearer ' + token}
        url = client.url + '/api/transcript-intent'
        assert requests.post(url,json={},timeout=2).status_code == 401
        assert requests.post(url,json={'synthetic':True},headers=headers,timeout=2).status_code == 400
        assert requests.post(url,data='a'*4097,headers=headers,timeout=2).status_code == 413
        assert requests.post(url,data=bytes([255]),headers=headers,timeout=2).status_code == 400
        auth.revoke_session(token)
        assert requests.post(url,json={},headers=headers,timeout=2).status_code == 401
        report = dict(mode='transcript_http_qt_mock_gemini', navigation_dispatched_once=True,
                      authentication_validation_and_size_checks=True, clinical_db_unchanged=True,
                      no_email=True, gemini_calls=0, microphone_tested=False,
                      latency_ms=round((time.monotonic()-started)*1000))
        Path('docs/test-reports/gemini_intent/http-smoke.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
    finally:
        coordinator.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(1)


if __name__ == '__main__':
    main()
