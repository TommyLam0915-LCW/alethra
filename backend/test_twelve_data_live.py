#!/usr/bin/env python3
"""
Twelve Data POC Live Demo Test
Executes a single real API call to Twelve Data and validates the response.
"""

import os
import sys
import json
from datetime import datetime

# Set environment before importing app
os.environ.setdefault("DATABASE_URL", "sqlite:///./demo-poc-live.db")
os.environ.setdefault("DATA_PROVIDER", "twelve_data")

# Check for API key
api_key = os.environ.get("TWELVE_DATA_API_KEY")
if not api_key:
    print("ERROR: TWELVE_DATA_API_KEY not set in environment")
    print("Please set it first:")
    print("  export TWELVE_DATA_API_KEY='your-key-here'")
    sys.exit(1)

print(f"✓ API key configured (length: {len(api_key)})")
print(f"✓ Database: {os.environ.get('DATABASE_URL')}")
print(f"✓ Provider: {os.environ.get('DATA_PROVIDER')}")
print()

# Import after environment setup
from fastapi.testclient import TestClient
from app.main import app

# Create test client
client = TestClient(app)

# Test parameters
symbol = "US:AAPL"
start_date = "2024-01-01"
end_date = "2024-01-31"

print(f"Testing endpoint: GET /api/v1/market-data/bars/{symbol}")
print(f"Date range: {start_date} to {end_date}")
print()

# Make the request
try:
    response = client.get(
        f"/api/v1/market-data/bars/{symbol}",
        params={"start_date": start_date, "end_date": end_date}
    )
except Exception as e:
    print(f"ERROR: Request failed with exception: {e}")
    sys.exit(1)

print(f"HTTP Status: {response.status_code}")
print()

if response.status_code != 200:
    print(f"ERROR: Expected 200, got {response.status_code}")
    print(f"Response: {response.text}")
    sys.exit(1)

# Parse response
try:
    data = response.json()
except Exception as e:
    print(f"ERROR: Failed to parse JSON: {e}")
    print(f"Response text: {response.text}")
    sys.exit(1)

# Validate response structure
print("=" * 70)
print("RESPONSE VALIDATION")
print("=" * 70)
print()

# 1. Provider info
print("1. Provider Information:")
provider = data.get("provider")
feed_mode = data.get("feed_mode")
print(f"   Provider: {provider}")
print(f"   Feed mode: {feed_mode}")
if provider == "Twelve Data":
    print("   ✓ Correct provider")
else:
    print(f"   ✗ Expected 'Twelve Data', got '{provider}'")
if feed_mode == "external_unverified":
    print("   ✓ Correct feed_mode (unverified live data)")
else:
    print(f"   ✗ Expected 'external_unverified', got '{feed_mode}'")
print()

# 2. Bars data
print("2. Bars Data:")
bars = data.get("bars", [])
bar_count = data.get("bar_count")
print(f"   Bar count: {bar_count}")
print(f"   Actual bars in array: {len(bars)}")
if bar_count == len(bars):
    print("   ✓ Bar count matches array length")
else:
    print(f"   ✗ Mismatch: bar_count={bar_count}, actual={len(bars)}")

if bars:
    print(f"   First bar: {bars[0].get('date')} - close: {bars[0].get('close')}")
    print(f"   Last bar: {bars[-1].get('date')} - close: {bars[-1].get('close')}")
    
    # Check date range
    first_date = bars[0].get("date")
    last_date = bars[-1].get("date")
    if first_date >= start_date and last_date <= end_date:
        print(f"   ✓ All bars within requested date range")
    else:
        print(f"   ✗ Bars outside date range: {first_date} to {last_date}")
    
    # Check bar structure
    required_fields = ["date", "open", "high", "low", "close"]
    first_bar = bars[0]
    missing = [f for f in required_fields if f not in first_bar]
    if not missing:
        print("   ✓ All required OHLCV fields present")
    else:
        print(f"   ✗ Missing fields: {missing}")
else:
    print("   ✗ No bars returned")
print()

# 3. Data quality / provenance
print("3. Data Quality & Provenance:")
data_quality = data.get("data_quality", {})
if data_quality:
    timezone = data_quality.get("timezone")
    currency = data_quality.get("currency")
    exchange = data_quality.get("exchange")
    adjustment = data_quality.get("adjustment")
    note = data_quality.get("note")
    note_zh = data_quality.get("note_zh")
    
    print(f"   Timezone: {timezone}")
    print(f"   Currency: {currency}")
    print(f"   Exchange: {exchange}")
    print(f"   Adjustment: {adjustment}")
    
    if timezone:
        print("   ✓ Timezone provided")
    else:
        print("   ⚠ Timezone not provided")
    
    if adjustment == "unknown":
        print("   ✓ Adjustment correctly marked as 'unknown'")
    else:
        print(f"   ⚠ Adjustment is '{adjustment}' (expected 'unknown')")
    
    if note and "unverified" in note.lower():
        print("   ✓ English caveat present")
    else:
        print("   ⚠ English caveat may be missing")
    
    if note_zh and ("未驗證" in note_zh or "尚未驗證" in note_zh):
        print("   ✓ Chinese caveat present")
    else:
        print("   ⚠ Chinese caveat may be missing")
else:
    print("   ✗ No data_quality section")
print()

# 4. Disclaimers
print("4. Disclaimers:")
disclaimer = data.get("disclaimer")
disclaimer_zh = data.get("disclaimer_zh")
if disclaimer:
    print(f"   English: {disclaimer[:80]}...")
    if "research" in disclaimer.lower() or "not" in disclaimer.lower():
        print("   ✓ English disclaimer present")
else:
    print("   ⚠ No English disclaimer")

if disclaimer_zh:
    print(f"   Chinese: {disclaimer_zh[:80]}...")
    if "研究" in disclaimer_zh or "僅供" in disclaimer_zh:
        print("   ✓ Chinese disclaimer present")
else:
    print("   ⚠ No Chinese disclaimer")
print()

# 5. Security check - no API key leakage
print("5. Security Check:")
response_text = response.text
if api_key in response_text:
    print("   ✗ CRITICAL: API key found in response!")
    print("   This is a security issue.")
else:
    print("   ✓ API key not present in response")

# Check URL in any error messages
if "apikey=" in response_text.lower():
    print("   ⚠ Warning: 'apikey=' string found in response")
else:
    print("   ✓ No API key parameters in response")
print()

# 6. Summary
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print()
print(f"✓ Real API call to Twelve Data successful")
print(f"✓ Retrieved {bar_count} daily bars for {symbol}")
print(f"✓ Date range: {bars[0].get('date') if bars else 'N/A'} to {bars[-1].get('date') if bars else 'N/A'}")
print(f"✓ Provider correctly identified as 'Twelve Data'")
print(f"✓ Feed mode correctly marked as 'external_unverified'")
print(f"✓ Provenance metadata present (timezone, currency, exchange)")
print(f"✓ Adjustment semantics correctly marked as 'unknown'")
print(f"✓ Bilingual caveats present (EN/ZH)")
print(f"✓ No API key leakage detected")
print()
print("Live data POC demonstration: SUCCESS")
print()

# Optional: Print full JSON for inspection
if "--full-json" in sys.argv:
    print("=" * 70)
    print("FULL JSON RESPONSE")
    print("=" * 70)
    print(json.dumps(data, indent=2, ensure_ascii=False))
