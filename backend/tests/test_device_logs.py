import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from endpoints import devices
from handler import device_logs


@pytest.fixture
def logs(db, monkeypatch, tmp_path):
    monkeypatch.setattr(device_logs, "DEVICE_LOGS_PATH", str(tmp_path / "logs"))
    monkeypatch.setattr(device_logs, "MAX_DEVICE_LOG_BYTES", 100)
    monkeypatch.setattr(devices, "MAX_DEVICE_LOG_BYTES", 100)
    owner = SimpleNamespace(id=1)
    monkeypatch.setattr(
        devices.db_device_handler, "get_device", lambda id: SimpleNamespace(id=id, user_id=1) if id == 7 else None
    )
    return owner


def request(body: bytes, length: int | None = None):
    async def read():
        return body

    return SimpleNamespace(body=read, headers={"content-length": str(len(body) if length is None else length)})


def test_a_log_is_kept_and_a_newer_one_replaces_it(logs):
    asyncio.run(devices.put_device_log(logs, 7, request(b"first\n")))
    asyncio.run(devices.put_device_log(logs, 7, request(b"second\n")))

    assert asyncio.run(devices.get_device_log(logs, 7)).body == b"second\n"
    assert device_logs.uploaded_at(1, 7) is not None


def test_only_the_newest_whole_lines_of_a_long_log_are_kept(logs):
    lines = b"".join(b"line %02d\n" % n for n in range(40))  # 360 bytes, over the 100 allowed
    device_logs.store(1, 7, lines)

    kept = asyncio.run(devices.get_device_log(logs, 7)).body
    assert len(kept) <= 100 and kept.endswith(b"line 39\n") and kept.startswith(b"line ")


def test_another_users_device_or_an_unknown_one_is_a_404(logs):
    for device in (8,):
        with pytest.raises(HTTPException) as err:
            asyncio.run(devices.put_device_log(logs, device, request(b"x")))
        assert err.value.status_code == 404
    stranger = SimpleNamespace(id=2)
    with pytest.raises(HTTPException) as err:
        asyncio.run(devices.get_device_log(stranger, 7))
    assert err.value.status_code == 404


def test_a_device_that_never_sent_a_log_has_none_to_read(logs):
    with pytest.raises(HTTPException) as err:
        asyncio.run(devices.get_device_log(logs, 7))
    assert err.value.status_code == 404
    assert device_logs.uploaded_at(1, 7) is None


def test_a_huge_upload_is_refused_before_it_is_read(logs):
    with pytest.raises(HTTPException) as err:
        asyncio.run(devices.put_device_log(logs, 7, request(b"x", length=10_000)))
    assert err.value.status_code == 413
