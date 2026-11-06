"""Publish an evidence index only after ZIP and relocated acceptance pass."""
from pathlib import Path
from datetime import datetime, timezone
import json

ROOT = Path(__file__).resolve().parents[4]


def finalize():
    result = {'version':'6.0.0-nativefix.2', 'package_type':'Windows x64 portable',
              'generated_utc':datetime.now(timezone.utc).isoformat(),
              'editions':{}, 'clean_windows_tested':False,
              'limitations':'Build-host Windows and relocated ZIP acceptance only. '
              'Clean Windows without Python/VC++, physical camera/microphone, '
              'different CPUs/Windows versions and long-duration runs not verified.',
              'retired_builds':'See docs/test-reports/portable-native-audit-before.json; '
              'use only the three current AI_FOR_EXAM ZIPs with nativefix version.'}
    for edition in ('PT','HP','V3'):
        validation = json.loads((ROOT/f'releases/PORTABLE_{edition}_VALIDATION.json').read_text())
        extracted = json.loads((ROOT/f'releases/EXTRACTED_AI_FOR_EXAM_{edition}_VALIDATION.json').read_text(encoding='utf-8'))
        qa_path = ROOT/f'releases/portable-nativefix/{edition}/AICare/QA_REPORT.txt'
        qa = json.loads(qa_path.read_text(encoding='utf-8'))
        if extracted.get('version') != result['version'] or not extracted['passed'] or validation.get('native_qa') != 'passed' or not qa['launcher_self_test']['passed']:
            raise RuntimeError('Required validation did not pass: '+edition)
        result['editions'][edition] = {**validation, 'version':'6.0.0-nativefix.2',
                                      'relocated_zip_acceptance':extracted,
                                      'qa_report':str(qa_path.relative_to(ROOT))}
    for name in ('AI_FOR_EXAM_VALIDATION.json','PORTABLE_NATIVEFIX_VALIDATION.json'):
        (ROOT/'releases'/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'editions':list(result['editions']),
                      'clean_windows_tested':False},indent=2))


if __name__ == '__main__':
    finalize()
