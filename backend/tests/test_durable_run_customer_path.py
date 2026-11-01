"""End-to-end customer path: durable run -> read-back -> compare -> saved reference.

Every step goes through the real HTTP routes the UI calls, so this is the chain
a customer actually drives: create a research object, execute a run (quota
reserved then settled), read the run back, run a revision, compare the two
runs, and save the comparison with a purpose.

It runs on the isolated test database, which is the point: the quota numbers
asserted here describe what one customer path costs, without spending the
candidate environment's allowance to find out.
"""
from datetime import date, timedelta

import pytest

from app.models.quota import QuotaCounter, QuotaReservation
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import MarketDataStorage
from tests.conftest import TestSession

_SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["US:AAPL"],
    "frequency": "1d",
    "start_date": "2024-01-02",
    "end_date": "2024-03-15",
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}

_PROVIDER = "synthetic"


def _weekdays(start: date, count: int):
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, base_price=150.0):
    return [
        OHLCVBar(
            date=day,
            open=base_price + i,
            high=base_price + i + 2,
            low=base_price + i - 1,
            close=base_price + i + 1,
            volume=1_000_000,
        )
        for i, day in enumerate(days)
    ]


@pytest.fixture()
def stored_dataset():
    """Stored bars spanning the spec window, under one named provider."""
    db = TestSession()
    try:
        storage = MarketDataStorage(db)
        days = _weekdays(date(2024, 1, 2), 55)
        storage.store_bars("US:AAPL", _PROVIDER, _bars(days), "synthetic")
    finally:
        db.close()
    return days


def _create_experiment(client, headers, spec=None):
    resp = client.post(
        "/api/v1/experiments/",
        json={
            "title": "Durable run customer path",
            "hypothesis": "A saved comparison remembers why two runs were compared.",
            "strategy_spec": spec or _SPEC,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run(client, headers, version_id, data_source="stored", provider_id=_PROVIDER):
    resp = client.post(
        f"/api/v1/versions/{version_id}/runs",
        params={"data_source": data_source, "provider_id": provider_id},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _quota_state(user_email="test@alethra.io"):
    db = TestSession()
    try:
        from app.models.user import User

        user = db.query(User).filter(User.email == user_email).first()
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == user.id,
                QuotaCounter.quota_type == "research_run",
            )
            .first()
        )
        reserved = (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user.id,
                QuotaReservation.quota_type == "research_run",
                QuotaReservation.status == "reserved",
            )
            .all()
        )
        return {
            "used": counter.used if counter else 0,
            "reserved": sum(r.amount for r in reserved),
        }
    finally:
        db.close()


def test_customer_path_run_read_back_compare_and_save(client, auth_headers, stored_dataset):
    experiment = _create_experiment(client, auth_headers)
    version_id = experiment["current_version_id"]

    first_run = _run(client, auth_headers, version_id)
    assert first_run["status"] == "completed"
    assert first_run["metrics"], "a completed run must carry metrics"
    assert first_run["dataset_checksum"]

    read_back = client.get(f"/api/v1/runs/{first_run['id']}", headers=auth_headers)
    assert read_back.status_code == 200
    replayed = read_back.json()
    assert replayed["status"] == "completed"
    assert replayed["metrics"] == first_run["metrics"]
    assert replayed["dataset_checksum"] == first_run["dataset_checksum"]
    assert replayed["result_json"]["bars_used"] == replayed["metrics"]["trading_days"]

    coverage = replayed["result_json"]["dataset_coverage"]["US:AAPL"]
    assert coverage["source"] == "stored"
    assert coverage["provider_id"] == _PROVIDER
    assert coverage["bar_count"] == replayed["result_json"]["bars_used"]
    assert coverage["data_nature"] == "synthetic"

    revised_spec = dict(_SPEC, slippage_rate=0.002)
    revision = client.post(
        f"/api/v1/experiments/{experiment['id']}/revisions",
        json={
            "base_version_id": version_id,
            "strategy_spec": revised_spec,
            "change_reason": "Charge slippage to see what the cost assumption does.",
        },
        headers=auth_headers,
    )
    assert revision.status_code == 201, revision.text
    second_version_id = revision.json()["version"]["id"]

    second_run = _run(client, auth_headers, second_version_id)
    assert second_run["status"] == "completed"
    assert second_run["id"] != first_run["id"]

    comparison = client.get(
        "/api/v1/runs/compare",
        params={"run_a": first_run["id"], "run_b": second_run["id"]},
        headers=auth_headers,
    )
    assert comparison.status_code == 200, comparison.text
    body = comparison.json()
    assert body["period_overlap"] is not None
    assert body["period_overlap"]["overlap"] == {
        "start": _SPEC["start_date"],
        "end": _SPEC["end_date"],
    }
    by_field = {row["field"]: row for row in body["conditions_comparison"]}
    assert by_field["slippage_rate"]["comparison_status"] == "differ"
    assert by_field["slippage_rate"]["value_a"] == 0.0
    assert by_field["slippage_rate"]["value_b"] == 0.002
    assert by_field["engine_version"]["comparison_status"] == "match"
    metric_fields = {row["field"] for row in body["metrics_comparison"]}
    assert metric_fields == {
        "total_return_pct",
        "final_equity",
        "max_drawdown_pct",
        "total_trades",
        "sharpe_ratio",
    }

    saved = client.post(
        "/api/v1/saved-comparisons",
        json={
            "experiment_id": experiment["id"],
            "run_a_id": first_run["id"],
            "run_b_id": second_run["id"],
            "purpose": "Check whether slippage changes the hold decision.",
            "notes": "Second run charges 20 bps; the entry rule is unchanged.",
        },
        headers=auth_headers,
    )
    assert saved.status_code == 201, saved.text
    saved_body = saved.json()
    assert saved_body["run_a_id"] == first_run["id"]
    assert saved_body["run_b_id"] == second_run["id"]

    listing = client.get(
        f"/api/v1/experiments/{experiment['id']}/saved-comparisons",
        headers=auth_headers,
    )
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [saved_body["id"]]

    reread = client.get(
        f"/api/v1/saved-comparisons/{saved_body['id']}", headers=auth_headers
    )
    assert reread.status_code == 200
    assert reread.json()["purpose"] == saved_body["purpose"]
    assert reread.json()["notes"] == saved_body["notes"]

    assert _quota_state() == {"used": 2, "reserved": 0}


