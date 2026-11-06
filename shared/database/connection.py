"""
Unified Database Manager supporting ANSI SQL / PostgreSQL / SQLite.
Provides ACID transactions, strict foreign keys, WAL journal mode, and row factory access.
"""

from __future__ import annotations

import logging
import os
try:
    import sqlite3
except ImportError:
    try:
        import pysqlite3 as sqlite3
    except ImportError:
        import subprocess
        import sys
        logging.getLogger("UnifiedDatabase").warning(
            "Standard sqlite3 not found in Python runtime. Attempting auto-installation of pysqlite3-binary..."
        )
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pysqlite3-binary"])
            import pysqlite3 as sqlite3
            logging.getLogger("UnifiedDatabase").info("Successfully installed and imported pysqlite3-binary.")
        except Exception as _exc:
            logging.getLogger("UnifiedDatabase").error("Failed to auto-install pysqlite3-binary: %s", _exc)
            raise ImportError(
                "Môi trường Python hiện tại không có sẵn module sqlite3 và không thể tự động cài đặt pysqlite3-binary. "
                "Vui lòng chạy: pip install pysqlite3-binary"
            ) from _exc
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

logger = logging.getLogger("UnifiedDatabase")


class DatabaseConnection:
    """Wrapper around active database connection providing consistent row access."""

    def __init__(self, raw_conn: sqlite3.Connection) -> None:
        self.raw_conn = raw_conn

    def execute(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> sqlite3.Cursor:
        return self.raw_conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: List[Union[Tuple, List, Dict]]) -> sqlite3.Cursor:
        return self.raw_conn.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> sqlite3.Cursor:
        return self.raw_conn.executescript(script)

    def fetchone(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> Optional[Dict[str, Any]]:
        cursor = self.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def fetchall(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> List[Dict[str, Any]]:
        cursor = self.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]

    def commit(self) -> None:
        self.raw_conn.commit()

    def rollback(self) -> None:
        self.raw_conn.rollback()


class UnifiedDatabaseManager:
    """
    Central Database Manager for the Unified Healthcare System.
    Connects to local relational store with strict 3NF foreign key enforcement.
    """

    def __init__(self, db_path: Optional[Union[Path, str]] = None) -> None:
        self._mem_conn: Optional[sqlite3.Connection] = None
        if db_path is None:
            env_db = os.environ.get("AI_CARE_DB_PATH")
            if env_db:
                self.db_path = str(Path(env_db).resolve())
            else:
                # Default to persistent file in project data/
                from shared.config import DATA_DIR
                self.db_path = str((DATA_DIR / "unified_care.sqlite3").resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        elif str(db_path) == ":memory:":
            self.db_path = ":memory:"
            self._mem_conn = sqlite3.connect(":memory:", timeout=30.0, check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            self.db_path = str(Path(db_path).resolve())
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def get_raw_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @contextmanager
    def connect(self) -> Generator[DatabaseConnection, None, None]:
        """Provides an active connection inside an ACID transaction."""
        if self._mem_conn is not None:
            with self._mem_conn:
                yield DatabaseConnection(self._mem_conn)
            return

        conn = self.get_raw_connection()
        try:
            with conn:
                yield DatabaseConnection(conn)
        finally:
            conn.close()

    def execute(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> Any:
        with self.connect() as db:
            return db.execute(sql, params)

    def fetchone(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> Optional[Dict[str, Any]]:
        with self.connect() as db:
            return db.fetchone(sql, params)

    def fetchall(self, sql: str, params: Union[Tuple, List, Dict] = ()) -> List[Dict[str, Any]]:
        with self.connect() as db:
            return db.fetchall(sql, params)

    def table_exists(self, table_name: str) -> bool:
        row = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None

