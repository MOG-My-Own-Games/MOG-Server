import asyncio
from types import SimpleNamespace

from endpoints import install


def test_the_defaults_endpoint_tells_the_client_how_many_files_to_fetch(monkeypatch):
    monkeypatch.setattr(install, "default_auto_mode", lambda: True)
    monkeypatch.setattr(install, "default_manual_mode", lambda: False)
    monkeypatch.setattr(install, "default_download_workers", lambda: 6)

    result = asyncio.run(install.get_install_defaults(SimpleNamespace()))

    assert result.model_dump() == {"auto_mode": True, "manual_mode": False, "download_workers": 6}


def test_a_session_does_not_carry_the_download_setting():
    from endpoints.responses.install import InstallSessionSchema

    assert "download_workers" not in InstallSessionSchema.model_fields
