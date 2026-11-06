"""
Database Migration Runner.
Applies SQL migrations sequentially and tracks version history in schema_migrations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from shared.database.connection import UnifiedDatabaseManager

logger = logging.getLogger("Migrator")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Migrator:
    def __init__(self, db: UnifiedDatabaseManager, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self.db = db
        self.migrations_dir = migrations_dir

    def get_applied_migrations(self) -> List[str]:
        with self.db.connect() as conn:
            # Ensure table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(64) PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """)
            rows = conn.fetchall("SELECT version FROM schema_migrations ORDER BY version")
            return [r["version"] for r in rows]

    def apply_all(self) -> List[str]:
        """Apply all pending migration files in lexicographical order."""
        applied = self.get_applied_migrations()
        migration_files = sorted(self.migrations_dir.glob("*.sql"))

        applied_now = []
        for mf in migration_files:
            v_name = mf.stem
            if v_name in applied:
                continue

            logger.info("Applying migration: %s", v_name)
            with open(mf, "r", encoding="utf-8") as f:
                sql_content = f.read()

            with self.db.connect() as conn:
                conn.executescript(sql_content)
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (v_name, now_iso),
                )
            applied_now.append(v_name)
            logger.info("Successfully applied: %s", v_name)

        from shared.services.credential_security import migrate_database_passwords
        migrate_database_passwords(self.db)
        return applied_now

