import asyncio
from types import SimpleNamespace

import pytest
from endpoints import users
from endpoints.responses.user import PasswordChangeForm, UserCreateForm, UserUpdateForm
from fastapi import HTTPException
from handler.auth import hash_password, verify_password
from pydantic import ValidationError


def _user(password="old-password"):
    return SimpleNamespace(id=3, hashed_password=hash_password(password))


@pytest.fixture
def store(monkeypatch):
    saved = {}
    monkeypatch.setattr(users.db_user_handler, "update_user", lambda uid, data: saved.update({uid: data}))
    return saved


def test_changing_your_password_needs_the_current_one(store):
    asyncio.run(users.change_my_password(_user(), PasswordChangeForm(current_password="old-password", new_password="brand-new-pw")))
    assert verify_password("brand-new-pw", store[3]["hashed_password"])


def test_wrong_current_password_is_a_400_not_a_401(store):
    with pytest.raises(HTTPException) as err:
        asyncio.run(users.change_my_password(_user(), PasswordChangeForm(current_password="nope", new_password="brand-new-pw")))
    assert err.value.status_code == 400 and not store


def test_the_new_password_must_differ(store):
    with pytest.raises(HTTPException) as err:
        asyncio.run(users.change_my_password(_user(), PasswordChangeForm(current_password="old-password", new_password="old-password")))
    assert err.value.status_code == 400 and not store


def test_short_passwords_are_refused_everywhere():
    for build in (
        lambda: PasswordChangeForm(current_password="x", new_password="short"),
        lambda: UserCreateForm(username="a", password="short"),
        lambda: UserUpdateForm(password="short"),
    ):
        with pytest.raises(ValidationError):
            build()
