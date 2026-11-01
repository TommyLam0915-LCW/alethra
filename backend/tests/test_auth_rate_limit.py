"""Credential endpoints must not be an unlimited password oracle.

``/auth/login`` and ``/auth/register`` are the only unauthenticated write paths,
so each carries a per-IP budget. These tests pin the behaviour: normal use
passes, the budget is enforced, and the two endpoints do not spend each other's.
"""

from app.api.auth import (
    LOGIN_ATTEMPTS_PER_WINDOW,
    REGISTRATIONS_PER_WINDOW,
)


def _register(client, email: str):
    return client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "testpass123",
            "display_name": "Rate Limit",
            "user_type": "individual",
        },
    )


class TestLoginBudget:
    def test_valid_login_within_budget_succeeds(self, client):
        assert _register(client, "budget-ok@alethra.io").status_code == 201
        resp = client.post(
            "/api/auth/login",
            json={"email": "budget-ok@alethra.io", "password": "testpass123"},
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]
        assert resp.headers["X-RateLimit-Limit"] == str(LOGIN_ATTEMPTS_PER_WINDOW)

    def test_repeated_bad_passwords_hit_the_budget(self, client):
        assert _register(client, "budget-bad@alethra.io").status_code == 201
        payload = {"email": "budget-bad@alethra.io", "password": "wrong-password"}

        for _ in range(LOGIN_ATTEMPTS_PER_WINDOW):
            assert client.post("/api/auth/login", json=payload).status_code == 401

        blocked = client.post("/api/auth/login", json=payload)
        assert blocked.status_code == 429
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        assert int(blocked.headers["Retry-After"]) >= 1

    def test_blocked_login_does_not_leak_whether_the_account_exists(self, client):
        payload = {"email": "nobody@alethra.io", "password": "anything"}
        for _ in range(LOGIN_ATTEMPTS_PER_WINDOW):
            assert client.post("/api/auth/login", json=payload).status_code == 401
        assert client.post("/api/auth/login", json=payload).status_code == 429


class TestRegisterBudget:
    def test_registrations_are_capped_per_ip(self, client):
        statuses = []
        for index in range(REGISTRATIONS_PER_WINDOW + 1):
            resp = _register(client, f"cap-{index}@alethra.io")
            statuses.append(resp.status_code)

        assert statuses[:REGISTRATIONS_PER_WINDOW] == [201] * REGISTRATIONS_PER_WINDOW
        assert statuses[-1] == 429

    def test_register_and_login_keep_separate_budgets(self, client):
        # Spending the whole login budget must not consume registration capacity.
        for _ in range(LOGIN_ATTEMPTS_PER_WINDOW):
            client.post(
                "/api/auth/login",
                json={"email": "nobody-else@alethra.io", "password": "x"},
            )
        assert client.post(
            "/api/auth/login",
            json={"email": "nobody-else@alethra.io", "password": "x"},
        ).status_code == 429

        assert _register(client, "still-open@alethra.io").status_code == 201
