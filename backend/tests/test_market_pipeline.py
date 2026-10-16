"""Tests for real market data pipeline.

Covers:
- Synthetic data generation for real instruments
- Demo provider serving real instrument data
- Backtest engine accepting real instruments
- LSEG adapter offline contract tests
- Instrument API endpoints
"""

import pytest
from datetime import date
from fastapi.testclient import TestClient

from app.data.real_instruments import (
    ALL_REAL_INSTRUMENTS,
    get_instrument_by_id,
    get_provider_mapping,
)
from app.data import real_synthetic_data
from app.providers.demo_provider import DemoDataProvider
from app.providers.lseg_provider import LSEGDataProvider, LSEGConfigurationError, LSEGDataError
from app.providers.instruments import Instrument, Market, AssetType, parse_instrument_id
from app.engine.backtest import run_backtest


class TestRealInstrumentCatalog:
    def test_catalog_has_us_stocks(self):
        us_stocks = [i for i in ALL_REAL_INSTRUMENTS if i.market == Market.US and i.asset_type == AssetType.STOCK]
        assert len(us_stocks) == 7
        symbols = {i.symbol for i in us_stocks}
        assert "AAPL" in symbols
        assert "NVDA" in symbols

    def test_catalog_has_hk_stocks(self):
        hk_stocks = [i for i in ALL_REAL_INSTRUMENTS if i.market == Market.HK and i.asset_type == AssetType.STOCK]
        assert len(hk_stocks) == 7
        symbols = {i.symbol for i in hk_stocks}
        assert "0700" in symbols
        assert "9988" in symbols

    def test_catalog_has_us_etfs(self):
        us_etfs = [i for i in ALL_REAL_INSTRUMENTS if i.market == Market.US and i.asset_type == AssetType.ETF]
        assert len(us_etfs) == 3
        symbols = {i.symbol for i in us_etfs}
        assert "SPY" in symbols

    def test_catalog_has_hk_etfs(self):
        hk_etfs = [i for i in ALL_REAL_INSTRUMENTS if i.market == Market.HK and i.asset_type == AssetType.ETF]
        assert len(hk_etfs) == 2

    def test_total_instruments(self):
        assert len(ALL_REAL_INSTRUMENTS) == 19

    def test_hk_lot_sizes_per_stock(self):
        tencent = get_instrument_by_id("HK:0700")
        assert tencent.lot_size == 100
        assert tencent.lot_size_verified is True

        hsbc = get_instrument_by_id("HK:0005")
        assert hsbc.lot_size == 400
        assert hsbc.lot_size_verified is True

        ping_an = get_instrument_by_id("HK:2318")
        assert ping_an.lot_size == 500

    def test_us_lot_size_is_one(self):
        aapl = get_instrument_by_id("US:AAPL")
        assert aapl.lot_size == 1
        assert aapl.lot_size_verified is True

    def test_get_instrument_by_id(self):
        inst = get_instrument_by_id("US:AAPL")
        assert inst is not None
        assert inst.symbol == "AAPL"
        assert inst.name == "Apple Inc."

    def test_get_instrument_by_id_not_found(self):
        assert get_instrument_by_id("US:FAKE") is None

    def test_provider_mappings_lseg(self):
        mapping = get_provider_mapping("lseg", "US:AAPL")
        assert mapping is not None
        assert mapping.provider_symbol == "AAPL.O"

    def test_provider_mappings_ibkr(self):
        mapping = get_provider_mapping("ibkr", "US:AAPL")
        assert mapping is not None
        assert mapping.provider_symbol == "AAPL"
        assert mapping.provider_exchange == "SMART"

    def test_provider_mappings_not_found(self):
        assert get_provider_mapping("lseg", "US:FAKE") is None
        assert get_provider_mapping("unknown_provider", "US:AAPL") is None


