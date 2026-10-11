import json
import pytest
from app.engine.backtest import run_backtest, compute_config_hash, ENGINE_VERSION
from app.engine.strategies import generate_transaction
from app.engine.metrics import compute_max_drawdown, compute_metrics, build_benchmark_equity_curve, calc_time_in_market
from app.data.demo_data import get_demo_ohlcv, compute_dataset_checksum, get_available_assets


def _base_spec(**overrides):
    spec = {
        "schema_version": "1.0",
        "template_type": "buy_and_hold",
        "asset_ids": ["DEMO-ALPHA"],
        "frequency": "1d",
        "start_date": "2024-01-02",
        "end_date": "2024-02-14",
        "initial_cash": 10000,
        "signal_parameters": {},
        "entry_rule": {},
        "exit_rule": {},
        "sizing_rule": {},
        "execution_timing": {"execute_at": "next_open"},
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "benchmark_id": "DEMO-BENCHMARK",
        "currency": "USD",
    }
    spec.update(overrides)
    return spec


# --- 1. buy_and_hold basic ---
def test_buy_and_hold_basic():
    result = run_backtest(_base_spec())
    assert result["run_status"] == "completed"
    assert result["errors"] == []
    assert len(result["transactions"]) == 2
    assert result["transactions"][0]["side"] == "BUY"
    assert result["transactions"][1]["side"] == "SELL"
    assert result["metrics"]["total_trades"] == 2
    assert result["metrics"]["initial_cash"] == 10000
    assert result["metrics"]["final_equity"] > 0


# --- 2. buy_and_hold with commission ---
def test_buy_and_hold_with_commission():
    r_zero = run_backtest(_base_spec(commission_rate=0.0))
    r_comm = run_backtest(_base_spec(commission_rate=0.01))
    assert r_comm["metrics"]["total_commissions"] > 0
    assert r_comm["metrics"]["final_equity"] < r_zero["metrics"]["final_equity"]


# --- 3. buy_and_hold with slippage ---
def test_buy_and_hold_with_slippage():
    r_zero = run_backtest(_base_spec(slippage_rate=0.0))
    r_slip = run_backtest(_base_spec(slippage_rate=0.01))
    assert r_slip["metrics"]["total_slippage_cost"] > 0
    assert r_slip["metrics"]["final_equity"] < r_zero["metrics"]["final_equity"]


# --- 4. deterministic: same spec → same config_hash ---
def test_deterministic_config_hash():
    spec = _base_spec()
    r1 = run_backtest(spec)
    r2 = run_backtest(spec)
    assert r1["config_hash"] == r2["config_hash"]
    assert r1["dataset_checksum"] == r2["dataset_checksum"]
    assert r1["metrics"]["final_equity"] == r2["metrics"]["final_equity"]


# --- 5. sma_crossover basic ---
def test_sma_crossover_basic():
    spec = _base_spec(
        template_type="sma_crossover",
        asset_ids=["DEMO-BETA"],
        signal_parameters={"short_window": 5, "long_window": 10},
        commission_rate=0.001,
        slippage_rate=0.001,
    )
    result = run_backtest(spec)
    assert result["run_status"] == "completed"
    assert result["errors"] == []
    assert len(result["transactions"]) >= 2
    assert result["methodology_summary"]["template_type"] == "sma_crossover"


# --- 6. sma_crossover rejects invalid params ---
def test_sma_crossover_invalid_params():
    spec = _base_spec(
        template_type="sma_crossover",
        signal_parameters={"short_window": 10, "long_window": 5},
    )
    result = run_backtest(spec)
    assert result["run_status"] == "error"
    assert len(result["errors"]) > 0


# --- 7. sma_crossover force exit at end of data ---
def test_sma_crossover_force_exit():
    spec = _base_spec(
        template_type="sma_crossover",
        asset_ids=["DEMO-BETA"],
        signal_parameters={"short_window": 3, "long_window": 5},
    )
    result = run_backtest(spec)
    assert result["run_status"] == "completed"
    last_txn = result["transactions"][-1]
    assert last_txn["side"] == "SELL"
    assert "shares_after" in last_txn and last_txn["shares_after"] == 0


# --- 8. price_change_fixed_hold basic ---
def test_price_change_basic():
    spec = _base_spec(
        template_type="price_change_fixed_hold",
        asset_ids=["DEMO-ALPHA"],
        signal_parameters={"lookback_days": 3, "threshold_pct": 0.05, "holding_days": 5},
        commission_rate=0.001,
        slippage_rate=0.001,
    )
    result = run_backtest(spec)
    assert result["run_status"] == "completed"
    assert result["errors"] == []
    assert result["methodology_summary"]["template_type"] == "price_change_fixed_hold"


