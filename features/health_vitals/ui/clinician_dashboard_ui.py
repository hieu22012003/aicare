"""
Clinician Dashboard UI for Remote Patient Monitoring (P2).
Implements Section 6A of the specification:
- Tier 1: Assigned Patients List (Care Team) with new unreviewed count, status badges.
- Tier 2: Patient Detail Profile:
  * Header, time filters (7/30/90 days), resolution toggle (RAW, DAY, WEEK with sample count n)
  * Latest vital sign cards with source tooltips
  * Dual-line Blood Pressure chart (Systolic & Diastolic) and Heart Rate chart
  * Raw measurements table synchronized with filters
  * Mark as reviewed button ('Xác nhận đã xem')
  * Doctor feedback / repeat measurement request messaging
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger("ClinicianDashboardUI")


def format_display_dt(iso_str: Optional[str]) -> str:
    """Format ISO datetime to Vietnam local DD/MM/YYYY HH:MM:SS format."""
    if not iso_str:
        return "---"
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo:
            dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_str[:19].replace("T", " ")


try:
    from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
    from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
    from PyQt5.QtWidgets import (
        QButtonGroup,
        QComboBox,
        QFrame,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSplitter,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ImportError:
    PYQT_AVAILABLE = False
    QWidget = object


def format_chart_tick(t_str: str, resolution: str = "raw") -> str:
    """Format tick timestamp for chart X-axis."""
    if not t_str:
        return ""
    if resolution == "week" or "W" in t_str:
        return t_str.split("-")[-1] if "-" in t_str else t_str
    if resolution == "day" and len(t_str) == 10 and t_str[4] == "-":
        parts = t_str.split("-")
        return f"{parts[2]}/{parts[1]}"
    try:
        dt = datetime.fromisoformat(t_str)
        if dt.tzinfo:
            dt = dt.astimezone()
        if resolution == "day":
            return dt.strftime("%d/%m")
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        if len(t_str) >= 10:
            return t_str[5:10].replace("-", "/")
        return t_str


class TrendChartCanvas(QWidget if PYQT_AVAILABLE else object):
    """Custom High-Performance Vector Chart Canvas for Qt rendering vital trends with gaps, ticks, and n counts."""

    def __init__(self, parent=None) -> None:
        if not PYQT_AVAILABLE:
            return
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setStyleSheet("background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px;")
        self.title: str = "Biểu đồ xu hướng"
        self.series1_label: str = "Dữ liệu 1"
        self.series2_label: str = ""
        self.series1_color: QColor = QColor("#0284c7")
        self.series2_color: QColor = QColor("#0d9488")
        self.unit: str = ""
        # Point format: (time_str, val, n_count, extra_label)
        self.series1_points: List[tuple] = []
        self.series2_points: List[tuple] = []
        self.data_gaps: List[tuple] = []
        self.is_empty: bool = True
        self.resolution_mode: str = "raw"
        self.empty_message: str = "Chưa có số đo được gửi trong khoảng thời gian này."

    def set_bp_data(self, bp_trend: dict, resolution: str = "raw") -> None:
        self.title = "XU HƯỚNG HUYẾT ÁP HAI THÀNH PHẦN (mmHg)"
        self.series1_label = "Tâm thu (SYS)"
        self.series2_label = "Tâm trương (DIA)"
        self.series1_color = QColor("#0284c7")  # Deep Sky Blue
        self.series2_color = QColor("#0d9488")  # Teal
        self.unit = "mmHg"
        self.data_gaps = bp_trend.get("data_gaps", [])
        self.resolution_mode = resolution
        self.empty_message = "Chưa có số đo Huyết áp của bệnh nhân trong khoảng thời gian này."

        if resolution in ("day", "week") and bp_trend.get("aggregated_points"):
            agg = bp_trend.get("aggregated_points", [])
            self.is_empty = len(agg) == 0
            self.series1_points = [
                (p["period_key"], p["systolic_mean"], p.get("sample_count_n", 1), f"n={p.get('sample_count_n', 1)}")
                for p in agg if p.get("systolic_mean") is not None
            ]
            self.series2_points = [
                (p["period_key"], p["diastolic_mean"], p.get("sample_count_n", 1), f"n={p.get('sample_count_n', 1)}")
                for p in agg if p.get("diastolic_mean") is not None
            ]
        else:
            readings = bp_trend.get("readings", [])
            self.is_empty = len(readings) == 0
            self.series1_points = [
                (r.measured_at, r.systolic, 1, r.device_label or "")
                for r in readings if r.systolic is not None
            ]
            self.series2_points = [
                (r.measured_at, r.diastolic, 1, r.device_label or "")
                for r in readings if r.diastolic is not None
            ]
        self.update()

    def set_vital_data(
        self,
        trend_dict: dict,
        title: str,
        label: str,
        unit: str,
        color_hex: str = "#e11d48",
        resolution: str = "raw",
    ) -> None:
        self.title = title
        self.series1_label = label
        self.series2_label = ""
        self.series1_color = QColor(color_hex)
        self.unit = unit
        self.data_gaps = trend_dict.get("data_gaps", [])
        self.resolution_mode = resolution
        self.empty_message = f"Chưa có số đo {label} của bệnh nhân trong khoảng thời gian này."

        if resolution in ("day", "week") and trend_dict.get("aggregated_points"):
            agg = trend_dict.get("aggregated_points", [])
            self.is_empty = len(agg) == 0
            self.series1_points = [
                (p.period_key, p.mean_value, p.sample_count_n, f"n={p.sample_count_n}")
                for p in agg if p.mean_value is not None
            ]
            self.series2_points = []
        else:
            raw_obs = trend_dict.get("raw_observations", [])
            self.is_empty = len(raw_obs) == 0
            self.series1_points = [
                (o.measured_at, o.value_numeric, 1, o.device_label or "")
                for o in raw_obs if o.value_numeric is not None
            ]
            self.series2_points = []
        self.update()

    def set_glucose_data(
        self,
        glu_trend: dict,
        unit: str = "mg/dL",
        resolution: str = "raw",
    ) -> None:
        self.title = f"XU HƯỚNG ĐƯỜNG HUYẾT ({unit})"
        self.series1_label = f"Đường huyết ({unit})"
        self.series2_label = ""
        self.series1_color = QColor("#b91c1c")  # Crimson
        self.unit = unit
        self.data_gaps = glu_trend.get("data_gaps", [])
        self.resolution_mode = resolution
        self.empty_message = f"Chưa có số đo Đường huyết ({unit}) của bệnh nhân trong khoảng thời gian này."

        if resolution in ("day", "week") and glu_trend.get("aggregated_points"):
            agg = [p for p in glu_trend.get("aggregated_points", []) if p.unit_code == unit]
            self.is_empty = len(agg) == 0
            self.series1_points = [
                (p.period_key, p.mean_value, p.sample_count_n, f"n={p.sample_count_n}")
                for p in agg if p.mean_value is not None
            ]
            self.series2_points = []
        else:
            raw_obs = [o for o in glu_trend.get("raw_observations", []) if o.unit_code == unit]
            self.is_empty = len(raw_obs) == 0
            self.series1_points = [
                (o.measured_at, o.value_numeric, 1, getattr(o, "notes", "") or getattr(o, "device_label", ""))
                for o in raw_obs if o.value_numeric is not None
            ]
            self.series2_points = []
        self.update()

    def _is_gap(self, t1: str, t2: str) -> bool:
        if not t1 or not t2:
            return False
        for g_start, g_end in self.data_gaps:
            if t1 == g_start and t2 == g_end:
                return True
        try:
            dt1 = datetime.fromisoformat(t1)
            dt2 = datetime.fromisoformat(t2)
            if abs((dt2 - dt1).total_seconds()) >= 48 * 3600:
                return True
        except Exception:
            pass
        return False

    def paintEvent(self, event) -> None:
        if not PYQT_AVAILABLE:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Canvas background & border
        painter.fillRect(0, 0, w, h, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Title
        painter.setPen(QColor("#1e293b"))
        font_title = QFont()
        font_title.setBold(True)
        font_title.setPointSize(10)
        painter.setFont(font_title)
        painter.drawText(16, 24, self.title)

        if self.is_empty or not self.series1_points:
            painter.setPen(QColor("#94a3b8"))
            font_empty = QFont()
            font_empty.setPointSize(11)
            painter.setFont(font_empty)
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, self.empty_message)
            return

        # Margins
        pad_left = 60
        pad_right = 30
        pad_top = 45
        pad_bottom = 45
        plot_w = w - pad_left - pad_right
        plot_h = h - pad_top - pad_bottom

        # Find min/max values
        all_vals = [p[1] for p in self.series1_points if p[1] is not None]
        if self.series2_points:
            all_vals.extend([p[1] for p in self.series2_points if p[1] is not None])
        if not all_vals:
            return

        min_val = min(all_vals)
        max_val = max(all_vals)
        if min_val == max_val:
            min_val = max(0, min_val - 10)
            max_val = max_val + 10
        else:
            diff = max_val - min_val
            min_val = max(0, min_val - diff * 0.15)
            max_val = max_val + diff * 0.15
        val_range = max_val - min_val if max_val > min_val else 1.0

        # Draw grid lines & Y labels
        painter.setPen(QPen(QColor("#f1f5f9"), 1, Qt.SolidLine))
        num_grids = 4
        for i in range(num_grids + 1):
            y_pos = pad_top + plot_h - (i * plot_h / num_grids)
            val_label = min_val + (i * val_range / num_grids)
            painter.drawLine(int(pad_left), int(y_pos), int(w - pad_right), int(y_pos))
            painter.setPen(QColor("#64748b"))
            font_y = QFont()
            font_y.setPointSize(8)
            painter.setFont(font_y)
            painter.drawText(12, int(y_pos + 4), f"{val_label:.0f}")
            painter.setPen(QPen(QColor("#f1f5f9"), 1, Qt.SolidLine))

        # Draw X-axis ticks
        self._draw_x_axis_ticks(painter, self.series1_points, pad_left, pad_top, plot_w, plot_h)

        # Draw Series 1
        self._draw_series_line(
            painter, self.series1_points, min_val, val_range,
            pad_left, pad_top, plot_w, plot_h, self.series1_color, self.series1_label
        )

        # Draw Series 2 if present
        if self.series2_points:
            self._draw_series_line(
                painter, self.series2_points, min_val, val_range,
                pad_left, pad_top, plot_w, plot_h, self.series2_color, self.series2_label
            )

        # Legend at top right
        legend_x = w - pad_right - 220
        font_leg = QFont()
        font_leg.setPointSize(9)
        font_leg.setBold(True)
        painter.setFont(font_leg)

        painter.setPen(self.series1_color)
        painter.drawText(int(legend_x), 24, f"● {self.series1_label}")
        if self.series2_points:
            painter.setPen(self.series2_color)
            painter.drawText(int(legend_x + 115), 24, f"● {self.series2_label}")

        # Gap notice at bottom left (Criterion 13)
        if self.data_gaps:
            painter.setPen(QColor("#dc2626"))
            font_gap_info = QFont()
            font_gap_info.setPointSize(8)
            painter.setFont(font_gap_info)
            painter.drawText(16, h - 8, f"* Phát hiện {len(self.data_gaps)} khoảng gián đoạn >= 48h (Dữ liệu không liên tục, không nối liền)")

    def _draw_x_axis_ticks(
        self,
        painter: QPainter,
        points: List[tuple],
        pad_l: float,
        pad_t: float,
        plot_w: float,
        plot_h: float,
    ) -> None:
        n_pts = len(points)
        if n_pts == 0:
            return

        x_step = plot_w / (n_pts - 1) if n_pts > 1 else 0
        font_axis = QFont()
        font_axis.setPointSize(8)
        painter.setFont(font_axis)
        painter.setPen(QColor("#64748b"))

        k = 1
        if n_pts > 8:
            k = max(1, n_pts // 6)

        for idx, pt in enumerate(points):
            if idx % k == 0 or idx == n_pts - 1:
                t_str = pt[0]
                x = pad_l + (idx * x_step if n_pts > 1 else plot_w / 2)
                y_axis = pad_t + plot_h
                painter.drawLine(int(x), int(y_axis), int(x), int(y_axis + 4))
                label = format_chart_tick(t_str, self.resolution_mode)
                painter.drawText(int(x - 24), int(y_axis + 16), label)

    def _draw_series_line(
        self,
        painter: QPainter,
        points: List[tuple],
        min_v: float,
        v_range: float,
        pad_l: float,
        pad_t: float,
        plot_w: float,
        plot_h: float,
        color: QColor,
        label: str,
    ) -> None:
        n_pts = len(points)
        if n_pts == 0:
            return

        x_step = plot_w / (n_pts - 1) if n_pts > 1 else 0

        coords = []
        for idx, pt in enumerate(points):
            t_str, val = pt[0], pt[1]
            n_count = pt[2] if len(pt) > 2 else 1
            x = pad_l + (idx * x_step if n_pts > 1 else plot_w / 2)
            y = pad_t + plot_h - ((val - min_v) / v_range * plot_h)
            coords.append((x, y, val, t_str, n_count))

        # 1. Draw connecting lines (Criterion 13: Break on gaps >= 48h)
        pen_line = QPen(color, 2)
        painter.setPen(pen_line)

        for i in range(len(coords) - 1):
            curr_c = coords[i]
            next_c = coords[i + 1]
            if self._is_gap(curr_c[3], next_c[3]):
                gap_pen = QPen(QColor("#fca5a5"), 1, Qt.DashLine)
                painter.setPen(gap_pen)
                painter.drawLine(int(curr_c[0]), int(curr_c[1]), int(next_c[0]), int(next_c[1]))
                font_gap = QFont()
                font_gap.setPointSize(7)
                font_gap.setItalic(True)
                painter.setFont(font_gap)
                painter.setPen(QColor("#ef4444"))
                mid_x = (curr_c[0] + next_c[0]) / 2
                mid_y = (curr_c[1] + next_c[1]) / 2 - 8
                painter.drawText(int(mid_x - 32), int(mid_y), "[Gián đoạn >= 48h]")
                painter.setPen(pen_line)
            else:
                painter.drawLine(int(curr_c[0]), int(curr_c[1]), int(next_c[0]), int(next_c[1]))

        # 2. Draw circular points & values (Criterion 12: show n count when aggregated)
        font_val = QFont()
        font_val.setPointSize(8)
        font_val.setBold(True)
        painter.setFont(font_val)

        for x, y, val, t_str, n_count in coords:
            painter.setPen(QPen(Qt.white, 1.5))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(x, y), 4.5, 4.5)

            painter.setPen(QColor("#0f172a"))
            if n_count > 1:
                val_txt = f"{val:.1f} (n={n_count})"
            else:
                val_txt = f"{int(val)}" if float(val).is_integer() else f"{val:.1f}"

            y_offset = -8 if y - pad_t > 15 else 14
            painter.drawText(int(x - 14), int(y + y_offset), val_txt)


class ClinicianDashboardWidget(QWidget if PYQT_AVAILABLE else object):
    """Two-tier Doctor Dashboard widget."""

    def __init__(self, service, clinician_id: str = "doc-001", parent=None) -> None:
        if not PYQT_AVAILABLE:
            raise RuntimeError("PyQt5 is required.")
        super().__init__(parent)
        self.service = service
        self.clinician_id = clinician_id
        self.selected_patient_id: Optional[str] = None
        self.days_filter: int = 30
        self.resolution: str = "raw"
        self._init_ui()

    def set_clinician(self, clinician_id: str) -> None:
        self.clinician_id = clinician_id
        self.selected_patient_id = None
        self.stack.setCurrentIndex(0)
        self.refresh_tier1()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Stacked Widget to switch between Tier 1 (List) and Tier 2 (Patient Detail)
        self.stack = QStackedWidget()

        # --- TIER 1: Patient List Widget ---
        self.tier1_widget = QWidget()
        t1_layout = QVBoxLayout(self.tier1_widget)

        # Header
        t1_hdr = QHBoxLayout()
        self.lbl_t1_title = QLabel("DANH SÁCH BỆNH NHÂN ĐƯỢC PHÂN CÔNG (CARE TEAM)")
        f_title = QFont()
        f_title.setPointSize(14)
        f_title.setBold(True)
        self.lbl_t1_title.setFont(f_title)
        t1_hdr.addWidget(self.lbl_t1_title)
        t1_hdr.addStretch()

        self.btn_refresh_t1 = QPushButton("🔄 TẢI LẠI DANH SÁCH")
        self.btn_refresh_t1.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        self.btn_refresh_t1.clicked.connect(self.refresh_tier1)
        t1_hdr.addWidget(self.btn_refresh_t1)
        t1_layout.addLayout(t1_hdr)

        # Patient List Table
        self.table_patients = QTableWidget()
        self.table_patients.setColumnCount(6)
        self.table_patients.setHorizontalHeaderLabels([
            "Mã BN", "Họ và tên", "Số đo mới", "Trạng thái", "Huyết áp gần nhất", "Thời điểm đo cuối"
        ])
        self.table_patients.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_patients.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_patients.itemDoubleClicked.connect(self._on_patient_selected)
        t1_layout.addWidget(self.table_patients)

        btn_view_detail = QPushButton("Xem Hồ Sơ Chi Tiết Bệnh Nhân Được Chọn")
        btn_view_detail.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 10px; font-size: 14px; border-radius: 4px;")
        btn_view_detail.clicked.connect(self._on_view_detail_clicked)
        t1_layout.addWidget(btn_view_detail)

        self.stack.addWidget(self.tier1_widget)

        # --- TIER 2: Patient Detail Profile Widget ---
        self.tier2_widget = QWidget()
        t2_layout = QVBoxLayout(self.tier2_widget)

        # Navigation Bar
        nav_bar = QHBoxLayout()
        self.btn_back_to_t1 = QPushButton("◀ Quay lại Danh Sách Bệnh Nhân")
        self.btn_back_to_t1.clicked.connect(self._on_back_to_tier1)
        nav_bar.addWidget(self.btn_back_to_t1)
        nav_bar.addStretch()

        self.lbl_patient_hdr = QLabel("HỒ SƠ BỆNH NHÂN: ---")
        self.lbl_patient_hdr.setFont(f_title)
        nav_bar.addWidget(self.lbl_patient_hdr)
        nav_bar.addStretch()

        self.btn_reload_t2_nav = QPushButton("🔄 Tải lại hồ sơ")
        self.btn_reload_t2_nav.setStyleSheet("background-color: #f1f5f9; color: #0f172a; font-weight: bold; padding: 5px 12px; border-radius: 4px; border: 1px solid #cbd5e1;")
        self.btn_reload_t2_nav.clicked.connect(self._on_manual_reload)
        nav_bar.addWidget(self.btn_reload_t2_nav)
        t2_layout.addLayout(nav_bar)

        # Controls Bar: Time Filter & Resolution
        ctrl_bar = QHBoxLayout()
        ctrl_bar.addWidget(QLabel("Khoảng thời gian:"))
        self.btn_7d = QPushButton("7 Ngày")
        self.btn_30d = QPushButton("30 Ngày")
        self.btn_90d = QPushButton("90 Ngày")
        self.btn_30d.setStyleSheet("background-color: #e0f2fe; font-weight: bold;")
        self.btn_7d.clicked.connect(lambda: self._set_days_filter(7))
        self.btn_30d.clicked.connect(lambda: self._set_days_filter(30))
        self.btn_90d.clicked.connect(lambda: self._set_days_filter(90))
        ctrl_bar.addWidget(self.btn_7d)
        ctrl_bar.addWidget(self.btn_30d)
        ctrl_bar.addWidget(self.btn_90d)

        ctrl_bar.addSpacing(20)
        ctrl_bar.addWidget(QLabel("Độ phân giải:"))
        self.rad_raw = QRadioButton("Gốc (RAW)")
        self.rad_day = QRadioButton("Tổng hợp Ngày (DAY)")
        self.rad_week = QRadioButton("Tổng hợp Tuần (WEEK)")
        self.rad_raw.setChecked(True)
        self.rad_raw.toggled.connect(self._on_resolution_changed)
        self.rad_day.toggled.connect(self._on_resolution_changed)
        self.rad_week.toggled.connect(self._on_resolution_changed)
        ctrl_bar.addWidget(self.rad_raw)
        ctrl_bar.addWidget(self.rad_day)
        ctrl_bar.addWidget(self.rad_week)
        ctrl_bar.addStretch()

        # Reload Button in Tier 2
        self.btn_reload_t2 = QPushButton("🔄 TẢI LẠI DỮ LIỆU")
        self.btn_reload_t2.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 6px 14px; border-radius: 4px;")
        self.btn_reload_t2.setToolTip("Tải lại tức thì số đo, thẻ chỉ số và biểu đồ của bệnh nhân từ cơ sở dữ liệu")
        self.btn_reload_t2.clicked.connect(self._on_manual_reload)
        ctrl_bar.addWidget(self.btn_reload_t2)

        self.lbl_reload_status = QLabel("")
        self.lbl_reload_status.setStyleSheet("color: #0369a1; font-weight: bold; font-size: 11px;")
        ctrl_bar.addWidget(self.lbl_reload_status)

        ctrl_bar.addSpacing(10)
        self.btn_mark_reviewed = QPushButton("✓ XÁC NHẬN ĐÃ XEM KỲ NÀY")
        self.btn_mark_reviewed.setStyleSheet("background-color: #15803d; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
        self.btn_mark_reviewed.clicked.connect(self._on_mark_reviewed_clicked)
        ctrl_bar.addWidget(self.btn_mark_reviewed)
        t2_layout.addLayout(ctrl_bar)

        # Latest Cards
        cards_layout = QHBoxLayout()
        self.card_bp = self._create_card("Huyết áp", "---", "#e0f2fe", "#0369a1")
        self.card_hr = self._create_card("Nhịp tim", "---", "#ecfdf5", "#047857")
        self.card_spo2 = self._create_card("SpO2", "---", "#fef3c7", "#b45309")
        self.card_temp = self._create_card("Thân nhiệt", "---", "#f3e8ff", "#7e22ce")
        self.card_glu = self._create_card("Đường huyết", "---", "#fee2e2", "#b91c1c")
        cards_layout.addWidget(self.card_bp)
        cards_layout.addWidget(self.card_hr)
        cards_layout.addWidget(self.card_spo2)
        cards_layout.addWidget(self.card_temp)
        cards_layout.addWidget(self.card_glu)
        t2_layout.addLayout(cards_layout)

        # Connect clicking vital cards to jump directly to corresponding chart tab
        self.card_bp.setCursor(Qt.PointingHandCursor)
        self.card_hr.setCursor(Qt.PointingHandCursor)
        self.card_glu.setCursor(Qt.PointingHandCursor)
        self.card_bp.mousePressEvent = lambda e: self.chart_tabs.setCurrentIndex(0)
        self.card_hr.mousePressEvent = lambda e: self.chart_tabs.setCurrentIndex(1)
        self.card_glu.mousePressEvent = lambda e: self.chart_tabs.setCurrentIndex(2)

        # Charts Area with Tabs for BP, Heart Rate, and Blood Glucose
        self.chart_tabs = QTabWidget()
        self.chart_tabs.setStyleSheet("""
            QTabBar::tab { font-size: 13px; font-weight: bold; padding: 8px 18px; }
            QTabBar::tab:selected { background: #0284c7; color: white; border-radius: 4px; }
        """)

        # 1. Blood Pressure Chart Tab (Dual-line SYS/DIA - mmHg)
        self.chart_bp = TrendChartCanvas()
        self.chart_tabs.addTab(self.chart_bp, "🩺 Huyết Áp (SYS/DIA - mmHg)")

        # 2. Heart Rate Chart Tab (Single-line - bpm)
        self.chart_hr = TrendChartCanvas()
        self.chart_tabs.addTab(self.chart_hr, "❤️ Nhịp Tim (bpm)")

        # 3. Blood Glucose Chart Tab with Unit Selection (Criterion 14)
        glu_tab_widget = QWidget()
        glu_layout = QVBoxLayout(glu_tab_widget)
        glu_layout.setContentsMargins(4, 4, 4, 4)

        glu_ctrl = QHBoxLayout()
        glu_ctrl.addWidget(QLabel("<b>Đơn vị đo đường huyết:</b>"))
        self.combo_glu_unit = QComboBox()
        self.combo_glu_unit.setStyleSheet("font-weight: bold; padding: 2px 8px;")
        self.combo_glu_unit.addItem("mg/dL (Đo mao mạch)", "mg/dL")
        self.combo_glu_unit.addItem("mmol/L (Tiêu chuẩn quốc tế)", "mmol/L")
        self.combo_glu_unit.currentIndexChanged.connect(self._on_glu_unit_changed)
        glu_ctrl.addWidget(self.combo_glu_unit)

        self.lbl_glu_units_info = QLabel("")
        self.lbl_glu_units_info.setStyleSheet("color: #b91c1c; font-style: italic; font-weight: bold; margin-left: 8px;")
        glu_ctrl.addWidget(self.lbl_glu_units_info)
        glu_ctrl.addStretch()
        glu_layout.addLayout(glu_ctrl)

        self.chart_glu = TrendChartCanvas()
        glu_layout.addWidget(self.chart_glu)

        self.chart_tabs.addTab(glu_tab_widget, "🩸 Đường Huyết (mg/dL & mmol/L)")
        t2_layout.addWidget(self.chart_tabs)

        # Bottom Splitter: Raw Table & Feedback Form
        bottom_box = QHBoxLayout()

        # Left: Raw table
        left_table_box = QVBoxLayout()
        left_table_box.addWidget(QLabel("<b>BẢNG SỐ ĐO GỐC (ĐỒNG BỘ THEO BỘ LỌC THỜI GIAN)</b>"))
        self.table_raw = QTableWidget()
        self.table_raw.setColumnCount(6)
        self.table_raw.setHorizontalHeaderLabels(["Thời điểm đo", "Chỉ số", "Giá trị", "Nguồn / Máy", "Người nhập", "Trạng thái"])
        self.table_raw.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        left_table_box.addWidget(self.table_raw)
        bottom_box.addLayout(left_table_box, 2)

        # Right: Feedback message form
        right_feedback_box = QVBoxLayout()
        right_feedback_box.addWidget(QLabel("<b>GỬI PHẢN HỒI / YÊU CẦU ĐO LẠI CHO BỆNH NHÂN</b>"))
        self.txt_feedback = QTextEdit()
        self.txt_feedback.setPlaceholderText("Nhập lời dặn chuyên môn của bác sĩ (ví dụ: Cụ đo lại huyết áp lúc 14:00 sau khi nghỉ ngơi nhé)...")
        right_feedback_box.addWidget(self.txt_feedback)

        self.btn_send_feedback = QPushButton("GỬI PHẢN HỒI CHO BỆNH NHÂN")
        self.btn_send_feedback.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold; padding: 8px; border-radius: 4px;")
        self.btn_send_feedback.clicked.connect(self._on_send_feedback_clicked)
        right_feedback_box.addWidget(self.btn_send_feedback)

        self.lbl_feedback_status = QLabel("")
        right_feedback_box.addWidget(self.lbl_feedback_status)
        bottom_box.addLayout(right_feedback_box, 1)

        t2_layout.addLayout(bottom_box)
        self.stack.addWidget(self.tier2_widget)

        layout.addWidget(self.stack)
        self.refresh_tier1()

    def _create_card(self, title: str, default_val: str, bg_color: str, text_color: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"background-color: {bg_color}; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px;")
        l = QVBoxLayout(card)
        l.setContentsMargins(6, 6, 6, 6)
        lbl_t = QLabel(title)
        lbl_t.setStyleSheet(f"font-size: 11px; font-weight: bold; color: {text_color};")
        lbl_v = QLabel(default_val)
        lbl_v.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {text_color};")
        lbl_v.setObjectName("val")
        lbl_sub = QLabel("")
        lbl_sub.setStyleSheet("font-size: 10px; color: #64748b;")
        lbl_sub.setObjectName("sub")
        l.addWidget(lbl_t)
        l.addWidget(lbl_v)
        l.addWidget(lbl_sub)
        return card

    def refresh_tier1(self) -> None:
        """Fetch Tier 1 patients assigned to current clinician."""
        try:
            patients = self.service.get_clinician_patients_list(self.clinician_id)
        except PermissionError as e:
            QMessageBox.critical(self, "Lỗi phân quyền", str(e))
            return

        self.table_patients.setRowCount(len(patients))
        for row, p in enumerate(patients):
            unrev_txt = f"+{p.unreviewed_records_count}" if p.unreviewed_records_count > 0 else "0 mới"
            last_m = format_display_dt(p.last_measured_at)
            self.table_patients.setItem(row, 0, QTableWidgetItem(p.patient_id))
            self.table_patients.setItem(row, 1, QTableWidgetItem(p.full_name))
            self.table_patients.setItem(row, 2, QTableWidgetItem(unrev_txt))
            self.table_patients.setItem(row, 3, QTableWidgetItem(p.review_status))
            self.table_patients.setItem(row, 4, QTableWidgetItem(p.latest_bp or "---"))
            self.table_patients.setItem(row, 5, QTableWidgetItem(last_m))

    def refresh_all(self) -> None:
        """Real-time refresh of both Tier 1 and Tier 2 (if patient is selected)."""
        self.refresh_tier1()
        if self.selected_patient_id:
            self._load_tier2_patient()

    def _on_back_to_tier1(self) -> None:
        self.selected_patient_id = None
        self.stack.setCurrentIndex(0)
        self.refresh_tier1()

    def _on_manual_reload(self) -> None:
        if self.selected_patient_id:
            self._load_tier2_patient()
            now_str = datetime.now().strftime("%H:%M:%S")
            self.lbl_reload_status.setText(f"✓ Vừa cập nhật {now_str}")
        else:
            self.refresh_tier1()

    def _on_view_detail_clicked(self) -> None:
        sel_row = self.table_patients.currentRow()
        if sel_row < 0:
            QMessageBox.warning(self, "Chọn bệnh nhân", "Vui lòng chọn một bệnh nhân trong bảng để xem chi tiết.")
            return
        pat_id = self.table_patients.item(sel_row, 0).text()
        self.selected_patient_id = pat_id
        self._load_tier2_patient()

    def _on_patient_selected(self, item) -> None:
        pat_id = self.table_patients.item(item.row(), 0).text()
        self.selected_patient_id = pat_id
        self._load_tier2_patient()

    def _load_tier2_patient(self) -> None:
        if not self.selected_patient_id:
            return

        try:
            prof = self.service.get_patient_profile(self.clinician_id, self.selected_patient_id)
        except PermissionError as e:
            QMessageBox.critical(self, "Từ chối truy cập", str(e))
            return

        pat = prof["patient"]
        self.lbl_patient_hdr.setText(f"HỒ SƠ BỆNH NHÂN: {pat.full_name} ({pat.patient_id}) | MÚI GIỜ: {pat.timezone}")

        # Update Latest Cards
        latest_bp = prof["latest_bp"]
        if latest_bp and latest_bp.systolic and latest_bp.diastolic:
            self.card_bp.findChild(QLabel, "val").setText(f"{int(latest_bp.systolic)}/{int(latest_bp.diastolic)} mmHg")
            self.card_bp.findChild(QLabel, "sub").setText(f"{format_display_dt(latest_bp.measured_at)} ({latest_bp.device_label})")
        else:
            self.card_bp.findChild(QLabel, "val").setText("Chưa có")
            self.card_bp.findChild(QLabel, "sub").setText("")

        latest_obs = prof["latest_by_type"]
        for card, code in [
            (self.card_hr, "heart_rate"),
            (self.card_spo2, "spo2"),
            (self.card_temp, "temperature"),
            (self.card_glu, "blood_glucose"),
        ]:
            obs = latest_obs.get(code)
            if obs:
                card.findChild(QLabel, "val").setText(f"{obs.value_numeric} {obs.unit_code}")
                card.findChild(QLabel, "sub").setText(f"{format_display_dt(obs.measured_at)} ({obs.device_label})")
            else:
                card.findChild(QLabel, "val").setText("Chưa có")
                card.findChild(QLabel, "sub").setText("")

                card.findChild(QLabel, "sub").setText("")

        self._refresh_tier2_data()
        self.stack.setCurrentIndex(1)

    def _refresh_tier2_data(self) -> None:
        if not self.selected_patient_id:
            return

        from features.health_vitals.domain.models import Resolution
        res_enum = Resolution.RAW
        res_str = "raw"
        if self.rad_day.isChecked():
            res_enum = Resolution.DAY
            res_str = "day"
        elif self.rad_week.isChecked():
            res_enum = Resolution.WEEK
            res_str = "week"

        # 1. Update Blood Pressure Chart (Dual-line: SYS/DIA)
        bp_trend = self.service.get_blood_pressure_trend(
            clinician_id=self.clinician_id,
            patient_id=self.selected_patient_id,
            days_filter=self.days_filter,
            resolution=res_enum,
        )
        self.chart_bp.set_bp_data(bp_trend, resolution=res_str)

        # 2. Update Heart Rate Chart (Single-line: bpm)
        hr_trend = self.service.get_observations_trend(
            clinician_id=self.clinician_id,
            patient_id=self.selected_patient_id,
            type_code="heart_rate",
            days_filter=self.days_filter,
            resolution=res_enum,
        )
        self.chart_hr.set_vital_data(
            trend_dict=hr_trend,
            title="XU HƯỚNG NHỊP TIM (bpm)",
            label="Nhịp tim",
            unit="bpm",
            color_hex="#e11d48",  # Rose
            resolution=res_str,
        )

        # 3. Update Blood Glucose Chart (mg/dL & mmol/L)
        glu_trend = self.service.get_observations_trend(
            clinician_id=self.clinician_id,
            patient_id=self.selected_patient_id,
            type_code="blood_glucose",
            days_filter=self.days_filter,
            resolution=res_enum,
        )
        self._cached_glu_trend = glu_trend
        self._cached_glu_res_str = res_str
        self._update_glu_chart()

        # 4. Update Raw Measurements Table
        from datetime import timedelta
        start_iso = (self.service.get_current_time() - timedelta(days=self.days_filter)).isoformat()
        raw_obs = self.service.obs_repo.get_observations(
            patient_id=self.selected_patient_id,
            start_time=start_iso,
            limit=50,
        )
        self.table_raw.setRowCount(len(raw_obs))
        for row, o in enumerate(raw_obs):
            self.table_raw.setItem(row, 0, QTableWidgetItem(format_display_dt(o.measured_at)))
            self.table_raw.setItem(row, 1, QTableWidgetItem(o.type_name))
            self.table_raw.setItem(row, 2, QTableWidgetItem(f"{o.value_numeric} {o.unit_code}"))
            self.table_raw.setItem(row, 3, QTableWidgetItem(o.device_label))
            self.table_raw.setItem(row, 4, QTableWidgetItem(o.entered_by_name))
            self.table_raw.setItem(row, 5, QTableWidgetItem(o.status))

    def _on_glu_unit_changed(self) -> None:
        self._update_glu_chart()

    def _update_glu_chart(self) -> None:
        if not hasattr(self, "_cached_glu_trend") or not self._cached_glu_trend:
            return
        glu_trend = self._cached_glu_trend
        res_str = getattr(self, "_cached_glu_res_str", "raw")
        unit = self.combo_glu_unit.currentData() or "mg/dL"

        # Check all available units in patient's data (Criterion 14)
        available_units = glu_trend.get("units", [])
        if available_units and unit not in available_units:
            unit = available_units[0]
            self.combo_glu_unit.blockSignals(True)
            idx = self.combo_glu_unit.findData(unit)
            if idx >= 0:
                self.combo_glu_unit.setCurrentIndex(idx)
            self.combo_glu_unit.blockSignals(False)

        if len(available_units) > 1:
            other_units = [u for u in available_units if u != unit]
            self.lbl_glu_units_info.setText(
                f"⚠️ Lưu ý: Bệnh nhân có cả số đo {', '.join(other_units)}. Trục đồ thị được phân tách riêng."
            )
        else:
            self.lbl_glu_units_info.setText("")

        self.chart_glu.set_glucose_data(glu_trend, unit=unit, resolution=res_str)

    def _set_days_filter(self, days: int) -> None:
        self.days_filter = days
        self.btn_7d.setStyleSheet("" if days != 7 else "background-color: #e0f2fe; font-weight: bold;")
        self.btn_30d.setStyleSheet("" if days != 30 else "background-color: #e0f2fe; font-weight: bold;")
        self.btn_90d.setStyleSheet("" if days != 90 else "background-color: #e0f2fe; font-weight: bold;")
        self._refresh_tier2_data()

    def _on_resolution_changed(self) -> None:
        self._refresh_tier2_data()

    def _on_mark_reviewed_clicked(self) -> None:
        if not self.selected_patient_id:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            rev = self.service.mark_as_reviewed(
                clinician_id=self.clinician_id,
                patient_id=self.selected_patient_id,
                period_start="2026-09-01T00:00:00Z",
                period_end=now_iso,
                notes=f"Đã xem xét chỉ số {self.days_filter} ngày",
            )
            QMessageBox.information(self, "Đã ghi nhận", f"Đã xác nhận xem hồ sơ (Mã review: {rev.review_id}). Số bản ghi mới đã được reset.")
            self.refresh_tier1()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", str(e))

    def _on_send_feedback_clicked(self) -> None:
        if not self.selected_patient_id:
            return
        body = self.txt_feedback.toPlainText().strip()
        if not body:
            QMessageBox.warning(self, "Thiếu nội dung", "Vui lòng nhập nội dung phản hồi cho bệnh nhân.")
            return

        try:
            msg = self.service.send_clinical_message(
                clinician_id=self.clinician_id,
                patient_id=self.selected_patient_id,
                body=body,
            )
            QMessageBox.information(self, "Đã gửi", f"Đã gửi phản hồi thành công (Mã tin nhắn: {msg.message_id})!")
            self.txt_feedback.clear()
            self.lbl_feedback_status.setText(f"✓ Đã gửi lúc {msg.created_at[:16].replace('T', ' ')}")
            self.refresh_tier1()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", str(e))
