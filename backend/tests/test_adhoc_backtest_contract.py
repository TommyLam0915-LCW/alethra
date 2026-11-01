"""Ad-hoc backtest: computed and returned, never stored or metered.

The route used to answer with an untyped dict whose only completion marker was
``completed_at``, which reads like a stored run. The contract now says what it
is — ``persisted: false`` and ``run_id: null`` — and points at the durable
endpoint. These tests pin both halves: the shape of the answer, and the fact
that no run row and no quota movement accompany it.
"""
from datetime import date, timedelta

import pytest

from app.models.quota import QuotaCounter, QuotaReservation
from app.models.research_run import ResearchRun
from app.models.user import User
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
    days, cursor = [], start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


@pytest.fixture()
def stored_dataset():
    db = TestSession()
    try:
        days = _weekdays(date(2024, 1, 2), 55)
        MarketDataStorage(db).store_bars(
            "US:AAPL",
            _PROVIDER,
            [
                OHLCVBar(
                    date=day,
                    open=150.0 + i,
                    high=152.0 + i,
                    low=149.0 + i,
                    close=151.0 + i,
                    volume=1_000_000,
                )
                for i, day in enumerate(days)
            ],
            "synthetic",
        )
    finally:
        db.close()
    return days


def _create_version(client, headers):
    resp = client.post(
        "/api/v1/experiments/",
        json={"title": "Ad-hoc contract", "strategy_spec": _SPEC},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["current_version_id"]


def _stored_state():
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").first()
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == user.id,
                QuotaCounter.quota_type == "research_run",
            )
            .first()
        )
        reservations = (
            db.query(QuotaReservation)
            .filter(
                QuotaReservation.user_id == user.id,
                QuotaReservation.quota_type == "research_run",
            )
            .count()
        )
        runs = db.query(ResearchRun).count()
        return {
            "used": counter.used if counter else 0,
            "reservations": reservations,
            "runs": runs,
        }
    finally:
        db.close()


def test_adhoc_response_declares_non_persistence(client, auth_headers, stored_dataset):
    experiment_id, version_id = _create_version(client, auth_headers)
    before = _stored_state()

    resp = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["persisted"] is False
    assert body["run_id"] is None
    assert body["experiment_id"] == experiment_id
    assert body["version_id"] == version_id

    assert body["durable_run"]["method"] == "POST"
    assert body["durable_run"]["path"] == "/api/v1/versions/{version_id}/runs"
    assert body["durable_run"]["persists"] is True

    assert body["computed_at"] == body["completed_at"]
    assert body["status"] in {"completed", "error"}


def test_adhoc_run_produces_real_numbers_from_the_stored_bars(client, auth_headers, stored_dataset):
    experiment_id, version_id = _create_version(client, auth_headers)
    body = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
        headers=auth_headers,
    ).json()

    assert body["status"] == "completed", body.get("errors")
    assert body["metrics"], "a completed computation must carry metrics"
    assert body["metrics"]["trading_days"] == len(body["equity_curve"])
    assert body["trades"], "buy_and_hold over 55 rising bars must produce a trade"
    assert body["metrics"]["final_equity"] > 0
    assert body["data_nature"] == {"US:AAPL": "synthetic"}
    assert body["dataset_checksum"]


def test_adhoc_run_creates_no_run_row_and_spends_no_quota(client, auth_headers, stored_dataset):
    experiment_id, version_id = _create_version(client, auth_headers)
    before = _stored_state()

    resp = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
        headers=auth_headers,
    )
    assert resp.status_code == 200

    after = _stored_state()
    assert after == before, "an ad-hoc computation must not store a run or move quota"
    assert after["used"] == before["used"]
    assert after["reservations"] == before["reservations"]


def test_adhoc_refusal_is_still_a_clean_404_with_no_side_effects(client, auth_headers):
    before = _stored_state()
    resp = client.post(
        "/api/v1/experiments/00000000-0000-0000-0000-000000000000"
        "/versions/00000000-0000-0000-0000-000000000001/backtest",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert _stored_state() == before


def test_adhoc_response_leaks_no_connection_string(client, auth_headers, stored_dataset):
    experiment_id, version_id = _create_version(client, auth_headers)
    text = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
        headers=auth_headers,
    ).text
    for marker in ("postgresql://", "sqlite:///", "DATABASE_URL", "password="):
        assert marker not in text


def test_durable_hint_points_at_a_real_route(client, auth_headers, stored_dataset):
    experiment_id, version_id = _create_version(client, auth_headers)
    body = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
        headers=auth_headers,
    ).json()

    spec = client.get("/openapi.json").json()
    operations = spec["paths"][body["durable_run"]["path"]]
    assert body["durable_run"]["method"].lower() in operations

    adhoc = spec["paths"][
        "/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest"
    ]["post"]
    assert adhoc["summary"]
    assert len(adhoc["description"]) > 80
    assert "NOT stored" in adhoc["description"]
    assert "$ref" in adhoc["responses"]["200"]["content"]["application/json"]["schema"]

    schemas = spec["components"]["schemas"]
    response_schema = schemas["AdHocBacktestResponse"]
    properties = response_schema["properties"]
    assert set(properties) >= {"persisted", "run_id", "durable_run", "computed_at"}
    assert properties["persisted"]["const"] is False
    assert properties["run_id"]["type"] == "null"
