"""
Role-Aware Progress Dashboard Widget for Cognitive Games.
Adheres to:
- Distinct views for Elderly, Caregiver, and Clinician (Section 2, Section 6).
- Elderly view: encouraging participation summary without ranks/comparison to others.
- Clinician view: strictly segregates metrics by game code, difficulty, and rules_version with sample count n (Criterion 4).
- Clear disclaimer: never presents scores as IQ or clinical dementia indicators.
- Enforces role-based permissions and tenant isolation (Criterion 6).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List, Optional

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from features.music_care.cognitive_games.domain.models import ProgressSummary
from features.music_care.cognitive_games.services.cognitive_service import CognitiveService

logger = logging.getLogger("ProgressDashboardUI")


class ProgressDashboardWidget(QWidget):
    def __init__(self, service: CognitiveService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.service = service

        self.current_actor_id: str = "pat-001"
        self.current_actor_role: str = "patient"
        self.target_patient_id: str = "pat-001"

        self._init_ui()

    def set_actor(self, actor_id: str, actor_role: str, target_patient_id: str) -> None:
        self.current_actor_id = actor_id
        self.current_actor_role = actor_role
        self.target_patient_id = target_patient_id
        self.refresh_data()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # 1. Header & Filter Bar
        header_layout = QHBoxLayout()

        self.title_label = QLabel("📊 Tiến Trình Hoạt Động & Tham Gia")
        self.title_label.setFont(QFont("Arial", 20, QFont.Bold))
        self.title_label.setStyleSheet("color: #0D47A1;")
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        self.btn_refresh = QPushButton("🔄 Tải Lại Dữ Liệu")
        self.btn_refresh.setMinimumHeight(46)
        self.btn_refresh.setFont(QFont("Arial", 13, QFont.Bold))
        self.btn_refresh.setStyleSheet(
            """
            QPushButton {
                background-color: #1976D2;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 6px 18px;
            }
            QPushButton:hover { background-color: #1565C0; }
            """
        )
        self.btn_refresh.clicked.connect(self.refresh_data)
        header_layout.addWidget(self.btn_refresh)

        main_layout.addLayout(header_layout)

        # 2. Informational Disclaimer Banner (Strictly Non-diagnostic)
        self.banner = QLabel(
            "“Luyện tập và giải trí phù hợp với bạn.”\n"
            "Dữ liệu ghi nhận mức độ tham gia và mức độ quen thuộc với bài tập. "
            "KHÔNG sử dụng điểm số này làm chỉ số chẩn đoán y khoa sa sút trí tuệ hay điểm IQ."
        )
        self.banner.setFont(QFont("Arial", 13))
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            """
            background-color: #FFF3E0;
            color: #E65100;
            padding: 12px 16px;
            border-radius: 8px;
            border: 1px solid #FFE0B2;
            """
        )
        main_layout.addWidget(self.banner)

        # 3. Role-specific content container
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setSpacing(16)
        main_layout.addWidget(self.content_widget, 1)

    def refresh_data(self) -> None:
        """Refreshes dashboard based on actor permissions and target patient."""
        # Clear existing view
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Check permissions via service (Criterion 6)
        try:
            summaries = self.service.get_patient_progress(
                actor_id=self.current_actor_id,
                actor_role=self.current_actor_role,
                patient_id=self.target_patient_id,
            )
            history = self.service.get_patient_session_history(
                actor_id=self.current_actor_id,
                actor_role=self.current_actor_role,
                patient_id=self.target_patient_id,
                limit=30,
            )
        except PermissionError as pe:
            self._render_permission_denied(str(pe))
            return
        except Exception as e:
            self._render_permission_denied(f"Lỗi truy vấn: {str(e)}")
            return

        role = self.current_actor_role.lower()
        if role in ["patient", "elderly"]:
            self._render_elderly_view(summaries, history)
        elif role == "caregiver":
            self._render_caregiver_view(summaries, history)
        elif role in ["clinician", "doctor"]:
            self._render_clinician_view(summaries, history)
        else:
            self._render_elderly_view(summaries, history)

    def _render_permission_denied(self, reason: str) -> None:
        box = QWidget()
        l = QVBoxLayout(box)
        lbl = QLabel(f"🚫 TRUY CẬP BỊ TỪ CHỐI\n\n{reason}")
        lbl.setFont(QFont("Arial", 16, QFont.Bold))
        lbl.setStyleSheet("color: #C62828; padding: 24px; background-color: #FFEBEE; border-radius: 8px;")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setWordWrap(True)
        l.addWidget(lbl)
        self.content_layout.addWidget(box)

    def _render_elderly_view(self, summaries: List[ProgressSummary], history: list) -> None:
        """Elderly View: positive, encouraging, no ranks or comparisons to others."""
        total_sessions = sum(s.total_sessions for s in summaries)
        completed = sum(s.completed_sessions for s in summaries)

        card = QGroupBox("🌟 Hoạt Động Của Bác")
        card.setFont(QFont("Arial", 15, QFont.Bold))
        c_layout = QVBoxLayout(card)

        msg = QLabel(
            f"🎉 Hoan hô bác! Bác đã tham gia tổng cộng {total_sessions} buổi rèn luyện trí nhớ.\n"
            f"Số buổi hoàn thành trọn vẹn: {completed} buổi.\n"
            "Mỗi ngày một chút niềm vui, giữ gìn sự minh mẫn và yêu đời!"
        )
        msg.setFont(QFont("Arial", 15))
        msg.setStyleSheet("color: #214A3B; line-height: 1.5;")
        msg.setWordWrap(True)
        c_layout.addWidget(msg)
        self.content_layout.addWidget(card)

        # Recent activities table
        hist_box = QGroupBox("🕒 Nhật Ký Các Buổi Rèn Luyện Gần Đây")
        hist_box.setFont(QFont("Arial", 14, QFont.Bold))
        h_layout = QVBoxLayout(hist_box)

        table = QTableWidget()
        table.setFont(QFont("Arial", 13))
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Thời Gian", "Trò Chơi", "Mức Độ", "Kết Quả"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setRowCount(min(len(history), 10))

        for row, sess in enumerate(history[:10]):
            table.setItem(row, 0, QTableWidgetItem(sess.started_at[:16].replace("T", " ")))
            g_name = "Lật hình ghép cặp" if "card" in sess.game_type_id else "Nhớ trình tự"
            table.setItem(row, 1, QTableWidgetItem(g_name))
            table.setItem(row, 2, QTableWidgetItem(sess.difficulty.upper()))
            st_text = "Hoàn thành ✓" if sess.status == "completed" else "Nghỉ ngang (Bỏ dở)"
            table.setItem(row, 3, QTableWidgetItem(st_text))

        h_layout.addWidget(table)
        self.content_layout.addWidget(hist_box)

    def _render_caregiver_view(self, summaries: List[ProgressSummary], history: list) -> None:
        """Caregiver View: engagement tracking without clinical alarm."""
        card = QGroupBox("👨‍👩‍👧 Nhật Ký Tham Gia Dành Cho Người Thân")
        card.setFont(QFont("Arial", 14, QFont.Bold))
        c_layout = QVBoxLayout(card)

        desc = QLabel(
            "Theo dõi mức độ duy trì thói quen và cảm nhận của người thân. "
            "Lưu ý: Không tạo áp lực hay đánh giá suy giảm trí nhớ nếu người thân có buổi bỏ dở."
        )
        desc.setFont(QFont("Arial", 13))
        desc.setWordWrap(True)
        c_layout.addWidget(desc)
        self.content_layout.addWidget(card)

        # Overview Table
        table_box = QGroupBox("Chi Tiết Từng Hoạt Động")
        table_box.setFont(QFont("Arial", 14, QFont.Bold))
        t_layout = QVBoxLayout(table_box)

        table = QTableWidget()
        table.setFont(QFont("Arial", 12))
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([
            "Trò Chơi", "Độ Khó", "Số Phiên Chơi", "Tỷ Lệ Hoàn Thành", "Thời Lượng Trung Bình"
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setRowCount(len(summaries))

        for row, s in enumerate(summaries):
            table.setItem(row, 0, QTableWidgetItem(s.game_name))
            table.setItem(row, 1, QTableWidgetItem(s.difficulty.upper()))
            table.setItem(row, 2, QTableWidgetItem(str(s.total_sessions)))
            table.setItem(row, 3, QTableWidgetItem(f"{s.completion_rate:.1f}%"))
            table.setItem(row, 4, QTableWidgetItem(f"{s.avg_active_seconds:.1f}s"))

        t_layout.addWidget(table)
        self.content_layout.addWidget(table_box)

    def _render_clinician_view(self, summaries: List[ProgressSummary], history: list) -> None:
        """
        Clinician View:
        - Segregated strictly by game code, difficulty, and rules_version (Criterion 4).
        - Explicit sample count n (sample_count_n).
        - Non-continuous metric analysis.
        """
        doc_header = QGroupBox("🩺 Báo Cáo Tần Suất Tham Gia Cho Bác Sĩ / Chuyên Gia")
        doc_header.setFont(QFont("Arial", 14, QFont.Bold))
        d_layout = QVBoxLayout(doc_header)

        notice = QLabel(
            "Chỉ số phân tích được tổng hợp RIÊNG BIỆT theo từng loại trò chơi, độ khó và phiên bản luật (Criterion 4).\n"
            "Không so sánh gộp các phiên khác luật hoặc khác độ khó như một thước đo liên tục. Mẫu hợp lệ: n phiên."
        )
        notice.setFont(QFont("Arial", 13))
        notice.setStyleSheet("color: #0D47A1;")
        notice.setWordWrap(True)
        d_layout.addWidget(notice)
        self.content_layout.addWidget(doc_header)

        # Segregated Summary Table
        table_box = QGroupBox("Bảng Thống Kê Phân Nhóm Chuẩn Hóa")
        table_box.setFont(QFont("Arial", 14, QFont.Bold))
        t_layout = QVBoxLayout(table_box)

        table = QTableWidget()
        table.setFont(QFont("Arial", 12))
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "Mã Game", "Tên Trò Chơi", "Độ Khó", "Phiên Bản Luật", "Mẫu (n)", "Tỷ Lệ Hoàn Thành", "Gợi Ý TB"
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setRowCount(len(summaries))

        for row, s in enumerate(summaries):
            table.setItem(row, 0, QTableWidgetItem(s.game_code))
            table.setItem(row, 1, QTableWidgetItem(s.game_name))
            table.setItem(row, 2, QTableWidgetItem(s.difficulty.upper()))
            table.setItem(row, 3, QTableWidgetItem(s.rules_version))
            table.setItem(row, 4, QTableWidgetItem(f"n = {s.sample_count_n}"))
            table.setItem(row, 5, QTableWidgetItem(f"{s.completion_rate:.1f}%"))
            table.setItem(row, 6, QTableWidgetItem(f"{s.avg_help_count:.1f} lần"))

        t_layout.addWidget(table)
        self.content_layout.addWidget(table_box)
