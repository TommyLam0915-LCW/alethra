import hashlib
import json
import uuid
from datetime import datetime, date
from typing import Dict, List, Any, Optional

import pandas as pd
from sqlalchemy.orm import Session

from app.data.demo_data import get_demo_ohlcv, compute_dataset_checksum, get_available_assets
from app.engine.strategies import (
    run_buy_and_hold,
    run_sma_crossover,
    run_price_change_fixed_hold,
)
from app.engine.metrics import compute_metrics, build_benchmark_equity_curve, calc_time_in_market

ENGINE_VERSION = "1.1.0"


def compute_config_hash(strategy_spec: Dict[str, Any]) -> str:
    canonical = json.dumps(strategy_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _provider_bars_to_dataframe(bars: List[Any]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    rows = []
    for bar in bars:
        rows.append({
            "date": datetime.strptime(bar.date, "%Y-%m-%d"),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _stored_bars_to_dataframe(stored_bars: List[Any]) -> pd.DataFrame:
    """Convert MarketDataBar model instances to DataFrame format.

    Stored bars have bar_date (date object) instead of date (string).
    """
    if not stored_bars:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    rows = []
    for bar in stored_bars:
        rows.append({
            "date": datetime.combine(bar.bar_date, datetime.min.time()),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _load_stored_bars(
    db: Session,
    asset_ids: List[str],
    start_date: str,
    end_date: str,
    provider_id: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """Load bars from database storage.

    Returns a dict mapping asset_id to DataFrame.
    Only includes assets that have stored data.
    """
    from app.services.market_data_storage import MarketDataStorage

    storage = MarketDataStorage(db)
    start_date_obj = date.fromisoformat(start_date)
    end_date_obj = date.fromisoformat(end_date)

    result = {}
    for asset_id in asset_ids:
        stored_bars = storage.get_bars(
            instrument_id=asset_id,
            provider_id=provider_id,
            start_date=start_date_obj,
            end_date=end_date_obj,
        )
        if stored_bars:
            result[asset_id] = _stored_bars_to_dataframe(stored_bars)

    return result


def _fetch_ohlcv_via_provider(
    provider: Any,
    asset_ids: List[str],
    start_date: str,
    end_date: str,
) -> Dict[str, pd.DataFrame]:
    result = {}
    for asset_id in asset_ids:
        bars = provider.get_bars(asset_id, start_date, end_date)
        if bars:
            result[asset_id] = _provider_bars_to_dataframe(bars)
    return result


def run_backtest(
    strategy_spec: Dict[str, Any],
    engine_version: str = ENGINE_VERSION,
    data_provider: Any = None,
    db_session: Optional[Session] = None,
    data_source: str = "auto",
) -> Dict[str, Any]:
    errors = []
    warnings = []

    template_type = strategy_spec.get("template_type")
    if not template_type:
        errors.append("Missing template_type")
        return _error_result(errors)

    asset_ids = strategy_spec.get("asset_ids", [])
    if not asset_ids:
        errors.append("asset_ids is required")
        return _error_result(errors)

    start_date = strategy_spec.get("start_date")
    end_date = strategy_spec.get("end_date")
    if not start_date or not end_date:
        errors.append("start_date and end_date are required")
        return _error_result(errors)
    if start_date >= end_date:
        errors.append("start_date must be before end_date")
        return _error_result(errors)

    initial_cash = strategy_spec.get("initial_cash", 0)
    if initial_cash <= 0:
        errors.append("initial_cash must be > 0")
        return _error_result(errors)

    commission_rate = strategy_spec.get("commission_rate", 0.0)
    slippage_rate = strategy_spec.get("slippage_rate", 0.0)

    all_asset_ids = list(set(asset_ids + ([strategy_spec["benchmark_id"]] if strategy_spec.get("benchmark_id") else [])))

    if data_source not in ("auto", "stored", "provider"):
        errors.append(f"Invalid data_source: {data_source}. Use 'auto', 'stored', or 'provider'.")
        return _error_result(errors)

    ohlcv_data: Dict[str, pd.DataFrame] = {}
    provider_name: str = ""
    feed_mode: str = ""
    dataset_checksum: Optional[str] = None
    data_source_used: str = ""

    if data_source == "stored":
        if db_session is None:
            errors.append("data_source='stored' requires a database session (db_session)")
            return _error_result(errors)
        ohlcv_data = _load_stored_bars(db_session, all_asset_ids, start_date, end_date)
        for aid in asset_ids:
            if aid not in ohlcv_data or ohlcv_data[aid].empty:
                errors.append(f"No stored data for {aid} in range {start_date} to {end_date}. Fetch from provider first.")
                return _error_result(errors)
        provider_name = "Stored"
        feed_mode = "cached"
        data_source_used = "stored"
        dataset_checksum = _compute_stored_checksum(ohlcv_data, all_asset_ids, start_date, end_date)

    elif data_source == "provider" or data_source == "auto":
        stored_data: Dict[str, pd.DataFrame] = {}
        if data_source == "auto" and db_session is not None:
            stored_data = _load_stored_bars(db_session, all_asset_ids, start_date, end_date)

        missing_from_storage = [aid for aid in all_asset_ids if aid not in stored_data or stored_data[aid].empty]

        provider_data: Dict[str, pd.DataFrame] = {}
        if missing_from_storage or data_source == "provider":
            assets_to_fetch = missing_from_storage if data_source == "auto" else all_asset_ids
            if data_provider is not None:
                available = set(data_provider.get_available_symbols())
                for aid in asset_ids:
                    if aid not in available:
                        errors.append(f"Unsupported asset: {aid}")
                        return _error_result(errors)
                provider_data = _fetch_ohlcv_via_provider(data_provider, assets_to_fetch, start_date, end_date)
                dataset_checksum = data_provider.compute_checksum(all_asset_ids, start_date, end_date)
                provider_name = data_provider.provider_name
                feed_mode = data_provider.feed_mode
            else:
                for aid in assets_to_fetch:
                    if aid not in get_available_assets():
                        errors.append(f"Unsupported asset: {aid}")
                        return _error_result(errors)
                provider_data = get_demo_ohlcv(assets_to_fetch, start_date, end_date)
                dataset_checksum = compute_dataset_checksum(assets_to_fetch, start_date, end_date)
                provider_name = "Demo (Synthetic)"
                feed_mode = "offline"

        ohlcv_data = {**stored_data, **provider_data}

        if data_source == "auto":
            from_storage = [aid for aid in all_asset_ids if aid in stored_data and aid not in provider_data]
            from_provider = [aid for aid in all_asset_ids if aid in provider_data]
            if from_storage and from_provider:
                data_source_used = "mixed"
            elif from_storage:
                data_source_used = "stored"
            else:
                data_source_used = "provider"
        else:
            data_source_used = "provider"

        if dataset_checksum is None:
            dataset_checksum = _compute_stored_checksum(ohlcv_data, all_asset_ids, start_date, end_date)

    for aid in asset_ids:
        if aid not in ohlcv_data or ohlcv_data[aid].empty:
            errors.append(f"No data available for {aid} in range {start_date} to {end_date}")
            return _error_result(errors)

    config_hash = compute_config_hash(strategy_spec)

    if data_source_used == "stored":
        warnings.append(f"Data source: stored/cached data ({provider_name}).")
    elif data_source_used == "mixed":
        warnings.append(f"Data source: mixed — some assets from storage, some from {provider_name}.")
    elif feed_mode == "offline":
        warnings.append(f"Data source: {provider_name} — not live market data.")
    else:
        warnings.append(f"Data source: {provider_name} ({feed_mode}).")
    warnings.append("Historical simulation for research purposes only.")
    if commission_rate == 0:
        warnings.append("Zero commission selected — real trading incurs costs.")
    if slippage_rate == 0:
        warnings.append("Zero slippage selected — real execution incurs slippage.")

    symbol = asset_ids[0]
    ohlcv = ohlcv_data[symbol]

    transactions: List[Dict[str, Any]] = []
    strategy_warnings: List[str] = []

    if template_type == "buy_and_hold":
        transactions, strategy_warnings = run_buy_and_hold(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
        )
    elif template_type == "sma_crossover":
        sig_params = strategy_spec.get("signal_parameters", {})
        short_window = sig_params.get("short_window")
        long_window = sig_params.get("long_window")
        if short_window is None or long_window is None:
            errors.append("sma_crossover requires signal_parameters.short_window and .long_window")
            return _error_result(errors)
        if not isinstance(short_window, int) or not isinstance(long_window, int):
            errors.append("SMA windows must be integers")
            return _error_result(errors)
        if short_window < 2 or short_window > 60:
            errors.append("short_window must be between 2 and 60")
            return _error_result(errors)
        if long_window < 3 or long_window > 200:
            errors.append("long_window must be between 3 and 200")
            return _error_result(errors)
        if short_window >= long_window:
            errors.append("short_window must be less than long_window")
            return _error_result(errors)

        transactions, strategy_warnings = run_sma_crossover(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            short_window=short_window,
            long_window=long_window,
        )
    elif template_type == "price_change_fixed_hold":
        sig_params = strategy_spec.get("signal_parameters", {})
        lookback_days = sig_params.get("lookback_days")
        threshold_pct = sig_params.get("threshold_pct")
        holding_days = sig_params.get("holding_days")
        if lookback_days is None or threshold_pct is None or holding_days is None:
            errors.append("price_change_fixed_hold requires signal_parameters.lookback_days, .threshold_pct, .holding_days")
            return _error_result(errors)
        if not isinstance(lookback_days, int) or lookback_days < 1 or lookback_days > 60:
            errors.append("lookback_days must be integer 1-60")
            return _error_result(errors)
        if not isinstance(threshold_pct, (int, float)) or threshold_pct < 0.01 or threshold_pct > 0.50:
            errors.append("threshold_pct must be 0.01-0.50")
            return _error_result(errors)
        if not isinstance(holding_days, int) or holding_days < 1 or holding_days > 120:
            errors.append("holding_days must be integer 1-120")
            return _error_result(errors)

        transactions, strategy_warnings = run_price_change_fixed_hold(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            lookback_days=lookback_days,
            threshold_pct=threshold_pct,
            holding_days=holding_days,
        )
    else:
        errors.append(f"Unsupported template_type: {template_type}")
        return _error_result(errors)

    warnings.extend(strategy_warnings)

    if not transactions:
        warnings.append("No trades generated by this strategy.")

    daily_equity = _build_daily_equity(transactions, initial_cash, ohlcv)

    trading_days = len(ohlcv)
    trading_dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]

    benchmark_equity = []
    benchmark_id = strategy_spec.get("benchmark_id")
    if benchmark_id and benchmark_id in ohlcv_data:
        benchmark_ohlcv = ohlcv_data[benchmark_id]
        benchmark_ohlcv = benchmark_ohlcv[benchmark_ohlcv["date"].dt.strftime("%Y-%m-%d").isin(trading_dates)]
        benchmark_equity = build_benchmark_equity_curve(benchmark_ohlcv, initial_cash, commission_rate, slippage_rate)
    elif benchmark_id:
        warnings.append(f"Benchmark {benchmark_id} unavailable for this date range.")
    final_equity = daily_equity[-1] if daily_equity else initial_cash

    time_in_market_pct = calc_time_in_market(transactions, trading_dates)

    metrics = compute_metrics(
        initial_cash=initial_cash,
        final_equity=final_equity,
        transactions=transactions,
        daily_equity=daily_equity,
        benchmark_equity=benchmark_equity,
        trading_days=trading_days,
        time_in_market_pct=time_in_market_pct,
    )

    if trading_days < 30:
        warnings.append(f"Short sample period: only {trading_days} trading days.")

    daily_equity_curve = [
        {"date": row["date"].strftime("%Y-%m-%d"), "equity": eq}
        for row, eq in zip(
            [ohlcv.iloc[i] for i in range(len(ohlcv))],
            daily_equity,
        )
    ] if daily_equity else []

    benchmark_equity_curve = []
    if benchmark_equity and benchmark_id and benchmark_id in ohlcv_data:
        bm_ohlcv_aligned = ohlcv_data[benchmark_id]
        bm_ohlcv_aligned = bm_ohlcv_aligned[bm_ohlcv_aligned["date"].dt.strftime("%Y-%m-%d").isin(trading_dates)]
        benchmark_equity_curve = [
            {"date": bm_ohlcv_aligned.iloc[i]["date"].strftime("%Y-%m-%d"), "equity": eq}
            for i, eq in enumerate(benchmark_equity)
        ]

    methodology_summary = _build_methodology_summary(strategy_spec, template_type)

    return {
        "run_status": "completed",
        "strategy_spec_snapshot": strategy_spec,
        "dataset_snapshot_id": str(uuid.uuid4()),
        "dataset_checksum": dataset_checksum,
        "engine_version": engine_version,
        "config_hash": config_hash,
        "data_provider": provider_name,
        "feed_mode": feed_mode,
        "data_source_requested": data_source,
        "data_source_used": data_source_used,
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": initial_cash,
        "final_equity": round(final_equity, 4),
        "transactions": transactions,
        "daily_equity_curve": daily_equity_curve,
        "benchmark_equity_curve": benchmark_equity_curve,
        "metrics": metrics,
        "warnings": warnings,
        "errors": errors,
        "methodology_summary": methodology_summary,
    }


def _build_daily_equity(
    transactions: List[Dict[str, Any]],
    initial_cash: float,
    ohlcv: pd.DataFrame,
) -> List[float]:
    if ohlcv.empty:
        return []

    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    closes = {row["date"].strftime("%Y-%m-%d"): row["close"] for _, row in ohlcv.iterrows()}

    cash = initial_cash
    shares = 0
    equity = []

    txn_by_date: Dict[str, List[Dict]] = {}
    for t in transactions:
        d = t["execution_date"]
        if d not in txn_by_date:
            txn_by_date[d] = []
        txn_by_date[d].append(t)

    for date_str in dates:
        if date_str in txn_by_date:
            for t in txn_by_date[date_str]:
                cash += t["net_cash_change"]
                shares = t["shares_after"]

        symbol = transactions[0]["symbol"] if transactions else None
        if symbol:
            price = closes.get(date_str, 0)
            portfolio_value = cash + shares * price
        else:
            portfolio_value = cash

        equity.append(round(portfolio_value, 4))

    return equity


def _build_methodology_summary(strategy_spec: Dict[str, Any], template_type: str) -> Dict[str, Any]:
    summary = {
        "template_type": template_type,
        "execution_convention": {
            "buy": "execution_price = next_day_open * (1 + slippage_rate)",
            "sell": "execution_price = close * (1 - slippage_rate) for buy-and-hold exit; next_day_open * (1 - slippage_rate) for SMA cross-down",
            "commission": "gross_amount * commission_rate",
            "signal_timing": "Signal computed from data available at day t close; execution at day t+1 open (except final liquidation)",
        },
        "position_sizing": "Integer whole shares only; no fractional shares in Phase 1",
        "constraints": [
            "Long-only (no short selling)",
            "No leverage",
            "No negative cash",
            "Demo data only — no live market data",
            "No dividends, corporate actions, or survivorship bias adjustment",
        ],
        "parameters": {
            "commission_rate": strategy_spec.get("commission_rate", 0),
            "slippage_rate": strategy_spec.get("slippage_rate", 0),
            "initial_cash": strategy_spec.get("initial_cash", 0),
            "signal_parameters": strategy_spec.get("signal_parameters", {}),
        },
    }

    if template_type == "sma_crossover":
        sp = strategy_spec.get("signal_parameters", {})
        summary["strategy_rules"] = [
            f"Compute SMA({sp.get('short_window')}) and SMA({sp.get('long_window')}) from prior completed daily closes",
            "Cross-up (short > long) generates buy signal",
            "Cross-down (short < long) generates sell signal",
            "Use all available cash on buy; sell all shares on sell",
            "No duplicate buys while holding; no sells when not holding",
        ]
    elif template_type == "price_change_fixed_hold":
        sp = strategy_spec.get("signal_parameters", {})
        summary["strategy_rules"] = [
            f"If price change over {sp.get('lookback_days')} days <= -{sp.get('threshold_pct')*100}%, generate buy signal",
            f"Hold exactly {sp.get('holding_days')} trading sessions after execution",
            "Liquidate at holding-period exit session close",
            "Ignore new entry signals while already holding",
        ]
    elif template_type == "buy_and_hold":
        summary["strategy_rules"] = [
            "Buy maximum affordable whole shares at first valid open",
            "Hold to final valid data date",
            "Liquidate at final valid close",
        ]

    return summary


def _compute_stored_checksum(
    ohlcv_data: Dict[str, pd.DataFrame],
    asset_ids: List[str],
    start_date: str,
    end_date: str,
) -> str:
    """Compute a deterministic checksum for stored data (same format as demo checksum)."""
    canonical_parts = []
    for aid in sorted(asset_ids):
        if aid in ohlcv_data and not ohlcv_data[aid].empty:
            df = ohlcv_data[aid]
            for _, row in df.iterrows():
                canonical_parts.append(
                    f"{aid}:{row['date'].strftime('%Y-%m-%d')}:{row['open']}:{row['high']}:{row['low']}:{row['close']}"
                )
    canonical = "|".join(canonical_parts)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _error_result(errors: List[str]) -> Dict[str, Any]:
    return {
        "run_status": "error",
        "strategy_spec_snapshot": {},
        "dataset_snapshot_id": None,
        "dataset_checksum": None,
        "engine_version": ENGINE_VERSION,
        "config_hash": None,
        "data_provider": None,
        "feed_mode": None,
        "data_source_requested": None,
        "data_source_used": None,
        "start_date": None,
        "end_date": None,
        "initial_cash": 0,
        "final_equity": 0,
        "transactions": [],
        "daily_equity_curve": [],
        "benchmark_equity_curve": [],
        "metrics": {},
        "warnings": [],
        "errors": errors,
        "methodology_summary": {},
    }
