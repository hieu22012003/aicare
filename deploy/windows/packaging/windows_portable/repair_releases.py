"""Stage corrected PT/HP/V3 packages without changing their UI or user data."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import os
import shutil
from patch_mediapipe import patch as patch_mediapipe

ROOT = Path(__file__).resolve().parents[4]
TEMPLATES = Path(__file__).resolve().parent


def stage(edition):
    source = ROOT / ('releases/portable-v3/V3/AICare' if edition == 'V3'
                     else f'releases/portable-zipformer/{edition}/AICare')
    dest = ROOT / f'releases/portable-nativefix/{edition}/AICare'
    if dest.exists():
        raise RuntimeError('Refusing to overwrite ' + str(dest))

    def copy(src, dst):
        if Path(src).is_relative_to(source / 'runtime'):
            try:
                os.link(src, dst)
                return dst
            except OSError:
                pass
        return shutil.copy2(src, dst)

    shutil.copytree(source, dest, copy_function=copy, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', '*.pyo', 'QA_REPORT.txt', 'SHA256SUMS.json'))
    for name in ('portable_entry.py', 'portable_native.py'):
        shutil.copy2(TEMPLATES / name, dest / 'app' / name)
    shutil.copy2(ROOT/'features/voice_assistant/local_voice/engine.py',dest/'apps/desktop/services/local_voice/engine.py')
    (dest / 'qa').mkdir(exist_ok=True)
    shutil.copy2(TEMPLATES / 'qa.py', dest / 'qa/acceptance.py')
    shutil.copy2(TEMPLATES / 'security_regression.py', dest / 'qa/security_regression.py')
    shutil.copy2(TEMPLATES / 'clean_machine_test.ps1', dest / 'qa/clean_machine_test.ps1')
    shutil.copy2(ROOT / 'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09/test_wavs/0.wav',
                 dest / 'qa/speech.wav')
    for path in (ROOT / 'runtime/portable-build-cache/vc-runtime').iterdir():
        target = dest / 'runtime' / path.name
        temp = target.with_suffix(target.suffix + '.tmp')
        shutil.copy2(path, temp)
        temp.replace(target)
    patch_mediapipe(dest)
    version_path = dest / 'VERSION.json'
    version = json.loads(version_path.read_text(encoding='utf-8'))
    version.update(version='6.0.0-nativefix.2',
                   build_utc=datetime.now(timezone.utc).isoformat(),
                   app_local_crt='14.44.35211', based_on=str(source.relative_to(ROOT)))
    version_path.write_text(json.dumps(version, indent=2), encoding='utf-8')
    (dest / 'CHANGELOG.txt').write_text(
        'Native runtime fix: bundle signed Microsoft x64 CRT beside python.exe; '
        'validate hashes and load before native extensions in both processes.\n', encoding='utf-8')
    (dest / 'HUONG_DAN.txt').write_text(
        f'AI CARE PORTABLE {edition} — 6.0.0-nativefix.2\n\n'
        'Windows 10/11 x64. Giải nén toàn bộ ZIP rồi chạy AICare.exe.\n'
        'Không cần cài Python, pip hay Visual C++ Redistributable. Không cần quyền admin.\n'
        'Model cục bộ đã có trong ZIP. Email/cloud tùy chọn cần mạng và cấu hình.\n'
        'Dữ liệu: %LOCALAPPDATA%\\AICareDemo\\' + edition + '\n'
        'Cho phép camera/micro trong Windows. Windows N cần Media Feature Pack của Windows.\n'
        'Đọc QA_REPORT.txt để biết phạm vi kiểm thử và giới hạn.\n', encoding='utf-8-sig')
    print(dest, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('editions', nargs='*', default=['PT', 'HP', 'V3'])
    for edition in parser.parse_args().editions:
        stage(edition)
