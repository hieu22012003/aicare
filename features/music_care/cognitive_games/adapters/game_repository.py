"""
Game repository implementation for Cognitive Games & Relaxation Module.
Handles session persistence, idempotency, pack resolution, and isolated metric summaries.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Dict, List, Optional

from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.domain.contracts import IGameRepository
from features.music_care.cognitive_games.domain.models import (
    ContentPack,
    GameResult,
    GameSession,
    GameType,
    ProgressSummary,
)

logger = logging.getLogger("CognitiveGameRepo")


class SqlGameRepository(IGameRepository):
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_game_types(self) -> List[GameType]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM game_types WHERE is_active = 1")
            return [
                GameType(
                    game_type_id=row["game_type_id"],
                    code=row["code"],
                    name=row["name"],
                    description=row["description"] or "",
                    rules_version=row["rules_version"],
                    is_active=bool(row["is_active"]),
                )
                for row in cursor.fetchall()
            ]

    def get_content_packs(self, game_type_id: Optional[str] = None) -> List[ContentPack]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            if game_type_id:
                cursor.execute("SELECT * FROM content_packs WHERE game_type_id = ?", (game_type_id,))
            else:
                cursor.execute("SELECT * FROM content_packs")

            packs = []
            for row in cursor.fetchall():
                try:
                    manifest = json.loads(row["assets_manifest"])
                except Exception:
                    manifest = {}
                packs.append(
                    ContentPack(
                        pack_id=row["pack_id"],
                        game_type_id=row["game_type_id"],
                        title=row["title"],
                        theme=row["theme"],
                        assets_manifest=manifest,
                        license=row["license"],
                        locale=row["locale"],
                        version=row["version"],
                    )
                )
            return packs

    def create_session(self, session: GameSession) -> GameSession:
        """
        Creates a game session with client_request_id idempotency.
        If client_request_id exists, returns existing session (Criterion 5).
        """
        with self.db.connect() as conn:
            cursor = conn.cursor()
            existing = self.get_session_by_request_id(session.client_request_id)
            if existing:
                logger.info("Session with client_request_id %s already exists. Returning existing.", session.client_request_id)
                return existing

            cursor.execute(
                """
                INSERT INTO game_sessions (
                    session_id, patient_id, game_type_id, pack_id, difficulty,
                    seed, started_at, ended_at, status, active_seconds, help_count,
                    rules_version, client_request_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.patient_id,
                    session.game_type_id,
                    session.pack_id,
                    session.difficulty,
                    session.seed,
                    session.started_at,
                    session.ended_at,
                    session.status,
                    session.active_seconds,
                    session.help_count,
                    session.rules_version,
                    session.client_request_id,
                    session.notes,
                ),
            )
            return session

    def update_session(self, session: GameSession) -> GameSession:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE game_sessions
                SET ended_at = ?,
                    status = ?,
                    active_seconds = ?,
                    help_count = ?,
                    notes = ?
                WHERE session_id = ?
                """,
                (
                    session.ended_at,
                    session.status,
                    session.active_seconds,
                    session.help_count,
                    session.notes,
                    session.session_id,
                ),
            )
            return session

    def save_result(self, result: GameResult) -> GameResult:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO game_results (
                    session_id, attempts, correct_count, total_tasks,
                    accuracy_percent, user_feedback, enjoyment, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    attempts = excluded.attempts,
                    correct_count = excluded.correct_count,
                    total_tasks = excluded.total_tasks,
                    accuracy_percent = excluded.accuracy_percent,
                    user_feedback = excluded.user_feedback,
                    enjoyment = excluded.enjoyment,
                    metrics_json = excluded.metrics_json
                """,
                (
                    result.session_id,
                    result.attempts,
                    result.correct_count,
                    result.total_tasks,
                    result.accuracy_percent,
                    result.user_feedback,
                    result.enjoyment,
                    json.dumps(result.metrics, ensure_ascii=False),
                ),
            )
            return result

    def get_session(self, session_id: str) -> Optional[GameSession]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM game_sessions WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return GameSession(
                session_id=row["session_id"],
                patient_id=row["patient_id"],
                game_type_id=row["game_type_id"],
                pack_id=row["pack_id"],
                difficulty=row["difficulty"],
                seed=row["seed"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                status=row["status"],
                active_seconds=row["active_seconds"],
                help_count=row["help_count"],
                rules_version=row["rules_version"],
                client_request_id=row["client_request_id"],
                notes=row["notes"] or "",
            )

    def get_session_by_request_id(self, client_request_id: str) -> Optional[GameSession]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM game_sessions WHERE client_request_id = ?", (client_request_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return GameSession(
                session_id=row["session_id"],
                patient_id=row["patient_id"],
                game_type_id=row["game_type_id"],
                pack_id=row["pack_id"],
                difficulty=row["difficulty"],
                seed=row["seed"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                status=row["status"],
                active_seconds=row["active_seconds"],
                help_count=row["help_count"],
                rules_version=row["rules_version"],
                client_request_id=row["client_request_id"],
                notes=row["notes"] or "",
            )

    def list_sessions(
        self,
        patient_id: str,
        game_type_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GameSession]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM game_sessions WHERE patient_id = ?"
            params: List[object] = [patient_id]

            if game_type_id:
                query += " AND game_type_id = ?"
                params.append(game_type_id)
            if difficulty:
                query += " AND difficulty = ?"
                params.append(difficulty)

            query += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, tuple(params))
            return [
                GameSession(
                    session_id=row["session_id"],
                    patient_id=row["patient_id"],
                    game_type_id=row["game_type_id"],
                    pack_id=row["pack_id"],
                    difficulty=row["difficulty"],
                    seed=row["seed"],
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    status=row["status"],
                    active_seconds=row["active_seconds"],
                    help_count=row["help_count"],
                    rules_version=row["rules_version"],
                    client_request_id=row["client_request_id"],
                    notes=row["notes"] or "",
                )
                for row in cursor.fetchall()
            ]

    def get_progress_summary(
        self,
        patient_id: str,
        game_type_id: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> List[ProgressSummary]:
        """
        Aggregates sessions grouped strictly by game_type, difficulty, and rules_version.
        Guarantees Criterion 4: Never compares sessions of different difficulties or rules as identical.
        """
        with self.db.connect() as conn:
            cursor = conn.cursor()
            query = """
                SELECT
                    s.patient_id,
                    gt.code as game_code,
                    gt.name as game_name,
                    s.difficulty,
                    s.rules_version,
                    COUNT(*) as total_sessions,
                    SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) as completed_sessions,
                    SUM(CASE WHEN s.status = 'abandoned' THEN 1 ELSE 0 END) as abandoned_sessions,
                    AVG(s.active_seconds) as avg_active_seconds,
                    AVG(s.help_count) as avg_help_count
                FROM game_sessions s
                JOIN game_types gt ON s.game_type_id = gt.game_type_id
                WHERE s.patient_id = ?
            """
            params: List[object] = [patient_id]

            if game_type_id:
                query += " AND s.game_type_id = ?"
                params.append(game_type_id)
            if difficulty:
                query += " AND s.difficulty = ?"
                params.append(difficulty)

            query += " GROUP BY s.patient_id, gt.code, gt.name, s.difficulty, s.rules_version"

            cursor.execute(query, tuple(params))
            results = []
            for row in cursor.fetchall():
                total = row["total_sessions"]
                completed = row["completed_sessions"] or 0
                comp_rate = (completed / total * 100.0) if total > 0 else 0.0
                results.append(
                    ProgressSummary(
                        patient_id=row["patient_id"],
                        game_code=row["game_code"],
                        game_name=row["game_name"],
                        difficulty=row["difficulty"],
                        rules_version=row["rules_version"],
                        total_sessions=total,
                        completed_sessions=completed,
                        abandoned_sessions=row["abandoned_sessions"] or 0,
                        completion_rate=round(comp_rate, 1),
                        avg_active_seconds=round(row["avg_active_seconds"] or 0.0, 1),
                        avg_help_count=round(row["avg_help_count"] or 0.0, 1),
                        sample_count_n=total,
                    )
                )
            return results
