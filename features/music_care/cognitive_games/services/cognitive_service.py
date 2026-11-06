"""
Cognitive Service - Orchestrator for elderly cognitive memory games.
Manages game lifecycle, active time calculation, suggestions, and role-based permissions.
Strictly non-diagnostic: scores are never framed as IQ or clinical dementia indicators.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from features.music_care.cognitive_games.domain.contracts import (
    ICareTeamRepository,
    IGameRepository,
    IPatientIdentityAdapter,
)
from features.music_care.cognitive_games.domain.models import (
    CardItem,
    ContentPack,
    DifficultyLevel,
    GameResult,
    GameSession,
    GameStatus,
    GameType,
    ProgressSummary,
)
from features.music_care.cognitive_games.services.game_engine import GameEngine

logger = logging.getLogger("CognitiveService")


class CognitiveService:
    def __init__(
        self,
        game_repo: IGameRepository,
        care_team_repo: ICareTeamRepository,
        identity_adapter: IPatientIdentityAdapter,
    ) -> None:
        self.game_repo = game_repo
        self.care_team_repo = care_team_repo
        self.identity_adapter = identity_adapter

        # In-memory tracking of active session timestamps for pause/resume calculation
        # session_id -> {
        #    "started_time": float,
        #    "accumulated_active_seconds": float,
        #    "last_resume_time": float,
        #    "is_paused": bool,
        #    "pause_start_time": Optional[float]
        # }
        self._active_timers: Dict[str, Dict[str, Any]] = {}

    def get_active_patient_id(self) -> Optional[str]:
        return self.identity_adapter.get_current_patient_id()

    def get_game_types(self) -> List[GameType]:
        return self.game_repo.get_game_types()

    def get_content_packs(self, game_type_id: Optional[str] = None) -> List[ContentPack]:
        return self.game_repo.get_content_packs(game_type_id)

    @staticmethod
    def normalize_difficulty(difficulty: str | DifficultyLevel) -> str:
        """Safely normalizes difficulty to allowed levels: easy, medium, hard, expert."""
        if isinstance(difficulty, DifficultyLevel):
            return difficulty.value
        val = str(difficulty).lower().strip()
        if val in ("easy", "medium", "hard", "expert"):
            return val
        if "8" in val or "expert" in val or "challenge" in val or "thử thách" in val or "nâng cao" in val:
            return DifficultyLevel.EXPERT.value
        if "6" in val or "hard" in val or "khó" in val:
            return DifficultyLevel.HARD.value
        if "4" in val or "medium" in val or "vừa" in val:
            return DifficultyLevel.MEDIUM.value
        if "3" in val or "2" in val or "easy" in val or "dễ" in val or "khởi động" in val:
            return DifficultyLevel.EASY.value
        return DifficultyLevel.MEDIUM.value

    def get_suggested_difficulty(self, patient_id: str, game_type_code: str) -> DifficultyLevel:
        """
        Suggests difficulty level based on recent sessions of that specific game.
        Rule:
        - If last 3 completed sessions had high accuracy and low help -> recommend MEDIUM/HARD/EXPERT.
        - If last sessions had frequent help or abandoned -> recommend EASY.
        - Defaults to EASY for elderly accessibility.
        """
        types = [gt for gt in self.game_repo.get_game_types() if gt.code == game_type_code]
        if not types:
            return DifficultyLevel.EASY

        recent = self.game_repo.list_sessions(patient_id=patient_id, game_type_id=types[0].game_type_id, limit=5)
        if not recent:
            return DifficultyLevel.EASY

        # If user abandoned recently, keep or drop to EASY
        if any(s.status == GameStatus.ABANDONED for s in recent[:2]):
            return DifficultyLevel.EASY

        completed = [s for s in recent if s.status == GameStatus.COMPLETED]
        if len(completed) >= 3:
            # Check average help in completed
            avg_help = sum(s.help_count for s in completed[:3]) / 3.0
            last_diff = completed[0].difficulty
            if avg_help < 1.0:
                if last_diff == DifficultyLevel.EASY.value:
                    return DifficultyLevel.MEDIUM
                elif last_diff == DifficultyLevel.MEDIUM.value:
                    return DifficultyLevel.HARD
                elif last_diff in (DifficultyLevel.HARD.value, DifficultyLevel.EXPERT.value):
                    return DifficultyLevel.EXPERT
                return DifficultyLevel.HARD
            elif avg_help > 3.0:
                return DifficultyLevel.EASY

        return DifficultyLevel.EASY

    def start_card_matching_session(
        self,
        patient_id: str,
        difficulty: DifficultyLevel | str = DifficultyLevel.EASY,
        pack_id: Optional[str] = None,
        seed: Optional[int] = None,
        client_request_id: Optional[str] = None,
    ) -> Tuple[GameSession, List[CardItem]]:
        """
        Starts a Card Matching session with deterministic seed and exact pairs (Criterion 2).
        """
        types = [gt for gt in self.game_repo.get_game_types() if gt.code == "card_matching"]
        if not types:
            raise ValueError("Game type 'card_matching' not found")
        game_type = types[0]

        packs = self.game_repo.get_content_packs(game_type.game_type_id)
        selected_pack = None
        if pack_id:
            for p in packs:
                if p.pack_id == pack_id:
                    selected_pack = p
                    break
        if not selected_pack and packs:
            selected_pack = packs[0]

        if not selected_pack:
            raise ValueError("No content pack available for card matching")

        chosen_seed = seed if seed is not None else int(time.time() * 1000) % 10000000
        req_id = client_request_id or f"req-{uuid.uuid4().hex[:12]}"
        session_id = f"gs-{uuid.uuid4().hex[:12]}"

        now_iso = datetime.now(timezone.utc).isoformat()
        session = GameSession(
            session_id=session_id,
            patient_id=patient_id,
            game_type_id=game_type.game_type_id,
            pack_id=selected_pack.pack_id,
            difficulty=self.normalize_difficulty(difficulty),
            seed=chosen_seed,
            started_at=now_iso,
            ended_at=None,
            status=GameStatus.IN_PROGRESS.value,
            active_seconds=0.0,
            help_count=0,
            rules_version=game_type.rules_version,
            client_request_id=req_id,
            notes="",
        )

        persisted = self.game_repo.create_session(session)

        # Initialize timer
        self._active_timers[persisted.session_id] = {
            "started_time": time.time(),
            "accumulated_active_seconds": 0.0,
            "last_resume_time": time.time(),
            "is_paused": False,
            "pause_start_time": None,
        }

        # Generate board
        manifest = selected_pack.assets_manifest
        if isinstance(manifest, list):
            items_pool = manifest
        elif isinstance(manifest, dict):
            items_pool = manifest.get("items", [])
        else:
            items_pool = []

        board = GameEngine.generate_card_matching_board(
            items_pool=items_pool,
            difficulty=persisted.difficulty,
            seed=persisted.seed,
            theme=selected_pack.theme,
        )

        return persisted, board

    def start_sequence_recall_session(
        self,
        patient_id: str,
        difficulty: DifficultyLevel | str = DifficultyLevel.EASY,
        pack_id: Optional[str] = None,
        seed: Optional[int] = None,
        client_request_id: Optional[str] = None,
    ) -> Tuple[GameSession, List[Dict[str, str]], List[Dict[str, str]]]:
        """
        Starts a Sequence Recall session with deterministic target and choices (Criterion 3).
        """
        types = [gt for gt in self.game_repo.get_game_types() if gt.code == "sequence_recall"]
        if not types:
            raise ValueError("Game type 'sequence_recall' not found")
        game_type = types[0]

        packs = self.game_repo.get_content_packs(game_type.game_type_id)
        selected_pack = None
        if pack_id:
            for p in packs:
                if p.pack_id == pack_id:
                    selected_pack = p
                    break
        if not selected_pack and packs:
            selected_pack = packs[0]

        if not selected_pack:
            raise ValueError("No content pack available for sequence recall")

        chosen_seed = seed if seed is not None else int(time.time() * 1000) % 10000000
        req_id = client_request_id or f"req-{uuid.uuid4().hex[:12]}"
        session_id = f"gs-{uuid.uuid4().hex[:12]}"

        now_iso = datetime.now(timezone.utc).isoformat()
        session = GameSession(
            session_id=session_id,
            patient_id=patient_id,
            game_type_id=game_type.game_type_id,
            pack_id=selected_pack.pack_id,
            difficulty=self.normalize_difficulty(difficulty),
            seed=chosen_seed,
            started_at=now_iso,
            ended_at=None,
            status=GameStatus.IN_PROGRESS.value,
            active_seconds=0.0,
            help_count=0,
            rules_version=game_type.rules_version,
            client_request_id=req_id,
            notes="",
        )

        persisted = self.game_repo.create_session(session)

        self._active_timers[persisted.session_id] = {
            "started_time": time.time(),
            "accumulated_active_seconds": 0.0,
            "last_resume_time": time.time(),
            "is_paused": False,
            "pause_start_time": None,
        }

        manifest = selected_pack.assets_manifest
        if isinstance(manifest, list):
            items_pool = manifest
        elif isinstance(manifest, dict):
            items_pool = manifest.get("items", [])
        else:
            items_pool = []

        target_seq, choices = GameEngine.generate_sequence_round(
            items_pool=items_pool,
            difficulty=persisted.difficulty,
            seed=persisted.seed,
        )

        return persisted, target_seq, choices

    def pause_session(self, session_id: str) -> None:
        """Pauses active timer so pause duration is NOT counted in active_seconds (Criterion 2)."""
        timer = self._active_timers.get(session_id)
        if timer and not timer["is_paused"]:
            now = time.time()
            elapsed_chunk = now - timer["last_resume_time"]
            timer["accumulated_active_seconds"] += max(0.0, elapsed_chunk)
            timer["is_paused"] = True
            timer["pause_start_time"] = now

        session = self.game_repo.get_session(session_id)
        if session:
            session.status = GameStatus.PAUSED.value
            if timer:
                session.active_seconds = round(timer["accumulated_active_seconds"], 2)
            self.game_repo.update_session(session)

    def resume_session(self, session_id: str) -> None:
        """Resumes active timer after pause."""
        timer = self._active_timers.get(session_id)
        if timer and timer["is_paused"]:
            timer["is_paused"] = False
            timer["last_resume_time"] = time.time()
            timer["pause_start_time"] = None

        session = self.game_repo.get_session(session_id)
        if session:
            session.status = GameStatus.IN_PROGRESS.value
            self.game_repo.update_session(session)

    def record_hint_used(self, session_id: str) -> int:
        """Increments help_count without penalty."""
        session = self.game_repo.get_session(session_id)
        if not session:
            return 0
        session.help_count += 1
        self.game_repo.update_session(session)
        return session.help_count

    def _finalize_active_seconds(self, session_id: str) -> float:
        timer = self._active_timers.pop(session_id, None)
        if not timer:
            sess = self.game_repo.get_session(session_id)
            return sess.active_seconds if sess else 0.0

        if not timer["is_paused"]:
            now = time.time()
            elapsed_chunk = now - timer["last_resume_time"]
            timer["accumulated_active_seconds"] += max(0.0, elapsed_chunk)

        return max(0.1, round(timer["accumulated_active_seconds"], 2))

    def abandon_session(self, session_id: str, notes: str = "Người dùng chủ động nghỉ ngang") -> Optional[GameSession]:
        """
        Marks session abandoned WITHOUT 0-penalty (Criterion 5).
        No row created in game_results.
        """
        session = self.game_repo.get_session(session_id)
        if not session:
            return None

        active_secs = self._finalize_active_seconds(session_id)
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.status = GameStatus.ABANDONED.value
        session.active_seconds = active_secs
        session.notes = notes

        return self.game_repo.update_session(session)

    @staticmethod
    def _normalize_feedback(fb: str) -> str:
        f = fb.lower().strip()
        if f in ("easy", "fair", "hard", "skipped", ""):
            return f
        if "dễ" in f:
            return "easy"
        if "vừa" in f:
            return "fair"
        if "khó" in f or "dài" in f:
            return "hard"
        return "skipped"

    @staticmethod
    def _normalize_enjoyment(enj: str) -> str:
        e = enj.lower().strip()
        if e in ("enjoyed", "neutral", "disliked", "skipped", ""):
            return e
        if "thích" in e or "vui" in e:
            return "enjoyed"
        if "bình thường" in e:
            return "neutral"
        if "không" in e:
            return "disliked"
        return "skipped"

    def finish_card_matching(
        self,
        session_id: str,
        attempts: int,
        correct_pairs: int,
        total_pairs: int,
        user_feedback: str = "",
        enjoyment: str = "",
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[GameSession, GameResult]:
        """Completes card matching session and persists result."""
        session = self.game_repo.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        active_secs = self._finalize_active_seconds(session_id)
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.status = GameStatus.COMPLETED.value
        session.active_seconds = active_secs
        self.game_repo.update_session(session)

        acc = (correct_pairs / max(1, attempts)) * 100.0
        result = GameResult(
            session_id=session_id,
            attempts=attempts,
            correct_count=correct_pairs,
            total_tasks=total_pairs,
            accuracy_percent=round(acc, 1),
            user_feedback=self._normalize_feedback(user_feedback),
            enjoyment=self._normalize_enjoyment(enjoyment),
            metrics=metrics or {},
        )
        saved_result = self.game_repo.save_result(result)
        return session, saved_result

    def finish_sequence_recall(
        self,
        session_id: str,
        attempts: int,
        correct_rounds: int,
        total_rounds: int,
        user_feedback: str = "",
        enjoyment: str = "",
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[GameSession, GameResult]:
        """Completes sequence recall session and persists result."""
        session = self.game_repo.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        active_secs = self._finalize_active_seconds(session_id)
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.status = GameStatus.COMPLETED.value
        session.active_seconds = active_secs
        self.game_repo.update_session(session)

        acc = (correct_rounds / max(1, total_rounds)) * 100.0
        result = GameResult(
            session_id=session_id,
            attempts=attempts,
            correct_count=correct_rounds,
            total_tasks=total_rounds,
            accuracy_percent=round(acc, 1),
            user_feedback=self._normalize_feedback(user_feedback),
            enjoyment=self._normalize_enjoyment(enjoyment),
            metrics=metrics or {},
        )
        saved_result = self.game_repo.save_result(result)
        return session, saved_result

    # -------------------------------------------------------------
    # Permission Enforced Queries (Criterion 6)
    # -------------------------------------------------------------
    def get_patient_progress(
        self,
        actor_id: str,
        actor_role: str,
        patient_id: str,
        game_type_id: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> List[ProgressSummary]:
        """
        Retrieves progress summaries grouped strictly by game, difficulty, and rules_version (Criterion 4).
        Enforces tenant and RBAC permission (Criterion 6).
        """
        self._check_permission(actor_id=actor_id, actor_role=actor_role, target_patient_id=patient_id)
        return self.game_repo.get_progress_summary(
            patient_id=patient_id,
            game_type_id=game_type_id,
            difficulty=difficulty,
        )

    def get_patient_session_history(
        self,
        actor_id: str,
        actor_role: str,
        patient_id: str,
        limit: int = 50,
    ) -> List[GameSession]:
        """
        Retrieves raw session history with permission check (Criterion 6).
        """
        self._check_permission(actor_id=actor_id, actor_role=actor_role, target_patient_id=patient_id)
        return self.game_repo.list_sessions(patient_id=patient_id, limit=limit)

    def _check_permission(self, actor_id: str, actor_role: str, target_patient_id: str) -> None:
        """
        Enforces:
        - Elderly role: actor_id MUST match patient_id (Cannot view other elderly patient, Criterion 6).
        - Caregiver role: actor_id MUST have active caregiver permission for target_patient_id.
        - Clinician role: actor_id MUST have active, non-expired care team consent for target_patient_id.
        """
        role = actor_role.lower()
        if role in ["patient", "elderly"]:
            if actor_id != target_patient_id:
                raise PermissionError(f"Người bệnh {actor_id} không có quyền xem dữ liệu của bệnh nhân {target_patient_id}")
        elif role == "caregiver":
            if not self.care_team_repo.can_caregiver_view_progress(actor_id, target_patient_id):
                raise PermissionError(f"Người nhà {actor_id} chưa được cấp quyền xem tiến trình của {target_patient_id}")
        elif role in ["clinician", "doctor"]:
            if not self.care_team_repo.is_clinician_authorized(actor_id, target_patient_id):
                raise PermissionError(f"Bác sĩ {actor_id} không thuộc nhóm chăm sóc hợp lệ hoặc đã hết hạn với {target_patient_id}")
        else:
            raise PermissionError(f"Vai trò không xác định: {actor_role}")
