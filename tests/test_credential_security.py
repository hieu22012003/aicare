import json
import http.client
from pathlib import Path

import pytest
from unittest.mock import patch

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.credential_security import (
    DISABLED, hash_password, verify_password, migrate_database_passwords,
    protect_profile_credentials,
)
from shared.services.patient_sync_service import sync_single_patient, sync_all_patient_profiles
from features.web_portal.auth import AuthService
from features.web_portal.api_router import ApiRouter
from features.web_portal.server import HealthcareWebServer

PASSWORD = 'Synthetic-Only-Passphrase-42!'


@pytest.fixture
def env(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / 'care.sqlite3')
    Migrator(db).apply_all()
    with db.connect() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE user_id=?',
                     (hash_password(PASSWORD), 'user_admin_001'))
    auth = AuthService(db)
    return db, auth, ApiRouter(db, auth)


def test_password_hash_is_salted_one_way_and_exact():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first)
    assert not verify_password(PASSWORD + ' ', first)
    for wrong in ('', 'demo', 'admin123', 'not-the-password'):
        assert not verify_password(wrong, first)


@pytest.mark.parametrize('stored', ['', '!', 'pbkdf2:', 'pbkdf2:demo', 'pbkdf2_sha256$999999999$x$y', 'malformed'])
def test_invalid_or_legacy_hash_is_never_a_login_credential(stored):
    assert not verify_password('demo', stored)


@pytest.mark.parametrize('password', ['', 'demo', 'admin123', '123456', 'wrong'])
def test_no_default_or_empty_password_bypasses_custom_admin(env, password):
    _, _, router = env
    status, response = router.handle_request('POST', '/api/auth/login', {}, {'email': 'admin', 'password': password})
    assert status == 401
    assert response == {'error': 'Thông tin đăng nhập không hợp lệ'}
    assert PASSWORD not in json.dumps(response)


def test_auth_errors_do_not_enumerate_accounts_or_read_profile_password(env):
    db, auth, _ = env
    missing = auth.authenticate_credentials('absent@example.test', 'wrong')
    wrong = auth.authenticate_credentials('admin', 'wrong')
    with db.connect() as conn:
        conn.execute("UPDATE users SET is_active=0 WHERE user_id='user_admin_001'")
    locked = auth.authenticate_credentials('admin', PASSWORD)
    assert missing == wrong == locked
    assert not hasattr(auth, '_find_desktop_profile_password')


@pytest.mark.parametrize('path,method', [('/api/auth/quick-accounts', 'GET'), ('/api/public/patients', 'GET'),
                                       ('/api/auth/register', 'POST'), ('/api/auth/register-caregiver', 'POST')])
def test_anonymous_discovery_and_patient_claiming_removed(env, path, method):
    db, _, router = env
    before = db.fetchone('SELECT count(*) AS count FROM users')['count']
    status, response = router.handle_request(method, path, {}, {'email': 'attacker@example.test', 'password': PASSWORD})
    assert status == 404
    assert 'admin' not in json.dumps(response) and PASSWORD not in json.dumps(response)
    assert db.fetchone('SELECT count(*) AS count FROM users')['count'] == before


def test_success_response_contains_no_hash_and_reset_invalidates_session(env):
    db, auth, router = env
    status, response = router.handle_request('POST', '/api/auth/login', {}, {'email': 'admin', 'password': PASSWORD})
    assert status == 200
    token = response['token']
    assert auth.get_session(token)
    assert 'password' not in json.dumps(response).lower()
    with db.connect() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE user_id=?', (hash_password(PASSWORD + 'new'), 'user_admin_001'))
    assert auth.get_session(token) is None


def test_legacy_upgrade_preserves_custom_password_and_disables_shipped_defaults(tmp_path):
    db = UnifiedDatabaseManager(tmp_path / 'legacy.sqlite3')
    Migrator(db).apply_all()
    for value in ('', 'pbkdf2:demo', 'pbkdf2:admin123', '123456', 'password123'):
        with db.connect() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE user_id='user_admin_001'", (value,))
        migrate_database_passwords(db)
        assert db.fetchone("SELECT password_hash FROM users WHERE user_id='user_admin_001'")['password_hash'] == DISABLED
    with db.connect() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE user_id='user_admin_001'", ('pbkdf2:' + PASSWORD,))
    migrate_database_passwords(db)
    saved = db.fetchone("SELECT password_hash FROM users WHERE user_id='user_admin_001'")['password_hash']
    assert PASSWORD not in saved and verify_password(PASSWORD, saved)


