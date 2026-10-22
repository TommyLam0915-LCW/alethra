"""
Provider read isolation tests for manual replay.

Verifies that manual replay sessions correctly isolate data by provider_id
and do not mix data from different providers.
"""
import pytest
from datetime import date
from unittest.mock import Mock
import pandas as pd


class TestProviderReadIsolation:
    """Test provider isolation in stored data reads."""

    def test_load_stored_ohlcv_filters_by_provider(self):
        """Verify _load_stored_ohlcv filters by provider_id when specified."""
        # Import here to avoid module-level import errors
        from app.api.simulations import _load_stored_ohlcv
        
        # Setup mock database session
        mock_db = Mock()
        
        # Create mock bars for two providers
        twelvedata_bar = Mock()
        twelvedata_bar.bar_date = date(2024, 1, 30)
        twelvedata_bar.open = 190.94
        twelvedata_bar.high = 191.8
        twelvedata_bar.low = 187.47
        twelvedata_bar.close = 188.04
        twelvedata_bar.volume = 55859400
        twelvedata_bar.provider_id = 'twelvedata'
        
        demo_bar = Mock()
        demo_bar.bar_date = date(2024, 1, 30)
        demo_bar.open = 200.176
        demo_bar.high = 200.975
        demo_bar.low = 196.463
        demo_bar.close = 197.484
        demo_bar.volume = 1437296
        demo_bar.provider_id = 'demo'
        
        # Mock query chain
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        
        # Test with provider_id='twelvedata'
        mock_order_by.all.return_value = [twelvedata_bar]
        result = _load_stored_ohlcv('US:AAPL', '2024-01-30', '2024-01-30', mock_db, provider_id='twelvedata')
        
        # Verify filter was called with provider_id
        assert mock_filter.filter.called
        filter_calls = mock_filter.filter.call_args_list
        provider_filter_found = any(
            'provider_id' in str(call) for call in filter_calls
        )
        assert provider_filter_found, "provider_id filter should be applied"
        
        # Verify result contains only twelvedata
        assert len(result) == 1
        assert result.iloc[0]['open'] == 190.94

    def test_load_stored_ohlcv_without_provider_loads_all(self):
        """Verify _load_stored_ohlcv loads all providers when provider_id is None."""
        from app.api.simulations import _load_stored_ohlcv
        
        mock_db = Mock()
        
        # Create mock bars for two providers
        twelvedata_bar = Mock()
        twelvedata_bar.bar_date = date(2024, 1, 30)
        twelvedata_bar.open = 190.94
        twelvedata_bar.high = 191.8
        twelvedata_bar.low = 187.47
        twelvedata_bar.close = 188.04
        twelvedata_bar.volume = 55859400
        
        demo_bar = Mock()
        demo_bar.bar_date = date(2024, 1, 30)
        demo_bar.open = 200.176
        demo_bar.high = 200.975
        demo_bar.low = 196.463
        demo_bar.close = 197.484
        demo_bar.volume = 1437296
        
        # Mock query chain
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.all.return_value = [twelvedata_bar, demo_bar]
        
        # Test without provider_id
        result = _load_stored_ohlcv('US:AAPL', '2024-01-30', '2024-01-30', mock_db, provider_id=None)
        
        # Verify provider_id filter was NOT applied
        filter_calls = mock_filter.filter.call_args_list
        provider_filter_found = any(
            'provider_id' in str(call) for call in filter_calls
        )
        assert not provider_filter_found, "provider_id filter should NOT be applied when None"
        
        # Verify result contains both providers (duplicate dates)
        assert len(result) == 2

    def test_get_stored_trade_dates_filters_by_provider(self):
        """Verify _get_stored_trade_dates filters by provider_id when specified."""
        from app.api.simulations import _get_stored_trade_dates
        
        mock_db = Mock()
        
        # Create mock bar dates
        mock_bar1 = Mock()
        mock_bar1.bar_date = date(2024, 1, 29)
        
        mock_bar2 = Mock()
        mock_bar2.bar_date = date(2024, 1, 30)
        
        # Mock query chain
        mock_query = Mock()
        mock_filter = Mock()
        mock_order_by = Mock()
        mock_distinct = Mock()
        
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_filter
        mock_filter.filter.return_value = mock_filter
        mock_filter.order_by.return_value = mock_order_by
        mock_order_by.distinct.return_value = mock_distinct
        mock_distinct.all.return_value = [mock_bar1, mock_bar2]
        
        # Test with provider_id='twelvedata'
        result = _get_stored_trade_dates('US:AAPL', '2024-01-29', '2024-01-30', mock_db, provider_id='twelvedata')
        
        # Verify filter was called with provider_id
        filter_calls = mock_filter.filter.call_args_list
        provider_filter_found = any(
            'provider_id' in str(call) for call in filter_calls
        )
        assert provider_filter_found, "provider_id filter should be applied"
        
        # Verify result
        assert len(result) == 2
        assert '2024-01-29' in result
        assert '2024-01-30' in result


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
