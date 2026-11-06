"""Set a local account password interactively; never accept passwords on the command line."""
import argparse
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.services.credential_security import hash_password, LEGACY_DEFAULTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--user-id', default='user_admin_001')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error('Run this command in an interactive terminal.')
    password = getpass.getpass('New password (at least 12 characters): ')
    confirm = getpass.getpass('Confirm password: ')
    if password != confirm or len(password) < 12 or password in LEGACY_DEFAULTS:
        parser.error('Passwords must match and contain at least 12 characters.')
    database = UnifiedDatabaseManager(args.database)
    Migrator(database).apply_all()
    with database.connect() as conn:
        if not conn.fetchone('SELECT user_id FROM users WHERE user_id=?', (args.user_id,)):
            parser.error('Account cannot be updated.')
        conn.execute('UPDATE users SET password_hash=? WHERE user_id=?',
                     (hash_password(password), args.user_id))
    print('Password updated. Previous sessions will no longer be accepted.')


if __name__ == '__main__':
    main()
