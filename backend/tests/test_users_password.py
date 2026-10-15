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


def _admin(uid, enabled=True):
    return SimpleNamespace(id=uid, is_admin=True, enabled=enabled)


@pytest.fixture
def admins(monkeypatch):
    rows = {1: _admin(1), 2: _admin(2)}
    monkeypatch.setattr(users.db_user_handler, "get_user", lambda uid: rows.get(uid))
    monkeypatch.setattr(users.db_user_handler, "get_all_users", lambda: list(rows.values()))
    monkeypatch.setattr(users.db_user_handler, "update_user", lambda uid, data: rows[uid])
    monkeypatch.setattr(users.UserSchema, "model_validate", classmethod(lambda cls, row: row))
    return rows


def test_the_last_enabled_admin_cannot_be_demoted_or_disabled(admins):
    del admins[2]
    for change in (UserUpdateForm(role="user"), UserUpdateForm(enabled=False)):
        with pytest.raises(HTTPException) as err:
            asyncio.run(users.update_user(_admin(1), 1, change))
        assert err.value.status_code == 400


def test_an_admin_can_be_demoted_while_another_one_stays(admins):
    asyncio.run(users.update_user(_admin(1), 2, UserUpdateForm(role="user")))


def test_a_username_is_a_plain_name():
    UserCreateForm(username="ann.b-1@home", password="long-enough-pw")
    for bad in ("", "a b", "a/b", "x" * 65, "<script>"):
        with pytest.raises(ValidationError):
            UserCreateForm(username=bad, password="long-enough-pw")


def _upload(data: bytes):
    import io

    from starlette.datastructures import UploadFile

    return UploadFile(io.BytesIO(data), filename="a.png", headers=None)


def test_an_avatar_is_checked_as_an_image_whatever_the_client_calls_it(monkeypatch, tmp_path):
    import io

    from PIL import Image

    monkeypatch.setattr(users, "_AVATAR_DIR", tmp_path)
    monkeypatch.setattr(users.db_user_handler, "update_user", lambda uid, data: SimpleNamespace(**data))
    monkeypatch.setattr(users.UserSchema, "model_validate", classmethod(lambda cls, row: row))
    me = SimpleNamespace(id=4, is_admin=False)

    with pytest.raises(HTTPException) as err:
        asyncio.run(users.upload_avatar(me, 4, _upload(b"<html><script>alert(1)</script>")))
    assert err.value.status_code == 400 and list(tmp_path.iterdir()) == []

    png = io.BytesIO()
    Image.new("RGB", (4, 4)).save(png, "PNG")
    (tmp_path / "4.jpg").write_bytes(b"old")
    updated = asyncio.run(users.upload_avatar(me, 4, _upload(png.getvalue())))
    assert updated.avatar_path == "/resources/avatars/4.png"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["4.png"]
