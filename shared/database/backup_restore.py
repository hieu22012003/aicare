"""
Database Backup, SQL Dump & Restore Verification Service.
Provides automated database dump generation and test restore verification.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from shared.database.connection import UnifiedDatabaseManager

logger = logging.getLogger("BackupRestore")


class BackupRestoreService:
    @staticmethod
    def create_sql_dump(db: UnifiedDatabaseManager, dump_file_path: Path) -> int:
        """
        Generate ANSI SQL / PostgreSQL compatible dump script from database.
        Returns the number of SQL statement lines generated.
        """
        dump_file_path = Path(dump_file_path)
        dump_file_path.parent.mkdir(parents=True, exist_ok=True)

        conn = db.get_raw_connection()
        try:
            with open(dump_file_path, "w", encoding="utf-8") as f:
                f.write(f"-- ==============================================================================\n")
                f.write(f"-- HEALTHCARE SYSTEM DATABASE BACKUP DUMP\n")
                f.write(f"-- Timestamp: {datetime.now(timezone.utc).isoformat()}\n")
                f.write(f"-- ==============================================================================\n\n")
                f.write("PRAGMA foreign_keys = OFF;\n\n")

                for line in conn.iterdump():
                    # Filter out sqlite system entries
                    if "sqlite_sequence" in line or "sqlite_stat" in line:
                        continue
                    f.write(f"{line}\n")

                f.write("\nPRAGMA foreign_keys = ON;\n")
        finally:
            if getattr(db, "_mem_conn", None) is None:
                conn.close()

        with open(dump_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        logger.info("Created backup dump at %s (%d lines)", dump_file_path, len(lines))
        return len(lines)

    @staticmethod
    def restore_from_dump(dump_file_path: Path, target_db: UnifiedDatabaseManager) -> bool:
        """
        Restore database from SQL dump into a target database instance.
        """
        dump_file_path = Path(dump_file_path)
        if not dump_file_path.exists():
            raise FileNotFoundError(f"Dump file not found: {dump_file_path}")

        with open(dump_file_path, "r", encoding="utf-8") as f:
            sql_script = f.read()

        conn = target_db.get_raw_connection()
        try:
            conn.executescript(sql_script)
            logger.info("Restored database from dump %s successfully.", dump_file_path)
            return True
        finally:
            if getattr(target_db, "_mem_conn", None) is None:
                conn.close()

    @staticmethod
    def verify_backup_and_restore(source_db: UnifiedDatabaseManager, test_dump_path: Path) -> Tuple[bool, Dict[str, int], Dict[str, int]]:
        """
        Full roundtrip verification:
        1. Dumps source_db to test_dump_path.
        2. Restores into a new isolated in-memory test database.
        3. Compares table list and row counts across all tables.
        """
        BackupRestoreService.create_sql_dump(source_db, test_dump_path)

        # Restore into new in-memory instance
        isolated_db = UnifiedDatabaseManager(":memory:")
        BackupRestoreService.restore_from_dump(test_dump_path, isolated_db)

        # Compare row counts
        source_counts: Dict[str, int] = {}
        target_counts: Dict[str, int] = {}

        with source_db.connect() as s_conn, isolated_db.connect() as t_conn:
            tables = [
                r["name"] for r in s_conn.fetchall(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for t in tables:
                s_row = s_conn.fetchone(f"SELECT COUNT(*) as cnt FROM {t}")
                t_row = t_conn.fetchone(f"SELECT COUNT(*) as cnt FROM {t}")
                source_counts[t] = s_row["cnt"] if s_row else 0
                target_counts[t] = t_row["cnt"] if t_row else 0

        is_identical = (source_counts == target_counts)
        return is_identical, source_counts, target_counts
