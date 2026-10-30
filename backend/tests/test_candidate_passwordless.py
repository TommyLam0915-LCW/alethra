"""Password-free local candidate entry: scope, allowlist and fail-closed guards.

The feature is a trial-entry convenience, so the tests focus on what it must
never do: serve a remote caller, issue an identity the server did not list,
hand out an admin session, widen data access, or boot under a production
configuration. The happy path only proves a normal short-lived JWT is minted
for the configured synthetic account.
"""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError
from jose import jwt as jose_jwt

from app.auth import candidate_access
from app.auth import jwt as jwt_module
from app.config import Settings, get_settings
from tests.conftest import TestSession


STRONG_SECRET = "candidate-passwordless-test-" + secrets.token_urlsafe(32)
OWNER_EMAIL = "candidate.owner@example.com"
THIN_EMAIL = "candidate.thin@example.com"
PASSWORD = "LocalTrial!aA1x"


def _settings(**overrides) -> Settings:
    values = dict(
        app_env="development",
        database_url="sqlite:///./test-candidate-access.db",
        jwt_secret_key=STRONG_SECRET,
        candidate_passwordless_mode=True,
        candidate_passwordless_accounts=f"owner={OWNER_EMAIL},thin={THIN_EMAIL}",
        candidate_passwordless_session_minutes=15,
    )
    values.update(overrides)
    return Settings(**values)


