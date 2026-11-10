import hashlib
import json
import uuid
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
from sqlalchemy.orm import Session

from app.data.demo_data import get_demo_ohlcv, compute_dataset_checksum, get_available_assets
from app.engine.strategies import (
    run_buy_and_hold,
    run_sma_crossover,
    run_price_change_fixed_hold,
)
from app.engine.metrics import compute_metrics, build_benchmark_equity_curve, calc_time_in_market
from app.engine.attribution import annotate_and_attribute
from app.engine import annualization
from app.engine import exit_rules
from app.engine.fill_math import quantity_precision_for
from app.engine.intraday import INTRADAY_TIMEFRAMES

ENGINE_VERSION = "1.1.0"

KNOWN_DATA_NATURES = frozenset({"synthetic", "live", "delayed", "mixed", "unknown"})

# Signal parameters each daily template reads, with the bounds the dispatcher
# enforces. Both the run path and the read-only preflight endpoint consume this
# table, so the published catalogue cannot promise bounds the engine refuses.
SIGNAL_PARAMETER_RULES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "buy_and_hold": {},
    "sma_crossover": {
        "short_window": {
            "type": "integer",
            "min": 2,
            "max": 60,
            "type_message": "SMA windows must be integers",
            "range_message": "short_window must be between 2 and 60",
        },
        "long_window": {
            "type": "integer",
            "min": 3,
            "max": 200,
            "type_message": "SMA windows must be integers",
            "range_message": "long_window must be between 3 and 200",
        },
    },
    "price_change_fixed_hold": {
        "lookback_days": {
            "type": "integer",
            "min": 1,
            "max": 60,
            "type_message": "lookback_days must be integer 1-60",
            "range_message": "lookback_days must be integer 1-60",
        },
        "threshold_pct": {
            "type": "number",
            "min": 0.01,
            "max": 0.50,
            "type_message": "threshold_pct must be 0.01-0.50",
            "range_message": "threshold_pct must be 0.01-0.50",
        },
        "holding_days": {
            "type": "integer",
            "min": 1,
            "max": 120,
            "type_message": "holding_days must be integer 1-120",
            "range_message": "holding_days must be integer 1-120",
        },
    },
}

SIGNAL_PARAMETER_REQUIREMENTS: Dict[str, str] = {
    "sma_crossover": "sma_crossover requires signal_parameters.short_window and .long_window",
    "price_change_fixed_hold": (
        "price_change_fixed_hold requires signal_parameters.lookback_days, "
        ".threshold_pct, .holding_days"
    ),
}


def validate_signal_parameters(template_type: str, signal_parameters: Any) -> List[str]:
    """Errors the dispatcher reports for a template's signal parameters.

    Unknown templates are the caller's concern; this function only validates the
    parameters of a template it knows.
    """
    rules = SIGNAL_PARAMETER_RULES.get(template_type, {})
    if not isinstance(signal_parameters, dict):
        return ["signal_parameters must be an object"]
    missing = [name for name in rules if signal_parameters.get(name) is None]
    if missing:
        return [
            SIGNAL_PARAMETER_REQUIREMENTS.get(
                template_type,
                f"{template_type} requires signal_parameters.{', .'.join(missing)}",
            )
        ]
    errors = []
    for name, rule in rules.items():
        value = signal_parameters.get(name)
        type_ok = isinstance(value, int) if rule["type"] == "integer" else isinstance(value, (int, float))
        if not type_ok:
            errors.append(rule["type_message"])
            continue
        if value < rule["min"] or value > rule["max"]:
            errors.append(rule["range_message"])
    if (
        template_type == "sma_crossover"
        and not errors
        and signal_parameters["short_window"] >= signal_parameters["long_window"]
    ):
        errors.append("short_window must be less than long_window")
    return errors


