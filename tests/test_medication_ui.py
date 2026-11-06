import os
os.environ['AI_CARE_TEST_MODE'] = '1'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QFont, QFontDatabase
from apps.desktop.medication_panel import MedicationPanel, ScheduleDialog
from features.medication.medication_service import MedicationService
from features.medication.medication_migration import archive_legacy
from shared.services.user_service import UserService
from shared.services.email_service import EmailService


class MedicationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Qt's offscreen plugin on Windows does not enumerate system fonts.
        font_path = Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'arial.ttf'
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
            cls.app.setFont(QFont('Arial',11))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.users = UserService(root/'users')
        self.users.legacy_patients_file = root/'patients.json'
        self.users.legacy_patient_file = root/'patient.json'
        self.users.save_user_profile('sample_1','Người dùng mẫu',caregiver_email='caregiver@example.com')
        self.users.save_user_profile('sample_2','Người dùng mẫu 2',caregiver_email='caregiver2@example.com')
        self.now = datetime(2026,10,2,0,59,tzinfo=timezone.utc)
        self.service = MedicationService(root/'medication.sqlite3',clock=lambda:self.now)
        self.panel = MedicationPanel(self.service,self.users,EmailService(mode='test'))
        self.panel.timer.stop()
        self.panel.result_timer.stop()

    def tearDown(self):
        self.panel.shutdown()
        self.panel.close()
        self.tmp.cleanup()

    def add(self, patient='sample_1'):
        return self.service.save_schedule(dict(patient_id=patient,medication_name='Thuốc mẫu A',instructions_text='Hướng dẫn mẫu theo đơn',
                   local_times=['08:00'],timezone='Asia/Ho_Chi_Minh',start_date='2026-10-02',caregiver_alert_enabled=True,overdue_minutes=1))

    def test_editor_and_confirmation_flow(self):
        dlg = ScheduleDialog(self.users,clock=lambda:self.now)
        dlg.fields['medication_name'].setText('Thuốc mẫu A')
        dlg.fields['instructions_text'].setText('Hướng dẫn mẫu theo đơn')
        dlg.fields['start_date'].setText('2026-10-02')
        self.service.save_schedule(dlg.data())
        dlg.close()
        self.now += timedelta(minutes=1)
        self.panel.tick()
        self.assertEqual(self.panel.table.rowCount(),1)
        self.panel.table.selectRow(0)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.panel.act('CONFIRMED')
        self.assertIn('Người dùng đã xác nhận',self.panel.table.item(0,3).text())

    def test_all_patients_reminded_and_email_resolved_per_patient(self):
        self.add()
        self.add('sample_2')
        self.now += timedelta(minutes=2)
        with patch.object(self.panel.email,'send_medication_overdue',wraps=self.panel.email.send_medication_overdue) as send:
            self.panel.tick()
            self.panel.future.result(timeout=5)
            self.panel.poll_email()
            self.panel.tick()
            self.panel.future.result(timeout=5)
            self.panel.poll_email()
            self.assertEqual({c.args[0] for c in send.call_args_list},{'caregiver@example.com','caregiver2@example.com'})
        self.assertEqual(self.panel.table.rowCount(),2)
        self.assertTrue(self.panel.notice.isVisible())

    def test_archive_is_idempotent_and_keeps_sources(self):
        root = Path(self.tmp.name)
        (root/'shared'/'results').mkdir(parents=True)
        source = root/'shared'/'results'/'fall.json'
        source.write_text('{"legacy":true}')
        archive = archive_legacy(root/'data',root/'shared')
        first = (archive/'fall.json').read_bytes()
        source.write_text('{"legacy":false}')
        archive_legacy(root/'data',root/'shared')
        self.assertEqual((archive/'fall.json').read_bytes(),first)
        self.assertTrue(source.exists())

    def test_screenshot(self):
        self.add()
        self.add('sample_2')
        self.now += timedelta(minutes=1)
        self.panel.tick()
        self.panel.notice.hide()
        self.panel.resize(1366,768)
        self.panel.show()
        self.app.processEvents()
        target = Path('docs/medication_reminder/screenshots')
        target.mkdir(parents=True,exist_ok=True)
        self.assertTrue(self.panel.grab().save(str(target/'medication_sample.png')))


if __name__ == '__main__':
    unittest.main()
