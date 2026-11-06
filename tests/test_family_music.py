"""Music upload -> private media -> patient catalog -> elder playback regression."""
import io
import json
import math
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from features.music_care.family_music_service import FamilyMusicService, MAX_AUDIO_BYTES
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from features.web_portal.server import HealthcareHttpHandler


def wav_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(2 * math.pi * 440 * i / 16000)))
                                   for i in range(int(16000 * seconds))))
    return output.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from shared import config
    monkeypatch.setattr(config, 'MEDIA_DIR', tmp_path / 'media')
    db = UnifiedDatabaseManager(tmp_path / 'care.sqlite3')
    Migrator(db).apply_all()
    auth = AuthService(db)
    tokens = {}
    for role, uid in [('caregiver', 'user_care_001'), ('other', 'user_care_002'),
                      ('clinician', 'user_doc_001')]:
        tokens[role] = auth.create_session(db.fetchone('SELECT * FROM users WHERE user_id = ?', (uid,)))
    return db, auth, ApiRouter(db, auth), tokens, tmp_path


def upload(env, data=None, filename='Bài hát của gia đình.wav', title='Bài hát dành cho bác'):
    _, _, router, tokens, _ = env
    return router.handle_request('POST', '/api/patients/patient_001/music', {},
                                 {'filename': filename, 'title': title, '_audio_bytes': wav_bytes() if data is None else data},
                                 tokens['caregiver'])


def test_upload_persists_bytes_metadata_event_and_deduplicates(env):
    db, _, router, tokens, tmp = env
    data = wav_bytes()
    status, saved = upload(env, data)
    assert status == 201, saved
    track_id = saved['track']['track_id']
    row = db.fetchone('SELECT * FROM family_music WHERE track_id = ?', (track_id,))
    assert (tmp / 'media' / row['stored_name']).read_bytes() == data
    assert row['duration_seconds'] == 1
    assert row['original_filename'] == 'Bài hát của gia đình.wav'
    assert '_audio_path' not in saved['track'] and 'stored_name' not in saved['track']
    status, listing = router.handle_request('GET', '/api/patients/patient_001/music', {}, {}, tokens['caregiver'])
    assert status == 200 and listing['tracks'][0] == saved['track']
    duplicate_status, duplicate = upload(env, data)
    assert duplicate_status == 200 and duplicate['created'] is False
    assert duplicate['track']['track_id'] == track_id
    assert len(list((tmp / 'media').iterdir())) == 1
    events = db.fetchall("SELECT * FROM outbox_events WHERE event_type = 'music.track_uploaded'")
    assert len(events) == 1
    assert json.loads(events[0]['payload_minimal'])['track_id'] == track_id


def test_real_mp3_can_be_uploaded_and_decoded(env):
    import av
    encoded = io.BytesIO()
    with av.open(encoded, mode='w', format='mp3') as output:
        stream = output.add_stream('libmp3lame', rate=16000)
        stream.layout = 'mono'
        with av.open(io.BytesIO(wav_bytes())) as source:
            for frame in source.decode(audio=0):
                for packet in stream.encode(frame):
                    output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    status, result = upload(env, data=encoded.getvalue(), filename='Bài hát.mp3')
    assert status == 201, result
    assert result['track']['duration_seconds'] > 0


@pytest.mark.parametrize('data,filename', [
    (b'', 'empty.wav'), (b'not audio', 'broken.mp3'), (wav_bytes(), 'fake.mp3'),
    (wav_bytes(), 'file.exe'), (None, 'missing.wav'),
], ids=['empty', 'corrupt_mp3', 'mismatched_container', 'unsupported_extension', 'missing_bytes'])
def test_reject_invalid_audio_without_file_or_partial_row(env, data, filename):
    db, _, router, tokens, tmp = env
    status, result = router.handle_request('POST', '/api/patients/patient_001/music', {},
        {'filename': filename, '_audio_bytes': data}, tokens['caregiver'])
    assert status == 400, result
    assert db.fetchone('SELECT COUNT(*) AS n FROM family_music')['n'] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM outbox_events WHERE event_type = 'music.track_uploaded'")['n'] == 0
    assert not (tmp / 'media').exists()


