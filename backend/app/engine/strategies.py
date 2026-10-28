import uuid
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd

from app.engine import exit_rules
from app.engine.fill_math import BUY, SELL, fill_economics, size_position, slipped_price


def generate_transaction(
    signal_date: Optional[str],
    execution_date: str,
    symbol: str,
    side: str,
    quantity: int,
    observed_price: float,
    execution_price: float,
    slippage_rate: float,
    commission_rate: float,
    cash_before: float,
    shares_after: int,
    rationale: str,
    exit_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """One filled leg of a trade, with its cash movement derived from its own prices.

    The economics come from :mod:`app.engine.fill_math`, and ``cash_after`` is
    that module's net change applied to ``cash_before``. Nothing recomputes the
    money afterwards, so the prices a run reports are the prices its cash was
    built from.
    """
    economics = fill_economics(
        side=side,
        quantity=quantity,
        observed_price=observed_price,
        execution_price=execution_price,
        commission_rate=commission_rate,
    )

    return {
        "transaction_id": str(uuid.uuid4()),
        "trade_date": execution_date,
        "signal_date": signal_date,
        "execution_date": execution_date,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "observed_price": round(float(observed_price), 4),
        "execution_price_before_costs": round(float(execution_price), 4),
        "slippage_amount": economics.slippage_amount,
        "commission_amount": economics.commission_amount,
        "gross_amount": economics.gross_amount,
        "net_cash_change": economics.net_cash_change,
        "cash_after": round(float(cash_before) + economics.net_cash_change, 4),
        "shares_after": shares_after,
        "rationale": rationale,
        "exit_reason": exit_reason,
    }


def _book_exit(
    transactions: List[Dict[str, Any]],
    *,
    symbol: str,
    quantity: int,
    signal_date: Optional[str],
    execution_date: str,
    reference_price: float,
    slippage_rate: float,
    commission_rate: float,
    cash: float,
    rationale: str,
    exit_reason: Optional[str],
) -> float:
    """Sell the entire position at reference_price less slippage; return new cash.

    One convention for every template, so a stop exit, a signal exit and an
    end-of-data exit cannot differ in how costs are applied.
    """
    sell_price = slipped_price(reference_price, SELL, slippage_rate)

    transaction = generate_transaction(
        signal_date=signal_date,
        execution_date=execution_date,
        symbol=symbol,
        side=SELL,
        quantity=quantity,
        observed_price=reference_price,
        execution_price=sell_price,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        cash_before=cash,
        shares_after=0,
        rationale=rationale,
        exit_reason=exit_reason,
    )
    transactions.append(transaction)
    return transaction["cash_after"]


def _book_entry(
    transactions: List[Dict[str, Any]],
    *,
    symbol: str,
    quantity: int,
    signal_date: Optional[str],
    execution_date: str,
    observed_price: float,
    execution_price: float,
    slippage_rate: float,
    commission_rate: float,
    cash: float,
    rationale: str,
) -> float:
    """Buy ``quantity`` shares at the booked price; return the cash left over."""
    transaction = generate_transaction(
        signal_date=signal_date,
        execution_date=execution_date,
        symbol=symbol,
        side=BUY,
        quantity=quantity,
        observed_price=observed_price,
        execution_price=execution_price,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        cash_before=cash,
        shares_after=quantity,
        rationale=rationale,
    )
    transactions.append(transaction)
    return transaction["cash_after"]


def _bar(row: Any) -> Dict[str, Any]:
    return {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}


def _warn_once(warnings: List[str], new_warnings: List[str]) -> None:
    """Append methodology warnings without repeating one per entry."""
    for message in new_warnings:
        if message not in warnings:
            warnings.append(message)


def run_buy_and_hold(
    symbol: str,
    ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
    risk_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    transactions = []
    warnings = []

    if len(ohlcv) < 2:
        warnings.append("Not enough data for buy-and-hold (need at least 2 trading days)")
        return transactions, warnings

    signal_date = ohlcv.iloc[0]["date"].strftime("%Y-%m-%d")
    exec_row = ohlcv.iloc[0]
    observed_open = exec_row["open"]
    exec_price = slipped_price(observed_open, BUY, slippage_rate)

    max_shares = size_position(initial_cash, exec_price, commission_rate)
    if max_shares <= 0:
        warnings.append(f"Insufficient cash to buy 1 share of {symbol} at {exec_price:.2f} (cash: {initial_cash:.2f})")
        return transactions, warnings

    cash_after_buy = _book_entry(
        transactions,
        symbol=symbol,
        quantity=max_shares,
        signal_date=None,
        execution_date=signal_date,
        observed_price=observed_open,
        execution_price=exec_price,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        cash=initial_cash,
        rationale="Initial allocation at first tradable session",
    )

    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    stop_level, target_level = exit_rules.resolve_levels(risk_plan, exec_price)
    _warn_once(warnings, exit_rules.unusable_warnings(risk_plan, stop_level, target_level))

    exited = False
    # Levels start one bar after the entry: the entry filled at bar 0's open and
    # the remainder of that bar's range is not knowable from a daily bar.
    for i in range(1, len(ohlcv)):
        decision = exit_rules.evaluate_bar(_bar(ohlcv.iloc[i]), stop_level, target_level)
        if decision is not None:
            _book_exit(
                transactions,
                symbol=symbol,
                quantity=max_shares,
                signal_date=dates[0],
                execution_date=dates[i],
                reference_price=decision.observed_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash=cash_after_buy,
                rationale=decision.note,
                exit_reason=decision.reason,
            )
            exited = True
            break

    if not exited:
        last_row = ohlcv.iloc[-1]
        _book_exit(
            transactions,
            symbol=symbol,
            quantity=max_shares,
            signal_date=dates[-1],
            execution_date=dates[-1],
            reference_price=last_row["close"],
            slippage_rate=slippage_rate,
            commission_rate=commission_rate,
            cash=cash_after_buy,
            rationale="Forced end-of-period liquidation at final valid close",
            exit_reason=exit_rules.END_OF_DATA,
        )

    return transactions, warnings


def run_sma_crossover(
    symbol: str,
    ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
    short_window: int,
    long_window: int,
    risk_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    transactions = []
    warnings = []

    if short_window >= long_window:
        warnings.append(f"Invalid SMA parameters: short_window ({short_window}) must be < long_window ({long_window})")
        return transactions, warnings

    if len(ohlcv) < long_window + 1:
        warnings.append(
            f"Not enough data for SMA({short_window},{long_window}): "
            f"need at least {long_window + 1} days, have {len(ohlcv)}"
        )
        return transactions, warnings

    closes = ohlcv["close"].values
    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    opens = ohlcv["open"].values

    cash = initial_cash
    shares = 0
    stop_level: Optional[float] = None
    target_level: Optional[float] = None

    # The loop walks execution bars, not signal bars. A crossover is read from
    # completed bars only, so the cross seen at bar i = b - 1 is traded at bar b's
    # open; a stop or target is read from the bar it actually fills on, so exits
    # are evaluated against bar b's own range. With no declared plan the two
    # orderings are the same set of trades the signal-bar loop produced.
    for b in range(long_window + 1, len(ohlcv)):
        if shares > 0:
            decision = exit_rules.evaluate_bar(_bar(ohlcv.iloc[b]), stop_level, target_level)
            if decision is not None:
                cash = _book_exit(
                    transactions,
                    symbol=symbol,
                    quantity=shares,
                    signal_date=dates[b - 1],
                    execution_date=dates[b],
                    reference_price=decision.observed_price,
                    slippage_rate=slippage_rate,
                    commission_rate=commission_rate,
                    cash=cash,
                    rationale=decision.note,
                    exit_reason=decision.reason,
                )
                shares = 0
                stop_level = target_level = None
                # No re-entry on this bar: the exit filled somewhere inside it, and
                # the open an entry would have used has already passed.
                continue

        i = b - 1
        sma_short = sum(closes[i - short_window + 1 : i + 1]) / short_window
        sma_long = sum(closes[i - long_window + 1 : i + 1]) / long_window

        prev_sma_short = sum(closes[i - short_window : i]) / short_window
        prev_sma_long = sum(closes[i - long_window : i]) / long_window

        signal_date = dates[i]
        exec_date = dates[b]
        exec_open = opens[b]

        cross_up = prev_sma_short <= prev_sma_long and sma_short > sma_long
        cross_down = prev_sma_short >= prev_sma_long and sma_short < sma_long

        if cross_up and shares == 0:
            exec_price = slipped_price(exec_open, BUY, slippage_rate)
            max_shares = size_position(cash, exec_price, commission_rate)
            if max_shares <= 0:
                warnings.append(f"Insufficient cash for SMA buy on {exec_date}")
                continue
            cash = _book_entry(
                transactions,
                symbol=symbol,
                quantity=max_shares,
                signal_date=signal_date,
                execution_date=exec_date,
                observed_price=exec_open,
                execution_price=exec_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash=cash,
                rationale=f"SMA({short_window},{long_window}) cross-up signal",
            )
            shares = max_shares
            stop_level, target_level = exit_rules.resolve_levels(risk_plan, exec_price)
            _warn_once(warnings, exit_rules.unusable_warnings(risk_plan, stop_level, target_level))

        elif cross_down and shares > 0:
            cash = _book_exit(
                transactions,
                symbol=symbol,
                quantity=shares,
                signal_date=signal_date,
                execution_date=exec_date,
                reference_price=exec_open,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash=cash,
                rationale=f"SMA({short_window},{long_window}) cross-down signal",
                exit_reason=exit_rules.SIGNAL,
            )
            shares = 0
            stop_level = target_level = None

    if shares > 0:
        last_row = ohlcv.iloc[-1]
        last_date = last_row["date"].strftime("%Y-%m-%d")
        cash = _book_exit(
            transactions,
            symbol=symbol,
            quantity=shares,
            signal_date=last_date,
            execution_date=last_date,
            reference_price=last_row["close"],
            slippage_rate=slippage_rate,
            commission_rate=commission_rate,
            cash=cash,
            rationale="Forced exit: end of data with open position",
            exit_reason=exit_rules.END_OF_DATA,
        )
        warnings.append(f"Data ended with open position; force-closed at {last_date} close")

    return transactions, warnings


def run_price_change_fixed_hold(
    symbol: str,
    ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
    lookback_days: int,
    threshold_pct: float,
    holding_days: int,
    risk_plan: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    transactions = []
    warnings = []

    closes = ohlcv["close"].values
    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    opens = ohlcv["open"].values

    cash = initial_cash
    shares = 0
    exit_index: Optional[int] = None
    entry_index: Optional[int] = None
    stop_level: Optional[float] = None
    target_level: Optional[float] = None

    i = lookback_days
    while i < len(ohlcv):
        if shares > 0:
            # Levels start the bar after the entry: the entry filled at that bar's
            # open, so the rest of its range is not evidence of anything.
            if entry_index is not None and i > entry_index:
                decision = exit_rules.evaluate_bar(
                    _bar(ohlcv.iloc[i]),
                    stop_level,
                    target_level,
                    time_exit=exit_index is not None and i >= exit_index,
                )
                if decision is not None:
                    # The clock exit keeps its original wording: it is a fixed
                    # schedule, not a price-derived condition, so filling at this
                    # session's close is not look-ahead.
                    rationale = (
                        f"Fixed holding period exit after {holding_days} sessions"
                        if decision.reason == exit_rules.TIME
                        else decision.note
                    )
                    cash = _book_exit(
                        transactions,
                        symbol=symbol,
                        quantity=shares,
                        signal_date=dates[i - 1],
                        execution_date=dates[i],
                        reference_price=decision.observed_price,
                        slippage_rate=slippage_rate,
                        commission_rate=commission_rate,
                        cash=cash,
                        rationale=rationale,
                        exit_reason=decision.reason,
                    )
                    shares = 0
                    exit_index = None
                    entry_index = None
                    stop_level = target_level = None
            i += 1
            continue

        if i >= len(ohlcv) - 1:
            # A signal here would execute on a session that does not exist.
            break

        current_close = closes[i]
        lookback_close = closes[i - lookback_days]
        pct_change = (current_close - lookback_close) / lookback_close

        if pct_change <= -threshold_pct:
            signal_date = dates[i]
            exec_date = dates[i + 1]
            exec_open = opens[i + 1]
            exec_price = slipped_price(exec_open, BUY, slippage_rate)

            max_shares = size_position(cash, exec_price, commission_rate)
            if max_shares <= 0:
                warnings.append(f"Insufficient cash for price-change buy on {exec_date}")
                i += 1
                continue

            cash = _book_entry(
                transactions,
                symbol=symbol,
                quantity=max_shares,
                signal_date=signal_date,
                execution_date=exec_date,
                observed_price=exec_open,
                execution_price=exec_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash=cash,
                rationale=(
                    f"Price drop {pct_change*100:.1f}% over {lookback_days} days "
                    f"(threshold: -{threshold_pct*100:.0f}%)"
                ),
            )
            shares = max_shares
            entry_index = i + 1
            stop_level, target_level = exit_rules.resolve_levels(risk_plan, exec_price)
            _warn_once(warnings, exit_rules.unusable_warnings(risk_plan, stop_level, target_level))

            exit_index = i + 1 + holding_days
            i += 2
            continue

        i += 1

    if shares > 0:
        last_row = ohlcv.iloc[-1]
        last_date = last_row["date"].strftime("%Y-%m-%d")
        cash = _book_exit(
            transactions,
            symbol=symbol,
            quantity=shares,
            signal_date=dates[-1],
            execution_date=last_date,
            reference_price=last_row["close"],
            slippage_rate=slippage_rate,
            commission_rate=commission_rate,
            cash=cash,
            rationale="Forced exit: end of data with open position",
            exit_reason=exit_rules.END_OF_DATA,
        )
        warnings.append(f"Data ended with open position; force-closed at {last_date} close")

    return transactions, warnings
