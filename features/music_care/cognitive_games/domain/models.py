"""
Domain models and Enums for Cognitive Games & Relaxation Module.
Strictly non-diagnostic: no IQ score or clinical dementia labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DifficultyLevel(str, Enum):
    EASY = "easy"        # e.g. 3 pairs (6 cards) / 2 items sequence
    MEDIUM = "medium"    # e.g. 4 pairs (8 cards) / 3 items sequence
    HARD = "hard"        # e.g. 6 pairs (12 cards) / 4 items sequence
    EXPERT = "expert"    # e.g. 8 pairs (16 cards) / 5 items sequence


class GameStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    PAUSED = "paused"


class AudioMode(str, Enum):
    ORDINARY = "ordinary"
    BINAURAL_TRIAL = "binaural_trial"


class FeedbackRating(str, Enum):
    PLEASANT = "pleasant"
    NORMAL = "normal"
    DISLIKED = "disliked"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    phone: str
    full_name: str
    is_active: bool
    created_at: str


@dataclass(frozen=True)
class Patient:
    patient_id: str
    user_id: str
    full_name: str
    date_of_birth: str
    gender: str
    timezone: str = "Asia/Ho_Chi_Minh"
    status: str = "ACTIVE"
    notes: str = ""


@dataclass(frozen=True)
class Caregiver:
    caregiver_id: str
    user_id: str
    email_verified: bool
    phone_verified: bool


@dataclass(frozen=True)
class Clinician:
    clinician_id: str
    user_id: str
    workplace: str
    specialty: str
    license_number: str
    is_verified: bool


@dataclass(frozen=True)
class PatientCaregiver:
    patient_caregiver_id: str
    patient_id: str
    caregiver_id: str
    relationship_type: str
    can_view_health: bool
    valid_from: str
    valid_to: str
    is_active: bool


@dataclass(frozen=True)
class CareTeamMember:
    care_team_id: str
    patient_id: str
    clinician_id: str
    role_description: str
    valid_from: str
    valid_to: str
    consent_status: str
    is_active: bool


@dataclass(frozen=True)
class GameType:
    game_type_id: str
    code: str
    name: str
    description: str
    rules_version: str = "v1.0"
    is_active: bool = True


@dataclass(frozen=True)
class ContentPack:
    pack_id: str
    game_type_id: str
    title: str
    theme: str
    assets_manifest: Dict[str, Any]
    license: str = "CC0-1.0"
    locale: str = "vi-VN"
    version: str = "1.0"


@dataclass
class CardItem:
    card_index: int
    item_id: str
    name: str
    icon_symbol: str
    theme: str
    is_flipped: bool = False
    is_matched: bool = False


@dataclass
class GameSession:
    session_id: str
    patient_id: str
    game_type_id: str
    pack_id: str
    difficulty: str
    seed: int
    started_at: str
    ended_at: Optional[str]
    status: str
    active_seconds: float
    help_count: int
    rules_version: str
    client_request_id: str
    notes: str = ""


@dataclass
class GameResult:
    session_id: str
    attempts: int
    correct_count: int
    total_tasks: int
    accuracy_percent: float
    user_feedback: str = ""
    enjoyment: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MusicTrack:
    track_id: str
    title: str
    artist_attribution: str
    asset_uri: str
    license: str
    audio_mode: str
    duration_seconds: int
    description: str = ""


@dataclass
class ListeningSession:
    listen_id: str
    patient_id: str
    track_id: str
    started_at: str
    ended_at: Optional[str]
    played_seconds: float
    is_binaural: bool
    headphone_confirmed: bool
    feedback: str
    stopped_reason: str
    client_request_id: str


@dataclass
class ProgressSummary:
    patient_id: str
    game_code: str
    game_name: str
    difficulty: str
    rules_version: str
    total_sessions: int
    completed_sessions: int
    abandoned_sessions: int
    completion_rate: float
    avg_active_seconds: float
    avg_help_count: float
    sample_count_n: int
