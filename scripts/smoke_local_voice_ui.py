"""Isolated dashboard lifecycle smoke, physical camera blocked, no production DB."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['AI_CARE_TEST_MODE'] = '1'
    os.environ['AI_CARE_DISABLE_TTS'] = '1'
    os.environ['EMAIL_ENABLED'] = 'false'
    with tempfile.TemporaryDirectory(prefix='local-voice-ui-') as data:
        os.environ['AI_CARE_TEST_DATA_DIR'] = data
        from unittest.mock import patch
        from PyQt5.QtWidgets import QApplication
        from shared.database.connection import UnifiedDatabaseManager
        from shared.database.migrator import Migrator
        from shared.database.importer import DataImporter
        from shared.camera.camera_manager import CameraManager
        from apps.desktop.dashboard import DashboardWindow
        import time
        db = UnifiedDatabaseManager()
        Migrator(db).apply_all()
        DataImporter(db).import_all()
        app = QApplication([])
        with patch.object(CameraManager, 'start_camera', return_value=True), patch('smtplib.SMTP', side_effect=AssertionError('SMTP forbidden')):
            for enabled in ('false', 'true'):
                os.environ['VOICE_AI_ENABLED'] = enabled
                window = DashboardWindow()
                assert window.btn_start.isEnabled()
                assert not window.local_voice_widget.isHidden()
                assert 'NHẬN GIỌNG NÓI' in window.local_voice_widget.micro.text()
                if enabled == 'true':
                    c = window.local_voice_controller
                    end = time.monotonic() + 15
                    while c.state == 'loading' and time.monotonic() < end:
                        app.processEvents()
                        time.sleep(.02)
                    assert c.state in ('idle', 'unavailable'), c.state
                    print('voice enabled:', c.state)
                    window._on_patient_registered({'patient_id': '1', 'name': 'Sample'})
                    captured = window.voice_dispatcher.snapshot()
                    assert captured is not None
                    plan = next(p for p in captured['plans'] if p['exercise_id'] == window.active_exercise_id and p['is_active'])
                    window._on_start_exercise(expected_plan=plan)
                    window._on_pause_exercise()
                    assert window.router.rehab_adapter.engine.is_paused
                    window._on_pause_exercise()
                    assert not window.router.rehab_adapter.engine.is_paused
                    assert window.btn_stop.isEnabled()
                else:
                    assert window.local_voice_controller is None
                    assert window.local_voice_widget.micro.isEnabled()
                    assert not window.local_voice_widget.stop.isEnabled()
                    window.local_voice_widget.micro.click()
                    assert window.local_voice_controller is not None
                    assert window.local_voice_controller.session is None
                    c = window.local_voice_controller
                window.showNormal()
                window.resize(1366, 900)
                app.processEvents()
                if enabled == 'true':
                    assert window.grab().save(str(Path('docs/test-reports/local_stt/ui-visible.png').resolve()))
                window.close()
                app.processEvents()
                assert not c.process.is_alive()
                print('dashboard closed:', enabled)


if __name__ == '__main__':
    main()
