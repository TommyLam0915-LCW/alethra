"""
Test request budget: ensure single ingestion path with no duplicate fetches.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import date, timedelta
import pandas as pd
from uuid import uuid4

from app.engine.backtest import run_backtest
from app.providers.data_provider import DataProvider
from app.models.market_data import MarketDataBar


class MockDataProvider(DataProvider):
    """Mock provider that tracks get_bars calls."""
    
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
        
        # Return mock bars
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
        return ["US:AAPL", "US:MSFT"]
    
    def get_data_quality(self, symbol: str):
        return {
            "data_nature": "live",
            "adjustment": "adjusted",
        }
    
    def compute_checksum(self, symbols, start_date, end_date):
        # This should NOT be called in the fixed version
        raise RuntimeError("compute_checksum should not be called - use _compute_stored_checksum instead")
    
    def get_status(self):
        return {"status": "ok"}
    
    def get_date_range(self, symbol: str):
        return {"start": "2024-01-01", "end": "2024-01-31"}
    
    def get_latest_bar(self, symbol: str):
        return Mock(date="2024-01-31", close=150.0)
    
    def list_assets(self):
        return ["US:AAPL", "US:MSFT"]


class TestRequestBudget:
    """Test that we don't make duplicate provider requests."""
    
    @pytest.fixture
    def mock_db_session(self):
        """Create a mock DB session with stored data."""
        session = Mock()
        
        # Mock MarketDataStorage - patch where it's imported
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            # Return stored bars when queried
            def mock_get_bars(instrument_id, provider_id, start_date, end_date):
                bars = []
                current = start_date
                price = 100.0
                
                while current <= end_date:
                    bar = Mock()
                    bar.bar_date = current
                    bar.open = price
                    bar.high = price + 1
                    bar.low = price - 1
                    bar.close = price
                    bar.volume = 1000
                    bar.provider_id = "mock"
                    bars.append(bar)
                    price += 1
                    current += timedelta(days=1)
                
                return bars
            
            mock_storage.get_bars.side_effect = mock_get_bars
            mock_storage.get_nature_map.return_value = {"mock": "live"}
            
            yield session
    
    def test_auto_mode_with_stored_data_no_provider_fetch(self, mock_db_session):
        """
        When data_source='auto' and data is already stored,
        should NOT fetch from provider.
        """
        provider = MockDataProvider()
        
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
        
        # Should NOT call provider.get_bars since data is stored
        assert provider.get_bars_call_count == 0, \
            f"Expected 0 provider calls, but got {provider.get_bars_call_count}"
        
        # Should complete successfully
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "stored"
        
    def test_auto_mode_with_missing_data_fetches_once(self, mock_db_session):
        """
        When data_source='auto' and some data is missing,
        should fetch from provider exactly once per missing asset.
        """
        # Make stored data return empty for US:AAPL
        with patch("app.services.market_data_storage.MarketDataStorage") as mock_storage_class:
            mock_storage = Mock()
            mock_storage_class.return_value = mock_storage
            
            def mock_get_bars(instrument_id, provider_id, start_date, end_date):
                if instrument_id == "US:AAPL":
                    return []  # No stored data
                return []
            
            mock_storage.get_bars.side_effect = mock_get_bars
            mock_storage.get_nature_map.return_value = {}
            
            provider = MockDataProvider()
            
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
            
            # Should call provider.get_bars exactly once for US:AAPL
            assert provider.get_bars_call_count == 1, \
                f"Expected 1 provider call, but got {provider.get_bars_call_count}"
            
            # Should NOT call compute_checksum (which would fetch again)
            # If it does call compute_checksum, the mock will raise RuntimeError
            
            assert result["run_status"] == "completed"
            assert result["data_source_used"] == "provider"
    
    def test_provider_mode_fetches_once(self):
        """
        When data_source='provider', should fetch from provider exactly once.
        """
        provider = MockDataProvider()
        
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
            db_session=None,
            data_source="provider"
        )
        
        # Should call provider.get_bars exactly once
        assert provider.get_bars_call_count == 1, \
            f"Expected 1 provider call, but got {provider.get_bars_call_count}"
        
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "provider"
    
    def test_stored_mode_no_provider_fetch(self, mock_db_session):
        """
        When data_source='stored', should NOT fetch from provider.
        """
        provider = MockDataProvider()
        
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
        
        # Should NOT call provider.get_bars
        assert provider.get_bars_call_count == 0, \
            f"Expected 0 provider calls, but got {provider.get_bars_call_count}"
        
        assert result["run_status"] == "completed"
        assert result["data_source_used"] == "stored"
    
    def test_checksum_computed_from_data_not_provider(self, mock_db_session):
        """
        Checksum should be computed from the data we have,
        not by calling provider.compute_checksum().
        """
        provider = MockDataProvider()
        
        strategy_spec = {
            "template_type": "buy_and_hold",
            "asset_ids": ["US:AAPL"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-05",
            "initial_cash": 100000,
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
        }
        
        # This will raise RuntimeError if provider.compute_checksum is called
        result = run_backtest(
            strategy_spec=strategy_spec,
            data_provider=provider,
            db_session=mock_db_session,
            data_source="auto"
        )
        
        # Should complete without calling compute_checksum
        assert result["run_status"] == "completed"
        assert result["dataset_checksum"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
