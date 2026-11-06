"""
Database connection manager for Cognitive Games & Relaxation Module.
Enforces SQLite foreign keys, WAL mode, row factory, and clean transaction handling.
Supports persistent in-memory database for testing via _mem_conn.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger("CognitiveGamesDatabase")

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


class DatabaseManager:
    """Manages SQLite connections with enforced foreign keys, WAL mode, and persistent in-memory mode."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._mem_conn: Optional[sqlite3.Connection] = None
        if db_path is None or str(db_path) == ":memory:":
            self.db_path = ":memory:"
            # Hold persistent in-memory connection
            self._mem_conn = sqlite3.connect(":memory:", timeout=20.0, check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            self.db_path = str(Path(db_path).resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=20.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    def initialize_schema(self) -> None:
        self._init_schema()

    def _init_schema(self) -> None:
        """Executes schema.sql to ensure all tables exist and performs migrations if needed."""
        if not SCHEMA_FILE.exists():
            raise FileNotFoundError(f"Schema file not found at {SCHEMA_FILE}")

        sql = SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self.get_connection()
        try:
            conn.executescript(sql)
            self._migrate_schema_if_needed(conn)
            conn.commit()
            logger.info("Cognitive Games database schema initialized at %s", self.db_path)
        finally:
            if self._mem_conn is None:
                conn.close()

    def _migrate_schema_if_needed(self, conn: sqlite3.Connection) -> None:
        """Migrates existing tables if constraints have been updated."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='game_sessions'")
            row = cursor.fetchone()
            if row and row[0]:
                sql_def = row[0]
                # If table contains old constraint without 'expert'
                if "'expert'" not in sql_def and ("'easy', 'medium', 'hard'" in sql_def or "('easy', 'medium', 'hard')" in sql_def):
                    logger.info("Migrating game_sessions schema to support 'expert' difficulty...")
                    cursor.execute("PRAGMA foreign_keys = OFF;")
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS game_sessions_migrated (
                            session_id VARCHAR(64) PRIMARY KEY,
                            patient_id VARCHAR(32) NOT NULL REFERENCES patients(patient_id) ON DELETE CASCADE,
                            game_type_id VARCHAR(32) NOT NULL REFERENCES game_types(game_type_id),
                            pack_id VARCHAR(64) NOT NULL REFERENCES content_packs(pack_id),
                            difficulty VARCHAR(32) NOT NULL CHECK(difficulty IN ('easy', 'medium', 'hard', 'expert')),
                            seed INTEGER NOT NULL,
                            started_at TEXT NOT NULL,
                            ended_at TEXT,
                            status VARCHAR(32) NOT NULL CHECK(status IN ('in_progress', 'completed', 'abandoned', 'paused')),
                            active_seconds REAL NOT NULL DEFAULT 0.0,
                            help_count INTEGER NOT NULL DEFAULT 0,
                            rules_version VARCHAR(16) NOT NULL DEFAULT 'v1.0',
                            client_request_id VARCHAR(64) UNIQUE NOT NULL,
                            notes TEXT
                        );
                    """)
                    cursor.execute("INSERT OR IGNORE INTO game_sessions_migrated SELECT * FROM game_sessions;")
                    cursor.execute("DROP TABLE game_sessions;")
                    cursor.execute("ALTER TABLE game_sessions_migrated RENAME TO game_sessions;")
                    cursor.execute("PRAGMA foreign_keys = ON;")
                    conn.commit()
                    logger.info("game_sessions migration complete.")
        except Exception as ex:
            logger.warning("Schema migration warning: %s", ex)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Provides a transactional context with automatic commit/rollback."""
        if self._mem_conn is not None:
            with self._mem_conn:
                yield self._mem_conn
            return

        conn = self.get_connection()
        try:
            with conn:
                yield conn
        finally:
            conn.close()
