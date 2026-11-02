"""FB-20: continuing a locked experiment's research.

The product keeps ``current_version_id`` as the replay/simulation contract and
requires that version to be locked. A revision of a locked experiment therefore
stays a lineage record. These tests pin the supported continuation path: copy
the selected version into a new experiment whose version 1 is current, unlocked
and lockable, with explicit lineage and no inherited approval stamp.
"""
import uuid

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


def _create_experiment(client, headers, title="Continuation Source") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "A locked contract stays replayable while research moves on",
            "falsification_conditions": "New version shows no improvement",
            "research_type": "quantitative",
            "benchmark": "DEMO-BENCHMARK",
            "scope": {"market": "US", "window": "2024-Q1"},
            "strategy_spec": dict(BASE_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _versions(client, headers, experiment_id: str) -> list:
    resp = client.get(f"/api/v1/experiments/{experiment_id}/versions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _lock(client, headers, experiment_id: str, version_id: str):
    return client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/lock",
        headers=headers,
        json={"confirm": True, "reason": "Owner froze the reviewed strategy"},
    )


def _lock_and_revise(client, headers) -> tuple[dict, dict, dict]:
    experiment = _create_experiment(client, headers)
    version = _versions(client, headers, experiment["id"])[0]
    assert _lock(client, headers, experiment["id"], version["id"]).status_code == 200

    revised_spec = {
        **BASE_SPEC,
        "initial_cash": 20000,
        # The revision was approved by the owner; the continuation must not
        # inherit that approval claim.
        "user_approved_at": "2026-11-01T09:30:00",
    }
    revision_resp = client.post(
        f"/api/v1/experiments/{experiment['id']}/revisions",
        headers=headers,
        json={
            "base_version_id": version["id"],
            "strategy_spec": revised_spec,
            "change_reason": "Widen the next study window's starting cash",
        },
    )
    assert revision_resp.status_code in (200, 201), revision_resp.text
    revision = revision_resp.json()
    return experiment, version, revision


def _continue(client, headers, experiment_id: str, version_id: str):
    return client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/continue",
        headers=headers,
    )


def _register_second_user(client) -> dict:
    email = f"continue-other-{uuid.uuid4().hex[:8]}@alethra.io"
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
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


class TestContinuationFromLockedRevision:
    def test_revision_of_locked_experiment_cannot_be_locked(self, client, auth_headers):
        experiment, _version, revision = _lock_and_revise(client, auth_headers)

        resp = _lock(
            client, auth_headers, experiment["id"], revision["version"]["id"]
        )

        assert resp.status_code == 409
        assert "current version" in resp.json()["detail"]
        assert f"/versions/{revision['version']['id']}/continue" in resp.json()["detail"]

    def test_continue_creates_lockable_experiment_with_lineage(self, client, auth_headers):
        experiment, version, revision = _lock_and_revise(client, auth_headers)

        resp = _continue(
            client, auth_headers, experiment["id"], revision["version"]["id"]
        )

        assert resp.status_code == 201, resp.text
        continuation = resp.json()
        assert continuation["id"] != experiment["id"]
        assert continuation["continued_from_experiment_id"] == experiment["id"]
        assert continuation["continued_from_version_id"] == revision["version"]["id"]
        assert continuation["status"] == "draft"
        assert continuation["title"].endswith("(continued)")
        assert continuation["scope"] == {"market": "US", "window": "2024-Q1"}
        assert continuation["current_version_id"] is not None

        versions = _versions(client, auth_headers, continuation["id"])
        assert len(versions) == 1
        new_version = versions[0]
        assert new_version["id"] == continuation["current_version_id"]
        assert new_version["version_number"] == 1
        assert new_version["locked_at"] is None
        # The copied spec carries the revision's parameters but not its approval.
        assert new_version["strategy_spec"]["initial_cash"] == 20000
        assert "user_approved_at" not in new_version["strategy_spec"]
        assert new_version["user_approved_at"] is None
        assert new_version["assumptions"]["continued_from"] == {
            "experiment_id": experiment["id"],
            "version_id": revision["version"]["id"],
            "version_number": revision["version"]["version_number"],
        }

    def test_source_experiment_is_untouched(self, client, auth_headers):
        experiment, version, revision = _lock_and_revise(client, auth_headers)
        before = client.get(
            f"/api/v1/experiments/{experiment['id']}", headers=auth_headers
        ).json()

        assert (
            _continue(
                client, auth_headers, experiment["id"], revision["version"]["id"]
            ).status_code
            == 201
        )

        after = client.get(
            f"/api/v1/experiments/{experiment['id']}", headers=auth_headers
        ).json()
        assert after["status"] == before["status"] == "locked"
        assert after["current_version_id"] == before["current_version_id"]
        assert after["updated_at"] == before["updated_at"]
        assert len(_versions(client, auth_headers, experiment["id"])) == 2

    def test_continuation_version_can_be_locked_and_carries_lineage_event(
        self, client, auth_headers
    ):
        experiment, _version, revision = _lock_and_revise(client, auth_headers)
        continuation = _continue(
            client, auth_headers, experiment["id"], revision["version"]["id"]
        ).json()
        target = _versions(client, auth_headers, continuation["id"])[0]

        locked = _lock(client, auth_headers, continuation["id"], target["id"])

        assert locked.status_code == 200, locked.text
        assert locked.json()["already_locked"] is False
        assert _versions(client, auth_headers, continuation["id"])[0]["locked_at"] is not None

        timeline = client.get(
            f"/api/v1/experiments/{continuation['id']}/timeline", headers=auth_headers
        ).json()
        events = [e for e in timeline if e["event_type"] == "experiment_continued"]
        assert len(events) == 1
        assert events[0]["payload_json"]["source_experiment_id"] == experiment["id"]
        assert events[0]["payload_json"]["source_version_id"] == revision["version"]["id"]


class TestContinuationScopeAndOwnership:
    def test_any_owned_version_can_be_continued(self, client, auth_headers):
        experiment, version, _revision = _lock_and_revise(client, auth_headers)

        resp = _continue(client, auth_headers, experiment["id"], version["id"])

        assert resp.status_code == 201, resp.text
        assert resp.json()["continued_from_version_id"] == version["id"]

    def test_version_of_another_experiment_is_not_continued(self, client, auth_headers):
        experiment, _version, _revision = _lock_and_revise(client, auth_headers)
        other = _create_experiment(client, auth_headers, title="Other Source")
        other_version = _versions(client, auth_headers, other["id"])[0]

        resp = _continue(
            client, auth_headers, experiment["id"], other_version["id"]
        )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Version not found"

    def test_other_user_cannot_continue(self, client, auth_headers):
        experiment, version, _revision = _lock_and_revise(client, auth_headers)
        other_headers = _register_second_user(client)

        resp = _continue(client, other_headers, experiment["id"], version["id"])

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Experiment not found"
