from typing import Dict, List, Any, Optional

import pandas as pd
import numpy as np

from app.engine.attribution import TIME_OF_DAY_UNAVAILABLE, day_of_week_breakdown
from app.engine.fill_math import size_position


def compute_max_drawdown(equity_curve: List[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    series = pd.Series(equity_curve)
    cummax = series.cummax()
    drawdown = (series - cummax) / cummax
    return float(abs(drawdown.min()))


def compute_metrics(
    initial_cash: float,
    final_equity: float,
    transactions: List[Dict[str, Any]],
    daily_equity: List[float],
    benchmark_equity: Optional[List[float]],
    trading_days: int,
    time_in_market_pct: float = 0.0,
    attribution: Optional[Dict[str, Any]] = None,
    trading_dates: Optional[List[str]] = None,
) -> Dict[str, Any]:
    total_return_pct = _to_float(round((final_equity - initial_cash) / initial_cash * 100, 4)) if initial_cash > 0 else 0.0

    total_commissions = _to_float(round(sum(t["commission_amount"] for t in transactions), 4))
    total_slippage_cost = _to_float(round(sum(t["slippage_amount"] for t in transactions), 4))
    total_trades = len(transactions)

    max_drawdown_pct = _to_float(round(compute_max_drawdown(daily_equity) * 100, 4)) if daily_equity else 0.0

    benchmark_total_return_pct = None
    excess_return_pct = None
    if benchmark_equity and len(benchmark_equity) >= 2:
        benchmark_total_return_pct = _to_float(round(
            (benchmark_equity[-1] - initial_cash) / initial_cash * 100, 4
        )) if initial_cash > 0 else 0.0
        excess_return_pct = _to_float(round(total_return_pct - benchmark_total_return_pct, 4))

    metrics: Dict[str, Any] = {
        "initial_cash": _to_float(initial_cash),
        "final_equity": _to_float(round(final_equity, 4)),
        "total_return_pct": total_return_pct,
        "benchmark_total_return_pct": benchmark_total_return_pct,
        "excess_return_pct": excess_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "total_trades": total_trades,
        "total_commissions": total_commissions,
        "total_slippage_cost": total_slippage_cost,
        "time_in_market_pct": _to_float(round(time_in_market_pct, 2)),
        "trading_days": trading_days,
    }

    metrics.update(_round_trip_metrics(
        initial_cash=initial_cash,
        final_equity=final_equity,
        attribution=attribution,
        trading_dates=trading_dates,
    ))

    years = trading_days / 252.0
    if years >= 1.0 and total_return_pct > -100:
        annualized = ((1 + total_return_pct / 100) ** (1 / years) - 1) * 100
        metrics["annualized_return_pct"] = _to_float(round(annualized, 4))
    else:
        metrics["annualized_return_pct"] = {
            "value": None,
            "reason": "Sample has fewer than 252 trading days; annualized return is not displayed because short-period annualization can exaggerate apparent performance."
        }

    if trading_days >= 252 and len(daily_equity) >= 2:
        returns = pd.Series(daily_equity).pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            daily_vol = float(returns.std())
            annualized_vol = daily_vol * np.sqrt(252)
            metrics["annualized_volatility_pct"] = _to_float(round(annualized_vol * 100, 4))
        else:
            metrics["annualized_volatility_pct"] = {
                "value": None,
                "reason": "Insufficient return variation"
            }
    else:
        metrics["annualized_volatility_pct"] = {
            "value": None,
            "reason": "Sample has fewer than 252 trading days; annualized volatility is not displayed because short-period estimates have wide confidence intervals."
        }

    return metrics


def calc_time_in_market(
    transactions: List[Dict[str, Any]],
    trading_dates: List[str],
) -> float:
    if not transactions or not trading_dates:
        return 0.0

    sorted_txns = sorted(transactions, key=lambda t: t["execution_date"])
    holding = False
    in_market_count = 0

    txn_by_date: Dict[str, str] = {}
    for t in sorted_txns:
        txn_by_date[t["execution_date"]] = t["side"]

    for date_str in trading_dates:
        if date_str in txn_by_date:
            if txn_by_date[date_str] == "BUY":
                holding = True
                in_market_count += 1
            elif txn_by_date[date_str] == "SELL":
                in_market_count += 1
                holding = False
        elif holding:
            in_market_count += 1

    return (in_market_count / len(trading_dates)) * 100 if trading_dates else 0.0


def build_benchmark_equity_curve(
    benchmark_ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float = 0.0,
    slippage_rate: float = 0.0,
    quantity_precision: int = 0,
) -> List[float]:
    if benchmark_ohlcv.empty or len(benchmark_ohlcv) < 1:
        return []

    opens = benchmark_ohlcv["open"].values
    closes = benchmark_ohlcv["close"].values

    first_open = opens[0]
    if first_open <= 0:
        return []

    buy_exec_price = first_open * (1 + slippage_rate)
    max_shares = size_position(
        initial_cash, buy_exec_price, commission_rate, quantity_precision
    )
    if max_shares <= 0:
        return []

    gross_buy = max_shares * buy_exec_price
    commission_buy = gross_buy * commission_rate
    cash = initial_cash - gross_buy - commission_buy
    shares = max_shares

    equity = []
    for i in range(len(benchmark_ohlcv)):
        if i == len(benchmark_ohlcv) - 1 and len(benchmark_ohlcv) > 1:
            last_close = closes[i]
            sell_exec_price = last_close * (1 - slippage_rate)
            gross_sell = shares * sell_exec_price
            commission_sell = gross_sell * commission_rate
            cash = cash + gross_sell - commission_sell
            shares = 0

        equity.append(_to_float(round(cash + shares * closes[i], 4)))

    return equity


def _round_trip_metrics(
    initial_cash: float,
    final_equity: float,
    attribution: Optional[Dict[str, Any]],
    trading_dates: Optional[List[str]],
) -> Dict[str, Any]:
    """Round-trip metrics derived from FIFO-matched closes.

    Anything that cannot be computed is reported as an explicit unavailable
    struct, never as 0.0 — a strategy that declared no stop has an undefined R,
    not an average R of zero.
    """
    net_pnl = _to_float(round(final_equity - initial_cash, 4)) if initial_cash else None
    if attribution is None:
        unavailable = {"value": None, "reason": "Round-trip attribution was not provided for this run."}
        return {
            "net_pnl": net_pnl,
            "win_rate_pct": dict(unavailable),
            "profit_factor": dict(unavailable),
            "average_realized_r": dict(unavailable),
            "day_of_week_breakdown": {
                "available": False, "value": None, "reason": "Round-trip attribution was not provided for this run.",
            },
            "time_of_day_breakdown": dict(TIME_OF_DAY_UNAVAILABLE),
        }

    closed_count = attribution["closed_trade_count"]
    if closed_count:
        win_rate_pct = _to_float(round(attribution["win_count"] / closed_count * 100, 4))
    else:
        win_rate_pct = {
            "value": None,
            "reason": "No closed round trips in this run; win rate is undefined, not 0%.",
        }

    if closed_count and attribution["gross_loss"] > 0:
        profit_factor = _to_float(round(attribution["gross_profit"] / attribution["gross_loss"], 4))
    elif closed_count:
        profit_factor = {
            "value": None,
            "reason": (
                "No losing round trip, so the profit-factor denominator is zero. "
                f"Reported instead as gross_profit={attribution['gross_profit']} over "
                f"{attribution['win_count']} winning closes."
            ),
            "gross_profit": attribution["gross_profit"],
        }
    else:
        profit_factor = {
            "value": None,
            "reason": "No closed round trips in this run; profit factor is undefined, not 0.",
        }

    if attribution["trades_with_planned_risk"] and attribution["mean_realized_r"] is not None:
        average_realized_r = _to_float(attribution["mean_realized_r"])
    elif closed_count:
        average_realized_r = {
            "value": None,
            "reason": (
                "The strategy template declares no stop, so planned risk is undefined and "
                f"realised R cannot be computed for {closed_count} closed trade(s). "
                "Declared as a risk-parameter block in the strategy spec to enable this metric."
            ),
        }
    else:
        average_realized_r = {
            "value": None,
            "reason": "No closed round trips in this run; average realised R is undefined.",
        }

    net_realized = attribution["net_realized_pnl"]
    if net_pnl is None:
        cross_check = {"value": None, "reason": "Initial cash is zero; P&L cross-check is undefined."}
    elif attribution["open_shares"]:
        cross_check = {
            "equity_net_pnl": net_pnl,
            "realized_net_pnl": net_realized,
            "consistent": None,
            "reason": (
                f"{attribution['open_shares']} share(s) remain open, so mark-to-market equity P&L "
                "and realised P&L are not directly comparable."
            ),
        }
    else:
        difference = float(round(float(net_pnl) - float(net_realized), 4))
        cross_check = {
            "equity_net_pnl": float(net_pnl),
            "realized_net_pnl": float(net_realized),
            "difference": difference,
            "consistent": bool(abs(difference) <= 0.01),
        }

    breakdown = (
        day_of_week_breakdown(attribution, trading_dates or [])
        if trading_dates
        else {
            "available": False,
            "value": None,
            "reason": "The run did not expose its trading-session calendar, so no weekday breakdown is possible.",
        }
    )

    return {
        "net_pnl": net_pnl,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "average_realized_r": average_realized_r,
        "closed_trade_count": closed_count,
        "round_trip_summary": {
            k: attribution[k] for k in (
                "win_count", "loss_count", "flat_count", "gross_profit", "gross_loss",
                "net_realized_pnl", "trades_with_planned_risk", "open_shares", "unmatched_sell_count",
            )
        },
        "pnl_cross_check": cross_check,
        "day_of_week_breakdown": breakdown,
        "time_of_day_breakdown": dict(TIME_OF_DAY_UNAVAILABLE),
    }


def _to_float(v):
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return v
