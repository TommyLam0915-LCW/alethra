"""``data_source="stored"``: the study prices off rows, never off a fetch.

The venue mode reads Kraken's rolling window and spends external quota. This
mode reads only ``market_data_bars`` at the exact grain requested, so every
refusal below is checked twice — for the reason it names and for the fact that
no venue HTTP call was made and no venue provider was even resolved. A series
the platform does not hold has to stay a refusal: falling back to Kraken or to
the synthetic demo feed would report a stored result that was never stored.

The bars below are deterministic synthetic fixtures written straight onto the
table, so the rules are exercised without touching real market data. Real-series
evidence is collected outside the suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import app.api.strategy_lab as lab
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import KrakenProvider
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES
from app.services.strategy_research import MIN_RESEARCH_BARS
from tests.conftest import TestSession

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
PROVIDER = "kraken"
DAILY = DAILY_INTERVAL_MINUTES
FOUR_H = 240
BASE = datetime(2024, 11, 13, 0, 0)
STUDY_BARS = 720
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _price(index: int) -> Decimal:
    return Decimal(100) + (index % 37) * Decimal("0.3")


def _close(index: int) -> Decimal:
    price = _price(index)
    return price + Decimal("0.5") if index % 3 else price - Decimal("0.5")


class RecordingTransport:
    """Serves the venue leg deterministically and counts every HTTP read."""

    def __init__(self, rows_by_interval):
        self.rows_by_interval = rows_by_interval
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        interval = int(params.get("interval", "1"))
        return {
            "error": [],
            "result": {"XXBTZUSD": self.rows_by_interval.get(interval, []),
                       "last": "unused"},
        }


def _venue_rows(interval: int, count: int = STUDY_BARS):
    step = interval * 60
    last_open = (int(NOW.timestamp()) // step) * step - step
    rows = []
    for index in range(count):
        price = _price(index)
        rows.append(
            [
                last_open - (count - 1 - index) * step,
                str(price),
                str(price + 1),
                str(price - 1),
                str(_close(index)),
                str(price),
                "1.0",
                4,
            ]
        )
    return rows


@pytest.fixture
def venue():
    transport = RecordingTransport({DAILY: _venue_rows(DAILY)})
    set_crypto_provider(
        KrakenProvider(
            transport=transport, clock=lambda: NOW, min_request_interval=0
        )
    )
    yield transport
    reset_crypto_provider()


@pytest.fixture
def no_venue_read(venue, monkeypatch):
    """Any venue resolution or HTTP read fails the test that requests it."""

    def explode(*args, **kwargs):
        raise AssertionError("stored-source study resolved a venue provider")

    monkeypatch.setattr(lab, "_get_provider_for_instrument", explode)
    return venue


def _seed(
    *,
    count: int = STUDY_BARS,
    interval: int = DAILY,
    provider: str = PROVIDER,
    nature: str = "real",
    volume: str = "int",
    skip_index: int | None = None,
    start: datetime = BASE,
) -> list[datetime]:
    """Write one synthetic series onto ``market_data_bars`` at one grain."""
    step = timedelta(minutes=interval)
    stamps = [
        start + index * step for index in range(count) if index != skip_index
    ]
    db = TestSession()
    try:
        for index, stamp in zip(range(count), stamps):
            price = _price(index)
            row = MarketDataBar(
                instrument_id=INSTRUMENT,
                provider_id=provider,
                bar_date=stamp.date(),
                bar_timestamp=stamp,
                interval_minutes=interval,
                open=float(price),
                high=float(price + 1),
                low=float(price - 1),
                close=float(_close(index)),
                fetched_at=stamp + timedelta(hours=23),
            )
            if volume == "int":
                row.volume = 1000 + index
            elif volume == "decimal":
                row.volume_decimal = Decimal("0.75")
            db.add(row)
        existing = (
            db.query(MarketDataMetadata)
            .filter(
                MarketDataMetadata.instrument_id == INSTRUMENT,
                MarketDataMetadata.provider_id == provider,
            )
            .first()
        )
        if existing is None:
            db.add(
                MarketDataMetadata(
                    instrument_id=INSTRUMENT,
                    provider_id=provider,
                    data_nature=nature,
                )
            )
        db.commit()
    finally:
        db.close()
    return stamps


def _clear_bars() -> None:
    db = TestSession()
    try:
        db.query(MarketDataBar).delete()
        db.commit()
    finally:
        db.close()


def _post(client, auth_headers, guard, body):
    """One preview request in a lane where every venue read is fatal."""
    response = client.post(
        "/api/v1/strategy-lab/research", headers=auth_headers, json=body
    )
    assert guard.calls == [], "a stored-source request made a venue call"
    return response


def _stored(body):
    return {"data_source": "stored", "interval_minutes": DAILY, **body}


# -- the mode ---------------------------------------------------------------


def test_stored_study_prices_off_stored_rows_and_says_so(client, auth_headers, no_venue_read):
    _seed()

    response = _post(client, auth_headers, no_venue_read, _stored({"folds": 3, "include_trades": True}))

    assert response.status_code == 200, response.text
    body = response.json()
    dataset = body["dataset"]
    assert dataset["source"] == "stored_market_data_bars"
    assert dataset["network_used"] is False
    assert dataset["provider_id"] == PROVIDER
    assert dataset["data_nature"] == "real"
    assert dataset["interval_minutes"] == DAILY
    assert dataset["bars"] == STUDY_BARS
    assert dataset["row_count"] == STUDY_BARS
    assert dataset["fingerprint"]
    assert dataset["integrity_ok"] is True
    assert dataset["gap_count"] == 0
    assert dataset["missing_intervals"] == 0
    # Not a venue clock: the newest write, taken from the rows themselves.
    assert dataset["fetched_at"] == (BASE + timedelta(days=STUDY_BARS - 1, hours=23)).isoformat() + "+00:00"
    assert dataset["is_stale"] is None
    assert dataset["staleness_assessed"] is False
    assert body["batch_metadata"]["fingerprint"] == dataset["fingerprint"]
    assert body["out_of_sample_trades"] is not None


def test_stored_is_opt_in_and_the_default_still_reads_the_venue(client, auth_headers, venue):
    # If "stored" became the default, an operator could price a study off a
    # stale series while believing they had just read the market.
    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"interval_minutes": DAILY},
    )

    assert response.status_code == 200, response.text
    dataset = response.json()["dataset"]
    assert dataset["source"] == "venue_completed_bars"
    assert dataset["network_used"] is True
    assert venue.calls, "the default mode made no venue read at all"


def test_stored_study_refuses_a_series_the_platform_does_not_hold(client, auth_headers, no_venue_read):
    _seed(interval=DAILY)

    response = _post(client, auth_headers, no_venue_read, _stored({"interval_minutes": FOUR_H}))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "no_stored_bars"
    assert detail["interval_minutes"] == FOUR_H
    assert detail["network_used"] is False
    assert 'data_source="venue"' in detail["message"]


def test_stored_study_binds_the_grain_not_the_timestamp(client, auth_headers, no_venue_read):
    # A daily bar and a four-hour bar legitimately share one midnight, so both
    # series coexist in the table and only the grain column tells them apart.
    _seed(interval=DAILY)
    _seed(count=200, interval=FOUR_H)

    response = _post(client, auth_headers, no_venue_read, _stored({}))

    assert response.status_code == 200, response.text
    dataset = response.json()["dataset"]
    assert dataset["bars"] == STUDY_BARS, "the four-hour rows leaked into the daily series"
    assert dataset["integrity_ok"] is True


def test_stored_study_refuses_a_series_held_by_two_providers(client, auth_headers, no_venue_read):
    _seed(count=400)
    _seed(count=400, provider="binance")

    response = _post(client, auth_headers, no_venue_read, _stored({}))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "ambiguous_stored_providers"
    assert detail["provider_ids"] == ["binance", "kraken"]
    assert detail["network_used"] is False
    assert "merge" in detail["message"]


def test_stored_study_refuses_a_bar_that_records_no_volume(client, auth_headers, no_venue_read):
    missing = 17
    stamps = _seed()
    db = TestSession()
    try:
        row = (
            db.query(MarketDataBar)
            .filter(MarketDataBar.bar_timestamp == stamps[missing])
            .one()
        )
        row.volume = None
        row.volume_decimal = None
        db.commit()
    finally:
        db.close()

    response = _post(client, auth_headers, no_venue_read, _stored({}))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "stored_bar_incomplete"
    assert detail["network_used"] is False
    # The row is named by its own timestamp, not counted away as a zero.
    assert detail["bar_timestamp"] == stamps[missing].replace(tzinfo=timezone.utc).isoformat()
    assert "zero" in detail["message"]

    # A fractional volume recorded only in the decimal column is complete.
    db = TestSession()
    try:
        row = (
            db.query(MarketDataBar)
            .filter(MarketDataBar.bar_timestamp == stamps[missing])
            .one()
        )
        row.volume_decimal = Decimal("0.25")
        db.commit()
    finally:
        db.close()

    ok = _post(client, auth_headers, no_venue_read, _stored({}))
    assert ok.status_code == 200, ok.text


def test_stored_study_measures_gaps_against_its_own_interval(client, auth_headers, no_venue_read):
    _seed(skip_index=300)

    response = _post(client, auth_headers, no_venue_read, _stored({}))

    assert response.status_code == 200, response.text
    dataset = response.json()["dataset"]
    assert dataset["bars"] == STUDY_BARS - 1
    assert dataset["integrity_ok"] is False
    assert dataset["gap_count"] == 1
    assert dataset["missing_intervals"] == 1


def test_stored_study_fingerprint_binds_provider_and_grain(client, auth_headers, no_venue_read):
    _seed()
    daily_kraken = _post(
        client, auth_headers, no_venue_read, _stored({})
    ).json()["dataset"]["fingerprint"]

    _clear_bars()
    _seed(provider="binance")
    daily_binance = _post(
        client, auth_headers, no_venue_read, _stored({})
    ).json()["dataset"]["fingerprint"]

    _seed(count=200, interval=FOUR_H, provider="binance")
    four_h_binance = _post(
        client, auth_headers, no_venue_read, _stored({"interval_minutes": FOUR_H})
    ).json()["dataset"]["fingerprint"]

    assert daily_kraken != daily_binance, "one digest for two providers of the same series"
    assert daily_binance != four_h_binance, "one digest for two grains"


def test_stored_study_refuses_a_series_shorter_than_the_engine_needs(client, auth_headers, no_venue_read):
    _seed(count=MIN_RESEARCH_BARS - 1)

    response = _post(client, auth_headers, no_venue_read, _stored({}))

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["reason"] == "insufficient_dataset"
    assert detail["data_source"] == "stored"
    assert detail["network_used"] is False
    assert detail["bars"] == MIN_RESEARCH_BARS - 1


def test_target_bars_is_a_venue_request_and_is_not_accepted_with_stored(client, auth_headers):
    # A stored read has no paging; answering 2,000 requested bars with the 720
    # rows the platform holds would be a silent short answer.
    response = client.post(
        "/api/v1/strategy-lab/research",
        headers=auth_headers,
        json={"data_source": "stored", "target_bars": 2000},
    )

    assert response.status_code == 422, response.text
    assert "target_bars" in response.text


# -- durable records --------------------------------------------------------


def test_durable_stored_run_records_which_bars_it_was_priced_off(client, auth_headers, no_venue_read):
    stamps = _seed()

    created = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json=_stored({"folds": 3}),
    )

    assert created.status_code == 201, created.text
    assert no_venue_read.calls == []
    body = created.json()
    assert body["dataset_source"] == "stored_market_data_bars"
    assert body["provider_id"] == PROVIDER
    assert body["data_nature"] == "real"
    assert body["bar_count"] == STUDY_BARS
    assert body["trades"], "the synthetic oscillating series should produce trades"

    record = next(
        item
        for item in client.get(
            "/api/v1/strategy-lab/research/runs", headers=auth_headers
        ).json()["records"]
        if item["id"] == body["id"]
    )
    assert record["dataset_source"] == "stored_market_data_bars"

    detail = client.get(
        f"/api/v1/strategy-lab/research/runs/{body['id']}",
        headers=auth_headers,
        params={"include_bars": "true"},
    ).json()
    assert detail["dataset_source"] == "stored_market_data_bars"
    assert len(detail["bars"]) == STUDY_BARS
    # The stored bars are the exact rows the engine read, oldest first.
    assert detail["bars"][0]["open_time"] == stamps[0].replace(tzinfo=timezone.utc).isoformat()
    assert detail["bars"][-1]["open_time"] == stamps[-1].replace(tzinfo=timezone.utc).isoformat()

    export = client.get(
        f"/api/v1/strategy-lab/research/runs/{body['id']}/trades.csv",
        headers=auth_headers,
    )
    assert export.status_code == 200
    assert export.text.splitlines()[0].startswith("trade_index,fold,direction,rule_id")
    assert len(export.text.splitlines()) == 1 + body["trade_count"]


def test_venue_run_and_stored_run_of_the_same_pair_stay_distinguishable(client, auth_headers, venue):
    _seed()
    stored = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json=_stored({"folds": 3}),
    )
    assert stored.status_code == 201, stored.text
    assert venue.calls == []

    venue_run = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json={"interval_minutes": DAILY, "folds": 3},
    )
    assert venue_run.status_code == 201, venue_run.text
    assert venue.calls, "the venue leg made no read, so it proved nothing"

    stored_body, venue_body = stored.json(), venue_run.json()
    assert stored_body["dataset_source"] == "stored_market_data_bars"
    assert venue_body["dataset_source"] == "venue_completed_bars"
    assert stored_body["id"] != venue_body["id"]
    # One Kraken pair read both ways carries the same provider id on both rows,
    # which is why the two families need their own source marker.
    assert stored_body["provider_id"] == venue_body["provider_id"] == PROVIDER
    # And the stored leg's nature is the one written to the metadata table by
    # the storage path, not the "live" the venue reports for the same pair.
    assert stored_body["data_nature"] == "real"
    assert venue_body["data_nature"] == "live"


# -- asked for, and read -----------------------------------------------------


def test_the_stored_run_row_records_what_was_asked_as_well_as_what_was_read(
    client, auth_headers, no_venue_read
):
    """The durable row holds the pair, not only the series.

    ``dataset_source`` says where the bars came from. It cannot say whether the
    caller asked for storage or the platform chose it, and the two columns do
    not even share a vocabulary — ``stored`` beside ``stored_market_data_bars``.
    A reader that has to infer the ask from the answer is reading one column
    twice.
    """
    _seed()

    created = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json=_stored({"folds": 3}),
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["dataset_source_requested"] == "stored"
    assert body["dataset_source"] == "stored_market_data_bars"
    assert body["dataset_source_requested"] != body["dataset_source"]

    for url in (
        "/api/v1/strategy-lab/research/runs",
        f"/api/v1/strategy-lab/research/runs/{body['id']}",
    ):
        response = client.get(url, headers=auth_headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        record = (
            next(item for item in payload["records"] if item["id"] == body["id"])
            if "records" in payload
            else payload
        )
        assert record["dataset_source_requested"] == "stored"
        assert record["dataset_source"] == "stored_market_data_bars"


def test_a_request_that_named_no_source_records_the_default_that_was_applied(
    client, auth_headers, venue
):
    # The payload omits ``data_source`` entirely, so the request holds the
    # model's default rather than a choice the caller typed. Recording it is
    # what keeps "the platform decided" readable apart from "asked to decide"
    # — and apart from a row written before the column existed, which reads
    # NULL in both halves.
    _seed()

    created = client.post(
        "/api/v1/strategy-lab/research/runs",
        headers=auth_headers,
        json={"interval_minutes": DAILY, "folds": 3},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert venue.calls, "the venue leg made no read, so it proved nothing"

    assert body["dataset_source_requested"] == "venue"
    assert body["dataset_source"] == "venue_completed_bars"


def test_the_preview_reports_the_pair_and_stores_nothing(
    client, auth_headers, no_venue_read
):
    _seed()

    response = _post(client, auth_headers, no_venue_read, _stored({"folds": 3}))

    assert response.status_code == 200, response.text
    dataset = response.json()["dataset"]
    assert dataset["data_source_requested"] == "stored"
    assert dataset["source"] == "stored_market_data_bars"
    assert (
        client.get("/api/v1/strategy-lab/research/runs", headers=auth_headers).json()["records"] == []
    ), "the preview wrote a durable record"
