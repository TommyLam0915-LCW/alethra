"""Single-use password-free entry for the local owner trial.

The trial entry is a convenience for one machine and one account, so the tests
focus on what it must never do: exist outside local development, accept a
remote caller, open an identity the server did not fix, issue an admin session,
reuse a spent link, or label a link session as a password check.

The candidate test-mode entry is covered separately; it must keep working
unchanged, and the ordinary password login must stay available.
"""
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError
from jose import jwt as jose_jwt

from app.auth import jwt as jwt_module
from app.config import Settings, get_settings
from tests.conftest import TestSession


STRONG_SECRET = "trial-passwordless-test-" + secrets.token_urlsafe(32)
TRIAL_EMAIL = "trial.owner@example.com"
PASSWORD = "LocalTrial!aA1x"


def _settings(**overrides) -> Settings:
    values = dict(
        app_env="development",
        database_url="sqlite:///./test-trial-login.db",
        jwt_secret_key=STRONG_SECRET,
        trial_passwordless_enabled=True,
        trial_passwordless_email=TRIAL_EMAIL,
        trial_passwordless_token_file="",
        trial_passwordless_session_minutes=90,
    )
    values.update(overrides)
    return Settings(**values)


_LOOP = None


def _loop():
    global _LOOP
    if _LOOP is None:
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def _call(app, method: str, path: str, *, peer="127.0.0.1", json=None, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else None

    async def run():
        transport = httpx.ASGITransport(app=app, client=(peer, 50123))
        async with httpx.AsyncClient(transport=transport, base_url="http://trial.test") as async_client:
            return await async_client.request(method, path, json=json, headers=headers)

    return _loop().run_until_complete(run())


def _write_token(path, token: str | None = None, mode: int = 0o600) -> str:
    value = token or secrets.token_urlsafe(32)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(mode)
    return value


@pytest.fixture()
def token_file(tmp_path):
    path = tmp_path / "trial-login-token"
    _write_token(path)
    return path


@pytest.fixture()
def trial_app(client, token_file):
    from app.main import app

    settings = _settings(trial_passwordless_token_file=str(token_file))
    app.dependency_overrides[get_settings] = lambda: settings
    yield app
    app.dependency_overrides.pop(get_settings, None)


def _register(client, email: str = TRIAL_EMAIL, password: str = PASSWORD):
    resp = client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": password,
            "display_name": "Trial Owner",
            "user_type": "individual",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _exchange(app, token: str, peer: str = "127.0.0.1"):
    return _call(app, "POST", "/api/auth/trial-login", peer=peer, json={"token": token})


class TestDisabledByDefault:
    def test_default_settings_refuse_the_entry(self, client):
        resp = client.post("/api/auth/trial-login", json={"token": "x" * 43})
        assert resp.status_code == 404
        assert "access_token" not in resp.text

    def test_entry_is_off_unless_explicitly_enabled(self):
        values = dict(
            app_env="development",
            database_url="sqlite:///./test-trial-login.db",
            jwt_secret_key=STRONG_SECRET,
        )
        settings = Settings(**values)
        assert settings.trial_passwordless_enabled is False


class TestExchange:
    def test_loopback_link_opens_the_one_account(self, client, trial_app, token_file):
        owner = _register(client)
        token = token_file.read_text().strip()

        resp = _exchange(trial_app, token)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["user"]["email"] == TRIAL_EMAIL
        assert body["user"]["id"] == owner["id"]
        assert body["user"]["is_admin"] is False
        assert body["expires_in"] == 90 * 60
        assert "single-use" in body["entry_label"]

        payload = jose_jwt.decode(
            body["access_token"],
            jwt_module.settings.jwt_secret_key,
            algorithms=[jwt_module.settings.jwt_algorithm],
        )
        assert payload["sub"] == owner["id"]
        # A link session is not a password check: provenance must stay unknown.
        assert payload["amr"] == ["trial_link"]
        assert "pwd" not in payload["amr"]
        remaining = payload["exp"] - datetime.now(timezone.utc).timestamp()
        assert 89 * 60 < remaining <= 90 * 60 + 5

        me = _call(trial_app, "GET", "/api/auth/me", token=body["access_token"])
        assert me.status_code == 200
        assert me.json()["email"] == TRIAL_EMAIL

    def test_link_is_consumed_on_first_use(self, client, trial_app, token_file):
        _register(client)
        token = token_file.read_text().strip()

        first = _exchange(trial_app, token)
        assert first.status_code == 200

        rotated = token_file.read_text().strip()
        assert rotated and rotated != token

        second = _exchange(trial_app, token)
        assert second.status_code == 401
        assert "access_token" not in second.text

        # The rotated value is the live one.
        assert _exchange(trial_app, rotated).status_code == 200

    def test_wrong_token_does_not_consume_the_link(self, client, trial_app, token_file):
        _register(client)
        token = token_file.read_text().strip()

        wrong = _exchange(trial_app, secrets.token_urlsafe(32))
        assert wrong.status_code == 401
        assert token_file.read_text().strip() == token
        assert _exchange(trial_app, token).status_code == 200

    def test_non_ascii_token_is_refused_without_a_server_error(self, client, trial_app):
        _register(client)
        resp = _exchange(trial_app, "é" * 43)
        assert resp.status_code == 401
        assert "access_token" not in resp.text

    def test_remote_peer_cannot_redeem(self, client, trial_app, token_file):
        _register(client)
        token = token_file.read_text().strip()
        resp = _exchange(trial_app, token, peer="203.0.113.9")
        assert resp.status_code == 403
        assert "access_token" not in resp.text
        # The refused attempt must not burn the link.
        assert token_file.read_text().strip() == token

    def test_testclient_peer_is_not_loopback(self, client, trial_app, token_file):
        _register(client)
        resp = client.post("/api/auth/trial-login", json={"token": token_file.read_text().strip()})
        assert resp.status_code == 403

    def test_client_cannot_name_an_identity(self, client, trial_app, token_file):
        _register(client)
        token = token_file.read_text().strip()
        resp = _call(
            trial_app,
            "POST",
            "/api/auth/trial-login",
            json={"token": token, "email": "attacker@example.com"},
        )
        assert resp.status_code == 422

    def test_short_token_is_refused_by_schema(self, client, trial_app):
        _register(client)
        resp = _exchange(trial_app, "short")
        assert resp.status_code == 422

    def test_missing_account_fails_closed(self, trial_app, token_file):
        token = token_file.read_text().strip()
        resp = _exchange(trial_app, token)
        assert resp.status_code == 404
        assert "access_token" not in resp.text

    def test_inactive_account_fails_closed(self, client, trial_app, token_file):
        from app.models.user import User

        _register(client)
        db = TestSession()
        try:
            user = db.query(User).filter(User.email == TRIAL_EMAIL).first()
            user.is_active = False
            db.commit()
        finally:
            db.close()

        resp = _exchange(trial_app, token_file.read_text().strip())
        assert resp.status_code == 404

    def test_admin_account_is_never_issued(self, client, trial_app, token_file):
        from app.models.user import User
        from app.auth.jwt import get_password_hash

        db = TestSession()
        try:
            db.add(
                User(
                    email=TRIAL_EMAIL,
                    password_hash=get_password_hash(PASSWORD),
                    display_name="Admin",
                    is_admin=True,
                )
            )
            db.commit()
        finally:
            db.close()

        resp = _exchange(trial_app, token_file.read_text().strip())
        assert resp.status_code == 403
        assert "access_token" not in resp.text

    def test_open_token_file_permissions_refuse_the_entry(self, client, token_file):
        from app.main import app

        _register(client)
        token = token_file.read_text().strip()
        # Startup accepts 0600; a later chmod must close the entry without a
        # restart, so the check also runs at request time.
        settings = _settings(trial_passwordless_token_file=str(token_file))
        token_file.chmod(0o644)
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            resp = _exchange(app, token)
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert resp.status_code == 503
        assert "access_token" not in resp.text

    def test_missing_token_file_refuses_the_entry(self, client, token_file):
        from app.main import app

        _register(client)
        # Startup requires the file; deleting it afterwards must fail closed.
        settings = _settings(trial_passwordless_token_file=str(token_file))
        token_file.unlink()
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            resp = _exchange(app, secrets.token_urlsafe(32))
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert resp.status_code == 503

    def test_exchange_writes_an_unknown_provenance_audit_event(self, client, trial_app, token_file):
        from app.models.audit_event import AuditEvent

        _register(client)
        resp = _exchange(trial_app, token_file.read_text().strip())
        assert resp.status_code == 200

        db = TestSession()
        try:
            event = (
                db.query(AuditEvent)
                .filter(AuditEvent.event_type == "trial_passwordless_login")
                .one()
            )
            assert event.actor_type == "unknown"
            assert event.entity_type == "user"
            assert event.payload_json["entry"] == "trial_link"
            assert event.payload_json["single_use"] is True
            assert token_file.read_text().strip() not in event.payload_json.values()
        finally:
            db.close()


class TestExistingAuthAndIsolation:
    def test_password_login_still_works(self, client, trial_app, token_file):
        _register(client)
        resp = client.post("/api/auth/login", json={"email": TRIAL_EMAIL, "password": PASSWORD})
        assert resp.status_code == 200
        payload = jose_jwt.decode(
            resp.json()["access_token"],
            jwt_module.settings.jwt_secret_key,
            algorithms=[jwt_module.settings.jwt_algorithm],
        )
        # The ordinary customer entry still records a password check.
        assert payload["amr"] == ["pwd"]

    def test_candidate_entry_is_unaffected(self, client, trial_app, token_file):
        # Candidate mode is off here, so its endpoint keeps its normal shape.
        resp = client.get("/api/auth/candidate-session")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_link_session_cannot_read_another_accounts_data(self, client, trial_app, token_file):
        _register(client)
        other_email = "trial.other@example.com"
        _register(client, other_email)
        login = client.post("/api/auth/login", json={"email": other_email, "password": PASSWORD})
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = client.post(
            "/api/v1/experiments/",
            headers=other_headers,
            json={
                "title": "Other",
                "hypothesis": "isolation probe",
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
            },
        )
        assert created.status_code == 201, created.text

        token = _exchange(trial_app, token_file.read_text().strip()).json()["access_token"]
        own = _call(trial_app, "GET", "/api/v1/experiments/", token=token)
        assert own.status_code == 200
        assert own.json() == []
        read_other = _call(
            trial_app, "GET", f"/api/v1/experiments/{created.json()['id']}", token=token
        )
        assert read_other.status_code == 404


class TestSettingsFailClosed:
    @pytest.mark.parametrize("env", ["production", "staging"])
    def test_production_like_environments_refuse_the_entry(self, env, token_file):
        with pytest.raises(ValidationError, match="TRIAL_PASSWORDLESS_ENABLED"):
            _settings(
                app_env=env,
                trial_passwordless_token_file=str(token_file),
            )

    def test_weak_jwt_secret_is_refused(self, token_file):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
            _settings(
                jwt_secret_key="short",
                trial_passwordless_token_file=str(token_file),
            )

    def test_remote_database_is_refused(self, token_file):
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            _settings(
                database_url="postgresql://user:pass@db.example.com:5432/alethra",
                trial_passwordless_token_file=str(token_file),
            )

    def test_email_is_required(self, token_file):
        with pytest.raises(ValidationError, match="TRIAL_PASSWORDLESS_EMAIL"):
            _settings(
                trial_passwordless_email="",
                trial_passwordless_token_file=str(token_file),
            )

    def test_token_file_must_exist(self, tmp_path):
        with pytest.raises(ValidationError, match="TRIAL_PASSWORDLESS_TOKEN_FILE"):
            _settings(trial_passwordless_token_file=str(tmp_path / "absent"))

    def test_token_file_must_be_0600(self, tmp_path):
        path = tmp_path / "trial-login-token"
        _write_token(path, mode=0o644)
        with pytest.raises(ValidationError, match="0600"):
            _settings(trial_passwordless_token_file=str(path))

    @pytest.mark.parametrize("minutes", [0, 1441])
    def test_session_minutes_are_bounded(self, minutes, token_file):
        with pytest.raises(ValidationError, match="SESSION_MINUTES"):
            _settings(
                trial_passwordless_session_minutes=minutes,
                trial_passwordless_token_file=str(token_file),
            )
