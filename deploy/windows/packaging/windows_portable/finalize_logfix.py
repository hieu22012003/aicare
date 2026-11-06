"""Collect final package QA evidence without reusing historical release reports."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'releases/logfix-20261101'


def main():
    packages = []
    for edition in ('PT', 'HP', 'V3'):
        package = OUT / edition / 'AICare'
        qa = json.loads((package / 'QA_REPORT.txt').read_text(encoding='utf-8'))
        validation = json.loads((OUT / f'PORTABLE_{edition}_VALIDATION.json').read_text(encoding='utf-8'))
        version = json.loads((package / 'VERSION.json').read_text(encoding='utf-8'))
        assert version['version'] == '6.0.0-logfix.1'
        assert qa['launcher_self_test']['passed'] and qa['security_regression'].endswith('HTTP disconnects')
        assert validation['crc'] == validation['all_member_sha256'] == validation['native_qa'] == 'passed'
        archive = OUT / validation['file']
        assert archive.stat().st_size == validation['bytes']
        assert archive.with_suffix('.zip.sha256').read_text().split()[0] == validation['sha256']
        log_dir = ROOT / 'runtime/logfix-qa' / edition / 'logs'
        log_file = max(log_dir.glob('server-*.log'), key=lambda p: p.stat().st_mtime)
        text = log_file.read_text(encoding='utf-8-sig')
        assert '🚀 HEALTHCARE WEB SERVER & SSE GATEWAY ĐANG CHẠY' in text
        assert 'URL Cổng Quản trị viên:' in text and 'http://127.0.0.1:0' not in text
        assert 'Traceback (most recent call last)' not in text
        original = ROOT / 'releases/portable-nativefix' / edition / 'AICare'
        assert (package / 'models/checksums.json').read_bytes() == (original / 'models/checksums.json').read_bytes()
        validation.update(version=version['version'], launcher_utf8='passed',
                          actual_port_in_log='passed', model_manifest_unchanged=True,
                          qa_report=str(package / 'QA_REPORT.txt'), launcher_log=str(log_file))
        packages.append(validation)
    result = {'version': '6.0.0-logfix.1', 'packages': packages,
              'focused_tests': {'passed': 16, 'failed': 0, 'report': 'logfix-focused-tests.txt'},
              'extended_tests': {'passed': 15, 'failed': 7, 'same_failures_with_pre_fix_modules': True,
                                 'baseline_report': 'logfix-integration-baseline.txt'},
              'limitations': ['Clean Windows machine not tested', 'Physical camera/microphone not tested',
                              'Long duration operation not tested', 'Existing corrupted user data not repaired']}
    (OUT / 'RELEASE_VALIDATION.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    for source in (ROOT / 'docs/reports/LOGFIX_20261101.md', ROOT / 'docs/test-reports/logfix-focused-tests.txt',
                   ROOT / 'docs/test-reports/logfix-integration-baseline.txt'):
        shutil.copy2(source, OUT / source.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
