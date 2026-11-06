"""Extract app-local Microsoft CRT from the signed, offline VC redistributable.

Never copy DLLs from System32: that hides packaging defects on build machines.
Windows expand extracts the Burn bundle's cabinets; no installer is run.
"""
from pathlib import Path
import argparse
import json
import struct
import subprocess
import hashlib
import shutil
import os
import xml.etree.ElementTree as ET

CRT_NAMES = ('concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll',
             'msvcp140_2.dll', 'msvcp140_atomic_wait.dll',
             'msvcp140_codecvt_ids.dll', 'vcomp140.dll', 'vcruntime140.dll',
             'vcruntime140_1.dll', 'vcruntime140_threads.dll')


def signed(path):
    # File paths are arguments, never interpolated into PowerShell code.
    script = ('$p=$env:AICARE_SIGNATURE_PATH; $s=Get-AuthenticodeSignature -LiteralPath $p; '
              'if ($s.Status -ne "Valid" -or '
              '$s.SignerCertificate.Subject -notmatch "Microsoft Corporation") '
              '{ throw "Invalid Microsoft signature: $p ($($s.Status))" }')
    env = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    env['AICARE_SIGNATURE_PATH'] = str(path)
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                   check=True, env=env)


def extract(installer, output):
    installer, output = installer.resolve(), output.resolve()
    signed(installer)
    work = output.parent / 'vc-extract'
    work.mkdir(parents=True, exist_ok=True)
    data = installer.read_bytes()
    start = 0
    for index in range(100):
        pos = data.find(b'MSCF', start)
        if pos < 0:
            break
        start = pos + 4
        size = struct.unpack_from('<I', data, pos + 8)[0]
        if not 36 < size <= len(data) - pos:
            continue
        cab = work / f'container-{index}.cab'
        cab.write_bytes(data[pos:pos + size])
        dest = work / f'container-{index}'
        dest.mkdir(exist_ok=True)
        subprocess.run(['expand.exe', str(cab), '-F:*', str(dest)], check=True,
                       capture_output=True)
    manifest = ET.parse(work / 'container-0/0').getroot()
    ns = {'b': 'http://schemas.microsoft.com/wix/2008/Burn'}
    payloads = {p.attrib['Id']: p.attrib for p in manifest.findall('b:Payload', ns)}
    package = manifest.find("b:Chain/b:MsiPackage[@Id='vcRuntimeMinimum_x64']", ns)
    if package is None:
        raise RuntimeError('Unsupported VC bundle: x64 minimum package missing')
    minimum = work / 'minimum'
    minimum.mkdir(exist_ok=True)
    for ref in package.findall('b:PayloadRef', ns):
        payload = payloads[ref.attrib['Id']]
        if not payload['FilePath'].lower().endswith('.cab'):
            continue
        source = work / 'container-1' / payload['SourcePath']
        subprocess.run(['expand.exe', str(source), '-F:*', str(minimum)],
                       check=True, capture_output=True)
    output.mkdir(parents=True, exist_ok=True)
    license_payload = manifest.find("b:UX/b:Payload[@FilePath='license.rtf']", ns)
    if license_payload is None:
        raise RuntimeError('Microsoft VC license missing from bundle')
    shutil.copy2(work/'container-0'/license_payload.attrib['SourcePath'],
                 output/'Microsoft-VCRuntime-LICENSE.rtf')
    files = {}
    for name in CRT_NAMES:
        source = minimum / (name + '_amd64')
        signed(source)
        dest = output / name
        # Replace rather than mutate: runtime files may be hard-linked to releases.
        temp = dest.with_suffix('.dll.tmp')
        shutil.copy2(source, temp)
        temp.replace(dest)
        files[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    report = {'source': str(installer.name),
              'source_sha256': hashlib.sha256(data).hexdigest(),
              'version': package.attrib['Version'], 'architecture': 'AMD64',
              'authenticode': 'Valid Microsoft Corporation', 'files': files}
    (output / 'vc-runtime.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('installer', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    extract(args.installer, args.output)
