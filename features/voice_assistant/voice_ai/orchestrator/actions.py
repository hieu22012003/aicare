"""
Allowlisted Client Action Tokens for Voice AI.
Strictly prevents arbitrary command execution or LLM-generated code.
Implements Section 4 and Section 7 of VOICE_AI_IMPLEMENTATION_PLAN.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class ActionType(str, Enum):
    """Allowlisted Client Actions."""
    ASSISTANCE_ALERT = "assistance.alert"
    MEDICATION_PROMPT = "medication.prompt"
    REHAB_START = "rehab.start"
    GAME_OPEN = "game.open"
    MEDIA_PLAY = "media.play"
    MEDIA_PAUSE = "media.pause"
    MEDIA_STOP = "media.stop"
    DIALOG_SPEAK = "dialog.speak"


ALLOWED_GAMES: Set[str] = {"card_matching", "sequence_recall"}
ALLOWED_REHAB_EXERCISES: Set[str] = {"sit_to_stand", "arm_raise", "march", "general"}


@dataclass
class ClientAction:
    """A strongly-typed, allowlisted action emitted to the client UI."""
    action_type: ActionType
    payload: Dict[str, Any] = field(default_factory=dict)
    target_component: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "payload": self.payload,
            "target_component": self.target_component,
        }


def make_assistance_alert_action(
    request_id: str,
    urgency: str = "high",
    symptoms: Optional[str] = None,
) -> ClientAction:
    return ClientAction(
        action_type=ActionType.ASSISTANCE_ALERT,
        payload={
            "request_id": request_id,
            "urgency": urgency,
            "symptoms": symptoms or "",
            "message": "Đang kết nối đến bác sĩ trực...",
        },
        target_component="assistance_dialog",
    )


def make_game_open_action(game_type: str = "card_matching") -> ClientAction:
    # Validate against strict allowlist
    clean_game = game_type.lower().strip() if game_type else "card_matching"
    if clean_game not in ALLOWED_GAMES:
        clean_game = "card_matching"
    return ClientAction(
        action_type=ActionType.GAME_OPEN,
        payload={"game_type": clean_game},
        target_component="game_container",
    )


def make_rehab_start_action(exercise_type: str = "sit_to_stand") -> ClientAction:
    clean_ex = exercise_type.lower().strip() if exercise_type else "sit_to_stand"
    if clean_ex not in ALLOWED_REHAB_EXERCISES:
        clean_ex = "sit_to_stand"
    return ClientAction(
        action_type=ActionType.REHAB_START,
        payload={"exercise_type": clean_ex},
        target_component="rehab_container",
    )


def make_media_action(action_type: ActionType, playlist: Optional[str] = None) -> ClientAction:
    assert action_type in (ActionType.MEDIA_PLAY, ActionType.MEDIA_PAUSE, ActionType.MEDIA_STOP)
    payload: Dict[str, Any] = {}
    if playlist:
        payload["playlist"] = str(playlist)[:100]
    return ClientAction(
        action_type=action_type,
        payload=payload,
        target_component="music_player",
    )


def make_dialog_speak_action(speech_text: str) -> ClientAction:
    return ClientAction(
        action_type=ActionType.DIALOG_SPEAK,
        payload={"speech_text": str(speech_text)[:300]},
        target_component="voice_speaker",
    )

