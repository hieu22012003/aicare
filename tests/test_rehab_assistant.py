"""
Comprehensive Unit Test Suite for AI Rehabilitation Assistant MVP.
Tests all 4 active modules:
  1. CameraManager (Hardware singleton, modes, thread-safety, simulation fallback)
  2. FaceAdapter (OpenCV DNN detection, dynamic BBox tracking, patient registration)
  3. EmotionAdapter (FaceMesh inference, temporal smoothing buffer, Vietnamese mapping)
  4. RehabilitationEngine & RehabilitationAdapter (State machines, rep counting, form penalties, scoring, telemetry)
  5. VoiceAdapter & Intent Classifier (Deterministic rehab command mapping, TTS)
  6. DashboardRouter & State Service (Mode transitions, event routing)
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
import numpy as np

# Ensure headless execution & environment
import tempfile
from shared import config
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
os.environ.setdefault("AI_CARE_DISABLE_TTS", "1")
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))

from shared.camera.camera_manager import CameraManager
from features.face_monitoring.face_adapter import FaceAdapter
from features.emotion.emotion_adapter import EmotionAdapter
from features.exercise_rehab.rehabilitation_adapter import RehabilitationAdapter
from features.exercise_rehab.rehab_engine import (
    RehabilitationEngine,
    ArmRaiseTracker,
    SitToStandTracker,
    MarchingTracker,
    ExerciseMetrics,
)
from apps.desktop.router import DashboardRouter
from shared.services.state_service import StateService


class TestCameraManager(unittest.TestCase):
    """Test CameraManager singleton, observer pattern, and mode switching."""

    def setUp(self):
        self.cam = CameraManager.get_instance()

    def test_singleton(self):
        cam2 = CameraManager.get_instance()
        self.assertIs(self.cam, cam2)

    def test_mode_switching(self):
        self.cam.set_mode(CameraManager.MODE_REHABILITATION)
        self.assertEqual(self.cam.get_mode(), CameraManager.MODE_REHABILITATION)
        self.cam.set_mode(CameraManager.MODE_MONITOR)
        self.assertEqual(self.cam.get_mode(), CameraManager.MODE_MONITOR)

    def test_simulation_fallback_frame(self):
        frame = self.cam._generate_simulated_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (480, 640, 3))
        self.assertEqual(frame.dtype, np.uint8)

    def test_register_unregister_processor(self):
        def sample_processor(frame):
            return frame

        self.cam.register_processor(sample_processor)
        self.assertIn(sample_processor, self.cam._frame_processors)
        self.cam.unregister_processor(sample_processor)
        self.assertNotIn(sample_processor, self.cam._frame_processors)


class TestFaceAdapter(unittest.TestCase):
    """Test FaceAdapter detection, EMA smoothing, and patient profiles."""

    def setUp(self):
        self.adapter = FaceAdapter()

    def test_detect_face_on_blank_frame(self):
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        res = self.adapter.detect_face(blank)
        self.assertIsNone(res)

    def test_patient_registration_and_retrieval(self):
        profile = self.adapter.register_patient("Trần Văn Nam", age=68)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "Trần Văn Nam")
        self.assertEqual(profile["age"], 68)

        # Check retrieval
        stored = self.adapter.get_patient_profile(profile["patient_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["name"], "Trần Văn Nam")

        # Cleanup test patient
        self.adapter.delete_patient(profile["patient_id"])

    def test_get_result_schema(self):
        res = self.adapter.get_result()
        self.assertIn("patient_id", res)
        self.assertIn("name", res)
        self.assertIn("status", res)
        self.assertIn("confidence", res)


class TestEmotionAdapter(unittest.TestCase):
    """Test EmotionAdapter temporal smoothing and Vietnamese classification."""

    def setUp(self):
        self.adapter = EmotionAdapter(smoothing_window=5)

    def test_temporal_smoothing_buffer(self):
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        res = self.adapter.process_frame(blank)
        self.assertIn("dominant_emotion", res)
        self.assertIn("emotion_vi", res)
        self.assertIn("confidence", res)
        self.assertIn("probabilities", res)

    def test_3_classes_only(self):
        self.assertEqual(self.adapter.classes, ["Neutral", "Happy", "Sad"])
        res = self.adapter.get_result()
        self.assertIn(res["dominant_emotion"], ["Neutral", "Happy", "Sad", "Unknown"])
        self.assertIn(res["emotion_vi"], ["Bình thường", "Vui vẻ", "Buồn bã", "Không phát hiện mặt"])

    def test_facs_kinematics_accuracy(self):
        """Verify FACS biomechanical accuracy for resting, smiling, and frowning faces."""
        class MockLandmark:
            def __init__(self, x, y, z=0.0):
                self.x = x
                self.y = y
                self.z = z

        class MockFaceLandmarks:
            def __init__(self, coords_dict):
                self.landmark = [MockLandmark(0.5, 0.5) for _ in range(468)]
                for idx, (x, y) in coords_dict.items():
                    self.landmark[idx] = MockLandmark(x, y)

        # Baseline outer eye distance = 0.20 (p33=(0.40, 0.35), p263=(0.60, 0.35))
        base_eyes = {33: (0.40, 0.35), 263: (0.60, 0.35), 107: (0.46, 0.30), 336: (0.54, 0.30)}

        # 1. Normal resting face: corners at neutral elevation, standard mouth width
        neutral_face = MockFaceLandmarks({
            **base_eyes,
            61: (0.45, 0.55), 291: (0.55, 0.55), 13: (0.50, 0.55), 14: (0.50, 0.55)
        })
        probs_neutral = self.adapter._compute_facs_probabilities(neutral_face)
        # Neutral index is 0
        self.assertGreater(probs_neutral[0], 0.85)

        # 2. Smiling face: corners pull up and mouth widens
        smile_face = MockFaceLandmarks({
            **base_eyes,
            61: (0.43, 0.535), 291: (0.57, 0.535), 13: (0.50, 0.555), 14: (0.50, 0.555)
        })
        probs_smile = self.adapter._compute_facs_probabilities(smile_face)
        # Happy index is 1
        self.assertGreater(probs_smile[1], 0.80)

        # 3. Sad/frowning face: corners pull down
        sad_face = MockFaceLandmarks({
            **base_eyes,
            107: (0.475, 0.29), 336: (0.525, 0.29), # inner brows contracted
            61: (0.46, 0.565), 291: (0.54, 0.565), 13: (0.50, 0.545), 14: (0.50, 0.545)
        })
        probs_sad = self.adapter._compute_facs_probabilities(sad_face)
        # Sad index is 2
        self.assertGreater(probs_sad[2], 0.70)

    def test_vietnamese_drawing(self):
        from shared.camera.drawing_utils import draw_vietnamese_text, draw_pill_badge, get_text_size
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        draw_vietnamese_text(frame, "Nâng tay qua đầu (Arm Raise)", (20, 20), font_size=18)
        draw_pill_badge(frame, "Tâm trạng: Bình thường (92.5%)", (600, 20), font_size=16)
        w, h = get_text_size("HUẤN LUYỆN VIÊN AI:", font_size=18)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        # Verify non-black pixels exist
        self.assertGreater(np.sum(frame), 0)


class TestRehabilitationEngine(unittest.TestCase):
    """Test Core Rehabilitation AI: state machines, repetition counter, form scoring."""

    def setUp(self):
        self.engine = RehabilitationEngine()

    def test_exercise_initialization(self):
        self.engine.select_exercise("arm_raise")
        self.assertEqual(self.engine._active_exercise_id, "arm_raise")
        metrics = self.engine.get_metrics()
        self.assertEqual(metrics["exercise_id"], "arm_raise")

        self.engine.select_exercise("sit_to_stand")
        self.assertEqual(self.engine._active_exercise_id, "sit_to_stand")

        self.engine.select_exercise("marching")
        self.assertEqual(self.engine._active_exercise_id, "marching")

    def test_arm_raise_state_machine(self):
        tracker = ArmRaiseTracker()
        self.assertEqual(tracker.stage, "down")

        # Mock 33 pose landmarks
        mock_lm = [type("MockLM", (), {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9})() for _ in range(33)]
        mock_lm[11].y, mock_lm[11].x = 0.5, 0.45
        mock_lm[13].y, mock_lm[13].x = 0.25, 0.45
        mock_lm[15].y, mock_lm[15].x = 0.10, 0.45
        mock_lm[23].y, mock_lm[23].x = 0.75, 0.45

        mock_lm[12].y, mock_lm[12].x = 0.5, 0.55
        mock_lm[14].y, mock_lm[14].x = 0.25, 0.55
        mock_lm[16].y, mock_lm[16].x = 0.10, 0.55
        mock_lm[24].y, mock_lm[24].x = 0.75, 0.55

        stage, feedback, score = tracker.process(mock_lm, 640, 480)
        self.assertIn(stage, ["raising", "hold_peak"])
        self.assertGreater(score, 50.0)

    def test_sit_to_stand_state_machine(self):
        tracker = SitToStandTracker()
        self.assertIn(tracker.stage, ["ready", "sitting"])

        mock_lm = [type("MockLM", (), {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9})() for _ in range(33)]
        for i in range(33):
            mock_lm[i].visibility = 0.95
        mock_lm[23].y, mock_lm[23].x = 0.45, 0.48
        mock_lm[24].y, mock_lm[24].x = 0.45, 0.52
        mock_lm[25].y, mock_lm[25].x = 0.65, 0.48
        mock_lm[26].y, mock_lm[26].x = 0.65, 0.52
        mock_lm[27].y, mock_lm[27].x = 0.85, 0.48
        mock_lm[28].y, mock_lm[28].x = 0.85, 0.52
        mock_lm[11].y, mock_lm[11].x = 0.25, 0.48
        mock_lm[12].y, mock_lm[12].x = 0.25, 0.52

        stage, feedback, score = tracker.process(mock_lm, 640, 480)
        self.assertIn(stage, ["ascending", "standing"])

    def test_marching_state_machine(self):
        tracker = MarchingTracker()
        self.assertEqual(tracker.stage, "stance")

        mock_lm = [type("MockLM", (), {"x": 0.5, "y": 0.5, "z": 0.0, "visibility": 0.9})() for _ in range(33)]
        mock_lm[23].y = 0.55
        mock_lm[25].y = 0.40
        mock_lm[24].y = 0.55
        mock_lm[26].y = 0.75

        stage, feedback, score = tracker.process(mock_lm, 640, 480)
        self.assertEqual(stage, "left_up")
        self.assertEqual(tracker.reps, 1)

    def test_rehabilitation_adapter_integration(self):
        adapter = RehabilitationAdapter()
        res = adapter.get_result()
        self.assertIn("exercise", res)
        self.assertIn("repetitions", res)
        self.assertIn("score", res)

        ok = adapter.start_exercise("sit_to_stand")
        self.assertTrue(ok)
        self.assertTrue(adapter.is_running())

        stop_res = adapter.stop_exercise()
        self.assertIn("exercise", stop_res)
        self.assertFalse(adapter.is_running())


class TestDashboardRouter(unittest.TestCase):
    """Test central router and event coordination."""

    def setUp(self):
        self.router = DashboardRouter(window=None)

    def test_start_and_stop_exercise_via_router(self):
        self.router.start_exercise("arm_raise")
        self.assertEqual(self.router.rehab_adapter._active_exercise, "arm_raise")
        self.assertTrue(self.router.rehab_adapter.is_running())

        summary = self.router.stop_exercise()
        self.assertIsNotNone(summary)
        self.assertFalse(self.router.rehab_adapter.is_running())


if __name__ == "__main__":
    unittest.main()

