"""Regression coverage for the customer-visible version lock flow (FB-18).

Replay is gated on the version's lock stamp. These tests pin the contract:

- an explicit confirmation is required before anything changes;
- locking freezes the current version and emits exactly one ``strategy_locked``;
- repeating the call is idempotent (no time drift, no duplicate event);
- only the current version of the owner's experiment can be locked;
- a parameter change can never be bundled with a lock;
- a legacy row (experiment marked locked, version unstamped) can be repaired
  through the API without direct SQL, and the repair records the real time.
"""
import uuid
from datetime import datetime

import pytest


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


def _create_experiment(client, headers, title="Lock Experiment") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "The frozen contract is what replay runs",
            "research_type": "quantitative",
            "strategy_spec": dict(BASE_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _versions(client, headers, experiment_id: str) -> list:
    resp = client.get(f"/api/v1/experiments/{experiment_id}/versions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _timeline(client, headers, experiment_id: str) -> list:
    resp = client.get(f"/api/v1/experiments/{experiment_id}/timeline", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lock_events(client, headers, experiment_id: str) -> list:
    return [
        event
        for event in _timeline(client, headers, experiment_id)
        if event["event_type"] == "strategy_locked"
    ]


def _lock_url(experiment_id: str, version_id: str) -> str:
    return f"/api/v1/experiments/{experiment_id}/versions/{version_id}/lock"


def _register_second_user(client) -> dict:
    email = f"lock-other-{uuid.uuid4().hex[:8]}@alethra.io"
    client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "otherpass123",
            "display_name": "Other User",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login", json={"email": email, "password": "otherpass123"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _clear_version_stamp(experiment_id: str, version_id: str) -> None:
    """Reproduce the legacy row observed in the 2026-11-01 candidate database.

    The experiment carries ``status='locked'`` while its current version has no
    lock stamp, so `/simulation` shows the replay link but session creation is
    refused. Test-fixture setup only: the product API never writes this state.
    """
    import app.database as db_mod
    from sqlalchemy.orm import Session
    from app.models.experiment import Experiment, ExperimentStatus
    from app.models.experiment_version import ExperimentVersion

    with Session(bind=db_mod.engine) as db:
        exp = db.get(Experiment, uuid.UUID(experiment_id))
        ver = db.get(ExperimentVersion, uuid.UUID(version_id))
        assert exp is not None and ver is not None
        ver.locked_at = None
        ver.locked_by = None
        ver.strategy_config_hash = None
        ver.dataset_cutoff_at = None
        ver.engine_version = None
        exp.status = ExperimentStatus.locked
        db.commit()


class TestLockRequiresExplicitConfirmation:
    def test_confirm_false_changes_nothing(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]

        resp = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": False},
        )

        assert resp.status_code == 400
        assert "explicit confirmation" in resp.json()["detail"]
        assert _versions(client, auth_headers, experiment["id"])[0]["locked_at"] is None
        status = client.get(
            f"/api/v1/experiments/{experiment['id']}", headers=auth_headers
        ).json()["status"]
        assert status != "locked"
        assert _lock_events(client, auth_headers, experiment["id"]) == []

    def test_confirm_is_required_by_the_schema(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]

        resp = client.post(
            _lock_url(experiment["id"], version["id"]), headers=auth_headers, json={}
        )

        assert resp.status_code == 422
        assert _versions(client, auth_headers, experiment["id"])[0]["locked_at"] is None


class TestLockFreezesTheCurrentVersion:
    def test_locks_version_stamps_contract_and_emits_one_event(
        self, client, auth_headers
    ):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]

        resp = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": True, "reason": "Owner froze the reviewed strategy"},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_id"] == version["id"]
        assert body["experiment_status"] == "locked"
        assert body["already_locked"] is False
        assert body["strategy_config_hash"] == version["config_hash"]

        locked_at = datetime.fromisoformat(body["locked_at"])
        created_at = datetime.fromisoformat(version["created_at"])
        assert locked_at >= created_at
        # The lock time is the moment of commitment, never the spec data cutoff.
        assert locked_at.date().isoformat() != BASE_SPEC["end_date"]

        stored = _versions(client, auth_headers, experiment["id"])[0]
        assert stored["locked_at"] == body["locked_at"]
        status = client.get(
            f"/api/v1/experiments/{experiment['id']}", headers=auth_headers
        ).json()["status"]
        assert status == "locked"

        events = _lock_events(client, auth_headers, experiment["id"])
        assert len(events) == 1
        assert events[0]["rules_locked"] is True
        assert events[0]["payload_json"]["version_id"] == version["id"]
        assert (
            events[0]["payload_json"]["dataset_cutoff_at"] == BASE_SPEC["end_date"]
        )

    def test_second_call_is_idempotent(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]
        first = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": True},
        )
        assert first.status_code == 200, first.text

        second = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": True},
        )

        assert second.status_code == 200, second.text
        assert second.json()["already_locked"] is True
        assert second.json()["locked_at"] == first.json()["locked_at"]
        assert len(_lock_events(client, auth_headers, experiment["id"])) == 1


