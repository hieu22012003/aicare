"""
Cognitive Memory Games & Relaxation Widget for Senior Dashboard.
Provides:
1. Card Matching (Lật hình ghép cặp) - spatial memory & focus.
2. Sequence Recall (Nhớ trình tự) - short-term sequential memory.
3. Relaxing Music Player (Âm nhạc thư giãn) - soothing gentle music for leisure and relaxation.
4. Historical Progress & Encouragement Dashboard.
Designed specifically for seniors: high contrast, large buttons, non-trapping back navigation.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor, QPalette
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from shared.config import DATA_DIR
from features.music_care.cognitive_games.adapters.care_team_repository import SqlCareTeamRepository
from features.music_care.cognitive_games.adapters.game_repository import SqlGameRepository
from features.music_care.cognitive_games.adapters.identity_adapter import MockPatientIdentityAdapter
from features.music_care.cognitive_games.adapters.music_repository import SqlMusicRepository
from features.music_care.cognitive_games.database.connection import DatabaseManager
from features.music_care.cognitive_games.database.seed_data import seed_catalogs_only, seed_cognitive_games_database
from features.music_care.cognitive_games.services.audio_service import AudioService
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService
from features.music_care.cognitive_games.ui.card_matching_widget import CardMatchingWidget
from features.music_care.cognitive_games.ui.music_player_widget import MusicPlayerWidget
from features.music_care.cognitive_games.ui.progress_dashboard_widget import ProgressDashboardWidget
from features.music_care.cognitive_games.ui.sequence_recall_widget import SequenceRecallWidget

logger = logging.getLogger("CognitiveGamesWidget")


class CognitiveGamesWidget(QWidget):
    """Integrated widget for cognitive memory training and leisure relaxation."""

    def __init__(self, patient_id: str = "patient_001", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.patient_id = patient_id

        # Initialize SQLite database for cognitive games
        db_path = DATA_DIR / "cognitive_games.sqlite3"
        self.db = DatabaseManager(str(db_path))
        seed_catalogs_only(self.db)
        self._ensure_patient_profile(self.patient_id)

        # Repositories & Services
        self.care_team_repo = SqlCareTeamRepository(self.db)
        self.game_repo = SqlGameRepository(self.db)
        from shared.database.connection import UnifiedDatabaseManager
        family_db = UnifiedDatabaseManager()
        self.music_repo = SqlMusicRepository(
            self.db, family_db=family_db if family_db.table_exists('family_music') else None,
            patient_id=self.patient_id,
        )
        self.identity_adapter = MockPatientIdentityAdapter(
            initial_patient_id=self._map_patient_id(self.patient_id),
            camera_active=True,
        )

        self.cognitive_service = CognitiveService(
            game_repo=self.game_repo,
            care_team_repo=self.care_team_repo,
            identity_adapter=self.identity_adapter,
        )
        self.audio_service = AudioService(self.music_repo)

        self._init_ui()
        # Explicit popup colors avoid inheriting the Windows dark palette.
        for combo in self.findChildren(QComboBox):
            combo.setStyleSheet('QComboBox {' + combo.styleSheet() +
                               ' background-color: #FFFFFF; color: #000000; }')
            palette = combo.palette()
            for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                for role in (QPalette.Base, QPalette.Window):
                    palette.setColor(group, role, QColor('#FFFFFF'))
                for role in (QPalette.Text, QPalette.WindowText, QPalette.HighlightedText):
                    palette.setColor(group, role, QColor('#000000'))
                palette.setColor(group, QPalette.Highlight, QColor('#E8F0FE'))
            combo.setPalette(palette)
            combo.view().setPalette(palette)
            combo.view().setStyleSheet(
                'QListView { background-color: #FFFFFF; color: #000000; '
                'selection-background-color: #E8F0FE; selection-color: #000000; '
                'border: 1px solid #CBD5E1; outline: none; } '
                'QListView::item { background-color: #FFFFFF; color: #000000; } '
                'QListView::item:selected, QListView::item:hover { background-color: #E8F0FE; color: #000000; }'
            )
            combo.view().ensurePolished()
            # Windows can size the popup using the old system font metrics.
            # Keep short lists tall enough to display their final item in full.
            visible_rows = min(combo.count(), combo.maxVisibleItems(), 8)
            combo.view().setMinimumHeight(
                sum(combo.view().sizeHintForRow(i) for i in range(visible_rows)) + 2
            )

    def _map_patient_id(self, pid: str) -> str:
        """Map canonical patient_id to cognitive games tenant id."""
        clean_pid = str(pid).strip()
        if clean_pid in ("1", "patient_001", "pat-001"):
            return "pat-001"
        if clean_pid in ("2", "patient_002", "pat-002"):
            return "pat-002"
        return clean_pid

    def _ensure_patient_profile(self, patient_id: str) -> None:
        """Ensure patient exists in cognitive_games.sqlite3 so foreign keys never fail."""
        mapped_pid = self._map_patient_id(patient_id)
        with self.db.connect() as conn:
            p = conn.cursor().execute("SELECT patient_id FROM patients WHERE patient_id = ?", (mapped_pid,)).fetchone()
            if not p:
                full_name = f"Người dùng {mapped_pid}"
                try:
                    from shared.database.connection import UnifiedDatabaseManager
                    with UnifiedDatabaseManager().connect() as uconn:
                        up = uconn.fetchone("SELECT full_name FROM patients WHERE patient_id = ?", (patient_id,))
                        if up and up.get("full_name"):
                            full_name = up["full_name"]
                except Exception:
                    pass

                now_iso = datetime.now(timezone.utc).isoformat()
                user_id = f"usr_{mapped_pid}"
                conn.cursor().execute(
                    "INSERT OR IGNORE INTO users (user_id, email, phone, full_name, is_active, created_at) VALUES (?, ?, '', ?, 1, ?)",
                    (user_id, f"{mapped_pid}@local", full_name, now_iso),
                )
                conn.cursor().execute(
                    "INSERT OR IGNORE INTO patients (patient_id, user_id, full_name, date_of_birth, gender, timezone, status, notes) VALUES (?, ?, ?, '1950-01-01', 'Nam', 'Asia/Ho_Chi_Minh', 'ACTIVE', '')",
                    (mapped_pid, user_id, full_name),
                )

    def set_patient(self, new_patient_id: str) -> None:
        """Switch active patient profile."""
        if getattr(self, "patient_id", None) == new_patient_id:
            return
        self.patient_id = new_patient_id
        mapped_pid = self._map_patient_id(new_patient_id)
        self._ensure_patient_profile(new_patient_id)
        self.identity_adapter.set_simulated_patient(mapped_pid)
        self.music_repo.set_patient_id(new_patient_id)
        if hasattr(self, "music_widget"):
            self.music_widget.set_patient_id(mapped_pid)
        if hasattr(self, "dashboard_widget"):
            self.dashboard_widget.set_actor(mapped_pid, "patient", mapped_pid)
        if hasattr(self, "welcome_label"):
            pat_name = f"Người dùng {mapped_pid}"
            try:
                from shared.database.connection import UnifiedDatabaseManager
                with UnifiedDatabaseManager().connect() as uconn:
                    up = uconn.fetchone("SELECT full_name FROM patients WHERE patient_id = ?", (new_patient_id,))
                    if up and up.get("full_name"):
                        pat_name = up["full_name"]
            except Exception:
                pass
            self.welcome_label.setText(f"Chào mừng {pat_name} đến với Góc Trí Nhớ và Thư Giãn!")

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Stacked widget: Index 0 = Hub/Menu, Index 1 = Card Matching, Index 2 = Sequence Recall
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)

        # --- VIEW 0: HUB / MENU ---
        hub_widget = self._build_hub_view()
        self.stack.addWidget(hub_widget)

        # --- VIEW 1: CARD MATCHING GAME ---
        self.card_widget = CardMatchingWidget(self.cognitive_service, on_back=self._return_to_hub)
        self.stack.addWidget(self.card_widget)

        # --- VIEW 2: SEQUENCE RECALL GAME ---
        self.sequence_widget = SequenceRecallWidget(self.cognitive_service, on_back=self._return_to_hub)
        self.stack.addWidget(self.sequence_widget)

    def _build_hub_view(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        # 1. Header Banner
        header_card = QFrame()
        header_card.setStyleSheet("background-color: #E8F0FE; border: 1.5px solid #C7D8F9; border-radius: 12px; padding: 14px;")
        h_layout = QVBoxLayout(header_card)

        self.welcome_label = QLabel("🧠 GÓC TRÍ NHỚ VÀ THƯ GIÃN DÀNH CHO NGƯỜI CAO TUỔI")
        self.welcome_label.setStyleSheet("font-size: 22px; font-weight: 800; color: #1565C0; border: none;")
        h_layout.addWidget(self.welcome_label)

        sub_lbl = QLabel("“Rèn luyện nhẹ nhàng khi rảnh rỗi — Thư thái tinh thần, không tạo áp lực mỗi ngày.”")
        sub_lbl.setStyleSheet("font-size: 15px; color: #37474F; font-style: italic; border: none;")
        h_layout.addWidget(sub_lbl)
        layout.addWidget(header_card)

        # 2. Main Games Row
        games_row = QHBoxLayout()
        games_row.setSpacing(16)

        # Game Card 1: Card Matching (Lật hình ghép cặp)
        card_box = QGroupBox("🧩 Lật Hình Ghép Cặp (Card Matching)")
        card_box.setStyleSheet(
            "QGroupBox { font-size: 17px; font-weight: 700; color: #1565C0; background-color: #FFFFFF; "
            "border: 1.5px solid #DFE5DF; border-radius: 12px; margin-top: 12px; padding: 16px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; }"
        )
        c_layout = QVBoxLayout(card_box)
        c_layout.setSpacing(12)

        c_desc = QLabel("Tìm các cặp hình đồ vật quen thuộc (ấm trà, hoa sen, đồng hồ...). Rèn luyện trí nhớ không gian và sự tập trung nhẹ nhàng.")
        c_desc.setStyleSheet("font-size: 15px; color: #455A64; line-height: 1.4;")
        c_desc.setWordWrap(True)
        c_layout.addWidget(c_desc)

        diff_row_c = QHBoxLayout()
        lbl_c_diff = QLabel("Mức độ:")
        lbl_c_diff.setStyleSheet("font-size: 15px; font-weight: 700; color: #E65100;")
        diff_row_c.addWidget(lbl_c_diff)

        self.combo_card_diff = QComboBox()
        self.combo_card_diff.setStyleSheet("font-size: 15px; padding: 6px 12px; min-height: 40px; border-radius: 8px;")
        self.combo_card_diff.addItem("3 cặp (6 thẻ - Khởi động)", "easy")
        self.combo_card_diff.addItem("4 cặp (8 thẻ - Vừa sức)", "medium")
        self.combo_card_diff.addItem("6 cặp (12 thẻ - Khá)", "hard")
        diff_row_c.addWidget(self.combo_card_diff)
        c_layout.addLayout(diff_row_c)

        btn_start_cards = QPushButton("▶ BẮT ĐẦU LẬT HÌNH")
        btn_start_cards.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: #FFFFFF; font-size: 16px; font-weight: 800; "
            "border-radius: 10px; padding: 14px; min-height: 52px; border: none; } "
            "QPushButton:hover { background-color: #1565C0; }"
        )
        btn_start_cards.clicked.connect(self._launch_card_matching)
        c_layout.addWidget(btn_start_cards)
        games_row.addWidget(card_box)

        # Game Card 2: Sequence Recall (Nhớ trình tự)
        seq_box = QGroupBox("🔢 Nhớ Trình Tự (Sequence Recall)")
        seq_box.setStyleSheet(
            "QGroupBox { font-size: 17px; font-weight: 700; color: #435D49; background-color: #FFFFFF; "
            "border: 1.5px solid #DFE5DF; border-radius: 12px; margin-top: 12px; padding: 16px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; }"
        )
        s_layout = QVBoxLayout(seq_box)
        s_layout.setSpacing(12)

        s_desc = QLabel("Quan sát thứ tự các hình xuất hiện và chọn lại đúng thứ tự đó. Giúp kích thích sự nhanh nhạy và trí nhớ ngắn hạn.")
        s_desc.setStyleSheet("font-size: 15px; color: #455A64; line-height: 1.4;")
        s_desc.setWordWrap(True)
        s_layout.addWidget(s_desc)

        diff_row_s = QHBoxLayout()
        lbl_s_diff = QLabel("Mức độ:")
        lbl_s_diff.setStyleSheet("font-size: 15px; font-weight: 700; color: #526E58;")
        diff_row_s.addWidget(lbl_s_diff)

        self.combo_seq_diff = QComboBox()
        self.combo_seq_diff.setStyleSheet("font-size: 15px; padding: 6px 12px; min-height: 40px; border-radius: 8px;")
        self.combo_seq_diff.addItem("2 hình (Khởi động)", "easy")
        self.combo_seq_diff.addItem("3 hình (Vừa sức)", "medium")
        self.combo_seq_diff.addItem("4 hình (Thử thách)", "hard")
        diff_row_s.addWidget(self.combo_seq_diff)
        s_layout.addLayout(diff_row_s)

        btn_start_seq = QPushButton("▶ BẮT ĐẦU NHỚ TRÌNH TỰ")
        btn_start_seq.setStyleSheet(
            "QPushButton { background-color: #526E58; color: #FFFFFF; font-size: 16px; font-weight: 800; "
            "border-radius: 10px; padding: 14px; min-height: 52px; border: none; } "
            "QPushButton:hover { background-color: #435D49; }"
        )
        btn_start_seq.clicked.connect(self._launch_sequence_recall)
        s_layout.addWidget(btn_start_seq)
        games_row.addWidget(seq_box)

        layout.addLayout(games_row)

        # 3. Relaxing Music Embedded Card
        self.music_widget = MusicPlayerWidget(self.audio_service)
        self.music_widget.set_patient_id(self._map_patient_id(self.patient_id))
        layout.addWidget(self.music_widget)

        # 4. Progress & History Tab Box
        self.dashboard_widget = ProgressDashboardWidget(self.cognitive_service)
        mapped_pid = self._map_patient_id(self.patient_id)
        self.dashboard_widget.set_actor(mapped_pid, "patient", mapped_pid)
        layout.addWidget(self.dashboard_widget)

        scroll.setWidget(container)

        view = QWidget()
        v_layout = QVBoxLayout(view)
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.addWidget(scroll)
        return view

    def _launch_card_matching(self) -> None:
        diff = self.combo_card_diff.currentData() or "easy"
        mapped_pid = self._map_patient_id(self.patient_id)
        self.card_widget.start_game(patient_id=mapped_pid, difficulty=diff)
        self.stack.setCurrentIndex(1)

    def _launch_sequence_recall(self) -> None:
        diff = self.combo_seq_diff.currentData() or "easy"
        mapped_pid = self._map_patient_id(self.patient_id)
        self.sequence_widget.start_game(patient_id=mapped_pid, difficulty=diff)
        self.stack.setCurrentIndex(2)

    def _return_to_hub(self) -> None:
        self.stack.setCurrentIndex(0)
        mapped_pid = self._map_patient_id(self.patient_id)
        if hasattr(self, "dashboard_widget"):
            self.dashboard_widget.set_actor(mapped_pid, "patient", mapped_pid)

