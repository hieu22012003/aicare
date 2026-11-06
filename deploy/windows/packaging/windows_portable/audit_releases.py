"""Record native dependency evidence for every archived/extracted release."""
from pathlib import Path
import json
import os
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[4]
CRT = ('msvcp140.dll', 'concrt140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')


def pe_imports(path):
    """Read PE normal import names without third party build dependencies."""
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Not PE: ' + str(path))
    machine, count = struct.unpack_from('<HH', data, pe+4)
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    magic = struct.unpack_from('<H', data, optional)[0]
    dirs = optional + (112 if magic == 0x20b else 96)
    import_rva = struct.unpack_from('<I', data, dirs+8)[0]
    sections = []
    for i in range(count):
        off = optional+optional_size+i*40
        size, va, raw_size, raw = struct.unpack_from('<IIII', data, off+8)
        sections.append((va, max(size, raw_size), raw))

    def offset(rva):
        for va, size, raw in sections:
            if va <= rva < va+size:
                return raw+rva-va
        raise ValueError('Unmapped RVA')

    names = []
    if import_rva:
        pos = offset(import_rva)
        while any(data[pos:pos+20]):
            name_rva = struct.unpack_from('<I', data, pos+12)[0]
            start = offset(name_rva)
            names.append(data[start:data.index(b'\0', start)].decode('ascii'))
            pos += 20
    return {'machine': hex(machine), 'imports': names}


def audit():
    report = {'archives': [], 'extracted': [], 'scope':
              'All release ZIPs (excluding internal runtime ZIPs) and extracted runtime/python.exe folders. '
              'Static inventory only; not proof of execution on another computer.'}
    for path in sorted((ROOT/'releases').rglob('*.zip')):
        if 'runtime' in path.relative_to(ROOT/'releases').parts:
            continue
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            prefix = next((n[:-len('runtime/python.exe')] for n in names
                           if n.endswith('runtime/python.exe')), None)
            missing = [name for name in CRT if prefix is None or
                       prefix+'runtime/'+name not in names]
            report['archives'].append({'path': str(path.relative_to(ROOT)),
                'kind': 'portable' if prefix else 'setup/source (requires installation)',
                'missing_app_local_crt': missing,
                'status': 'superseded; do not distribute' if missing else 'CRT present; see QA'})
    for parent, dirs, files in os.walk(ROOT/'releases'):
        if Path(parent).name == 'runtime':
            dirs[:] = []
            if 'python.exe' not in files:
                continue
            runtime = Path(parent)
            missing = [name for name in CRT if not (runtime/name).exists()]
            native = next((runtime/'Lib/site-packages/mediapipe/python').glob('_framework_bindings*.pyd'), None)
            report['extracted'].append({'path': str(runtime.parent.relative_to(ROOT)),
                'missing_app_local_crt': missing,
                'mediapipe_pe': pe_imports(native) if native else None,
                'status': 'superseded; do not distribute' if missing else 'CRT present; see QA'})
    output = ROOT/'docs/test-reports/portable-native-audit.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    audit()
