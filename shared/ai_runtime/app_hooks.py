"""The only entry points the existing desktop app calls. Each one fails safe: an error here is
logged and the app carries on exactly as before.

    apps/desktop/dashboard.py            start_ai_core(window) after the camera starts, stop in closeEvent
    features/voice_assistant/local_voice/...    voice_reply(window, patient_id, transcript) before the strict intents
"""

from __future__ import annotations

import logging

log = logging.getLogger("AICore")
_runtime = None


def get_runtime():
    return _runtime


_waker = None  # keeps the Qt bridge alive


def _make_camera_waker(window):
    """Thread-safe 'switch the camera on' for the AI worker threads.

    The signal is emitted from a worker thread; the QObject lives in the UI thread, so Qt queues the
    call and the camera is started on the UI thread like the app does at launch.
    """
    global _waker
    try:
        from PyQt5.QtCore import QObject, pyqtSignal

        class _CameraWaker(QObject):
            wake = pyqtSignal()

        def on_wake():
            cam = window.camera_manager
            try:
                cam.resume()
                cam.start_camera()
                bar = window.statusBar() if hasattr(window, "statusBar") else None
                if bar is not None:
                    bar.showMessage("💊 Đến giờ uống thuốc: camera đã bật để giám sát uống thuốc", 15000)
            except Exception:
                log.exception("Camera wake-up failed")

        _waker = _CameraWaker()
        _waker.wake.connect(on_wake)
        return _waker.wake.emit
    except Exception:
        log.exception("Camera wake-up bridge unavailable")
        return None


def start_ai_core(window):
    """Create and start the AI worker for the dashboard window. Returns the runtime or None.

    Cloud device mode (AICARE_CLOUD_URL + AICARE_DEVICE_KEY): landmarks go to the Render backend,
    which owns the medication schedule and the caregiver portal. Otherwise the brain runs locally.
    """
    global _runtime
    try:
        from shared.ai_runtime.runtime import AICoreRuntime, enabled

        if not enabled():
            return None

        def provider():
            face = window.router.face_adapter.get_latest_recognition() or {}
            pid = getattr(window, "_active_resolved_pid", None) or getattr(window.today_todo_widget, "patient_id", None)
            return pid, face.get("status") == "recognized"

        def speak(text: str) -> None:
            try:
                from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator

                get_tts_coordinator().speak(text)
            except Exception:
                log.exception("TTS failed: %s", text)

        wake = _make_camera_waker(window)
        from shared.ai_runtime.cloud_device import CloudDeviceRuntime, HttpTransport, cloud_settings

        cloud = cloud_settings()
        if cloud:
            runtime = CloudDeviceRuntime(HttpTransport(*cloud), window.camera_manager, provider, speak, wake_camera=wake,
                                         db=window.unified_db)
        else:
            runtime = AICoreRuntime(window.unified_db, window.camera_manager, provider, speak, wake_camera=wake)
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
    if not patient_id or not transcript:
        return None
    try:
        from features.voice_assistant.clock_answers import answer as clock_answer

        reply = clock_answer(transcript)  # "mấy giờ rồi", "hôm nay thứ mấy": works offline, no AI core needed
        if reply is None and _runtime is not None:
            reply = _runtime.handle_transcript(patient_id, transcript)
        if reply:
            try:
                from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator

                get_tts_coordinator().speak(reply)
            except Exception:
                pass
        return reply
    except Exception:
        log.exception("AI voice handling failed")
        return None
