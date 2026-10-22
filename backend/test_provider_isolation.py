"""Focused test for provider read isolation."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mock sklearn to avoid import errors
sys.modules['sklearn'] = type(sys)('sklearn')
sys.modules['sklearn.linear_model'] = type(sys)('sklearn.linear_model')
sys.modules['sklearn.ensemble'] = type(sys)('sklearn.ensemble')
sys.modules['sklearn.preprocessing'] = type(sys)('sklearn.preprocessing')
sys.modules['sklearn.model_selection'] = type(sys)('sklearn.model_selection')
sys.modules['sklearn.metrics'] = type(sys)('sklearn.metrics')
sys.modules['sklearn.base'] = type(sys)('sklearn.base')

from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models.market_data import MarketDataBar
from app.api.simulations import _load_stored_ohlcv, _get_stored_trade_dates

# Create in-memory test database
engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

def test_provider_isolation():
    """Test that provider_id filtering works correctly."""
    db = Session()
    
    # Insert test data: same date, two providers
    test_date = date(2024, 1, 30)
    
    # Twelve Data bar
    td_bar = MarketDataBar(
        instrument_id='US:AAPL',
        bar_timestamp=test_date,
        bar_date=test_date,
        open=188.0,
        high=189.0,
        low=187.5,
        close=188.5,
        volume=50000000,
        provider_id='twelvedata',
        source='twelvedata'
    )
    
    # Demo bar (same date)
    demo_bar = MarketDataBar(
        instrument_id='US:AAPL',
        bar_timestamp=test_date,
        bar_date=test_date,
        open=190.0,
        high=191.0,
        low=189.0,
        close=190.5,
        volume=1000000,
        provider_id='demo',
        source='demo'
    )
    
    db.add(td_bar)
    db.add(demo_bar)
    db.commit()
    
    # Test 1: Load with twelvedata filter
    df_td = _load_stored_ohlcv('US:AAPL', '2024-01-30', '2024-01-30', db, provider_id='twelvedata')
    assert len(df_td) == 1, f"Expected 1 row for twelvedata, got {len(df_td)}"
    assert df_td.iloc[0]['close'] == 188.5, f"Expected close=188.5, got {df_td.iloc[0]['close']}"
    print("✓ Test 1 passed: twelvedata filter returns only twelvedata data")
    
    # Test 2: Load with demo filter
    df_demo = _load_stored_ohlcv('US:AAPL', '2024-01-30', '2024-01-30', db, provider_id='demo')
    assert len(df_demo) == 1, f"Expected 1 row for demo, got {len(df_demo)}"
    assert df_demo.iloc[0]['close'] == 190.5, f"Expected close=190.5, got {df_demo.iloc[0]['close']}"
    print("✓ Test 2 passed: demo filter returns only demo data")
    
    # Test 3: Load without filter (should get both)
    df_all = _load_stored_ohlcv('US:AAPL', '2024-01-30', '2024-01-30', db, provider_id=None)
    assert len(df_all) == 2, f"Expected 2 rows without filter, got {len(df_all)}"
    print("✓ Test 3 passed: no filter returns all providers")
    
    # Test 4: Get trade dates with twelvedata filter
    dates_td = _get_stored_trade_dates('US:AAPL', '2024-01-30', '2024-01-30', db, provider_id='twelvedata')
    assert len(dates_td) == 1, f"Expected 1 date for twelvedata, got {len(dates_td)}"
    print("✓ Test 4 passed: trade dates filtered by provider")
    
    # Test 5: Demo-only date should not extend twelvedata replay
    demo_only_date = date(2024, 1, 31)
    demo_only_bar = MarketDataBar(
        instrument_id='US:AAPL',
        bar_timestamp=demo_only_date,
        bar_date=demo_only_date,
        open=191.0,
        high=192.0,
        low=190.5,
        close=191.5,
        volume=1000000,
        provider_id='demo',
        source='demo'
    )
    db.add(demo_only_bar)
    db.commit()
    
    dates_td_filtered = _get_stored_trade_dates('US:AAPL', '2024-01-30', '2024-01-31', db, provider_id='twelvedata')
    assert len(dates_td_filtered) == 1, f"Expected 1 date (2024-01-30) for twelvedata, got {len(dates_td_filtered)}"
    assert '2024-01-31' not in dates_td_filtered, "Demo-only date should not appear in twelvedata filter"
    print("✓ Test 5 passed: demo-only date does not extend twelvedata replay")
    
    db.close()
    print("\n✅ All provider isolation tests passed!")

if __name__ == '__main__':
    test_provider_isolation()
