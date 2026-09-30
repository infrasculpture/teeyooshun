"""Parent PIN: login, a signed cookie, and lockout after repeated wrong PINs.

No accounts. A correct PIN sets a cookie valid for SESSION_HOURS. The cookie
is signed with SESSION_SECRET and the PIN, so changing either logs parents out.
"""

import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from app.config import get_settings

COOKIE_NAME = "teeyooshun_parent"
SESSION_HOURS = 12
MAX_WRONG = 5
LOCK_SECONDS = 300

# Lockout state lives in memory: a restart clears it, which is fine for a home app.
_wrong = {"count": 0, "locked_until": 0.0}


def reset_lockout() -> None:
    _wrong["count"] = 0
    _wrong["locked_until"] = 0.0


def parent_enabled(settings=None) -> bool:
    s = settings or get_settings()
    return bool(s.session_secret) and s.parent_pin not in ("", "0000")


def _signature(expires: int, settings) -> str:
    key = f"{settings.session_secret}:{settings.parent_pin}".encode("utf-8")
    return hmac.new(key, f"parent:{expires}".encode("utf-8"), hashlib.sha256).hexdigest()


def check_pin(pin: str, now=None) -> str:
    """Returns "ok", "wrong", "locked" or "disabled"."""
    settings = get_settings()
    now = time.time() if now is None else now
    if not parent_enabled(settings):
        return "disabled"
    if now < _wrong["locked_until"]:
        return "locked"
    if hmac.compare_digest(pin.strip().encode("utf-8"), settings.parent_pin.encode("utf-8")):
        reset_lockout()
        return "ok"
    _wrong["count"] += 1
    if _wrong["count"] >= MAX_WRONG:
        _wrong["locked_until"] = now + LOCK_SECONDS
        _wrong["count"] = 0
    return "wrong"


def make_cookie(now=None) -> str:
    settings = get_settings()
    now = time.time() if now is None else now
    expires = int(now) + SESSION_HOURS * 3600
    return f"{expires}.{_signature(expires, settings)}"


def is_parent(request: Request, now=None) -> bool:
    settings = get_settings()
    if not parent_enabled(settings):
        return False
    value = request.cookies.get(COOKIE_NAME, "")
    expires_text, _, signature = value.partition(".")
    if not expires_text.isdigit():
        return False
    expires = int(expires_text)
    now = time.time() if now is None else now
    if expires < now:
        return False
    return hmac.compare_digest(signature, _signature(expires, settings))


def require_parent(request: Request) -> None:
    """Route dependency: refuse with 403 unless the parent cookie is valid."""
    if not is_parent(request):
        raise HTTPException(status_code=403, detail="Parent PIN required")


def safe_next(url: str) -> str:
    """Only allow redirects back into this app."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return "/"
