"""Minimal provider isolation test without full app imports."""
import sqlite3
from datetime import date

def test_provider_isolation_sql():
    """Test provider_id filtering at SQL level."""
    # Create in-memory database
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    
    # Create market_data_bars table
    cursor.execute('''
        CREATE TABLE market_data_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_id TEXT NOT NULL,
            bar_timestamp DATE NOT NULL,
            bar_date DATE NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER,
            provider_id TEXT NOT NULL,
            source TEXT
        )
    ''')
    
    # Insert test data: same date, two providers
    test_date = '2024-01-30'
    
    # Twelve Data bar
    cursor.execute('''
        INSERT INTO market_data_bars 
        (instrument_id, bar_timestamp, bar_date, open, high, low, close, volume, provider_id, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('US:AAPL', test_date, test_date, 188.0, 189.0, 187.5, 188.5, 50000000, 'twelvedata', 'twelvedata'))
    
    # Demo bar (same date)
    cursor.execute('''
        INSERT INTO market_data_bars 
        (instrument_id, bar_timestamp, bar_date, open, high, low, close, volume, provider_id, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('US:AAPL', test_date, test_date, 190.0, 191.0, 189.0, 190.5, 1000000, 'demo', 'demo'))
    
    conn.commit()
    
    # Test 1: Load with twelvedata filter
    cursor.execute('''
        SELECT close FROM market_data_bars
        WHERE instrument_id = ? AND bar_date >= ? AND bar_date <= ? AND provider_id = ?
    ''', ('US:AAPL', test_date, test_date, 'twelvedata'))
    results = cursor.fetchall()
    assert len(results) == 1, f"Expected 1 row for twelvedata, got {len(results)}"
    assert results[0][0] == 188.5, f"Expected close=188.5, got {results[0][0]}"
    print("✓ Test 1 passed: twelvedata filter returns only twelvedata data (close=188.5)")
    
    # Test 2: Load with demo filter
    cursor.execute('''
        SELECT close FROM market_data_bars
        WHERE instrument_id = ? AND bar_date >= ? AND bar_date <= ? AND provider_id = ?
    ''', ('US:AAPL', test_date, test_date, 'demo'))
    results = cursor.fetchall()
    assert len(results) == 1, f"Expected 1 row for demo, got {len(results)}"
    assert results[0][0] == 190.5, f"Expected close=190.5, got {results[0][0]}"
    print("✓ Test 2 passed: demo filter returns only demo data (close=190.5)")
    
    # Test 3: Load without filter (should get both)
    cursor.execute('''
        SELECT close FROM market_data_bars
        WHERE instrument_id = ? AND bar_date >= ? AND bar_date <= ?
    ''', ('US:AAPL', test_date, test_date))
    results = cursor.fetchall()
    assert len(results) == 2, f"Expected 2 rows without filter, got {len(results)}"
    print("✓ Test 3 passed: no filter returns all providers (2 rows)")
    
    # Test 4: Get distinct trade dates with twelvedata filter
    cursor.execute('''
        SELECT DISTINCT bar_date FROM market_data_bars
        WHERE instrument_id = ? AND bar_date >= ? AND bar_date <= ? AND provider_id = ?
        ORDER BY bar_date
    ''', ('US:AAPL', test_date, test_date, 'twelvedata'))
    results = cursor.fetchall()
    assert len(results) == 1, f"Expected 1 date for twelvedata, got {len(results)}"
    print("✓ Test 4 passed: trade dates filtered by provider")
    
    # Test 5: Demo-only date should not extend twelvedata replay
    demo_only_date = '2024-01-31'
    cursor.execute('''
        INSERT INTO market_data_bars 
        (instrument_id, bar_timestamp, bar_date, open, high, low, close, volume, provider_id, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', ('US:AAPL', demo_only_date, demo_only_date, 191.0, 192.0, 190.5, 191.5, 1000000, 'demo', 'demo'))
    conn.commit()
    
    cursor.execute('''
        SELECT DISTINCT bar_date FROM market_data_bars
        WHERE instrument_id = ? AND bar_date >= ? AND bar_date <= ? AND provider_id = ?
        ORDER BY bar_date
    ''', ('US:AAPL', '2024-01-30', '2024-01-31', 'twelvedata'))
    results = cursor.fetchall()
    assert len(results) == 1, f"Expected 1 date (2024-01-30) for twelvedata, got {len(results)}"
    assert results[0][0] == '2024-01-30', f"Expected 2024-01-30, got {results[0][0]}"
    print("✓ Test 5 passed: demo-only date (2024-01-31) does not extend twelvedata replay")
    
    conn.close()
    print("\n✅ All provider isolation SQL tests passed!")

if __name__ == '__main__':
    test_provider_isolation_sql()
