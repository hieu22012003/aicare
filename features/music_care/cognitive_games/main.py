"""
Top-level entry point for Cognitive Games & Relaxation Module.
Allows running:
    python features/music_care/cognitive_games/main.py
"""

import sys
from pathlib import Path

# Ensure workspace root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.music_care.cognitive_games.ui.main_app import run_app

if __name__ == "__main__":
    run_app()
