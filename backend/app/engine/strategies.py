import uuid
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd


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
    cash_after: float,
    shares_after: int,
    rationale: str,
) -> Dict[str, Any]:
    slippage_amount = round(abs(execution_price - observed_price) * quantity, 4)
    gross_amount = round(quantity * execution_price, 4)
    commission_amount = round(gross_amount * commission_rate, 4)

    if side == "BUY":
        net_cash_change = round(-(gross_amount + commission_amount), 4)
    else:
        net_cash_change = round(gross_amount - commission_amount, 4)

    return {
        "transaction_id": str(uuid.uuid4()),
        "trade_date": execution_date,
        "signal_date": signal_date,
        "execution_date": execution_date,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "observed_price": round(observed_price, 4),
        "execution_price_before_costs": round(execution_price, 4),
        "slippage_amount": slippage_amount,
        "commission_amount": commission_amount,
        "gross_amount": gross_amount,
        "net_cash_change": net_cash_change,
        "cash_after": round(cash_after, 4),
        "shares_after": shares_after,
        "rationale": rationale,
    }


def run_buy_and_hold(
    symbol: str,
    ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    transactions = []
    warnings = []

    if len(ohlcv) < 2:
        warnings.append("Not enough data for buy-and-hold (need at least 2 trading days)")
        return transactions, warnings

    signal_date = ohlcv.iloc[0]["date"].strftime("%Y-%m-%d")
    exec_row = ohlcv.iloc[0]
    observed_open = exec_row["open"]
    exec_price = observed_open * (1 + slippage_rate)

    max_shares = int(initial_cash / (exec_price * (1 + commission_rate)))
    if max_shares <= 0:
        warnings.append(f"Insufficient cash to buy 1 share of {symbol} at {exec_price:.2f} (cash: {initial_cash:.2f})")
        return transactions, warnings

    gross = max_shares * exec_price
    commission = gross * commission_rate
    total_cost = gross + commission
    cash_after_buy = initial_cash - total_cost

    transactions.append(generate_transaction(
        signal_date=None,
        execution_date=signal_date,
        symbol=symbol,
        side="BUY",
        quantity=max_shares,
        observed_price=observed_open,
        execution_price=exec_price,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        cash_after=cash_after_buy,
        shares_after=max_shares,
        rationale="Initial allocation at first tradable session",
    ))

    last_row = ohlcv.iloc[-1]
    sell_signal_date = last_row["date"].strftime("%Y-%m-%d")
    observed_close = last_row["close"]
    sell_exec_price = observed_close * (1 - slippage_rate)

    gross_sell = max_shares * sell_exec_price
    commission_sell = gross_sell * commission_rate
    cash_after_sell = cash_after_buy + gross_sell - commission_sell

    transactions.append(generate_transaction(
        signal_date=sell_signal_date,
        execution_date=sell_signal_date,
        symbol=symbol,
        side="SELL",
        quantity=max_shares,
        observed_price=observed_close,
        execution_price=sell_exec_price,
        slippage_rate=slippage_rate,
        commission_rate=commission_rate,
        cash_after=cash_after_sell,
        shares_after=0,
        rationale="Forced end-of-period liquidation at final valid close",
    ))

    return transactions, warnings


def run_sma_crossover(
    symbol: str,
    ohlcv: pd.DataFrame,
    initial_cash: float,
    commission_rate: float,
    slippage_rate: float,
    short_window: int,
    long_window: int,
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

    for i in range(long_window, len(ohlcv) - 1):
        sma_short = sum(closes[i - short_window + 1 : i + 1]) / short_window
        sma_long = sum(closes[i - long_window + 1 : i + 1]) / long_window

        prev_sma_short = sum(closes[i - short_window : i]) / short_window
        prev_sma_long = sum(closes[i - long_window : i]) / long_window

        signal_date = dates[i]
        exec_date = dates[i + 1]
        exec_open = opens[i + 1]

        cross_up = prev_sma_short <= prev_sma_long and sma_short > sma_long
        cross_down = prev_sma_short >= prev_sma_long and sma_short < sma_long

        if cross_up and shares == 0:
            exec_price = exec_open * (1 + slippage_rate)
            max_shares = int(cash / (exec_price * (1 + commission_rate)))
            if max_shares <= 0:
                warnings.append(f"Insufficient cash for SMA buy on {exec_date}")
                continue
            gross = max_shares * exec_price
            commission = gross * commission_rate
            cash = cash - gross - commission
            shares = max_shares

            transactions.append(generate_transaction(
                signal_date=signal_date,
                execution_date=exec_date,
                symbol=symbol,
                side="BUY",
                quantity=max_shares,
                observed_price=exec_open,
                execution_price=exec_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash_after=cash,
                shares_after=shares,
                rationale=f"SMA({short_window},{long_window}) cross-up signal",
            ))

        elif cross_down and shares > 0:
            exec_price = exec_open * (1 - slippage_rate)
            gross = shares * exec_price
            commission = gross * commission_rate
            cash = cash + gross - commission

            transactions.append(generate_transaction(
                signal_date=signal_date,
                execution_date=exec_date,
                symbol=symbol,
                side="SELL",
                quantity=shares,
                observed_price=exec_open,
                execution_price=exec_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash_after=cash,
                shares_after=0,
                rationale=f"SMA({short_window},{long_window}) cross-down signal",
            ))
            shares = 0

    if shares > 0:
        last_row = ohlcv.iloc[-1]
        last_date = last_row["date"].strftime("%Y-%m-%d")
        observed_close = last_row["close"]
        sell_price = observed_close * (1 - slippage_rate)
        gross = shares * sell_price
        commission = gross * commission_rate
        cash = cash + gross - commission

        transactions.append(generate_transaction(
            signal_date=last_date,
            execution_date=last_date,
            symbol=symbol,
            side="SELL",
            quantity=shares,
            observed_price=observed_close,
            execution_price=sell_price,
            slippage_rate=slippage_rate,
            commission_rate=commission_rate,
            cash_after=cash,
            shares_after=0,
            rationale="Forced exit: end of data with open position",
        ))
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
) -> Tuple[List[Dict[str, Any]], List[str]]:
    transactions = []
    warnings = []

    closes = ohlcv["close"].values
    dates = [row["date"].strftime("%Y-%m-%d") for _, row in ohlcv.iterrows()]
    opens = ohlcv["open"].values

    cash = initial_cash
    shares = 0
    exit_index: Optional[int] = None

    i = lookback_days
    while i < len(ohlcv) - 1:
        if shares > 0:
            if exit_index is not None and i == exit_index:
                exec_date = dates[i]
                exec_open = opens[i]
                signal_date = dates[i - 1] if i > 0 else dates[i]

                actual_exit_idx = min(i + holding_days - 1, len(ohlcv) - 1)
                exit_row = ohlcv.iloc[actual_exit_idx]
                exit_date = exit_row["date"].strftime("%Y-%m-%d")
                exit_close = exit_row["close"]
                sell_price = exit_close * (1 - slippage_rate)

                gross = shares * sell_price
                commission = gross * commission_rate
                cash = cash + gross - commission

                transactions.append(generate_transaction(
                    signal_date=signal_date,
                    execution_date=exit_date,
                    symbol=symbol,
                    side="SELL",
                    quantity=shares,
                    observed_price=exit_close,
                    execution_price=sell_price,
                    slippage_rate=slippage_rate,
                    commission_rate=commission_rate,
                    cash_after=cash,
                    shares_after=0,
                    rationale=f"Fixed holding period exit after {holding_days} sessions",
                ))

                if actual_exit_idx >= len(ohlcv) - 1 and i + holding_days - 1 >= len(ohlcv):
                    warnings.append(f"Data ended before forced exit at session {i + holding_days - 1}; exited at last available close")

                shares = 0
                exit_index = None
                i += 1
                continue
            i += 1
            continue

        if i < lookback_days:
            i += 1
            continue

        current_close = closes[i]
        lookback_close = closes[i - lookback_days]
        pct_change = (current_close - lookback_close) / lookback_close

        if pct_change <= -threshold_pct:
            signal_date = dates[i]
            exec_date = dates[i + 1]
            exec_open = opens[i + 1]
            exec_price = exec_open * (1 + slippage_rate)

            max_shares = int(cash / (exec_price * (1 + commission_rate)))
            if max_shares <= 0:
                warnings.append(f"Insufficient cash for price-change buy on {exec_date}")
                i += 1
                continue

            gross = max_shares * exec_price
            commission = gross * commission_rate
            cash = cash - gross - commission
            shares = max_shares

            transactions.append(generate_transaction(
                signal_date=signal_date,
                execution_date=exec_date,
                symbol=symbol,
                side="BUY",
                quantity=max_shares,
                observed_price=exec_open,
                execution_price=exec_price,
                slippage_rate=slippage_rate,
                commission_rate=commission_rate,
                cash_after=cash,
                shares_after=shares,
                rationale=f"Price drop {pct_change*100:.1f}% over {lookback_days} days (threshold: -{threshold_pct*100:.0f}%)",
            ))

            exit_index = i + 1 + holding_days
            i += 2
            continue

        i += 1

    if shares > 0:
        last_row = ohlcv.iloc[-1]
        last_date = last_row["date"].strftime("%Y-%m-%d")
        observed_close = last_row["close"]
        sell_price = observed_close * (1 - slippage_rate)
        gross = shares * sell_price
        commission = gross * commission_rate
        cash = cash + gross - commission

        transactions.append(generate_transaction(
            signal_date=dates[-1],
            execution_date=last_date,
            symbol=symbol,
            side="SELL",
            quantity=shares,
            observed_price=observed_close,
            execution_price=sell_price,
            slippage_rate=slippage_rate,
            commission_rate=commission_rate,
            cash_after=cash,
            shares_after=0,
            rationale="Forced exit: end of data with open position",
        ))
        warnings.append(f"Data ended with open position; force-closed at {last_date} close")

    return transactions, warnings
