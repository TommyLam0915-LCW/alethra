#!/usr/bin/env python3
"""Multi-market batch testing for MB-17 research.

Tests crypto, stock, and forex instruments across multiple timeframes.
"""
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any

import httpx

BASE_URL = "http://127.0.0.1:8000"
AUTH_HEADER = {"X-Test-Auth": "localhost"}

# Test configurations
INSTRUMENTS = [
    # Crypto
    {"id": "CRYPTO:KRAKEN:BTC-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    {"id": "CRYPTO:KRAKEN:ETH-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    {"id": "CRYPTO:KRAKEN:SOL-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    # Stock
    {"id": "US:AAPL", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    {"id": "US:TSLA", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    {"id": "US:SPY", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    # Forex
    {"id": "FOREX:EUR/USD", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
    {"id": "FOREX:GBP/USD", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
    {"id": "FOREX:USD/JPY", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
]

INTERVALS = [1440, 240, 60]  # Daily, 4h, 1h


def test_instrument(instrument: Dict[str, Any], interval: int) -> Dict[str, Any]:
    """Test a single instrument/interval combination."""
    payload = {
        "instrument_id": instrument["id"],
        "interval_minutes": interval,
        "folds": 3,
        "starting_cash": 10000,
        "notional_per_trade": 1000,
        "commission_rate": instrument["commission"],
        "slippage_rate": instrument["slippage"],
        "include_trades": False,
    }
    
    try:
        response = httpx.post(
            f"{BASE_URL}/api/v1/strategy-lab/research",
            json=payload,
            headers=AUTH_HEADER,
            timeout=30.0,
        )
        
        if response.status_code != 200:
            return {
                "status": "ERROR",
                "http_code": response.status_code,
                "error": response.text,
            }
        
        data = response.json()
        bars = data.get("bars", {}).get("count", 0)
        wf = data.get("walk_forward", {}).get("out_of_sample", {})
        
        return {
            "status": "OK",
            "bars": bars,
            "trades": wf.get("trades", 0),
            "win_rate": float(wf.get("win_rate", 0)),
            "net_pnl": float(wf.get("net_pnl", 0)),
            "snr": wf.get("mean_over_stdev_per_trade", "N/A"),
            "data_nature": data.get("dataset_provenance", {}).get("data_nature", "unknown"),
            "provider_id": data.get("dataset_provenance", {}).get("provider_id", "unknown"),
            "integrity_ok": data.get("dataset_provenance", {}).get("integrity_ok", False),
            "response": data,
        }
    
    except Exception as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
        }


def main():
    """Run batch tests."""
    started_at = datetime.now(timezone.utc).isoformat()
    results = {
        "batch_id": "MB-17-MultiMarket",
        "started_at": started_at,
        "calls": [],
    }
    
    total_tests = len(INSTRUMENTS) * len(INTERVALS)
    current = 0
    
    print("=" * 80)
    print("MB-17 Multi-Market Batch Testing")
    print("=" * 80)
    print(f"Started at: {started_at}")
    print(f"Total tests: {total_tests} ({len(INSTRUMENTS)} instruments × {len(INTERVALS)} intervals)")
    print()
    
    for instrument in INSTRUMENTS:
        for interval in INTERVALS:
            current += 1
            print(f"[{current}/{total_tests}] Testing {instrument['id']} @ {interval}min...")
            
            result = test_instrument(instrument, interval)
            
            if result["status"] == "OK":
                print(f"  ✓ Bars: {result['bars']} | Trades: {result['trades']} | "
                      f"Net PnL: {result['net_pnl']:.2f} | Win Rate: {result['win_rate']*100:.1f}% | "
                      f"SNR: {result['snr']}")
                print(f"    Data: {result['data_nature']} | Provider: {result['provider_id']} | "
                      f"Integrity: {result['integrity_ok']}")
            else:
                print(f"  ✗ ERROR: {result.get('error', 'Unknown error')}")
            
            # Store result
            results["calls"].append({
                "instrument_id": instrument["id"],
                "market": instrument["market"],
                "interval_minutes": interval,
                "commission_rate": instrument["commission"],
                "slippage_rate": instrument["slippage"],
                **result,
            })
            
            print()
    
    # Save results
    output_file = "mb17_multimarket_batch_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    completed_at = datetime.now(timezone.utc).isoformat()
    results["completed_at"] = completed_at
    
    print("=" * 80)
    print("Batch execution complete.")
    print(f"Results saved to: {output_file}")
    print(f"Completed at: {completed_at}")
    print("=" * 80)
    
    # Summary statistics
    ok_count = sum(1 for call in results["calls"] if call["status"] == "OK")
    error_count = sum(1 for call in results["calls"] if call["status"] == "ERROR")
    total_trades = sum(call.get("trades", 0) for call in results["calls"] if call["status"] == "OK")
    
    print()
    print("Summary:")
    print(f"  Successful: {ok_count}/{total_tests}")
    print(f"  Errors: {error_count}/{total_tests}")
    print(f"  Total trades: {total_trades}")
    
    # Market breakdown
    for market in ["crypto", "stock", "forex"]:
        market_calls = [c for c in results["calls"] if c.get("market") == market and c["status"] == "OK"]
        if market_calls:
            market_trades = sum(c.get("trades", 0) for c in market_calls)
            print(f"  {market.upper()}: {len(market_calls)} tests, {market_trades} trades")
    
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
