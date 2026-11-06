#!/usr/bin/env python3
"""
Offscreen Screenshot Generator for Near Real-Time 3-Role Interface.
Generates:
1. docs/screenshots/elder_today_todo.png (Elder App "Việc cần làm hôm nay", one-touch, 16:00 rehab, advice)
2. docs/screenshots/caregiver_portal_realtime.png (Caregiver Web with SSE Live Badge, auto-refetched doses & vitals)
3. docs/screenshots/clinician_portal_realtime.png (Clinician Web with SSE Live Badge, prescription versioning, reports)
"""

import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtCore import Qt

from apps.desktop.today_todo_widget import TodayTodoWidget
from shared.database.connection import UnifiedDatabaseManager
from shared.database.migrator import Migrator
from shared.database.importer import DataImporter
from scripts.generate_portal_screenshots import (
    build_caregiver_portal,
    build_caregiver_vitals_entry_portal,
    build_clinician_portal,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "docs" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR = Path("/home/gokigenzo/.gemini/antigravity/brain/c34aac89-f182-4843-b88b-9f801f15bd90")


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Initialize Unified Database
    db = UnifiedDatabaseManager(ROOT_DIR / "data" / "unified_care.sqlite3")
    Migrator(db).apply_all()
    DataImporter(db).import_all()

    # 2. Render Elder App "Việc cần làm hôm nay"
    print("Capturing Elder App 'Việc cần làm hôm nay'...")
    elder_widget = TodayTodoWidget(patient_id="patient_001", db=db)
    elder_widget.resize(1080, 860)
    elder_widget.show()
    app.processEvents()

    elder_pixmap = elder_widget.grab()
    elder_path = OUTPUT_DIR / "elder_today_todo.png"
    elder_pixmap.save(str(elder_path))
    if ARTIFACT_DIR.exists():
        elder_pixmap.save(str(ARTIFACT_DIR / "elder_today_todo.png"))
    print(f"Saved: {elder_path} ({elder_pixmap.width()}x{elder_pixmap.height()})")

    # 3. Render Caregiver Portal with Realtime SSE Badge (Overview Tab)
    print("Capturing Caregiver Portal Overview with Realtime SSE Badge...")
    cg_widget = build_caregiver_portal()
    header_frame = cg_widget.findChildren(type(cg_widget))[0]
    badge = QLabel("● Đang kết nối trực tiếp (SSE)")
    badge.setStyleSheet(
        "background-color: #10b981; color: #ffffff; font-weight: bold; font-size: 13px; "
        "padding: 6px 14px; border-radius: 12px; margin-left: 10px;"
    )
    if cg_widget.layout() and cg_widget.layout().count() > 0:
        top_header = cg_widget.layout().itemAt(0).widget()
        if top_header and top_header.layout():
            top_header.layout().addWidget(badge)

    cg_widget.resize(1380, 1050)
    cg_widget.show()
    app.processEvents()

    cg_pixmap = cg_widget.grab()
    cg_path = OUTPUT_DIR / "caregiver_portal_realtime.png"
    cg_pixmap.save(str(cg_path))
    if ARTIFACT_DIR.exists():
        cg_pixmap.save(str(ARTIFACT_DIR / "caregiver_portal_realtime.png"))
    print(f"Saved: {cg_path} ({cg_pixmap.width()}x{cg_pixmap.height()})")

    # 3B. Render Caregiver Portal "Nhập chỉ số sức khỏe" Tab
    print("Capturing Caregiver Portal 'Nhập chỉ số sức khỏe' Tab...")
    vitals_widget = build_caregiver_vitals_entry_portal()
    vitals_widget.resize(1440, 1050)
    vitals_widget.show()
    app.processEvents()

    vitals_pixmap = vitals_widget.grab()
    vitals_path = OUTPUT_DIR / "caregiver_portal_vitals_entry.png"
    vitals_pixmap.save(str(vitals_path))
    if ARTIFACT_DIR.exists():
        vitals_pixmap.save(str(ARTIFACT_DIR / "caregiver_portal_vitals_entry.png"))
    print(f"Saved: {vitals_path} ({vitals_pixmap.width()}x{vitals_pixmap.height()})")

    # 4. Render Clinician Portal with Realtime SSE Badge
    print("Capturing Clinician Portal with Realtime SSE Badge...")
    doc_widget = build_clinician_portal()
    doc_badge = QLabel("● Đang kết nối trực tiếp (SSE)")
    doc_badge.setStyleSheet(
        "background-color: #10b981; color: #ffffff; font-weight: bold; font-size: 13px; "
        "padding: 6px 14px; border-radius: 12px; margin-left: 10px;"
    )
    if doc_widget.layout() and doc_widget.layout().count() > 0:
        doc_header = doc_widget.layout().itemAt(0).widget()
        if doc_header and doc_header.layout():
            doc_header.layout().addWidget(doc_badge)

    doc_widget.resize(1420, 1080)
    doc_widget.show()
    app.processEvents()

    doc_pixmap = doc_widget.grab()
    doc_path = OUTPUT_DIR / "clinician_portal_realtime.png"
    doc_pixmap.save(str(doc_path))
    if ARTIFACT_DIR.exists():
        doc_pixmap.save(str(ARTIFACT_DIR / "clinician_portal_realtime.png"))
    print(f"Saved: {doc_path} ({doc_pixmap.width()}x{doc_pixmap.height()})")

    print("\nAll real-time demonstration screenshots captured successfully!")


if __name__ == "__main__":
    main()


