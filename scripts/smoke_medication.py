"""Headless full-dashboard smoke and screenshots using sample profiles only."""
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
os.chdir(root)
os.environ['AI_CARE_TEST_MODE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

with tempfile.TemporaryDirectory(prefix='medication-smoke-') as data:
    os.environ['AI_CARE_TEST_DATA_DIR'] = data
    from shared import config
    import numpy as np
    from unittest.mock import patch
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFontDatabase, QFont
    from shared.camera.camera_manager import CameraManager
    from shared.services.user_service import UserService
    from apps.desktop.dashboard import DashboardWindow

    app = QApplication([])
    for filename in ('arial.ttf','segoeui.ttf','segoeuib.ttf'):
        font = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI',11))
    users = UserService()
    users.save_user_profile('sample_1','Người dùng mẫu',caregiver_email='caregiver@example.com')
    users.save_user_profile('sample_2','Người dùng mẫu 2',caregiver_email='caregiver2@example.com')
    with patch.object(CameraManager,'start_camera',return_value=True), patch('smtplib.SMTP',side_effect=AssertionError('SMTP forbidden')):
        window = DashboardWindow()
        window.showNormal()
        window.resize(1366,900)
        now = [datetime(2026,10,2,0,59,tzinfo=timezone.utc)]
        service = window.medication_panel.service
        service.clock = lambda:now[0]
        for uid in ('sample_1','sample_2'):
            service.save_schedule(dict(patient_id=uid,medication_name='Thuốc mẫu A',instructions_text='Chỉ dẫn mẫu nguyên văn theo đơn',
                                  local_times=['08:00'],timezone='Asia/Ho_Chi_Minh',start_date='2026-10-02'))
        now[0] += timedelta(minutes=1)
        window.medication_panel.tick()
        window.medication_panel.notice.hide()
        assert window.medication_panel.table.rowCount() == 2
        frame = np.zeros((480,640,3),dtype=np.uint8)
        assert window._process_video_frame(frame.copy()).shape == frame.shape
        window.router.start_exercise('arm_raise')
        assert window._process_video_frame(frame.copy()).shape == frame.shape
        window.router.stop_exercise()
        window.update_dashboard_ui()
        for retired in ('features.fall_risk.fall_monitor','features.fall_risk.fall_test_runner','features.fall_risk.fall_adapter','features.fall_risk.hfd_model.fall_detector'):
            assert retired not in sys.modules, retired
        output = root/'docs'/'medication_reminder'/'screenshots'
        output.mkdir(parents=True,exist_ok=True)
        for index,name in [(0,'dashboard_rehabilitation.png'),(1,'dashboard_medication.png')]:
            window.medication_tabs.setCurrentIndex(index)
            app.processEvents()
            assert window.grab().save(str(output/name))
        window.close()
        app.processEvents()
    logging.shutdown()
print('PASS: full dashboard, two independent reminders, frame routing, tab switching, shutdown; zero fall imports and zero SMTP.')
