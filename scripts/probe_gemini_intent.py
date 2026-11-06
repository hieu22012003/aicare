"""One synthetic structured-output smoke. Key via stdin/env; never in argv/log/output."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.voice_assistant.transcript_intent.config import BackendConfig
from features.voice_assistant.transcript_intent.gemini_adapter import GeminiIntentAdapter
from features.voice_assistant.transcript_intent.schema import IntentProposal
from features.voice_assistant.transcript_intent.registry import Intent
from features.voice_assistant.transcript_intent.policy import validate_proposal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--free-tier-confirmed', action='store_true')
    parser.add_argument('--diagnose-request', action='store_true')
    parser.add_argument('--check-model-only', action='store_true')
    parser.add_argument('--output', default='docs/test-reports/gemini_intent/live-smoke.json')
    args = parser.parse_args()
    report = {'model': 'gemini-3.5-flash-lite', 'synthetic_only': True,
              'free_tier': 'operator_confirmed' if args.free_tier_confirmed else 'unverified',
              'project_id': 'unverified', 'quota': 'unverified'}
    if not args.free_tier_confirmed:
        report['outcome'] = 'tier_not_confirmed'
    else:
        key = sys.stdin.readline().strip() if args.key_stdin else os.getenv('GEMINI_API_KEY', '')
        if not key:
            report['outcome'] = 'missing_key'
        else:
            cfg = BackendConfig(enabled=True, free_tier_verified=True, api_key=key)
            started = time.monotonic()
            try:
                if args.check_model_only:
                    from google import genai
                    from google.genai import types, errors
                    client = genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(timeout=8000, retry_options=types.HttpRetryOptions(attempts=1)))
                    try:
                        model = client.models.get(model=cfg.model)
                        report['outcome'] = 'model_available'
                        report['supported_actions'] = model.supported_actions
                    except errors.APIError as exc:
                        detail = str(exc).lower()
                        report['outcome'] = 'invalid_key' if any(mark in detail for mark in ('api key not valid', 'api_key_invalid', 'api_key_expired')) else 'model_probe_rejected'
                    finally:
                        client.close()
                    raise StopIteration
                value = GeminiIntentAdapter(cfg).generate('Cho tôi quay về màn hình ban đầu', 'home_navigation', {Intent.HOME, Intent.UNKNOWN}, 8)
                context = {'patient_id': 'synthetic', 'tab': 2, 'running': False}
                value = validate_proposal(value, 'Cho tôi quay về màn hình ban đầu', context)
                report['outcome'] = 'validated' if value.intent == Intent.HOME else 'semantic_rejected'
                report['intent'] = value.intent.value
            except StopIteration:
                pass
            except Exception as exc:
                report['outcome'] = getattr(exc, 'code', 'failed')
                if args.diagnose_request:
                    # Only this fixed synthetic probe; never production transcript/error logging.
                    cause = getattr(exc, '__context__', None)
                    message = str(getattr(cause, 'message', ''))
                    report['request_diagnostic'] = message.replace(key, '[secret]')[:1000]
            report['latency_ms'] = round((time.monotonic() - started) * 1000)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report['outcome'] in ('validated', 'model_available') else 1


if __name__ == '__main__':
    raise SystemExit(main())
