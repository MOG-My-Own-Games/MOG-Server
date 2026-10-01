"""HTTP Basic auth against the local `users` table.

Phase 1 keeps this deliberately simple: one auth method (HTTP Basic, which
works the same from a browser prompt, curl, or the CLI client with zero
client-side session/CSRF handling), two roles (admin/user, see
models.user.Role). OIDC and granular per-scope permissions are Phase 2 (see
docs/TODO.md) if they turn out to be worth the complexity here.
"""

from __future__ import annotations

from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from handler.database import db_user_handler
from models.user import User

_security = HTTPBasic()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def get_current_user(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> User:
    user = db_user_handler.get_user_by_username(credentials.username)
    if user is None or not user.enabled or not verify_password(credentials.password, user.hashed_password):
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
