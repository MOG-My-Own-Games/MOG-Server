from types import SimpleNamespace

from handler import auth


def _hashed(password):
    return auth.hash_password(password)


def test_a_verified_password_skips_bcrypt_next_time(monkeypatch):
    auth._verified.clear()
    hashed = _hashed("secret-pass")
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(1) or real(p, h))

    assert auth._password_checks_out("ann", "secret-pass", hashed)
    assert auth._password_checks_out("ann", "secret-pass", hashed)
    assert len(calls) == 1


def test_a_wrong_password_is_never_cached(monkeypatch):
    auth._verified.clear()
    hashed = _hashed("secret-pass")
    assert not auth._password_checks_out("ann", "wrong", hashed)
    assert not auth._password_checks_out("ann", "wrong", hashed)
    assert auth._verified == {}


def test_changing_the_password_ends_the_cached_login():
    auth._verified.clear()
    old = _hashed("old-password")
    assert auth._password_checks_out("ann", "old-password", old)
    new = _hashed("new-password")
    assert not auth._password_checks_out("ann", "old-password", new)  # the stored hash changed
    assert auth._password_checks_out("ann", "new-password", new)


def test_an_entry_expires(monkeypatch):
    auth._verified.clear()
    hashed = _hashed("secret-pass")
    assert auth._password_checks_out("ann", "secret-pass", hashed)
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(1) or real(p, h))
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: 10**12))
    assert auth._password_checks_out("ann", "secret-pass", hashed)
    assert len(calls) == 1


def test_the_cache_holds_no_plaintext_passwords():
    auth._verified.clear()
    hashed = _hashed("secret-pass")
    auth._password_checks_out("ann", "secret-pass", hashed)
    assert all("secret-pass" not in key[1] for key in auth._verified)