def test_sync_does_not_restore_old_password_after_admin_reset(env):
    db, _, _ = env
    profile = {'patient_id': '987', 'name': 'Synthetic Patient', 'caregiver': {
        'email': 'family@example.test', 'password': PASSWORD}}
    sync_single_patient(db, profile)
    new_hash = hash_password(PASSWORD + 'new')
    with db.connect() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE email=?', (new_hash, 'family@example.test'))
    sync_single_patient(db, profile)
    assert db.fetchone('SELECT password_hash FROM users WHERE email=?', ('family@example.test',))['password_hash'] == new_hash


def test_legacy_profile_files_are_rewritten_without_cleartext_credentials(env, tmp_path):
    db, _, _ = env
    users = tmp_path / 'profiles/data/users'
    shared = tmp_path / 'profiles/shared/data'
    users.mkdir(parents=True)
    shared.mkdir(parents=True)
    profile = {'patient_id': '987', 'name': 'Synthetic Patient', 'caregiver': {
        'email': 'family@example.test', 'password': PASSWORD}}
    (users / '987.json').write_text(json.dumps(profile), encoding='utf-8')
    (shared / 'patients.json').write_text(json.dumps({'987': profile}), encoding='utf-8')
    sync_all_patient_profiles(db, tmp_path / 'profiles')
    for path in (users / '987.json', shared / 'patients.json'):
        text = path.read_text(encoding='utf-8')
        assert PASSWORD not in text and '"password"' not in text
    stored = db.fetchone('SELECT password_hash FROM users WHERE email=?', ('family@example.test',))['password_hash']
    assert verify_password(PASSWORD, stored)


def test_login_page_contains_no_quick_accounts_or_default_credentials():
    html = (Path(__file__).resolve().parents[1] / 'features/web_portal/templates/login.html').read_text(encoding='utf-8')
    assert 'quick-accounts' not in html
    assert 'admin123' not in html and '123456' not in html
    assert 'demoAccounts' not in html


@pytest.mark.parametrize('kind,id_field', [('doctors', 'clinician_id'), ('caregivers', 'caregiver_id')])
def test_admin_creation_and_reset_never_return_or_store_cleartext(env, kind, id_field):
    db, auth, router = env
    token = auth.create_session(auth.authenticate_user('admin', PASSWORD))
    body = {'full_name': 'Synthetic Account', 'email': f'{kind}@example.test', 'password': PASSWORD + kind}
    status, result = router.handle_request('POST', f'/api/admin/{kind}', {}, body, token)
    assert status == 201
    row = db.fetchone('SELECT password_hash FROM users WHERE email=?', (body['email'],))
    assert verify_password(body['password'], row['password_hash'])
    assert body['password'] not in json.dumps(result) and 'password_hash' not in result
    account = auth.authenticate_user(body['email'], body['password'])
    session = auth.create_session(account)
    new_password = PASSWORD + 'reset'
    status, result = router.handle_request('POST', f'/api/admin/{kind}/{result[id_field]}/reset-password', {},
                                           {'new_password': new_password}, token)
    assert status == 200
    assert new_password not in json.dumps(result)
    assert auth.get_session(session) is None
    assert auth.authenticate_user(body['email'], body['password']) is None
    assert auth.authenticate_user(body['email'], new_password)


@pytest.mark.parametrize('kind', ['doctors', 'caregivers'])
def test_admin_cannot_silently_create_account_with_default_password(env, kind):
    _, auth, router = env
    token = auth.create_session(auth.authenticate_user('admin', PASSWORD))
    status, _ = router.handle_request('POST', f'/api/admin/{kind}', {},
                                      {'full_name': 'Synthetic', 'email': 'new@example.test'}, token)
    assert status == 400


def test_real_http_login_and_anonymous_routes_do_not_expose_credentials(env):
    db, _, _ = env
    with patch('shared.services.patient_sync_service.sync_all_patient_profiles'):
        server = HealthcareWebServer(db, port=0)
    server.start()
    connection = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
    try:
        for path in ('/api/auth/quick-accounts', '/api/public/patients'):
            connection.request('GET', path)
            response = connection.getresponse()
            assert response.status == 404
            assert PASSWORD not in response.read().decode()
        for identifier, password in [('admin', 'demo'), ('absent@example.test', 'wrong'), (['invalid'], PASSWORD)]:
            connection.request('POST', '/api/auth/login', json.dumps({'email': identifier, 'password': password}),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            assert response.status == 401
            assert json.loads(response.read()) == {'error': 'Thông tin đăng nhập không hợp lệ'}
        connection.request('POST', '/api/auth/login', json.dumps({'email': 'admin', 'password': PASSWORD}),
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        assert response.status == 200
        payload = response.read().decode()
        assert PASSWORD not in payload and 'password_hash' not in payload
    finally:
        connection.close()
        server.stop()
