"""Reviewed synthetic-only benchmark. Offline measures policy coverage, not Gemini accuracy."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.voice_assistant.transcript_intent.synthetic_dataset import load_cases
from features.voice_assistant.transcript_intent.policy import local_proposal, privacy_eligible, validate_proposal
from features.voice_assistant.transcript_intent.schema import proposal
from features.voice_assistant.transcript_intent.registry import Intent
from features.voice_assistant.transcript_intent.config import BackendConfig
from features.voice_assistant.transcript_intent.service import GeminiIntentService


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*q + .5))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--free-tier-confirmed', action='store_true')
    parser.add_argument('--project-id', default='')
    parser.add_argument('--cases', default='synth_002,synth_094,synth_086')
    parser.add_argument('--output', default='docs/test-reports/gemini_intent/offline-benchmark.json')
    args = parser.parse_args()
    cases = load_cases()
    out_dir = Path(args.output).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    for split in ('tune', 'acceptance'):
        (out_dir / (split + '.jsonl')).write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in cases if c['split'] == split))
    if args.live:
        if not args.free_tier_confirmed or not args.project_id:
            parser.error('Explicit free tier confirmation and project label are required; label alone does not verify quota')
        key = sys.stdin.readline().strip() if args.key_stdin else os.getenv('GEMINI_API_KEY', '')
        if not key:
            parser.error('Missing backend key (use protected stdin or environment)')
        selected = args.cases.split(',')
        if len(selected) > 3 or len(set(selected)) != len(selected):
            parser.error('This smoke run accepts at most three distinct reviewed samples')
        lookup = {c['id']: c for c in cases if c['split'] == 'acceptance'}
        if any(i not in lookup for i in selected):
            parser.error('Select existing acceptance IDs only')
        cfg = BackendConfig(True, True, key, project=args.project_id,
            quota_path=str(out_dir / 'smoke-quota.json'))
        svc = GeminiIntentService(cfg)
        results = []
        for index, case_id in enumerate(selected):
            if index:
                print('Waiting within local RPM cap...', flush=True)
                time.sleep(30)
                time.sleep(31)  # CLI only; no UI blocking. Never retries quota failures.
            case = lookup[case_id]
            result = svc.classify_synthetic_case(case)
            results.append(dict(id=case_id, expected=case['expected'], **result))
            print(json.dumps({'id': case_id, 'outcome': result['outcome'], 'latency_ms': result['latency_ms']}), flush=True)
        report = dict(mode='limited_live_synthetic', model=cfg.model, actual_project_id_verified=False,
            quota_verified=False, full_acceptance_completed=False, records=results,
            p95_ms=percentile([r['latency_ms'] for r in results], .95),
            schema_success=sum(r['outcome'] == 'gemini' for r in results),
            intent_correct=sum(r['proposal']['intent'] == r['expected'] for r in results))
    else:
        records = []
        for case in cases:
            started = time.perf_counter()
            local = local_proposal(case['text'])
            value = validate_proposal(local, case['text'], case['context']) if local else proposal()
            records.append(dict(id=case['id'], split=case['split'], expected=case['expected'], actual=value.intent.value,
                correct=value.intent.value == case['expected'], cloud_eligible=privacy_eligible(case['text']),
                latency_ms=(time.perf_counter()-started)*1000))
        report = dict(mode='cloud_disabled_policy_coverage', gemini_calls=0, records=records,
            total=len(records), acceptance_count=sum(r['split'] == 'acceptance' for r in records),
            correct=sum(r['correct'] for r in records),
            acceptance_correct=sum(r['correct'] and r['split'] == 'acceptance' for r in records),
            p95_local_ms=percentile([r['latency_ms'] for r in records], .95),
            confusion_pairs=[dict(expected=a, actual=b, count=n) for (a,b),n in Counter((r['expected'],r['actual']) for r in records if not r['correct']).items()],
            unexpected_sensitive_routes=sum(r['expected']=='unknown' and r['actual'] not in ('unknown','navigate_home') for r in records),
            limitation='Coverage of conservative local policy only, NOT model accuracy or full voice latency')
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
