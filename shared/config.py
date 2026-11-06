"""
Central Configuration for AI_for_older MVP Integration.
Provides directory paths, runtime settings, thresholds, and logging configuration.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Environment flags for Windows compatibility & MediaPipe Protobuf
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
import tempfile
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))

# Windows needs this import order for C-runtime / OpenMP DLL collisions (WinError 1114).
# Backend configuration on Linux must not initialize camera/audio or load Torch.
if sys.platform == 'win32':
    try:
        import mediapipe
    except Exception:
        pass
    try:
        import torch
    except Exception:
        pass

# Base paths & PyInstaller frozen bundle detection
if getattr(sys, "frozen", False):
    # Running inside compiled PyInstaller binary (.exe)
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parents[1]
    BASE_DIR = BUNDLE_DIR


def resource_path(relative_path: str | Path) -> Path:
    """
    Resolve absolute path to read-only resource (models, icons, templates).
    Works seamlessly in both normal Python development and PyInstaller frozen execution.
    """
    rel = Path(relative_path)
    # Check inside BUNDLE_DIR first (covers sys._MEIPASS when frozen)
    bundled = BUNDLE_DIR / rel
    if bundled.exists():
        return bundled
    # Fallback to BASE_DIR
    base_loc = BASE_DIR / rel
    if base_loc.exists():
        return base_loc
    return bundled


# Bundled static AI models and assets (read-only), one folder per feature
FEATURES_DIR = BUNDLE_DIR / "features"
FACE_MODULE_DIR = FEATURES_DIR / "face_monitoring" / "standalone"
EMOTION_MODULE_DIR = FEATURES_DIR / "emotion" / "model"
MODULES_DIR = FEATURES_DIR  # legacy name kept for old imports

# Writable application data directory
# In portable release mode, isolate writable data in package-local 'runtime/' directory
# On Windows, when frozen or installed, use %LOCALAPPDATA%\AIRehabilitationAssistant
# In local development mode, fallback to local BASE_DIR
local_app_data = os.environ.get("LOCALAPPDATA")
PORTABLE_RUNTIME_DIR = BASE_DIR / "runtime"

if os.environ.get("AI_CARE_HOME"):
    WRITABLE_ROOT = Path(os.environ["AI_CARE_HOME"]).expanduser().resolve()
    WRITABLE_ROOT.mkdir(parents=True, exist_ok=True)
elif PORTABLE_RUNTIME_DIR.exists() or os.environ.get("PORTABLE_MODE", "0") == "1":
    WRITABLE_ROOT = PORTABLE_RUNTIME_DIR
    PORTABLE_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
elif getattr(sys, "frozen", False) and local_app_data:
    WRITABLE_ROOT = Path(local_app_data) / "AIRehabilitationAssistant"
elif local_app_data and os.environ.get("AI_CARE_USE_LOCALAPPDATA", "0") == "1":
    WRITABLE_ROOT = Path(local_app_data) / "AIRehabilitationAssistant"
else:
    WRITABLE_ROOT = BASE_DIR

# Automated tests and --test use isolated data, even with a real .env present.
TEST_MODE = os.environ.get('AI_CARE_TEST_MODE') == '1' or '--test' in sys.argv
if TEST_MODE:
    WRITABLE_ROOT = Path(os.environ.get('AI_CARE_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='ai-care-test-'))

# Persistent user data directories (written to runtime/ or data/)
if WRITABLE_ROOT == PORTABLE_RUNTIME_DIR:
    DATA_DIR = WRITABLE_ROOT
    USERS_DIR = WRITABLE_ROOT / "users"
    SESSIONS_DIR = WRITABLE_ROOT / "sessions"
    FALL_EVENTS_DIR = WRITABLE_ROOT / "fall_events"
else:
    DATA_DIR = WRITABLE_ROOT / "data"
    USERS_DIR = DATA_DIR / "users"
    SESSIONS_DIR = DATA_DIR / "sessions"
    FALL_EVENTS_DIR = DATA_DIR / "fall_events"


SHARED_DIR = WRITABLE_ROOT / "shared"
SHARED_DATA_DIR = SHARED_DIR / "data"
SHARED_RESULTS_DIR = SHARED_DIR / "results"
SHARED_ALERTS_DIR = SHARED_DIR / "alerts"
SHARED_RECORDINGS_DIR = SHARED_DIR / "recordings"
SHARED_REPORTS_DIR = SHARED_DIR / "reports"
LOGS_DIR = WRITABLE_ROOT / "logs"
MEDIA_DIR = WRITABLE_ROOT / "media"

# Ensure all vital directories exist
for directory in (
    SHARED_DIR,
    SHARED_DATA_DIR,
    SHARED_RESULTS_DIR,
    LOGS_DIR,
    MEDIA_DIR,
    DATA_DIR,
    USERS_DIR,
    SESSIONS_DIR,
    FALL_EVENTS_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)

# Seed initial baseline user files if running from external writable directory
if WRITABLE_ROOT != BASE_DIR and not TEST_MODE:
    import shutil
    seed_shared = BUNDLE_DIR / "shared" / "data"
    if seed_shared.exists():
        for item in seed_shared.glob("*.*"):
            dest_file = SHARED_DATA_DIR / item.name
            if not dest_file.exists():
                try:
                    shutil.copy2(str(item), str(dest_file))
                except Exception:
                    pass
    seed_users = BUNDLE_DIR / "data" / "users"
    if seed_users.exists():
        for item in seed_users.glob("*.json"):
            dest_file = USERS_DIR / item.name
            if not dest_file.exists():
                try:
                    shutil.copy2(str(item), str(dest_file))
                except Exception:
                    pass

# Load configuration from .env
env_file = WRITABLE_ROOT / ".env"
if not env_file.exists():
    env_file = BASE_DIR / ".env"
load_dotenv(env_file)

MEDICATION_CATCHUP_MINUTES = int(os.getenv('MEDICATION_CATCHUP_MINUTES', '60'))
MEDICATION_ALERT_LIMIT = int(os.getenv('MEDICATION_ALERT_LIMIT', '3'))

# Legacy constants retained only for historical fall tools; no active runtime consumers.
FALL_CONFIRM_SECONDS = float(os.getenv("FALL_CONFIRM_SECONDS", "3.0"))
FALL_CONFIDENCE_THRESHOLD = float(os.getenv("FALL_CONFIDENCE_THRESHOLD", "0.75"))
FALL_COOLDOWN_SECONDS = float(os.getenv("FALL_COOLDOWN_SECONDS", "30.0"))

# Email Alert Configuration (Caregiver notifications)
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "true").lower() in ("true", "1", "yes")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com").strip()
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USERNAME = os.getenv("EMAIL_USERNAME", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "").strip()
DEFAULT_CAREGIVER_EMAIL = (os.getenv("DEFAULT_CAREGIVER_EMAIL") or os.getenv("CAREGIVER_EMAIL") or "").strip()
EMAIL_RETRY_MAX = int(os.getenv("EMAIL_RETRY_MAX", "3"))
EMAIL_RETRY_DELAY = float(os.getenv("EMAIL_RETRY_DELAY", "3.0"))

# Python runtime executable: prefer project venv
if sys.platform == "win32":
    VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
else:
    linux_venv = BASE_DIR / ".venv-linux" / "bin" / "python"
    VENV_PYTHON = linux_venv if linux_venv.exists() else (BASE_DIR / ".venv" / "bin" / "python")

if VENV_PYTHON.exists():
    PYTHON_EXE = str(VENV_PYTHON)
else:
    PYTHON_EXE = sys.executable

# Camera settings
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "640"))
FRAME_HEIGHT = int(os.getenv("FRAME_HEIGHT", "480"))
FPS_TARGET = int(os.getenv("FPS_TARGET", "30"))

# Default User / Patient configuration
DEFAULT_PATIENT_ID = "patient_001"
DEFAULT_PATIENT_NAME = "tonghoanganh"

# Rehabilitation Exercise Configurations
EXERCISES = {
    "arm_raise": {
        "id": "arm_raise",
        "name": "Vươn vai (Nâng tay qua đầu)",
        "name_short": "Nâng tay qua đầu",
        "target_reps": 10,
        "instructions": "Nâng hai tay thẳng lên qua vai, giữ 1 nhịp trên cao rồi hạ đều xuống.",
    },
    "sit_to_stand": {
        "id": "sit_to_stand",
        "name": "Co duỗi chân (Đứng lên ngồi xuống)",
        "name_short": "Đứng lên ngồi xuống",
        "target_reps": 10,
        "instructions": "Ngồi gập gối vuông góc, sau đó đứng thẳng hoàn toàn rồi ngồi xuống chậm rãi.",
    },
    "marching": {
        "id": "marching",
        "name": "Đi bộ tại chỗ (Nâng cao đùi)",
        "name_short": "Nâng cao đùi tại chỗ",
        "target_reps": 15,
        "instructions": "Nâng luân phiên đùi trái và đùi phải lên ngang hông, giữ thẳng người.",
    },
}

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Setup standard logging
LOG_FILE = LOGS_DIR / "app.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("AI_Rehab_Assistant")
