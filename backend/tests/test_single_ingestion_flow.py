"""
Single-ingestion regression test: verify retrieve-once flow.

The backtest engine fetches data from provider when storage is empty,
then uses stored data on subsequent runs. Persistence is handled by
the API layer, not the backtest engine.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import timedelta
import pandas as pd

from app.engine.backtest import run_backtest
from app.providers.data_provider import DataProvider


class MockDataProvider(DataProvider):
    """Mock provider that tracks all calls."""
    
    def __init__(self):
        self._provider_id = "mock"
        self._provider_name = "Mock Provider"
        self._feed_mode = "test"
        self.get_bars_call_count = 0
        self.get_bars_calls = []
        
    @property
    def provider_id(self):
        return self._provider_id
    
    @property
    def provider_name(self):
        return self._provider_name
    
    @property
    def feed_mode(self):
        return self._feed_mode
    
    def get_bars(self, symbol: str, start_date: str, end_date: str):
        self.get_bars_call_count += 1
        self.get_bars_calls.append((symbol, start_date, end_date))
        
        bars = []
        current = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        price = 100.0
        
        while current <= end:
            bars.append(Mock(
                date=current.strftime("%Y-%m-%d"),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000
            ))
            price += 1
            current += timedelta(days=1)
        
        return bars
    
    def get_available_symbols(self):
        return ["US:AAPL"]
    
    def get_data_quality(self, symbol: str):
        return {
            "data_nature": "live",
            "adjustment": "adjusted",
        }
    
    def compute_checksum(self, symbols, start_date, end_date):
        raise RuntimeError("compute_checksum should not be called")
    
    def get_status(self):
        return {"status": "ok"}
    
    def get_date_range(self, symbol: str):
        return {"start": "2024-01-01", "end": "2024-01-31"}
    
    def get_latest_bar(self, symbol: str):
        return Mock(date="2024-01-31", close=150.0)
    
    def list_assets(self):
        return ["US:AAPL"]


class TestSingleIngestionFlow:
    """Test the retrieve-once flow: fetch when empty, use storage when available."""
    
    def test_auto_mode_fetches_when_storage_empty(self):
        """
        Verify that auto mode fetches from provider when storage is empty.
        """
        provider = MockDataProvider()
        
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            # Storage returns empty (no data persisted yet)
            mock_storage.get_bars.return_value = []
            mock_storage.get_nature_map.return_value = {}
            
            mock_db_session = Mock()
            
            strategy_spec = {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.0005,
            }
            
            result = run_backtest(
                strategy_spec=strategy_spec,
                data_provider=provider,
                db_session=mock_db_session,
                data_source="auto"
            )
            
            # Should fetch from provider since storage is empty
            assert provider.get_bars_call_count == 1, \
                f"Expected 1 provider call (storage empty), got {provider.get_bars_call_count}"
            assert result["data_source_used"] == "provider"
            assert result["run_status"] == "completed"
    
    def test_auto_mode_uses_storage_when_available(self):
        """
        Verify that auto mode uses stored data when available (no provider call).
        """
        provider = MockDataProvider()
        
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            # Storage has data (simulating previous fetch + API persistence)
            # get_bars returns List[MarketDataBar], not DataFrame
            stored_bars = []
            current = pd.to_datetime("2024-01-01")
            end = pd.to_datetime("2024-01-05")
            price = 100.0
            while current <= end:
                bar = Mock()
                bar.bar_date = current.date()
                bar.open = price
                bar.high = price + 1
                bar.low = price - 1
                bar.close = price
                bar.volume = 1000
                bar.provider_id = "mock"
                stored_bars.append(bar)
                price += 1
                current += timedelta(days=1)
            
            mock_storage.get_bars.return_value = stored_bars
            mock_storage.get_nature_map.return_value = {"US:AAPL": "live"}
            
            mock_db_session = Mock()
            
            strategy_spec = {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.0005,
            }
            
            result = run_backtest(
                strategy_spec=strategy_spec,
                data_provider=provider,
                db_session=mock_db_session,
                data_source="auto"
            )
            
            # Should NOT fetch from provider since storage has data
            assert provider.get_bars_call_count == 0, \
                f"Expected 0 provider calls (data in storage), got {provider.get_bars_call_count}"
            assert result["data_source_used"] == "stored"
            assert result["run_status"] == "completed"
    
    def test_stored_mode_never_calls_provider(self):
        """
        Verify that stored mode never calls provider, even if storage is empty.
        """
        provider = MockDataProvider()
        
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            # Storage has data
            stored_bars = []
            current = pd.to_datetime("2024-01-01")
            end = pd.to_datetime("2024-01-05")
            price = 100.0
            while current <= end:
                bar = Mock()
                bar.bar_date = current.date()
                bar.open = price
                bar.high = price + 1
                bar.low = price - 1
                bar.close = price
                bar.volume = 1000
                bar.provider_id = "mock"
                stored_bars.append(bar)
                price += 1
                current += timedelta(days=1)
            
            mock_storage.get_bars.return_value = stored_bars
            mock_storage.get_nature_map.return_value = {"US:AAPL": "live"}
            
            mock_db_session = Mock()
            
            strategy_spec = {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-01",
                "end_date": "2024-01-05",
                "initial_cash": 100000,
                "commission_rate": 0.001,
                "slippage_rate": 0.0005,
            }
            
            result = run_backtest(
                strategy_spec=strategy_spec,
                data_provider=provider,
                db_session=mock_db_session,
                data_source="stored"
            )
            
            # Should NOT fetch from provider in stored mode
            assert provider.get_bars_call_count == 0, \
                f"Expected 0 provider calls (stored mode), got {provider.get_bars_call_count}"
            assert result["data_source_used"] == "stored"
            assert result["run_status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
