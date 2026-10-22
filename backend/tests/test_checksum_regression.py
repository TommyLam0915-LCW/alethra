"""
Checksum regression test: verify deterministic behavior and no provider calls.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import timedelta
import pandas as pd

from app.engine.backtest import run_backtest, _compute_stored_checksum
from app.providers.data_provider import DataProvider


class MockDataProvider(DataProvider):
    """Mock provider that tracks compute_checksum calls."""
    
    def __init__(self):
        self._provider_id = "mock"
        self._provider_name = "Mock Provider"
        self._feed_mode = "test"
        self.compute_checksum_call_count = 0
        
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
        self.compute_checksum_call_count += 1
        raise RuntimeError("compute_checksum should not be called")
    
    def get_status(self):
        return {"status": "ok"}
    
    def get_date_range(self, symbol: str):
        return {"start": "2024-01-01", "end": "2024-01-31"}
    
    def get_latest_bar(self, symbol: str):
        return Mock(date="2024-01-31", close=150.0)
    
    def list_assets(self):
        return ["US:AAPL"]


class TestChecksumRegression:
    """Test checksum behavior: deterministic, content-sensitive, no provider calls."""
    
    def test_checksum_is_deterministic(self):
        """
        Verify that the same data produces the same checksum.
        """
        # Create test data with 'date' as a column, not index
        data = {
            "US:AAPL": pd.DataFrame({
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1000, 1100, 1200],
            })
        }
        
        checksum1 = _compute_stored_checksum(data, ["US:AAPL"], "2024-01-01", "2024-01-03")
        checksum2 = _compute_stored_checksum(data, ["US:AAPL"], "2024-01-01", "2024-01-03")
        
        assert checksum1 == checksum2, "Checksum should be deterministic"
        assert checksum1 is not None, "Checksum should not be None"
    
    def test_checksum_changes_with_content(self):
        """
        Verify that different data produces different checksums.
        """
        data1 = {
            "US:AAPL": pd.DataFrame({
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1000, 1100, 1200],
            })
        }
        
        data2 = {
            "US:AAPL": pd.DataFrame({
                "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 999.0],  # Changed value
                "volume": [1000, 1100, 1200],
            })
        }
        
        checksum1 = _compute_stored_checksum(data1, ["US:AAPL"], "2024-01-01", "2024-01-03")
        checksum2 = _compute_stored_checksum(data2, ["US:AAPL"], "2024-01-01", "2024-01-03")
        
        assert checksum1 != checksum2, "Different data should produce different checksums"
    
    def test_backtest_does_not_call_provider_compute_checksum(self):
        """
        Verify that run_backtest uses _compute_stored_checksum, not provider.compute_checksum.
        """
        provider = MockDataProvider()
        
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            # Storage returns empty (will fetch from provider)
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
            
            # Should NOT call provider.compute_checksum
            assert provider.compute_checksum_call_count == 0, \
                f"Expected 0 compute_checksum calls, got {provider.compute_checksum_call_count}"
            
            # Should have a checksum in the result
            assert result.get("dataset_checksum") is not None, \
                "Result should include dataset_checksum"
            assert result["run_status"] == "completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
