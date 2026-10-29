"""The research record must be complete and honest.

Decision review is only possible if one record keeps revision conclusions,
every robustness study (including cancelled ones), saved comparison rationales
and both decision lanes. These tests compare the record against the product
endpoints that produced the data, so a section cannot silently go stale again.
"""
import uuid
from datetime import date, datetime

import pytest

from tests.conftest import TestSession


BUY_HOLD_SPEC = {
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


def _create_experiment(client, headers, spec=None, title="Record Experiment") -> dict:
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "hypothesis": "The record keeps what was concluded",
            "research_type": "quantitative",
            "strategy_spec": dict(spec or BUY_HOLD_SPEC),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _revision(client, headers, experiment_id: str, base_version_id: str) -> dict:
    spec = {**BUY_HOLD_SPEC, "commission_rate": 0.002}
    resp = client.post(
        f"/api/v1/experiments/{experiment_id}/revisions",
        headers=headers,
        json={
            "base_version_id": base_version_id,
            "strategy_spec": spec,
            "change_reason": "提高手續費以檢驗成本假設",
            "expectation": "淨報酬應下降",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_version(client, headers, version_id: str) -> dict:
    resp = client.post(f"/api/v1/versions/{version_id}/runs", headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _record(client, headers, experiment_id: str) -> dict:
    resp = client.get(f"/api/v1/experiments/{experiment_id}/record", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _user_id(email: str = "test@alethra.io") -> uuid.UUID:
    from app.models.user import User

    db = TestSession()
    try:
        return db.query(User).filter(User.email == email).first().id
    finally:
        db.close()


def _grant_research_runs(limit: int = 100, email: str = "test@alethra.io") -> None:
    from app.models.pilot import PilotEntitlement

    db = TestSession()
    try:
        existing = (
            db.query(PilotEntitlement)
            .filter(PilotEntitlement.user_id == _user_id(email))
            .first()
        )
        if existing:
            existing.research_run_limit = limit
            existing.pilot_access = True
        else:
            db.add(
                PilotEntitlement(
                    user_id=_user_id(email),
                    pilot_access=True,
                    research_run_limit=limit,
                )
            )
        db.commit()
    finally:
        db.close()


def _register_second_user(client) -> dict:
    email = f"other-{uuid.uuid4().hex[:8]}@alethra.io"
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


class TestRecordShape:
    def test_empty_experiment_has_every_section(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        record = _record(client, auth_headers, exp["id"])

        assert record["schema_version"] == "experiment-record-2"
        assert record["experiment"]["id"] == exp["id"]
        assert record["coverage"]["versions"] == 1
        assert record["coverage"]["revisions"] == 0
        assert record["coverage"]["runs"] == 0
        assert record["coverage"]["robustness_studies"] == 0
        assert record["coverage"]["saved_comparisons"] == 0
        assert record["coverage"]["simulation_sessions"] == 0
        assert record["coverage"]["notes"] == 0
        assert record["coverage"]["events"] == len(record["events"])

        review = record["decision_review"]
        assert review["revisions"] == []
        assert review["robustness_studies"] == []
        assert review["saved_comparisons"] == []
        assert review["simulation_sessions"] == []
        assert record["version_history"][0]["revision"] is None
        assert record["current_version"]["version_number"] == 1
        assert record["runs"] == []
        assert record["notes"] == []
        assert len(record["events"]) >= 1
        assert record["disclaimer_zh"]

    def test_record_names_what_it_leaves_out(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        _run_version(client, auth_headers, exp["current_version_id"])

        record = _record(client, auth_headers, exp["id"])
        excluded = record["coverage"]["excluded"]
        assert "runs.result_json.transactions" in excluded
        assert record["coverage"]["runs"] == 1
        assert record["coverage"]["completed_runs"] == 1
        # The record is a decision record, not a copy of the trade book.
        assert "transactions" not in record["runs"][0]

    def test_record_is_owner_scoped(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        second = _register_second_user(client)

        assert client.get(
            f"/api/v1/experiments/{exp['id']}/record", headers=second
        ).status_code == 404
        assert client.get(
            f"/api/v1/experiments/{exp['id']}/export", headers=second
        ).status_code == 404
        assert client.get(f"/api/v1/experiments/{exp['id']}/record").status_code in (
            401,
            403,
        )

    def test_reading_the_record_does_not_emit_an_export_event(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        _record(client, auth_headers, exp["id"])
        _record(client, auth_headers, exp["id"])

        timeline = client.get(
            f"/api/v1/experiments/{exp['id']}/timeline", headers=auth_headers
        ).json()
        assert [e["event_type"] for e in timeline].count("export_generated") == 0

        client.get(f"/api/v1/experiments/{exp['id']}/export", headers=auth_headers)
        client.get(f"/api/v1/experiments/{exp['id']}/export", headers=auth_headers)
        timeline = client.get(
            f"/api/v1/experiments/{exp['id']}/timeline", headers=auth_headers
        ).json()
        assert [e["event_type"] for e in timeline].count("export_generated") == 2

    def test_export_returns_the_same_record(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        client.post(
            f"/api/v1/experiments/{exp['id']}/notes",
            headers=auth_headers,
            json={"content": "匯出前的研究筆記"},
        )

        record = _record(client, auth_headers, exp["id"])
        exported = client.get(
            f"/api/v1/experiments/{exp['id']}/export", headers=auth_headers
        ).json()

        assert exported["schema_version"] == record["schema_version"]
        assert exported["coverage"]["versions"] == record["coverage"]["versions"]
        assert exported["notes"][0]["content"] == "匯出前的研究筆記"
        assert exported["decision_review"].keys() == record["decision_review"].keys()


class TestRevisionReview:
    def test_revision_lineage_and_outcome_are_in_the_record(
        self, client, auth_headers
    ):
        exp = _create_experiment(client, auth_headers)
        source_id = exp["current_version_id"]
        revision = _revision(client, auth_headers, exp["id"], source_id)
        revision_id = revision["version"]["id"]
        hash_before = revision["version"]["config_hash"]

        _run_version(client, auth_headers, revision_id)
        outcome = client.patch(
            f"/api/v1/experiments/{exp['id']}/revisions/{revision_id}/outcome",
            headers=auth_headers,
            json={"met_expectation": False, "outcome": "成本上升，淨報酬低於原版本"},
        )
        assert outcome.status_code == 200, outcome.text

        record = _record(client, auth_headers, exp["id"])
        assert record["coverage"]["versions"] == 2
        assert record["coverage"]["revisions"] == 1

        entry = record["decision_review"]["revisions"][0]
        assert entry["version_id"] == revision_id
        assert entry["version_number"] == 2
        assert entry["base_version_id"] == source_id
        assert entry["base_version_number"] == 1
        assert entry["change_kind"] == "strategy_only"
        assert entry["changed_fields"] == ["commission_rate"]
        assert entry["change_reason"] == "提高手續費以檢驗成本假設"
        assert entry["expectation"] == "淨報酬應下降"
        assert entry["outcome"] == "成本上升，淨報酬低於原版本"
        assert entry["met_expectation"] is False
        assert entry["outcome_recorded_at"] is not None

        history = {v["version_number"]: v for v in record["version_history"]}
        assert history[1]["revision"] is None
        assert history[2]["revision"]["outcome"] == entry["outcome"]
        # Recording a conclusion never rewrites the strategy it judged.
        assert history[2]["config_hash"] == hash_before
        assert history[2]["strategy_spec"]["commission_rate"] == 0.002

        assert record["runs"][0]["version_number"] == 2


class TestRobustnessReview:
    def test_every_study_and_attempt_is_in_the_record(self, client, auth_headers):
        _grant_research_runs()
        exp = _create_experiment(client, auth_headers)
        version_id = exp["current_version_id"]

        created = client.post(
            f"/api/v1/experiments/{exp['id']}/robustness",
            headers=auth_headers,
            json={
                "version_id": version_id,
                "kind": "period_slices",
                "config": {"slices": 3},
            },
        )
        assert created.status_code == 201, created.text
        study_id = created.json()["id"]
        ran = client.post(f"/api/v1/robustness/{study_id}/run", headers=auth_headers)
        assert ran.status_code == 200, ran.text
        assert ran.json()["status"] == "completed"

        doomed = client.post(
            f"/api/v1/experiments/{exp['id']}/robustness",
            headers=auth_headers,
            json={
                "version_id": version_id,
                "kind": "period_slices",
                "config": {"slices": 2},
            },
        )
        assert doomed.status_code == 201, doomed.text
        cancelled = client.post(
            f"/api/v1/robustness/{doomed.json()['id']}/cancel", headers=auth_headers
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        record = _record(client, auth_headers, exp["id"])
        assert record["coverage"]["robustness_studies"] == 2
        assert record["coverage"]["robustness_attempts"] == 3
        assert record["coverage"]["runs"] == 3

        studies = record["decision_review"]["robustness_studies"]
        completed_study = next(s for s in studies if s["id"] == study_id)
        assert completed_study["status"] == "completed"
        assert len(completed_study["attempts"]) == 3
        assert completed_study["aggregate"]["counts"]["completed"] == 3

        cancelled_study = next(
            s for s in studies if s["id"] == doomed.json()["id"]
        )
        # A cancelled study is kept in the record with no attempts, so the
        # missing evidence is visible instead of looking like it never happened.
        assert cancelled_study["status"] == "cancelled"
        assert cancelled_study["attempts"] == []

        runs_by_id = {run["id"]: run for run in record["runs"]}
        for attempt in completed_study["attempts"]:
            assert attempt["status"] == "completed"
            linked = runs_by_id[attempt["run_id"]]
            assert linked["robustness"]["cell_key"] == attempt["cell_key"]
            assert linked["robustness"]["study_id"] == study_id


class TestComparisonReview:
    def test_saved_comparison_purpose_is_in_the_record(self, client, auth_headers):
        exp = _create_experiment(client, auth_headers)
        source_id = exp["current_version_id"]
        revision = _revision(client, auth_headers, exp["id"], source_id)
        revision_id = revision["version"]["id"]

        run_a = _run_version(client, auth_headers, source_id)
        run_b = _run_version(client, auth_headers, revision_id)

        saved = client.post(
            "/api/v1/saved-comparisons",
            headers=auth_headers,
            json={
                "experiment_id": exp["id"],
                "run_a_id": run_a["id"],
                "run_b_id": run_b["id"],
                "purpose": "確認成本上升是否改變結論",
                "notes": "若回撤同步擴大，則不採用新成本設定。",
            },
        )
        assert saved.status_code == 201, saved.text

        record = _record(client, auth_headers, exp["id"])
        assert record["coverage"]["saved_comparisons"] == 1

        entry = record["decision_review"]["saved_comparisons"][0]
        assert entry["purpose"] == "確認成本上升是否改變結論"
        assert entry["notes"] == "若回撤同步擴大，則不採用新成本設定。"
        assert entry["run_a_id"] == run_a["id"]
        assert entry["run_b_id"] == run_b["id"]
        assert entry["run_a_version_number"] == 1
        assert entry["run_b_version_number"] == 2


_DEMO_ALPHA_TRADING_DAYS = [
    date(2024, 1, d)
    for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 24, 25, 26, 29, 30, 31)
]


def _seed_demo_bars() -> None:
    """Deterministic synthetic bars for the replay cursor.

    Session creation and advance require stored bars; this fixture is synthetic
    and never represents captured market data.
    """
    from app.models.market_data import MarketDataBar

    db = TestSession()
    try:
        for i, d in enumerate(_DEMO_ALPHA_TRADING_DAYS):
            price = 50.0 + float(i)
            db.add(
                MarketDataBar(
                    instrument_id="DEMO-ALPHA",
                    provider_id="demo",
                    bar_date=d,
                    bar_timestamp=datetime.combine(d, datetime.min.time()),
                    open=price,
                    high=price + 1.0,
                    low=price - 1.0,
                    close=price + 0.5,
                    volume=1_000_000,
                )
            )
        db.commit()
    finally:
        db.close()


class TestDecisionReview:
    def test_simulation_decisions_carry_rationale_and_reflection(
        self, client, auth_headers
    ):
        _seed_demo_bars()
        spec = {**BUY_HOLD_SPEC, "end_date": "2024-01-31"}
        exp = _create_experiment(client, auth_headers, spec=spec)
        version_id = exp["current_version_id"]

        lock = client.put(
            f"/api/v1/experiments/{exp['id']}",
            headers=auth_headers,
            json={"scope": {"status": "locked"}},
        )
        assert lock.status_code == 200, lock.text

        session = client.post(
            f"/api/v1/simulations/versions/{version_id}",
            headers=auth_headers,
            json={"initial_virtual_cash": 100000},
        )
        assert session.status_code == 201, session.text
        session_id = session.json()["id"]

        advanced = client.post(
            f"/api/v1/simulations/{session_id}/advance", headers=auth_headers
        )
        assert advanced.status_code == 200, advanced.text

        decisions = client.get(
            f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers
        ).json()
        assert len(decisions) >= 1
        engine_decision_id = decisions[0]["id"]
        reflected = client.patch(
            f"/api/v1/simulations/{session_id}/decisions/{engine_decision_id}/reflection",
            headers=auth_headers,
            json={"reflection": "第一根 K 線就進場，忽略了盤整訊號。"},
        )
        assert reflected.status_code == 200, reflected.text

        manual = client.post(
            f"/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "hold",
                "idempotency_key": f"record-{uuid.uuid4().hex[:10]}",
                "rationale": "觀望，等待突破確認。",
                "reflection": "事後看，這段觀望避免了假突破。",
            },
        )
        assert manual.status_code == 200, manual.text

        intent = client.post(
            "/api/v1/human-decisions",
            headers=auth_headers,
            json={
                "simulation_session_id": session_id,
                "idempotency_key": f"intent-{uuid.uuid4().hex[:10]}",
                "action": "hold",
                "rationale": "記錄一筆只作研究用途的意向。",
            },
        )
        assert intent.status_code == 201, intent.text

        record = _record(client, auth_headers, exp["id"])
        assert record["coverage"]["simulation_sessions"] == 1

        session_entry = record["decision_review"]["simulation_sessions"][0]
        assert session_entry["id"] == session_id
        assert session_entry["version_number"] == 1
        assert session_entry["status"] == "active"

        api_count = len(
            client.get(
                f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers
            ).json()
        )
        assert len(session_entry["decisions"]) == api_count
        assert record["coverage"]["simulation_decisions"] == api_count

        reflections = {
            d["reflection"] for d in session_entry["decisions"] if d["reflection"]
        }
        assert "第一根 K 線就進場，忽略了盤整訊號。" in reflections
        assert "事後看，這段觀望避免了假突破。" in reflections
        rationales = {
            d["rationale"] for d in session_entry["decisions"] if d["rationale"]
        }
        assert "觀望，等待突破確認。" in rationales

        # The record-only intent lane is kept separate from replay decisions.
        assert record["coverage"]["human_decisions"] == 1
        assert len(session_entry["human_decisions"]) == 1
        assert session_entry["human_decisions"][0]["action"] == "hold"
        assert (
            session_entry["human_decisions"][0]["rationale"]
            == "記錄一筆只作研究用途的意向。"
        )
