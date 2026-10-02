from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from models.user import Role


class UserSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    enabled: bool
    role: Role
    avatar_path: str | None
    hidden_library_ids: list[int]


class UserCreateForm(BaseModel):
    username: str
    password: str
    role: Role = Role.USER


class UserUpdateForm(BaseModel):
    """Every field optional, only what's sent is changed (exclude_unset)."""

    password: str | None = None
    role: Role | None = None
    enabled: bool | None = None
    hidden_library_ids: list[int] | None = None
