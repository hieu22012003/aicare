"""Existing web backend with a separate secret file; never part of the elder executable."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True, help='Private backend env file outside the desktop distribution')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    if getattr(sys, 'frozen', False):
        parser.error('Run this backend from its own Python environment')
    private_file = Path(args.env_file).expanduser().resolve()
    if not private_file.is_file():
        parser.error('Private backend env file was not found')
    from dotenv import load_dotenv
    load_dotenv(private_file, override=False)
    from features.voice_assistant.transcript_intent.config import BackendConfig
    try:
        cfg = BackendConfig.from_env()
    except ValueError:
        parser.error('Invalid backend model/timeout/quota settings')
    missing = []
    if cfg.enabled:
        for name, present in [('GEMINI_API_KEY', bool(cfg.api_key)), ('GEMINI_PROJECT_ID', bool(cfg.project)),
                              ('GEMINI_FREE_TIER_VERIFIED', cfg.free_tier_verified)]:
            if not present:
                missing.append(name)
    report = dict(enabled=cfg.enabled, key_configured=bool(cfg.api_key), project_configured=bool(cfg.project),
                  free_tier_operator_confirmed=cfg.free_tier_verified, model=cfg.model,
                  local_caps=dict(rpm=cfg.rpm, tpm_estimated=cfg.tpm, rpd=cfg.rpd, reserve=cfg.daily_reserve),
                  timeout_seconds=cfg.timeout_seconds, retries=cfg.retries, missing=missing)
    print(json.dumps(report, indent=2))  # Presence only; no key/project/session/transcript values.
    if missing:
        return 2
    if args.check_config:
        return 0
    from shared.database.connection import UnifiedDatabaseManager
    from shared.database.migrator import Migrator
    from features.web_portal.server import HealthcareWebServer
    db = UnifiedDatabaseManager()
    Migrator(db).apply_all()
    HealthcareWebServer(db, args.host, args.port).start(block=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
