"""Verify the actual distribution, then exercise it from a relocated Unicode path."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[4]
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def test(archive):
    archive = archive.resolve()
    expected = archive.with_suffix('.zip.sha256').read_text().split()[0]
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise RuntimeError('Archive checksum mismatch')
    dest = ROOT/'runtime/portable-extracted'/('Máy mới '+archive.stem+' '+uuid.uuid4().hex[:6])
    dest.mkdir(parents=True)
    print('EXTRACT', archive.name, dest, flush=True)
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            target = (dest/info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise RuntimeError('Unsafe ZIP member')
        z.extractall(dest)
    print('EXTRACTED',archive.name,flush=True)
    package = dest/'AICare'
    manifest = json.loads((package/'SHA256SUMS.json').read_text())
    def verify(item):
        rel, expected = item
        path = package/rel
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected['sha256'] or path.stat().st_size != expected['size']:
            raise RuntimeError('Extracted file mismatch: '+rel)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(verify, manifest.items()))
    print('FILES_VERIFIED',archive.name,len(manifest),flush=True)
    report = dest/'EXTRACTED_QA.json'
    command = [str(package/'runtime/python.exe'), '-I', '-X', 'utf8', str(package/'qa/acceptance.py'),
               str(package), '--home', str(dest/'home'), '--report', str(report)]
    log_path=dest/'acceptance-console.txt'
    print('ACCEPTANCE',archive.name,flush=True)
    with log_path.open('w',encoding='utf-8') as log:
        process=subprocess.run(command, cwd=dest, stdout=log,stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode:
        raise RuntimeError('Extracted acceptance failed: '+str(log_path)+'\n'+
                           log_path.read_text(encoding='utf-8',errors='replace')[-7000:])
    result = {'archive':str(archive), 'extracted_to':str(package),
              'version':json.loads((package/'VERSION.json').read_text(encoding='utf-8'))['version'],
              'verified_files':len(manifest), 'relocated_unicode_path': True,
              'acceptance_report':str(report), 'passed':True,
              'clean_windows': 'not tested; relocated on build host'}
    output = ROOT/'releases'/('EXTRACTED_'+archive.stem+'_VALIDATION.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2),flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('archive', type=Path)
    test(p.parse_args().archive)
