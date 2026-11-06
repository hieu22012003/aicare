"""Audit compiled application code against source, without executing the bundle."""
import json
import marshal
from pathlib import Path
import types

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT/'dist'/'medication'/'AI_Rehabilitation_Assistant_DEBUG'
EXE = BUNDLE/'AI_Rehabilitation_Assistant_DEBUG.exe'
PYZ = ROOT/'build'/'medication'/'AI_Rehabilitation_Assistant'/'PYZ-00.pyz'


def signature(value):
    if isinstance(value,types.CodeType):
        return (value.co_code.hex(),value.co_names,value.co_varnames,value.co_flags,
                tuple(signature(c) for c in value.co_consts))
    if isinstance(value,(tuple,list)):
        return tuple(signature(c) for c in value)
    if isinstance(value,frozenset):
        return tuple(sorted(repr(c) for c in value))
    return value


archive = ZlibArchiveReader(str(PYZ))
modules = ('config','apps.desktop.dashboard','apps.desktop.router','apps.desktop.medication_panel',
           'shared.services.email_service','features.medication.medication_service','features.medication.medication_migration',
           'shared.services.user_service')
for module in modules:
    source = ROOT/(module.replace('.','/')+'.py')
    expected = compile(source.read_text(encoding='utf-8'),str(source),'exec')
    assert signature(archive.extract(module)) == signature(expected), f'Stale bundled code: {module}'
    print('PASS source matches compiled module:',module)

entry = marshal.loads(CArchiveReader(str(EXE)).extract('main'))
assert signature(entry) == signature(compile((ROOT/'apps/desktop/main.py').read_text(encoding='utf-8'),'apps/desktop/main.py','exec'))
assert not any(name.startswith(('features.fall_risk.hfd_model','adapters.fall','services.fall','core.fall','app.fall')) for name in archive.toc)
assert not (BUNDLE/'_internal'/'data'/'users').exists()
assert not (BUNDLE/'_internal'/'shared'/'data').exists()
assert (BUNDLE/'_internal'/'tzdata'/'zoneinfo'/'Asia'/'Ho_Chi_Minh').is_file()
print('PASS entry point, zero retired fall modules, no bundled profiles, IANA timezone data present.')