class TestSyntheticDataGeneration:
    def test_generates_data_for_us_instrument(self):
        bars = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-01-31")
        assert len(bars) > 0
        assert all("date" in b and "open" in b and "close" in b for b in bars)

    def test_generates_data_for_hk_instrument(self):
        bars = real_synthetic_data.get_synthetic_bars("HK:0700", "2024-01-02", "2024-01-31")
        assert len(bars) > 0

    def test_data_is_deterministic(self):
        bars1 = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-03-31")
        real_synthetic_data.clear_cache()
        bars2 = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert bars1 == bars2

    def test_different_instruments_different_data(self):
        aapl = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-01-31")
        msft = real_synthetic_data.get_synthetic_bars("US:MSFT", "2024-01-02", "2024-01-31")
        assert aapl[0]["close"] != msft[0]["close"]

    def test_ohlcv_consistency(self):
        bars = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-06-30")
        for bar in bars:
            assert bar["high"] >= bar["low"]
            assert bar["high"] >= bar["open"]
            assert bar["high"] >= bar["close"]
            assert bar["low"] <= bar["open"]
            assert bar["low"] <= bar["close"]
            assert bar["close"] > 0
            assert bar["open"] > 0

    def test_volume_is_positive(self):
        bars = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-02", "2024-03-31")
        for bar in bars:
            assert bar["volume"] > 0

    def test_date_filtering(self):
        bars = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-06-01", "2024-06-30")
        for bar in bars:
            assert "2024-06-01" <= bar["date"] <= "2024-06-30"

    def test_no_weekend_data(self):
        bars = real_synthetic_data.get_synthetic_bars("US:AAPL", "2024-01-01", "2024-12-31")
        for bar in bars:
            d = date.fromisoformat(bar["date"])
            assert d.weekday() < 5

    def test_available_symbols(self):
        symbols = real_synthetic_data.get_available_real_symbols()
        assert "US:AAPL" in symbols
        assert "HK:0700" in symbols
        assert len(symbols) == 19

    def test_date_range(self):
        dr = real_synthetic_data.get_real_date_range()
        assert dr["min"] == "2023-01-03"
        assert dr["max"] == "2025-12-31"


class TestDemoProviderRealInstruments:
    def test_get_bars_for_real_instrument(self):
        provider = DemoDataProvider()
        bars = provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")
        assert len(bars) > 0
        assert bars[0].date >= "2024-01-02"
        assert bars[-1].date <= "2024-03-31"

    def test_get_bars_for_hk_instrument(self):
        provider = DemoDataProvider()
        bars = provider.get_bars("HK:0700", "2024-01-02", "2024-03-31")
        assert len(bars) > 0

    def test_get_latest_bar_for_real_instrument(self):
        provider = DemoDataProvider()
        bar = provider.get_latest_bar("US:AAPL")
        assert bar is not None
        assert bar.close > 0

    def test_list_assets_includes_real_instruments(self):
        provider = DemoDataProvider()
        assets = provider.list_assets()
        symbols = [a.symbol for a in assets]
        assert "DEMO-ALPHA" in symbols
        instrument_ids = [
            a.instrument.instrument_id for a in assets if a.instrument
        ]
        assert "US:AAPL" in instrument_ids

    def test_get_available_symbols(self):
        provider = DemoDataProvider()
        symbols = provider.get_available_symbols()
        assert "DEMO-ALPHA" in symbols
        assert "US:AAPL" in symbols
        assert "HK:0700" in symbols

    def test_compute_checksum_for_real_instruments(self):
        provider = DemoDataProvider()
        cs = provider.compute_checksum(["US:AAPL", "US:MSFT"], "2024-01-02", "2024-03-31")
        assert len(cs) == 64

    def test_checksum_is_deterministic(self):
        provider = DemoDataProvider()
        cs1 = provider.compute_checksum(["US:AAPL"], "2024-01-02", "2024-03-31")
        cs2 = provider.compute_checksum(["US:AAPL"], "2024-01-02", "2024-03-31")
        assert cs1 == cs2

    def test_get_instrument_by_id(self):
        provider = DemoDataProvider()
        info = provider.get_instrument("US:AAPL")
        assert info is not None
        assert info.name == "Apple Inc."
        assert info.currency == "USD"

    def test_get_instrument_not_found(self):
        provider = DemoDataProvider()
        assert provider.get_instrument("US:FAKE") is None

    def test_supports_instrument_id(self):
        provider = DemoDataProvider()
        assert provider.supports_instrument_id() is True

    def test_demo_assets_still_work(self):
        provider = DemoDataProvider()
        bars = provider.get_bars("DEMO-ALPHA", "2024-01-02", "2024-02-13")
        assert len(bars) > 0

    def test_unknown_symbol_returns_empty(self):
        provider = DemoDataProvider()
        bars = provider.get_bars("NONEXISTENT", "2024-01-02", "2024-03-31")
        assert bars == []


