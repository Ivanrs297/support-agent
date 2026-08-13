"""Who gets in, who gets locked out, and who gets told to slow down.

These run against the real endpoints with a real token, but never reach Groq: a
request that is refused is refused before the model is ever called, which is the
whole point of refusing it there.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import config, security
from app.main import app

AUTH = {"Authorization": f"Bearer {config.settings.api_token}"}


@pytest.fixture
def limits(monkeypatch):
    """Swap in different limits. Settings is frozen, so it is replaced whole."""

    def apply(**changes):
        monkeypatch.setattr(config, "settings", replace(config.settings, **changes))

    return apply


@pytest.fixture
def client():
    # raise_server_exceptions=False so a request that passes the gate and then
    # fails at Groq surfaces as a 500 instead of blowing up the test.
    security.reset_state()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    security.reset_state()


def address(ip: str) -> dict[str, str]:
    """Caddy sets X-Forwarded-For; the lockout is keyed on it."""
    return {"X-Forwarded-For": ip}


class TestAuthentication:
    def test_health_needs_no_token(self, client):
        # The container healthcheck calls this. A healthcheck that needs a
        # secret is a healthcheck that fails for the wrong reasons.
        assert client.get("/health").status_code == 200

    def test_ui_is_public(self, client):
        # The page itself is not the secret; it asks for one.
        assert client.get("/ui").status_code == 200

    def test_chat_rejects_a_missing_token(self, client):
        response = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 401

    def test_chat_rejects_a_wrong_token(self, client):
        response = client.post(
            "/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer not-the-token"},
        )
        assert response.status_code == 401

    def test_session_accepts_the_right_token(self, client):
        response = client.get("/session", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["status"] == "authenticated"


class TestLockout:
    def test_locks_out_after_the_configured_attempts(self, client):
        wrong = {"Authorization": "Bearer wrong", **address("203.0.113.7")}

        for _ in range(config.settings.max_token_attempts):
            assert client.get("/session", headers=wrong).status_code == 401

        # The next one is refused without even looking at the token.
        response = client.get("/session", headers=wrong)
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0

    def test_lockout_is_per_address(self, client):
        # Otherwise five bad requests from anyone would take the system down
        # for everyone — a denial of service handed out for free.
        wrong = {"Authorization": "Bearer wrong", **address("203.0.113.7")}
        for _ in range(config.settings.max_token_attempts + 1):
            client.get("/session", headers=wrong)

        assert (
            client.get("/session", headers={**AUTH, **address("198.51.100.9")}).status_code
            == 200
        )

    def test_a_correct_token_clears_the_record(self, client):
        # Four typos followed by the right token should not leave someone one
        # mistake away from a lockout for the rest of the window.
        wrong = {"Authorization": "Bearer wrong", **address("203.0.113.7")}
        for _ in range(config.settings.max_token_attempts - 1):
            client.get("/session", headers=wrong)

        assert client.get("/session", headers={**AUTH, **address("203.0.113.7")}).status_code == 200

        for _ in range(config.settings.max_token_attempts - 1):
            assert client.get("/session", headers=wrong).status_code == 401


class TestRateLimits:
    def test_per_token_window(self, client, limits):
        limits(rate_limit_per_minute=3)
        body = {"messages": [{"role": "user", "content": "hi"}]}

        for _ in range(3):
            assert client.post("/chat", json=body, headers=AUTH).status_code != 429

        response = client.post("/chat", json=body, headers=AUTH)
        assert response.status_code == 429
        assert "per minute" in response.json()["detail"]
        assert int(response.headers["Retry-After"]) > 0

    def test_daily_cap(self, client, limits):
        limits(rate_limit_per_minute=1000, daily_request_cap=4)
        body = {"messages": [{"role": "user", "content": "hi"}]}

        for _ in range(4):
            assert client.post("/chat", json=body, headers=AUTH).status_code != 429

        assert client.post("/chat", json=body, headers=AUTH).status_code == 429

    def test_checking_a_token_costs_no_quota(self, client, limits):
        # The browser client validates a token before it will accept a
        # question. If that check were charged, opening the page would eat the
        # quota of whoever opened it.
        limits(rate_limit_per_minute=2)

        for _ in range(5):
            assert client.get("/session", headers=AUTH).status_code == 200

        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert client.post("/chat", json=body, headers=AUTH).status_code != 429