# --- 9. price_change rejects invalid params ---
def test_price_change_invalid_params():
    spec = _base_spec(
        template_type="price_change_fixed_hold",
        signal_parameters={"lookback_days": 0, "threshold_pct": 0.05, "holding_days": 5},
    )
    result = run_backtest(spec)
    assert result["run_status"] == "error"
    assert len(result["errors"]) > 0


# --- 10. transaction ledger has 16 fields ---
def test_transaction_ledger_fields():
    result = run_backtest(_base_spec())
    txn = result["transactions"][0]
    expected_fields = {
        "transaction_id", "trade_date", "signal_date", "execution_date",
        "symbol", "side", "quantity", "observed_price",
        "execution_price_before_costs", "slippage_amount", "commission_amount",
        "gross_amount", "net_cash_change", "cash_after", "shares_after", "rationale",
    }
    assert set(txn.keys()) == expected_fields


# --- 11. benchmark equity curve aligned to trading dates ---
def test_benchmark_aligned_to_trading_dates():
    result = run_backtest(_base_spec(benchmark_id="DEMO-BENCHMARK"))
    strategy_dates = [p["date"] for p in result["daily_equity_curve"]]
    benchmark_dates = [p["date"] for p in result["benchmark_equity_curve"]]
    assert benchmark_dates == strategy_dates
    assert len(benchmark_dates) == len(strategy_dates)


# --- 12. dataset checksum deterministic ---
def test_dataset_checksum_deterministic():
    cs1 = compute_dataset_checksum(["DEMO-ALPHA", "DEMO-BENCHMARK"], "2024-01-02", "2024-02-14")
    cs2 = compute_dataset_checksum(["DEMO-ALPHA", "DEMO-BENCHMARK"], "2024-01-02", "2024-02-14")
    assert cs1 == cs2
    assert len(cs1) == 64


# --- 13. warnings for zero commission/slippage ---
def test_warnings_zero_costs():
    result = run_backtest(_base_spec(commission_rate=0.0, slippage_rate=0.0))
    warning_text = " ".join(result["warnings"])
    assert "Zero commission" in warning_text
    assert "Zero slippage" in warning_text


# --- 14. unsupported asset rejected ---
def test_unsupported_asset():
    spec = _base_spec(asset_ids=["INVALID-ASSET"])
    result = run_backtest(spec)
    assert result["run_status"] == "error"
    assert any("Unsupported" in e for e in result["errors"])


# --- 15. metrics JSON serializable ---
def test_metrics_json_serializable():
    result = run_backtest(_base_spec(commission_rate=0.001, slippage_rate=0.001))
    serialized = json.dumps(result["metrics"])
    deserialized = json.loads(serialized)
    assert deserialized["initial_cash"] == result["metrics"]["initial_cash"]
    assert deserialized["total_trades"] == result["metrics"]["total_trades"]


# --- 16. max_drawdown calculation ---
def test_max_drawdown_calculation():
    equity = [100, 110, 105, 90, 95, 100]
    dd = compute_max_drawdown(equity)
    assert dd == pytest.approx(20 / 110, abs=0.001)

    assert compute_max_drawdown([100]) == 0.0
    assert compute_max_drawdown([]) == 0.0


# --- 17. benchmark Model A: uses same initial cash as base ---
def test_benchmark_uses_same_initial_cash():
    spec = _base_spec(commission_rate=0.001, slippage_rate=0.001)
    result = run_backtest(spec)
    bm_curve = result["benchmark_equity_curve"]
    assert len(bm_curve) > 0
    bm_first = bm_curve[0]["equity"]
    assert bm_first < spec["initial_cash"]


# --- 18. benchmark Model A: whole-share sizing with costs ---
def test_benchmark_whole_shares_with_costs():
    spec = _base_spec(commission_rate=0.005, slippage_rate=0.003)
    result = run_backtest(spec)
    bm_curve = result["benchmark_equity_curve"]
    strategy_curve = result["daily_equity_curve"]
    assert len(bm_curve) == len(strategy_curve)
    bm_first_equity = bm_curve[0]["equity"]
    assert bm_first_equity < spec["initial_cash"]
    assert bm_first_equity > 0