def _call(app, method: str, path: str, *, peer="127.0.0.1", json=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else None

    async def run():
        transport = httpx.ASGITransport(app=app, client=(peer, 50123))
        async with httpx.AsyncClient(transport=transport, base_url="http://candidate.test") as async_client:
            return await async_client.request(method, path, json=json, headers=headers)

    return asyncio.run(run())


@pytest.fixture()
def candidate_app(client):
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: _settings()
    yield app
    app.dependency_overrides.pop(get_settings, None)


def _register(client, email: str, password: str = PASSWORD):
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "Candidate", "user_type": "individual"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _experiment_payload(title: str) -> dict:
    return {
        "title": title,
        "hypothesis": "ownership isolation probe",
        "research_type": "quantitative",
        "strategy_spec": {
            "schema_version": "1.0",
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "frequency": "1d",
            "start_date": "2024-01-02",
            "end_date": "2024-02-14",
            "initial_cash": 10000,
            "signal_parameters": {},
            "commission_rate": 0.001,
            "slippage_rate": 0.001,
            "benchmark_id": "DEMO-BENCHMARK",
            "currency": "USD",
        },
    }


class TestDisabledByDefault:
    def test_status_is_disabled_with_real_settings(self, client):
        resp = client.get("/api/auth/candidate-session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert body["accounts"] == []

    def test_session_is_not_offered(self, client):
        resp = client.post("/api/auth/candidate-session", json={"role": "owner"})
        assert resp.status_code == 404


class TestStatusSurface:
    def test_remote_peer_learns_nothing(self, candidate_app):
        resp = _call(candidate_app, "GET", "/api/auth/candidate-session", peer="203.0.113.9")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert resp.json()["accounts"] == []

    def test_loopback_lists_roles_without_emails(self, candidate_app):
        resp = _call(candidate_app, "GET", "/api/auth/candidate-session")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["synthetic"] is True
        assert "本機測試模式" in body["label_zh"]
        assert [account["role"] for account in body["accounts"]] == ["owner", "thin"]
        assert OWNER_EMAIL not in resp.text and THIN_EMAIL not in resp.text
        assert body["session_minutes"] == 15


class TestSessionIssuance:
    def test_loopback_gets_a_short_lived_identity_bound_token(self, client, candidate_app):
        owner = _register(client, OWNER_EMAIL)
        resp = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["expires_in"] == 15 * 60
        assert body["role"] == "owner"
        assert body["user"]["email"] == OWNER_EMAIL
        assert body["user"]["is_admin"] is False

        payload = jose_jwt.decode(
            body["access_token"], jwt_module.settings.jwt_secret_key, algorithms=[jwt_module.settings.jwt_algorithm]
        )
        assert payload["sub"] == owner["id"]
        assert payload["cand"] == "local_passwordless"
        assert payload["role"] == "owner"
        remaining = payload["exp"] - datetime.now(timezone.utc).timestamp()
        assert 14 * 60 < remaining <= 15 * 60 + 5

    def test_remote_peer_cannot_create_a_session(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        resp = _call(
            candidate_app, "POST", "/api/auth/candidate-session", peer="203.0.113.9", json={"role": "owner"}
        )
        assert resp.status_code == 403
        assert "access_token" not in resp.text

    def test_testclient_peer_is_not_loopback(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        resp = client.post("/api/auth/candidate-session", json={"role": "owner"})
        assert resp.status_code == 403

    def test_unlisted_role_is_refused(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        resp = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "trained"})
        assert resp.status_code == 400

    def test_client_cannot_name_an_identity(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        for extra in ({"email": "attacker@example.com"}, {"user_id": "00000000-0000-0000-0000-000000000000"}):
            resp = _call(
                candidate_app,
                "POST",
                "/api/auth/candidate-session",
                json={"role": "owner", **extra},
            )
            assert resp.status_code == 422, extra

    def test_admin_account_is_never_issued(self, client, candidate_app):
        from app.models.user import User
        from app.auth.jwt import get_password_hash

        db = TestSession()
        try:
            db.add(
                User(
                    email=THIN_EMAIL,
                    password_hash=get_password_hash(PASSWORD),
                    display_name="Admin",
                    is_admin=True,
                )
            )
            db.commit()
        finally:
            db.close()

        resp = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "thin"})
        assert resp.status_code == 403
        assert "access_token" not in resp.text

    def test_missing_account_fails_closed(self, client, candidate_app):
        resp = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        assert resp.status_code == 404
        assert "access_token" not in resp.text

    def test_inactive_account_fails_closed(self, client, candidate_app):
        from app.models.user import User

        _register(client, OWNER_EMAIL)
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == OWNER_EMAIL).first()
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        assert resp.status_code == 404


class TestExistingAuthAndIsolation:
    def test_password_login_still_works_with_mode_on(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        resp = client.post(
            "/api/auth/login", json={"email": OWNER_EMAIL, "password": PASSWORD}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_session_token_cannot_read_another_accounts_data(self, client, candidate_app):
        _register(client, OWNER_EMAIL)
        other_email = "candidate.other@example.com"
        _register(client, other_email)
        login = client.post("/api/auth/login", json={"email": other_email, "password": PASSWORD})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post("/api/v1/experiments/", headers=other_headers, json=_experiment_payload("Other"))
        assert created.status_code == 201, created.text
        other_experiment_id = created.json()["id"]

        session = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        token = session.json()["access_token"]

        own_list = _call(candidate_app, "GET", "/api/v1/experiments/", token=token)
        assert own_list.status_code == 200
        assert own_list.json() == []

        read_other = _call(
            candidate_app, "GET", f"/api/v1/experiments/{other_experiment_id}", token=token
        )
        assert read_other.status_code == 404

    def test_me_endpoint_resolves_the_same_identity(self, client, candidate_app):
        owner = _register(client, OWNER_EMAIL)
        token = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"}).json()[
            "access_token"
        ]
        me = _call(candidate_app, "GET", "/api/auth/me", token=token)
        assert me.status_code == 200
        assert me.json()["id"] == owner["id"]


class TestSessionLifetime:
    def test_expired_session_token_is_rejected(self, candidate_app):
        token = jwt_module.create_access_token(
            data={"sub": "00000000-0000-0000-0000-000000000000", "cand": "local_passwordless"},
            expires_delta=timedelta(minutes=-1),
        )
        resp = _call(candidate_app, "GET", "/api/auth/me", token=token)
        assert resp.status_code == 401

    def test_mode_off_stops_issuance_but_keeps_live_sessions_until_expiry(
        self, client, candidate_app
    ):
        """Turning the mode off is not a revocation: already-issued tokens stay valid.

        The client therefore renews only on a 401 and never treats a missing
        status as a sign that the current session was ended.
        """
        _register(client, OWNER_EMAIL)
        issued = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        assert issued.status_code == 200
        token = issued.json()["access_token"]

        candidate_app.dependency_overrides[get_settings] = lambda: _settings(
            candidate_passwordless_mode=False, candidate_passwordless_accounts=""
        )

        refused = _call(candidate_app, "POST", "/api/auth/candidate-session", json={"role": "owner"})
        assert refused.status_code == 404

        still_valid = _call(candidate_app, "GET", "/api/auth/me", token=token)
        assert still_valid.status_code == 200
        assert still_valid.json()["email"] == OWNER_EMAIL


class TestSettingsFailClosed:
    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_production_like_environments_refuse_the_mode(self, env):
        with pytest.raises(ValidationError, match="CANDIDATE_PASSWORDLESS_MODE"):
            _settings(app_env=env)

    def test_non_development_environment_refuses_the_mode(self):
        with pytest.raises(ValidationError, match="CANDIDATE_PASSWORDLESS_MODE"):
            _settings(app_env="qa")

    def test_default_jwt_secret_refuses_the_mode(self):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
            _settings(jwt_secret_key="change-me-in-production-use-a-long-random-string")

    def test_short_jwt_secret_refuses_the_mode(self):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
            _settings(jwt_secret_key="short-secret")

    def test_remote_database_refuses_the_mode(self):
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            _settings(database_url="postgresql://u:p@db.example.com:5432/app")

    @pytest.mark.parametrize(
        "accounts",
        ["", "owner", "Owner=owner@example.com", "owner=not-an-email", "owner=a@example.com,owner=b@example.com"],
    )
    def test_allowlist_must_be_explicit_and_unique(self, accounts):
        with pytest.raises(ValidationError, match="CANDIDATE_PASSWORDLESS_ACCOUNTS"):
            _settings(candidate_passwordless_accounts=accounts)

    @pytest.mark.parametrize("minutes", [0, 121])
    def test_session_length_is_bounded(self, minutes):
        with pytest.raises(ValidationError, match="CANDIDATE_PASSWORDLESS_SESSION_MINUTES"):
            _settings(candidate_passwordless_session_minutes=minutes)

    @pytest.mark.parametrize(
        "database_url",
        ["sqlite:///./local.db", "postgresql://u:p@127.0.0.1:5432/app", "postgresql+psycopg2://u:p@localhost:5432/app"],
    )
    def test_local_databases_are_accepted(self, database_url):
        settings = _settings(database_url=database_url)
        assert settings.candidate_passwordless_mode is True


class TestParsingHelpers:
    def test_allowlist_normalizes_email_case(self):
        assert candidate_access.parse_account_allowlist("owner= Candidate.Owner@Example.COM ") == {
            "owner": "candidate.owner@example.com"
        }

    def test_unknown_role_label_falls_back_to_role(self):
        assert candidate_access.role_label("scenario_x") == "scenario_x"

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
    def test_loopback_hosts(self, host):
        assert candidate_access.is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["10.0.0.8", "203.0.113.9", "localhost", "", None])
    def test_non_loopback_hosts(self, host):
        assert candidate_access.is_loopback_host(host) is False
