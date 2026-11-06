"""Generate an offline Windows Sandbox test with read-only package mapping."""
from pathlib import Path
import argparse
from xml.sax.saxutils import escape


def make(package, output):
    package, output = package.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name('clean_machine_test.ps1').resolve()
    import shutil
    shutil.copy2(script, output/script.name)
    command = ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
               'C:\\Results\\clean_machine_test.ps1 -Package C:\\Package '
               '-Results C:\\Results\\Run')
    xml = f'''<Configuration>
<Networking>Disable</Networking><VGpu>Disable</VGpu><MemoryInMB>8192</MemoryInMB>
<MappedFolders>
<MappedFolder><HostFolder>{escape(str(package))}</HostFolder><SandboxFolder>C:\\Package</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
<MappedFolder><HostFolder>{escape(str(output))}</HostFolder><SandboxFolder>C:\\Results</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
</MappedFolders><LogonCommand><Command>{escape(command)}</Command></LogonCommand>
</Configuration>'''
    target = output/'Offline-AICare.wsb'
    target.write_text(xml, encoding='utf-8')
    print(target)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('package', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    make(args.package, args.output)
