"""
Games and Media (Music) Business Flow Adapter.
Implements Workflows W5, W6 and Phase 5 of VOICE_AI_IMPLEMENTATION_PLAN.md.

Guarantees:
1. Allowlisted game types only ('card_matching', 'sequence_recall').
2. Allowlisted media commands ('media.play', 'media.pause', 'media.stop').
3. No arbitrary execution or unvetted parameter passing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from features.voice_assistant.voice_ai.domain.contracts import IntentType, VoiceIntent
from features.voice_assistant.voice_ai.orchestrator.actions import (
    ActionType,
    ALLOWED_GAMES,
    make_game_open_action,
    make_media_action,
)

logger = logging.getLogger("voice_ai.business.media_games")


class MediaAndGamesAdapter:
    """Handles cognitive games and music playback commands."""

    def handle_game(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
    ) -> Dict[str, Any]:
        """Validates game launch and emits allowlisted client token."""
        raw_type = str(intent.entities.get("game_type", "card_matching")).lower().strip()
        clean_game = raw_type if raw_type in ALLOWED_GAMES else "card_matching"
        action = make_game_open_action(clean_game)

        game_display_names = {
            "card_matching": "trò chơi lật thẻ trí nhớ",
            "sequence_recall": "trò chơi ghi nhớ dãy số",
        }
        name = game_display_names.get(clean_game, "trò chơi")

        return {
            "status": "COMPLETED",
            "game_type": clean_game,
            "action": action.to_dict(),
            "message": f"Con mở {name} cho cụ ngay đây ạ.",
        }

    def handle_music(
        self,
        auth_context: Dict[str, Any],
        intent: VoiceIntent,
    ) -> Dict[str, Any]:
        """Validates music command and emits allowlisted client token."""
        playlist = intent.entities.get("playlist")

        if intent.type == IntentType.MUSIC_PLAY:
            action = make_media_action(ActionType.MEDIA_PLAY, playlist=playlist)
            msg = f"Con bật {playlist or 'nhạc nhẹ'} cho cụ nghe đây ạ."
        elif intent.type == IntentType.MUSIC_PAUSE:
            action = make_media_action(ActionType.MEDIA_PAUSE)
            msg = "Con đã tạm dừng nhạc ạ."
        elif intent.type == IntentType.MUSIC_STOP:
            action = make_media_action(ActionType.MEDIA_STOP)
            msg = "Con đã tắt nhạc rồi ạ."
        else:
            action = make_media_action(ActionType.MEDIA_STOP)
            msg = "Con đã dừng phát nhạc ạ."

        return {
            "status": "COMPLETED",
            "action_type": action.action_type.value,
            "action": action.to_dict(),
            "message": msg,
        }

