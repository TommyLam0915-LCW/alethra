#!/usr/bin/env python3
"""Import validated real historical data into the isolated test database.

This script uses the formal storage entry point (MarketDataStorage.store_bars)
to persist the validated live2b_warehouse_bars_1440m.json data.

Usage:
    PYTHONPATH=/Users/tommylam0915/Documents/Qoder/2026-10-08/alethra-integration-auto/backend \
    DATABASE_URL="postgresql+psycopg://alethra_test:test_isolated_pwd_20261109@localhost:5432/alethra_test_isolated_20261109" \
    python3 scripts/import_real_data.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.market_data_storage import MarketDataStorage
from app.providers.data_provider import OHLCVBar


def main():
    # Configuration
    json_path = Path("/tmp/live2b_warehouse_bars_1440m.json")
    database_url = os.environ.get("DATABASE_URL")
    
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    
    if not json_path.exists():
        print(f"ERROR: {json_path} not found", file=sys.stderr)
        sys.exit(1)
    
    # Load validated JSON data
    print(f"Loading {json_path}...")
    with open(json_path, "r") as f:
        data = json.load(f)
    
    instrument_id = data["instrument_id"]
    interval_minutes = data["interval_minutes"]
    harvest_fingerprint = data.get("harvest_fingerprint")
    bars_raw = data["bars"]
    
    print(f"Instrument: {instrument_id}")
    print(f"Interval: {interval_minutes} minutes")
    print(f"Harvest fingerprint: {harvest_fingerprint}")
    print(f"Raw bars count: {len(bars_raw)}")
    
    # Convert to OHLCVBar objects
    bars = []
    for row in bars_raw:
        timestamp, open_price, high, low, close, volume = row
        # Convert timestamp to date string
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        
        # Convert string values to floats (JSON stores them as strings)
        bar = OHLCVBar(
            date=date_str,
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(close),
            volume=float(volume) if volume is not None else None,
        )
        bars.append(bar)
    
    print(f"Converted {len(bars)} bars to OHLCVBar objects")
    
    # Connect to database
    print(f"Connecting to {database_url.split('@')[1]}...")
    engine = create_engine(database_url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Use formal storage entry point
        storage = MarketDataStorage(db)
        
        # Store bars with proper metadata
        # data_nature: "real" (not synthetic)
        # provider_id: "kraken" (the source exchange)
        stored_count = storage.store_bars(
            instrument_id=instrument_id,
            provider_id="kraken",
            bars=bars,
            data_nature="real",
            adjustment_status=None,
            timezone="UTC",
            currency="USD",
            provider_note=f"Imported from live2b_warehouse_bars_1440m.json (harvest: {harvest_fingerprint[:16]}...)",
            provider_note_zh="從 live2b_warehouse_bars_1440m.json 匯入的真實歷史資料",
        )
        
        print(f"✓ Successfully stored {stored_count} bars")
        
        # Verify metadata
        metadata = storage.get_metadata(instrument_id, "kraken")
        if metadata:
            print(f"✓ Metadata updated:")
            print(f"  - First bar: {metadata.first_bar_date}")
            print(f"  - Last bar: {metadata.last_bar_date}")
            print(f"  - Total bars: {metadata.total_bars}")
            print(f"  - Data nature: {metadata.data_nature}")
            print(f"  - Health status: {metadata.health_status}")
        
        print("\n✓ Import complete. Real historical data is now in the isolated test database.")
        
    except Exception as e:
        print(f"ERROR: Import failed: {e}", file=sys.stderr)
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
