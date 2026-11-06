"""
App package for AI_for_older PyQt5 Dashboard.
"""

# Pre-initialize mediapipe and torch on Windows before Qt to prevent C-runtime DLL collision (WinError 1114)
try:
    import mediapipe
except Exception:
    pass

try:
    import torch
except Exception:
    pass

from .dashboard import DashboardWindow
from .launcher import ModuleLauncher
from .router import DashboardRouter
from .state import AppState

__all__ = [
    "DashboardWindow",
    "ModuleLauncher",
    "DashboardRouter",
    "AppState",
]