def test_upload_uses_generated_filename_and_cannot_escape_media(env):
    db, _, _, _, tmp = env
    status, saved = upload(env, filename='../../outside.wav')
    assert status == 201
    row = db.fetchone('SELECT * FROM family_music')
    assert row['original_filename'] == 'outside.wav'
    assert FamilyMusicService(db).audio_path(row).parent == (tmp / 'media').resolve()
    assert not (tmp / 'outside.wav').exists()


def test_upload_permissions_and_patient_scoped_audio(env):
    db, _, router, tokens, _ = env
    status, saved = upload(env)
    track_id = saved['track']['track_id']
    for method, path, token in [
        ('POST', '/api/patients/patient_001/music', tokens['other']),
        ('POST', '/api/patients/patient_001/music', tokens['clinician']),
        ('GET', f'/api/patients/patient_001/music/{track_id}/audio', tokens['other']),
        ('GET', '/api/patients/patient_001/music', None),
    ]:
        status, _ = router.handle_request(method, path, {}, {'filename': 'x.wav', '_audio_bytes': wav_bytes()}, token)
        assert status in (401, 403)
    status, _ = router.handle_request('GET', f'/api/patients/patient_002/music/{track_id}/audio', {}, {}, tokens['other'])
    assert status == 404
    assert db.fetchone('SELECT COUNT(*) AS n FROM family_music')['n'] == 1


def test_failed_database_write_cleans_up_media(env, monkeypatch):
    import features.music_care.family_music_service as module
    db, _, _, _, tmp = env
    def fail(*args, **kwargs):
        raise RuntimeError('simulated outbox failure')
    monkeypatch.setattr(module, 'insert_outbox_event', fail)
    with pytest.raises(RuntimeError):
        upload(env)
    assert db.fetchone('SELECT COUNT(*) AS n FROM family_music')['n'] == 0
    assert list((tmp / 'media').iterdir()) == []


@pytest.fixture
def http_env(env):
    db, auth, router, tokens, tmp = env
    handler = type('TestMusicHandler', (HealthcareHttpHandler,), {'router': router, 'auth': auth})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield env, f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_real_http_binary_upload_and_private_range_playback(http_env):
    env, base = http_env
    _, _, _, tokens, _ = env
    data = wav_bytes()
    request = urllib.request.Request(base + '/api/patients/patient_001/music', data=data, method='POST', headers={
        'Authorization': 'Bearer ' + tokens['caregiver'], 'Content-Type': 'audio/wav',
        'X-File-Name': urllib.parse.quote('Gia đình.wav'), 'X-Track-Title': urllib.parse.quote('Lời ru của mẹ')})
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 201
        saved = json.load(response)
    url = base + '/api/patients/patient_001/music/' + saved['track']['track_id'] + '/audio'
    for range_header, expected in [('bytes=0-15', data[:16]), ('bytes=-16', data[-16:])]:
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tokens['caregiver'], 'Range': range_header})
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 206
            assert response.headers['Content-Type'] == 'audio/wav'
            assert response.read() == expected
    # Browser audio uses its own GET request with a session token.
    with urllib.request.urlopen(url + '?token=' + tokens['caregiver'], timeout=5) as response:
        assert response.read() == data
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, timeout=5)
    assert error.value.code == 401
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + tokens['caregiver'], 'Range': 'bytes=999999999-'})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 416


def test_http_rejects_oversized_upload_before_reading_body(http_env):
    env, base = http_env
    _, _, _, tokens, _ = env
    request = urllib.request.Request(base + '/api/patients/patient_001/music', data=b'x', method='POST', headers={
        'Authorization': 'Bearer ' + tokens['caregiver'], 'Content-Length': str(MAX_AUDIO_BYTES + 1)})
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 413


