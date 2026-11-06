"""One-way password storage shared by desktop registration and web administration."""
import hashlib
import hmac
import secrets

ITERATIONS = 600_000
DISABLED = "!"
LEGACY_DEFAULTS = frozenset(("demo", "admin123", "123456", "password123"))


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password or len(password) > 1024:
        raise ValueError("Mật khẩu không được để trống hoặc dài quá 1024 ký tự")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def is_password_hash(value: str) -> bool:
    try:
        algorithm, rounds, salt, digest = value.split("$")
        return (algorithm == "pbkdf2_sha256" and 100_000 <= int(rounds) <= 2_000_000
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except (ValueError, AttributeError, TypeError):
        return False


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or not password or len(password) > 1024 or not is_password_hash(stored):
        return False
    _, rounds, salt, expected = stored.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(actual, expected)


def upgrade_legacy_password(value: str) -> str:
    if is_password_hash(value) or value == DISABLED:
        return value
    raw = (value or "")
    if raw.startswith("pbkdf2:"):
        raw = raw[7:]
    return DISABLED if not raw or raw in LEGACY_DEFAULTS else hash_password(raw)


def migrate_database_passwords(db) -> None:
    if not db.table_exists("users"):
        return
    with db.connect() as conn:
        for row in conn.fetchall("SELECT user_id, password_hash FROM users"):
            old = row.get("password_hash") or ""
            new = upgrade_legacy_password(old)
            if new != old:
                conn.execute("UPDATE users SET password_hash=? WHERE user_id=? AND COALESCE(password_hash, '')=?",
                             (new, row["user_id"], old))


def protect_profile_credentials(value):
    """Return a copy with legacy cleartext credentials replaced by one-way hashes."""
    if isinstance(value, list):
        return [protect_profile_credentials(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: protect_profile_credentials(item) for key, item in value.items()
              if key not in ("password", "caregiver_password")}
    if "password" in value:
        result["password_hash"] = upgrade_legacy_password(value["password"])
    if "caregiver_password" in value:
        caregiver = result.setdefault("caregiver", {})
        caregiver.setdefault("password_hash", upgrade_legacy_password(value["caregiver_password"]))
    return result