def test_reading_a_run_does_not_charge_it_again(client, auth_headers, stored_dataset):
    experiment = _create_experiment(client, auth_headers)
    run = _run(client, auth_headers, experiment["current_version_id"])
    assert _quota_state() == {"used": 1, "reserved": 0}

    for _ in range(3):
        assert (
            client.get(f"/api/v1/runs/{run['id']}", headers=auth_headers).status_code
            == 200
        )
    listing = client.get(
        f"/api/v1/experiments/{experiment['id']}/runs", headers=auth_headers
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert _quota_state() == {"used": 1, "reserved": 0}


def test_refused_run_is_recorded_but_not_charged(client, auth_headers, stored_dataset):
    experiment = _create_experiment(client, auth_headers)
    refused = _run(
        client,
        auth_headers,
        experiment["current_version_id"],
        provider_id="absent",
    )
    assert refused["status"] == "failed"
    assert refused["error_details"]
    assert refused["metrics"] is None

    assert _quota_state() == {"used": 0, "reserved": 0}


def test_comparison_refuses_a_run_that_did_not_complete(client, auth_headers, stored_dataset):
    experiment = _create_experiment(client, auth_headers)
    completed = _run(client, auth_headers, experiment["current_version_id"])
    refused = _run(
        client,
        auth_headers,
        experiment["current_version_id"],
        provider_id="absent",
    )

    body = client.get(
        "/api/v1/runs/compare",
        params={"run_a": completed["id"], "run_b": refused["id"]},
        headers=auth_headers,
    )
    assert body.status_code == 400
    assert "completed" in body.json()["detail"]

    same = client.get(
        "/api/v1/runs/compare",
        params={"run_a": completed["id"], "run_b": completed["id"]},
        headers=auth_headers,
    )
    assert same.status_code == 400
    assert "itself" in same.json()["detail"]


def test_saved_reference_cannot_cite_a_failed_run(client, auth_headers, stored_dataset):
    experiment = _create_experiment(client, auth_headers)
    completed = _run(client, auth_headers, experiment["current_version_id"])
    refused = _run(
        client,
        auth_headers,
        experiment["current_version_id"],
        provider_id="absent",
    )

    saved = client.post(
        "/api/v1/saved-comparisons",
        json={
            "experiment_id": experiment["id"],
            "run_a_id": completed["id"],
            "run_b_id": refused["id"],
            "purpose": "Should not be storable.",
        },
        headers=auth_headers,
    )
    assert saved.status_code == 404
    listing = client.get(
        f"/api/v1/experiments/{experiment['id']}/saved-comparisons",
        headers=auth_headers,
    )
    assert listing.json() == []
