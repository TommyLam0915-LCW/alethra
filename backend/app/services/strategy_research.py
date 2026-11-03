"""Cost-aware multi-bar research engine.

The first Strategy Lab round proved a negative result honestly: 5,000 one-bar
round trips lost money. Measured on the same venue bars, the median one-bar
move is 31.5 bps on 4h and 14.7 bps on 1h, while a round trip costs 40 bps.
A one-bar hold therefore cannot pay for itself, whatever the signal says.

This engine studies the next question: with a holding period long enough for
the move to clear costs, do the same signal families show a real edge, and does
that edge survive out of sample?

Design decisions that keep the answer honest:

* entries read only bars up to the signal bar and fill at the next bar open;
* exits are ATR-scaled, so target and stop distance scale with volatility;
* a setup is refused when ATR cannot cover a multiple of the round-trip cost;
* if a bar touches both stop and target, the stop is assumed first — the
  pessimistic assumption, since the bar does not reveal its own path;
* one position at a time, fixed notional, no leverage, no compounding;
* rule/exit selection is walk-forward: each fold is traded using only the
  preceding folds, so the reported out-of-sample number is not a fitted one;
* win rate and expectancy are reported separately, because a tight target with
  a wide stop can produce a high win rate and still lose money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from app.services.strategy_lab import LabBar

RESEARCH_VERSION = "1.0.0"
LANE = "strategy_research_simulated"

DEFAULT_ATR_WINDOW = 14
DEFAULT_COST_MULTIPLE = Decimal("2")
MIN_RESEARCH_BARS = 120
WARMUP_BARS = 60
DEFAULT_FOLDS = 5
MIN_TRAIN_TRADES = 6

MONEY_QUANT = Decimal("0.00000001")
QTY_QUANT = Decimal("0.00000001")
RATE_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class ResearchSettings:
    starting_cash: Decimal = Decimal("10000")
    notional_per_trade: Decimal = Decimal("1000")
    commission_rate: Decimal = Decimal("0.001")
    slippage_rate: Decimal = Decimal("0.001")
    atr_window: int = DEFAULT_ATR_WINDOW
    cost_multiple: Decimal = DEFAULT_COST_MULTIPLE

    @property
    def round_trip_cost_rate(self) -> Decimal:
        return (self.commission_rate + self.slippage_rate) * Decimal(2)


@dataclass(frozen=True)
class ExitConfig:
    exit_id: str
    label_zh: str
    target_atr: Decimal
    stop_atr: Decimal
    time_stop_bars: int
    thesis_zh: str


@dataclass(frozen=True)
class ResearchRule:
    rule_id: str
    label_zh: str
    family: str
    thesis_zh: str
    signal: Callable[["_Indicators", int], int]


@dataclass
class _Indicators:
    close: List[Decimal]
    atr: List[Optional[Decimal]]
    ema_fast: List[Optional[Decimal]]
    ema_slow: List[Optional[Decimal]]
    rsi: List[Optional[Decimal]]
    zscore: List[Optional[Decimal]]
    high_prior: List[Optional[Decimal]]
    low_prior: List[Optional[Decimal]]
    roc: List[Optional[Decimal]]
    trend_strength: List[Optional[Decimal]]


@dataclass
class ResearchTrade:
    rule_id: str
    exit_id: str
    direction: int
    signal_index: int
    entry_index: int
    exit_index: int
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl: Decimal
    commission: Decimal
    slippage_cost: Decimal
    net_pnl: Decimal
    exit_reason: str
    bars_held: int
    cash_after: Decimal
    fold: Optional[int] = None
    # Best and worst price excursion during the hold, as a percentage of the
    # executed entry price. MFE answers "how much of the move was available",
    # MAE answers "how much heat the position took before it closed".
    mfe_pct: Optional[Decimal] = None
    mae_pct: Optional[Decimal] = None


# -- indicator preparation --------------------------------------------------


def _sma(values: Sequence[Decimal], window: int) -> Optional[Decimal]:
    if len(values) < window:
        return None
    return sum(values[-window:], Decimal(0)) / Decimal(window)


def _stdev(values: Sequence[Decimal], window: int) -> Optional[Decimal]:
    sample = values[-window:]
    if len(sample) < window:
        return None
    mean = sum(sample, Decimal(0)) / Decimal(window)
    variance = sum(((value - mean) ** 2 for value in sample), Decimal(0)) / Decimal(window)
    return variance.sqrt()


def _ema_series(values: Sequence[Decimal], window: int) -> List[Optional[Decimal]]:
    out: List[Optional[Decimal]] = [None] * len(values)
    if len(values) < window:
        return out
    weight = Decimal(2) / Decimal(window + 1)
    previous: Optional[Decimal] = None
    for index, value in enumerate(values):
        if previous is None:
            if index + 1 >= window:
                previous = sum(values[index + 1 - window : index + 1], Decimal(0)) / Decimal(window)
                out[index] = previous
            continue
        previous = value * weight + previous * (Decimal(1) - weight)
        out[index] = previous
    return out


def _true_range(bars: Sequence[LabBar], index: int) -> Decimal:
    bar = bars[index]
    if index == 0:
        return bar.high - bar.low
    previous_close = bars[index - 1].close
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def prepare_indicators(bars: Sequence[LabBar], atr_window: int = DEFAULT_ATR_WINDOW) -> _Indicators:
    closes = [bar.close for bar in bars]
    atr: List[Optional[Decimal]] = [None] * len(bars)
    true_ranges = [_true_range(bars, index) for index in range(len(bars))]
    for index in range(len(bars)):
        if index + 1 >= atr_window:
            window = true_ranges[index + 1 - atr_window : index + 1]
            atr[index] = sum(window, Decimal(0)) / Decimal(atr_window)

    rsi: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(14, len(bars)):
        gains = Decimal(0)
        losses = Decimal(0)
        for offset in range(index - 13, index + 1):
            change = closes[offset] - closes[offset - 1]
            if change > 0:
                gains += change
            elif change < 0:
                losses -= change
        if losses == 0:
            rsi[index] = Decimal(100)
        else:
            rs = gains / losses
            rsi[index] = Decimal(100) - (Decimal(100) / (Decimal(1) + rs))

    zscore: List[Optional[Decimal]] = [None] * len(bars)
    high_prior: List[Optional[Decimal]] = [None] * len(bars)
    low_prior: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(19, len(bars)):
        window = closes[index - 19 : index + 1]
        mean = sum(window, Decimal(0)) / Decimal(20)
        deviation = _stdev(window, 20)
        if deviation and deviation > 0:
            zscore[index] = (closes[index] - mean) / deviation
    for index in range(20, len(bars)):
        prior = bars[index - 20 : index]
        high_prior[index] = max(bar.high for bar in prior)
        low_prior[index] = min(bar.low for bar in prior)

    roc: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(10, len(bars)):
        previous = closes[index - 10]
        if previous > 0:
            roc[index] = closes[index] / previous - Decimal(1)

    ema_fast = _ema_series(closes, 20)
    ema_slow = _ema_series(closes, 50)
    trend_strength: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(len(bars)):
        if ema_fast[index] is not None and ema_slow[index] is not None and atr[index]:
            trend_strength[index] = abs(ema_fast[index] - ema_slow[index]) / atr[index]

    return _Indicators(
        close=closes,
        atr=atr,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi=rsi,
        zscore=zscore,
        high_prior=high_prior,
        low_prior=low_prior,
        roc=roc,
        trend_strength=trend_strength,
    )


# -- rules ------------------------------------------------------------------


def _sig_breakout_trend(ind: _Indicators, index: int) -> int:
    high, low = ind.high_prior[index], ind.low_prior[index]
    strength = ind.trend_strength[index]
    if high is None or low is None or strength is None or strength < Decimal("0.5"):
        return 0
    if ind.close[index] > high:
        return 1
    if ind.close[index] < low:
        return -1
    return 0


def _sig_momentum_continuation(ind: _Indicators, index: int) -> int:
    roc, slow, strength = ind.roc[index], ind.ema_slow[index], ind.trend_strength[index]
    if roc is None or slow is None or strength is None or strength < Decimal("0.3"):
        return 0
    if roc > 0 and ind.close[index] > slow:
        return 1
    if roc < 0 and ind.close[index] < slow:
        return -1
    return 0


def _sig_pullback_in_trend(ind: _Indicators, index: int) -> int:
    fast, slow = ind.ema_fast[index], ind.ema_slow[index]
    rsi = ind.rsi[index]
    if fast is None or slow is None or rsi is None:
        return 0
    if fast > slow and ind.close[index] > fast and Decimal(40) <= rsi <= Decimal(65):
        return 1
    if fast < slow and ind.close[index] < fast and Decimal(35) <= rsi <= Decimal(60):
        return -1
    return 0


def _sig_range_fade(ind: _Indicators, index: int) -> int:
    z, strength = ind.zscore[index], ind.trend_strength[index]
    if z is None or strength is None or strength > Decimal("0.25"):
        return 0
    if z >= Decimal("1.5"):
        return -1
    if z <= Decimal("-1.5"):
        return 1
    return 0


RULES: Tuple[ResearchRule, ...] = (
    ResearchRule(
        rule_id="breakout_trend",
        label_zh="趨勢突破",
        family="trend",
        thesis_zh="收盤突破前 20 根的區間，且均線分離度足夠時順勢進場。",
        signal=_sig_breakout_trend,
    ),
    ResearchRule(
        rule_id="momentum_continuation",
        label_zh="動量延續",
        family="momentum",
        thesis_zh="10 根動量為正且價格在長期均線上方時續多，鏡像做空。",
        signal=_sig_momentum_continuation,
    ),
    ResearchRule(
        rule_id="pullback_in_trend",
        label_zh="趨勢回撤",
        family="trend",
        thesis_zh="均線多頭排列時等待回撤到快線附近、RSI 回到中性區再進場。",
        signal=_sig_pullback_in_trend,
    ),
    ResearchRule(
        rule_id="range_fade",
        label_zh="區間均值回歸",
        family="mean_reversion",
        thesis_zh="只有在趨勢強度低時，才對 1.5 標準差的偏離做反向。",
        signal=_sig_range_fade,
    ),
)

EXIT_CONFIGS: Tuple[ExitConfig, ...] = (
    ExitConfig(
        exit_id="balanced_1_1",
        label_zh="對稱 1:1",
        target_atr=Decimal("1.0"),
        stop_atr=Decimal("1.0"),
        time_stop_bars=6,
        thesis_zh="目標與停損同為 1 倍 ATR，持有上限 6 根。",
    ),
    ExitConfig(
        exit_id="trend_1p5_1",
        label_zh="順勢 1.5:1",
        target_atr=Decimal("1.5"),
        stop_atr=Decimal("1.0"),
        time_stop_bars=8,
        thesis_zh="讓趨勢單跑得比停損遠，持有上限 8 根。",
    ),
    ExitConfig(
        exit_id="trend_2_1p5",
        label_zh="長線 2:1.5",
        target_atr=Decimal("2.0"),
        stop_atr=Decimal("1.5"),
        time_stop_bars=12,
        thesis_zh="更寬的停損與目標，持有上限 12 根。",
    ),
    ExitConfig(
        exit_id="high_win_1_2",
        label_zh="高勝率示範 1:2",
        target_atr=Decimal("1.0"),
        stop_atr=Decimal("2.0"),
        time_stop_bars=6,
        thesis_zh="小目標配寬停損，勝率會被推高，但期望值通常被拖低；列入對照。",
    ),
)

RULE_BY_ID = {rule.rule_id: rule for rule in RULES}
EXIT_BY_ID = {config.exit_id: config for config in EXIT_CONFIGS}


# -- simulation -------------------------------------------------------------


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANT), "f")


def _rate(value: Decimal) -> str:
    return format(value.quantize(RATE_QUANT), "f")


def _pct(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.0001")), "f")


EXIT_REASON_TEXT = {
    "target": (
        "停利目標成交：價格走到 ATR 目標。",
        "Target reached: price moved to the ATR-scaled target.",
    ),
    "target_gap": (
        "跳空越過停利目標：以開盤價成交，實際獲利大於目標距離。",
        "Gapped through the target: filled at the open, so the gain exceeds the target distance.",
    ),
    "stop": (
        "停損觸發：價格走到 ATR 停損。",
        "Stop reached: price moved to the ATR-scaled stop.",
    ),
    "stop_gap": (
        "跳空越過停損：以開盤價成交，虧損大於停損距離。",
        "Gapped through the stop: filled at the open, so the loss exceeds the stop distance.",
    ),
    "time_stop": (
        "時間停損：持有上限內未達目標或停損，以收盤價平倉。",
        "Time stop: neither target nor stop was reached before the holding limit, so the position closed at that bar's close.",
    ),
}


def describe_trade(trade: ResearchTrade) -> dict:
    """Turn one trade into a reviewable earn/loss explanation.

    The reason is mechanical, not interpretive: it names the exit rule that
    fired, the money split between gross, fees and slippage, and how much of
    the move was available (MFE) versus how much heat the position took (MAE).
    """
    if trade.net_pnl > 0:
        outcome = "win"
    elif trade.net_pnl < 0:
        outcome = "loss"
    else:
        outcome = "flat"
    reason_zh, reason_en = EXIT_REASON_TEXT.get(
        trade.exit_reason,
        (f"出場（{trade.exit_reason}）。", f"Exit ({trade.exit_reason})."),
    )
    total_cost = (trade.commission + trade.slippage_cost).quantize(MONEY_QUANT)
    excursion_zh = ""
    excursion_en = ""
    if trade.mfe_pct is not None and trade.mae_pct is not None:
        excursion_zh = f"最大有利幅度 {_pct(trade.mfe_pct)}%，最大不利幅度 {_pct(trade.mae_pct)}%。"
        excursion_en = (
            f"Max favourable excursion {_pct(trade.mfe_pct)}%, "
            f"max adverse excursion {_pct(trade.mae_pct)}%."
        )
    cost_zh = (
        f"毛利 {_money(trade.gross_pnl)}，手續費 {_money(trade.commission)}，"
        f"滑價 {_money(trade.slippage_cost)}，淨額 {_money(trade.net_pnl)}。"
    )
    cost_en = (
        f"Gross {_money(trade.gross_pnl)}, commission {_money(trade.commission)}, "
        f"slippage {_money(trade.slippage_cost)}, net {_money(trade.net_pnl)}."
    )
    cost_ratio_pct: Optional[Decimal] = None
    if trade.gross_pnl != 0:
        cost_ratio_pct = (total_cost / abs(trade.gross_pnl) * Decimal(100)).quantize(
            Decimal("0.0001")
        )
    return {
        "outcome": outcome,
        "pnl_reason_zh": f"{reason_zh}持有 {trade.bars_held} 根。{cost_zh}{excursion_zh}",
        "pnl_reason_en": f"{reason_en} Held {trade.bars_held} bars. {cost_en}{excursion_en}",
        "total_cost": _money(total_cost),
        "cost_ratio_pct": _pct(cost_ratio_pct),
    }


def _exec_entry(mid: Decimal, direction: int, slippage: Decimal) -> Decimal:
    return mid * (Decimal(1) + slippage) if direction > 0 else mid * (Decimal(1) - slippage)


def _exec_exit(mid: Decimal, direction: int, slippage: Decimal) -> Decimal:
    return mid * (Decimal(1) - slippage) if direction > 0 else mid * (Decimal(1) + slippage)


def _simulate_range(
    bars: Sequence[LabBar],
    ind: _Indicators,
    rule: ResearchRule,
    exit_config: ExitConfig,
    settings: ResearchSettings,
    start_index: int,
    end_index: int,
    *,
    cash: Optional[Decimal] = None,
    fold: Optional[int] = None,
) -> Tuple[List[ResearchTrade], Dict[str, int]]:
    """Simulate one rule/exit pair over [start_index, end_index] inclusive.

    ``cash`` lets a caller carry one equity curve across walk-forward folds; it
    defaults to the strategy's starting cash.
    """
    trades: List[ResearchTrade] = []
    cash = settings.starting_cash if cash is None else cash
    stats = {"scanned": 0, "signals": 0, "blocked_by_cost_gate": 0, "blocked_by_range": 0}
    index = start_index
    while index <= end_index:
        stats["scanned"] += 1
        direction = rule.signal(ind, index)
        if direction == 0:
            index += 1
            continue
        stats["signals"] += 1
        atr_value = ind.atr[index]
        close = ind.close[index]
        if atr_value is None or atr_value <= 0 or close <= 0:
            index += 1
            continue
        if atr_value / close < settings.cost_multiple * settings.round_trip_cost_rate:
            stats["blocked_by_cost_gate"] += 1
            index += 1
            continue
        entry_index = index + 1
        if entry_index > end_index:
            stats["blocked_by_range"] += 1
            break

        entry_mid = bars[entry_index].open
        if entry_mid <= 0:
            index = entry_index
            continue
        quantity = (settings.notional_per_trade / entry_mid).quantize(QTY_QUANT, rounding=ROUND_DOWN)
        if quantity <= 0:
            index = entry_index
            continue

        entry_price = _exec_entry(entry_mid, direction, settings.slippage_rate)
        target = close + atr_value * exit_config.target_atr * direction
        stop = close - atr_value * exit_config.stop_atr * direction
        last_index = min(entry_index + exit_config.time_stop_bars - 1, end_index)

        exit_index = last_index
        exit_reason = "time_stop"
        exit_mid = bars[last_index].close

        # A gap through the stop or target is filled at the bar open, not at
        # the (unreachable) level.
        if direction > 0:
            if entry_mid <= stop:
                exit_index, exit_reason, exit_mid = entry_index, "stop_gap", entry_mid
            elif entry_mid >= target:
                exit_index, exit_reason, exit_mid = entry_index, "target_gap", entry_mid
        else:
            if entry_mid >= stop:
                exit_index, exit_reason, exit_mid = entry_index, "stop_gap", entry_mid
            elif entry_mid <= target:
                exit_index, exit_reason, exit_mid = entry_index, "target_gap", entry_mid

        if exit_reason.endswith("_gap"):
            pass
        else:
            for bar_index in range(entry_index, last_index + 1):
                bar = bars[bar_index]
                if direction > 0:
                    hit_stop = bar.low <= stop
                    hit_target = bar.high >= target
                else:
                    hit_stop = bar.high >= stop
                    hit_target = bar.low <= target
                # Stop first: a bar does not reveal whether its high or low
                # came first, so claiming the target would be optimistic.
                if hit_stop:
                    exit_index, exit_reason, exit_mid = bar_index, "stop", stop
                    break
                if hit_target:
                    exit_index, exit_reason, exit_mid = bar_index, "target", target
                    break

        exit_price = _exec_exit(exit_mid, direction, settings.slippage_rate)
        gross = (exit_price - entry_price) * quantity * Decimal(direction)
        commission = (quantity * entry_price + quantity * exit_price) * settings.commission_rate
        slippage_cost = quantity * abs(entry_price - entry_mid) + quantity * abs(exit_price - exit_mid)
        net = (gross - commission).quantize(MONEY_QUANT)
        cash = (cash + net).quantize(MONEY_QUANT)

        hold = bars[entry_index : exit_index + 1]
        if direction > 0:
            best = max(bar.high for bar in hold)
            worst = min(bar.low for bar in hold)
            mfe_pct = ((best - entry_price) / entry_price * Decimal(100)).quantize(Decimal("0.0001"))
            mae_pct = ((worst - entry_price) / entry_price * Decimal(100)).quantize(Decimal("0.0001"))
        else:
            best = min(bar.low for bar in hold)
            worst = max(bar.high for bar in hold)
            mfe_pct = ((entry_price - best) / entry_price * Decimal(100)).quantize(Decimal("0.0001"))
            mae_pct = ((entry_price - worst) / entry_price * Decimal(100)).quantize(Decimal("0.0001"))

        trades.append(
            ResearchTrade(
                rule_id=rule.rule_id,
                exit_id=exit_config.exit_id,
                direction=direction,
                signal_index=index,
                entry_index=entry_index,
                exit_index=exit_index,
                entry_time=bars[entry_index].open_time,
                exit_time=bars[exit_index].open_time,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                gross_pnl=gross,
                commission=commission,
                slippage_cost=slippage_cost,
                net_pnl=net,
                exit_reason=exit_reason,
                bars_held=exit_index - entry_index + 1,
                cash_after=cash,
                fold=fold,
                mfe_pct=mfe_pct,
                mae_pct=mae_pct,
            )
        )
        index = exit_index + 1
    return trades, stats


# -- metrics ----------------------------------------------------------------


def summarize(trades: Sequence[ResearchTrade], *, starting_cash: Decimal) -> dict:
    executed = len(trades)
    net = sum((trade.net_pnl for trade in trades), Decimal(0))
    gross = sum((trade.gross_pnl for trade in trades), Decimal(0))
    fees = sum((trade.commission for trade in trades), Decimal(0))
    slippage = sum((trade.slippage_cost for trade in trades), Decimal(0))
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    peak = starting_cash
    max_drawdown = Decimal(0)
    consecutive = worst_consecutive = 0
    for trade in trades:
        if trade.net_pnl < 0:
            consecutive += 1
            worst_consecutive = max(worst_consecutive, consecutive)
        else:
            consecutive = 0
        if trade.cash_after > peak:
            peak = trade.cash_after
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - trade.cash_after) / peak)

    mean = net / Decimal(executed) if executed else None
    variance: Optional[Decimal] = None
    if executed > 1:
        variance = sum(((trade.net_pnl - mean) ** 2 for trade in trades), Decimal(0)) / Decimal(executed - 1)
    stdev = variance.sqrt() if variance is not None and variance >= 0 else None

    return {
        "trades": executed,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": _rate(Decimal(len(wins)) / Decimal(executed)) if executed else None,
        "gross_pnl": _money(gross),
        "total_fees": _money(fees),
        "total_slippage_cost": _money(slippage),
        "net_pnl": _money(net),
        "expectancy_per_trade": _money(mean) if mean is not None else None,
        "avg_win": _money(sum(wins, Decimal(0)) / Decimal(len(wins))) if wins else None,
        "avg_loss": _money(sum(losses, Decimal(0)) / Decimal(len(losses))) if losses else None,
        "profit_factor": _rate(
            sum(wins, Decimal(0)) / abs(sum(losses, Decimal(0)))
        )
        if losses and sum(losses, Decimal(0)) != 0
        else None,
        "mean_over_stdev_per_trade": _rate(mean / stdev)
        if mean is not None and stdev and stdev > 0
        else None,
        "max_drawdown_pct": _rate(max_drawdown * Decimal(100)),
        "worst_consecutive_losses": worst_consecutive,
        "ending_cash": _money(starting_cash + net),
        "accounting_ok": (
            trades[-1].cash_after == (starting_cash + net).quantize(MONEY_QUANT)
            if trades
            else True
        ),
    }


# -- walk-forward -----------------------------------------------------------


def fold_bounds(total_bars: int, warmup: int, folds: int) -> List[Tuple[int, int]]:
    if total_bars <= warmup + folds:
        raise ValueError("not enough bars for the requested folds")
    span = total_bars - warmup
    size = span // folds
    remaining = span - size * folds
    bounds: List[Tuple[int, int]] = []
    start = warmup
    for number in range(folds):
        length = size + (1 if number < remaining else 0)
        bounds.append((start, start + length - 1))
        start += length
    return bounds


def _select_combo(
    bars: Sequence[LabBar],
    ind: _Indicators,
    settings: ResearchSettings,
    train_range: Tuple[int, int],
) -> Optional[dict]:
    candidates = []
    for rule in RULES:
        for exit_config in EXIT_CONFIGS:
            trades, _ = _simulate_range(
                bars, ind, rule, exit_config, settings, train_range[0], train_range[1]
            )
            if len(trades) < MIN_TRAIN_TRADES:
                continue
            summary = summarize(trades, starting_cash=settings.starting_cash)
            candidates.append(
                {
                    "rule_id": rule.rule_id,
                    "exit_id": exit_config.exit_id,
                    "train_trades": len(trades),
                    "train_expectancy": Decimal(summary["expectancy_per_trade"]),
                    "train_profit_factor": summary["profit_factor"],
                }
            )
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            row["train_expectancy"],
            Decimal(row["train_profit_factor"]) if row["train_profit_factor"] else Decimal(0),
            row["train_trades"],
        ),
        reverse=True,
    )
    return candidates[0]


def _selection_payload(selected: dict) -> dict:
    """JSON-safe copy of a selection; money stays a fixed-point string."""
    return {
        **selected,
        "train_expectancy": _money(selected["train_expectancy"]),
    }


def run_study(
    bars: Sequence[LabBar],
    *,
    settings: Optional[ResearchSettings] = None,
    folds: int = DEFAULT_FOLDS,
    include_trades: bool = False,
    min_train_expectancy: Optional[Decimal] = None,
) -> dict:
    settings = settings or ResearchSettings()
    if len(bars) < MIN_RESEARCH_BARS:
        raise ValueError(
            f"at least {MIN_RESEARCH_BARS} bars are required, got {len(bars)}"
        )
    ind = prepare_indicators(bars, settings.atr_window)
    bounds = fold_bounds(len(bars), WARMUP_BARS, folds)

    walk_forward_folds: List[dict] = []
    out_of_sample: List[ResearchTrade] = []
    oos_cash = settings.starting_cash
    for number, (start, end) in enumerate(bounds):
        if number == 0:
            walk_forward_folds.append(
                {
                    "fold": number,
                    "role": "seed",
                    "range": [start, end],
                    "bars": end - start + 1,
                    "selected": None,
                }
            )
            continue
        train_range = (bounds[0][0], start - 1)
        selected = _select_combo(bars, ind, settings, train_range)
        if selected is None:
            walk_forward_folds.append(
                {
                    "fold": number,
                    "role": "test",
                    "range": [start, end],
                    "bars": end - start + 1,
                    "selected": None,
                    "reason": "no_combo_with_enough_training_trades",
                }
            )
            continue
        if (
            min_train_expectancy is not None
            and selected["train_expectancy"] <= min_train_expectancy
        ):
            walk_forward_folds.append(
                {
                    "fold": number,
                    "role": "test",
                    "range": [start, end],
                    "bars": end - start + 1,
                    "selected": _selection_payload(selected),
                    "reason": "training_expectancy_below_policy",
                    "policy_threshold": str(min_train_expectancy),
                }
            )
            continue
        rule = RULE_BY_ID[selected["rule_id"]]
        exit_config = EXIT_BY_ID[selected["exit_id"]]
        fold_start_cash = oos_cash
        trades, stats = _simulate_range(
            bars, ind, rule, exit_config, settings, start, end, cash=fold_start_cash, fold=number
        )
        out_of_sample.extend(trades)
        if trades:
            oos_cash = trades[-1].cash_after
        summary = summarize(trades, starting_cash=fold_start_cash)
        walk_forward_folds.append(
            {
                "fold": number,
                "role": "test",
                "range": [start, end],
                "bars": end - start + 1,
                "selected": _selection_payload(selected),
                "stats": stats,
                "test": summary,
            }
        )

    # Full-sample grid: reported as in-sample, never as evidence of edge.
    grid: List[dict] = []
    for rule in RULES:
        for exit_config in EXIT_CONFIGS:
            trades, stats = _simulate_range(
                bars, ind, rule, exit_config, settings, bounds[0][0], bounds[-1][1]
            )
            summary = summarize(trades, starting_cash=settings.starting_cash)
            grid.append(
                {
                    "rule_id": rule.rule_id,
                    "exit_id": exit_config.exit_id,
                    "label_zh": f"{rule.label_zh} · {exit_config.label_zh}",
                    "stats": stats,
                    "summary": summary,
                }
            )

    best_in_sample = max(
        (row for row in grid if row["summary"]["trades"] > 0),
        key=lambda row: Decimal(row["summary"]["expectancy_per_trade"] or "0"),
        default=None,
    )
    highest_win_rate = max(
        (row for row in grid if row["summary"]["trades"] > 0),
        key=lambda row: Decimal(row["summary"]["win_rate"] or "0"),
        default=None,
    )

    oos_summary = summarize(out_of_sample, starting_cash=settings.starting_cash)

    winning_folds = sum(
        1
        for fold in walk_forward_folds
        if fold.get("test") and Decimal(fold["test"]["net_pnl"]) > 0
    )
    tested_folds = sum(1 for fold in walk_forward_folds if fold.get("test"))

    payload = {
        "lane": LANE,
        "research_version": RESEARCH_VERSION,
        "bars": {
            "count": len(bars),
            "first_open_time": bars[0].open_time.isoformat(),
            "last_open_time": bars[-1].open_time.isoformat(),
            "interval_minutes": bars[0].interval_minutes,
        },
        "settings": {
            "starting_cash": _money(settings.starting_cash),
            "notional_per_trade": _money(settings.notional_per_trade),
            "commission_rate_per_side": _rate(settings.commission_rate),
            "slippage_rate_per_side": _rate(settings.slippage_rate),
            "round_trip_cost_rate": _rate(settings.round_trip_cost_rate),
            "atr_window": settings.atr_window,
            "cost_multiple": _rate(settings.cost_multiple),
            "holding": "multi-bar with ATR target/stop and time stop",
            "max_one_position": True,
            "compounding": False,
        },
        "rules": [
            {
                "rule_id": rule.rule_id,
                "label_zh": rule.label_zh,
                "family": rule.family,
                "thesis_zh": rule.thesis_zh,
            }
            for rule in RULES
        ],
        "exit_configs": [
            {
                "exit_id": config.exit_id,
                "label_zh": config.label_zh,
                "target_atr": str(config.target_atr),
                "stop_atr": str(config.stop_atr),
                "time_stop_bars": config.time_stop_bars,
                "thesis_zh": config.thesis_zh,
            }
            for config in EXIT_CONFIGS
        ],
        "walk_forward": {
            "folds": walk_forward_folds,
            "tested_folds": tested_folds,
            "profitable_folds": winning_folds,
            "selection_rule": "highest training expectancy with at least "
            f"{MIN_TRAIN_TRADES} training trades"
            + (
                f"; abstain unless training expectancy > {min_train_expectancy}"
                if min_train_expectancy is not None
                else ""
            ),
            "out_of_sample": oos_summary,
        },
        "in_sample_grid": grid,
        "highlights": {
            "best_in_sample": {
                "label_zh": best_in_sample["label_zh"],
                "summary": best_in_sample["summary"],
            }
            if best_in_sample
            else None,
            "highest_win_rate": {
                "label_zh": highest_win_rate["label_zh"],
                "summary": highest_win_rate["summary"],
            }
            if highest_win_rate
            else None,
        },
        "disclosures": [
            "Simulated research only: no broker order, no payment, no real money.",
            "Entries fill at the next bar open; indicators read only completed bars.",
            "If one bar touches both stop and target, the stop is assumed first.",
            "Rule and exit selection is walk-forward; the out-of-sample block is the evidence.",
            "The in-sample grid is shown for comparison and is not evidence of an edge.",
            "Win rate is not the objective: a tight target with a wide stop can raise it "
            "while lowering expectancy.",
            "Market-data redistribution is NOT_CLEARED; internal research only.",
        ],
        "disclosures_zh": [
            "此為模擬研究：不接券商、不付款、不動用真實資金。",
            "訊號只讀已完成的 K 線，並在下一根開盤成交。",
            "若同一根同時觸及停損與停利，一律先假設停損成交。",
            "規則與出場組合以走步前推（walk-forward）選擇；只有樣本外區塊可作為證據。",
            "全樣本網格僅供對照，不構成優勢證據。",
            "勝率不是目標：小目標配寬停損可以推高勝率，同時拉低期望值。",
            "行情資料對外重新分發尚未取得授權，僅供內部研究。",
        ],
    }
    if include_trades:
        payload["out_of_sample_trades"] = [
            {
                "fold": trade.fold,
                "rule_id": trade.rule_id,
                "rule_label_zh": RULE_BY_ID[trade.rule_id].label_zh,
                "exit_id": trade.exit_id,
                "exit_label_zh": EXIT_BY_ID[trade.exit_id].label_zh,
                "direction": "long" if trade.direction > 0 else "short",
                "signal_time": bars[trade.signal_index].open_time.isoformat(),
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(),
                "entry_price": _money(trade.entry_price),
                "exit_price": _money(trade.exit_price),
                "quantity": format(trade.quantity, "f"),
                "gross_pnl": _money(trade.gross_pnl),
                "commission": _money(trade.commission),
                "slippage_cost": _money(trade.slippage_cost),
                "net_pnl": _money(trade.net_pnl),
                "exit_reason": trade.exit_reason,
                "bars_held": trade.bars_held,
                "cash_after": _money(trade.cash_after),
                "mfe_pct": _pct(trade.mfe_pct),
                "mae_pct": _pct(trade.mae_pct),
                **describe_trade(trade),
            }
            for trade in out_of_sample
        ]
    return payload


__all__ = [
    "DEFAULT_FOLDS",
    "EXIT_CONFIGS",
    "ExitConfig",
    "RULES",
    "RESEARCH_VERSION",
    "ResearchRule",
    "ResearchSettings",
    "ResearchTrade",
    "describe_trade",
    "fold_bounds",
    "prepare_indicators",
    "run_study",
    "summarize",
]