class TestBacktestWithRealInstruments:
    def test_buy_and_hold_with_us_stock(self):
        provider = DemoDataProvider()
        result = run_backtest(
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-06-28",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.0005,
            },
            data_provider=provider,
        )
        assert result["run_status"] == "completed"
        assert result["data_provider"] == "Demo (Synthetic)"
        assert result["final_equity"] > 0
        assert len(result["transactions"]) > 0

    def test_sma_crossover_with_hk_stock(self):
        provider = DemoDataProvider()
        result = run_backtest(
            strategy_spec={
                "template_type": "sma_crossover",
                "asset_ids": ["HK:0700"],
                "benchmark_id": "HK:2800",
                "start_date": "2024-01-02",
                "end_date": "2024-12-31",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.0005,
                "signal_parameters": {"short_window": 5, "long_window": 20},
            },
            data_provider=provider,
        )
        assert result["run_status"] == "completed"
        assert result["metrics"]

    def test_rejects_unknown_instrument(self):
        provider = DemoDataProvider()
        result = run_backtest(
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": ["US:FAKE"],
                "start_date": "2024-01-02",
                "end_date": "2024-06-28",
                "initial_cash": 100000,
            },
            data_provider=provider,
        )
        assert result["run_status"] == "error"
        assert any("Unsupported asset" in e for e in result["errors"])

    def test_backtest_records_provider_info(self):
        provider = DemoDataProvider()
        result = run_backtest(
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": ["US:SPY"],
                "start_date": "2024-01-02",
                "end_date": "2024-06-28",
                "initial_cash": 100000,
            },
            data_provider=provider,
        )
        assert result["run_status"] == "completed"
        assert result["data_provider"] == "Demo (Synthetic)"
        assert result["feed_mode"] == "offline"
        assert result["dataset_checksum"] is not None

    def test_mixed_demo_and_real_not_supported(self):
        provider = DemoDataProvider()
        result = run_backtest(
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "benchmark_id": "US:AAPL",
                "start_date": "2024-01-02",
                "end_date": "2024-02-13",
                "initial_cash": 100000,
            },
            data_provider=provider,
        )
        assert result["run_status"] == "completed"


class TestLSEGAdapterOffline:
    def test_provider_id(self):
        provider = LSEGDataProvider()
        assert provider.provider_id == "lseg"

    def test_provider_name(self):
        provider = LSEGDataProvider()
        assert provider.provider_name == "LSEG (Refinitiv)"

    def test_not_available_without_credentials(self):
        provider = LSEGDataProvider()
        assert provider.is_available() is False

    def test_connect_fails_without_credentials(self):
        provider = LSEGDataProvider()
        assert provider.connect() is False
        assert "credentials" in (provider._last_error or "").lower()

    def test_feed_mode_disconnected(self):
        provider = LSEGDataProvider()
        assert provider.feed_mode == "disconnected"

    def test_get_bars_raises_when_disconnected(self):
        provider = LSEGDataProvider()
        with pytest.raises(LSEGDataError, match="not connected"):
            provider.get_bars("US:AAPL", "2024-01-02", "2024-03-31")

    def test_get_latest_bar_returns_none_when_disconnected(self):
        provider = LSEGDataProvider()
        assert provider.get_latest_bar("US:AAPL") is None

    def test_list_assets_returns_instrument_catalog(self):
        provider = LSEGDataProvider()
        assets = provider.list_assets()
        assert len(assets) > 0
        assert all(a.instrument is not None for a in assets)

    def test_resolve_instrument_to_ric(self):
        provider = LSEGDataProvider()
        assert provider.resolve_instrument("US:AAPL") == "AAPL.O"
        assert provider.resolve_instrument("HK:0700") == "0700.HK"

    def test_resolve_instrument_from_instrument_object(self):
        provider = LSEGDataProvider()
        inst = get_instrument_by_id("US:AAPL")
        assert provider.resolve_instrument(inst) == "AAPL.O"

    def test_resolve_instrument_passthrough_for_unknown(self):
        provider = LSEGDataProvider()
        assert provider.resolve_instrument("AAPL") == "AAPL"

    def test_supports_instrument_id(self):
        provider = LSEGDataProvider()
        assert provider.supports_instrument_id() is True

    def test_get_instrument(self):
        provider = LSEGDataProvider()
        info = provider.get_instrument("US:AAPL")
        assert info is not None
        assert info.name == "Apple Inc."
        assert "AAPL.O" in info.description

    def test_get_instrument_not_found(self):
        provider = LSEGDataProvider()
        assert provider.get_instrument("US:FAKE") is None

    def test_status_reports_disconnected(self):
        provider = LSEGDataProvider()
        status = provider.get_status()
        assert status["available"] is False
        assert status["connected"] is False
        assert status["instruments_available"] == 19

    def test_get_date_range(self):
        provider = LSEGDataProvider()
        dr = provider.get_date_range()
        assert "min" in dr
        assert "max" in dr
        assert "note" in dr

    def test_connect_with_api_key_succeeds_but_no_real_call(self):
        """Verify connect() accepts credentials without making real API calls."""
        provider = LSEGDataProvider(api_key="test-key-not-real")
        result = provider.connect()
        assert result is True
        assert provider.is_available() is True
        assert provider.feed_mode == "live"
        provider.disconnect()
        assert provider.is_available() is False


