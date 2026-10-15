from fastapi.testclient import TestClient

import main


def _get(path):
    return TestClient(main.app).get(path)


def test_every_response_forbids_type_guessing_and_foreign_framing():
    response = _get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_the_web_ui_gets_a_content_policy_but_the_api_pages_do_not():
    assert "script-src 'self'" in main.FRONTEND_CSP
    assert "Content-Security-Policy" not in _get("/api/health").headers
    assert "script-src 'self'" in _get("/app.js").headers["Content-Security-Policy"]
