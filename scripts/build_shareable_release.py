"""Build a shareable Windows setup ZIP without touching local patient data."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'releases'
TREES = ('apps', 'features', 'shared', 'models', 'scripts', 'deploy', 'tests')
ROOT_FILES = ('apps/desktop/main.py', 'shared/config.py', 'scripts/check_system.py', 'deploy/windows/Setup.cmd', 'deploy/windows/Start Demo.cmd', 'deploy/windows/Stop Demo.cmd', 'deploy/windows/Diagnostics.cmd', 'deploy/windows/Reset Demo.cmd', 'requirements-windows.txt', 'requirements.txt', '.env.example', 'deploy/windows/demo.example.env')
SUFFIXES = {'.py', '.css', '.sql', '.html', '.json', '.md', '.txt', '.ps1', '.cmd', '.bat', '.spec', '.sh', '.png', '.svg', '.ico', '.wav', '.mp3', '.pb', '.pbtxt', '.prototxt', '.caffemodel', '.pth', '.task', '.onnx', '.pkl', '.sav', '.bin'}
SKIP = {'__pycache__', '.git', '.venv', '.pytest_cache', 'registered_faces', 'unknown_faces', 'logs', 'backups', 'reports', 'checkpoints', 'runs'}
REQUIRED = ('apps/desktop/main.py', 'deploy/windows/Setup.cmd', 'deploy/windows/Start Demo.cmd', 'scripts/setup_windows.ps1', 'scripts/start_windows.ps1', 'scripts/install_python.ps1', 'features/web_portal/templates/portal_admin.html', 'features/web_portal/templates/portal_caregiver.html', 'features/web_portal/templates/portal_clinician.html', 'shared/database/migrations/012_emotion_proportions.sql', 'features/voice_assistant/local_rules/rules.json', 'features/web_portal/assets/portal-theme.css', 'models/manifest.json', 'scripts/download_models.py', 'features/emotion/model/models/expression_net.pth', 'features/face_monitoring/standalone/models/opencv_face_detector.pbtxt')

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def entries():
    for name in ROOT_FILES:
        yield ROOT / name
    for name in TREES:
        for parent, dirs, files in os.walk(ROOT / name):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]
            for filename in sorted(files):
                p = Path(parent) / filename
                rel = p.relative_to(ROOT)
                if filename.startswith('.') or p.suffix.lower() not in SUFFIXES:
                    continue
                # Setup downloads the runtime models listed in models/manifest.json.
                # ExpressionNet is a tiny local checkpoint without a download URL.
                if p.suffix.lower() in {'.bin', '.pb', '.task', '.onnx', '.pkl', '.sav', '.caffemodel'}:
                    continue
                yield p

def build():
    OUT.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    tag = now.strftime('%Y%m%d-%H%M%S')
    name = f'AICareDemo-v6.0.0-windows-x64-setup-{tag}'
    target = OUT / (name + '.zip')
    temporary = target.with_suffix('.zip.partial')
    files = sorted(set(entries()))
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise RuntimeError(f'Missing files: {missing}')
    paths = {p.relative_to(ROOT).as_posix() for p in files}
    assert set(REQUIRED) <= paths, sorted(set(REQUIRED)-paths)
    for p in files:
        if p.suffix == '.py':
            ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))
    manifest = json.loads((ROOT / 'models/manifest.json').read_text(encoding='utf-8'))
    for model in manifest['models']:
        p = ROOT / model['target_path']
        if not model.get('source_url'):
            if p not in files or digest(p) != model['sha256']:
                raise RuntimeError(f'Missing or corrupt bundled model: {p}')
        elif not model['source_url'].startswith('https://') or not model.get('sha256'):
            raise RuntimeError(f'Invalid model download metadata: {p}')
    print(f'Validated {len(files)} files; writing {target}', flush=True)
    hashes = {}
    with zipfile.ZipFile(temporary, 'x', zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as z:
        for p in files:
            rel = p.relative_to(ROOT).as_posix()
            hashes[rel] = {'size_bytes': p.stat().st_size, 'sha256': digest(p)}
            z.write(p, 'AICareDemo/' + rel)
            if p.stat().st_size > 100_000_000:
                print(f'Bundled {rel}', flush=True)
        z.writestr('AICareDemo/runtime/', '')
        readme = '# AI Care Demo — Gói cài đặt Windows 10/11 64-bit\n\n1. Giải nén toàn bộ ZIP vào thư mục có quyền ghi, ví dụ D:\\AICareDemo.\n2. Kết nối Internet, chạy deploy/windows/Setup.cmd một lần. Setup cài Python nếu thiếu, cài thư viện và tải model AI theo models/manifest.json; các model tải về được kiểm tra SHA-256.\n3. Chờ Setup báo thành công, rồi chạy deploy/windows/Start Demo.cmd. Dừng bằng deploy/windows/Stop Demo.cmd.\n\nZIP chỉ chứa mã nguồn, giao diện, tài nguyên và checkpoint cảm xúc nhỏ không có URL tải. Không chứa môi trường .venv, model Whisper, dữ liệu bệnh nhân hoặc cấu hình riêng trên máy đóng gói.\n\nSetup mặc định tải Whisper small và các model thị giác cần thiết. Không tải Whisper medium. Dung lượng sau cài phụ thuộc phiên bản thư viện và bộ nhớ đệm pip; không cố định ở 2,5 GB. Cần đủ dung lượng trống theo kiểm tra của Setup.\n\nĐăng nhập: http://127.0.0.1:8080/login\nQuản trị viên: admin / admin123\nĐăng ký hồ sơ mới trên ứng dụng desktop. Migration cũng tạo các tài khoản mẫu cơ sở.\n\nCác chức năng cục bộ chạy offline sau khi Setup hoàn tất; email và chức năng cloud tùy chọn cần cấu hình/kết nối riêng. Cho phép camera và micro trong cài đặt Windows.\n\nĐã kiểm tra cấu trúc ZIP và hợp đồng tải model. Chưa kiểm chứng cài đặt từ đầu trên máy Windows sạch.\n'
        z.writestr('AICareDemo/README_WINDOWS_VI.md', readme)
        release = {'version':'6.0.0', 'built_at_utc':now.isoformat(), 'platform':'Windows 10/11 x64', 'distribution':'lightweight setup; models and libraries downloaded on first setup', 'validation':'archive integrity, required files and model download metadata; see external validation report', 'files':hashes}
        z.writestr('AICareDemo/RELEASE_MANIFEST.json', json.dumps(release, ensure_ascii=False, indent=2))
    print('Verifying ZIP CRC and required members...', flush=True)
    with zipfile.ZipFile(temporary) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f'Corrupt member: {bad}')
        members = set(z.namelist())
        assert all('AICareDemo/'+p in members for p in REQUIRED)
        assert not any(m.endswith(('.bin', '.onnx', '.task', '.pb', '.caffemodel', '.pkl', '.sav')) for m in members)
        assert not any('/.venv/' in m or m.endswith(('.sqlite3','.db','.pyc','/.env')) or '/shared/' in m for m in members)
    temporary.rename(target)
    sha = digest(target)
    target.with_suffix('.zip.sha256').write_text(f'{sha}  {target.name}\n', encoding='ascii')
    record = {'zip':str(target), 'size_bytes':target.stat().st_size, 'sha256':sha, 'source_files':len(files), 'zip_crc':'passed', 'bundled_model_hashes':'passed', 'download_manifest_metadata':'passed', 'clean_machine_setup':'not tested'}
    (OUT/'RELEASE_VALIDATION.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2), flush=True)
    return target

if __name__ == '__main__':
    build()
