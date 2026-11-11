"""Run AICare as a cloud backend (Render, any Linux host).

    python -m apps.server.main

Serves the admin / caregiver / clinician portals, the REST API, SSE, the medication scheduler
(OutboxRelay) and the AI features (posture, fall risk, object finder, camera medication intake)
fed by home devices through /api/device/... . No PyQt, no camera, no MediaPipe on the server.

Environment
    PORT                    port to bind (Render sets it)                         default 8080
    AI_CARE_DB_PATH         SQLite file; put it on a persistent disk               default ./data/unified_care.sqlite3
    AI_CARE_HOME            writable folder for logs/uploads                       default ./runtime
    AICARE_ADMIN_PASSWORD   sets the admin password at boot (>= 12 characters)
    AICARE_DEVICE_KEYS      "key:patient_a|patient_b,key2:*" for /api/device (keys >= 16 characters)
    AICARE_BOOTSTRAP_CAREGIVER  "email|password|Full name|patient_id": creates that caregiver account at boot
                            if it does not exist yet (useful on hosts without a persistent disk)
    AICARE_BOOTSTRAP_CLINICIAN  "email|password|Full name|patient_id": same for a doctor account
    EMAIL_USERNAME / EMAIL_PASSWORD / EMAIL_HOST / EMAIL_PORT   caregiver email alerts (SMTP)
    AICARE_KEEPALIVE        1 = request our own public URL every 10 minutes so a Render Free instance never
                            spins down (spin-down wipes the /tmp database). Uses the workspace's Free hours
                            (750/month shared by all Free services): turn it on only while someone is testing.
    AICARE_PUBLIC_URL       public URL for the keep-alive; Render's RENDER_EXTERNAL_URL is used when unset
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import sys
import time
from pathlib import Path

# Cloud hosts run in UTC. "Today", duty hours and dose days must follow the patients' clock, like the home app.
os.environ.setdefault("TZ", "Asia/Ho_Chi_Minh")
if hasattr(time, "tzset"):
    time.tzset()

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("AI_CARE_HOME", str(ROOT / "runtime"))
os.environ.setdefault("AI_CARE_DB_PATH", str(ROOT / "data" / "unified_care.sqlite3"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("AICareBackend")


def ensure_admin_password(db) -> None:
    from shared.services.credential_security import LEGACY_DEFAULTS, hash_password

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


def _bootstrap_spec(var: str, default_name: str):
    parts = [p.strip() for p in os.getenv(var, "").split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    name = parts[2] if len(parts) > 2 and parts[2] else default_name
    patient_id = parts[3] if len(parts) > 3 and parts[3] else "patient_001"
    if len(parts[1]) < 12:
        log.error("%s: password must have at least 12 characters; account not created", var)
        return None
    return parts[0].lower(), parts[1], name, patient_id


def ensure_bootstrap_clinician(db) -> None:
    spec = _bootstrap_spec("AICARE_BOOTSTRAP_CLINICIAN", "Bác sĩ")
    if not spec:
        return
    email, password, name, patient_id = spec
    if db.fetchone("SELECT 1 FROM users WHERE LOWER(email)=?", (email,)):
        return
    from features.web_portal.api_router import ApiRouter
    from features.web_portal.auth import AuthService

    code, res = ApiRouter(db, AuthService(db))._admin_create_doctor(
        {"full_name": name, "email": email, "password": password, "patient_ids": [patient_id]}, "user_admin_001")
    if code == 201:
        log.info("Clinician account %s created for %s", email, patient_id)
    else:
        log.error("Could not create clinician %s: %s", email, res.get("error"))


def ensure_bootstrap_caregiver(db) -> None:
    raw = os.getenv("AICARE_BOOTSTRAP_CAREGIVER", "")
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return
    email, password = parts[0].lower(), parts[1]
    name = parts[2] if len(parts) > 2 and parts[2] else "Người nhà"
    patient_id = parts[3] if len(parts) > 3 and parts[3] else "patient_001"
    if len(password) < 12:
        log.error("AICARE_BOOTSTRAP_CAREGIVER: password must have at least 12 characters; account not created")
        return
    if db.fetchone("SELECT 1 FROM users WHERE LOWER(email)=?", (email,)):
        return
    from features.web_portal.api_router import ApiRouter
    from features.web_portal.auth import AuthService

    code, res = ApiRouter(db, AuthService(db))._admin_create_caregiver(
        {"full_name": name, "email": email, "password": password, "patient_id": patient_id,
         "relationship_type": "Người thân", "relationship_desc": "Người thân"}, "user_admin_001")
    if code == 201:
        log.info("Caregiver account %s created for %s", email, patient_id)
    else:
        log.error("Could not create caregiver %s: %s", email, res.get("error"))


def start_keepalive(interval_s: float = 600.0):
    """Render Free spins an instance down after 15 min without inbound traffic, and the local SQLite file goes
    with it. A request to our own public URL goes through Render's proxy and counts as inbound traffic."""
    if os.getenv("AICARE_KEEPALIVE", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    base = (os.getenv("AICARE_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not base.startswith("http"):
        log.warning("AICARE_KEEPALIVE is on but no public URL (AICARE_PUBLIC_URL / RENDER_EXTERNAL_URL): disabled")
        return None
    import threading
    import urllib.request

    stop = threading.Event()

    def run():
        while not stop.wait(interval_s):
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=30) as r:
                    r.read(64)
            except Exception as e:  # a failed ping only means the next one tries again
                log.warning("keep-alive ping failed: %s", e)

    threading.Thread(target=run, name="KeepAlive", daemon=True).start()
    log.info("Keep-alive on: %s/api/health every %d s", base, int(interval_s))
    return stop


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from shared.ai_runtime.device_api import BackendAIService, set_service
    from shared.database.connection import UnifiedDatabaseManager
    from shared.database.migrator import Migrator
    from features.web_portal.server import HealthcareWebServer

    db = UnifiedDatabaseManager()
    applied = Migrator(db).apply_all()
    log.info("Database %s ready (%d migrations checked)", db.db_path, len(applied))
    ensure_admin_password(db)
    ensure_bootstrap_caregiver(db)
    ensure_bootstrap_clinician(db)

    ai = BackendAIService(db)
    set_service(ai)
    ai.start()
    start_keepalive()

    server = HealthcareWebServer(db, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")))

    def shutdown(*_):
        ai.stop()
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    server.start(block=True)


if __name__ == "__main__":
    main()