def game_repo(db):
    from features.music_care.cognitive_games.database.connection import DatabaseManager
    from features.music_care.cognitive_games.database.seed_data import seed_catalogs_only
    from features.music_care.cognitive_games.adapters.music_repository import SqlMusicRepository
    games = DatabaseManager(':memory:')
    seed_catalogs_only(games)
    with games.connect() as conn:
        for pid in ('pat-001', 'pat-002'):
            conn.execute('INSERT OR IGNORE INTO users (user_id, email, phone, full_name, created_at) VALUES (?, ?, ?, ?, ?)',
                         (pid, pid + '@test.local', '', pid, '2026-01-01T00:00:00Z'))
            conn.execute('INSERT OR IGNORE INTO patients (patient_id, user_id, full_name, date_of_birth, gender) VALUES (?, ?, ?, ?, ?)',
                         (pid, pid, pid, '1950-01-01', 'Nam'))
    return SqlMusicRepository(games, family_db=db, patient_id='patient_001')


def test_elder_catalog_scopes_uploads_and_supports_listening_history(env):
    from features.music_care.cognitive_games.services.audio_service import AudioService
    db, _, _, _, _ = env
    status, saved = upload(env)
    track_id = saved['track']['track_id']
    repo = game_repo(db)
    assert any(t.track_id == track_id for t in repo.list_tracks())
    service = AudioService(repo)
    service.start_playback('pat-001', track_id)
    assert service.current_track.asset_uri.endswith('.wav')
    service.stop_playback()
    assert repo.list_listening_sessions('pat-001')[0].track_id == track_id
    repo.set_patient_id('patient_002')
    assert all(not t.track_id.startswith('family_') for t in repo.list_tracks())
    assert repo.get_track(track_id) is None


def test_elder_widget_receives_new_track_without_autoplay_or_interrupt(env):
    from PyQt5.QtWidgets import QApplication
    from features.music_care.cognitive_games.services.audio_service import AudioService
    from features.music_care.cognitive_games.ui.music_player_widget import MusicPlayerWidget
    db, _, _, _, _ = env
    app = QApplication.instance() or QApplication([])
    repo = game_repo(db)
    service = AudioService(repo)
    widget = MusicPlayerWidget(service)
    class Player:
        calls = []
        def setMedia(self, media): self.media = media
        def setVolume(self, volume): pass
        def play(self): self.calls.append('play')
        def stop(self): self.calls.append('stop')
    player = Player()
    widget._media_player = player
    try:
        selected = widget.combo_tracks.currentData()
        status, saved = upload(env)
        widget._refresh_track_list()
        track_id = saved['track']['track_id']
        assert widget.combo_tracks.findData(track_id) >= 0
        assert '[Người nhà]' in widget.combo_tracks.itemText(widget.combo_tracks.findData(track_id))
        assert widget.combo_tracks.currentData() == (selected or track_id)
        assert not service.is_playing and player.calls == []
        widget.combo_tracks.setCurrentIndex(widget.combo_tracks.findData(track_id))
        widget._on_play_clicked()
        assert service.is_playing and player.calls == ['play']
        assert Path(player.media.toLocalFile()) == Path(repo.get_track(track_id).asset_uri)
        # A different upload must not stop this track or reset the dropdown.
        upload(env, data=wav_bytes(2), title='Bài thứ hai')
        widget._refresh_track_list()
        assert widget.combo_tracks.currentData() == track_id
        assert service.current_track.track_id == track_id
        assert player.calls == ['play']
        repo.set_patient_id('patient_002')
        widget.set_patient_id('pat-002')
        assert not service.is_playing and player.calls[-1] == 'stop'
        assert widget.combo_tracks.findData(track_id) == -1
    finally:
        widget._catalog_timer.stop()
        widget._tick_timer.stop()
        widget.close()


def test_elder_todo_no_longer_has_manual_mood_confirmation(env):
    from PyQt5.QtWidgets import QApplication, QPushButton
    from apps.desktop.today_todo_widget import TodayTodoWidget
    from shared.services.elder_sync_client import ElderSyncClient
    db, _, _, _, tmp = env
    app = QApplication.instance() or QApplication([])
    widget = TodayTodoWidget(db, sync_client=ElderSyncClient(db, queue_dir=tmp / 'queue'))
    try:
        assert not hasattr(widget, 'mood_card')
        assert not any(button.text() == '● Buồn' for button in widget.findChildren(QPushButton))
    finally:
        widget._refresh_timer.stop()
        widget.close()
