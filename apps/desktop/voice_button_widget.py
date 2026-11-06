"""
Push-to-Talk (PTT) Voice Interaction Widget for Senior Desktop Dashboard.
Designed specifically for elderly users:
- Large tactile button (min-height 52px), prominent microphone icon.
- Immediate visual state feedback: Idle (Blue) -> Recording (Red) -> Processing (Amber) -> Success (Green).
- Mouse press/release and Spacebar keyboard activation.
- Phase B & C: ASR separation. Supports VoiceSessionController for backend STT gateway,
  and emits audio_captured and transcript_ready signals safely.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt5.QtCore import QEvent, QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from features.voice_assistant.voice_ai.audio.capture import AudioRecorder
from features.voice_assistant.voice_ai.domain.contracts import TranscriptResult

logger = logging.getLogger("VoiceButtonWidget")


class _MockOrDirectWorker(QThread):
    """Off-thread worker for local mock or testing provider."""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, provider: Any, wav_bytes: bytes) -> None:
        super().__init__()
        self.provider = provider
        self.wav_bytes = wav_bytes

    def run(self) -> None:
        try:
            res = self.provider.transcribe(self.wav_bytes, language="vi")
            self.finished.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class VoiceButtonWidget(QWidget):
    """
    Push-to-Talk audio capture widget.

    Responsibilities:
    - Record audio via AudioRecorder on PTT press/release.
    - Enforce max-duration cutoff and debounce.
    - Emit recording_started, audio_captured(bytes), transcript_ready(object).
    - Can be wired directly to a VoiceSessionController or handle mock ASR in tests.
    - Safe UI thread updates.
    """

    recording_started = pyqtSignal()
    audio_captured = pyqtSignal(bytes)          # WAV bytes ready for STT gateway
    transcript_ready = pyqtSignal(object)       # Emits TranscriptResult
    interaction_error = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        controller: Optional[Any] = None,
        asr_provider: Optional[Any] = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        if asr_provider is not None:
            self.asr_provider = asr_provider
        self.recorder = AudioRecorder()
        self.recorder.set_max_duration_callback(self._on_max_duration_safety_cutoff)
        self._active_worker: Optional[_MockOrDirectWorker] = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Main Push-to-Talk Button
        self.btn_ptt = QPushButton("🎙 GIỮ ĐỂ NÓI (PUSH-TO-TALK)")
        self.btn_ptt.setObjectName("btnVoicePTT")
        self.btn_ptt.setMinimumHeight(48)
        self.btn_ptt.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.btn_ptt.setFocusPolicy(Qt.StrongFocus)
        self._apply_idle_style()

        self.btn_ptt.pressed.connect(self._on_ptt_pressed)
        self.btn_ptt.released.connect(self._on_ptt_released)
        self.btn_ptt.installEventFilter(self)
        layout.addWidget(self.btn_ptt)

        # Status label
        self.lbl_status = QLabel("Nhấn giữ phím để nói với Trợ lý AI")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #5A6E85;")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status, 1)

    # ------------------------------------------------------------------
    # Visual state helpers
    # ------------------------------------------------------------------

    def _apply_idle_style(self) -> None:
        self.btn_ptt.setText("🎙 GIỮ ĐỂ NÓI")
        self.btn_ptt.setStyleSheet(
            """
            QPushButton#btnVoicePTT {
                background-color: #285C4D;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 800;
            }
            QPushButton#btnVoicePTT:hover {
                background-color: #1A5BB8;
            }
            """
        )

    def _apply_recording_style(self) -> None:
        self.btn_ptt.setText("🔴 ĐANG NGHE... (THẢ TAY KHI NÓI XONG)")
        self.btn_ptt.setStyleSheet(
            """
            QPushButton#btnVoicePTT {
                background-color: #D32F2F;
                color: #FFFFFF;
                border: 2px solid #FFCDD2;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 800;
            }
            """
        )
        self.lbl_status.setText("Cụ đang nói... Con đang chú ý lắng nghe nhé.")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #D32F2F;")

    def _apply_processing_style(self) -> None:
        self.btn_ptt.setText("⏳ ĐANG CHUYỂN THÀNH CHỮ...")
        self.btn_ptt.setStyleSheet(
            """
            QPushButton#btnVoicePTT {
                background-color: #ED6C02;
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 800;
            }
            """
        )
        self.lbl_status.setText("Đang chuyển giọng nói thành văn bản...")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #ED6C02;")

    def set_status(self, text: str, color: str = "#5A6E85") -> None:
        """Allow controller to update status label cleanly."""
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {color};")
        self._apply_idle_style()

    # ------------------------------------------------------------------
    # Keyboard / Event handling
    # ------------------------------------------------------------------

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Filters out OS keyboard auto-repeat bounce on PTT button."""
        if watched == self.btn_ptt:
            if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Return):
                if event.isAutoRepeat():
                    return True
                if not self.recorder.is_recording:
                    self._on_ptt_pressed()
                return True
            elif event.type() == QEvent.KeyRelease and event.key() in (Qt.Key_Space, Qt.Key_Return):
                if event.isAutoRepeat():
                    return True
                if self.recorder.is_recording:
                    self._on_ptt_released()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return):
            if event.isAutoRepeat():
                event.ignore()
                return
            if not self.recorder.is_recording:
                self._on_ptt_pressed()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return):
            if event.isAutoRepeat():
                event.ignore()
                return
            if self.recorder.is_recording:
                self._on_ptt_released()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # PTT logic
    # ------------------------------------------------------------------

    def _on_ptt_pressed(self) -> None:
        """Triggered when elder presses down mouse or Spacebar on PTT button."""
        if self.recorder.start_recording():
            self._apply_recording_style()
            self.recording_started.emit()
            if self.controller and hasattr(self.controller, "on_recording_started"):
                self.controller.on_recording_started()

    def _on_ptt_released(self) -> None:
        """Triggered when elder releases button or Spacebar."""
        if not self.recorder.is_recording:
            return

        wav_bytes = self.recorder.stop_recording()
        if not wav_bytes:
            self._apply_idle_style()
            self.lbl_status.setText("Nhấn giữ phím để nói với Trợ lý AI")
            self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #5A6E85;")
            return

        self._apply_processing_style()
        self.audio_captured.emit(wav_bytes)

        if self.controller and hasattr(self.controller, "process_audio"):
            self.controller.process_audio(wav_bytes)
        elif getattr(self, "asr_provider", None):
            self._active_worker = _MockOrDirectWorker(self.asr_provider, wav_bytes)
            self._active_worker.finished.connect(self._on_direct_worker_finished)
            self._active_worker.error.connect(self._on_direct_worker_error)
            self._active_worker.start()

    def _on_direct_worker_finished(self, result: Optional[TranscriptResult]) -> None:
        self._apply_idle_style()
        if result and getattr(result, "transcript", None):
            self.lbl_status.setText(f"“{result.transcript}”")
            self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #35634D;")
            self.transcript_ready.emit(result)
        else:
            self.lbl_status.setText("Chưa nhận rõ câu nói. Cụ bấm lại để nói nhé.")
            self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #ED6C02;")

    def _on_direct_worker_error(self, err_msg: str) -> None:
        self._apply_idle_style()
        self.lbl_status.setText("Lỗi nhận diện giọng nói.")
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 600; color: #C3293E;")
        self.interaction_error.emit(err_msg)

    def _on_max_duration_safety_cutoff(self) -> None:
        """Invoked from background timer when recording reaches max duration limit."""
        QTimer.singleShot(0, self._on_ptt_released)
