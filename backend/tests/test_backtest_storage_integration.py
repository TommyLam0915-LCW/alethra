"""Tests for stored market data integration with backtest engine.

Verifies the "adapter -> storage -> research/backtest" pipeline:
- data_source="stored" uses only database-stored bars
- data_source="auto" prefers stored, falls back to provider
- data_source="provider" ignores stored data
- data provenance (data_source_used) is correctly reported
"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from app.engine.backtest import run_backtest, _stored_bars_to_dataframe


class FakeStoredBar:
    """Mimics MarketDataBar model attributes used by _stored_bars_to_dataframe."""
    def __init__(self, bar_date, open, high, low, close):
        self.bar_date = bar_date
        self.open = open
        self.high = high
        self.low = low
        self.close = close


class TestStoredBarsToDataframe:
    def test_empty_list(self):
        df = _stored_bars_to_dataframe([])
        assert df.empty
        assert list(df.columns) == ["date", "open", "high", "low", "close"]

    def test_converts_stored_bars(self):
        bars = [
            FakeStoredBar(date(2024, 1, 2), 100.0, 105.0, 99.0, 103.0),
            FakeStoredBar(date(2024, 1, 3), 103.0, 107.0, 102.0, 106.0),
        ]
        df = _stored_bars_to_dataframe(bars)
        assert len(df) == 2
        assert df.iloc[0]["open"] == 100.0
        assert df.iloc[1]["close"] == 106.0
        assert df.iloc[0]["date"].year == 2024

    def test_sorted_by_date(self):
        bars = [
            FakeStoredBar(date(2024, 1, 5), 100.0, 105.0, 99.0, 103.0),
            FakeStoredBar(date(2024, 1, 2), 103.0, 107.0, 102.0, 106.0),
        ]
        df = _stored_bars_to_dataframe(bars)
        assert df.iloc[0]["date"] < df.iloc[1]["date"]


class TestBacktestWithStoredData:
    """Test run_backtest with data_source parameter."""

    def _make_strategy_spec(self, asset_id="US:AAPL"):
        return {
            "template_type": "buy_and_hold",
            "asset_ids": [asset_id],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }

    def _make_bars(self, start_day=2, count=20, base_price=150.0):
        return [
            FakeStoredBar(date(2024, 1, start_day + i), base_price + i, base_price + 2 + i, base_price - 1 + i, base_price + 1 + i)
            for i in range(count)
        ]

    def test_stored_data_source_requires_db_session(self):
        spec = self._make_strategy_spec()
        result = run_backtest(spec, data_source="stored")
        assert result["run_status"] == "error"
        assert any("database session" in e for e in result["errors"])

    def test_invalid_data_source_rejected(self):
        spec = self._make_strategy_spec()
        result = run_backtest(spec, data_source="invalid")
        assert result["run_status"] == "error"
        assert any("Invalid data_source" in e for e in result["errors"])

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_stored_data_source_with_no_data_errors(self, MockStorage):
        spec = self._make_strategy_spec()
        db = MagicMock()
        MockStorage.return_value.get_bars.return_value = []

        result = run_backtest(spec, db_session=db, data_source="stored")
        assert result["run_status"] == "error"
        assert any("No stored data" in e for e in result["errors"])

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_stored_data_source_uses_db_bars(self, MockStorage):
        bars = self._make_bars()
        spec = self._make_strategy_spec()
        db = MagicMock()
        MockStorage.return_value.get_bars.return_value = bars

        result = run_backtest(spec, db_session=db, data_source="stored")
        assert result["run_status"] == "completed"
        assert result["data_source_requested"] == "stored"
        assert result["data_source_used"] == "stored"
        assert result["data_provider"] == "Stored"
        assert result["feed_mode"] == "cached"
        assert result["dataset_checksum"] is not None
        assert len(result["transactions"]) > 0

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_auto_data_source_falls_back_to_provider(self, MockStorage):
        spec = self._make_strategy_spec(asset_id="DEMO-ALPHA")
        db = MagicMock()
        MockStorage.return_value.get_bars.return_value = []

        result = run_backtest(spec, db_session=db, data_source="auto")
        assert result["run_status"] == "completed"
        assert result["data_source_requested"] == "auto"
        assert result["data_source_used"] == "provider"

    def test_auto_without_db_uses_provider(self):
        spec = self._make_strategy_spec(asset_id="DEMO-ALPHA")
        result = run_backtest(spec, data_source="auto")
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "provider"

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_provider_data_source_ignores_stored(self, MockStorage):
        spec = self._make_strategy_spec(asset_id="DEMO-ALPHA")
        db = MagicMock()
        MockStorage.return_value.get_bars.return_value = self._make_bars(base_price=999.0)

        result = run_backtest(spec, db_session=db, data_source="provider")
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "provider"

    def test_backward_compatible_no_data_source(self):
        """Calling run_backtest without data_source or db_session still works (default behavior)."""
        spec = self._make_strategy_spec(asset_id="DEMO-ALPHA")
        result = run_backtest(spec)
        assert result["run_status"] == "completed"
        assert result["data_source_requested"] == "auto"
        assert result["data_source_used"] == "provider"

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_stored_data_warning_message(self, MockStorage):
        bars = self._make_bars()
        spec = self._make_strategy_spec()
        db = MagicMock()
        MockStorage.return_value.get_bars.return_value = bars

        result = run_backtest(spec, db_session=db, data_source="stored")
        assert result["run_status"] == "completed"
        assert any("stored/cached" in w for w in result["warnings"])

    @patch("app.services.market_data_storage.MarketDataStorage")
    def test_auto_mixed_data_source(self, MockStorage):
        """When some assets are in storage and others need provider, data_source_used='mixed'."""
        spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA", "DEMO-BETA"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        db = MagicMock()

        def mock_get_bars(instrument_id=None, **kwargs):
            if instrument_id == "DEMO-ALPHA":
                return self._make_bars()
            return []

        MockStorage.return_value.get_bars.side_effect = mock_get_bars

        result = run_backtest(spec, db_session=db, data_source="auto")
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "mixed"
        assert any("mixed" in w for w in result["warnings"])
