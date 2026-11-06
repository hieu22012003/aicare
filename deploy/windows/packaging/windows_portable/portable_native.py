"""Native startup shared by the supervisor, desktop and offline diagnostic.

Only stdlib imports here: this must run before any C++ extension is imported.
"""
from pathlib import Path
import ctypes
import hashlib
import json
import os
import sys

_HANDLES = []
_READY = False


def bootstrap():
    global _READY
    if _READY or os.name != 'nt':
        return
    runtime = Path(sys.executable).resolve().parent
    manifest = runtime / 'vc-runtime.json'
    if not manifest.is_file():
        raise RuntimeError('Portable runtime incomplete: missing runtime/vc-runtime.json. '
                           'Extract the entire corrected ZIP again.')
    info = json.loads(manifest.read_text(encoding='utf-8'))
    for name, expected in info['files'].items():
        path = runtime / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Portable VC runtime missing or damaged: ' + name)
    _HANDLES.append(os.add_dll_directory(str(runtime)))
    # Load a coherent CRT from this package before MediaPipe, Torch, Qt or ONNX.
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll',
                 'msvcp140_1.dll', 'msvcp140_2.dll', 'msvcp140_atomic_wait.dll',
                 'msvcp140_codecvt_ids.dll', 'concrt140.dll', 'vcomp140.dll'):
        _HANDLES.append(ctypes.WinDLL(str(runtime / name)))
    _READY = True


def loaded_modules():
    """Use pointer-sized signatures; works in an x64 embedded interpreter."""
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.K32EnumProcessModules.argtypes = (w.HANDLE, ctypes.POINTER(w.HMODULE),
                                            w.DWORD, ctypes.POINTER(w.DWORD))
    kernel.GetModuleFileNameW.argtypes = (w.HMODULE, w.LPWSTR, w.DWORD)
    modules = (w.HMODULE * 4096)()
    needed = w.DWORD()
    if not kernel.K32EnumProcessModules(kernel.GetCurrentProcess(), modules,
                                       ctypes.sizeof(modules), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    if needed.value > ctypes.sizeof(modules):
        raise RuntimeError('Module inventory overflow')
    paths = []
    for handle in modules[:needed.value // ctypes.sizeof(w.HMODULE)]:
        buf = ctypes.create_unicode_buffer(32768)
        if not kernel.GetModuleFileNameW(handle, buf, len(buf)):
            raise ctypes.WinError(ctypes.get_last_error())
        paths.append(Path(buf.value).resolve())
    return paths


def audit_loaded():
    runtime = Path(sys.executable).resolve().parent
    windows = Path(os.environ['SystemRoot']).resolve()
    paths = loaded_modules()
    # Windows Defender injects this OS security module on normal Windows hosts.
    defender = Path(os.environ.get('ProgramData', 'C:/ProgramData'))/'Microsoft/Windows Defender/Platform'
    injected = [p for p in paths if p.name.lower() == 'mpoav.dll'
                and p.is_relative_to(defender.resolve())]
    external = [str(p) for p in paths if not p.is_relative_to(runtime)
                and not p.is_relative_to(windows) and p not in injected]
    crt = [p for p in paths if p.name.lower().startswith(
        ('msvcp140', 'vcruntime140', 'concrt140', 'vcomp140'))]
    external_crt = [str(p) for p in crt if not p.is_relative_to(runtime)]
    if external or external_crt:
        raise RuntimeError('Nonportable native dependencies: ' +
                           json.dumps(external + external_crt))
    return {'loaded_dlls': [str(p.relative_to(runtime)) for p in paths
                            if p.is_relative_to(runtime)],
            'app_local_crt': [str(p.relative_to(runtime)) for p in crt],
            'windows_defender_injected': [str(p) for p in injected],
            'external_non_windows_modules': external}