def _coerce_nature(value: Any) -> str:
    """Collapse anything that is not a recognized nature token to "unknown"."""
    return value if isinstance(value, str) and value in KNOWN_DATA_NATURES else "unknown"


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
) -> Tuple[
    Dict[str, pd.DataFrame],
    Dict[str, str],
    Dict[str, List[str]],
    Dict[str, List[str]],
]:
    """Load bars from database storage with per-asset data nature.

    Returns (data, natures, ambiguous, providers): a dict mapping asset_id to
    DataFrame (only assets that have stored data from exactly one provider), a
    dict mapping asset_id to the resolved data_nature
    ("synthetic"/"live"/"delayed"/"mixed"/"unknown") of the bars actually
    loaded — from MarketDataMetadata for the backing providers. Synthetic-
    sourced bars must never be presented as real market data. ``providers``
    maps each loaded asset to its single backing provider id, so a run can
    record which stored dataset it priced off.

    ``ambiguous`` maps asset_id to the sorted provider ids that each hold rows
    in the range. Rows are unique per (instrument, timestamp, provider), so two
    providers mean two prices for one date; merging them would hand a strategy a
    calendar that never existed. The forward replay already refuses this at
    session creation and asks for one provider — binding is done the same way
    here, by passing ``provider_id``. An ambiguous asset is withheld from
    ``data`` so a merged series cannot reach a template unnoticed.
    """
    from app.services.market_data_storage import MarketDataStorage

    storage = MarketDataStorage(db)
    start_date_obj = date.fromisoformat(start_date)
    end_date_obj = date.fromisoformat(end_date)

    result = {}
    ambiguous: Dict[str, List[str]] = {}
    loaded_provider_ids: Dict[str, set] = {}
    for asset_id in asset_ids:
        stored_bars = storage.get_bars(
            instrument_id=asset_id,
            provider_id=provider_id,
            start_date=start_date_obj,
            end_date=end_date_obj,
        )
        if not stored_bars:
            continue
        providers = {
            getattr(bar, "provider_id", None) for bar in stored_bars
        } - {None}
        if len(providers) > 1:
            ambiguous[asset_id] = sorted(providers)
            continue
        result[asset_id] = _stored_bars_to_dataframe(stored_bars)
        loaded_provider_ids[asset_id] = providers

    nature_map = storage.get_nature_map(list(loaded_provider_ids.keys()))
    natures = {
        asset_id: MarketDataStorage.resolve_nature(
            nature_map, asset_id, pids
        )
        for asset_id, pids in loaded_provider_ids.items()
    }
    providers = {
        asset_id: sorted(pids) for asset_id, pids in loaded_provider_ids.items()
    }
    return result, natures, ambiguous, providers


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


def _ambiguous_dataset_error(
    ambiguous: Dict[str, List[str]], asset_ids: List[str]
) -> Optional[str]:
    """One sentence explaining why a stored range cannot be traded as one series."""
    hits = [aid for aid in asset_ids if aid in ambiguous]
    if not hits:
        return None
    detail = "; ".join(f"{aid}: {', '.join(ambiguous[aid])}" for aid in hits)
    return (
        f"Stored data for {detail} comes from more than one provider, so this "
        "range holds more than one price per date. A backtest must price off one "
        "dataset — pass a single provider_id, exactly as the forward replay "
        "requires when a session is created."
    )


