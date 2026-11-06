"""
Music and audio session repository for Cognitive Games & Relaxation module.
Handles track catalog and listening history logs.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.domain.contracts import IMusicRepository
from features.music_care.cognitive_games.domain.models import ListeningSession, MusicTrack

logger = logging.getLogger("CognitiveMusicRepo")


class SqlMusicRepository(IMusicRepository):
    @staticmethod
    def _is_legacy_linux_path(asset_uri: str) -> bool:
        # Old release manifests contain /mnt/... Linux paths; preserve Windows UNC paths.
        return asset_uri.startswith('/') and not asset_uri.startswith('//')

    def __init__(self, db: DatabaseManager, family_db=None, patient_id: str = '') -> None:
        self.db = db
        self.patient_id = patient_id
        if family_db is not None:
            from features.music_care.family_music_service import FamilyMusicService
            self.family_music = FamilyMusicService(family_db)
        else:
            self.family_music = None

    def set_patient_id(self, patient_id: str) -> None:
        self.patient_id = patient_id

    def _family_track(self, row):
        return MusicTrack(track_id=row['track_id'], title=row['title'],
                          artist_attribution='Người nhà gửi',
                          asset_uri=str(self.family_music.audio_path(row)),
                          license='Tệp do người nhà cung cấp', audio_mode='ordinary',
                          duration_seconds=row['duration_seconds'], description='Bài nhạc dành riêng cho bác từ người nhà.')

    def list_tracks(self, audio_mode: Optional[str] = None) -> List[MusicTrack]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            if audio_mode:
                cursor.execute("SELECT * FROM music_tracks WHERE audio_mode = ? AND track_id NOT GLOB 'family_*'", (audio_mode,))
            else:
                cursor.execute("SELECT * FROM music_tracks WHERE track_id NOT GLOB 'family_*'")

            tracks = [
                MusicTrack(
                    track_id=row["track_id"],
                    title=row["title"],
                    artist_attribution=row["artist_attribution"],
                    asset_uri=row["asset_uri"],
                    license=row["license"],
                    audio_mode=row["audio_mode"],
                    duration_seconds=row["duration_seconds"],
                    description=row["description"] or "",
                )
                for row in cursor.fetchall()
            ]
        tracks = [track for track in tracks if not self._is_legacy_linux_path(track.asset_uri)]
        if self.family_music and audio_mode in (None, 'ordinary'):
            tracks.extend(self._family_track(row) for row in self.family_music.list_tracks(self.patient_id))
        return tracks

    def get_track(self, track_id: str) -> Optional[MusicTrack]:
        if track_id.startswith('family_'):
            row = self.family_music.get_track(self.patient_id, track_id) if self.family_music else None
            if not row:
                return None
            track = self._family_track(row)
            # Listening history in the games DB has a foreign key to its catalog.
            # Cache metadata for that key; list/get still filter by current patient.
            with self.db.connect() as conn:
                conn.execute('INSERT OR IGNORE INTO music_tracks '
                             '(track_id, title, artist_attribution, asset_uri, license, audio_mode, duration_seconds, description) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (track.track_id, track.title, track.artist_attribution, track.asset_uri,
                              track.license, track.audio_mode, track.duration_seconds, track.description))
            return track
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM music_tracks WHERE track_id = ?", (track_id,))
            row = cursor.fetchone()
            if not row or self._is_legacy_linux_path(row['asset_uri']):
                return None
            return MusicTrack(
                track_id=row["track_id"],
                title=row["title"],
                artist_attribution=row["artist_attribution"],
                asset_uri=row["asset_uri"],
                license=row["license"],
                audio_mode=row["audio_mode"],
                duration_seconds=row["duration_seconds"],
                description=row["description"] or "",
            )

    def record_listening_session(self, session: ListeningSession) -> ListeningSession:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            # Idempotent insert on client_request_id
            cursor.execute("SELECT listen_id FROM listening_sessions WHERE client_request_id = ?", (session.client_request_id,))
            existing = cursor.fetchone()
            if existing:
                return session

            cursor.execute(
                """
                INSERT INTO listening_sessions (
                    listen_id, patient_id, track_id, started_at, ended_at,
                    played_seconds, is_binaural, headphone_confirmed, feedback, stopped_reason, client_request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.listen_id,
                    session.patient_id,
                    session.track_id,
                    session.started_at,
                    session.ended_at,
                    session.played_seconds,
                    1 if session.is_binaural else 0,
                    1 if session.headphone_confirmed else 0,
                    session.feedback,
                    session.stopped_reason,
                    session.client_request_id,
                ),
            )
            return session

    def list_listening_sessions(self, patient_id: str, limit: int = 20) -> List[ListeningSession]:
        with self.db.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM listening_sessions
                WHERE patient_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (patient_id, limit),
            )
            return [
                ListeningSession(
                    listen_id=row["listen_id"],
                    patient_id=row["patient_id"],
                    track_id=row["track_id"],
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    played_seconds=row["played_seconds"],
                    is_binaural=bool(row["is_binaural"]),
                    headphone_confirmed=bool(row["headphone_confirmed"]),
                    feedback=row["feedback"] or "",
                    stopped_reason=row["stopped_reason"],
                    client_request_id=row["client_request_id"],
                )
                for row in cursor.fetchall()
            ]
