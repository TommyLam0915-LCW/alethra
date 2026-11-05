"""Crypto pairs in the shared instrument catalog.

The research form needs BTC/USD, but every other catalog reader expects the
equity lane and one synthetic-data disclaimer over it. These tests pin the
opt-in contract: crypto appears only under ``market=CRYPTO``, and its detail
route answers from stored venue bars instead of consulting the equity provider.
"""

from datetime import datetime
from decimal import Decimal

from app.api import instruments as instruments_api
from app.services.crypto_series import Candle
from app.services.market_data_storage import MarketDataStorage
from tests.conftest import TestSession

BTC = "CRYPTO:KRAKEN:BTC-USD"
ETH = "CRYPTO:KRAKEN:ETH-USD"
SOL = "CRYPTO:KRAKEN:SOL-USD"


def _candle(day: int, close: float) -> Candle:
    return Candle(
        instrument_id=BTC,
        interval_minutes=1440,
        open_time=datetime(2026, 10, day),
        open=Decimal(str(close - 1)),
        high=Decimal(str(close + 1)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        volume=Decimal("12.5"),
        trade_count=100 + day,
        is_final=True,
    )


def _seed_stored_btc_bars():
    db = TestSession()
    try:
        storage = MarketDataStorage(db)
        result = storage.store_candles(
            instrument_id=BTC,
            provider_id="kraken",
            candles=[_candle(29, 101.0), _candle(30, 103.0), _candle(31, 104.0)],
            timezone="UTC",
            currency="USD",
            data_nature="live",
            adjustment_status="not_applicable",
            provider_note="Kraken public spot OHLC",
        )
        return result
    finally:
        db.close()


class TestCryptoCatalogIsOptIn:
    def test_default_catalog_does_not_mix_crypto_into_the_equity_feed(self, client):
        resp = client.get("/api/v1/instruments")
        assert resp.status_code == 200
        ids = [row["instrument_id"] for row in resp.json()["instruments"]]
        assert ids, "the equity catalog must not be empty"
        assert not any(i.startswith("CRYPTO:") for i in ids)

    def test_market_crypto_lists_the_venue_pair_with_terms_status(self, client):
        resp = client.get("/api/v1/instruments", params={"market": "CRYPTO"})
        assert resp.status_code == 200
        body = resp.json()
        ids = [row["instrument_id"] for row in body["instruments"]]
        assert BTC in ids
        assert all(i.startswith("CRYPTO:") for i in ids)
        assert "US:AAPL" not in ids
        assert body["redistribution"] == "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
        assert body["data_status"]["provider_id"] == "kraken"
        assert body["data_status"]["state"] == "venue_public_data"
        assert "Kraken" in body["data_disclaimer"]

        row = next(r for r in body["instruments"] if r["instrument_id"] == BTC)
        assert row["market"] == "CRYPTO"
        assert row["asset_type"] == "CRYPTO_PAIR"
        assert row["lot_size"] is None
        assert row["lot_size_verified"] is False
        # The venue publishes no historical window, so none may be claimed.
        assert row["data_window"]["availability_source"] == "unknown"
        assert row["data_window"]["available_start"] is None
        assert row["provider_mappings"][0]["provider_id"] == "kraken"

    def test_market_value_is_case_insensitive(self, client):
        upper = client.get("/api/v1/instruments", params={"market": "CRYPTO"})
        lower = client.get("/api/v1/instruments", params={"market": "crypto"})
        assert upper.status_code == lower.status_code == 200
        assert upper.json()["instruments"] == lower.json()["instruments"]

    def test_unknown_market_names_the_supported_lanes(self, client):
        resp = client.get("/api/v1/instruments", params={"market": "MARS"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "CRYPTO" in detail

    def test_asset_type_and_query_filters_apply_to_crypto(self, client):
        pair = client.get(
            "/api/v1/instruments", params={"market": "CRYPTO", "asset_type": "CRYPTO_PAIR"}
        ).json()
        assert [r["instrument_id"] for r in pair["instruments"]] == [BTC, ETH, SOL]

        hit = client.get(
            "/api/v1/instruments", params={"market": "CRYPTO", "q": "btc"}
        ).json()
        assert [r["instrument_id"] for r in hit["instruments"]] == [BTC]

        miss = client.get(
            "/api/v1/instruments", params={"market": "CRYPTO", "q": "no-such-pair"}
        ).json()
        assert miss["instruments"] == []
        assert miss["total"] == 0


class TestCryptoInstrumentDetail:
    def test_detail_answers_from_stored_bars(self, client):
        assert _seed_stored_btc_bars()["stored"] == 3

        resp = client.get(f"/api/v1/instruments/{BTC}")
        assert resp.status_code == 200
        body = resp.json()

        assert body["lane"] == "crypto_live_research"
        assert body["read_only"] is True
        assert body["orders_supported"] is False
        assert body["data_source"] == "Kraken"

        latest = body["latest_bar"]
        assert latest["date"] == "2026-10-31"
        assert latest["close"] == 104.0
        # Storage may render the Numeric at column scale; the amount is exact.
        assert Decimal(latest["volume"]) == Decimal("12.5")
        assert latest["source"] == "storage"

        coverage = body["data_coverage"]
        assert coverage["provider_id"] == "kraken"
        assert coverage["stored_bars"] == 3
        assert coverage["stored_start"] == "2026-10-29"
        assert coverage["stored_end"] == "2026-10-31"
        assert coverage["availability_source"] == "unknown"
        assert coverage["available_start"] is None
        assert coverage["available_end"] is None
        assert coverage["stored_datasets"][0]["data_nature"] == "live"

        assert body["data_quality"]["data_nature"] == "live"
        assert body["data_quality"]["redistribution"] == (
            "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
        )
        assert body["redistribution"] == "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"

    def test_detail_without_stored_bars_reports_empty_not_synthetic(self, client):
        resp = client.get(f"/api/v1/instruments/{BTC}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest_bar"] is None
        assert body["data_coverage"]["stored_bars"] == 0
        assert body["data_coverage"]["stored_datasets"] == []
        assert body["data_quality"]["data_nature"] is None
        assert "synthetic" not in str(body).lower()

    def test_crypto_detail_never_consults_the_equity_provider(self, client, monkeypatch):
        def explode():
            raise AssertionError("crypto detail must not call the equity provider")

        monkeypatch.setattr(instruments_api, "get_data_provider", explode)
        assert client.get(f"/api/v1/instruments/{BTC}").status_code == 200
        catalog = client.get("/api/v1/instruments", params={"market": "CRYPTO"})
        assert catalog.status_code == 200

    def test_unknown_crypto_pair_is_still_a_404(self, client):
        resp = client.get("/api/v1/instruments/CRYPTO:KRAKEN:DOGE-USD")
        assert resp.status_code == 404


def test_equity_detail_is_unchanged(client):
    body = client.get("/api/v1/instruments/US:AAPL").json()
    assert "lane" not in body
    coverage = body["data_coverage"]
    assert coverage["provider_id"] == "demo"
    assert coverage["availability_source"] == "provider_declared"
    assert coverage["available_start"] is not None
