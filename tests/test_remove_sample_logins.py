"""Regression checks for V3's sample-account removal migration."""
from pathlib import Path
import sqlite3
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT/'shared/database/migrations'


def test_fresh_install_has_no_sample_logins():
    with sqlite3.connect(':memory:') as db:
        db.execute('PRAGMA foreign_keys=ON')
        for path in sorted(MIGRATIONS.glob('*.sql')):
            db.executescript(path.read_text(encoding='utf-8'))
        assert db.execute("SELECT count(*) FROM users WHERE role IN ('caregiver','clinician')").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM patients').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_upgrade_removes_seven_and_preserves_registered_account():
    with sqlite3.connect(':memory:') as db, zipfile.ZipFile(ROOT/'releases/AI_FOR_EXAM_PT.zip') as old:
        db.execute('PRAGMA foreign_keys=ON')
        for path in sorted(MIGRATIONS.glob('*.sql')):
            if path.name.startswith('013_'):
                continue
            sql = old.read('AICare/app/database/migrations/'+path.name).decode('utf-8-sig')
            db.executescript(sql)
        db.execute("INSERT INTO users VALUES ('registered-user','registered@example.test','', 'Registered user','pbkdf2:custom','caregiver',1,'2026-10-30')")
        db.execute("INSERT INTO caregivers VALUES ('registered-caregiver','registered-user',1,1,'Family')")
        db.commit()
        sql = (MIGRATIONS/'013_remove_sample_login_accounts.sql').read_text(encoding='utf-8')
        db.executescript(sql)
        db.executescript(sql)
        assert db.execute("SELECT count(*) FROM users WHERE role IN ('caregiver','clinician')").fetchone()[0] == 1
        assert db.execute("SELECT email FROM users WHERE user_id='registered-user'").fetchone()[0] == 'registered@example.test'
        assert db.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM patients').fetchone()[0] == 2
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
