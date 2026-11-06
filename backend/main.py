"""Run AICare as a cloud backend (Render, any Linux host).

    python -m backend.main

Serves the admin / caregiver / clinician portals, the REST API, SSE, the medication scheduler
(OutboxRelay) and the AI features (posture, fall risk, object finder, camera medication intake)
fed by home devices through /api/device/... . No PyQt, no camera, no MediaPipe on the server.

Environment
    PORT                    port to bind (Render sets it)                         default 8080
    AI_CARE_DB_PATH         SQLite file; put it on a persistent disk               default ./data/unified_care.sqlite3
    AI_CARE_HOME            writable folder for logs/uploads                       default ./runtime
    AICARE_ADMIN_PASSWORD   sets the admin password at boot (>= 12 characters)
    AICARE_DEVICE_KEYS      "key:patient_a|patient_b,key2:*" for /api/device (keys >= 16 characters)
    EMAIL_USERNAME / EMAIL_PASSWORD / EMAIL_HOST / EMAIL_PORT   caregiver email alerts (SMTP)
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AI_CARE_HOME", str(ROOT / "runtime"))
os.environ.setdefault("AI_CARE_DB_PATH", str(ROOT / "data" / "unified_care.sqlite3"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("AICareBackend")


def ensure_admin_password(db) -> None:
    from services.credential_security import LEGACY_DEFAULTS, hash_password

    row = db.fetchone("SELECT password_hash FROM users WHERE user_id='user_admin_001'")
    if not row:
        return
    wanted = os.getenv("AICARE_ADMIN_PASSWORD", "")
    if wanted:
        if len(wanted) < 12 or wanted in LEGACY_DEFAULTS:
            log.error("AICARE_ADMIN_PASSWORD must have at least 12 characters; admin password not changed")
            return
        db.execute("UPDATE users SET password_hash=? WHERE user_id='user_admin_001'", (hash_password(wanted),))
        log.info("Admin password set from AICARE_ADMIN_PASSWORD")
    elif row["password_hash"] in ("", "!"):
        generated = secrets.token_urlsafe(12)
        db.execute("UPDATE users SET password_hash=? WHERE user_id='user_admin_001'", (hash_password(generated),))
        log.warning("No AICARE_ADMIN_PASSWORD: generated admin password (shown once): %s", generated)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from ai_core.integration.device_api import BackendAIService, set_service
    from database.connection import UnifiedDatabaseManager
    from database.migrator import Migrator
    from web.server import HealthcareWebServer

    db = UnifiedDatabaseManager()
    applied = Migrator(db).apply_all()
    log.info("Database %s ready (%d migrations checked)", db.db_path, len(applied))
    ensure_admin_password(db)

    ai = BackendAIService(db)
    set_service(ai)
    ai.start()

    server = HealthcareWebServer(db, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))

    def shutdown(*_):
        ai.stop()
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    server.start(block=True)


if __name__ == "__main__":
    main()
