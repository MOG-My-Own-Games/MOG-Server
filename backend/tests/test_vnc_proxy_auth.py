from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from endpoints import install
from handler.install import vnc


@pytest.fixture
def client(monkeypatch):
    session = SimpleNamespace(vnc_token="right-token")
    handler = SimpleNamespace(get_running_session_for_port=lambda port: session if port == 6900 else None)
    monkeypatch.setattr(install, "db_install_session_handler", handler)
    app = FastAPI()
    app.include_router(install.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize("query", ["", "?token=wrong-token", "?token="])
def test_the_websocket_needs_the_sessions_own_token(client, query):
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(f"/api/games/install/vnc/6900/websockify{query}"):
            pass
    assert refused.value.code == 4403


def test_a_port_with_no_running_install_serves_nothing(client):
    assert client.get("/api/games/install/vnc/6901/vnc.html").status_code == 404


def test_the_bridge_listens_on_loopback_only():
    """Reachable from outside, the bridge would skip the proxy's token check."""
    assert vnc.VNC_PUBLIC_HOST == "127.0.0.1"
