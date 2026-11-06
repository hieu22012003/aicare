"""Build an additional charcoal/mint edition without changing PT or HP."""
from pathlib import Path
import ast
import colorsys
from datetime import datetime, timezone
import json
import os
import re
import shutil
from patch_mediapipe import patch as patch_mediapipe

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / 'releases/portable-v3/V3/AICare'
HEX = re.compile(r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b')


def color(match):
    raw = match.group()[1:]
    if len(raw) == 3:
        raw = ''.join(c * 2 for c in raw)
    suffix = raw[6:]
    r, g, b = [int(raw[i:i+2], 16) / 255 for i in (0, 2, 4)]
    h, light, saturation = colorsys.rgb_to_hls(r, g, b)
    # Preserve recognizable danger/warning states on dark surfaces.
    if saturation > .35 and (h < .13 or h > .93):
        result = ('#442b2d' if light > .8 else '#f3b966' if h > .055 else '#ff8f9f')
    elif light > .97:
        result = '#242827'
    elif light > .90:
        result = '#1b1f1e'
    elif light > .78:
        result = '#39433f'
    elif light < .24:
        result = '#edf7f3'
    elif saturation > .24:
        result = '#b9f4e5'
    else:
        result = '#a8b8b2'
    return result + suffix


def restyle(text):
    text = HEX.sub(color, text)
    text = text.replace('background-color: #edf7f3', 'background-color: #141817')
    text = text.replace('background: #edf7f3', 'background: #141817')
    text = text.replace('color: white', 'color: #152b25')
    text = text.replace('painter.fillRect(self.rect(), QColor("#edf7f3"))', 'painter.fillRect(self.rect(), QColor("#0d1110"))')
    return text


def remove_sample_profiles(app):
    """Remove bundled caregiver links that would re-create deleted sample users."""
    import sys
    sys.path.insert(0, str(ROOT/'scripts'))
    from remove_sample_login_accounts import EMAILS
    paths = [app/'shared/data/patients.json', *sorted((app/'data/users').glob('*.json'))]
    for path in paths:
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        records = data.values() if path.name == 'patients.json' else [data]
        for record in records:
            cg = record.get('caregiver') or {}
            if str(cg.get('email') or record.get('caregiver_email') or '').strip().lower() in EMAILS:
                record['caregiver'] = {}
                for key in ('caregiver_email','caregiver_phone','caregiver_password','caregiver_name'):
                    record.pop(key, None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def build():
    source = ROOT / 'releases/portable-zipformer/PT/AICare'
    if PACKAGE.exists():
        raise RuntimeError('V3 already exists; refusing to overwrite: ' + str(PACKAGE))
    # Only immutable runtime binaries are linked; application and metadata are copied.
    def copy(src, dst):
        if 'runtime' in Path(src).relative_to(source).parts:
            try:
                os.link(src, dst)
                return dst
            except OSError:
                pass
        return shutil.copy2(src, dst)
    shutil.copytree(source, PACKAGE, copy_function=copy,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'SHA256SUMS.json', 'QA_REPORT.txt'))
    app = PACKAGE / 'app'
    shutil.copy2(ROOT/'features/voice_assistant/local_voice/engine.py',app/'features/voice_assistant/local_voice/engine.py')
    # V3 receives the same native bootstrap and CRT as PT/HP.
    for name in ('portable_entry.py', 'portable_native.py'):
        shutil.copy2(Path(__file__).parent/name, apps/desktop/name)
    for original in (ROOT/'runtime/portable-build-cache/vc-runtime').iterdir():
        target = PACKAGE/'runtime'/original.name
        temp = target.with_suffix(target.suffix+'.tmp')
        shutil.copy2(original,temp)
        temp.replace(target)
    patch_mediapipe(PACKAGE)
    (PACKAGE/'qa').mkdir(exist_ok=True)
    shutil.copy2(Path(__file__).parent/'qa.py',PACKAGE/'qa/acceptance.py')
    shutil.copy2(Path(__file__).parent/'security_regression.py',PACKAGE/'qa/security_regression.py')
    shutil.copy2(Path(__file__).parent/'clean_machine_test.ps1',PACKAGE/'qa/clean_machine_test.ps1')
    shutil.copy2(ROOT/'data/models/sherpa-onnx-zipformer-vi-30M-int8-2026-02-09/test_wavs/0.wav',PACKAGE/'qa/speech.wav')
    for name in ('007_seed_core_actors.sql', '013_remove_sample_login_accounts.sql'):
        shutil.copy2(ROOT/'shared/database/migrations'/name, app/'shared/database/migrations'/name)
    remove_sample_profiles(app)
    for folder in ('app', 'web', 'modules'):
        for path in (app / folder).rglob('*'):
            if path.suffix not in {'.py', '.css', '.html'}:
                continue
            original = path.read_text(encoding='utf-8-sig')
            updated = restyle(original)
            if path.suffix == '.html':
                updated = updated.replace('portal-theme.css?v=20261024', 'portal-theme.css?v=v3-1')
                if 'portal-theme.css' not in updated:
                    updated = updated.replace('</head>', '<link rel="stylesheet" href="/assets/portal-theme.css?v=v3-1">\n</head>')
            if path.suffix == '.py':
                ast.parse(updated, filename=str(path))
            if updated != original:
                path.write_text(updated, encoding='utf-8')
    theme = app / 'apps/desktop/theme.py'
    theme.write_text(theme.read_text(encoding='utf-8') + '\nLIGHT_THEME_STYLESHEET += ' + repr((Path(__file__).parent/'v3-desktop.qss').read_text(encoding='utf-8')) + '\n', encoding='utf-8')
    css = app / 'features/web_portal/assets/portal-theme.css'
    css.write_text(css.read_text(encoding='utf-8') + '\n' + (Path(__file__).parent/'v3-web.css').read_text(encoding='utf-8'), encoding='utf-8')
    (PACKAGE / 'EDITION.txt').write_text('V3', encoding='ascii')
    version = json.loads((PACKAGE/'VERSION.json').read_text(encoding='utf-8'))
    version.update(version='6.0.0-nativefix.2', edition='V3', theme='Charcoal & Mint',
                   build_utc=datetime.now(timezone.utc).isoformat(),
                   data_directory='%LOCALAPPDATA%/AICareDemo/V3', based_on='portable-zipformer/PT')
    (PACKAGE/'VERSION.json').write_text(json.dumps(version, ensure_ascii=False, indent=2), encoding='utf-8')
    (PACKAGE/'HUONG_DAN.txt').write_text('AI CARE V3 — CHARCOAL & MINT\n\nGiải nén toàn bộ gói, chạy AICare.exe để mở ứng dụng và web.\nKhông cần cài Python hoặc tải lại model.\nDữ liệu riêng: %LOCALAPPDATA%\\AICareDemo\\V3\nHai bản PT/HP cũ được giữ nguyên.\nTài khoản quản trị mặc định: admin / admin123.\nXem QA_REPORT.txt để biết các kiểm tra và giới hạn.\n', encoding='utf-8-sig')
    (PACKAGE/'CHANGELOG.txt').write_text('V3: giao diện charcoal–mint đồng bộ web và desktop; sidebar, thẻ bo tròn, nút dạng pill, bảng và biểu mẫu nền tối. Giữ các luồng nghiệp vụ và model của portable-zipformer. Dữ liệu V3 độc lập với PT/HP.\n', encoding='utf-8-sig')
    print(PACKAGE, flush=True)


if __name__ == '__main__':
    build()
