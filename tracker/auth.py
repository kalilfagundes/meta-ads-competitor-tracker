"""Authentication: password hashing (stdlib) + signed session cookie (HMAC).

  - credentials live in the `users` table (hashed); the admin is created by the
    first-run wizard;
  - the session cookie is "<expiry>.<hmac_hex>": HMAC-SHA256 over the expiry
    string, keyed with the instance's session secret (see `tracker.config`). A
    missing or short secret is refused: with an empty key anyone could sign a cookie.

No external dependencies: `hashlib`/`hmac`/`secrets` from the stdlib.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

SESSION_COOKIE_NAME = "session"
SESSION_DURATION_SECONDS = 7 * 24 * 60 * 60  # 7 days
_PBKDF2_ITERATIONS = 200_000
MIN_SECRET_LENGTH = 32


# ── Password (pbkdf2-hmac-sha256) ───────────────────────────────────────────

def hash_password(password: str, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Return 'pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>'."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Compare in (near) constant time; never raises."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ── Session cookie (HMAC-SHA256 over the expiry) ────────────────────────────

def secret_is_usable(secret: str | None) -> bool:
    return bool(secret) and len(secret) >= MIN_SECRET_LENGTH


def create_session_value(secret: str, now: float | None = None) -> str:
    if not secret_is_usable(secret):
        raise RuntimeError(
            f"SESSION_SECRET is missing or shorter than {MIN_SECRET_LENGTH} characters"
        )
    expiry = int((now if now is not None else time.time())) + SESSION_DURATION_SECONDS
    sig = hmac.new(secret.encode(), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def verify_session_value(value: str | None, secret: str, now: float | None = None) -> bool:
    if not secret_is_usable(secret):
        return False  # never trust a cookie signed with a guessable key
    if not value or "." not in value:
        return False
    expiry_str, sig_hex = value.split(".", 1)
    try:
        expiry = int(expiry_str)
    except ValueError:
        return False
    if expiry < int(now if now is not None else time.time()):
        return False
    expected = hmac.new(secret.encode(), expiry_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_hex)
