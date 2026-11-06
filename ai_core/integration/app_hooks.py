"""The only entry points the existing desktop app calls. Each one fails safe: an error here is
logged and the app carries on exactly as before.

    app/dashboard.py            start_ai_core(window) after the camera starts, stop in closeEvent
    services/local_voice/...    voice_reply(window, patient_id, transcript) before the strict intents
"""

from __future__ import annotations

import logging

log = logging.getLogger("AICore")
_runtime = None


def get_runtime():
    return _runtime


def start_ai_core(window):
    """Create and start the AI worker for the dashboard window. Returns the runtime or None."""
    global _runtime
    try:
        from ai_core.integration.runtime import AICoreRuntime, enabled

        if not enabled():
            return None

        def provider():
            face = window.router.face_adapter.get_latest_recognition() or {}
            pid = getattr(window, "_active_resolved_pid", None) or getattr(window.today_todo_widget, "patient_id", None)
            return pid, face.get("status") == "recognized"

        def speak(text: str) -> None:
            try:
                from services.voice_ai.audio.tts_player import get_tts_coordinator

                get_tts_coordinator().speak(text)
            except Exception:
                log.exception("TTS failed: %s", text)

        runtime = AICoreRuntime(window.unified_db, window.camera_manager, provider, speak)
        if runtime.start():
            _runtime = runtime
            return runtime
    except Exception:
        log.exception("AI core could not start; the app continues without it")
    return None


def stop_ai_core(runtime) -> None:
    global _runtime
    try:
        if runtime is not None:
            runtime.stop()
    except Exception:
        log.exception("AI core stop failed")
    _runtime = None


def voice_reply(patient_id: str | None, transcript: str) -> str | None:
    """Answer utterances owned by the AI features ('kính của tôi đâu', fall check-in replies)."""
    if not patient_id or not transcript or _runtime is None:
        return None
    try:
        reply = _runtime.handle_transcript(patient_id, transcript)
        if reply:
            try:
                from services.voice_ai.audio.tts_player import get_tts_coordinator

                get_tts_coordinator().speak(reply)
            except Exception:
                pass
        return reply
    except Exception:
        log.exception("AI voice handling failed")
        return None