# --- 19. benchmark Model A: zero-cost benchmark matches frictionless ---
def test_benchmark_zero_costs_entry():
    spec = _base_spec(commission_rate=0.0, slippage_rate=0.0)
    result = run_backtest(spec)
    bm_curve = result["benchmark_equity_curve"]
    assert len(bm_curve) > 0
    bm_first = bm_curve[0]["equity"]
    assert bm_first == spec["initial_cash"]


# --- 20. excess return uses initial_cash as comparable base ---
def test_excess_return_comparable_base():
    spec = _base_spec(commission_rate=0.001, slippage_rate=0.001)
    result = run_backtest(spec)
    m = result["metrics"]
    assert m["benchmark_total_return_pct"] is not None
    assert m["excess_return_pct"] is not None
    expected_excess = round(m["total_return_pct"] - m["benchmark_total_return_pct"], 4)
    assert m["excess_return_pct"] == expected_excess


# --- 21. buy-and-hold initial BUY has null signal_date ---
def test_buy_and_hold_initial_buy_null_signal_date():
    result = run_backtest(_base_spec())
    buy_txn = result["transactions"][0]
    assert buy_txn["side"] == "BUY"
    assert buy_txn["signal_date"] is None
    assert buy_txn["rationale"] == "Initial allocation at first tradable session"


# --- 22. buy-and-hold SELL rationale indicates forced liquidation ---
def test_buy_and_hold_sell_forced_liquidation():
    result = run_backtest(_base_spec())
    sell_txn = result["transactions"][1]
    assert sell_txn["side"] == "SELL"
    assert "Forced end-of-period liquidation" in sell_txn["rationale"]


# --- 23. SMA signal_date strictly precedes execution_date ---
def test_sma_signal_date_precedes_execution():
    spec = _base_spec(
        template_type="sma_crossover",
        asset_ids=["DEMO-BETA"],
        signal_parameters={"short_window": 5, "long_window": 10},
        commission_rate=0.001,
        slippage_rate=0.001,
    )
    result = run_backtest(spec)
    signal_driven_txns = [
        t for t in result["transactions"]
        if "signal" in t["rationale"].lower()
    ]
    assert len(signal_driven_txns) > 0
    for t in signal_driven_txns:
        assert t["signal_date"] is not None
        assert t["signal_date"] < t["execution_date"]


# --- 24. price-change signal_date strictly precedes execution_date ---
def test_price_change_signal_date_precedes_execution():
    spec = _base_spec(
        template_type="price_change_fixed_hold",
        asset_ids=["DEMO-ALPHA"],
        signal_parameters={"lookback_days": 3, "threshold_pct": 0.05, "holding_days": 5},
        commission_rate=0.001,
        slippage_rate=0.001,
    )
    result = run_backtest(spec)
    buy_txns = [t for t in result["transactions"] if t["side"] == "BUY"]
    if buy_txns:
        for t in buy_txns:
            assert t["signal_date"] is not None
            assert t["signal_date"] < t["execution_date"]


# --- 25. annualized return unavailable for short sample ---
def test_annualized_return_unavailable_short_sample():
    result = run_backtest(_base_spec())
    ann = result["metrics"]["annualized_return_pct"]
    assert isinstance(ann, dict)
    assert ann["value"] is None
    assert "252" in ann["reason"]


# --- 26. annualized volatility unavailable for short sample ---
def test_annualized_volatility_unavailable_short_sample():
    result = run_backtest(_base_spec())
    vol = result["metrics"]["annualized_volatility_pct"]
    assert isinstance(vol, dict)
    assert vol["value"] is None
    assert "252" in vol["reason"]


# --- 27. unavailable metrics are not represented as zero ---
def test_unavailable_metrics_not_zero():
    result = run_backtest(_base_spec())
    ann = result["metrics"]["annualized_return_pct"]
    vol = result["metrics"]["annualized_volatility_pct"]
    assert isinstance(ann, dict)
    assert ann["value"] is None
    assert isinstance(vol, dict)
    assert vol["value"] is None


# --- 28. engine version is 1.1.0 ---
def test_engine_version():
    assert ENGINE_VERSION == "1.1.0"
    result = run_backtest(_base_spec())
    assert result["engine_version"] == "1.1.0"


# --- 29. trade_date always equals execution_date ---
def test_trade_date_equals_execution_date():
    spec = _base_spec(commission_rate=0.001, slippage_rate=0.001)
    result = run_backtest(spec)
    for t in result["transactions"]:
        assert t["trade_date"] == t["execution_date"]
