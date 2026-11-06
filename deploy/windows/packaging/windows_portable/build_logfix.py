"""Rebuild the three existing editions with focused log/security corrections."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import sys

from archive import archive
from qa import run_qa

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'releases/logfix-20261101'
VERSION = '6.0.0-logfix.1'


def build_launcher():
    output = ROOT / 'runtime/log-hardening-tests/AICare.exe'
    output.parent.mkdir(parents=True, exist_ok=True)
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                    '/reference:System.Web.Extensions.dll', '/out:' + str(output),
                    str(ROOT / 'deploy/windows/packaging/windows_portable/Launcher.cs')], check=True)


def write(path, data):
    temp = path.with_name(path.name + '.logfix-tmp')
    temp.write_bytes(data)
    temp.replace(path)


def stage(edition):
    source = ROOT / 'releases/portable-nativefix' / edition / 'AICare'
    dest = OUT / edition / 'AICare'
    if dest.exists():
        raise RuntimeError('Refusing to overwrite ' + str(dest))
    def copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
        return dst
    shutil.copytree(source, dest, copy_function=copy,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'QA_REPORT.txt', 'SHA256SUMS.json'))
    # Preserve the individual editions' routes and styling.
    path = dest / 'apps/desktop/web/server.py'
    text = path.read_text(encoding='utf-8')
    handler = (ROOT / 'features/web_portal/server.py').read_text(encoding='utf-8')
    method = handler[handler.index('    def handle(self)'):handler.index('    def log_message')]
    assert '    def handle(self)' not in text
    text = text.replace('    def log_message', method + '    def log_message', 1)
    text = text.replace('self.port = candidate_port', 'self.port = self.httpd.server_port')
    text = text.replace('if e.errno == 98 or "Address already in use" in str(e):',
                        'if e.errno in (98, 10048) or getattr(e, "winerror", None) == 10048 or "Address already in use" in str(e):')
    write(path, text.encode('utf-8'))
    for src, rel in (
        (ROOT / 'shared/services/patient_sync_service.py', 'apps/desktop/services/patient_sync_service.py'),
        (ROOT / 'runtime/log-hardening-tests/AICare.exe', 'AICare.exe'),
        (ROOT / 'deploy/windows/packaging/windows_portable/qa.py', 'qa/acceptance.py'),
        (ROOT / 'deploy/windows/packaging/windows_portable/security_regression.py', 'qa/security_regression.py'),
    ):
        write(dest / rel, src.read_bytes())
    value = json.loads((dest / 'VERSION.json').read_text(encoding='utf-8'))
    value.update(version=VERSION, build_utc=datetime.now(timezone.utc).isoformat(),
                 based_on=str(source.relative_to(ROOT)))
    write(dest / 'VERSION.json', json.dumps(value, indent=2).encode('utf-8'))
    write(dest / 'CHANGELOG.txt', (
        VERSION + '\nPreserve revoked caregiver permissions and locked accounts during profile sync.\n'
        'Deliver caregiver registration exclusively through the authorized outbox relay.\n'
        'Report actual dynamic HTTP port; handle Windows port collisions and client disconnects.\n'
        'Decode Python stdout/stderr as UTF-8 in launcher.\n'
        'Native runtime and models unchanged. Physical devices and clean Windows not tested.\n'
    ).encode('utf-8'))
    return dest


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    build_launcher()
    for edition in sys.argv[1:] or ('PT', 'HP', 'V3'):
        package = stage(edition)
        print('STAGED', edition, flush=True)
        run_qa(package, ROOT / 'runtime/logfix-qa' / edition)
        archive(package, OUT)
