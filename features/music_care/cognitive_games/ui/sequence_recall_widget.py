"""
Sequence Recall Widget (Nhớ trình tự) for Elderly Cognitive Exercises.
Adheres to:
- Distinct phases using QStackedWidget:
  1. Memorization Phase (Xem và ghi nhớ)
  2. Recall Phase (Ẩn hoàn toàn đáp án, tự chọn theo trí nhớ)
  3. Reveal Answer Phase (Hiện đáp án đối chiếu sau khi hoàn thành lượt kiểm tra)
- Difficulty levels: 2, 3, 4, 5 items based on preference.
- Multiple rounds (default: 5 rounds of puzzles).
- Large buttons, high contrast, non-punitive review (Criterion 3).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from features.music_care.cognitive_games.domain.models import DifficultyLevel, GameSession, GameStatus
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.services.game_engine import GameEngine

logger = logging.getLogger("SequenceRecallUI")


class SequenceRecallWidget(QWidget):
    def __init__(
        self,
        service: CognitiveService,
        on_back: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.on_back = on_back

        self.current_patient_id: str = "pat-001"
        self.current_session: Optional[GameSession] = None
        self.current_difficulty: str = "medium"  # Default medium: 3 items (harder & engaging)

        self.target_seq: List[Dict[str, str]] = []
        self.choices_pool: List[Dict[str, str]] = []
        self.user_selection: List[Dict[str, str]] = []

        self.review_count: int = 0
        self.current_round: int = 1
        self.total_rounds: int = 5  # 5 câu đố/vòng chơi
        self.correct_rounds: int = 0
        self.total_attempts: int = 0

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # 1. Header with Title, Difficulty Selector, and Control Buttons
        header_layout = QHBoxLayout()

        self.title_label = QLabel("🔢 Nhớ Trình Tự Hình Ảnh")
        self.title_label.setFont(QFont("Arial", 22, QFont.Bold))
        self.title_label.setStyleSheet("color: #0D47A1;")
        header_layout.addWidget(self.title_label)

        header_layout.addSpacing(16)

        # Difficulty selector directly on game screen
        lbl_diff = QLabel("Độ khó:")
        lbl_diff.setFont(QFont("Arial", 13, QFont.Bold))
        lbl_diff.setStyleSheet("color: #37474F;")
        header_layout.addWidget(lbl_diff)

        self.combo_diff = QComboBox()
        self.combo_diff.setFont(QFont("Arial", 13))
        self.combo_diff.setMinimumHeight(44)
        self.combo_diff.addItem("3 hình (Vừa sức)", "medium")
        self.combo_diff.addItem("4 hình (Thử thách)", "hard")
        self.combo_diff.addItem("5 hình (Nâng cao)", "expert")
        self.combo_diff.addItem("2 hình (Khởi động)", "easy")
        self.combo_diff.currentIndexChanged.connect(self._on_difficulty_changed)
        header_layout.addWidget(self.combo_diff)

        header_layout.addStretch()

        self.btn_review = QPushButton("👁 Xem Lại Trình Tự")
        self.btn_review.setMinimumHeight(48)
        self.btn_review.setFont(QFont("Arial", 13, QFont.Bold))
        self.btn_review.setStyleSheet(
            """
            QPushButton {
                background-color: #8E24AA;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 16px;
            }
            QPushButton:hover { background-color: #526E58; }
            """
        )
        self.btn_review.clicked.connect(self._on_review_clicked)
        header_layout.addWidget(self.btn_review)

        self.btn_pause = QPushButton("⏸ Tạm Dừng")
        self.btn_pause.setMinimumHeight(48)
        self.btn_pause.setFont(QFont("Arial", 13, QFont.Bold))
        self.btn_pause.setStyleSheet(
            """
            QPushButton {
                background-color: #356C60;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 16px;
            }
            QPushButton:hover { background-color: #28574D; }
            """
        )
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        header_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹ Nghỉ Chơi")
        self.btn_stop.setMinimumHeight(48)
        self.btn_stop.setFont(QFont("Arial", 13, QFont.Bold))
        self.btn_stop.setStyleSheet(
            """
            QPushButton {
                background-color: #D32F2F;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 16px;
            }
            QPushButton:hover { background-color: #C62828; }
            """
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        header_layout.addWidget(self.btn_stop)

        main_layout.addLayout(header_layout)

        # 2. Informational Instruction Banner
        self.status_banner = QLabel("Hãy ghi nhớ thứ tự các hình bên dưới, sau đó chọn lại theo đúng thứ tự đó!")
        self.status_banner.setFont(QFont("Arial", 15))
        self.status_banner.setWordWrap(True)
        self.status_banner.setStyleSheet(
            """
            background-color: #EDE7F6;
            color: #4A148C;
            padding: 10px 16px;
            border-radius: 8px;
            border: 1px solid #D1C4E9;
            """
        )
        main_layout.addWidget(self.status_banner)

        # 3. Main Center Stacked Widget (Page 0: Memorize, Page 1: Recall, Page 2: Reveal Feedback)
        self.stacked_phases = QStackedWidget()
        main_layout.addWidget(self.stacked_phases, 1)

        # Build the 3 distinct phase pages
        self._build_page_presentation()
        self._build_page_recall()
        self._build_page_feedback()

        # 4. Footer stats
        footer_layout = QHBoxLayout()
        self.stats_label = QLabel("Vòng chơi: 1/5 | Đã đúng: 0 | Số lần xem lại: 0")
        self.stats_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.stats_label.setStyleSheet("color: #37474F;")
        footer_layout.addWidget(self.stats_label)
        footer_layout.addStretch()
        main_layout.addLayout(footer_layout)

    # -------------------------------------------------------------------------
    # PAGE 0: MEMORIZATION PHASE (Ghi nhớ trình tự)
    # -------------------------------------------------------------------------
    def _build_page_presentation(self) -> None:
        self.page_pres = QWidget()
        layout = QVBoxLayout(self.page_pres)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignCenter)

        self.pres_header = QLabel("⭐ Hãy quan sát và ghi nhớ thứ tự từ TRÁI sang PHẢI:")
        self.pres_header.setFont(QFont("Arial", 17, QFont.Bold))
        self.pres_header.setStyleSheet("color: #1565C0;")
        self.pres_header.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.pres_header)

        # Target sequence cards container
        self.pres_cards_box = QHBoxLayout()
        self.pres_cards_box.setSpacing(20)
        self.pres_cards_box.setAlignment(Qt.AlignCenter)
        layout.addLayout(self.pres_cards_box)

        # Button to begin recall (Hides the answer)
        self.btn_begin_recall = QPushButton("👍 Bác Đã Nhớ Xong, Bắt Đầu Thử Thách! (Ẩn đáp án)")
        self.btn_begin_recall.setMinimumHeight(60)
        self.btn_begin_recall.setMaximumWidth(460)
        self.btn_begin_recall.setFont(QFont("Arial", 16, QFont.Bold))
        self.btn_begin_recall.setStyleSheet(
            """
            QPushButton {
                background-color: #35634D;
                color: #FFFFFF;
                border-radius: 12px;
                padding: 10px 24px;
            }
            QPushButton:hover { background-color: #214A3B; }
            """
        )
        self.btn_begin_recall.clicked.connect(self._switch_to_recall_phase)
        layout.addWidget(self.btn_begin_recall, alignment=Qt.AlignCenter)

        self.stacked_phases.addWidget(self.page_pres)

    # -------------------------------------------------------------------------
    # PAGE 1: RECALL PHASE (Trả lời - Ẩn hoàn toàn đáp án)
    # -------------------------------------------------------------------------
    def _build_page_recall(self) -> None:
        self.page_rec = QWidget()
        layout = QVBoxLayout(self.page_rec)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignCenter)

        self.rec_header = QLabel("Bác hãy chọn các hình theo đúng thứ tự vừa ghi nhớ:")
        self.rec_header.setFont(QFont("Arial", 16, QFont.Bold))
        self.rec_header.setStyleSheet("color: #263238;")
        self.rec_header.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.rec_header)

        # User's chosen slots container
        self.slots_box = QHBoxLayout()
        self.slots_box.setSpacing(16)
        self.slots_box.setAlignment(Qt.AlignCenter)
        layout.addLayout(self.slots_box)

        # Choices Pool
        lbl_choices = QLabel("Các hình trong kho lựa chọn:")
        lbl_choices.setFont(QFont("Arial", 14, QFont.Bold))
        lbl_choices.setStyleSheet("color: #455A64;")
        lbl_choices.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl_choices)

        self.choices_box = QHBoxLayout()
        self.choices_box.setSpacing(12)
        self.choices_box.setAlignment(Qt.AlignCenter)
        layout.addLayout(self.choices_box)

        # Action Buttons: Clear & Submit
        ctrl_box = QHBoxLayout()
        ctrl_box.setSpacing(18)
        ctrl_box.setAlignment(Qt.AlignCenter)

        btn_clear = QPushButton("↩ Xóa Chọn Lại")
        btn_clear.setMinimumHeight(48)
        btn_clear.setFont(QFont("Arial", 13, QFont.Bold))
        btn_clear.setStyleSheet("background-color: #78909C; color: #FFF; border-radius: 8px; padding: 6px 18px;")
        btn_clear.clicked.connect(self._clear_selection)
        ctrl_box.addWidget(btn_clear)

        btn_submit = QPushButton("✔ Kiểm Tra Kết Quả")
        btn_submit.setMinimumHeight(56)
        btn_submit.setMinimumWidth(240)
        btn_submit.setFont(QFont("Arial", 15, QFont.Bold))
        btn_submit.setStyleSheet(
            """
            QPushButton {
                background-color: #1565C0;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 10px 24px;
            }
            QPushButton:hover { background-color: #0D47A1; }
            """
        )
        btn_submit.clicked.connect(self._on_submit_clicked)
        ctrl_box.addWidget(btn_submit)

        layout.addLayout(ctrl_box)

        self.stacked_phases.addWidget(self.page_rec)

    # -------------------------------------------------------------------------
    # PAGE 2: REVEAL FEEDBACK PHASE (Hiện đáp án sau khi kiểm tra)
    # -------------------------------------------------------------------------
    def _build_page_feedback(self) -> None:
        self.page_feed = QWidget()
        layout = QVBoxLayout(self.page_feed)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignCenter)

        self.feedback_title = QLabel("Kết quả vòng chơi")
        self.feedback_title.setFont(QFont("Arial", 18, QFont.Bold))
        self.feedback_title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.feedback_title)

        # 1. Correct Answer Display (Hiện đáp án chính xác)
        ans_box = QFrame()
        ans_box.setStyleSheet("background-color: #E3F2FD; border: 2px solid #1E88E5; border-radius: 10px; padding: 10px;")
        ans_layout = QVBoxLayout(ans_box)
        ans_lbl = QLabel("🎯 ĐÁP ÁN CHÍNH XÁC:")
        ans_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        ans_lbl.setStyleSheet("color: #0D47A1;")
        ans_layout.addWidget(ans_lbl)

        self.feed_correct_box = QHBoxLayout()
        self.feed_correct_box.setSpacing(16)
        self.feed_correct_box.setAlignment(Qt.AlignCenter)
        ans_layout.addLayout(self.feed_correct_box)
        layout.addWidget(ans_box)

        # 2. User's Answer Display (Lựa chọn của bác)
        user_box = QFrame()
        user_box.setStyleSheet("background-color: #FAFAFA; border: 2px solid #B0BEC5; border-radius: 10px; padding: 10px;")
        user_layout = QVBoxLayout(user_box)
        user_lbl = QLabel("📝 LỰA CHỌN CỦA BÁC:")
        user_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        user_lbl.setStyleSheet("color: #37474F;")
        user_layout.addWidget(user_lbl)

        self.feed_user_box = QHBoxLayout()
        self.feed_user_box.setSpacing(16)
        self.feed_user_box.setAlignment(Qt.AlignCenter)
        user_layout.addLayout(self.feed_user_box)
        layout.addWidget(user_box)

        # 3. Action Buttons on Feedback Page
        feed_ctrl_box = QHBoxLayout()
        feed_ctrl_box.setSpacing(16)
        feed_ctrl_box.setAlignment(Qt.AlignCenter)

        self.btn_retry_round = QPushButton("🔄 Thử Lại Vòng Này")
        self.btn_retry_round.setMinimumHeight(52)
        self.btn_retry_round.setFont(QFont("Arial", 14, QFont.Bold))
        self.btn_retry_round.setStyleSheet("background-color: #FFA000; color: #FFF; border-radius: 8px; padding: 8px 18px;")
        self.btn_retry_round.clicked.connect(self._retry_current_round)
        feed_ctrl_box.addWidget(self.btn_retry_round)

        self.btn_next_round = QPushButton("Tiếp Tục Vòng Tiếp Theo ➡")
        self.btn_next_round.setMinimumHeight(52)
        self.btn_next_round.setFont(QFont("Arial", 14, QFont.Bold))
        self.btn_next_round.setStyleSheet("background-color: #35634D; color: #FFF; border-radius: 8px; padding: 8px 24px;")
        self.btn_next_round.clicked.connect(self._advance_to_next_round)
        feed_ctrl_box.addWidget(self.btn_next_round)

        layout.addLayout(feed_ctrl_box)

        self.stacked_phases.addWidget(self.page_feed)

    # -------------------------------------------------------------------------
    # GAME LIFECYCLE & ROUND MANAGEMENT
    # -------------------------------------------------------------------------
    def start_game(
        self,
        patient_id: str,
        difficulty: DifficultyLevel | str = "medium",
        seed: Optional[int] = None,
    ) -> None:
        """Starts sequence recall game."""
        if self.current_session and self.current_session.status == GameStatus.IN_PROGRESS.value:
            try:
                self.service.abandon_session(self.current_session.session_id, notes="Chuyển độ khó / Bắt đầu ván mới")
            except Exception:
                pass

        self.current_patient_id = patient_id
        self.current_difficulty = self.service.normalize_difficulty(difficulty)

        # Update combo box selection without re-triggering signal
        self.combo_diff.blockSignals(True)
        idx = self.combo_diff.findData(self.current_difficulty)
        if idx >= 0:
            self.combo_diff.setCurrentIndex(idx)
        self.combo_diff.blockSignals(False)

        self.current_round = 1
        self.total_rounds = 5  # 5 câu đố/vòng
        self.correct_rounds = 0
        self.total_attempts = 0
        self.review_count = 0

        self.current_session, self.target_seq, self.choices_pool = self.service.start_sequence_recall_session(
            patient_id=patient_id,
            difficulty=self.current_difficulty,
            seed=seed,
        )

        self._start_round()

    def _on_difficulty_changed(self) -> None:
        new_diff = self.combo_diff.currentData()
        if new_diff:
            p_id = getattr(self, "current_patient_id", "pat-001")
            self.start_game(patient_id=p_id, difficulty=new_diff)

    def _start_round(self) -> None:
        self.user_selection = []
        self._render_presentation_cards()
        self._update_stats_label()
        self.stacked_phases.setCurrentIndex(0)
        self.btn_review.setEnabled(False)
        self.status_banner.setText("Bác cứ nhìn thoải mái, khi nào cảm thấy đã nhớ thứ tự các hình thì bấm nút xanh bên dưới nhé.")

    def _clear_layout(self, layout) -> None:
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    sub_layout = item.layout()
                    if sub_layout is not None:
                        self._clear_layout(sub_layout)

    def _render_presentation_cards(self) -> None:
        self._clear_layout(self.pres_cards_box)
        self.pres_header.setText(f"⭐ VÒNG {self.current_round}/{self.total_rounds}: Hãy quan sát và ghi nhớ thứ tự từ TRÁI sang PHẢI:")

        for i, item in enumerate(self.target_seq):
            card = QWidget()
            card.setFixedSize(150, 160)
            card.setStyleSheet(
                """
                background-color: #E3F2FD;
                border: 3px solid #1E88E5;
                border-radius: 12px;
                """
            )
            c_layout = QVBoxLayout(card)
            step_lbl = QLabel(f"Bước {i + 1}")
            step_lbl.setFont(QFont("Arial", 12, QFont.Bold))
            step_lbl.setStyleSheet("color: #0D47A1;")
            step_lbl.setAlignment(Qt.AlignCenter)
            c_layout.addWidget(step_lbl)

            icon_lbl = QLabel(item["icon"])
            icon_lbl.setFont(QFont("Arial", 36))
            icon_lbl.setAlignment(Qt.AlignCenter)
            c_layout.addWidget(icon_lbl)

            name_lbl = QLabel(item["name"])
            name_lbl.setFont(QFont("Arial", 13, QFont.Bold))
            name_lbl.setStyleSheet("color: #263238;")
            name_lbl.setAlignment(Qt.AlignCenter)
            c_layout.addWidget(name_lbl)

            self.pres_cards_box.addWidget(card)

    def _switch_to_recall_phase(self) -> None:
        """Switches to recall phase where the target answer is COMPLETELY HIDDEN."""
        self.btn_review.setEnabled(True)
        self.status_banner.setText("Đáp án đã được ẩn. Bác hãy chọn các hình bên dưới theo đúng thứ tự vừa ghi nhớ!")
        self.rec_header.setText(f"VÒNG {self.current_round}/{self.total_rounds}: Bác hãy chọn các hình theo đúng thứ tự vừa ghi nhớ:")

        self._refresh_slots()
        self._render_choice_buttons()
        self.stacked_phases.setCurrentIndex(1)

    def _refresh_slots(self) -> None:
        self._clear_layout(self.slots_box)

        for i in range(len(self.target_seq)):
            slot = QWidget()
            slot.setFixedSize(140, 110)
            s_layout = QVBoxLayout(slot)

            if i < len(self.user_selection):
                sel = self.user_selection[i]
                slot.setStyleSheet("background-color: #EEF3E9; border: 3px solid #43A047; border-radius: 10px;")
                num_lbl = QLabel(f"Ô {i + 1}")
                num_lbl.setFont(QFont("Arial", 11, QFont.Bold))
                num_lbl.setStyleSheet("color: #214A3B;")
                num_lbl.setAlignment(Qt.AlignCenter)
                s_layout.addWidget(num_lbl)

                val_lbl = QLabel(f"{sel['icon']} {sel['name']}")
                val_lbl.setFont(QFont("Arial", 13, QFont.Bold))
                val_lbl.setStyleSheet("color: #214A3B;")
                val_lbl.setAlignment(Qt.AlignCenter)
                s_layout.addWidget(val_lbl)
            else:
                slot.setStyleSheet("background-color: #F5F5F5; border: 2px dashed #BDBDBD; border-radius: 10px;")
                empty_lbl = QLabel(f"Ô {i + 1}\n(Đang chờ chọn)")
                empty_lbl.setFont(QFont("Arial", 12))
                empty_lbl.setStyleSheet("color: #757575;")
                empty_lbl.setAlignment(Qt.AlignCenter)
                s_layout.addWidget(empty_lbl)

            self.slots_box.addWidget(slot)

    def _render_choice_buttons(self) -> None:
        self._clear_layout(self.choices_box)

        for item in self.choices_pool:
            btn = QPushButton(f"{item['icon']}\n{item['name']}")
            btn.setFixedSize(130, 105)
            btn.setFont(QFont("Arial", 13, QFont.Bold))
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #FAFAFA;
                    color: #212121;
                    border: 3px solid #78909C;
                    border-radius: 12px;
                }
                QPushButton:hover { background-color: #ECEFF1; border-color: #1E88E5; }
                QPushButton:focus { border: 4px solid #FF8F00; }
                """
            )
            btn.clicked.connect(lambda checked, it=item: self._on_item_selected(it))
            self.choices_box.addWidget(btn)

    def _on_item_selected(self, item: Dict[str, str]) -> None:
        if len(self.user_selection) < len(self.target_seq):
            self.user_selection.append(item)
            self._refresh_slots()

    def _clear_selection(self) -> None:
        self.user_selection.clear()
        self._refresh_slots()

    def _on_review_clicked(self) -> None:
        """Allows reviewing sequence without penalty (Criterion 3)."""
        self.review_count += 1
        self._update_stats_label()
        self.stacked_phases.setCurrentIndex(0)
        self.btn_review.setEnabled(False)
        self.status_banner.setText("Bác đang xem lại trình tự (không bị tính lỗi). Khi nào nhớ xong, bấm nút xanh để tiếp tục chọn.")

    def _on_submit_clicked(self) -> None:
        if len(self.user_selection) < len(self.target_seq):
            QMessageBox.information(
                self,
                "Chưa đủ số hình",
                f"Bác hãy chọn đủ {len(self.target_seq)} hình rồi hẵng kiểm tra nhé!",
            )
            return

        self.total_attempts += 1
        is_perfect, correct_pos, feedback_msg = GameEngine.grade_sequence(
            target_seq=self.target_seq,
            user_input_seq=self.user_selection,
        )

        if is_perfect:
            self.correct_rounds += 1

        self._update_stats_label()
        self._show_reveal_feedback_phase(is_perfect, correct_pos, feedback_msg)

    # -------------------------------------------------------------------------
    # REVEAL FEEDBACK: Show answers only AFTER submit
    # -------------------------------------------------------------------------
    def _show_reveal_feedback_phase(self, is_perfect: bool, correct_pos: int, feedback_msg: str) -> None:
        """Displays answer comparison screen only after player finishes the attempt."""
        if is_perfect:
            self.feedback_title.setText(f"🎉 CHÍNH XÁC VÒNG {self.current_round}/{self.total_rounds}!")
            self.feedback_title.setStyleSheet("color: #35634D;")
            self.status_banner.setText("Xuất sắc! Bác đã nhớ đúng 100% thứ tự các hình.")
            self.btn_retry_round.setVisible(False)
        else:
            self.feedback_title.setText(f"ĐÚNG {correct_pos}/{len(self.target_seq)} VỊ TRÍ - VÒNG {self.current_round}/{self.total_rounds}")
            self.feedback_title.setStyleSheet("color: #E65100;")
            self.status_banner.setText(f"{feedback_msg} Bác hãy quan sát đáp án đúng bên dưới để ghi nhớ nhé!")
            self.btn_retry_round.setVisible(True)

        # 1. Render Correct Answer
        self._clear_layout(self.feed_correct_box)
        for i, item in enumerate(self.target_seq):
            card = QWidget()
            card.setFixedSize(130, 95)
            card.setStyleSheet("background-color: #FFFFFF; border: 2px solid #1E88E5; border-radius: 8px;")
            l = QVBoxLayout(card)
            step_lbl = QLabel(f"Bước {i + 1}")
            step_lbl.setFont(QFont("Arial", 11, QFont.Bold))
            step_lbl.setStyleSheet("color: #0D47A1;")
            step_lbl.setAlignment(Qt.AlignCenter)
            l.addWidget(step_lbl)
            val_lbl = QLabel(f"{item['icon']} {item['name']}")
            val_lbl.setFont(QFont("Arial", 12, QFont.Bold))
            val_lbl.setStyleSheet("color: #0D47A1;")
            val_lbl.setAlignment(Qt.AlignCenter)
            l.addWidget(val_lbl)
            self.feed_correct_box.addWidget(card)

        # 2. Render User's Answer with Match Indicators
        self._clear_layout(self.feed_user_box)
        for i in range(len(self.target_seq)):
            card = QWidget()
            card.setFixedSize(130, 95)
            l = QVBoxLayout(card)

            user_item = self.user_selection[i] if i < len(self.user_selection) else None
            is_match = (user_item is not None and user_item["id"] == self.target_seq[i]["id"])

            if is_match:
                card.setStyleSheet("background-color: #EEF3E9; border: 2px solid #35634D; border-radius: 8px;")
                s_lbl = QLabel(f"Ô {i + 1} ✓ Đúng")
                s_lbl.setFont(QFont("Arial", 11, QFont.Bold))
                s_lbl.setStyleSheet("color: #214A3B;")
                s_lbl.setAlignment(Qt.AlignCenter)
                l.addWidget(s_lbl)
                v_lbl = QLabel(f"{user_item['icon']} {user_item['name']}")
                v_lbl.setFont(QFont("Arial", 12, QFont.Bold))
                v_lbl.setStyleSheet("color: #214A3B;")
                v_lbl.setAlignment(Qt.AlignCenter)
                l.addWidget(v_lbl)
            else:
                card.setStyleSheet("background-color: #FFEBEE; border: 2px solid #C62828; border-radius: 8px;")
                s_lbl = QLabel(f"Ô {i + 1} ✗ Chưa đúng")
                s_lbl.setFont(QFont("Arial", 11, QFont.Bold))
                s_lbl.setStyleSheet("color: #C62828;")
                s_lbl.setAlignment(Qt.AlignCenter)
                l.addWidget(s_lbl)
                u_text = f"{user_item['icon']} {user_item['name']}" if user_item else "Trống"
                v_lbl = QLabel(u_text)
                v_lbl.setFont(QFont("Arial", 12, QFont.Bold))
                v_lbl.setStyleSheet("color: #C62828;")
                v_lbl.setAlignment(Qt.AlignCenter)
                l.addWidget(v_lbl)

            self.feed_user_box.addWidget(card)

        # Update button text for last round
        if self.current_round >= self.total_rounds:
            self.btn_next_round.setText("Hoàn Thành Hoạt Động 🎉")
        else:
            self.btn_next_round.setText(f"Tiếp Tục Vòng {self.current_round + 1} ➡")

        self.stacked_phases.setCurrentIndex(2)

    def _retry_current_round(self) -> None:
        """Lets the player re-attempt the current round."""
        self.user_selection.clear()
        self._switch_to_recall_phase()

    def _advance_to_next_round(self) -> None:
        if self.current_round < self.total_rounds:
            self.current_round += 1
            if self.current_session:
                packs = self.service.get_content_packs(self.current_session.game_type_id)
                items_pool = packs[0].assets_manifest.get("items", []) if packs else []
                seed = self.current_session.seed + self.current_round * 31
                self.target_seq, self.choices_pool = GameEngine.generate_sequence_round(
                    items_pool=items_pool,
                    difficulty=self.current_difficulty,
                    seed=seed,
                )
            self._start_round()
        else:
            self._on_game_completed()

    def _on_pause_clicked(self) -> None:
        if not self.current_session:
            return

        self.service.pause_session(self.current_session.session_id)

        dlg = QDialog(self)
        dlg.setWindowTitle("Trò Chơi Đang Tạm Dừng")
        dlg.setMinimumSize(420, 240)
        vbox = QVBoxLayout(dlg)
        vbox.setContentsMargins(24, 24, 24, 24)
        vbox.setSpacing(18)

        lbl = QLabel("☕ Bác đang tạm nghỉ.\nThời gian nghỉ sẽ KHÔNG tính vào thời gian chơi của bác.")
        lbl.setFont(QFont("Arial", 16))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        vbox.addWidget(lbl)

        btn_resume = QPushButton("▶ Tiếp Tục Chơi")
        btn_resume.setMinimumHeight(60)
        btn_resume.setFont(QFont("Arial", 16, QFont.Bold))
        btn_resume.setStyleSheet("background-color: #35634D; color: #FFF; border-radius: 10px;")
        btn_resume.clicked.connect(dlg.accept)
        vbox.addWidget(btn_resume)

        dlg.exec_()
        self.service.resume_session(self.current_session.session_id)
        self.status_banner.setText("Chào mừng bác quay lại! Chúc bác chơi vui vẻ.")

    def _on_stop_clicked(self) -> None:
        if not self.current_session:
            if self.on_back:
                self.on_back()
            return

        reply = QMessageBox.question(
            self,
            "Nghỉ chơi",
            "Bác có muốn nghỉ chơi lúc này không?\n\nHoạt động sẽ được lưu lại để ghi nhận sự tham gia của bác mà KHÔNG bị trừ điểm phạt.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.service.abandon_session(self.current_session.session_id)
            self.current_session = None
            if self.on_back:
                self.on_back()

    def _on_game_completed(self) -> None:
        if not self.current_session:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Hoàn Thành Hoạt Động")
        dlg.setMinimumSize(500, 380)
        vbox = QVBoxLayout(dlg)
        vbox.setContentsMargins(24, 24, 24, 24)
        vbox.setSpacing(16)

        title = QLabel("🎉 Chúc Mừng Bác Đã Hoàn Thành!")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        title.setStyleSheet("color: #35634D;")
        title.setAlignment(Qt.AlignCenter)
        vbox.addWidget(title)

        motivational = QLabel(
            f"“Luyện tập và giải trí phù hợp với bạn.”\n"
            f"Bác đã hoàn thành trọn vẹn cả {self.total_rounds} vòng câu đố nhớ trình tự!\n\n"
            f"⭐ Số vòng đúng chính xác: {self.correct_rounds}/{self.total_rounds}\n"
            f"👁 Số lần xem lại gợi ý: {self.review_count}"
        )
        motivational.setFont(QFont("Arial", 15))
        motivational.setAlignment(Qt.AlignCenter)
        motivational.setWordWrap(True)
        vbox.addWidget(motivational)

        fb_label = QLabel("Bác thấy độ khó các câu đố hôm nay thế nào?")
        fb_label.setFont(QFont("Arial", 14, QFont.Bold))
        vbox.addWidget(fb_label)

        fb_group = QButtonGroup(dlg)
        rb_easy = QRadioButton("Dễ nhớ, vừa sức")
        rb_medium = QRadioButton("Vừa phải, thú vị và hào hứng")
        rb_hard = QRadioButton("Khá thử thách")
        rb_medium.setChecked(True)
        for i, rb in enumerate([rb_easy, rb_medium, rb_hard]):
            rb.setFont(QFont("Arial", 13))
            fb_group.addButton(rb, i)
            vbox.addWidget(rb)

        btn_box = QHBoxLayout()
        btn_replay = QPushButton("🔄 Chơi Ván Mới")
        btn_replay.setMinimumHeight(54)
        btn_replay.setFont(QFont("Arial", 14, QFont.Bold))
        btn_replay.setStyleSheet("background-color: #1976D2; color: #FFF; border-radius: 8px;")

        btn_home = QPushButton("🏠 Quay Về Màn Hình Chính")
        btn_home.setMinimumHeight(54)
        btn_home.setFont(QFont("Arial", 14, QFont.Bold))
        btn_home.setStyleSheet("background-color: #388E3C; color: #FFF; border-radius: 8px;")

        btn_box.addWidget(btn_replay)
        btn_box.addWidget(btn_home)
        vbox.addLayout(btn_box)

        def save_and_close(replaying: bool) -> None:
            selected_fb = "fair"
            if rb_easy.isChecked():
                selected_fb = "easy"
            elif rb_hard.isChecked():
                selected_fb = "hard"

            if self.current_session:
                self.service.finish_sequence_recall(
                    session_id=self.current_session.session_id,
                    attempts=self.total_attempts,
                    correct_rounds=self.correct_rounds,
                    total_rounds=self.total_rounds,
                    user_feedback=selected_fb,
                    enjoyment="enjoyed",
                    metrics={"reviews_count": self.review_count},
                )
            dlg.accept()
            if replaying:
                self.start_game(patient_id=self.current_patient_id, difficulty=self.current_difficulty)
            else:
                if self.on_back:
                    self.on_back()

        btn_replay.clicked.connect(lambda: save_and_close(True))
        btn_home.clicked.connect(lambda: save_and_close(False))

        dlg.exec_()

    def _update_stats_label(self) -> None:
        self.stats_label.setText(
            f"Vòng chơi: {self.current_round}/{self.total_rounds} | Đã đúng: {self.correct_rounds} | Số lần xem lại: {self.review_count}"
        )
