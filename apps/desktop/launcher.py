"""
Process and Module Launcher for AI_for_older MVP.
Manages subprocess lifecycles, active instances, and camera resource arbitration.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Dict, Optional

from shared.config import PYTHON_EXE

logger = logging.getLogger("Launcher")

# Modules that require exclusive webcam access
CAMERA_BOUND_MODULES = {"face", "emotion", "rehabilitation", "gesture"}


class ModuleLauncher:
    """Manages external processes and prevents camera resource collisions."""

    def __init__(self) -> None:
        self.active_processes: Dict[str, subprocess.Popen] = {}
        self.active_camera_module: Optional[str] = None

    def launch(
        self,
        module_name: str,
        cmd: list[str],
        cwd: str,
        is_camera_bound: bool = False,
    ) -> tuple[bool, str]:
        """
        Launch a module process safely.
        
        Returns:
            (success, message)
        """
        # If already running
        if self.is_running(module_name):
            return True, f"Module '{module_name}' đang chạy."

        # Camera collision check
        if is_camera_bound:
            if self.active_camera_module and self.is_running(self.active_camera_module):
                msg = (
                    f"Camera đang được sử dụng bởi module: {self.active_camera_module}. "
                    f"Vui lòng đóng module đó trước khi mở {module_name}."
                )
                logger.warning(msg)
                return False, msg

        try:
            if cmd and cmd[0] in ("python", "python3"):
                cmd = [sys.executable] + cmd[1:]
            logger.info("Launching [%s] with command: %s in %s", module_name, cmd, cwd)
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.active_processes[module_name] = proc
            if is_camera_bound:
                self.active_camera_module = module_name
            return True, f"Khởi động '{module_name}' thành công."
        except Exception as e:
            err_msg = f"Không thể khởi động '{module_name}': {e}"
            logger.error(err_msg)
            return False, err_msg

    def stop(self, module_name: str) -> None:
        """Terminate a specific running process."""
        proc = self.active_processes.get(module_name)
        if proc and proc.poll() is None:
            logger.info("Stopping module [%s] (PID: %d)...", module_name, proc.pid)
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as e:
                logger.error("Error killing module [%s]: %s", module_name, e)
            finally:
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

        self.active_processes.pop(module_name, None)
        if self.active_camera_module == module_name:
            self.active_camera_module = None

    def stop_all(self) -> None:
        """Stop all managed running processes upon application exit."""
        modules = list(self.active_processes.keys())
        for mod in modules:
            self.stop(mod)

    def is_running(self, module_name: str) -> bool:
        """Check if process is active and running."""
        proc = self.active_processes.get(module_name)
        if proc is None:
            return False
        if proc.poll() is not None:
            # Clean up exited process
            self.active_processes.pop(module_name, None)
            if self.active_camera_module == module_name:
                self.active_camera_module = None
            return False
        return True

    def get_camera_holder(self) -> Optional[str]:
        """Return the module currently using the camera, if any."""
        if self.active_camera_module and self.is_running(self.active_camera_module):
            return self.active_camera_module
        self.active_camera_module = None
        return None
