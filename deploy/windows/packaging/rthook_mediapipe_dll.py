"""
PyInstaller Runtime Hook: Windows DLL Initialization Order for MediaPipe & PyTorch.

On Windows, MediaPipe's C++ extension (_framework_bindings.pyd) links to opencv_world3410.dll.
If PyQt5 is initialized BEFORE MediaPipe, the Windows C-runtime / graphics DLL state causes
_framework_bindings.pyd initialization routine to fail with WinError 1114.

Fix:
1. Register both _internal and _internal/mediapipe/python in the Windows DLL search path.
2. Pre-load opencv_world3410.dll.
3. Pre-import mediapipe.
4. Pre-import torch.
All BEFORE PyQt5 or GUI initialization runs.
"""
import os
import sys
import ctypes
os.environ.setdefault('MPLBACKEND', 'Agg')

def _init_windows_dlls():
    if not getattr(sys, 'frozen', False):
        return

    meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    mp_python_dir = os.path.join(meipass, 'mediapipe', 'python')

    # 1. Register DLL directories
    for d in [meipass, mp_python_dir]:
        if os.path.isdir(d):
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
            current_path = os.environ.get('PATH', '')
            if d not in current_path:
                os.environ['PATH'] = d + os.pathsep + current_path

    # 2. Pre-load opencv_world3410.dll
    for loc in [
        os.path.join(meipass, 'opencv_world3410.dll'),
        os.path.join(mp_python_dir, 'opencv_world3410.dll'),
    ]:
        if os.path.exists(loc):
            try:
                ctypes.WinDLL(loc)
            except Exception:
                pass
            break

    # 3. Pre-import mediapipe & torch before PyQt5
    try:
        import mediapipe
    except Exception as e:
        sys.stderr.write(f"[RTHOOK] mediapipe pre-import note: {e}\n")

    try:
        import torch
    except Exception as e:
        sys.stderr.write(f"[RTHOOK] torch pre-import note: {e}\n")

_init_windows_dlls()
