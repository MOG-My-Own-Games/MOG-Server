from unittest.mock import patch

from handler.auth import hash_password, verify_password
from models.user import Role, User
from startup import _ensure_default_admin


def _admin(password: str) -> User:
    return User(id=1, username="admin", hashed_password=hash_password(password), role=Role.ADMIN, enabled=True)


@patch("startup.db_user_handler")
class TestEnsureDefaultAdmin:
    def test_creates_admin_when_no_users(self, db, monkeypatch):
        monkeypatch.setenv("MOG_ADMIN_PASSWORD", "secret")
        db.get_all_users.return_value = []

        _ensure_default_admin()

        created = db.add_user.call_args.args[0]
        assert created.username == "admin"
        assert verify_password("secret", created.hashed_password)

    def test_rewrites_hash_when_env_password_changed(self, db, monkeypatch):
        monkeypatch.setenv("MOG_ADMIN_PASSWORD", "new")
        admin = _admin("old")
        db.get_all_users.return_value = [admin]
        db.get_user_by_username.return_value = admin

        _ensure_default_admin()

        db.update_user.assert_called_once()
        assert db.update_user.call_args.args[0] == 1
        assert verify_password("new", db.update_user.call_args.args[1]["hashed_password"])

    def test_leaves_hash_when_password_unchanged(self, db, monkeypatch):
        monkeypatch.setenv("MOG_ADMIN_PASSWORD", "same")
        admin = _admin("same")
        db.get_all_users.return_value = [admin]
        db.get_user_by_username.return_value = admin

        _ensure_default_admin()

        db.update_user.assert_not_called()

    def test_leaves_hash_when_env_password_unset(self, db, monkeypatch):
        monkeypatch.delenv("MOG_ADMIN_PASSWORD", raising=False)
        admin = _admin("whatever")
        db.get_all_users.return_value = [admin]
        db.get_user_by_username.return_value = admin

        _ensure_default_admin()

        db.update_user.assert_not_called()
