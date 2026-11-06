"""
Audio Service for Relaxing Music and Optional Experimental Binaural Beats.
Enforces safety guardrails:
- Off by default; no unexpected auto-play (Criterion 8).
- Sleep timer support (5, 10, 15 min).
- Binaural alpha 10Hz strictly off by default, requires explicit consent & stereo headphone notice (Criterion 9).
- Strictly no dementia cure / cognitive boost claims.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from features.music_care.cognitive_games.domain.contracts import IMusicRepository
from features.music_care.cognitive_games.domain.models import (
    AudioMode,
    FeedbackRating,
    ListeningSession,
    MusicTrack,
)

logger = logging.getLogger("AudioService")


class AudioService:
    def __init__(self, music_repo: IMusicRepository) -> None:
        self.music_repo = music_repo

        # Playback runtime state
        self._current_track: Optional[MusicTrack] = None
        self._active_session: Optional[ListeningSession] = None
        self._is_playing: bool = False
        self._is_paused: bool = False
        self._volume: int = 35  # Gentle starting volume (0-100)
        self._start_wall_time: Optional[float] = None
        self._last_resume_time: Optional[float] = None
        self._accumulated_seconds: float = 0.0

        # Sleep timer (seconds remaining, or None)
        self._sleep_timer_seconds: Optional[float] = None

    @property
    def is_playing(self) -> bool:
        return self._is_playing and not self._is_paused

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def current_track(self) -> Optional[MusicTrack]:
        return self._current_track

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def sleep_timer_seconds(self) -> Optional[float]:
        return self._sleep_timer_seconds

    def set_volume(self, volume: int) -> int:
        """Sets gentle playback volume (clamped between 0 and 100)."""
        self._volume = max(0, min(100, volume))
        return self._volume

    def list_tracks(self, audio_mode: Optional[str] = None) -> List[MusicTrack]:
        return self.music_repo.list_tracks(audio_mode=audio_mode)

    def get_track(self, track_id: str) -> Optional[MusicTrack]:
        return self.music_repo.get_track(track_id)

    def start_playback(
        self,
        patient_id: str,
        track_id: str,
        timer_minutes: Optional[int] = None,
        is_binaural_consent: bool = False,
        headphone_confirmed: bool = False,
        client_request_id: Optional[str] = None,
    ) -> ListeningSession:
        """
        Starts playback of a selected track.
        Enforces binaural safety guardrails (Criterion 9):
        If track is binaural_trial, requires is_binaural_consent and headphone_confirmed.
        """
        track = self.get_track(track_id)
        if not track:
            raise ValueError(f"Không tìm thấy bản nhạc {track_id}")

        # Stop existing playback if any
        if self._is_playing:
            self.stop_playback(stopped_reason="switch_track")

        is_binaural = (track.audio_mode == AudioMode.BINAURAL_TRIAL.value)
        if is_binaural:
            if not is_binaural_consent:
                raise ValueError("Binaural beats yêu cầu sự đồng ý tham gia chế độ âm thanh thử nghiệm.")
            if not headphone_confirmed:
                raise ValueError("Binaural beats yêu cầu xác nhận sử dụng tai nghe stereo hai bên.")

        now_iso = datetime.now(timezone.utc).isoformat()
        listen_id = f"ls-{uuid.uuid4().hex[:12]}"
        req_id = client_request_id or f"req-{uuid.uuid4().hex[:12]}"

        session = ListeningSession(
            listen_id=listen_id,
            patient_id=patient_id,
            track_id=track.track_id,
            started_at=now_iso,
            ended_at=None,
            played_seconds=0.0,
            is_binaural=is_binaural,
            headphone_confirmed=headphone_confirmed if is_binaural else False,
            feedback=FeedbackRating.SKIPPED.value,
            stopped_reason="playing",
            client_request_id=req_id,
        )

        self._current_track = track
        self._active_session = session
        self._is_playing = True
        self._is_paused = False
        self._start_wall_time = time.time()
        self._last_resume_time = time.time()
        self._accumulated_seconds = 0.0

        if timer_minutes and timer_minutes > 0:
            self._sleep_timer_seconds = float(timer_minutes * 60)
        else:
            self._sleep_timer_seconds = None

        logger.info(
            "Bắt đầu phát track: %s (chế độ: %s, hẹn giờ: %s phút)",
            track.title,
            track.audio_mode,
            timer_minutes,
        )
        return session

    def pause_playback(self) -> None:
        if self._is_playing and not self._is_paused:
            now = time.time()
            if self._last_resume_time:
                self._accumulated_seconds += max(0.0, now - self._last_resume_time)
            self._is_paused = True

    def resume_playback(self) -> None:
        if self._is_playing and self._is_paused:
            self._is_paused = False
            self._last_resume_time = time.time()

    def stop_playback(
        self,
        stopped_reason: str = "user_stopped",
        feedback: FeedbackRating | str = FeedbackRating.SKIPPED,
    ) -> Optional[ListeningSession]:
        """Stops playback, computes total played seconds, and logs session."""
        if not self._active_session:
            return None

        now = time.time()
        if not self._is_paused and self._last_resume_time:
            self._accumulated_seconds += max(0.0, now - self._last_resume_time)

        played_secs = max(0.1, round(self._accumulated_seconds, 1))
        session = self._active_session
        session.ended_at = datetime.now(timezone.utc).isoformat()
        session.played_seconds = played_secs
        session.stopped_reason = stopped_reason
        session.feedback = feedback.value if isinstance(feedback, FeedbackRating) else str(feedback)

        # Persist to database
        saved = self.music_repo.record_listening_session(session)

        # Reset state
        self._active_session = None
        self._current_track = None
        self._is_playing = False
        self._is_paused = False
        self._sleep_timer_seconds = None
        self._start_wall_time = None
        self._last_resume_time = None
        self._accumulated_seconds = 0.0

        logger.info("Đã dừng phát nhạc (lý do: %s, đã nghe: %.1fs)", stopped_reason, played_secs)
        return saved

    def tick(self, delta_seconds: float) -> bool:
        """
        Advances runtime clock by delta_seconds.
        Handles sleep timer countdown and triggers auto-stop when 0 reached (Criterion 8).
        Returns True if timer expired and stopped playback, False otherwise.
        """
        if not self.is_playing:
            return False

        if self._sleep_timer_seconds is not None:
            self._sleep_timer_seconds -= delta_seconds
            if self._sleep_timer_seconds <= 0:
                self._sleep_timer_seconds = 0
                self.stop_playback(stopped_reason="sleep_timer_expired")
                return True
        return False

    def list_history(self, patient_id: str, limit: int = 20) -> List[ListeningSession]:
        return self.music_repo.list_listening_sessions(patient_id=patient_id, limit=limit)
