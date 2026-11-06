"""
Router and Action Dispatcher for AI Rehabilitation & Safety Assistant.
Connects UI actions, Vision Adapters (Face, Emotion, Rehabilitation), and State.
Voice is completely removed from runtime.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING, Any, Dict, Optional

from PyQt5.QtWidgets import QMessageBox

from features.emotion.emotion_adapter import EmotionAdapter
from features.exercise_rehab.rehabilitation_adapter import RehabilitationAdapter
from features.face_monitoring.face_adapter import FaceAdapter
from shared.services.state_service import StateService

if TYPE_CHECKING:
    from apps.desktop.dashboard import DashboardWindow

logger = logging.getLogger("RehabRouter")


class DashboardRouter:
    """Dispatches actions between UI, Vision Adapters, medication UI, and State."""

    def __init__(self, window: Optional[Any] = None) -> None:
        self.window = window

        # State service
        self.state_service = StateService()

        # Core Vision Adapters
        self.face_adapter = FaceAdapter()
        self.emotion_adapter = EmotionAdapter(smoothing_window=8)
        self.rehab_adapter = RehabilitationAdapter()

    def update_recognized_user(self, user_id: str, user_name: Optional[str] = None) -> None:
        """Keep the current identity for display; medication schedules have explicit owners."""
        self.current_user_id = user_id
        self.current_user_name = user_name

    def start_exercise(self, exercise_id: str, target_reps: Optional[int] = None) -> None:
        """Switch camera mode to rehabilitation and begin tracking."""
        self.rehab_adapter.start_exercise(exercise_id, target_reps=target_reps)
        if self.window and hasattr(self.window, "camera_manager"):
            self.window.camera_manager.set_mode("rehabilitation")
        metrics = self.rehab_adapter.get_result()
        ex_name = metrics.get("exercise_name", "bài tập")

        if self.window and hasattr(self.window, "show_status_message"):
            self.window.show_status_message(f"Bắt đầu bài tập: {ex_name}")
        if self.window and hasattr(self.window, "update_dashboard_ui"):
            self.window.update_dashboard_ui()

    def stop_exercise(self) -> Dict[str, Any]:
        """Stop exercise and return to monitoring mode."""
        metrics = self.rehab_adapter.stop_exercise()
        if self.window and hasattr(self.window, "camera_manager"):
            self.window.camera_manager.set_mode("monitor")

        if self.window and hasattr(self.window, "show_status_message"):
            self.window.show_status_message(f"Đã dừng bài tập. Điểm số: {metrics.get('score')}%")
        if self.window and hasattr(self.window, "update_dashboard_ui"):
            self.window.update_dashboard_ui()
        return metrics

    def show_exercise_result_modal(self) -> None:
        """Display comprehensive session evaluation modal."""
        # Resolve patient name from window property or face adapter
        user_name = "Bác"
        if self.window and hasattr(self.window, "current_patient_name") and self.window.current_patient_name:
            user_name = self.window.current_patient_name
        else:
            profile = self.face_adapter.get_patient_profile()
            if profile and profile.get("name"):
                user_name = profile["name"]

        metrics = self.rehab_adapter.get_result()
        reps = metrics.get("repetitions", 0)
        target = metrics.get("target_reps", 10)
        correct = metrics.get("correct_reps", 0)
        incorrect = metrics.get("incorrect_reps", 0)
        has_score = metrics.get("has_score", reps > 0)
        score = metrics.get("score", 0.0)
        ex_name = metrics.get("exercise_name", "Phục hồi chức năng")

        if reps == 0 or not has_score:
            score_html = "<span style='font-size: 15px; color: #53657E; font-weight: 600;'>Chưa có dữ liệu (0 lần hoàn thành)</span>"
            rating = "Chưa có lượt tập nào được hoàn thành trong phiên này."
        else:
            score_color = '#167D62' if score >= 80 else '#A96300'
            score_html = f"<span style='font-size: 18px; font-weight: bold; color: {score_color};'>{score}%</span>"
            rating = "Xuất sắc! Động tác rất chuẩn xác." if score >= 90 else (
                "Khá tốt! Cần chú ý giữ thẳng lưng và biên độ tay." if score >= 75 else
                "Cần cố gắng hơn ở các lần tập tiếp theo."
            )

        msg = QMessageBox(self.window)
        msg.setWindowTitle("Kết Quả Đánh Giá Phục Hồi Chức Năng")
        msg.setText(f"""
            <div style='color: #183A34; font-size: 13px; line-height: 1.6; padding: 4px;'>
                <h3 style='color: #285C4D; margin-top: 0; font-size: 16px;'>📋 BÁO CÁO PHIÊN TẬP PHỤC HỒI CHỨC NĂNG</h3>
                • <b>Người tập:</b> {user_name}<br>
                • <b>Bài tập:</b> <span style='color: #285C4D; font-weight: bold;'>{ex_name}</span><br>
                • <b>Tổng số lần hoàn thành:</b> <b>{reps} / {target} lần</b><br>
                • <b>Động tác chuẩn:</b> <span style='color: #167D62; font-weight: bold;'>{correct}</span>  |  <b>Chưa chuẩn:</b> <span style='color: #C3293E; font-weight: bold;'>{incorrect}</span><br>
                • <b>Điểm kỹ thuật động tác:</b> {score_html}<br>
                • <b>Nhận xét chuyên môn:</b> {rating}
            </div>
        """)
        msg.setIcon(QMessageBox.Information)
        msg.setStyleSheet("""
            QMessageBox { background-color: #FFFFFF; border: 1.5px solid #DFE5DF; border-radius: 12px; }
            QLabel { color: #183A34 !important; background-color: transparent; }
            QPushButton { background-color: #285C4D; color: #FFFFFF !important; font-weight: bold; border-radius: 8px; padding: 8px 24px; min-width: 90px; }
            QPushButton:hover { background-color: #214F42; }
        """)
        if os.environ.get("AI_CARE_TEST_MODE") == "1" or "--test" in sys.argv:
            return
        msg.exec_()
