"""Regression coverage for the personal-model version lifecycle.

The product promise is: a model version can be frozen from one of the owner's
own strategy versions, two versions can be compared without the system
declaring a winner, the record tracks which version is active, and a rollback
is an event with a reason rather than a deletion or an in-place rewrite.
"""
import pytest

from tests.conftest import TestSession
from app.models.audit_event import AuditEvent


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


def _create_experiment(client, headers, title="Model Lifecycle Experiment") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "Version history is traceable",
            "research_type": "quantitative",
            "strategy_spec": dict(BASE_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_revision(client, headers, experiment: dict, **overrides) -> dict:
    spec = {**BASE_SPEC, **overrides}
    resp = client.post(
        f"/api/v1/experiments/{experiment['id']}/revisions",
        headers=headers,
        json={
            "base_version_id": experiment["current_version_id"],
            "strategy_spec": spec,
            "change_reason": "修訂成本假設",
            "expectation": "淨報酬應低於原版本",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["version"]


def _create_record(client, headers, title="Lifecycle Model") -> dict:
    resp = client.post(
        "/api/v1/model-research",
        headers=headers,
        json={"title": title, "purpose": "Track the personal model lifecycle"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _from_strategy(client, headers, record_id: str, experiment_version_id: str, **extra):
    body = {"experiment_version_id": experiment_version_id, **extra}
    return client.post(
        f"/api/v1/model-research/{record_id}/versions/from-strategy",
        headers=headers,
        json=body,
    )


def _set_active(client, headers, record_id: str, version_id: str, reason=None):
    return client.post(
        f"/api/v1/model-research/{record_id}/active-version",
        headers=headers,
        json={"version_id": version_id, "reason": reason},
    )


def _rollback(client, headers, record_id: str, to_version_id: str, reason="回復到較保守的設定"):
    return client.post(
        f"/api/v1/model-research/{record_id}/rollback",
        headers=headers,
        json={"to_version_id": to_version_id, "reason": reason},
    )


def _events(entity_id: str, event_type: str = None) -> list:
    db = TestSession()
    try:
        query = db.query(AuditEvent).filter(AuditEvent.entity_id == str(entity_id))
        if event_type:
            query = query.filter(AuditEvent.event_type == event_type)
        return query.order_by(AuditEvent.created_at).all()
    finally:
        db.close()


def _create_user2(client) -> dict:
    client.post("/api/auth/register", json={
        "email": "lifecycle-user2@alethra.io",
        "password": "testpass123",
        "display_name": "User 2",
        "user_type": "individual",
    })
    resp = client.post("/api/auth/login", json={
        "email": "lifecycle-user2@alethra.io",
        "password": "testpass123",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestVersionFromStrategy:
    def test_freezes_strategy_spec_and_links_source_version(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        versions = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()
        source = versions[0]
        record = _create_record(client, auth_headers)

        resp = _from_strategy(
            client, auth_headers, record["id"], source["id"], note="由已驗證策略建立"
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["version_number"] == 1
        assert body["model_type"] == "rule_based"
        assert body["linked_experiment_version_ids"] == [source["id"]]

        spec = body["model_specification_json"]
        assert spec["source"] == "strategy_version"
        assert spec["experiment_id"] == experiment["id"]
        assert spec["experiment_title"] == experiment["title"]
        assert spec["experiment_version_id"] == source["id"]
        assert spec["experiment_version_number"] == source["version_number"]
        assert spec["strategy_spec"] == source["strategy_spec"]
        assert spec["config_hash"] == source["config_hash"]
        assert spec["note"] == "由已驗證策略建立"

        events = _events(body["id"], "model_version_created")
        assert len(events) == 1
        assert events[0].payload_json["source"] == "strategy_version"

    def test_cross_user_experiment_version_is_not_found(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        other_headers = _create_user2(client)
        foreign_experiment = _create_experiment(client, other_headers)
        foreign_versions = client.get(
            f"/api/v1/experiments/{foreign_experiment['id']}/versions",
            headers=other_headers,
        ).json()

        resp = _from_strategy(
            client, auth_headers, record["id"], foreign_versions[0]["id"]
        )
        assert resp.status_code == 404

        mine = client.get(
            f"/api/v1/model-research/{record['id']}/versions", headers=auth_headers
        ).json()
        assert mine == []

    def test_refuses_when_record_no_longer_editable(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        versions = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()
        record = _create_record(client, auth_headers)

        for status in ("researching", "historical_test_complete"):
            patch = client.patch(
                f"/api/v1/model-research/{record['id']}",
                headers=auth_headers,
                json={"status": status},
            )
            assert patch.status_code == 200, patch.text

        resp = _from_strategy(client, auth_headers, record["id"], versions[0]["id"])
        assert resp.status_code == 400
        assert "Cannot add versions" in resp.json()["detail"]


class TestCompareVersions:
    @pytest.fixture()
    def paired_versions(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        first = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        second = _create_revision(client, auth_headers, experiment, commission_rate=0.002)

        record = _create_record(client, auth_headers)
        v1 = _from_strategy(client, auth_headers, record["id"], first["id"]).json()
        v2 = _from_strategy(client, auth_headers, record["id"], second["id"]).json()
        return record, v1, v2

    def test_lists_spec_differences_without_declaring_a_winner(
        self, client, auth_headers, paired_versions
    ):
        record, v1, v2 = paired_versions
        resp = client.get(
            f"/api/v1/model-research/{record['id']}/versions/compare",
            headers=auth_headers,
            params={"version_a_id": v1["id"], "version_b_id": v2["id"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["version_a"]["version_number"] == 1
        assert body["version_b"]["version_number"] == 2
        assert body["version_a"]["id"] == v1["id"]
        assert body["version_b"]["id"] == v2["id"]

        changes = {c["path"]: c for c in body["spec_changes"]}
        commission = changes["strategy_spec.commission_rate"]
        assert commission["kind"] == "changed"
        assert commission["a"] == 0.001
        assert commission["b"] == 0.002
        assert body["spec_changes_truncated"] is False
        assert body["evaluations"] == {"a": [], "b": []}

        assert "比較" in body["note"]
        assert "不會自動判定哪個版本更好" in body["note"]
        assert not any(
            key in body for key in ("winner", "recommended", "best_version")
        )

    def test_same_version_on_both_sides_is_refused(
        self, client, auth_headers, paired_versions
    ):
        record, v1, _ = paired_versions
        resp = client.get(
            f"/api/v1/model-research/{record['id']}/versions/compare",
            headers=auth_headers,
            params={"version_a_id": v1["id"], "version_b_id": v1["id"]},
        )
        assert resp.status_code == 400

    def test_version_from_another_record_is_not_found(
        self, client, auth_headers, paired_versions
    ):
        record, v1, _ = paired_versions
        other_record = _create_record(client, auth_headers, title="Other Record")
        resp = client.get(
            f"/api/v1/model-research/{other_record['id']}/versions/compare",
            headers=auth_headers,
            params={"version_a_id": v1["id"], "version_b_id": v1["id"]},
        )
        assert resp.status_code in (400, 404)

        resp = client.get(
            f"/api/v1/model-research/{record['id']}/versions/compare",
            headers=auth_headers,
            params={"version_a_id": v1["id"], "version_b_id": other_record["id"]},
        )
        assert resp.status_code == 404

    def test_active_flag_reflects_the_pointer(
        self, client, auth_headers, paired_versions
    ):
        record, v1, v2 = paired_versions
        assert _set_active(client, auth_headers, record["id"], v2["id"]).status_code == 200

        body = client.get(
            f"/api/v1/model-research/{record['id']}/versions/compare",
            headers=auth_headers,
            params={"version_a_id": v1["id"], "version_b_id": v2["id"]},
        ).json()
        assert body["version_a"]["is_active"] is False
        assert body["version_b"]["is_active"] is True


class TestActiveVersion:
    def test_set_active_updates_record_and_records_event(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        source = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        record = _create_record(client, auth_headers)
        version = _from_strategy(client, auth_headers, record["id"], source["id"]).json()
        assert record["active_version_id"] is None

        resp = _set_active(client, auth_headers, record["id"], version["id"], reason="開始使用")
        assert resp.status_code == 200, resp.text
        assert resp.json()["active_version_id"] == version["id"]

        events = _events(record["id"], "model_active_version_set")
        assert len(events) == 1
        payload = events[0].payload_json
        assert payload["from_version_id"] is None
        assert payload["to_version_id"] == version["id"]
        assert payload["to_version_number"] == 1
        assert payload["reason"] == "開始使用"

    def test_repeating_the_same_choice_is_a_no_op(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        source = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        record = _create_record(client, auth_headers)
        version = _from_strategy(client, auth_headers, record["id"], source["id"]).json()

        _set_active(client, auth_headers, record["id"], version["id"])
        again = _set_active(client, auth_headers, record["id"], version["id"])
        assert again.status_code == 200
        assert again.json()["active_version_id"] == version["id"]
        assert len(_events(record["id"], "model_active_version_set")) == 1

    def test_cannot_activate_a_revoked_version(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        source = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        record = _create_record(client, auth_headers)
        version = _from_strategy(client, auth_headers, record["id"], source["id"]).json()

        revoke = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke",
            headers=auth_headers,
            params={"reason": "設定有誤"},
        )
        assert revoke.status_code == 200, revoke.text

        resp = _set_active(client, auth_headers, record["id"], version["id"])
        assert resp.status_code == 400

    def test_version_from_another_record_is_not_found(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        source = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        record = _create_record(client, auth_headers)
        other = _create_record(client, auth_headers, title="Other Record")
        version = _from_strategy(client, auth_headers, record["id"], source["id"]).json()

        resp = _set_active(client, auth_headers, other["id"], version["id"])
        assert resp.status_code == 404


class TestRollback:
    @pytest.fixture()
    def record_with_versions(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        first = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        second = _create_revision(client, auth_headers, experiment, commission_rate=0.002)
        record = _create_record(client, auth_headers)
        v1 = _from_strategy(client, auth_headers, record["id"], first["id"]).json()
        v2 = _from_strategy(client, auth_headers, record["id"], second["id"]).json()
        return record, v1, v2

    def test_rolls_back_to_older_version_with_reason(self, client, auth_headers, record_with_versions):
        record, v1, v2 = record_with_versions
        assert _set_active(client, auth_headers, record["id"], v2["id"]).status_code == 200

        resp = _rollback(client, auth_headers, record["id"], v1["id"])
        assert resp.status_code == 200, resp.text
        assert resp.json()["active_version_id"] == v1["id"]

        events = _events(record["id"], "model_version_rolled_back")
        assert len(events) == 1
        payload = events[0].payload_json
        assert payload["from_version_id"] == v2["id"]
        assert payload["to_version_id"] == v1["id"]
        assert payload["reason"] == "回復到較保守的設定"

        versions = client.get(
            f"/api/v1/model-research/{record['id']}/versions", headers=auth_headers
        ).json()
        assert [v["version_number"] for v in versions] == [1, 2]
        assert all(v["revoked_at"] is None for v in versions)

    def test_requires_a_non_blank_reason(self, client, auth_headers, record_with_versions):
        record, v1, v2 = record_with_versions
        _set_active(client, auth_headers, record["id"], v2["id"])

        resp = _rollback(client, auth_headers, record["id"], v1["id"], reason="   ")
        assert resp.status_code == 400
        assert "reason" in resp.json()["detail"].lower()

        missing = client.post(
            f"/api/v1/model-research/{record['id']}/rollback",
            headers=auth_headers,
            json={"to_version_id": v1["id"]},
        )
        assert missing.status_code == 422

        assert len(_events(record["id"], "model_version_rolled_back")) == 0

    def test_cannot_roll_back_to_newer_or_equal_version(self, client, auth_headers, record_with_versions):
        record, v1, v2 = record_with_versions
        _set_active(client, auth_headers, record["id"], v1["id"])

        resp = _rollback(client, auth_headers, record["id"], v2["id"])
        assert resp.status_code == 400

        resp = _rollback(client, auth_headers, record["id"], v1["id"])
        assert resp.status_code == 200
        assert resp.json()["active_version_id"] == v1["id"]
        assert len(_events(record["id"], "model_version_rolled_back")) == 0

    def test_cannot_roll_back_without_active_version(self, client, auth_headers, record_with_versions):
        record, v1, _ = record_with_versions
        resp = _rollback(client, auth_headers, record["id"], v1["id"])
        assert resp.status_code == 400
        assert "No active version" in resp.json()["detail"]

    def test_cannot_roll_back_to_a_revoked_version(self, client, auth_headers, record_with_versions):
        record, v1, v2 = record_with_versions
        _set_active(client, auth_headers, record["id"], v2["id"])
        revoke = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{v1['id']}/revoke",
            headers=auth_headers,
            params={"reason": "不再使用"},
        )
        assert revoke.status_code == 200, revoke.text

        resp = _rollback(client, auth_headers, record["id"], v1["id"])
        assert resp.status_code == 400

    def test_cross_user_rollback_is_unauthorized(self, client, auth_headers, record_with_versions):
        record, v1, v2 = record_with_versions
        _set_active(client, auth_headers, record["id"], v2["id"])
        other_headers = _create_user2(client)

        resp = _rollback(client, other_headers, record["id"], v1["id"])
        assert resp.status_code in (403, 404)


class TestRevokeClearsActivePointer:
    def test_revoking_the_active_version_clears_the_pointer(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        source = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        record = _create_record(client, auth_headers)
        version = _from_strategy(client, auth_headers, record["id"], source["id"]).json()
        _set_active(client, auth_headers, record["id"], version["id"])

        revoke = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke",
            headers=auth_headers,
            params={"reason": "資料截止日錯誤"},
        )
        assert revoke.status_code == 200, revoke.text

        refreshed = client.get(
            f"/api/v1/model-research/{record['id']}", headers=auth_headers
        ).json()
        assert refreshed["active_version_id"] is None

        events = _events(version["id"], "model_version_revoked")
        assert len(events) == 1
        assert events[0].payload_json["cleared_active"] is True

    def test_revoking_an_inactive_version_keeps_the_pointer(self, client, auth_headers):
        experiment = _create_experiment(client, auth_headers)
        first = client.get(
            f"/api/v1/experiments/{experiment['id']}/versions", headers=auth_headers
        ).json()[0]
        second = _create_revision(client, auth_headers, experiment, commission_rate=0.002)
        record = _create_record(client, auth_headers)
        v1 = _from_strategy(client, auth_headers, record["id"], first["id"]).json()
        v2 = _from_strategy(client, auth_headers, record["id"], second["id"]).json()
        _set_active(client, auth_headers, record["id"], v2["id"])

        revoke = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{v1['id']}/revoke",
            headers=auth_headers,
            params={"reason": "舊版不再使用"},
        )
        assert revoke.status_code == 200, revoke.text

        refreshed = client.get(
            f"/api/v1/model-research/{record['id']}", headers=auth_headers
        ).json()
        assert refreshed["active_version_id"] == v2["id"]

        events = _events(v1["id"], "model_version_revoked")
        assert events[0].payload_json["cleared_active"] is False
