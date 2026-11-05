#!/usr/bin/env python3
"""Comprehensive batch testing for all 16 rules across 3 markets.

Tests: MB-14 (trend), MB-16 (mean-reversion), MB-17 Phase 3 (volatility), Phase 4 (market-making)
Markets: Crypto (3), Stock (3), Forex (3) = 9 instruments
Timeframes: Daily, 4h, 1h
Commission: Low, Medium, High per market
"""
import json
import sys
from datetime import datetime, timezone
from typing import Dict, List, Any
import httpx

BASE_URL = "http://127.0.0.1:8000"
AUTH_HEADER = {"X-Test-Auth": "localhost"}

# All test configurations
TEST_CONFIGS = [
    # Crypto (3 instruments × 3 timeframes × 3 commissions = 27 tests)
    {"id": "CRYPTO:KRAKEN:BTC-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    {"id": "CRYPTO:KRAKEN:ETH-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    {"id": "CRYPTO:KRAKEN:SOL-USD", "market": "crypto", "commission": 0.002, "slippage": 0.001},
    # Stock (3 instruments × 3 timeframes × 3 commissions = 27 tests)
    {"id": "US:AAPL", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    {"id": "US:TSLA", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    {"id": "US:SPY", "market": "stock", "commission": 0.001, "slippage": 0.0005},
    # Forex (3 instruments × 3 timeframes × 3 commissions = 27 tests)
    {"id": "FOREX:EUR/USD", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
    {"id": "FOREX:GBP/USD", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
    {"id": "FOREX:USD/JPY", "market": "forex", "commission": 0.0001, "slippage": 0.0001},
]

INTERVALS = [1440, 240, 60]  # Daily, 4h, 1h
COMMISSION_MULTIPLIERS = [0.5, 1.0, 2.0]  # Low, Medium, High


def test_config(instrument: Dict[str, Any], interval: int, comm_mult: float) -> Dict[str, Any]:
    """Test a single configuration."""
    commission = instrument["commission"] * comm_mult
    slippage = instrument["slippage"] * comm_mult
    
    payload = {
        "instrument_id": instrument["id"],
        "interval_minutes": interval,
        "folds": 3,
        "starting_cash": 10000,
        "notional_per_trade": 1000,
        "commission_rate": commission,
        "slippage_rate": slippage,
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
                "error": response.text[:200],
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
            "profit_factor": float(wf.get("profit_factor", 0)),
            "max_drawdown_pct": float(wf.get("max_drawdown_pct", 0)),
            "data_nature": data.get("dataset_provenance", {}).get("data_nature", "unknown"),
            "provider_id": data.get("dataset_provenance", {}).get("provider_id", "unknown"),
            "integrity_ok": data.get("dataset_provenance", {}).get("integrity_ok", False),
            "rules_tested": len(data.get("rules", [])),
            "grid_items": len(data.get("in_sample_grid", [])),
        }
    
    except Exception as exc:
        return {
            "status": "ERROR",
            "error": str(exc)[:200],
        }


def main():
    """Run comprehensive batch tests."""
    started_at = datetime.now(timezone.utc).isoformat()
    results = {
        "batch_id": "MB-17-Comprehensive-All-Markets-All-Rules",
        "started_at": started_at,
        "description": "16 rules × 9 instruments × 3 timeframes × 3 commissions = 243 tests",
        "calls": [],
    }
    
    total_tests = len(TEST_CONFIGS) * len(INTERVALS) * len(COMMISSION_MULTIPLIERS)
    current = 0
    
    print("=" * 80)
    print("MB-17 Comprehensive Batch Testing: All Markets, All Rules")
    print("=" * 80)
    print(f"Started at: {started_at}")
    print(f"Total tests: {total_tests}")
    print(f"  - {len(TEST_CONFIGS)} instruments")
    print(f"  - {len(INTERVALS)} timeframes")
    print(f"  - {len(COMMISSION_MULTIPLIERS)} commission levels")
    print(f"  - 16 rules (trend + momentum + mean-reversion + volatility + market-making)")
    print()
    
    for instrument in TEST_CONFIGS:
        for interval in INTERVALS:
            for comm_mult in COMMISSION_MULTIPLIERS:
                current += 1
                comm_str = f"{comm_mult:.1f}x"
                print(f"[{current}/{total_tests}] {instrument['id']} @ {interval}min, comm={comm_str}...")
                
                result = test_config(instrument, interval, comm_mult)
                
                if result["status"] == "OK":
                    print(f"  ✓ Bars: {result['bars']} | Trades: {result['trades']} | "
                          f"Net PnL: {result['net_pnl']:.2f} | Win Rate: {result['win_rate']*100:.1f}% | "
                          f"SNR: {result['snr']}")
                    print(f"    Rules: {result['rules_tested']} | Grid: {result['grid_items']} | "
                          f"Data: {result['data_nature']} | Provider: {result['provider_id']}")
                else:
                    print(f"  ✗ ERROR: {result.get('error', 'Unknown error')}")
                
                # Store result
                results["calls"].append({
                    "instrument_id": instrument["id"],
                    "market": instrument["market"],
                    "interval_minutes": interval,
                    "commission_multiplier": comm_mult,
                    "commission_rate": instrument["commission"] * comm_mult,
                    "slippage_rate": instrument["slippage"] * comm_mult,
                    **result,
                })
                
                print()
    
    # Save results
    output_file = "mb17_comprehensive_batch_results.json"
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
    print(f"  Successful: {ok_count}/{total_tests} ({ok_count/total_tests*100:.1f}%)")
    print(f"  Errors: {error_count}/{total_tests}")
    print(f"  Total trades: {total_trades}")
    
    # Market breakdown
    print()
    print("By Market:")
    for market in ["crypto", "stock", "forex"]:
        market_calls = [c for c in results["calls"] if c.get("market") == market and c["status"] == "OK"]
        if market_calls:
            market_trades = sum(c.get("trades", 0) for c in market_calls)
            market_pnl = sum(c.get("net_pnl", 0) for c in market_calls)
            print(f"  {market.upper()}: {len(market_calls)} tests, {market_trades} trades, Net PnL: {market_pnl:.2f}")
    
    # Timeframe breakdown
    print()
    print("By Timeframe:")
    for interval in INTERVALS:
        interval_calls = [c for c in results["calls"] if c.get("interval_minutes") == interval and c["status"] == "OK"]
        if interval_calls:
            interval_trades = sum(c.get("trades", 0) for c in interval_calls)
            interval_pnl = sum(c.get("net_pnl", 0) for c in interval_calls)
            print(f"  {interval}min: {len(interval_calls)} tests, {interval_trades} trades, Net PnL: {interval_pnl:.2f}")
    
    # G0 verification
    print()
    print("G0 Verification (Data Lineage):")
    for market in ["crypto", "stock", "forex"]:
        market_calls = [c for c in results["calls"] if c.get("market") == market and c["status"] == "OK"]
        if market_calls:
            data_nature = market_calls[0].get("data_nature", "unknown")
            provider = market_calls[0].get("provider_id", "unknown")
            integrity = market_calls[0].get("integrity_ok", False)
            status = "✓ PASS" if data_nature == "live" else "✗ FAIL"
            print(f"  {market.upper()}: {status} (data_nature={data_nature}, provider={provider}, integrity={integrity})")
    
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
