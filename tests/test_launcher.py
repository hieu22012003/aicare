"""
Unit tests for ModuleLauncher.
Verifies process tracking, camera collision avoidance, and graceful termination.
"""

from __future__ import annotations

import sys
import unittest

from shared import config
from apps.desktop.launcher import ModuleLauncher


class TestModuleLauncher(unittest.TestCase):
    def setUp(self):
        self.launcher = ModuleLauncher()

    def tearDown(self):
        self.launcher.stop_all()

    def test_launch_and_stop(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(5)"]
        success, msg = self.launcher.launch("test_sleep", cmd, cwd=".", is_camera_bound=False)
        self.assertTrue(success)
        self.assertTrue(self.launcher.is_running("test_sleep"))

        self.launcher.stop("test_sleep")
        self.assertFalse(self.launcher.is_running("test_sleep"))

    def test_camera_collision_avoidance(self):
        cmd1 = [sys.executable, "-c", "import time; time.sleep(5)"]
        cmd2 = [sys.executable, "-c", "import time; time.sleep(5)"]

        # First camera process
        ok1, _ = self.launcher.launch("cam_proc1", cmd1, cwd=".", is_camera_bound=True)
        self.assertTrue(ok1)
        self.assertEqual(self.launcher.get_camera_holder(), "cam_proc1")

        # Second camera process attempt should be rejected
        ok2, msg2 = self.launcher.launch("cam_proc2", cmd2, cwd=".", is_camera_bound=True)
        self.assertFalse(ok2)
        self.assertIn("Camera đang được sử dụng", msg2)

        # Stop first, now second can acquire
        self.launcher.stop("cam_proc1")
        ok3, _ = self.launcher.launch("cam_proc2", cmd2, cwd=".", is_camera_bound=True)
        self.assertTrue(ok3)
        self.assertEqual(self.launcher.get_camera_holder(), "cam_proc2")


if __name__ == "__main__":
    unittest.main()
