"""Remove the seven shipped sample logins, with a shared/database/profile backup."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = '013_remove_sample_login_accounts'
EMAILS = {
    'tuan.caregiver@example.com', 'lan.caregiver@example.com',
    'binh.old@example.com', 'contact-a2e402b0@example.com',
    'dr.an@laokhoa.example.com', 'contact-4bcc03e4@example.com',
    'dr.cuong@laokhoa.example.com',
}


def clean(home):
    home = Path(home).resolve()
    database = home/'data/unified_care.sqlite3'
    if not database.is_file():
        raise FileNotFoundError(database)
    backup = home/'backups'/('remove-demo-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True, exist_ok=False)
    profiles = [home/'shared/data/patients.json', *sorted((home/'data/users').glob('*.json'))]
    updates = []
    for path in profiles:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        records = data.values() if path.name == 'patients.json' and isinstance(data, dict) else [data]
        changed = False
        for record in records:
            if not isinstance(record, dict):
                continue
            cg = record.get('caregiver') or {}
            email = str(cg.get('email') or record.get('caregiver_email') or '').strip().lower()
            if email in EMAILS:
                record['caregiver'] = {}
                for key in ('caregiver_email', 'caregiver_phone', 'caregiver_password', 'caregiver_name'):
                    record.pop(key, None)
                changed = True
        if changed:
            dest = backup/path.relative_to(home)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            updates.append((path, data))
    with sqlite3.connect(database) as conn:
        with sqlite3.connect(backup/'unified_care.sqlite3') as copy:
            conn.backup(copy)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript((ROOT/'shared/database/migrations'/f'{MIGRATION}.sql').read_text(encoding='utf-8'))
        conn.execute('INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (?,?)',
                     (MIGRATION, datetime.now(timezone.utc).isoformat()))
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    for path, data in updates:
        temporary = path.with_suffix('.cleanup.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    print(json.dumps({'backup':str(backup), 'profiles_cleaned':len(updates), 'status':'removed'},ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    clean(parser.parse_args().home)
