"""
Database Connection and Initialization Manager for Health Monitoring Module.
Enforces foreign keys, ACID immediate transactions, WAL journal mode, and schema setup.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger("HealthDatabase")

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


class DatabaseManager:
    """Thread-safe SQLite Database Manager with Foreign Key Enforcement."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self._mem_conn: Optional[sqlite3.Connection] = None
        if db_path is None or str(db_path) == ":memory:":
            self.db_path = ":memory:"
            # Hold a persistent in-memory connection so schemas and data persist across queries
            self._mem_conn = sqlite3.connect(":memory:", timeout=20.0, check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            self.db_path = str(Path(db_path).resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Create or return an SQLite connection with strict settings."""
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=20.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an active connection with transaction auto-commit/rollback."""
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

    def _init_schema(self) -> None:
        """Apply schema.sql definitions."""
        if not SCHEMA_FILE.exists():
            raise FileNotFoundError(f"Schema file not found at {SCHEMA_FILE}")

        with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
            sql_script = f.read()

        conn = self.get_connection()
        try:
            conn.executescript(sql_script)
            logger.info("Health Monitoring schema initialized successfully.")
        finally:
            if self._mem_conn is None:
                conn.close()
