"""Documented, deterministic patch for MediaPipe 0.10.20 Windows resources.

Retains upstream license and changes only native resource path spelling.
The portable entry point sets CWD to app before any graph is constructed.
"""
from pathlib import Path
import hashlib
import json

ROOT_LINE = '    root_path = os.sep.join(os.path.abspath(__file__).split(os.sep)[:-3])\n'
MARKER = '    # AICare portable: native Windows file readers require ASCII paths.\n'
PATCH = (MARKER +
         "    if os.name == 'nt' and not root_path.isascii():\n" +
         '      relative_root = os.path.relpath(root_path)\n' +
         '      if relative_root.isascii():\n' +
         '        root_path = relative_root\n')


def patch(package):
    path = package/'runtime/Lib/site-packages/mediapipe/python/solution_base.py'
    text = path.read_text(encoding='utf-8')
    if MARKER not in text:
        if text.count(ROOT_LINE) != 1:
            raise RuntimeError('Unsupported MediaPipe source; review resource patch: '+str(path))
        text = text.replace(ROOT_LINE, ROOT_LINE+PATCH)
        temp = path.with_suffix('.py.tmp')
        temp.write_text(text,encoding='utf-8')
        temp.replace(path)  # Do not mutate runtime files hard-linked to older releases.
    info = {'mediapipe':'0.10.20', 'change':'ASCII relative native resource paths on Windows',
            'file':path.relative_to(package).as_posix(),
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (package/'PORTABLE_COMPAT.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    return info
