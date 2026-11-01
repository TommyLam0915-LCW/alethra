"""Regression coverage for strategy revisions built from an existing version.

The product promise is: a revision never rewrites its source, its lineage and
reason are traceable, and an outcome can be recorded without mutating the
strategy that produced it.
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


def _create_experiment(client, headers, title="Revision Experiment") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "Costs change the conclusion",
            "research_type": "quantitative",
            "strategy_spec": dict(BASE_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _revision_body(base_version_id: str, **spec_overrides) -> dict:
    spec = {**BASE_SPEC, **spec_overrides}
    return {
        "base_version_id": base_version_id,
        "strategy_spec": spec,
        "change_reason": "下調手續費以檢驗成本假設",
        "expectation": "淨報酬應高於原版本",
    }


def _register_second_user(client) -> dict:
    email = f"other-{uuid.uuid4().hex[:8]}@alethra.io"
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


def _versions(client, headers, experiment_id: str) -> list:
    resp = client.get(f"/api/v1/experiments/{experiment_id}/versions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestCreateRevision:
    def test_creates_next_version_and_keeps_source_unchanged(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        source_id = exp["current_version_id"]

        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(source_id, commission_rate=0.004),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["version"]["version_number"] == 2
        assert body["is_current"] is True
        assert body["change_kind"] == "strategy_only"
        assert body["changed_fields"] == ["commission_rate"]
        assert body["version"]["change_reason"] == "下調手續費以檢驗成本假設"
        assert body["version"]["assumptions"]["revision"]["of_version_id"] == source_id
        assert body["version"]["assumptions"]["revision"]["expectation"] == "淨報酬應高於原版本"

        versions = _versions(client, auth_headers, exp["id"])
        assert len(versions) == 2
        source = next(v for v in versions if v["id"] == source_id)
        assert source["strategy_spec"]["commission_rate"] == 0.001
        assert source["version_number"] == 1
        assert source["change_reason"] is None

        refreshed = client.get(
            f"/api/v1/experiments/{exp['id']}", headers=auth_headers
        ).json()
        assert refreshed["current_version_id"] == body["version"]["id"]

    def test_period_only_change_is_classified_separately(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(exp["current_version_id"], end_date="2024-03-15"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["change_kind"] == "period_only"
        assert body["changed_fields"] == ["end_date"]

    def test_locked_source_stays_locked_and_current_pointer_does_not_move(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        source_id = exp["current_version_id"]

        lock = client.put(
            f"/api/v1/experiments/{exp['id']}",
            headers=auth_headers,
            json={"scope": {"status": "locked"}},
        )
        assert lock.status_code == 200, lock.text
        assert lock.json()["status"] == "locked"

        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(source_id, slippage_rate=0.002),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["is_current"] is False
        assert body["version"]["version_number"] == 2

        versions = _versions(client, auth_headers, exp["id"])
        source = next(v for v in versions if v["id"] == source_id)
        assert source["locked_at"] is not None
        assert source["strategy_spec"]["slippage_rate"] == 0.001

        refreshed = client.get(
            f"/api/v1/experiments/{exp['id']}", headers=auth_headers
        ).json()
        assert refreshed["current_version_id"] == source_id

    def test_revision_can_be_researched_directly(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        revision = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(exp["current_version_id"], commission_rate=0.0),
        ).json()

        run = client.post(
            f"/api/v1/versions/{revision['version']['id']}/runs",
            headers=auth_headers,
        )
        assert run.status_code == 201, run.text
        run_body = run.json()
        assert run_body["status"] == "completed"
        assert run_body["experiment_version_id"] == revision["version"]["id"]

    def test_identical_to_source_is_refused(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(exp["current_version_id"]),
        )
        assert resp.status_code == 400
        assert "完全相同" in resp.json()["detail"]

        assert len(_versions(client, auth_headers, exp["id"])) == 1

    def test_duplicate_configuration_is_refused(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        source_id = exp["current_version_id"]
        first = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(source_id, commission_rate=0.002),
        )
        assert first.status_code == 201

        second = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(source_id, commission_rate=0.002),
        )
        assert second.status_code == 400
        assert "已存在" in second.json()["detail"]
        assert len(_versions(client, auth_headers, exp["id"])) == 2

    def test_request_id_makes_retry_idempotent(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        request_id = str(uuid.uuid4())
        body = {
            **_revision_body(exp["current_version_id"], commission_rate=0.003),
            "request_id": request_id,
        }

        first = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=body,
        )
        assert first.status_code == 201
        second = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=body,
        )
        assert second.status_code == 201
        assert second.json()["version"]["id"] == first.json()["version"]["id"]
        assert len(_versions(client, auth_headers, exp["id"])) == 2

    def test_change_reason_is_required(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        body = _revision_body(exp["current_version_id"], commission_rate=0.002)
        body["change_reason"] = ""
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=body,
        )
        assert resp.status_code == 422

    def test_unsupported_intraday_frequency_is_rejected_not_downgraded(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        body = _revision_body(exp["current_version_id"], frequency="5m")
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=body,
        )
        assert resp.status_code == 422
        detail = str(resp.json()["detail"])
        assert "frequency" in detail

    def test_legacy_source_spec_reports_only_the_real_edit(
        self, client, auth_headers
    ):
        """Versions stored before schema defaults must not inflate the diff."""
        from tests.conftest import TestSession
        from app.models.experiment_version import ExperimentVersion

        exp = _create_experiment(client, auth_headers)
        legacy_spec = {
            "template_type": "price_change_fixed_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-02-01",
            "end_date": "2024-02-13",
            "initial_cash": 100000,
            "signal_parameters": {
                "holding_days": 5,
                "lookback_days": 3,
                "threshold_pct": 0.99,
            },
            "commission_rate": 0.001,
            "slippage_rate": 0.001,
            "synthetic": True,
            "dataset": {"provider": "demo", "bar_count": 9},
        }
        db = TestSession()
        try:
            version = db.query(ExperimentVersion).filter(
                ExperimentVersion.id == uuid.UUID(exp["current_version_id"])
            ).first()
            version.strategy_spec = legacy_spec
            db.commit()
        finally:
            db.close()

        body = {
            "base_version_id": exp["current_version_id"],
            "strategy_spec": {**legacy_spec, "signal_parameters": {**legacy_spec["signal_parameters"], "holding_days": 3}},
            "change_reason": "縮短持有天數以觀察資金周轉",
        }
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=body,
        )
        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["changed_fields"] == ["signal_parameters.holding_days"]
        assert payload["change_kind"] == "strategy_only"

    def test_intraday_source_version_is_refused_not_downgraded(
        self, client, auth_headers
    ):
        from tests.conftest import TestSession
        from app.models.experiment_version import ExperimentVersion

        exp = _create_experiment(client, auth_headers)

        for override in ({"frequency": "5m"}, {"session": {"timeframe": "5m"}}):
            db = TestSession()
            try:
                version = db.query(ExperimentVersion).filter(
                    ExperimentVersion.id == uuid.UUID(exp["current_version_id"])
                ).first()
                version.strategy_spec = {**BASE_SPEC, **override}
                db.commit()
            finally:
                db.close()

            resp = client.post(
                f"/api/v1/experiments/{exp['id']}/revisions",
                headers=auth_headers,
                json=_revision_body(exp["current_version_id"], commission_rate=0.002),
            )
            assert resp.status_code == 400, resp.text
            assert "不會自動降級" in resp.json()["detail"]

        assert len(_versions(client, auth_headers, exp["id"])) == 1

    def test_foreign_base_version_is_not_reachable(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        other_headers = _register_second_user(client)
        other_exp = _create_experiment(client, other_headers, title="Other")

        # The other user's own experiment, but this user's base version.
        resp = client.post(
            f"/api/v1/experiments/{other_exp['id']}/revisions",
            headers=other_headers,
            json=_revision_body(exp["current_version_id"], commission_rate=0.002),
        )
        assert resp.status_code == 404

        # And the experiment itself is not addressable by a stranger.
        resp = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=other_headers,
            json=_revision_body(exp["current_version_id"], commission_rate=0.002),
        )
        assert resp.status_code == 404


class TestRevisionOutcome:
    def _revision(self, client, headers):
        exp = _create_experiment(client, headers)
        created = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=headers,
            json=_revision_body(exp["current_version_id"], commission_rate=0.002),
        )
        assert created.status_code == 201, created.text
        return exp, created.json()["version"]

    def test_outcome_is_recorded_without_touching_strategy(
        self, client, auth_headers
    ):
        exp, revision = self._revision(client, auth_headers)
        spec_before = revision["strategy_spec"]
        hash_before = revision["config_hash"]

        resp = client.patch(
            f"/api/v1/experiments/{exp['id']}/revisions/{revision['id']}/outcome",
            headers=auth_headers,
            json={"met_expectation": False, "outcome": "成本下降但回撤同步擴大，未達預期"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        meta = body["assumptions"]["revision"]
        assert meta["met_expectation"] is False
        assert meta["outcome"] == "成本下降但回撤同步擴大，未達預期"
        assert meta["outcome_recorded_at"] is not None
        assert body["strategy_spec"] == spec_before
        assert body["config_hash"] == hash_before

        reloaded = _versions(client, auth_headers, exp["id"])
        stored = next(v for v in reloaded if v["id"] == revision["id"])
        assert stored["assumptions"]["revision"]["outcome"] == (
            "成本下降但回撤同步擴大，未達預期"
        )

    def test_outcome_refused_for_non_revision_version(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        resp = client.patch(
            f"/api/v1/experiments/{exp['id']}/revisions/{exp['current_version_id']}/outcome",
            headers=auth_headers,
            json={"met_expectation": True, "outcome": "不應寫入"},
        )
        assert resp.status_code == 400

    def test_outcome_requires_text(self, client, auth_headers):
        exp, revision = self._revision(client, auth_headers)
        resp = client.patch(
            f"/api/v1/experiments/{exp['id']}/revisions/{revision['id']}/outcome",
            headers=auth_headers,
            json={"met_expectation": True, "outcome": ""},
        )
        assert resp.status_code == 422


class TestRevisionAudit:
    def test_revision_events_are_on_the_experiment_timeline(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        created = client.post(
            f"/api/v1/experiments/{exp['id']}/revisions",
            headers=auth_headers,
            json=_revision_body(exp["current_version_id"], commission_rate=0.002),
        ).json()
        client.patch(
            f"/api/v1/experiments/{exp['id']}/revisions/{created['version']['id']}/outcome",
            headers=auth_headers,
            json={"met_expectation": True, "outcome": "符合預期"},
        )

        timeline = client.get(
            f"/api/v1/experiments/{exp['id']}/timeline", headers=auth_headers
        ).json()
        event_types = [event["event_type"] for event in timeline]
        assert "strategy_version_created" in event_types
        assert "revision_outcome_recorded" in event_types

        created_event = next(
            event
            for event in timeline
            if event["event_type"] == "strategy_version_created"
            and event["entity_id"] == created["version"]["id"]
        )
        assert created_event["payload_json"]["revision_of_version_number"] == 1
        assert created_event["payload_json"]["changed_fields"] == ["commission_rate"]
