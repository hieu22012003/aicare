"""
Unit tests for decoupled Face Recognition & Patient Registration system.
"""

from shared import config
import sys
import unittest
import numpy as np
import cv2
from pathlib import Path
from PyQt5.QtWidgets import QApplication

from features.face_monitoring.face_adapter import FaceAdapter
from apps.desktop.registration_dialog import RegistrationDialog, PatientManagementDialog

# Ensure single QApplication instance for Qt tests
app = QApplication.instance() or QApplication(sys.argv)


class TestFaceRegistrationDecoupled(unittest.TestCase):
    """Test full separation of Face Recognition and User Registration."""

    def setUp(self):
        self.adapter = FaceAdapter()

    def test_seeded_patients_recognition(self):
        """Test recognition of seeded known faces vs unknown faces."""
        face1_path = Path("shared/data/registered_faces/1/face_01.jpg")
        if face1_path.exists():
            img1 = cv2.imread(str(face1_path))
            rec1 = self.adapter.recognize_face(img1)
            self.assertEqual(rec1["status"], "recognized")
            self.assertEqual(rec1["patient_id"], "1")
            self.assertTrue(rec1["confidence"] > 0.60)
            self.assertIsNotNone(rec1["profile"])

    def test_unknown_face_classification(self):
        """Ensure an unknown or blank image does not get misidentified as a registered user."""
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        rec_blank = self.adapter.recognize_face(blank)
        self.assertEqual(rec_blank["status"], "no_face")
        self.assertIsNone(rec_blank["patient_id"])

    def test_dynamic_patient_registration_cycle(self):
        """Test enrolling a new user, saving face images, and retraining."""
        test_id = "test_999"
        test_name = "Người Thử Nghiệm"

        # Create synthetic face image
        test_face = np.full((120, 120, 3), 180, dtype=np.uint8)
        # Add high-contrast feature
        cv2.circle(test_face, (40, 40), 10, (20, 20, 20), -1)
        cv2.circle(test_face, (80, 40), 10, (20, 20, 20), -1)
        cv2.ellipse(test_face, (60, 80), (30, 15), 0, 0, 180, (20, 20, 20), 4)

        # Register
        profile = self.adapter.register_patient(
            name=test_name,
            patient_id=test_id,
            age=72,
            gender="Nam",
            notes="Kiểm tra hệ thống đăng ký độc lập",
            face_images=[test_face],
        )

        self.assertEqual(profile["patient_id"], test_id)
        self.assertEqual(profile["name"], test_name)

        # Verify retrieval
        p_info = self.adapter.get_patient_profile(test_id)
        self.assertIsNotNone(p_info)
        self.assertEqual(p_info["name"], test_name)

        # Clean up test user
        deleted = self.adapter.delete_patient(test_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.adapter.get_patient_profile(test_id))

    def test_dialog_instantiation(self):
        """Verify RegistrationDialog and PatientManagementDialog can instantiate cleanly."""
        reg_dlg = RegistrationDialog(self.adapter)
        self.assertIsNotNone(reg_dlg)
        self.assertTrue(len(reg_dlg.edit_id.text()) > 0)
        self.assertIsNotNone(reg_dlg.edit_name)
        self.assertIsNotNone(reg_dlg.spin_age)
        self.assertIsNotNone(reg_dlg.combo_gender)
        reg_dlg.close()

        # Ensure at least 1 patient exists for table rendering verification
        if not self.adapter.get_all_patients():
            self.adapter.register_patient("Bác An", patient_id="seed_01", age=75)

        mgr_dlg = PatientManagementDialog(self.adapter)
        self.assertIsNotNone(mgr_dlg)
        self.assertTrue(mgr_dlg.table.rowCount() >= 1)
        mgr_dlg.close()


if __name__ == "__main__":
    unittest.main()
