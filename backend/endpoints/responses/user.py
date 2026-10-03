from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from models.user import PASSWORD_MIN_LENGTH, Role


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
    password: str = Field(min_length=PASSWORD_MIN_LENGTH)
    role: Role = Role.USER


class UserUpdateForm(BaseModel):
    """Every field optional, only what's sent is changed (exclude_unset)."""

    password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH)
    role: Role | None = None
    enabled: bool | None = None
    hidden_library_ids: list[int] | None = None


class PasswordChangeForm(BaseModel):
    current_password: str
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH)
