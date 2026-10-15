"""HTTP Basic auth against the local `users` table.

Phase 1 keeps this deliberately simple: one auth method (HTTP Basic, which
works the same from a browser prompt, curl, or the CLI client with zero
client-side session/CSRF handling), two roles (admin/user, see
models.user.Role). OIDC and granular per-scope permissions are Phase 2 (see
docs/TODO.md) if they turn out to be worth the complexity here.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from handler.database import db_user_handler
from logger.logger import log
from models.user import User

_security = HTTPBasic()

# HTTP Basic sends the password with every request and bcrypt costs a couple of hundred
# milliseconds of CPU, which made every download request (a file, a manifest poll) slow and
# serialised a busy server. A password verified a moment ago is remembered for a few minutes,
# keyed by an HMAC of it under a key that exists only in this process, never the password itself.
# The stored hash is part of the entry, so changing the password ends it at once.
AUTH_CACHE_SECONDS = 300
_AUTH_CACHE_MAX = 1024
_auth_key = os.urandom(32)
_verified: dict[tuple[str, str], tuple[float, str]] = {}


# Failed logins are counted per address and username. After a few free tries each further failure doubles the wait (up to
# five minutes) before that pair is looked at again, so a password cannot be guessed at the pace of the network. A right
# password ends it. Kept in memory: a restart forgives, which is enough against guessing.
FAILURES_FREE = 5
LOCK_MAX_SECONDS = 300
_FAILURES_MAX = 4096
_failures: dict[tuple[str, str], tuple[int, float]] = {}
# Checked against when the user does not exist, so that a wrong name costs the same time as a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-password", bcrypt.gensalt()).decode()


def _failure_key(request: Request, username: str) -> tuple[str, str]:
    return (request.client.host if request.client else "", username.lower())


def _lock_remaining(key: tuple[str, str]) -> float:
    count, last = _failures.get(key, (0, 0.0))
    if count < FAILURES_FREE:
        return 0.0
    wait = min(LOCK_MAX_SECONDS, 2 ** (count - FAILURES_FREE))
    return max(0.0, last + wait - time.monotonic())


def _record_failure(key: tuple[str, str]) -> None:
    if len(_failures) >= _FAILURES_MAX:
        now = time.monotonic()
        for stale in [k for k, (_, last) in _failures.items() if now - last > LOCK_MAX_SECONDS]:
            _failures.pop(stale, None)
        if len(_failures) >= _FAILURES_MAX:
            _failures.clear()
    count, _ = _failures.get(key, (0, 0.0))
    _failures[key] = (count + 1, time.monotonic())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def _password_checks_out(username: str, password: str, hashed: str) -> bool:
    now = time.monotonic()
    key = (username, hmac.new(_auth_key, password.encode(), hashlib.sha256).hexdigest())
    cached = _verified.get(key)
    if cached is not None and cached[0] > now and hmac.compare_digest(cached[1], hashed):
        return True
    if not verify_password(password, hashed):
        return False
    if len(_verified) >= _AUTH_CACHE_MAX:
        snapshot = list(_verified.items())  # other requests add entries meanwhile
        for stale in [k for k, (expires, _) in snapshot if expires <= now] or [k for k, _ in snapshot[: _AUTH_CACHE_MAX // 2]]:
            _verified.pop(stale, None)
    _verified[key] = (now + AUTH_CACHE_SECONDS, hashed)
    return True


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> User:
    key = _failure_key(request, credentials.username)
    remaining = _lock_remaining(key)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed logins, try again later",
            headers={"Retry-After": str(int(remaining) + 1)},
        )
    user = db_user_handler.get_user_by_username(credentials.username)
    if user is None:
        verify_password(credentials.password, _DUMMY_HASH)
    if user is None or not user.enabled or not _password_checks_out(
        credentials.username, credentials.password, user.hashed_password
    ):
        _record_failure(key)
        # No Basic challenge here: the credentials were sent and are wrong, and a challenge makes a browser open its own
        # sign-in box over the web UI's. A request with no credentials at all still gets it, from HTTPBasic.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    _failures.pop(key, None)
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
