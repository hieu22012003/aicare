"""
Relaxing Music Player & Optional Binaural Alpha 10Hz Trial Widget.
Guardrails enforced:
- Default OFF: never auto-plays on startup (Criterion 8).
- Sleep timer countdown (5/10/15 min) and auto-stop (Criterion 8).
- Binaural Alpha mode OFF by default; requires stereo headphone confirmation and explicit consent (Criterion 9).
- Strictly NO dementia cure or cognitive boost claims (Criterion 9).
- Stop feedback is completely optional and skippable (Criterion 8).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QSize, Qt, QTimer, QUrl
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from features.music_care.cognitive_games.domain.models import (
    AudioMode,
    FeedbackRating,
    ListeningSession,
    MusicTrack,
)
from features.music_care.cognitive_games.services.audio_service import AudioService

logger = logging.getLogger("MusicPlayerUI")


class MusicPlayerWidget(QWidget):
    def __init__(self, audio_service: AudioService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.audio_service = audio_service
        self.patient_id = "pat-001"

        # Qt Multimedia player (with safe fallback)
        self._media_player = None
        self._init_media_player()

        # Local timer for UI countdown and service ticking
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)

        self._init_ui()
        self._refresh_track_list()
        self._catalog_timer = QTimer(self)
        self._catalog_timer.setInterval(2000)
        self._catalog_timer.timeout.connect(self._refresh_track_list)
        self._catalog_timer.start()

    def set_patient_id(self, patient_id: str) -> None:
        if patient_id != self.patient_id and self.audio_service.current_track:
            if self._media_player:
                self._media_player.stop()
            self.audio_service.stop_playback(stopped_reason='patient_changed')
            self._tick_timer.stop()
            self.btn_play.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.info_banner.setText('Đã chuyển hồ sơ. Chọn bài nhạc để nghe.')
        self.patient_id = patient_id
        self._track_signature = None
        self._refresh_track_list()

    def _init_media_player(self) -> None:
        from features.music_care.music_playback import PcmMusicPlayer
        self._media_player = PcmMusicPlayer(self)
        self._media_player.started.connect(self._on_audio_started)
        self._media_player.failed.connect(self._on_audio_failed)
        self._media_player.finished.connect(self._on_audio_finished)

    def _on_audio_started(self) -> None:
        track = self.audio_service.current_track
        if track:
            self.info_banner.setText(f"🎶 Đang phát: {track.title}")

    def _on_audio_failed(self, message) -> None:
        logger.error("Không thể phát nhạc: %s", message)
        self._media_player.stop()
        self.audio_service.stop_playback(stopped_reason='playback_error')
        self._tick_timer.stop()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.info_banner.setText("Không phát được nhạc. Kiểm tra loa/tai nghe và âm lượng Windows. Chi tiết: " + message)

    def _on_audio_finished(self) -> None:
        self.audio_service.stop_playback(stopped_reason='finished')
        self._tick_timer.stop()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ Tạm Dừng")
        self.btn_stop.setEnabled(False)
        self.info_banner.setText("Đã phát hết bài nhạc. Bác có thể chọn bài khác.")

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # 1. Header
        header = QLabel("🎵 Âm Nhạc Thư Giãn Dành Cho Người Cao Tuổi")
        header.setFont(QFont("Arial", 20, QFont.Bold))
        header.setStyleSheet("color: #214A3B;")
        main_layout.addWidget(header)

        # 2. Safety & Information Notice (Criterion 8, 9)
        self.info_banner = QLabel(
            "Giai điệu êm dịu giúp tâm trí thư thái. Nhạc KHÔNG tự động phát khi mở ứng dụng. "
            "Bác có thể nghe độc lập hoặc chọn hẹn giờ tắt tự động."
        )
        self.info_banner.setFont(QFont("Arial", 14))
        self.info_banner.setWordWrap(True)
        self.info_banner.setStyleSheet(
            """
            background-color: #EEF3E9;
            color: #214A3B;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #A5D6A7;
            """
        )
        main_layout.addWidget(self.info_banner)

        # 3. Track Selection Area
        track_box = QGroupBox("Danh Sách Giai Điệu Thư Giãn")
        track_box.setFont(QFont("Arial", 14, QFont.Bold))
        track_layout = QVBoxLayout(track_box)
        track_layout.setSpacing(10)

        self.combo_tracks = QComboBox()
        self.combo_tracks.setFont(QFont("Arial", 14))
        self.combo_tracks.setMinimumHeight(48)
        self.combo_tracks.currentIndexChanged.connect(self._on_track_selected)
        track_layout.addWidget(self.combo_tracks)

        self.track_desc = QLabel("Mô tả bài nhạc...")
        self.track_desc.setFont(QFont("Arial", 13))
        self.track_desc.setStyleSheet("color: #455A64;")
        self.track_desc.setWordWrap(True)
        track_layout.addWidget(self.track_desc)
        self.family_music_notice = QLabel('')
        self.family_music_notice.setWordWrap(True)
        self.family_music_notice.setStyleSheet('color: #1565C0; font-weight: 600;')
        track_layout.addWidget(self.family_music_notice)

        main_layout.addWidget(track_box)

        # 4. Experimental Binaural Beats Toggle Box (Criterion 9)
        binaural_box = QGroupBox("Tùy Chọn Âm Thanh Thử Nghiệm (Binaural Beats 10Hz)")
        binaural_box.setFont(QFont("Arial", 13, QFont.Bold))
        b_layout = QVBoxLayout(binaural_box)

        self.chk_binaural = QCheckBox("Bật chế độ thử nghiệm âm thanh sóng não Alpha 10Hz (Mặc định: TẮT)")
        self.chk_binaural.setFont(QFont("Arial", 13, QFont.Bold))
        self.chk_binaural.setChecked(False)  # MUST BE OFF BY DEFAULT (Criterion 9)
        self.chk_binaural.clicked.connect(self._on_binaural_toggled)
        b_layout.addWidget(self.chk_binaural)

        binaural_notice = QLabel(
            "ℹ Nhánh thử nghiệm: Cần tai nghe stereo hai bên tai. "
            "Đây là hoạt động thư giãn, KHÔNG phải thuốc chữa bệnh hay phương pháp điều trị sa sút trí tuệ."
        )
        binaural_notice.setFont(QFont("Arial", 12))
        binaural_notice.setStyleSheet("color: #795548;")
        binaural_notice.setWordWrap(True)
        b_layout.addWidget(binaural_notice)

        main_layout.addWidget(binaural_box)

        # 5. Playback Controls & Volume
        controls_box = QGroupBox("Điều Khiển Phát Nhạc")
        controls_box.setFont(QFont("Arial", 14, QFont.Bold))
        c_layout = QVBoxLayout(controls_box)
        c_layout.setSpacing(14)

        # Play / Pause / Stop buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self.btn_play = QPushButton("▶ Phát Nhạc")
        self.btn_play.setMinimumHeight(56)
        self.btn_play.setFont(QFont("Arial", 15, QFont.Bold))
        self.btn_play.setStyleSheet(
            """
            QPushButton {
                background-color: #35634D;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 10px 24px;
            }
            QPushButton:hover { background-color: #214A3B; }
            """
        )
        self.btn_play.clicked.connect(self._on_play_clicked)
        btn_row.addWidget(self.btn_play)

        self.btn_pause = QPushButton("⏸ Tạm Dừng")
        self.btn_pause.setMinimumHeight(56)
        self.btn_pause.setFont(QFont("Arial", 15, QFont.Bold))
        self.btn_pause.setStyleSheet(
            """
            QPushButton {
                background-color: #356C60;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #28574D; }
            """
        )
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        self.btn_pause.setEnabled(False)
        btn_row.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹ Dừng Nhạc")
        self.btn_stop.setMinimumHeight(56)
        self.btn_stop.setFont(QFont("Arial", 15, QFont.Bold))
        self.btn_stop.setStyleSheet(
            """
            QPushButton {
                background-color: #D32F2F;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 10px 20px;
            }
            QPushButton:hover { background-color: #C62828; }
            """
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_stop)

        c_layout.addLayout(btn_row)

        # Volume slider (gentle starting volume, Criterion 8)
        vol_layout = QHBoxLayout()
        vol_lbl = QLabel("🔊 Âm lượng khởi đầu nhẹ nhàng:")
        vol_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        vol_layout.addWidget(vol_lbl)

        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(self.audio_service.volume)
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        vol_layout.addWidget(self.vol_slider)

        self.vol_val_lbl = QLabel(f"{self.audio_service.volume}%")
        self.vol_val_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        self.vol_val_lbl.setMinimumWidth(50)
        vol_layout.addWidget(self.vol_val_lbl)

        c_layout.addLayout(vol_layout)

        # Sleep Timer Options (5, 10, 15 min or none)
        timer_layout = QHBoxLayout()
        timer_lbl = QLabel("⏳ Hẹn giờ tự tắt:")
        timer_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        timer_layout.addWidget(timer_lbl)

        self.combo_timer = QComboBox()
        self.combo_timer.setFont(QFont("Arial", 13))
        self.combo_timer.setMinimumHeight(40)
        self.combo_timer.addItem("Không hẹn giờ (Nghe tự do)", 0)
        self.combo_timer.addItem("5 phút", 5)
        self.combo_timer.addItem("10 phút", 10)
        self.combo_timer.addItem("15 phút", 15)
        timer_layout.addWidget(self.combo_timer)

        self.timer_status = QLabel("")
        self.timer_status.setFont(QFont("Arial", 13, QFont.Bold))
        self.timer_status.setStyleSheet("color: #E65100;")
        timer_layout.addWidget(self.timer_status)
        timer_layout.addStretch()

        c_layout.addLayout(timer_layout)

        main_layout.addWidget(controls_box)
        main_layout.addStretch()

    def _refresh_track_list(self) -> None:
        try:
            tracks = self.audio_service.list_tracks()
        except Exception:
            logger.exception('Không thể cập nhật thư viện nhạc')
            return

        # If binaural trial unchecked, filter only ordinary tracks
        is_binaural_allowed = self.chk_binaural.isChecked()
        tracks = [t for t in tracks if is_binaural_allowed or t.audio_mode != AudioMode.BINAURAL_TRIAL.value]
        signature = [(t.track_id, t.title) for t in tracks]
        if signature == getattr(self, '_track_signature', None):
            return
        self._track_signature = signature
        active = self.audio_service.current_track
        if active and active.track_id not in {t.track_id for t in tracks}:
            if self._media_player:
                self._media_player.stop()
            self.audio_service.stop_playback(stopped_reason='track_deleted')
            self._tick_timer.stop()
            self.btn_pause.setEnabled(False)
            self.btn_pause.setText('⏸ Tạm Dừng')
            self.btn_stop.setEnabled(False)
            self.timer_status.setText('')
            self.info_banner.setText('Bài nhạc đã được xoá khỏi danh sách. Bác có thể chọn bài khác để nghe.')
        previous = self.combo_tracks.currentData()
        self.combo_tracks.blockSignals(True)
        self.combo_tracks.clear()
        for t in tracks:
            display_title = ('[Người nhà] ' if t.track_id.startswith('family_') else '') + t.title
            if t.audio_mode == AudioMode.BINAURAL_TRIAL.value:
                display_title += " [Thử nghiệm 10Hz]"
            self.combo_tracks.addItem(display_title, t.track_id)
        previous_index = self.combo_tracks.findData(previous)
        if previous_index >= 0:
            self.combo_tracks.setCurrentIndex(previous_index)
        self.combo_tracks.blockSignals(False)
        self.combo_tracks.setEnabled(bool(tracks))
        self.btn_play.setEnabled(bool(tracks) and not self.audio_service.is_playing)
        if not tracks:
            self.track_desc.setText('Chưa có bài nhạc. Người nhà có thể tải bài hát lên từ mục Âm thanh & Bài hát.')
        count = sum(t.track_id.startswith('family_') for t in tracks)
        self.family_music_notice.setText(f'💌 Đã nhận {count} bài nhạc từ người nhà. Bác chọn bài có nhãn [Người nhà] rồi bấm Phát Nhạc.' if count else '')

        self._on_track_selected()

    def _on_track_selected(self) -> None:
        track_id = self.combo_tracks.currentData()
        if not track_id:
            return
        track = self.audio_service.get_track(track_id)
        if track:
            self.track_desc.setText(
                f"Tác giả: {track.artist_attribution} | Bản quyền: {track.license}\n"
                f"{track.description}"
            )

    def _on_binaural_toggled(self, checked: bool) -> None:
        """Enforces stereo headphone consent before activating binaural trial (Criterion 9)."""
        if checked:
            dlg = QDialog(self)
            dlg.setWindowTitle("Xác Nhận Thử Nghiệm Âm Thanh Binaural Beats")
            dlg.setMinimumSize(500, 320)
            vbox = QVBoxLayout(dlg)
            vbox.setSpacing(14)
            vbox.setContentsMargins(20, 20, 20, 20)

            title = QLabel("⚠️ Hướng Dẫn & Cam Kết An Toàn:")
            title.setFont(QFont("Arial", 15, QFont.Bold))
            title.setStyleSheet("color: #C62828;")
            vbox.addWidget(title)

            msg = QLabel(
                "1. Để tạo hiệu ứng 10Hz, bạn BẮT BUỘC phải đeo tai nghe stereo 2 bên tai.\n"
                "2. Đây là hoạt động âm thanh thư giãn thử nghiệm, KHÔNG PHẢI phương pháp chữa bệnh hay tăng trí nhớ đã kiểm chứng y khoa.\n"
                "3. Bác hãy bấm dừng ngay nếu cảm thấy chóng mặt, đau đầu hoặc khó chịu."
            )
            msg.setFont(QFont("Arial", 13))
            msg.setWordWrap(True)
            vbox.addWidget(msg)

            chk_agree = QCheckBox("Tôi hiểu, đã chuẩn bị tai nghe stereo và đồng ý nghe thử nghiệm.")
            chk_agree.setFont(QFont("Arial", 13, QFont.Bold))
            vbox.addWidget(chk_agree)

            btn_box = QHBoxLayout()
            btn_ok = QPushButton("Đồng Ý Kích Hoạt")
            btn_ok.setMinimumHeight(48)
            btn_ok.setFont(QFont("Arial", 13, QFont.Bold))
            btn_ok.setStyleSheet("background-color: #35634D; color: #FFF; border-radius: 6px;")

            btn_cancel = QPushButton("Hủy Bỏ")
            btn_cancel.setMinimumHeight(48)
            btn_cancel.setFont(QFont("Arial", 13, QFont.Bold))
            btn_cancel.setStyleSheet("background-color: #757575; color: #FFF; border-radius: 6px;")

            btn_box.addWidget(btn_ok)
            btn_box.addWidget(btn_cancel)
            vbox.addLayout(btn_box)

            def on_ok() -> None:
                if not chk_agree.isChecked():
                    QMessageBox.warning(dlg, "Chưa đồng ý", "Bác vui lòng tích chọn đồng ý trước khi kích hoạt.")
                    return
                dlg.accept()

            btn_ok.clicked.connect(on_ok)
            btn_cancel.clicked.connect(dlg.reject)

            if dlg.exec_() != QDialog.Accepted:
                self.chk_binaural.setChecked(False)
        self._refresh_track_list()

    def _on_play_clicked(self) -> None:
        track_id = self.combo_tracks.currentData()
        if not track_id:
            return

        track = self.audio_service.get_track(track_id)
        if not track:
            return

        if not self._media_player or not Path(track.asset_uri).is_file():
            self.info_banner.setText("Không tìm thấy tệp nhạc hoặc thiết bị phát. Vui lòng tải lại bài nhạc.")
            return

        is_binaural = (track.audio_mode == AudioMode.BINAURAL_TRIAL.value)
        timer_min = self.combo_timer.currentData()

        try:
            self.audio_service.start_playback(
                patient_id=self.patient_id,
                track_id=track_id,
                timer_minutes=timer_min,
                is_binaural_consent=is_binaural,
                headphone_confirmed=is_binaural,
            )
        except Exception as e:
            QMessageBox.warning(self, "Không thể phát nhạc", str(e))
            return

        # Decode through PyAV and stream PCM, without relying on system MP3 codecs
        if self._media_player and os.path.exists(track.asset_uri):
            try:
                url = QUrl.fromLocalFile(os.path.abspath(track.asset_uri))
                self._media_player.setMedia(url)
                self._media_player.setVolume(self.audio_service.volume)
                self._media_player.play()
            except Exception as e:
                self._on_audio_failed(str(e))
                return

        self._tick_timer.start()
        self.btn_play.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.info_banner.setText(f"Đang chuẩn bị âm thanh: {track.title}...")

    def _on_pause_clicked(self) -> None:
        if self.audio_service.is_paused:
            self.audio_service.resume_playback()
            if self._media_player:
                self._media_player.play()
            self.btn_pause.setText("⏸ Tạm Dừng")
            self.info_banner.setText("🎶 Tiếp tục phát nhạc...")
        else:
            self.audio_service.pause_playback()
            if self._media_player:
                self._media_player.pause()
            self.btn_pause.setText("▶ Tiếp Tục")
            self.info_banner.setText("⏸ Nhạc đang tạm dừng.")

    def _on_stop_clicked(self) -> None:
        self._stop_and_prompt_feedback(stopped_reason="user_stopped")

    def _stop_and_prompt_feedback(self, stopped_reason: str) -> None:
        self._tick_timer.stop()
        if self._media_player:
            self._media_player.stop()

        self.timer_status.setText("")
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ Tạm Dừng")
        self.btn_stop.setEnabled(False)

        # Optional and skippable feedback survey (Criterion 8)
        dlg = QDialog(self)
        dlg.setWindowTitle("Cảm Nhận Sau Khi Nghe Nhạc")
        dlg.setObjectName('MusicFeedbackDialog')
        dlg.setStyleSheet('''
            QDialog#MusicFeedbackDialog { background-color: #FFFFFF; color: #000000; }
            QLabel, QRadioButton { background-color: transparent; color: #000000; }
            QPushButton { background-color: #FFFFFF; color: #000000; border: 1px solid #94A3B8;
                          border-radius: 6px; padding: 8px 14px; }
            QPushButton:hover { background-color: #F1F5F9; color: #000000; }
        ''')
        dlg.setMinimumSize(420, 260)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(14)
        vbox.setContentsMargins(20, 20, 20, 20)

        lbl = QLabel("Bác cảm thấy bản nhạc này thế nào? (Có thể bỏ qua nếu muốn):")
        lbl.setFont(QFont("Arial", 14, QFont.Bold))
        lbl.setWordWrap(True)
        vbox.addWidget(lbl)

        rb_pleasant = QRadioButton("🌸 Rất dễ chịu và thư giãn")
        rb_normal = QRadioButton("🍃 Bình thường")
        rb_disliked = QRadioButton("❌ Không thích")
        rb_pleasant.setChecked(True)

        for rb in [rb_pleasant, rb_normal, rb_disliked]:
            rb.setFont(QFont("Arial", 13))
            vbox.addWidget(rb)

        btn_box = QHBoxLayout()
        btn_submit = QPushButton("Gửi Cảm Nhận")
        btn_submit.setFont(QFont("Arial", 13, QFont.Bold))
        btn_submit.setMinimumHeight(44)
        btn_submit.setStyleSheet('background-color: #FFFFFF; color: #000000; border: 1px solid #94A3B8; border-radius: 6px;')

        btn_skip = QPushButton("Bỏ Qua")
        btn_skip.setFont(QFont("Arial", 13))
        btn_skip.setMinimumHeight(44)
        btn_skip.setStyleSheet('background-color: #FFFFFF; color: #000000; border: 1px solid #94A3B8; border-radius: 6px;')

        btn_box.addWidget(btn_submit)
        btn_box.addWidget(btn_skip)
        vbox.addLayout(btn_box)

        def finish_with(rating: FeedbackRating) -> None:
            self.audio_service.stop_playback(stopped_reason=stopped_reason, feedback=rating)
            dlg.accept()

        btn_submit.clicked.connect(
            lambda: finish_with(
                FeedbackRating.PLEASANT
                if rb_pleasant.isChecked()
                else (FeedbackRating.NORMAL if rb_normal.isChecked() else FeedbackRating.DISLIKED)
            )
        )
        btn_skip.clicked.connect(lambda: finish_with(FeedbackRating.SKIPPED))

        dlg.exec_()
        self.info_banner.setText("Đã dừng phát nhạc. Chúc bác có một ngày thật nhiều niềm vui!")

    def _on_volume_changed(self, val: int) -> None:
        vol = self.audio_service.set_volume(val)
        self.vol_val_lbl.setText(f"{vol}%")
        if self._media_player:
            self._media_player.setVolume(vol)

    def _on_tick(self) -> None:
        """Countdown sleep timer and auto-stop when 0 (Criterion 8)."""
        expired = self.audio_service.tick(1.0)
        if expired:
            self._stop_and_prompt_feedback(stopped_reason="sleep_timer_expired")
            return

        remaining = self.audio_service.sleep_timer_seconds
        if remaining is not None and remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            self.timer_status.setText(f"⏳ Tự tắt sau: {mins:02d}:{secs:02d}")
        else:
            self.timer_status.setText("")

    def closeEvent(self, event):
        self._catalog_timer.stop()
        self._tick_timer.stop()
        if self._media_player:
            self._media_player.stop()
        if self.audio_service.current_track:
            self.audio_service.stop_playback(stopped_reason='closed')
        super().closeEvent(event)