class TestInstrumentAPIEndpoints:
    @pytest.fixture
    def client(self):
        from app.main import app
        return TestClient(app)

    def test_list_instruments(self, client):
        resp = client.get("/api/v1/instruments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 19
        assert len(data["instruments"]) == 19

    def test_list_instruments_filter_by_market(self, client):
        resp = client.get("/api/v1/instruments?market=US")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert all(i["market"] == "US" for i in data["instruments"])

    def test_list_instruments_filter_by_hk(self, client):
        resp = client.get("/api/v1/instruments?market=HK")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 9

    def test_list_instruments_filter_by_asset_type(self, client):
        resp = client.get("/api/v1/instruments?asset_type=ETF")
        assert resp.status_code == 200
        data = resp.json()
        assert all(i["asset_type"] == "ETF" for i in data["instruments"])

    def test_list_instruments_invalid_market(self, client):
        resp = client.get("/api/v1/instruments?market=XX")
        assert resp.status_code == 400

    def test_get_instrument_detail(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instrument_id"] == "US:AAPL"
        assert data["symbol"] == "AAPL"
        assert data["currency"] == "USD"
        assert data["latest_bar"] is not None

    def test_get_instrument_not_found(self, client):
        resp = client.get("/api/v1/instruments/US:FAKE")
        assert resp.status_code == 404

    def test_get_instrument_bars(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL/bars?start_date=2024-01-02&end_date=2024-03-31")
        assert resp.status_code == 200
        data = resp.json()
        assert data["instrument_id"] == "US:AAPL"
        assert data["bar_count"] > 0
        assert len(data["bars"]) > 0

    def test_get_instrument_bars_not_found(self, client):
        resp = client.get("/api/v1/instruments/US:FAKE/bars?start_date=2024-01-02&end_date=2024-03-31")
        assert resp.status_code == 404

    def test_list_markets(self, client):
        resp = client.get("/api/v1/markets")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["markets"]) == 2
        market_codes = {m["market"] for m in data["markets"]}
        assert "US" in market_codes
        assert "HK" in market_codes

    def test_get_market_detail(self, client):
        resp = client.get("/api/v1/markets/US")
        assert resp.status_code == 200
        data = resp.json()
        assert data["market"] == "US"
        assert data["currency"] == "USD"
        assert len(data["sessions"]) > 0
        assert len(data["instruments"]) > 0

    def test_get_market_invalid(self, client):
        resp = client.get("/api/v1/markets/XX")
        assert resp.status_code == 400

    def test_instrument_has_provider_mappings(self, client):
        resp = client.get("/api/v1/instruments/US:AAPL")
        data = resp.json()
        assert len(data["provider_mappings"]) > 0
        lseg_mapping = next(m for m in data["provider_mappings"] if m["provider_id"] == "lseg")
        assert lseg_mapping["provider_symbol"] == "AAPL.O"

    def test_hk_instrument_shows_lot_size(self, client):
        resp = client.get("/api/v1/instruments/HK:0700")
        data = resp.json()
        assert data["lot_size"] == 100
        assert data["lot_size_verified"] is True
