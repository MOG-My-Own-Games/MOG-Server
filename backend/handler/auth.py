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
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from handler.database import db_user_handler
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
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> User:
    user = db_user_handler.get_user_by_username(credentials.username)
    if user is None or not user.enabled or not _password_checks_out(
        credentials.username, credentials.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
