from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np


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
) -> List[float]:
    if benchmark_ohlcv.empty or len(benchmark_ohlcv) < 1:
        return []

    opens = benchmark_ohlcv["open"].values
    closes = benchmark_ohlcv["close"].values

    first_open = opens[0]
    if first_open <= 0:
        return []

    buy_exec_price = first_open * (1 + slippage_rate)
    max_shares = int(initial_cash / (buy_exec_price * (1 + commission_rate)))
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


def _to_float(v):
    if isinstance(v, (np.floating, np.integer)):
        return float(v)
    return v
