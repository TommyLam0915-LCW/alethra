import pytest
from app.providers.data_provider import DataProvider, OHLCVBar, AssetInfo
from app.providers.demo_provider import DemoDataProvider
from app.providers.provider_registry import get_data_provider, get_provider_info, register_provider


class TestDemoDataProvider:
    def setup_method(self):
        self.provider = DemoDataProvider()

    def test_provider_id(self):
        assert self.provider.provider_id == "demo"

    def test_provider_name(self):
        assert self.provider.provider_name == "Demo (Synthetic)"

    def test_feed_mode(self):
        assert self.provider.feed_mode == "offline"

    def test_is_available(self):
        assert self.provider.is_available() is True

    def test_list_assets(self):
        assets = self.provider.list_assets()
        assert len(assets) == 3
        symbols = {a.symbol for a in assets}
        assert "DEMO-ALPHA" in symbols
        assert "DEMO-BETA" in symbols
        assert "DEMO-BENCHMARK" in symbols
        for a in assets:
            assert a.asset_type == "synthetic"
            assert a.currency == "USD"
            assert a.description

    def test_get_bars_returns_sorted_bars(self):
        bars = self.provider.get_bars("DEMO-ALPHA", "2024-01-02", "2024-01-12")
        assert len(bars) > 0
        for bar in bars:
            assert isinstance(bar, OHLCVBar)
            assert bar.open > 0
            assert bar.high >= bar.low
            assert bar.close > 0
        dates = [b.date for b in bars]
        assert dates == sorted(dates)

    def test_get_bars_respects_date_range(self):
        bars = self.provider.get_bars("DEMO-ALPHA", "2024-01-10", "2024-01-20")
        for bar in bars:
            assert bar.date >= "2024-01-10"
            assert bar.date <= "2024-01-20"

    def test_get_bars_unknown_symbol(self):
        bars = self.provider.get_bars("NONEXISTENT", "2024-01-02", "2024-01-31")
        assert bars == []

    def test_get_latest_bar(self):
        bar = self.provider.get_latest_bar("DEMO-ALPHA")
        assert bar is not None
        assert bar.date == "2024-02-13"
        assert bar.close > 0

    def test_get_latest_bar_unknown_symbol(self):
        bar = self.provider.get_latest_bar("NONEXISTENT")
        assert bar is None

    def test_get_date_range(self):
        dr = self.provider.get_date_range()
        assert dr["min"] == "2024-01-02"
        assert dr["max"] == "2024-02-13"
        assert dr["total_trading_days"] == 30

    def test_compute_checksum_deterministic(self):
        cs1 = self.provider.compute_checksum(["DEMO-ALPHA"], "2024-01-02", "2024-01-31")
        cs2 = self.provider.compute_checksum(["DEMO-ALPHA"], "2024-01-02", "2024-01-31")
        assert cs1 == cs2
        assert len(cs1) == 64

    def test_compute_checksum_changes_with_data(self):
        cs1 = self.provider.compute_checksum(["DEMO-ALPHA"], "2024-01-02", "2024-01-31")
        cs2 = self.provider.compute_checksum(["DEMO-ALPHA"], "2024-01-02", "2024-02-13")
        assert cs1 != cs2

    def test_compute_checksum_symbol_order_invariant(self):
        cs1 = self.provider.compute_checksum(["DEMO-ALPHA", "DEMO-BETA"], "2024-01-02", "2024-01-31")
        cs2 = self.provider.compute_checksum(["DEMO-BETA", "DEMO-ALPHA"], "2024-01-02", "2024-01-31")
        assert cs1 == cs2

    def test_get_status(self):
        status = self.provider.get_status()
        assert status["provider_id"] == "demo"
        assert status["available"] is True
        assert status["feed_mode"] == "offline"


class TestProviderRegistry:
    def test_get_data_provider_returns_demo_by_default(self, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "demo")
        from app.config import get_settings
        get_settings.cache_clear()
        provider = get_data_provider()
        assert provider.provider_id == "demo"

    def test_get_data_provider_unknown_raises(self, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "nonexistent")
        from app.config import get_settings
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="Unknown data provider"):
            get_data_provider()
        monkeypatch.setenv("DATA_PROVIDER", "demo")
        get_settings.cache_clear()

    def test_get_provider_info(self, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "demo")
        from app.config import get_settings
        get_settings.cache_clear()
        info = get_provider_info()
        assert info["active_provider"] == "demo"
        assert info["available"] is True

    def test_register_provider(self):
        class MockProvider(DataProvider):
            @property
            def provider_id(self): return "mock"
            @property
            def provider_name(self): return "Mock"
            @property
            def feed_mode(self): return "test"
            def list_assets(self): return []
            def get_bars(self, symbol, start, end): return []
            def get_latest_bar(self, symbol): return None
            def get_date_range(self): return {}
            def compute_checksum(self, symbols, start, end): return "abc"

        register_provider("mock", MockProvider())
        from app.providers.provider_registry import _PROVIDERS
        assert "mock" in _PROVIDERS
        del _PROVIDERS["mock"]


class TestBacktestWithProvider:
    def test_backtest_with_demo_provider(self):
        from app.engine.backtest import run_backtest
        provider = DemoDataProvider()
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "benchmark_id": "DEMO-BENCHMARK",
            "start_date": "2024-01-02",
            "end_date": "2024-02-13",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        result = run_backtest(spec, data_provider=provider)
        assert result["run_status"] == "completed"
        assert result["data_provider"] == "Demo (Synthetic)"
        assert result["feed_mode"] == "offline"
        assert result["dataset_checksum"] is not None
        assert len(result["transactions"]) > 0

    def test_backtest_without_provider_backward_compatible(self):
        from app.engine.backtest import run_backtest
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "benchmark_id": "DEMO-BENCHMARK",
            "start_date": "2024-01-02",
            "end_date": "2024-02-13",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        result = run_backtest(spec)
        assert result["run_status"] == "completed"
        assert result["data_provider"] == "Demo (Synthetic)"
        assert result["feed_mode"] == "offline"

    def test_backtest_provider_checksum_matches_direct(self):
        from app.engine.backtest import run_backtest
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "benchmark_id": "DEMO-BENCHMARK",
            "start_date": "2024-01-02",
            "end_date": "2024-02-13",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        r1 = run_backtest(spec, data_provider=DemoDataProvider())
        r2 = run_backtest(spec)
        assert r1["dataset_checksum"] == r2["dataset_checksum"]

    def test_backtest_error_result_has_provider_fields(self):
        from app.engine.backtest import run_backtest
        result = run_backtest({"template_type": "buy_and_hold"})
        assert result["run_status"] == "error"
        assert "data_provider" in result
        assert "feed_mode" in result