def run_backtest(
    strategy_spec: Dict[str, Any],
    engine_version: str = ENGINE_VERSION,
    data_provider: Any = None,
    db_session: Optional[Session] = None,
    data_source: str = "auto",
    provider_id: Optional[str] = None,
) -> Dict[str, Any]:
    errors = []
    warnings = []

    template_type = strategy_spec.get("template_type")
    if not template_type:
        errors.append("Missing template_type")
        return _error_result(errors)

    # The batch backtester runs the daily templates and reports daily-annualized
    # metrics keyed on one bar per calendar date. Feeding it an intraday spec would
    # silently misrepresent 5-minute bars as daily ones, so refuse it here and point
    # at the per-bar intraday forward replay, which is the path intraday strategies
    # actually execute through. Additive: a spec with no timeframe (or "1d") is
    # untouched and runs exactly as before.
    timeframe = str(strategy_spec.get("timeframe", "1d"))
    if timeframe in INTRADAY_TIMEFRAMES:
        return _error_result([
            f"Batch backtest does not execute '{timeframe}' intraday specs: the daily "
            "templates and daily-annualized metrics would misrepresent intraday bars. "
            "Run intraday strategies through the per-bar forward replay (simulation "
            "session create + advance), which binds one stored intraday dataset."
        ])

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
    stored_data: Dict[str, pd.DataFrame] = {}
    stored_natures: Dict[str, str] = {}
    stored_provider_ids: Dict[str, List[str]] = {}
    provider_data: Dict[str, pd.DataFrame] = {}
    provider_name: str = ""
    feed_mode: str = ""
    dataset_checksum: Optional[str] = None
    data_source_used: str = ""

    if data_source == "stored":
        if db_session is None:
            errors.append("data_source='stored' requires a database session (db_session)")
            return _error_result(errors)
        ohlcv_data, stored_natures, stored_ambiguous, stored_provider_ids = _load_stored_bars(
            db_session, all_asset_ids, start_date, end_date, provider_id=provider_id
        )
        stored_data = ohlcv_data
        refusal = _ambiguous_dataset_error(stored_ambiguous, all_asset_ids)
        if refusal:
            return _error_result([refusal])
        for aid in asset_ids:
            if aid not in ohlcv_data or ohlcv_data[aid].empty:
                errors.append(f"No stored data for {aid} in range {start_date} to {end_date}. Fetch from provider first.")
                return _error_result(errors)
        provider_name = "Stored"
        feed_mode = "cached"
        data_source_used = "stored"
        dataset_checksum = _compute_stored_checksum(ohlcv_data, all_asset_ids, start_date, end_date)

    elif data_source == "provider" or data_source == "auto":
        stored_ambiguous: Dict[str, List[str]] = {}
        if data_source == "auto" and db_session is not None:
            stored_data, stored_natures, stored_ambiguous, stored_provider_ids = _load_stored_bars(
                db_session, all_asset_ids, start_date, end_date, provider_id=provider_id
            )
            # Refuse before any fallback feed is consulted: an asset the user
            # bound to two providers must not be quietly re-priced from a
            # different dataset, and must not be averaged into one.
            refusal = _ambiguous_dataset_error(stored_ambiguous, all_asset_ids)
            if refusal:
                return _error_result([refusal])

        missing_from_storage = [aid for aid in all_asset_ids if aid not in stored_data or stored_data[aid].empty]

        if missing_from_storage or data_source == "provider":
            assets_to_fetch = missing_from_storage if data_source == "auto" else all_asset_ids
            if data_provider is not None:
                available = set(data_provider.get_available_symbols())
                for aid in asset_ids:
                    if aid not in available:
                        errors.append(f"Unsupported asset: {aid}")
                        return _error_result(errors)
                provider_data = _fetch_ohlcv_via_provider(data_provider, assets_to_fetch, start_date, end_date)
                provider_name = data_provider.provider_name
                feed_mode = data_provider.feed_mode
            else:
                for aid in assets_to_fetch:
                    if aid not in get_available_assets():
                        errors.append(f"Unsupported asset: {aid}")
                        return _error_result(errors)
                provider_data = get_demo_ohlcv(assets_to_fetch, start_date, end_date)
                provider_name = "Demo (Synthetic)"
                feed_mode = "offline"

        ohlcv_data = {**stored_data, **provider_data}
        
        # Compute checksum from the data we already have, not by fetching again
        dataset_checksum = _compute_stored_checksum(ohlcv_data, all_asset_ids, start_date, end_date)

        if data_source == "auto":
            from_storage = [aid for aid in all_asset_ids if aid in stored_data and aid not in provider_data]
            from_provider = [aid for aid in all_asset_ids if aid in provider_data]
            if from_storage and from_provider:
                data_source_used = "mixed"
            elif from_storage:
                data_source_used = "stored"
                # Nothing was fetched, so provider_name is still unset here.
                # Name the stored dataset instead of leaving the provenance
                # warning with empty parentheses.
                stored_names = sorted({
                    pid for pids in stored_provider_ids.values() for pid in pids
                })
                provider_name = ", ".join(stored_names) if stored_names else "Stored"
                feed_mode = "cached"
            else:
                data_source_used = "provider"
        else:
            data_source_used = "provider"

    for aid in asset_ids:
        if aid not in ohlcv_data or ohlcv_data[aid].empty:
            errors.append(f"No data available for {aid} in range {start_date} to {end_date}")
            return _error_result(errors)

    data_nature: Dict[str, str] = {}
    for aid in asset_ids:
        if aid in provider_data:
            if data_provider is not None:
                quality_fn = getattr(data_provider, "get_data_quality", None)
                data_nature[aid] = (
                    _coerce_nature(quality_fn(aid).get("data_nature"))
                    if quality_fn is not None
                    else "unknown"
                )
            else:
                # Built-in demo fallback serves deterministic synthetic data.
                data_nature[aid] = "synthetic"
        else:
            data_nature[aid] = _coerce_nature(stored_natures.get(aid))

    # What the run actually priced off. The checksum identifies the series, but
    # it cannot say whose series it was: the same instrument can hold a 140-row
    # stored fixture and a 752-day provider universe, and 140 rows that include
    # exchange holidays are not 133 sessions. Recording source, provider,
    # coverage and nature with the run makes that difference reconcilable
    # afterwards instead of leaving two counts that look contradictory.
    dataset_coverage: Dict[str, Dict[str, Any]] = {}
    for aid in asset_ids:
        df = ohlcv_data.get(aid)
        if df is None or df.empty:
            continue
        provider_ids = stored_provider_ids.get(aid) or []
        if aid in stored_data and not stored_data[aid].empty:
            source = "stored"
            provider_id = provider_ids[0] if len(provider_ids) == 1 else None
        else:
            source = "provider"
            provider_id = (
                getattr(data_provider, "provider_id", None)
                if data_provider is not None
                else "demo"
            )
        dataset_coverage[aid] = {
            "source": source,
            "provider_id": provider_id,
            "provider_ids": provider_ids,
            "data_nature": data_nature.get(aid, "unknown"),
            "bar_count": int(len(df)),
            "first_bar_date": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "last_bar_date": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        }

    config_hash = compute_config_hash(strategy_spec)

    if data_source_used == "stored":
        warnings.append(f"Data source: stored/cached data ({provider_name}).")
    elif data_source_used == "mixed":
        warnings.append(f"Data source: mixed — some assets from storage, some from {provider_name}.")
    elif feed_mode == "offline":
        warnings.append(f"Data source: {provider_name} — not live market data.")
    else:
        warnings.append(f"Data source: {provider_name} ({feed_mode}).")
    synthetic_assets = sorted(a for a, n in data_nature.items() if n == "synthetic")
    if synthetic_assets:
        warnings.append(
            "Synthetic data used for: " + ", ".join(synthetic_assets)
            + " — results are not based on real market prices."
        )
    warnings.append("Historical simulation for research purposes only.")
    if commission_rate == 0:
        warnings.append("Zero commission selected — real trading incurs costs.")
    if slippage_rate == 0:
        warnings.append("Zero slippage selected — real execution incurs slippage.")

    symbol = asset_ids[0]
    ohlcv = ohlcv_data[symbol]
    # Share markets trade whole units; a registered crypto pair trades at its
    # own lot decimals, so $10,000 can hold 0.11 BTC instead of being refused.
    quantity_precision = quantity_precision_for(symbol)

    transactions: List[Dict[str, Any]] = []
    strategy_warnings: List[str] = []

    # One reading of the declared plan for execution and for the journal: if the
    # engine traded a different stop than the one attribution reports, the R
    # multiples in the risk journal would describe a strategy nobody ran.
    risk_plan = exit_rules.plan_from_spec(strategy_spec)

    if template_type == "buy_and_hold":
        transactions, strategy_warnings = run_buy_and_hold(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            risk_plan=risk_plan,
            quantity_precision=quantity_precision,
        )
    elif template_type == "sma_crossover":
        sig_params = strategy_spec.get("signal_parameters", {})
        signal_errors = validate_signal_parameters(template_type, sig_params)
        if signal_errors:
            errors.extend(signal_errors)
            return _error_result(errors)
        short_window = sig_params["short_window"]
        long_window = sig_params["long_window"]

        transactions, strategy_warnings = run_sma_crossover(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            short_window=short_window,
            long_window=long_window,
            risk_plan=risk_plan,
            quantity_precision=quantity_precision,
        )
    elif template_type == "price_change_fixed_hold":
        sig_params = strategy_spec.get("signal_parameters", {})
        signal_errors = validate_signal_parameters(template_type, sig_params)
        if signal_errors:
            errors.extend(signal_errors)
            return _error_result(errors)
        lookback_days = sig_params["lookback_days"]
        threshold_pct = sig_params["threshold_pct"]
        holding_days = sig_params["holding_days"]

        transactions, strategy_warnings = run_price_change_fixed_hold(
            symbol=symbol,
            ohlcv=ohlcv,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_rate=slippage_rate,
            lookback_days=lookback_days,
            threshold_pct=threshold_pct,
            holding_days=holding_days,
            risk_plan=risk_plan,
            quantity_precision=quantity_precision,
        )
    else:
        errors.append(f"Unsupported template_type: {template_type}")
        return _error_result(errors)

    warnings.extend(strategy_warnings)

    if not transactions:
        warnings.append("No trades generated by this strategy.")

    attribution = annotate_and_attribute(
        transactions,
        strategy_spec=strategy_spec,
        config_hash=config_hash,
        actor="strategy",
    )

    daily_equity = _build_daily_equity(transactions, initial_cash, ohlcv)
    daily_accounting = _build_daily_accounting(transactions, initial_cash, ohlcv)

    trading_days = len(ohlcv)
    trading_dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]

    # Keyed on the asset the run traded and the dates it traded on, so the sample
    # length and the observation frequency come from this dataset rather than from
    # an equity-trading-day constant that every market inherited.
    basis = annualization.basis_for_series(asset_ids[0], trading_dates)

    benchmark_equity = []
    benchmark_id = strategy_spec.get("benchmark_id")
    if benchmark_id and benchmark_id in ohlcv_data:
        benchmark_ohlcv = ohlcv_data[benchmark_id]
        benchmark_ohlcv = benchmark_ohlcv[benchmark_ohlcv["date"].dt.strftime("%Y-%m-%d").isin(trading_dates)]
        benchmark_equity = build_benchmark_equity_curve(
            benchmark_ohlcv,
            initial_cash,
            commission_rate,
            slippage_rate,
            quantity_precision_for(benchmark_id),
        )
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
        attribution=attribution,
        trading_dates=trading_dates,
        basis=basis,
    )

    if not basis.available:
        warnings.append(f"Annualized metrics are unavailable for this run: {basis.reason}")
    elif basis.elapsed_years is not None and basis.elapsed_years < annualization.MIN_YEARS_FOR_ANNUALIZATION:
        warnings.append(
            f"Sample spans {round(basis.elapsed_years, 4)} years, below the "
            f"{annualization.MIN_YEARS_FOR_ANNUALIZATION}-year minimum under "
            f"{basis.method_version}, so annualized return and volatility are not displayed."
        )

    if trading_days < 30:
        warnings.append(f"Short sample period: only {trading_days} trading days.")

    cross_check = metrics.get("pnl_cross_check") or {}
    if cross_check.get("consistent") is False:
        warnings.append(
            "Accounting cross-check failed: equity P&L "
            f"{cross_check['equity_net_pnl']} vs FIFO realised P&L "
            f"{cross_check['realized_net_pnl']} (difference {cross_check['difference']}). "
            "These run metrics are not reconciled."
        )

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

    methodology_summary = _build_methodology_summary(
        strategy_spec,
        template_type,
        data_nature=data_nature,
        provider_name=provider_name,
        feed_mode=feed_mode,
    )

    return {
        "run_status": "completed",
        "strategy_spec_snapshot": strategy_spec,
        "dataset_snapshot_id": str(uuid.uuid4()),
        "dataset_checksum": dataset_checksum,
        "dataset_coverage": dataset_coverage,
        "bars_used": trading_days,
        "engine_version": engine_version,
        "config_hash": config_hash,
        "data_provider": provider_name,
        "feed_mode": feed_mode,
        "data_source_requested": data_source,
        "data_source_used": data_source_used,
        "data_nature": data_nature,
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": initial_cash,
        "final_equity": round(final_equity, 4),
        "transactions": transactions,
        "round_trips": attribution["closed_trades"],
        "daily_equity_curve": daily_equity_curve,
        "daily_accounting": daily_accounting,
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


def _build_daily_accounting(
    transactions: List[Dict[str, Any]],
    initial_cash: float,
    ohlcv: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """Per-day breakdown: cash, shares, close price, position value, equity."""
    if ohlcv.empty:
        return []

    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    closes = {row["date"].strftime("%Y-%m-%d"): row["close"] for _, row in ohlcv.iterrows()}

    cash = initial_cash
    shares = 0

    txn_by_date: Dict[str, List[Dict]] = {}
    for t in transactions:
        d = t["execution_date"]
        if d not in txn_by_date:
            txn_by_date[d] = []
        txn_by_date[d].append(t)

    accounting: List[Dict[str, Any]] = []
    for date_str in dates:
        day_transactions = []
        if date_str in txn_by_date:
            for t in txn_by_date[date_str]:
                cash += t["net_cash_change"]
                shares = t["shares_after"]
                day_transactions.append({
                    "side": t["side"],
                    "quantity": t.get("quantity", 0),
                    "execution_price": t.get("execution_price_before_costs", 0),
                    "commission": t.get("commission_amount", 0),
                    "net_cash_change": t["net_cash_change"],
                })

        close_price = closes.get(date_str, 0)
        position_value = shares * close_price
        total_equity = cash + position_value

        entry: Dict[str, Any] = {
            "date": date_str,
            "cash": round(cash, 4),
            "shares": shares,
            "close_price": round(close_price, 4),
            "position_value": round(position_value, 4),
            "equity": round(total_equity, 4),
        }
        if day_transactions:
            entry["transactions"] = day_transactions
        accounting.append(entry)

    return accounting


def _build_methodology_summary(
    strategy_spec: Dict[str, Any],
    template_type: str,
    *,
    data_nature: Optional[Dict[str, str]] = None,
    provider_name: Optional[str] = None,
    feed_mode: Optional[str] = None,
) -> Dict[str, Any]:
    natures = sorted({n for n in (data_nature or {}).values() if n})
    if not natures:
        data_constraint = "Data provenance unresolved for this run — do not read these results as market-based."
    elif natures == ["synthetic"]:
        data_constraint = "Synthetic demo bars only — no real market data in this run."
    else:
        per_asset = ", ".join(f"{aid}={nat}" for aid, nat in sorted((data_nature or {}).items()))
        data_constraint = (
            f"Data nature per asset: {per_asset} (provider: {provider_name}, feed mode: {feed_mode}). "
            "Any asset marked synthetic is not real market data."
        )

    summary = {
        "template_type": template_type,
        "execution_convention": {
            "buy": "execution_price = next_session_open * (1 + slippage_rate) for every signal-driven entry; buy-and-hold enters at the first session's open",
            "sell": (
                "execution_price = observed_exit_price * (1 - slippage_rate), where observed_exit_price "
                "comes from the exit reason: min(session_open, stop_level) for a stop, the target level "
                "for a target, the session close for a holding-period or end-of-data exit, and the "
                "execution session's open for an SMA cross-down"
            ),
            "commission": "gross_amount * commission_rate",
            "signal_timing": (
                "Entries are computed from completed bars and execute at the next session's open. A stop "
                "or target exit is not a lagged signal: it is evidenced by the bar it fills on. The "
                "holding-period exit and the end-of-data liquidation fill on their own session."
            ),
        },
        "exit_policy": {
            "source": "app/engine/exit_rules.py — one policy shared by the backtester, the forward simulator and the risk journal",
            "stop": "Fires when the session low reaches the stop; fills at min(session_open, stop_level), so a gap down through the level fills at the worse open rather than at a price nobody could have traded at.",
            "target": "Fires when the session high reaches the target; fills at the target level exactly, so a gap up beyond it does not earn the better open.",
            "same_bar_stop_and_target": (
                "A daily bar carries no evidence of intra-bar price order. When one bar touches both "
                "levels the stop is assumed to fill first. This is a conservative assumption disclosed "
                "here, not inferred from the data."
            ),
            "level_evaluation_start": "Levels are evaluated from the bar after the entry execution bar; the entry bar's own range is never used as exit evidence.",
            "time_exit": "A holding-period exit fills at that session's close, and only if no stop or target triggered on or before that session.",
            "end_of_data": "A position still open on the last available bar is force-closed at that bar's close and labelled end_of_data.",
            "undeclared_plan": "A strategy that declares no risk_parameters gets no stop and no target, and behaves exactly as it did before exit-level execution existed.",
        },
        "position_sizing": "Integer whole shares only; no fractional shares in Phase 1",
        "round_trip_attribution": (
            "FIFO by share quantity. A buy's cost basis includes its commission; a sell's "
            "proceeds are net of its commission; slippage is already in the execution price, "
            "so costs are never counted twice."
        ),
        "annualization": {
            "method_version": annualization.METHOD_VERSION,
            "rule": (
                "Annualized return compounds the sample over the elapsed years between its first "
                "and last observation; annualized volatility scales the per-observation standard "
                "deviation by sqrt(observations per year). Both rates come from the market calendar "
                "in app/providers/instruments.py plus the grain and dates of the series the run "
                "priced against, so a US daily bar and a crypto daily bar annualize differently; "
                "no market inherits an equity-trading-day constant, and an unresolvable frequency "
                "or window yields an unavailable metric instead of an assumed one."
            ),
            "sample_gate": (
                f"samples spanning under {annualization.MIN_YEARS_FOR_ANNUALIZATION} elapsed years "
                "report annualized return and volatility as unavailable, because short-period "
                "annualization can exaggerate apparent performance"
            ),
            "basis": (
                "recorded per run under metrics.annualization, including market, frequency, "
                "observations_per_year, elapsed_years, coverage and whether the window sat inside "
                "the calendar's declared coverage"
            ),
        },
        "journal_field_semantics": {
            "actor": "Who produced the fill. Engine fills are recorded as 'strategy'; human and "
                      "api_key actors appear on paper orders, not in a backtest.",
            "strategy_config_hash": "SHA-256 of the strategy spec this run executed against.",
            "stop_price": "Declared protective stop for the open position, simulated against each session's range after the entry bar; null when the template declares none or when the level defines no risk unit for a long position.",
            "target_price": "Declared profit target, simulated the same way; null when the template declares none or when the level would have fired on the entry bar.",
            "planned_risk": "Cash at risk if the stop is hit (entry - stop) * quantity; null when no stop is declared.",
            "planned_r": "Reward/risk implied by stop and target at entry; null unless both are declared.",
            "realized_pnl": "P&L booked by this fill. Null on a buy (position still open); on a sell it is the FIFO-matched close.",
            "realized_r": "realized_pnl / planned_risk; null when planned risk is undefined. Never treated as zero, and never floored at -1R: a stop that gaps through its level really does lose more than the declared risk unit.",
            "exit_reason": "Why a SELL filled: stop, target, time, signal or end_of_data. Null on a BUY, where no exit has happened yet.",
        },
        "constraints": [
            "Long-only (no short selling)",
            "No leverage",
            "No negative cash",
            data_constraint,
            "No dividends, corporate actions, or survivorship bias adjustment",
            "Stops and targets are executed, but only against daily OHLC bars: the intra-bar price "
            "order is unknown, so a bar touching both levels is resolved as stop-first by disclosed "
            "conservative assumption rather than by evidence",
            "No intraday granularity: a stop or target can only be evidenced by a daily bar's range, "
            "so a level breached and recovered inside a session is reported as an exit on that session",
        ],
        "parameters": {
            "commission_rate": strategy_spec.get("commission_rate", 0),
            "slippage_rate": strategy_spec.get("slippage_rate", 0),
            "initial_cash": strategy_spec.get("initial_cash", 0),
            "signal_parameters": strategy_spec.get("signal_parameters", {}),
            "risk_parameters": strategy_spec.get("risk_parameters", {}),
            # Read through the same single source the engine trades against, and keyed
            # on presence rather than truthiness: a declared stop_pct of 0.0 is still a
            # declaration, and a target-only plan is still a plan.
            "risk_parameters_declared": any(
                value is not None
                for value in exit_rules.plan_from_spec(strategy_spec).values()
            ),
            # Recorded even when null: a reader has to be able to tell "no
            # benchmark was requested" from "a benchmark was requested but its
            # return is absent".
            "benchmark_id": strategy_spec.get("benchmark_id"),
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
            "A declared stop or target can close the position before a cross-down, and the session it "
            "fills on generates no re-entry because its open has already passed",
        ]
    elif template_type == "price_change_fixed_hold":
        sp = strategy_spec.get("signal_parameters", {})
        summary["strategy_rules"] = [
            f"If price change over {sp.get('lookback_days')} days <= -{sp.get('threshold_pct')*100}%, generate buy signal",
            f"Hold at most {sp.get('holding_days')} trading sessions after execution",
            "Liquidate at the holding-period exit session close, or earlier if a declared stop or target fires first",
            "Ignore new entry signals while already holding",
        ]
    elif template_type == "buy_and_hold":
        summary["strategy_rules"] = [
            "Buy maximum affordable whole shares at first valid open",
            "Hold until a declared stop or target fires, otherwise to the final valid data date",
            "Liquidate at the final valid close when no declared level fired",
        ]

    if summary["parameters"]["risk_parameters_declared"]:
        summary["strategy_rules"].append(
            "Declared stop and target levels are checked against every session from the bar after entry; "
            "see exit_policy for the fill and ordering assumptions."
        )

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
        "dataset_coverage": None,
        "bars_used": 0,
        "engine_version": ENGINE_VERSION,
        "config_hash": None,
        "data_provider": None,
        "feed_mode": None,
        "data_source_requested": None,
        "data_source_used": None,
        "data_nature": None,
        "start_date": None,
        "end_date": None,
        "initial_cash": 0,
        "final_equity": 0,
        "transactions": [],
        "round_trips": [],
        "daily_equity_curve": [],
        "daily_accounting": [],
        "benchmark_equity_curve": [],
        "metrics": {},
        "warnings": [],
        "errors": errors,
        "methodology_summary": {},
    }