class TestLockRefusals:
    def test_only_the_current_version_can_be_locked(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        first_version = _versions(client, auth_headers, experiment["id"])[0]

        revision = client.post(
            f"/api/v1/experiments/{experiment['id']}/revisions",
            headers=auth_headers,
            json={
                "base_version_id": first_version["id"],
                "strategy_spec": {**BASE_SPEC, "initial_cash": 20000},
                "change_reason": "Larger starting cash",
            },
        )
        assert revision.status_code in (200, 201), revision.text

        resp = client.post(
            _lock_url(experiment["id"], first_version["id"]),
            headers=auth_headers,
            json={"confirm": True},
        )

        assert resp.status_code == 409
        assert "current version" in resp.json()["detail"]
        assert all(
            v["locked_at"] is None
            for v in _versions(client, auth_headers, experiment["id"])
        )

    def test_other_user_cannot_lock(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]
        other_headers = _register_second_user(client)

        resp = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=other_headers,
            json={"confirm": True},
        )

        assert resp.status_code == 404
        assert _versions(client, auth_headers, experiment["id"])[0]["locked_at"] is None
        assert _lock_events(client, auth_headers, experiment["id"]) == []

    def test_put_cannot_change_parameters_and_lock_in_one_step(
        self, client, auth_headers
    ):
        experiment = _create_experiment(client, auth_headers)

        resp = client.put(
            f"/api/v1/experiments/{experiment['id']}",
            headers=auth_headers,
            json={
                "strategy_spec": {**BASE_SPEC, "initial_cash": 20000},
                "change_reason": "Smuggle a new parameter into the lock",
                "scope": {"status": "locked"},
            },
        )

        assert resp.status_code == 400
        assert "cannot be changed and locked" in resp.json()["detail"]
        assert len(_versions(client, auth_headers, experiment["id"])) == 1
        status = client.get(
            f"/api/v1/experiments/{experiment['id']}", headers=auth_headers
        ).json()["status"]
        assert status != "locked"


class TestLegacyLockedRowRepair:
    def test_repairs_experiment_marked_locked_with_unstamped_version(
        self, client, auth_headers
    ):
        experiment = _create_experiment(client, auth_headers)
        version = _versions(client, auth_headers, experiment["id"])[0]
        locked = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": True},
        )
        assert locked.status_code == 200, locked.text
        _clear_version_stamp(experiment["id"], version["id"])

        # The dead end observed in the candidate DB: status locked, no stamp.
        assert _versions(client, auth_headers, experiment["id"])[0]["locked_at"] is None
        events_before = len(_lock_events(client, auth_headers, experiment["id"]))

        repaired = client.post(
            _lock_url(experiment["id"], version["id"]),
            headers=auth_headers,
            json={"confirm": True, "reason": "Repair: version had no lock stamp"},
        )

        assert repaired.status_code == 200, repaired.text
        assert repaired.json()["already_locked"] is False
        assert datetime.fromisoformat(repaired.json()["locked_at"]) >= datetime.fromisoformat(
            version["created_at"]
        )
        assert _versions(client, auth_headers, experiment["id"])[0]["locked_at"] is not None
        # The repair is a real lock action, so it is recorded — exactly once more.
        assert (
            len(_lock_events(client, auth_headers, experiment["id"]))
            == events_before + 1
        )
