# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification for AI REHABILITATION & SAFETY ASSISTANT.
Compiles a standalone, highly-optimized Windows application bundle with all
OpenCV DNN models, PyTorch ExpressionNet weights, MediaPipe Pose & FaceMesh pipelines,
and PyQt5 UI resources included in ONEDIR mode.
"""

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Build-time discovery must not import Qt before MediaPipe or execute every
# optional ML subpackage. Runtime initialization is handled by the existing hook.
import runpy
runpy.run_path(str(Path(SPECPATH) / 'windows_dependency_scan.py'))['configure']()

# SPECPATH is the directory where the .spec file lives
SPEC_DIR = Path(os.path.abspath(SPECPATH))
if SPEC_DIR.name == "packaging":
    PROJECT_ROOT = SPEC_DIR.parent
else:
    PROJECT_ROOT = SPEC_DIR

# Determine build mode (Debug with console vs Release windowed)
DEBUG_BUILD = os.environ.get("AI_CARE_BUILD_DEBUG", "0") == "1"
APP_NAME = "AI_Rehabilitation_Assistant_DEBUG" if DEBUG_BUILD else "AI_Rehabilitation_Assistant"

# Collect MediaPipe data files (binary graphs, tflite models)
mediapipe_datas = collect_data_files('mediapipe')

# Project assets and models to bundle (read-only)
datas = [
    # Static model binaries
    (str(PROJECT_ROOT / 'modules' / 'face' / 'models'), os.path.join('modules', 'face', 'models')),
    (str(PROJECT_ROOT / 'modules' / 'emotion' / 'models'), os.path.join('modules', 'emotion', 'models')),
    # Baseline shared structures & default data templates
    # Configuration template
    (str(PROJECT_ROOT / '.env.example'), '.'),
]

# Assets if present
open_cv_png = PROJECT_ROOT / 'modules' / 'face' / 'open-cv.png'
if open_cv_png.exists():
    datas.append((str(open_cv_png), os.path.join('modules', 'face')))

datas.extend(mediapipe_datas)
datas.extend(collect_data_files('tzdata'))

# Comprehensive hidden imports
hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.sip',
    'cv2',
    'cv2.face',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
    'PIL.ImageFont',
    'torch',
    'torch.nn',
    'torch.nn.functional',
    'torchvision',
    'mediapipe',
    'mediapipe.python',
    'mediapipe.python.solutions',
    'mediapipe.python.solutions.pose',
    'mediapipe.python.solutions.face_mesh',
    'mediapipe.python.solutions.drawing_utils',
    'google.protobuf',
    'google.protobuf.symbol_database',
    'numpy',
    'dotenv',
    'smtplib',
    'email.mime.text',
    'email.mime.multipart',
    'email.mime.image',
    'email.mime.base',
    'psutil',
    'requests',
    # Project internal packages
    'config',
    'core',
    'shared.camera.camera_manager',
    'shared.camera.frame_source',
    'scipy.optimize',
    'scipy.optimize._lsap',
    'shared.camera.drawing_utils',
    'adapters',
    'features.face_monitoring.face_adapter',
    'features.emotion.emotion_adapter',
    'features.exercise_rehab.rehabilitation_adapter',
    'services',
    'features.medication.medication_service',
    'apps.desktop.medication_panel',
    'sqlite3',
    'zoneinfo',
    'tzdata',
    'shared.services.user_service',
    'shared.services.email_service',
    'shared.services.state_service',
    'modules',
    'features.exercise_rehab',
    'features.exercise_rehab.rehab_engine',
    'features.emotion.model',
    'features.emotion.model.main',
    'features.emotion.model.src',
    'features.emotion.model.src.config',
    'features.emotion.model.src.model',
    'features.emotion.model.utils',
    'features.emotion.model.utils.preprocess',
    'app',
    'apps.desktop.dashboard',
    'apps.desktop.router',
    'apps.desktop.state',
    'apps.desktop.registration_dialog',
]
# Explicitly place mediapipe's opencv_world3410.dll in root directory
mp_opencv_dll = PROJECT_ROOT / '.venv' / 'Lib' / 'site-packages' / 'mediapipe' / 'python' / 'opencv_world3410.dll'
binaries = [(str(mp_opencv_dll), '.')] if mp_opencv_dll.exists() else []

a = Analysis(
    [str(PROJECT_ROOT / 'apps/desktop/main.py')],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / 'modules' / 'emotion')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['Agg']}},
    runtime_hooks=[str(SPEC_DIR / 'rthook_mediapipe_dll.py')],
    excludes=[
        'tkinter', 'IPython', 'notebook', 'pytest', 'ultralytics', 'vosk', 'pyttsx3',
        'sentencepiece', 'jax', 'jaxlib', 'tensorflow', 'tensorboard',
        'pandas', 'sklearn', 'scikit-learn'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX disabled for native DLL stability
    console=DEBUG_BUILD,  # Console visible for DEBUG, hidden for RELEASE
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
