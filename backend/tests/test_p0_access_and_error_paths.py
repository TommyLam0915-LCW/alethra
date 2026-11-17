"""P0 batch regressions: guard coverage, cross-user reads, and error bodies.

Four defects had no test that could have caught them:

1. ``/api/v1/kill-switches/*`` authenticated with ``get_current_user`` and then
   checked ``is_admin`` itself, so a loopback bypass token (which
   ``require_admin`` rejects) could list and flip global kill switches.
2. ``GET /api/v1/versions/compare`` filtered ownership on version A only, so any
   logged-in user could diff a stranger's version UUID and read its
   ``strategy_spec`` field values.
3. Catch-all ``except Exception`` handlers echoed ``str(exc)`` into the response
   body; that text can carry a DSN with a password or an absolute path.
4. Version numbers were derived from ``count()``, which repeats a number as soon
   as the sequence has a gap.

Each test below fails against the pre-fix build for the reason stated in its
docstring, not because of an unrelated assertion.
"""

import uuid
from datetime import datetime, timezone

import pytest


KILL_SWITCH_ROUTES = [
    ("GET", "/api/v1/kill-switches", None),
    ("GET", "/api/v1/kill-switches/global", None),
    ("POST", "/api/v1/kill-switches/activate", {"scope": "global", "reason": "incident"}),
    ("POST", "/api/v1/kill-switches/deactivate", {"scope": "global"}),
]


