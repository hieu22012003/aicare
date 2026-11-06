"""
Voice Session Controller.
Manages push-to-talk lifecycle, state machine transitions, context snapshots,
cancel/late response invalidation, elder change safety, and off-UI HTTP execution.
Implements Section 4 & 5 of KE_HOACH_TICH_HOP_API_STT.md.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional
import uuid

from PyQt5.QtCore import QObject, QThread, pyqtSignal

from features.voice_assistant.voice_ai.audio.capture import AudioRecorder
from features.voice_assistant.voice_ai.audio.tts_player import get_tts_coordinator
from features.voice_assistant.voice_ai.stt.client import SttClient
from features.voice_assistant.voice_ai.stt.contracts import SttErrorCode, SttRequest, SttResponse, SttSessionState

logger = logging.getLogger("VoiceSessionController")


class _SttWorkerThread(QThread):
    """Off-main-thread worker for HTTP transmission to STT Gateway."""

    finished = pyqtSignal(object)  # Emits SttResponse
    error = pyqtSignal(str)

    def __init__(self, client: SttClient, request: SttRequest, audio_bytes: bytes) -> None:
        super().__init__()
        self.client = client
        self.request = request
        self.audio_bytes = audio_bytes

    def run(self) -> None:
        try:
            resp = self.client.transcribe_audio(self.request, self.audio_bytes)
            self.finished.emit(resp)
        except Exception as ex:
            logger.error("[_SttWorkerThread] Unexpected error in HTTP worker: %s", ex)
            self.error.emit(str(ex))


class VoiceSessionController(QObject):
    """
    State machine and lifecycle coordinator for elder voice sessions.
    Runs on the UI side, with all network operations isolated in worker threads.
    """

    state_changed = pyqtSignal(str, str)          # (state_name, human_readable_message)
    transcript_ready = pyqtSignal(object)         # (SttResponse)
    interaction_ready = pyqtSignal(object)        # (OrchestratorExecutionResult or CommandResult)
    confirmation_needed = pyqtSignal(object)      # (Dict describing dose/action to confirm)
    error_occurred = pyqtSignal(str, str)         # (error_code, user_friendly_message)
    session_cancelled = pyqtSignal()

    def __init__(
        self,
        stt_client: Optional[SttClient] = None,
        audio_recorder: Optional[AudioRecorder] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.client = stt_client or SttClient()
        self.recorder = audio_recorder or AudioRecorder()
        self.state = SttSessionState.IDLE

        # Active Session Snapshot
        self.active_session_id: Optional[str] = None
        self.active_request_id: Optional[str] = None
        self.snapshot_patient_id: Optional[str] = None
        self.snapshot_screen: str = "today_todo"
        self.snapshot_context_type: Optional[str] = None
        self.snapshot_context_id: Optional[str] = None
        self.snapshot_context_version: Optional[int] = None
        self.recording_start_time: float = 0.0

        self._active_worker: Optional[_SttWorkerThread] = None

    def snapshot_context(
        self,
        patient_id: str,
        current_screen: str = "today_todo",
        context_type: Optional[str] = None,
        context_id: Optional[str] = None,
        context_version: Optional[int] = None,
    ) -> None:
        """Snapshots operational context at the start of interaction."""
        self.snapshot_patient_id = patient_id
        self.snapshot_screen = current_screen
        self.snapshot_context_type = context_type
        self.snapshot_context_id = context_id
        self.snapshot_context_version = context_version

    def on_recording_started(self, patient_id: Optional[str] = None) -> bool:
        """
        Called when PTT is pressed.
        Validates transition and starts recording.
        """
        if self.state not in (SttSessionState.IDLE, SttSessionState.SUCCESS, SttSessionState.ERROR, SttSessionState.CANCELLED):
            logger.warning("[Controller] Cannot start recording from state %s", self.state)
            return False

        # Generate unique IDs for this interaction turn
        self.active_session_id = f"vses_{uuid.uuid4().hex[:12]}"
        self.active_request_id = f"vreq_{uuid.uuid4().hex[:12]}"
        if patient_id:
            self.snapshot_patient_id = patient_id

        # Pause TTS playback to prevent mic leakage
        try:
            get_tts_coordinator().stop()
        except Exception:
            pass

        self.recording_start_time = time.time()
        self._set_state(SttSessionState.RECORDING, "Đang nghe... Cụ nói xong hãy thả nút ra nhé.")
        return True

    def process_audio(self, audio_bytes: bytes) -> None:
        """
        Called when PTT is released and AudioRecorder provides WAV bytes.
        Sends audio to backend STT Gateway outside the UI thread.
        """
        if self.state != SttSessionState.RECORDING:
            logger.warning("[Controller] process_audio called but state is %s", self.state)
            return

        duration_ms = int((time.time() - self.recording_start_time) * 1000)

        if not audio_bytes or len(audio_bytes) < 44:
            self._set_state(SttSessionState.ERROR, "Chưa thu được âm thanh. Cụ bấm lại để nói nhé.")
            self.error_occurred.emit(SttErrorCode.NO_AUDIO.value, "Chưa thu được âm thanh.")
            self._set_state(SttSessionState.IDLE, "Sẵn sàng.")
            return

        self._set_state(SttSessionState.TRANSCRIBING, "Đang chuyển giọng nói thành văn bản...")

        req = SttRequest(
            request_id=self.active_request_id or str(uuid.uuid4()),
            voice_session_id=self.active_session_id or str(uuid.uuid4()),
            patient_id=self.snapshot_patient_id or "unknown",
            language="vi",
            context_type=self.snapshot_context_type,
            context_id=self.snapshot_context_id,
            context_version=self.snapshot_context_version,
            duration_ms=duration_ms,
        )

        # Offload network HTTP call to background QThread
        self._active_worker = _SttWorkerThread(self.client, req, audio_bytes)
        self._active_worker.finished.connect(self._on_stt_worker_finished)
        self._active_worker.error.connect(self._on_stt_worker_error)
        self._active_worker.start()

    def _on_stt_worker_finished(self, response: SttResponse) -> None:
        """
        Handles completion from background HTTP worker.
        Safely runs on the main Qt event loop.
        """
        # Late response / session invalidation check
        if response.voice_session_id != self.active_session_id or self.state == SttSessionState.CANCELLED:
            logger.info(
                "[Controller] Discarding late STT response for session %s (current: %s, state: %s)",
                response.voice_session_id,
                self.active_session_id,
                self.state,
            )
            return

        if response.status != "completed" or not response.transcript:
            err_code = response.error_code or SttErrorCode.UNKNOWN_ERROR.value
            msg = self._get_friendly_error_message(err_code)
            self._set_state(SttSessionState.ERROR, msg)
            self.error_occurred.emit(err_code, msg)
            self._set_state(SttSessionState.IDLE, "Sẵn sàng.")
            return

        # Transcript received successfully
        self._set_state(SttSessionState.REVIEW_CONFIRMING, f"Bạn vừa nói: “{response.transcript}”")
        self.transcript_ready.emit(response)

    def _on_stt_worker_error(self, error_str: str) -> None:
        if self.state == SttSessionState.CANCELLED:
            return
        logger.error("[Controller] Worker error: %s", error_str)
        self._set_state(SttSessionState.ERROR, "Không thể kết nối máy chủ nhận diện.")
        self.error_occurred.emit(SttErrorCode.PROVIDER_UNAVAILABLE.value, "Mất kết nối nhận diện giọng nói.")
        self._set_state(SttSessionState.IDLE, "Sẵn sàng.")

    def cancel(self) -> None:
        """
        Cancels active session.
        Invalidates active session ID so any in-flight response is discarded.
        """
        logger.info("[Controller] Cancelling voice session: %s", self.active_session_id)
        self.active_session_id = None
        self.active_request_id = None
        self._set_state(SttSessionState.CANCELLED, "Đã hủy phiên nói chuyện.")
        self.session_cancelled.emit()
        self._set_state(SttSessionState.IDLE, "Sẵn sàng.")

    def notify_patient_changed(self, new_patient_id: str) -> None:
        """
        If user switches elder profile during an ongoing voice turn,
        immediately invalidate session so old elder commands never execute.
        """
        if self.snapshot_patient_id and self.snapshot_patient_id != new_patient_id:
            logger.warning(
                "[Controller] Patient changed from %s to %s during voice interaction. Cancelling session.",
                self.snapshot_patient_id,
                new_patient_id,
            )
            self.cancel()
            self.snapshot_patient_id = new_patient_id

    def execution_success(self, message: str = "Đã thực hiện xong.") -> None:
        self._set_state(SttSessionState.SUCCESS, message)
        self._set_state(SttSessionState.IDLE, "Sẵn sàng.")

    def _set_state(self, new_state: SttSessionState, message: str) -> None:
        self.state = new_state
        self.state_changed.emit(new_state.value, message)

    def _get_friendly_error_message(self, error_code: str) -> str:
        mapping = {
            SttErrorCode.NO_AUDIO.value: "Chưa nhận rõ câu nói. Cụ bấm lại để nói nhé.",
            SttErrorCode.INVALID_AUDIO.value: "Âm thanh không rõ hoặc quá ngắn.",
            SttErrorCode.MICROPHONE_UNAVAILABLE.value: "Không tìm thấy mic thu âm. Vui lòng kiểm tra thiết bị.",
            SttErrorCode.AUTH_REQUIRED.value: "Phiên làm việc hết hạn. Vui lòng đăng nhập lại.",
            SttErrorCode.FORBIDDEN.value: "Không có quyền thực hiện thao tác này.",
            SttErrorCode.TIMEOUT.value: "Máy chủ phản hồi quá lâu. Cụ thử nói lại nhé.",
            SttErrorCode.RATE_LIMITED.value: "Nói quá nhanh. Cụ đợi vài giây rồi thử lại nhé.",
            SttErrorCode.PROVIDER_UNAVAILABLE.value: "Dịch vụ nhận diện tạm bận. Cụ có thể dùng nút bấm.",
            SttErrorCode.PATIENT_MISMATCH.value: "Hồ sơ người cao tuổi đã thay đổi.",
            SttErrorCode.VERSION_CONFLICT.value: "Dữ liệu vừa được cập nhật từ màn hình khác. Đang tải lại.",
        }
        return mapping.get(error_code, "Đã xảy ra lỗi khi xử lý giọng nói. Cụ thử lại nhé.")

