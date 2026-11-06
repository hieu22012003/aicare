"""
Card Matching Widget (Lật hình ghép cặp) for Elderly Cognitive Exercises.
Adheres to Accessibility Principles:
- Large fonts, big touch/click targets (min-height 90px cards).
- Clear contrast, distinct icons & Vietnamese labels.
- Keyboard accessible (Tab, Space, Enter).
- Explicit Pause / Resume / Hint / Stop buttons without trapping the user.
- Non-punitive hint usage and abandoned sessions without 0-penalty (Criteria 1, 2, 5).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
) 
from features.music_care.cognitive_games.domain.models import CardItem, DifficultyLevel, GameSession, GameStatus
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService

logger = logging.getLogger("CardMatchingUI")


class CardButton(QPushButton):
    """Large, accessible card button with keyboard focus and high contrast."""

    def __init__(self, card_item: CardItem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.card_item = card_item
        self.setMinimumSize(120, 120)
        self.setFocusPolicy(Qt.StrongFocus)
        self.update_appearance()

    def update_appearance(self) -> None:
        font = QFont("Arial", 16, QFont.Bold)
        self.setFont(font)
        if self.card_item.is_matched:
            self.setText(f"{self.card_item.icon_symbol}\n{self.card_item.name}\n✓")
            self.setStyleSheet(
                """
                QPushButton {
                    background-color: #EEF3E9;
                    color: #214A3B;
                    border: 3px solid #4CAF50;
                    border-radius: 12px;
                    font-size: 16pt;
                    font-weight: bold;
                }
                """
            )
            self.setEnabled(False)
        elif self.card_item.is_flipped:
            self.setText(f"{self.card_item.icon_symbol}\n{self.card_item.name}")
            self.setStyleSheet(
                """
                QPushButton {
                    background-color: #FFF9C4;
                    color: #E65100;
                    border: 3px solid #FFB300;
                    border-radius: 12px;
                    font-size: 16pt;
                    font-weight: bold;
                }
                """
            )
        else:
            self.setText("❓\nLật ô")
            self.setStyleSheet(
                """
                QPushButton {
                    background-color: #1976D2;
                    color: #FFFFFF;
                    border: 3px solid #0D47A1;
                    border-radius: 12px;
                    font-size: 16pt;
                    font-weight: bold;
                }
                QPushButton:focus {
                    border: 4px solid #FFD600;
                    background-color: #1E88E5;
                }
                QPushButton:hover {
                    background-color: #2196F3;
                }
                """
            )


class CardMatchingWidget(QWidget):
    def __init__(
        self,
        service: CognitiveService,
        on_back: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.on_back = on_back

        self.current_session: Optional[GameSession] = None
        self.board: List[CardItem] = []
        self.card_buttons: List[CardButton] = []
        self.flipped_indices: List[int] = []
        self.attempts: int = 0
        self.matched_pairs_count: int = 0
        self.total_pairs_count: int = 0
        self.is_processing_turn: bool = False

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # 1. Header with instructions and action buttons
        header_layout = QHBoxLayout()

        self.title_label = QLabel("🧩 Lật Hình Ghép Cặp")
        title_font = QFont("Arial", 22, QFont.Bold)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #0D47A1;")
        header_layout.addWidget(self.title_label)

        header_layout.addSpacing(16)

        lbl_diff = QLabel("Độ khó:")
        lbl_diff.setFont(QFont("Arial", 13, QFont.Bold))
        lbl_diff.setStyleSheet("color: #37474F;")
        header_layout.addWidget(lbl_diff)

        self.combo_diff = QComboBox()
        self.combo_diff.setFont(QFont("Arial", 13))
        self.combo_diff.setMinimumHeight(44)
        self.combo_diff.addItem("4 cặp (8 thẻ - Vừa)", "medium")
        self.combo_diff.addItem("6 cặp (12 thẻ - Khá)", "hard")
        self.combo_diff.addItem("8 cặp (16 thẻ - Thử thách)", "expert")
        self.combo_diff.addItem("3 cặp (6 thẻ - Khởi động)", "easy")
        self.combo_diff.currentIndexChanged.connect(self._on_difficulty_changed)
        header_layout.addWidget(self.combo_diff)

        header_layout.addStretch()

        self.btn_hint = QPushButton("💡 Gợi Ý")
        self.btn_hint.setMinimumHeight(52)
        self.btn_hint.setMinimumWidth(120)
        self.btn_hint.setFont(QFont("Arial", 14, QFont.Bold))
        self.btn_hint.setStyleSheet(
            """
            QPushButton {
                background-color: #FFA000;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #FF8F00; }
            QPushButton:focus { border: 3px solid #000; }
            """
        )
        self.btn_hint.clicked.connect(self._on_hint_clicked)
        header_layout.addWidget(self.btn_hint)

        self.btn_pause = QPushButton("⏸ Tạm Dừng")
        self.btn_pause.setMinimumHeight(52)
        self.btn_pause.setMinimumWidth(140)
        self.btn_pause.setFont(QFont("Arial", 14, QFont.Bold))
        self.btn_pause.setStyleSheet(
            """
            QPushButton {
                background-color: #356C60;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #28574D; }
            QPushButton:focus { border: 3px solid #000; }
            """
        )
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        header_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹ Nghỉ Chơi")
        self.btn_stop.setMinimumHeight(52)
        self.btn_stop.setMinimumWidth(130)
        self.btn_stop.setFont(QFont("Arial", 14, QFont.Bold))
        self.btn_stop.setStyleSheet(
            """
            QPushButton {
                background-color: #D32F2F;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 8px 16px;
            }
            QPushButton:hover { background-color: #C62828; }
            QPushButton:focus { border: 3px solid #000; }
            """
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        header_layout.addWidget(self.btn_stop)

        main_layout.addLayout(header_layout)

        # 2. Informational Banner (Criterion 1: Large clear status and help)
        self.status_banner = QLabel("Hãy chọn 2 ô để tìm cặp hình giống nhau. Chơi thoải mái không tính thời gian ép buộc!")
        self.status_banner.setFont(QFont("Arial", 15))
        self.status_banner.setWordWrap(True)
        self.status_banner.setStyleSheet(
            """
            background-color: #E3F2FD;
            color: #0D47A1;
            padding: 12px 18px;
            border-radius: 8px;
            border: 1px solid #90CAF9;
            """
        )
        main_layout.addWidget(self.status_banner)

        # 3. Card Grid Container
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(16)
        main_layout.addWidget(self.grid_container, 1)

        # 4. Footer with stats
        footer_layout = QHBoxLayout()
        self.stats_label = QLabel("Số cặp đã tìm: 0 | Lượt thử: 0 | Gợi ý: 0")
        self.stats_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.stats_label.setStyleSheet("color: #37474F;")
        footer_layout.addWidget(self.stats_label)

        footer_layout.addStretch()

        main_layout.addLayout(footer_layout)

    def start_game(
        self,
        patient_id: str,
        difficulty: DifficultyLevel | str = "medium",
        seed: Optional[int] = None,
    ) -> None:
        """Starts a game session and initializes card buttons."""
        if self.current_session and self.current_session.status == GameStatus.IN_PROGRESS.value:
            try:
                self.service.abandon_session(self.current_session.session_id, notes="Chuyển độ khó / Bắt đầu ván mới")
            except Exception:
                pass

        self.current_patient_id = patient_id
        diff_str = self.service.normalize_difficulty(difficulty)
        self.current_difficulty = diff_str

        self.combo_diff.blockSignals(True)
        idx = self.combo_diff.findData(diff_str)
        if idx >= 0:
            self.combo_diff.setCurrentIndex(idx)
        self.combo_diff.blockSignals(False)

        self.current_session, self.board = self.service.start_card_matching_session(
            patient_id=patient_id,
            difficulty=diff_str,
            seed=seed,
        )

        self.attempts = 0
        self.matched_pairs_count = 0
        self.total_pairs_count = len(self.board) // 2
        self.flipped_indices = []
        self.is_processing_turn = False

        self._render_board()
        self._update_stats_label()
        self.status_banner.setText("Hãy bấm vào một ô bất kỳ để bắt đầu lật hình. Chúc bác có giờ phút vui vẻ!")

    def _on_difficulty_changed(self) -> None:
        new_diff = self.combo_diff.currentData()
        if new_diff:
            p_id = getattr(self, "current_patient_id", "pat-001")
            self.start_game(patient_id=p_id, difficulty=new_diff)

    def _render_board(self) -> None:
        # Clear existing buttons
        for btn in self.card_buttons:
            self.grid_layout.removeWidget(btn)
            btn.deleteLater()
        self.card_buttons.clear()

        # Determine grid cols (easy: 3 cols x 2 rows = 6; medium: 4 cols x 2 rows = 8; hard: 4 cols x 3 rows = 12)
        total_cards = len(self.board)
        cols = 3 if total_cards <= 6 else 4

        for idx, item in enumerate(self.board):
            btn = CardButton(item)
            btn.clicked.connect(lambda checked, i=idx: self._on_card_clicked(i))
            row = idx // cols
            col = idx % cols
            self.grid_layout.addWidget(btn, row, col)
            self.card_buttons.append(btn)

    def _on_card_clicked(self, index: int) -> None:
        if self.is_processing_turn:
            return

        card = self.board[index]
        if card.is_matched or card.is_flipped:
            return

        # Flip card
        card.is_flipped = True
        self.card_buttons[index].update_appearance()
        self.flipped_indices.append(index)

        if len(self.flipped_indices) == 2:
            self._handle_pair_flipped()

    def _handle_pair_flipped(self) -> None:
        self.is_processing_turn = True
        self.attempts += 1
        idx1, idx2 = self.flipped_indices
        card1 = self.board[idx1]
        card2 = self.board[idx2]

        if card1.item_id == card2.item_id:
            # Match!
            card1.is_matched = True
            card2.is_matched = True
            self.matched_pairs_count += 1
            self.status_banner.setText(f"🎉 Giỏi quá! Bác đã tìm đúng cặp '{card1.name}'.")
            self.card_buttons[idx1].update_appearance()
            self.card_buttons[idx2].update_appearance()
            self.flipped_indices.clear()
            self.is_processing_turn = False
            self._update_stats_label()

            if self.matched_pairs_count == self.total_pairs_count:
                QTimer.singleShot(600, self._on_game_completed)
        else:
            # No match: give elderly user enough time to see both cards before flipping back (1.4 seconds)
            self.status_banner.setText("Hai hình chưa giống nhau. Bác hãy ghi nhớ vị trí rồi thử tiếp nhé!")
            self._update_stats_label()

            def flip_back() -> None:
                card1.is_flipped = False
                card2.is_flipped = False
                self.card_buttons[idx1].update_appearance()
                self.card_buttons[idx2].update_appearance()
                self.flipped_indices.clear()
                self.is_processing_turn = False

            QTimer.singleShot(1400, flip_back)

    def _on_hint_clicked(self) -> None:
        """Reveals one un-matched pair temporarily without penalizing score (Criterion 1, 2)."""
        if self.is_processing_turn or not self.current_session:
            return

        # Find first unmatched pair
        unmatched_items: dict[str, list[int]] = {}
        for idx, card in enumerate(self.board):
            if not card.is_matched:
                unmatched_items.setdefault(card.item_id, []).append(idx)

        pair_indices = None
        for item_id, indices in unmatched_items.items():
            if len(indices) == 2:
                pair_indices = indices
                break

        if not pair_indices:
            return

        self.service.record_hint_used(self.current_session.session_id)
        self._update_stats_label()
        self.status_banner.setText("💡 Hệ thống gợi ý vị trí cặp hình cho bác trong giây lát...")

        i1, i2 = pair_indices
        self.board[i1].is_flipped = True
        self.board[i2].is_flipped = True
        self.card_buttons[i1].update_appearance()
        self.card_buttons[i2].update_appearance()
        self.is_processing_turn = True

        def reset_hint() -> None:
            self.board[i1].is_flipped = False
            self.board[i2].is_flipped = False
            self.card_buttons[i1].update_appearance()
            self.card_buttons[i2].update_appearance()
            self.is_processing_turn = False
            self.status_banner.setText("Bác hãy tiếp tục ghép các cặp hình nhé!")

        QTimer.singleShot(1800, reset_hint)

    def _on_pause_clicked(self) -> None:
        if not self.current_session:
            return

        self.service.pause_session(self.current_session.session_id)

        # Accessible Pause Modal
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
        """Gentle confirmation without penalty. Saves as abandoned (Criterion 5)."""
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
        """Celebration screen and optional feedback submission."""
        if not self.current_session:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Hoàn Thành Hoạt Động")
        dlg.setMinimumSize(480, 360)
        vbox = QVBoxLayout(dlg)
        vbox.setContentsMargins(24, 24, 24, 24)
        vbox.setSpacing(16)

        title = QLabel("🎉 Chúc Mừng Bác Đã Hoàn Thành!")
        title.setFont(QFont("Arial", 20, QFont.Bold))
        title.setStyleSheet("color: #35634D;")
        title.setAlignment(Qt.AlignCenter)
        vbox.addWidget(title)

        motivational = QLabel("“Luyện tập và giải trí phù hợp với bạn.”\nBác đã hoàn thành xuất sắc tất cả các cặp hình!")
        motivational.setFont(QFont("Arial", 15))
        motivational.setAlignment(Qt.AlignCenter)
        motivational.setWordWrap(True)
        vbox.addWidget(motivational)

        # Subjective feedback (Optional, non-diagnostic, Section 4)
        fb_label = QLabel("Bác thấy ván chơi hôm nay thế nào?")
        fb_label.setFont(QFont("Arial", 14, QFont.Bold))
        vbox.addWidget(fb_label)

        fb_group = QButtonGroup(dlg)
        rb_easy = QRadioButton("Dễ dàng, vừa sức")
        rb_medium = QRadioButton("Vừa phải, thú vị")
        rb_hard = QRadioButton("Hơi khó một chút")
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
            selected_fb = "vừa phải"
            if rb_easy.isChecked():
                selected_fb = "dễ"
            elif rb_hard.isChecked():
                selected_fb = "hơi khó"

            if self.current_session:
                self.service.finish_card_matching(
                    session_id=self.current_session.session_id,
                    attempts=self.attempts,
                    correct_pairs=self.matched_pairs_count,
                    total_pairs=self.total_pairs_count,
                    user_feedback=selected_fb,
                    enjoyment="thích",
                )
            dlg.accept()
            if replaying:
                patient_id = self.current_session.patient_id if self.current_session else "pat-001"
                self.start_game(patient_id=patient_id, difficulty=self.current_session.difficulty if self.current_session else DifficultyLevel.EASY)
            else:
                if self.on_back:
                    self.on_back()

        btn_replay.clicked.connect(lambda: save_and_close(True))
        btn_home.clicked.connect(lambda: save_and_close(False))

        dlg.exec_()

    def _update_stats_label(self) -> None:
        hints = 0
        if self.current_session:
            sess = self.service.game_repo.get_session(self.current_session.session_id)
            hints = sess.help_count if sess else 0

        self.stats_label.setText(
            f"Số cặp đã tìm: {self.matched_pairs_count}/{self.total_pairs_count} | Lượt thử: {self.attempts} | Gợi ý: {hints}"
        )
