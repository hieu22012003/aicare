"""Avoid importing every ML subpackage during PyInstaller's Windows DLL scan.

PyInstaller imports Qt first during this phase, which conflicts with this project's
MediaPipe initialization order and can stall imports of optional ML packages.
Hooks still collect all binaries; PE dependency analysis is unchanged. Supply the
known native directories explicitly without executing optional subpackages.
This affects the build process only, not the frozen application.
"""
from pathlib import Path
import sys


def configure():
    if sys.platform != 'win32':
        return
    from PyInstaller.building import build_main
    from PyInstaller.depend import bindepend
    from PyInstaller import compat

    original = build_main.find_binary_dependencies
    site = Path(sys.prefix)/'Lib'/'site-packages'
    paths = [site/'torch'/'lib', site/'mediapipe'/'python', site/'numpy.libs',
             site/'scipy.libs', site/'cv2', site/'PyQt5'/'Qt5'/'bin']
    native_paths = [str(p) for p in paths if p.is_dir()]

    def scan(binaries, import_packages, symlink_suppression_patterns):
        # Analyze PE imports using explicit paths. Do not execute application or
        # optional ML subpackages as part of DLL discovery.
        search = [compat.base_prefix,
                  str(Path(getattr(sys,'_base_executable',sys.executable)).parent),
                  *native_paths]
        build_main.logger.info('Project Windows DLL scan: explicit native directories %r',search)
        return bindepend.binary_dependency_analysis(
            binaries, search_paths=search,
            symlink_suppression_patterns=symlink_suppression_patterns)

    build_main.find_binary_dependencies = scan
    return original
