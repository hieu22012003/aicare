"""Full dashboard smoke with isolated data and fake camera/audio/notification hardware.
This does not establish microphone quality, live camera counting or real SMTP delivery.
"""
import os
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from unittest.mock import Mock,patch
    from types import SimpleNamespace
    os.environ.update(QT_QPA_PLATFORM='offscreen',AI_CARE_TEST_MODE='1',AI_CARE_DISABLE_TTS='1',
                      EMAIL_ENABLED='false',VOICE_AI_ENABLED='false',VOICE_SEMANTIC_BACKEND='local_rules')
    # Avoid native PortAudio initialization during MediaPipe's eager audio imports.
    sys.modules['sounddevice']=Mock()
    with tempfile.TemporaryDirectory(prefix='local-rules-dashboard-') as data:
        os.environ['AI_CARE_TEST_DATA_DIR']=data
        from PyQt5.QtWidgets import QApplication
        from shared.database.connection import UnifiedDatabaseManager
        from shared.database.migrator import Migrator
        from shared.database.importer import DataImporter
        from shared.camera.camera_manager import CameraManager
        from apps.desktop.dashboard import DashboardWindow
        from features.voice_assistant.local_rules.requests import RequestService
        from features.voice_assistant.voice_ai.business_adapters.assistance_adapter import DoctorAssistanceAdapter
        db=UnifiedDatabaseManager();Migrator(db).apply_all();DataImporter(db).import_all()
        # Explicit fake recipient mapping: never rely on imported demo/production contacts.
        with db.connect() as conn:
            conn.execute("UPDATE patient_caregivers SET is_active=0 WHERE patient_id='patient_001'")
            conn.execute("INSERT INTO users(user_id,email,phone,full_name,role,created_at) VALUES ('voice_fake','contact-8bba82fa@example.com','0','Người nhà demo','caregiver','2026-01-01')")
            conn.execute("INSERT INTO caregivers(caregiver_id,user_id) VALUES ('voice_cg','voice_fake')")
            conn.execute("INSERT INTO patient_caregivers(patient_caregiver_id,patient_id,caregiver_id,valid_from,valid_to,is_primary,notification_priority) VALUES ('voice_link','patient_001','voice_cg','2000-01-01','2099-01-01',1,0)")
        class FakeTransport:
            calls=[]
            def send_voice_request(self,*args):
                self.calls.append(args)
                return SimpleNamespace(success=True,status='SMTP_ACCEPTED')
        transport=FakeTransport()
        app=QApplication([])
        def wait(predicate):
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                app.processEvents();time.sleep(.01)
                if predicate():return
            raise AssertionError('UI smoke timeout')
        with patch.object(CameraManager,'start_camera',return_value=True),patch('smtplib.SMTP',side_effect=AssertionError('Real SMTP forbidden')),patch.object(DoctorAssistanceAdapter,'local_request_service',lambda self,*args:RequestService(self.db,transport)):
            window=DashboardWindow()
            # Worker is enabled lazily; no microphone recording is started.
            window.local_voice_widget.micro.click()
            wait(lambda:window.local_voice_controller.state in ('idle','unavailable'))
            window._on_patient_registered({'patient_id':'1','name':'Demo'})
            coordinator=window.local_voice_widget.request_coordinator
            wait(lambda:not coordinator.busy)
            captured=window.voice_dispatcher.snapshot();assert captured
            window.local_voice_controller.transcript_ready.emit(dict(session_id='ui-final-1',transcript='Gọi con giúp tôi',status='completed',is_final=True),captured)
            if not coordinator.dialogue.pending:
                print('Review state:',window.local_voice_widget.status.text(),flush=True)
            assert coordinator.dialogue.pending and not transport.calls
            assert window.local_voice_widget.confirm_button.isEnabled()
            window.local_voice_widget.confirm_button.click();wait(lambda:not coordinator.busy)
            coordinator.poll();app.processEvents()
            assert len(transport.calls)==1
            assert 'Máy chủ thư đã chấp nhận' in window.local_voice_widget.status.text()
            window.showNormal();window.resize(1366,900);app.processEvents()
            assert window.grab().save('docs/test-reports/local_rules/ui-smoke.png')
            window.close();app.processEvents()
            assert not window.local_voice_controller.process.is_alive()
            print('PASS full dashboard with fake camera, fake audio module, fake final transcript, fake SMTP adapter; real business DB/service in temporary directory.')
            print('Live microphone, spoken STT quality, camera counting and live SMTP remain untested.')

if __name__=='__main__':main()