def _switch():
    """A stand-in row: the routes serialize it through KillSwitchResponse."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    class _Row:
        id = uuid.uuid4()
        scope = "global"
        is_active = True
        activated_at = now
        activated_by = "operator"
        reason = "incident"
        created_at = now
        updated_at = now

    return _Row()


class TestKillSwitchGuardRejectsNonAdmin:
    @pytest.mark.parametrize("method,path,body", KILL_SWITCH_ROUTES)
    def test_unauthenticated(self, client, method, path, body):
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401, f"{method} {path} must reject anonymous"

    @pytest.mark.parametrize("method,path,body", KILL_SWITCH_ROUTES)
    def test_regular_user(self, client, auth_headers, method, path, body):
        resp = client.request(method, path, json=body, headers=auth_headers)
        assert resp.status_code == 403, f"{method} {path} must reject non-admin"
        assert "Admin access required" in resp.json()["detail"]


class TestKillSwitchGuardRejectsBypass:
    """The defect: a bypass-authenticated admin passed the old inline check.

    ``require_admin`` is what rejects ``auth_amr=["bypass"]``; a route that only
    uses ``get_current_user`` and inspects ``is_admin`` never reaches it.
    """

    @pytest.mark.parametrize("method,path,body", KILL_SWITCH_ROUTES)
    def test_bypass_admin_rejected(self, client, method, path, body):
        from app.auth.jwt import get_current_user, get_password_hash, security
        from app.database import get_db
        from app.main import app
        from app.models.user import User
        from fastapi import Depends
        from fastapi.security import HTTPAuthorizationCredentials
        from sqlalchemy.orm import Session
        from starlette.requests import Request
        from tests.conftest import TestSession

        db = TestSession()
        saved = app.dependency_overrides.copy()
        try:
            admin = User(
                email=f"ks-bypass-{uuid.uuid4().hex[:8]}@alethra.io",
                password_hash=get_password_hash("bypasspass123"),
                display_name="Kill Switch Bypass Admin",
                is_admin=True,
                is_active=True,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

            def bypass_get_current_user(
                request: Request,
                credentials: HTTPAuthorizationCredentials = Depends(security),
                session: Session = Depends(get_db),
            ):
                request.state.auth_amr = ["bypass"]
                request.state.api_key_scopes = None
                request.state.api_key_id = None
                return admin

            app.dependency_overrides[get_current_user] = bypass_get_current_user
            with pytest.MonkeyPatch.context() as mp:
                calls = []
                mp.setattr("app.api.kill_switches.list_kill_switches",
                           lambda *a, **k: calls.append("list") or [])
                mp.setattr("app.api.kill_switches.get_kill_switch",
                           lambda *a, **k: calls.append("get") or _switch())
                mp.setattr("app.api.kill_switches.activate_kill_switch",
                           lambda *a, **k: calls.append("activate") or _switch())
                mp.setattr("app.api.kill_switches.deactivate_kill_switch",
                           lambda *a, **k: calls.append("deactivate") or _switch())

                resp = client.request(method, path, json=body)

            assert resp.status_code == 403, (
                f"{method} {path} accepted a bypass-authenticated admin"
            )
            assert "bypass" in resp.json()["detail"].lower()
            assert calls == [], f"{method} {path} reached the service: {calls}"
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(saved)
            db.rollback()
            db.query(User).filter(
                User.email.like("ks-bypass-%@alethra.io")
            ).delete(synchronize_session=False)
            db.commit()
            db.close()


class TestKillSwitchAdminAccepted:
    """The guard must not be widened into a lockout: real admin still works."""

    def test_list_and_activate(self, client, admin_headers):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.api.kill_switches.list_kill_switches", lambda *a, **k: [])
            mp.setattr("app.api.kill_switches.activate_kill_switch",
                       lambda *a, **k: _switch())
            listing = client.get("/api/v1/kill-switches", headers=admin_headers)
            activating = client.post(
                "/api/v1/kill-switches/activate",
                headers=admin_headers,
                json={"scope": "global", "reason": "incident"},
            )
        assert listing.status_code == 200, listing.text
        assert activating.status_code == 201, activating.text


BASE_SPEC = {
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
}


def _register_user(client, email: str) -> dict:
    client.post("/api/auth/register", json={
        "risk_acknowledged": True,
        "email": email,
        "password": "comparepass123",
        "display_name": "Compare User",
        "user_type": "individual",
    })
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "comparepass123"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _experiment(client, headers, title: str) -> dict:
    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": title,
        "hypothesis": "Whose version is this?",
        "research_type": "quantitative",
        "strategy_spec": dict(BASE_SPEC),
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestVersionCompareCrossUserIsolation:
    def test_stranger_version_is_not_comparable(self, client, auth_headers):
        victim = _register_user(client, f"victim-{uuid.uuid4().hex[:8]}@alethra.io")
        mine = _experiment(client, auth_headers, "Mine")
        theirs = _experiment(client, victim, "Theirs")
        mine_version = mine["current_version_id"]
        their_version = theirs["current_version_id"]

        leaky = client.get(
            f"/api/v1/versions/compare?v1={mine_version}&v2={their_version}",
            headers=auth_headers,
        )
        assert leaky.status_code == 404, (
            "A version owned by someone else must read as absent, not diffable"
        )
        assert "diffs" not in leaky.json()

        both_theirs = client.get(
            f"/api/v1/versions/compare?v1={their_version}&v2={their_version}",
            headers=auth_headers,
        )
        assert both_theirs.status_code == 404

    def test_owner_still_compares_own_versions(self, client, auth_headers):
        exp = _experiment(client, auth_headers, "Owned")
        v1 = exp["current_version_id"]
        put = client.put(
            f"/api/v1/experiments/{exp['id']}",
            headers=auth_headers,
            json={
                "strategy_spec": {**BASE_SPEC, "initial_cash": 50000},
                "change_reason": "capital change",
            },
        )
        assert put.status_code in (200, 201), put.text
        versions = client.get(
            f"/api/v1/experiments/{exp['id']}/versions", headers=auth_headers
        ).json()
        v2 = next(v["id"] for v in versions if v["version_number"] == 2)

        resp = client.get(
            f"/api/v1/versions/compare?v1={v1}&v2={v2}", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        fields = {d["field"] for d in resp.json()["diffs"]}
        assert "initial_cash" in fields


class TestRunCreationCrossUser:
    def test_stranger_version_cannot_be_run(self, client, auth_headers):
        # Before the fix this answered 403, which proved the id existed and
        # named the caller as a non-owner. It must read as absent instead,
        # and must not reach the backtest at all.
        victim = _register_user(client, f"run-victim-{uuid.uuid4().hex[:8]}@alethra.io")
        theirs = _experiment(client, victim, "Not mine")

        resp = client.post(
            f"/api/v1/versions/{theirs['current_version_id']}/runs", headers=auth_headers
        )
        assert resp.status_code == 404, resp.text

        missing = client.post(f"/api/v1/versions/{uuid.uuid4()}/runs", headers=auth_headers)
        assert (missing.status_code, missing.json()) == (resp.status_code, resp.json())

        from app.database import get_db
        from app.models.research_run import ResearchRun

        db = next(get_db())
        try:
            assert db.query(ResearchRun).filter(
                ResearchRun.experiment_version_id == uuid.UUID(theirs["current_version_id"])
            ).count() == 0
        finally:
            db.close()


class TestErrorBodyScrubbing:
    """A 500 body must not carry a DSN, a password, or an absolute path."""

    def test_collection_trigger_scrubs_dsn(self, client, admin_headers):
        secret_password = "TriggerPass-" + uuid.uuid4().hex[:8]

        def boom(*args, **kwargs):
            raise RuntimeError(
                f"connection failed for "
                f"postgresql://alethra_mig:{secret_password}@127.0.0.1:54330/alethra "
                f"in /Users/operator/alethra/backend"
            )

        # admin.py imports the name itself, so the patch must target the binding
        # the route actually calls; patching the service module leaves the real
        # collector running.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "app.api.admin.trigger_manual_collection", boom, raising=False
            )
            resp = client.post(
                "/api/v1/admin/collection/trigger", headers=admin_headers
            )

        assert resp.status_code == 500, resp.text
        body = resp.json()["detail"]
        assert body["reason"] == "collection_failed"
        assert secret_password not in body["message"]
        assert "postgresql://" not in body["message"]
        assert "/Users/" not in body["message"]

    def test_scrubber_keeps_plain_text_intact(self):
        from app.lib.error_safety import safe_error_message

        exc = ValueError("Only completed runs can be compared")
        assert safe_error_message(exc) == "Only completed runs can be compared"


class TestVersionNumberingUsesMax:
    """count()+1 repeats a number once the sequence has a gap."""

    def test_next_version_after_deleted_middle(self, client, auth_headers):
        from tests.conftest import TestSession
        from app.models.experiment_version import ExperimentVersion

        exp = _experiment(client, auth_headers, "Gap")
        for cash in (20000, 30000):
            put = client.put(
                f"/api/v1/experiments/{exp['id']}",
                headers=auth_headers,
                json={
                    "strategy_spec": {**BASE_SPEC, "initial_cash": cash},
                    "change_reason": f"cash {cash}",
                },
            )
            assert put.status_code in (200, 201), put.text

        db = TestSession()
        try:
            row = (
                db.query(ExperimentVersion)
                .filter(
                    ExperimentVersion.experiment_id == uuid.UUID(exp["id"]),
                    ExperimentVersion.version_number == 2,
                )
                .one()
            )
            db.delete(row)
            db.commit()
        finally:
            db.close()

        put = client.put(
            f"/api/v1/experiments/{exp['id']}",
            headers=auth_headers,
            json={
                "strategy_spec": {**BASE_SPEC, "initial_cash": 40000},
                "change_reason": "after gap",
            },
        )
        assert put.status_code in (200, 201), put.text
        numbers = sorted(
            v["version_number"]
            for v in client.get(
                f"/api/v1/experiments/{exp['id']}/versions", headers=auth_headers
            ).json()
        )
        assert len(numbers) == len(set(numbers)), f"duplicate version numbers: {numbers}"
        assert numbers == [1, 3, 4], numbers
