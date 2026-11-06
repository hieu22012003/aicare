"""
Abstract port contracts and repository interfaces for Cognitive Games module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from features.music_care.cognitive_games.domain.models import (
    CardItem,
    ContentPack,
    GameResult,
    GameSession,
    GameType,
    ListeningSession,
    MusicTrack,
    Patient,
    ProgressSummary,
)


class IPatientIdentityAdapter(ABC):
    """Port for identity resolution (e.g. Face recognition or authenticated fallback)."""

    @abstractmethod
    def get_current_patient_id(self) -> Optional[str]:
        """Returns patient_id if identified; None if unauthenticated."""
        pass

    @abstractmethod
    def is_camera_active(self) -> bool:
        """Returns True if camera recognition is operational."""
        pass

    @abstractmethod
    def set_camera_active(self, active: bool) -> None:
        """Toggles camera simulation for fallback testing (Criterion 7)."""
        pass


class ICareTeamRepository(ABC):
    """Port for checking permissions across patients, caregivers, and clinicians."""

    @abstractmethod
    def is_clinician_authorized(self, clinician_id: str, patient_id: str) -> bool:
        """Returns True only if active consent and non-expired care_team relation exists."""
        pass

    @abstractmethod
    def can_caregiver_view_progress(self, caregiver_id: str, patient_id: str) -> bool:
        """Returns True only if patient_caregivers relation has can_view_health = 1."""
        pass

    @abstractmethod
    def get_patient(self, patient_id: str) -> Optional[Patient]:
        """Retrieves patient by patient_id."""
        pass


class IGameRepository(ABC):
    """Port for storing and retrieving game types, content packs, sessions, and results."""

    @abstractmethod
    def get_game_types(self) -> List[GameType]:
        pass

    @abstractmethod
    def get_content_packs(self, game_type_id: Optional[str] = None) -> List[ContentPack]:
        pass

    @abstractmethod
    def create_session(self, session: GameSession) -> GameSession:
        pass

    @abstractmethod
    def update_session(self, session: GameSession) -> GameSession:
        pass

    @abstractmethod
    def save_result(self, result: GameResult) -> GameResult:
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[GameSession]:
        pass

    @abstractmethod
    def get_session_by_request_id(self, client_request_id: str) -> Optional[GameSession]:
        pass

    @abstractmethod
    def list_sessions(
        self,
        patient_id: str,
        game_type_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[GameSession]:
        pass

    @abstractmethod
    def get_progress_summary(
        self,
        patient_id: str,
        game_type_id: Optional[str] = None,
        difficulty: Optional[str] = None,
    ) -> List[ProgressSummary]:
        pass


class IMusicRepository(ABC):
    """Port for music tracks and listening session logs."""

    @abstractmethod
    def list_tracks(self, audio_mode: Optional[str] = None) -> List[MusicTrack]:
        pass

    @abstractmethod
    def get_track(self, track_id: str) -> Optional[MusicTrack]:
        pass

    @abstractmethod
    def record_listening_session(self, session: ListeningSession) -> ListeningSession:
        pass

    @abstractmethod
    def list_listening_sessions(self, patient_id: str, limit: int = 20) -> List[ListeningSession]:
        pass
