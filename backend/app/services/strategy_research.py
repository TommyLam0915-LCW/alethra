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

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.engine import annualization
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
ZERO = Decimal(0)


# -- MB-15: batch_metadata hashing helpers ------------------------------------
import hashlib
import json

def _hash_rules(rules):
    """Hash rule definitions for batch_metadata."""
    payload = json.dumps(
        [{"rule_id": r.rule_id, "family": r.family} for r in rules],
        sort_keys=True
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

def _hash_exit_configs(configs):
    """Hash exit config definitions for batch_metadata."""
    payload = json.dumps(
        [
            {
                "exit_id": c.exit_id,
                "target_atr": str(c.target_atr),
                "stop_atr": str(c.stop_atr),
                "time_stop_bars": c.time_stop_bars,
            }
            for c in configs
        ],
        sort_keys=True
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

def _hash_settings(settings):
    """Hash research settings for batch_metadata."""
    payload = json.dumps(
        {
            "starting_cash": str(settings.starting_cash),
            "notional_per_trade": str(settings.notional_per_trade),
            "commission_rate": str(settings.commission_rate),
            "slippage_rate": str(settings.slippage_rate),
            "atr_window": settings.atr_window,
            "cost_multiple": str(settings.cost_multiple),
        },
        sort_keys=True
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


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
    # MB-16: mean-reversion indicators
    open: List[Decimal]
    volume: List[Decimal]
    volume_sma: List[Optional[Decimal]]
    return_5bar: List[Optional[Decimal]]
    # MB-17: volatility and momentum indicators
    macd: List[Optional[Decimal]]
    macd_signal: List[Optional[Decimal]]
    macd_hist: List[Optional[Decimal]]
    hv: List[Optional[Decimal]]  # Historical volatility (annualized)
    hv_factor: Optional[Decimal]  # sqrt(observations per year) used to annualize hv
    hv_basis: Dict[str, Any]  # the recorded annualization basis behind hv_factor
    atr_pct: List[Optional[Decimal]]  # ATR as percentage of close
    # MB-18: crypto-specific indicators
    open_times: List[Optional[datetime]]  # Bar open time for session detection
    return_4bar: List[Optional[Decimal]]  # 4-bar return for session momentum
    atr_sma20: List[Optional[Decimal]]  # 20-bar SMA of ATR for volatility breakout
    volume_sma10: List[Optional[Decimal]]  # 10-bar SMA of volume for liquidity cycle
    return_6bar: List[Optional[Decimal]]  # 6-bar return for extreme reversal
    # MB-20: triple MA alignment for 捕鯨者
    ema_mid: List[Optional[Decimal]]  # EMA(60)
    ema_long: List[Optional[Decimal]]  # EMA(120)
    # MB-20: VWAP and day-trading indicators for 當日沖銷
    high: List[Decimal]  # Bar high prices
    low: List[Decimal]  # Bar low prices
    vwap: List[Optional[Decimal]]  # Rolling VWAP (96-bar window ≈ 24h of 15m bars)
    prev_day_high: List[Optional[Decimal]]  # Previous "day" high (last 96 bars high)
    prev_day_low: List[Optional[Decimal]]  # Previous "day" low (last 96 bars low)


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
    # MB-20: EMA(60) and EMA(120) for triple MA alignment (捕鯨者)
    ema_mid = _ema_series(closes, 60)
    ema_long = _ema_series(closes, 120)
    trend_strength: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(len(bars)):
        if ema_fast[index] is not None and ema_slow[index] is not None and atr[index]:
            trend_strength[index] = abs(ema_fast[index] - ema_slow[index]) / atr[index]

    # MB-16: mean-reversion indicators
    opens = [bar.open for bar in bars]
    volumes = [bar.volume for bar in bars]
    volume_sma: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(19, len(bars)):
        window = volumes[index - 19 : index + 1]
        volume_sma[index] = sum(window, Decimal(0)) / Decimal(20)
    return_5bar: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(5, len(bars)):
        previous = closes[index - 5]
        if previous > 0:
            return_5bar[index] = closes[index] / previous - Decimal(1)

    # MB-17: MACD (12-26-9)
    ema_12 = _ema_series(closes, 12)
    ema_26 = _ema_series(closes, 26)
    macd_line: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(len(bars)):
        if ema_12[index] is not None and ema_26[index] is not None:
            macd_line[index] = ema_12[index] - ema_26[index]
    macd_signal = _ema_series([m if m is not None else Decimal(0) for m in macd_line], 9)
    macd_hist: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(len(bars)):
        if macd_line[index] is not None and macd_signal[index] is not None:
            macd_hist[index] = macd_line[index] - macd_signal[index]

    # MB-17: Historical volatility, annualized on this series' own cadence.
    # The research lane is handed bars, not an instrument, so the frequency is
    # measured from the timestamps it actually holds: a 4h crypto series has ~6
    # observations a day, not ~1, and a 20-bar window is not 20 days. Whatever
    # the factor turns out to be, _sig_volatility_ratio_arb divides back out the
    # same number, so that comparison stays paired.
    hv_basis = annualization.measured_basis(
        [bar.open_time for bar in bars],
        interval_minutes=bars[0].interval_minutes if bars else None,
    )
    hv_opy = annualization.observations_per_year(hv_basis)
    hv_factor = Decimal(str(math.sqrt(hv_opy))) if hv_opy else None

    hv: List[Optional[Decimal]] = [None] * len(bars)
    if hv_factor is not None:
        for index in range(20, len(bars)):
            log_returns = []
            for offset in range(index - 19, index + 1):
                if closes[offset - 1] > 0 and closes[offset] > 0:
                    log_ret = Decimal(str(math.log(float(closes[offset] / closes[offset - 1]))))
                    log_returns.append(log_ret)
            if len(log_returns) == 20:
                mean_ret = sum(log_returns, Decimal(0)) / Decimal(20)
                variance = sum((r - mean_ret) ** 2 for r in log_returns) / Decimal(19)
                std_dev = variance.sqrt()
                hv[index] = std_dev * hv_factor

    # MB-17: ATR as percentage of close
    atr_pct: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(len(bars)):
        if atr[index] is not None and closes[index] > 0:
            atr_pct[index] = (atr[index] / closes[index]) * Decimal(100)

    # MB-18: crypto-specific indicators
    open_times: List[Optional[datetime]] = [bar.open_time for bar in bars]

    return_4bar: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(4, len(bars)):
        previous = closes[index - 4]
        if previous > 0:
            return_4bar[index] = closes[index] / previous - Decimal(1)

    atr_sma20: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(19, len(bars)):
        window = [atr[i] for i in range(index - 19, index + 1) if atr[i] is not None]
        if len(window) == 20:
            atr_sma20[index] = sum(window, Decimal(0)) / Decimal(20)

    volume_sma10: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(9, len(bars)):
        window = volumes[index - 9 : index + 1]
        volume_sma10[index] = sum(window, Decimal(0)) / Decimal(10)

    return_6bar: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(6, len(bars)):
        previous = closes[index - 6]
        if previous > 0:
            return_6bar[index] = closes[index] / previous - Decimal(1)

    # MB-20: VWAP and day-trading indicators for 當日沖銷
    bar_highs = [bar.high for bar in bars]
    bar_lows = [bar.low for bar in bars]
    VWAP_WINDOW = 96  # ~24h of 15m bars, or ~4 days of 1h bars

    vwap: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(VWAP_WINDOW - 1, len(bars)):
        cumulative_pv = Decimal(0)
        cumulative_vol = Decimal(0)
        for offset in range(VWAP_WINDOW):
            i = index - offset
            typical_price = (bar_highs[i] + bar_lows[i] + closes[i]) / Decimal(3)
            cumulative_pv += typical_price * volumes[i]
            cumulative_vol += volumes[i]
        if cumulative_vol > 0:
            vwap[index] = cumulative_pv / cumulative_vol

    prev_day_high: List[Optional[Decimal]] = [None] * len(bars)
    prev_day_low: List[Optional[Decimal]] = [None] * len(bars)
    for index in range(VWAP_WINDOW, len(bars)):
        window_highs = bar_highs[index - VWAP_WINDOW:index]
        window_lows = bar_lows[index - VWAP_WINDOW:index]
        prev_day_high[index] = max(window_highs)
        prev_day_low[index] = min(window_lows)

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
        open=opens,
        volume=volumes,
        volume_sma=volume_sma,
        return_5bar=return_5bar,
        macd=macd_line,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        hv=hv,
        hv_factor=hv_factor,
        hv_basis=hv_basis.as_dict(),
        atr_pct=atr_pct,
        open_times=open_times,
        return_4bar=return_4bar,
        atr_sma20=atr_sma20,
        volume_sma10=volume_sma10,
        return_6bar=return_6bar,
        ema_mid=ema_mid,
        ema_long=ema_long,
        high=bar_highs,
        low=bar_lows,
        vwap=vwap,
        prev_day_high=prev_day_high,
        prev_day_low=prev_day_low,
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
    """捕鯨者：三線多頭排列 + 回踩進場 (MB-20 升級版).
    
    多頭：EMA(20) > EMA(60) > EMA(120) + 價格回踩 EMA(20) 附近 + RSI 40-65
    空头：EMA(20) < EMA(60) < EMA(120) + 價格反彈 EMA(20) 附近 + RSI 35-60
    """
    fast, slow = ind.ema_fast[index], ind.ema_slow[index]
    mid, long_ = ind.ema_mid[index], ind.ema_long[index]
    rsi = ind.rsi[index]
    if fast is None or slow is None or mid is None or long_ is None or rsi is None:
        return 0
    # Triple MA bullish alignment: EMA(20) > EMA(60) > EMA(120)
    if fast > mid > long_ and ind.close[index] > fast and Decimal(40) <= rsi <= Decimal(65):
        return 1
    # Triple MA bearish alignment: EMA(20) < EMA(60) < EMA(120)
    if fast < mid < long_ and ind.close[index] < fast and Decimal(35) <= rsi <= Decimal(60):
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


# -- MB-16: mean-reversion signal functions ---------------------------------


def _sig_bollinger_mean_reversion(ind: _Indicators, index: int) -> int:
    """Price touches Bollinger Band outer rail + RSI confirmation.
    
    Long when close < lower band (zscore < -2) AND RSI < 30.
    Short when close > upper band (zscore > 2) AND RSI > 70.
    """
    z, rsi = ind.zscore[index], ind.rsi[index]
    if z is None or rsi is None:
        return 0
    if z <= Decimal("-2") and rsi < Decimal("30"):
        return 1
    if z >= Decimal("2") and rsi > Decimal("70"):
        return -1
    return 0


def _sig_rsi_extreme_reversion(ind: _Indicators, index: int) -> int:
    """RSI extreme + no trend (釣魚郎參數修正版).
    
    Long when RSI < 30 AND close within 5% of 50-day MA.
    Short when RSI > 70 AND close within 5% of 50-day MA.
    
    修正：RSI 從 20/80 放寬到 30/70，乖離率從 1% 增加到 5%。
    """
    rsi, slow = ind.rsi[index], ind.ema_slow[index]
    if rsi is None or slow is None or slow == 0:
        return 0
    deviation = abs(ind.close[index] - slow) / slow
    if deviation > Decimal("0.05"):
        return 0
    if rsi < Decimal("30"):
        return 1
    if rsi > Decimal("70"):
        return -1
    return 0


def _sig_rsi_extreme_reversion_wide(ind: _Indicators, index: int) -> int:
    """RSI extreme + wide deviation (經典釣魚郎 10% 乖離率).
    
    Long when RSI < 30 AND close within 10% of 50-day MA.
    Short when RSI > 70 AND close within 10% of 50-day MA.
    
    經典釣魚郎設定：乖離率放寬到 10%，捕捉更極端的均值回歸機會。
    """
    rsi, slow = ind.rsi[index], ind.ema_slow[index]
    if rsi is None or slow is None or slow == 0:
        return 0
    deviation = abs(ind.close[index] - slow) / slow
    if deviation > Decimal("0.10"):
        return 0
    if rsi < Decimal("30"):
        return 1
    if rsi > Decimal("70"):
        return -1
    return 0


def _sig_gap_fill(ind: _Indicators, index: int) -> int:
    """Open gaps > 1% + volume confirmation.
    
    Short when open > previous close × 1.01 AND volume > 1.5 × 20-day avg.
    Long when open < previous close × 0.99 AND volume > 1.5 × 20-day avg.
    """
    if index == 0:
        return 0
    open_price = ind.open[index]
    prev_close = ind.close[index - 1]
    vol_sma = ind.volume_sma[index]
    volume = ind.volume[index]
    if prev_close == 0 or vol_sma is None or vol_sma == 0:
        return 0
    gap_ratio = open_price / prev_close
    if gap_ratio > Decimal("1.01") and volume > vol_sma * Decimal("1.5"):
        return -1
    if gap_ratio < Decimal("0.99") and volume > vol_sma * Decimal("1.5"):
        return 1
    return 0


def _sig_short_term_overbought_oversold(ind: _Indicators, index: int) -> int:
    """5-bar return > 3% + no trend filter.
    
    Short when 5-bar gain > 3%.
    Long when 5-bar loss > 3%.
    """
    ret = ind.return_5bar[index]
    if ret is None:
        return 0
    if ret > Decimal("0.03"):
        return -1
    if ret < Decimal("-0.03"):
        return 1
    return 0


# -- MB-17 Phase 3: volatility signal functions -----------------------------


def _sma_atr(ind: _Indicators, index: int, window: int = 20) -> Optional[Decimal]:
    """Calculate SMA of ATR over the given window ending at index."""
    if index < window - 1:
        return None
    values = []
    for i in range(index - window + 1, index + 1):
        if ind.atr[i] is not None:
            values.append(ind.atr[i])
    if len(values) < window:
        return None
    return sum(values, Decimal(0)) / Decimal(window)


def _hv_percentile(ind: _Indicators, index: int, lookback: int = 100) -> Optional[Decimal]:
    """Calculate HV percentile (0-100) over the lookback period."""
    if ind.hv[index] is None or index < lookback:
        return None
    current_hv = ind.hv[index]
    values = []
    for i in range(index - lookback + 1, index + 1):
        if ind.hv[i] is not None:
            values.append(ind.hv[i])
    if len(values) < 20:
        return None
    values_sorted = sorted(values)
    rank = 0
    for v in values_sorted:
        if v < current_hv:
            rank += 1
        else:
            break
    return Decimal(str(rank)) / Decimal(str(len(values))) * Decimal(100)


def _bollinger_band_width(ind: _Indicators, index: int, window: int = 20) -> Optional[Decimal]:
    """Calculate Bollinger band width: (upper - lower) / middle."""
    if index < window - 1:
        return None
    closes = ind.close[index - window + 1:index + 1]
    if len(closes) < window:
        return None
    mean = sum(closes, Decimal(0)) / Decimal(window)
    if mean == 0:
        return None
    variance = sum((c - mean) ** 2 for c in closes) / Decimal(window)
    std_dev = variance.sqrt()
    upper = mean + 2 * std_dev
    lower = mean - 2 * std_dev
    return (upper - lower) / mean


def _sig_volatility_breakout(ind: _Indicators, index: int) -> int:
    """MB-17 Phase 3 Rule 1: volatility_breakout.
    
    Entry conditions:
    - ATR(14) > 1.5 × SMA(ATR, 20) [volatility expansion]
    - Close > Bollinger upper band (z-score > 2) → long
    - Close < Bollinger lower band (z-score < -2) → short
    - MACD histogram confirms direction (> 0 for long, < 0 for short)
    """
    atr_val = ind.atr[index]
    sma_atr_val = _sma_atr(ind, index, 20)
    z = ind.zscore[index]
    macd_hist_val = ind.macd_hist[index]
    
    if atr_val is None or sma_atr_val is None or z is None or macd_hist_val is None:
        return 0
    if sma_atr_val == 0:
        return 0
    
    # Volatility expansion: ATR > 1.5 × SMA(ATR, 20)
    if atr_val <= Decimal("1.5") * sma_atr_val:
        return 0
    
    # Bollinger band breakout with MACD confirmation
    if z > Decimal("2") and macd_hist_val > 0:
        return 1  # Long
    if z < Decimal("-2") and macd_hist_val < 0:
        return -1  # Short
    return 0


def _sig_volatility_mean_reversion(ind: _Indicators, index: int) -> int:
    """MB-17 Phase 3 Rule 2: volatility_mean_reversion.
    
    Entry conditions:
    - ATR(14) > 2.0 × SMA(ATR, 20) [extreme volatility]
    - HV(20) > 80th percentile [high historical volatility]
    - Close near Bollinger midline (|z-score| < 0.5) [expecting contraction]
    - Direction: opposite to prior 5-bar trend
    """
    atr_val = ind.atr[index]
    sma_atr_val = _sma_atr(ind, index, 20)
    hv_pct = _hv_percentile(ind, index, 100)
    z = ind.zscore[index]
    ret_5bar = ind.return_5bar[index]
    
    if atr_val is None or sma_atr_val is None or hv_pct is None or z is None or ret_5bar is None:
        return 0
    if sma_atr_val == 0:
        return 0
    
    # Extreme volatility: ATR > 2.0 × SMA(ATR, 20)
    if atr_val <= Decimal("2.0") * sma_atr_val:
        return 0
    
    # HV > 80th percentile
    if hv_pct < Decimal("80"):
        return 0
    
    # Near Bollinger midline: |z-score| < 0.5
    if abs(z) >= Decimal("0.5"):
        return 0
    
    # Opposite to prior 5-bar trend
    if ret_5bar > Decimal("0.005"):  # Recent uptrend → short
        return -1
    if ret_5bar < Decimal("-0.005"):  # Recent downtrend → long
        return 1
    return 0


def _sig_volatility_regime(ind: _Indicators, index: int) -> int:
    """MB-17 Phase 3 Rule 3: volatility_regime.
    
    Entry conditions:
    - ATR(14) < 0.5 × SMA(ATR, 20) [extreme low volatility]
    - HV(20) < 20th percentile [low historical volatility]
    - Bollinger band width < minimum of last 20 bars [compression]
    - Direction: breakout (close crosses Bollinger band)
    """
    atr_val = ind.atr[index]
    sma_atr_val = _sma_atr(ind, index, 20)
    hv_pct = _hv_percentile(ind, index, 100)
    bb_width = _bollinger_band_width(ind, index, 20)
    z = ind.zscore[index]
    
    if atr_val is None or sma_atr_val is None or hv_pct is None or bb_width is None or z is None:
        return 0
    if sma_atr_val == 0:
        return 0
    
    # Extreme low volatility: ATR < 0.5 × SMA(ATR, 20)
    if atr_val >= Decimal("0.5") * sma_atr_val:
        return 0
    
    # HV < 20th percentile
    if hv_pct >= Decimal("20"):
        return 0
    
    # Check Bollinger band width is at minimum (compare to prior 20 bars)
    if index >= 40:
        min_width = bb_width
        for i in range(index - 20, index):
            w = _bollinger_band_width(ind, i, 20)
            if w is not None and w < min_width:
                min_width = w
        if bb_width > min_width * Decimal("1.1"):  # Not at minimum
            return 0
    
    # Breakout: close crosses Bollinger band
    if z > Decimal("2"):
        return 1  # Long breakout
    if z < Decimal("-2"):
        return -1  # Short breakout
    return 0


def _sig_hv_extreme(ind: _Indicators, index: int) -> int:
    """MB-17 Phase 3 Rule 4: hv_extreme.
    
    Entry conditions:
    - HV(20) > 90th percentile → expect contraction (fade direction)
    - HV(20) < 10th percentile → expect expansion (trend direction)
    - ATR% > 5% [high volatility] or < 1% [low volatility]
    - Direction: high HV → opposite, low HV → same as recent trend
    """
    hv_pct = _hv_percentile(ind, index, 100)
    atr_pct_val = ind.atr_pct[index]
    ret_5bar = ind.return_5bar[index]
    
    if hv_pct is None or atr_pct_val is None or ret_5bar is None:
        return 0
    
    # High HV (> 90th percentile): expect contraction, fade the trend
    if hv_pct > Decimal("90") and atr_pct_val > Decimal("5"):
        if ret_5bar > Decimal("0.005"):  # Recent uptrend → short
            return -1
        if ret_5bar < Decimal("-0.005"):  # Recent downtrend → long
            return 1
    
    # Low HV (< 10th percentile): expect expansion, follow the trend
    if hv_pct < Decimal("10") and atr_pct_val < Decimal("1"):
        if ret_5bar > Decimal("0.005"):  # Recent uptrend → long
            return 1
        if ret_5bar < Decimal("-0.005"):  # Recent downtrend → short
            return -1
    
    return 0


# -- MB-17 Phase 4: market making rules -------------------------------------

def _sig_spread_capture(ind: _Indicators, index: int) -> int:
    """Market Making Rule 1: spread_capture.
    
    Simulates market making by capturing the bid-ask spread.
    Entry conditions:
    - Low volatility environment (ATR% < 2%)
    - Price at candle low → simulate bid fill (long entry)
    - Price at candle high → simulate ask fill (short entry)
    - Tight target: 0.5 × ATR (capture half the spread)
    - Tight stop: 0.5 × ATR
    
    This rule profits when the market is range-bound and we can capture
    the spread by providing liquidity at both sides.
    """
    if index < 1:
        return 0
    
    atr_pct_val = ind.atr_pct[index]
    if atr_pct_val is None:
        return 0
    
    # Only trade in low volatility (tight spreads are profitable)
    if atr_pct_val > Decimal("2"):
        return 0
    
    bar = ind.close[index]
    # Use high/low from the indicators (we need to add them)
    # For now, approximate: if close is near the low of recent range, go long
    # if close is near the high, go short
    
    # Simple proxy: compare to 5-bar high/low
    if index >= 5:
        recent_closes = ind.close[index-5:index+1]
        recent_high = max(recent_closes)
        recent_low = min(recent_closes)
        range_size = recent_high - recent_low
        
        if range_size == 0:
            return 0
        
        # Position in range: 0 = at low, 1 = at high
        position_in_range = (bar - recent_low) / range_size
        
        if position_in_range < Decimal("0.2"):  # Near low → simulate bid fill
            return 1  # Long
        if position_in_range > Decimal("0.8"):  # Near high → simulate ask fill
            return -1  # Short
    
    return 0


def _sig_inventory_neutral(ind: _Indicators, index: int) -> int:
    """Market Making Rule 2: inventory_neutral.
    
    Adjusts quotes based on inventory to maintain neutral position.
    Entry conditions:
    - Z-score > 1.5 → skew quotes downward (prefer short)
    - Z-score < -1.5 → skew quotes upward (prefer long)
    - ATR% < 3% (moderate volatility)
    
    This simulates a market maker adjusting prices to manage inventory risk.
    """
    z = ind.zscore[index]
    atr_pct_val = ind.atr_pct[index]
    
    if z is None or atr_pct_val is None:
        return 0
    
    # Only trade in moderate volatility
    if atr_pct_val > Decimal("3") or atr_pct_val < Decimal("0.5"):
        return 0
    
    # Skew based on z-score (inventory proxy)
    if z > Decimal("1.5"):
        return -1  # Price high → prefer short (skew ask down)
    if z < Decimal("-1.5"):
        return 1  # Price low → prefer long (skew bid up)
    
    return 0


def _sig_mean_reversion_mm(ind: _Indicators, index: int) -> int:
    """Market Making Rule 3: mean_reversion_mm.
    
    Combines mean reversion with market making logic.
    Entry conditions:
    - |Z-score| > 2 (price deviated from mean)
    - RSI confirms overbought/oversold
    - ATR% < 2% (low volatility, tight spreads profitable)
    - Fade the move: buy when oversold, sell when overbought
    
    This is a market maker providing liquidity at extreme prices.
    """
    z = ind.zscore[index]
    rsi = ind.rsi[index]
    atr_pct_val = ind.atr_pct[index]
    
    if z is None or rsi is None or atr_pct_val is None:
        return 0
    
    # Only trade in low volatility
    if atr_pct_val > Decimal("2"):
        return 0
    
    # Oversold: Z < -2 and RSI < 30 → provide liquidity (buy)
    if z < Decimal("-2") and rsi < Decimal("30"):
        return 1
    
    # Overbought: Z > 2 and RSI > 70 → provide liquidity (sell)
    if z > Decimal("2") and rsi > Decimal("70"):
        return -1
    
    return 0


def _sig_volatility_mm(ind: _Indicators, index: int) -> int:
    """Market Making Rule 4: volatility_mm.
    
    Adjusts spread based on volatility regime.
    Entry conditions:
    - ATR expanding (current ATR > 1.2 × prior ATR) → widen quotes, trade less
    - ATR contracting (current ATR < 0.8 × prior ATR) → tighten quotes, trade more
    - HV < 50th percentile → favorable for market making
    
    This simulates a market maker adjusting to volatility changes.
    """
    atr_val = ind.atr[index]
    hv_pct = _hv_percentile(ind, index, 100)
    
    if atr_val is None or hv_pct is None or index < 20:
        return 0
    
    # Calculate prior ATR (20 bars ago)
    atr_prior = ind.atr[index - 20]
    if atr_prior is None or atr_prior == 0:
        return 0
    
    atr_ratio = atr_val / atr_prior
    
    # Only trade when HV is low (favorable for market making)
    if hv_pct > Decimal("50"):
        return 0
    
    # ATR contracting: tighten quotes, be more aggressive
    if atr_ratio < Decimal("0.8"):
        # Use z-score to determine direction
        z = ind.zscore[index]
        if z is None:
            return 0
        if z < Decimal("-1"):
            return 1  # Buy
        if z > Decimal("1"):
            return -1  # Sell
    
    # ATR expanding: widen quotes, be more selective
    if atr_ratio > Decimal("1.2"):
        # Only trade at extremes
        z = ind.zscore[index]
        if z is None:
            return 0
        if z < Decimal("-2"):
            return 1  # Buy at extreme low
        if z > Decimal("2"):
            return -1  # Sell at extreme high
    
    return 0


# -- MB-18: crypto-specific rules --------------------------------------------


def _sig_session_momentum(ind: _Indicators, index: int) -> int:
    """MB-18 Rule 1: session_momentum.

    Crypto trades 24/7 but volume clusters in Asian (UTC 0-8) and US (UTC 13-21)
    sessions. This rule captures session-specific momentum:
    - Asian session + 4-bar return > 2% + RSI > 50 → long
    - US session + 4-bar return < -2% + RSI < 50 → short
    """
    ret_4 = ind.return_4bar[index]
    rsi_val = ind.rsi[index]
    open_time = ind.open_times[index]

    if ret_4 is None or rsi_val is None or open_time is None:
        return 0

    utc_hour = open_time.hour

    # Asian session (UTC 0-8): momentum continuation on strength
    if 0 <= utc_hour <= 8:
        if ret_4 > Decimal("0.02") and rsi_val > Decimal("50"):
            return 1  # Long
        if ret_4 < Decimal("-0.02") and rsi_val < Decimal("50"):
            return -1  # Short

    # US session (UTC 13-21): momentum continuation on strength
    if 13 <= utc_hour <= 21:
        if ret_4 > Decimal("0.02") and rsi_val > Decimal("50"):
            return 1  # Long
        if ret_4 < Decimal("-0.02") and rsi_val < Decimal("50"):
            return -1  # Short

    return 0


def _sig_volatility_breakout_crypto(ind: _Indicators, index: int) -> int:
    """MB-18 Rule 2: volatility_breakout_crypto.

    Crypto volatility clusters. When ATR expands above its 20-bar mean AND price
    breaks a 20-bar high/low with volume confirmation, the move often continues:
    - ATR(14) > 2.0 × SMA(ATR, 20)
    - Close breaks 20-bar high (long) or low (short)
    - Volume > 1.5 × SMA(Volume, 20)
    """
    atr_val = ind.atr[index]
    atr_avg = ind.atr_sma20[index]
    close_val = ind.close[index]
    high_20 = ind.high_prior[index]
    low_20 = ind.low_prior[index]
    vol_val = ind.volume[index]
    vol_sma = ind.volume_sma[index]

    if (atr_val is None or atr_avg is None or high_20 is None or
            low_20 is None or vol_val is None or vol_sma is None):
        return 0

    if atr_avg == 0 or vol_sma == 0:
        return 0

    # ATR must be expanding significantly
    if atr_val < Decimal("2.0") * atr_avg:
        return 0

    # Volume must confirm
    if vol_val < Decimal("1.5") * vol_sma:
        return 0

    # Breakout direction
    if close_val > high_20:
        return 1  # Long breakout
    if close_val < low_20:
        return -1  # Short breakout

    return 0


def _sig_liquidity_cycle(ind: _Indicators, index: int) -> int:
    """MB-18 Rule 3: liquidity_cycle.

    Crypto liquidity dries up before big moves. When volume drops below half its
    10-bar mean AND price breaks a Bollinger Band rail, the breakout often leads
    to a sustained move:
    - Volume < 0.5 × SMA(Volume, 10)
    - Close breaks Bollinger Band outer rail (z-score > 2 or < -2)
    - Direction = breakout direction
    """
    vol_val = ind.volume[index]
    vol_sma10 = ind.volume_sma10[index]
    z = ind.zscore[index]

    if vol_val is None or vol_sma10 is None or z is None:
        return 0

    if vol_sma10 == 0:
        return 0

    # Volume must be low (liquidity drying up)
    if vol_val > Decimal("0.5") * vol_sma10:
        return 0

    # Bollinger Band breakout
    if z > Decimal("2"):
        return 1  # Long breakout
    if z < Decimal("-2"):
        return -1  # Short breakout

    return 0


def _sig_extreme_reversal(ind: _Indicators, index: int) -> int:
    """MB-18 Rule 4: extreme_reversal.

    Crypto overspends frequently. After an extreme 6-bar move (>5% or <-5%),
    RSI extremes signal exhaustion and reversal:
    - 6-bar return > 5% + RSI > 80 → short (overbought exhaustion)
    - 6-bar return < -5% + RSI < 20 → long (oversold exhaustion)
    - Volume declining confirms exhaustion
    """
    ret_6 = ind.return_6bar[index]
    rsi_val = ind.rsi[index]
    vol_val = ind.volume[index]
    vol_sma10 = ind.volume_sma10[index]

    if ret_6 is None or rsi_val is None or vol_val is None or vol_sma10 is None:
        return 0

    if vol_sma10 == 0:
        return 0

    # Volume must be declining (exhaustion)
    if vol_val > vol_sma10:
        return 0

    # Extreme move + RSI exhaustion
    if ret_6 > Decimal("0.05") and rsi_val > Decimal("80"):
        return -1  # Short reversal
    if ret_6 < Decimal("-0.05") and rsi_val < Decimal("20"):
        return 1  # Long reversal

    return 0


# -- MB-18 TASK 2: arbitrage-proxy rules --------------------------------------
# These rules approximate arbitrage-like patterns within the single-instrument
# framework. True triangular arbitrage requires multi-instrument data alignment,
# which is handled by the standalone arbitrage_engine.py. These proxy rules
# detect intra-bar price inefficiencies and volatility divergences.

def _sig_intra_bar_arbitrage(ind: _Indicators, index: int) -> int:
    """MB-18 TASK 2: Detect intra-bar price inefficiency (arbitrage proxy).

    When the close is significantly displaced from the bar's midpoint
    (H+L)/2, it suggests rapid price discovery that may overshoot.
    Combined with volume confirmation and ATR-scaled threshold.

    Thesis: large close-to-midpoint displacement relative to ATR, with
    volume confirmation, indicates potential overshoot -> mean-reversion trade.
    """
    if index < 1:
        return 0
    atr_val = ind.atr[index]
    if atr_val is None or atr_val <= ZERO:
        return 0

    close = ind.close[index]
    high = ind.high_prior[index] if ind.high_prior[index] is not None else close
    low = ind.low_prior[index] if ind.low_prior[index] is not None else close
    open_price = ind.open[index]

    bar_high = max(high, open_price, close)
    bar_low = min(low, open_price, close)
    mid = (bar_high + bar_low) / Decimal("2")

    displacement = (close - mid).copy_abs()
    displacement_ratio = displacement / atr_val if atr_val > ZERO else ZERO

    if displacement_ratio < Decimal("0.5"):
        return 0

    vol_sma = ind.volume_sma[index] if index < len(ind.volume_sma) else None
    if vol_sma is not None and vol_sma > ZERO:
        vol_ratio = ind.volume[index] / vol_sma
        if vol_ratio < Decimal("1.2"):
            return 0
    else:
        return 0

    if close > mid:
        return -1
    else:
        return 1


def _sig_volatility_ratio_arb(ind: _Indicators, index: int) -> int:
    """MB-18 TASK 2: Volatility ratio arbitrage proxy.

    When ATR% (short-term volatility relative to price) diverges significantly
    from historical volatility (long-term annualized), it suggests a volatility
    regime change that may be tradable as a convergence bet.

    Thesis: if ATR% > 2x the per-bar HV (the annualized series divided back by
    the same recorded factor that annualized it), volatility is elevated relative
    to history -> expect mean reversion in vol -> fade the move. The two sides
    share one factor, so the comparison means the same thing at every grain.
    """
    if index < 20:
        return 0

    atr_pct_val = ind.atr_pct[index]
    hv_val = ind.hv[index]

    if atr_pct_val is None or hv_val is None:
        return 0
    if atr_pct_val <= ZERO or hv_val <= ZERO:
        return 0
    if ind.hv_factor is None or ind.hv_factor <= ZERO:
        return 0

    per_bar_hv = hv_val / ind.hv_factor
    vol_ratio = atr_pct_val / per_bar_hv if per_bar_hv > ZERO else ZERO

    if vol_ratio < Decimal("2.0"):
        return 0

    rsi_val = ind.rsi[index]
    if rsi_val is None:
        return 0

    roc_val = ind.roc[index]
    if roc_val is None:
        return 0

    if rsi_val > Decimal("60") and roc_val > ZERO:
        return -1
    elif rsi_val < Decimal("40") and roc_val < ZERO:
        return 1

    return 0


def _sig_momentum_divergence_arb(ind: _Indicators, index: int) -> int:
    """MB-18 TASK 2: Momentum divergence arbitrage proxy.

    When short-term momentum (ROC) and medium-term momentum (MACD histogram)
    diverge -- one accelerating while the other decelerating -- it signals
    an impending convergence (similar to cross-asset convergence trades).

    Thesis: ROC > 0 but MACD histogram declining (or vice versa) indicates
    momentum exhaustion -> trade the convergence direction.
    """
    if index < 2:
        return 0

    roc_val = ind.roc[index]
    macd_hist_val = ind.macd_hist[index]
    macd_hist_prev = ind.macd_hist[index - 1] if index - 1 < len(ind.macd_hist) else None

    if roc_val is None or macd_hist_val is None or macd_hist_prev is None:
        return 0

    roc_prev = ind.roc[index - 1]
    if roc_prev is None:
        return 0

    roc_accel = roc_val - roc_prev
    macd_delta = macd_hist_val - macd_hist_prev

    if roc_accel > ZERO and macd_delta < ZERO:
        return -1
    elif roc_accel < ZERO and macd_delta > ZERO:
        return 1

    return 0


# -- MB-20: triple MA alignment for 捕鯨者 -----------------------------------


def _sig_triple_ma_trend(ind: _Indicators, index: int) -> int:
    """捕鯨者：三線多頭/空头排列突破。
    
    多頭：EMA(20) > EMA(60) > EMA(120) + 收盤 > EMA(20) + 趨勢確認
    空头：EMA(20) < EMA(60) < EMA(120) + 收盤 < EMA(20) + 趨勢確認
    
    與 pullback_in_trend 差異：此規則不需要 RSI 中性區間，
    只要三線排列正確且價格在正確側即可進場。
    """
    fast, mid, long_ = ind.ema_fast[index], ind.ema_mid[index], ind.ema_long[index]
    strength = ind.trend_strength[index]
    if fast is None or mid is None or long_ is None or strength is None:
        return 0
    if strength < Decimal("0.2"):
        return 0
    if fast > mid > long_ and ind.close[index] > fast:
        return 1
    if fast < mid < long_ and ind.close[index] < fast:
        return -1
    return 0


# -- MB-20: VWAP and day-trading rules for 當日沖銷 ---------------------------


def _sig_vwap_bounce(ind: _Indicators, index: int) -> int:
    """當日沖銷：VWAP 反彈交易。
    
    多頭：價格從下方觸及 VWAP 後反彈（close > VWAP 且前一根 close <= VWAP）
          + 成交量 > 1.2 × 均量 + 價格在 VWAP 上方 1% 以內
    空头：價格從上方觸及 VWAP 後回落（close < VWAP 且前一根 close >= VWAP）
          + 成交量 > 1.2 × 均量 + 價格在 VWAP 下方 1% 以內
    
    VWAP 是日內交易的強弱分界線，觸及後反彈是經典進場信號。
    """
    if index < 1:
        return 0
    vwap_val = ind.vwap[index]
    vwap_prev = ind.vwap[index - 1]
    vol_sma = ind.volume_sma[index]
    
    if vwap_val is None or vwap_prev is None or vol_sma is None or vol_sma == 0:
        return 0
    
    close = ind.close[index]
    prev_close = ind.close[index - 1]
    volume = ind.volume[index]
    
    # Volume confirmation
    if volume < Decimal("1.2") * vol_sma:
        return 0
    
    distance_from_vwap = abs(close - vwap_val) / vwap_val
    
    # Must be near VWAP (within 1%)
    if distance_from_vwap > Decimal("0.01"):
        return 0
    
    # Long: price bounced up from VWAP
    if prev_close <= vwap_prev and close > vwap_val:
        return 1
    
    # Short: price bounced down from VWAP
    if prev_close >= vwap_prev and close < vwap_val:
        return -1
    
    return 0


def _sig_previous_day_breakout(ind: _Indicators, index: int) -> int:
    """當日沖銷：前日高低點突破。
    
    多頭：收盤突破前日高點 + 成交量 > 1.5 × 均量
    空头：收盤跌破前日低點 + 成交量 > 1.5 × 均量
    
    前日高低點是日內交易的重要支撐/壓力位。
    """
    pdh = ind.prev_day_high[index]
    pdl = ind.prev_day_low[index]
    vol_sma = ind.volume_sma[index]
    
    if pdh is None or pdl is None or vol_sma is None or vol_sma == 0:
        return 0
    
    close = ind.close[index]
    volume = ind.volume[index]
    
    # Volume confirmation: must be above average
    if volume < Decimal("1.5") * vol_sma:
        return 0
    
    if close > pdh:
        return 1  # Long breakout
    if close < pdl:
        return -1  # Short breakout
    
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
        label_zh="趨勢回撤（三線排列）",
        family="trend",
        thesis_zh="EMA(20) > EMA(60) > EMA(120) 三線多頭排列時，等待回撤到 EMA(20) 附近、RSI 回到中性區再進場。",
        signal=_sig_pullback_in_trend,
    ),
    ResearchRule(
        rule_id="triple_ma_trend",
        label_zh="三線趨勢突破",
        family="trend",
        thesis_zh="EMA(20)/60/120 多頭排列且收盤在 EMA(20) 上方、趨勢分離度 > 0.2 時順勢進場。",
        signal=_sig_triple_ma_trend,
    ),
    ResearchRule(
        rule_id="range_fade",
        label_zh="區間均值回歸",
        family="mean_reversion",
        thesis_zh="只有在趨勢強度低時，才對 1.5 標準差的偏離做反向。",
        signal=_sig_range_fade,
    ),
    # MB-16: pure mean-reversion rules
    ResearchRule(
        rule_id="bollinger_mean_reversion",
        label_zh="布林帶均值回歸",
        family="mean_reversion",
        thesis_zh="收盤觸及布林帶外軌（z-score < -2 或 > 2）且 RSI 確認過度反應時反向進場。",
        signal=_sig_bollinger_mean_reversion,
    ),
    ResearchRule(
        rule_id="rsi_extreme_reversion",
        label_zh="RSI 極值回歸",
        family="mean_reversion",
        thesis_zh="RSI < 30 或 > 70 且價格在 50 日均線 5% 以內（無趨勢）時反向進場。",
        signal=_sig_rsi_extreme_reversion,
    ),
    ResearchRule(
        rule_id="rsi_extreme_reversion_wide",
        label_zh="RSI 極值回歸（寬版）",
        family="mean_reversion",
        thesis_zh="RSI < 30 或 > 70 且價格在 50 日均線 10% 以內（經典釣魚郎乖離率）時反向進場。",
        signal=_sig_rsi_extreme_reversion_wide,
    ),
    ResearchRule(
        rule_id="gap_fill",
        label_zh="跳空回填",
        family="mean_reversion",
        thesis_zh="開盤跳空 > 1% 且成交量確認過度反應時，預期回填反向進場。",
        signal=_sig_gap_fill,
    ),
    ResearchRule(
        rule_id="short_term_overbought_oversold",
        label_zh="短期超買超賣",
        family="mean_reversion",
        thesis_zh="5 根漲跌 > 3% 時反向進場，無趨勢過濾。",
        signal=_sig_short_term_overbought_oversold,
    ),
    # MB-17 Phase 3: volatility trading rules
    ResearchRule(
        rule_id="volatility_breakout",
        label_zh="波動率突破",
        family="volatility",
        thesis_zh="ATR 擴張（> 1.5× SMA）+ 布林帶突破（|z| > 2）+ MACD 確認時順勢進場。",
        signal=_sig_volatility_breakout,
    ),
    ResearchRule(
        rule_id="volatility_mean_reversion",
        label_zh="波動率均值回歸",
        family="volatility",
        thesis_zh="ATR 極高（> 2× SMA）+ HV > 80th percentile + 價格在中軌附近時反向進場。",
        signal=_sig_volatility_mean_reversion,
    ),
    ResearchRule(
        rule_id="volatility_regime",
        label_zh="波動率狀態",
        family="volatility",
        thesis_zh="ATR 極低（< 0.5× SMA）+ HV < 20th percentile + 布林帶壓縮後突破時進場。",
        signal=_sig_volatility_regime,
    ),
    ResearchRule(
        rule_id="hv_extreme",
        label_zh="歷史波動率極值",
        family="volatility",
        thesis_zh="HV > 90th 時反向（預期收縮），HV < 10th 時順向（預期擴張）。",
        signal=_sig_hv_extreme,
    ),
    # MB-17 Phase 4: market making rules
    ResearchRule(
        rule_id="spread_capture",
        label_zh="價差捕捉",
        family="market_making",
        thesis_zh="低波動率環境（ATR% < 2%）下，價格觸及區間邊緣時提供流動性捕捉價差。",
        signal=_sig_spread_capture,
    ),
    ResearchRule(
        rule_id="inventory_neutral",
        label_zh="庫存中性",
        family="market_making",
        thesis_zh="根據 Z-score 偏離調整報價方向，維持庫存中性（|Z| > 1.5 時反向操作）。",
        signal=_sig_inventory_neutral,
    ),
    ResearchRule(
        rule_id="mean_reversion_mm",
        label_zh="均值回歸做市",
        family="market_making",
        thesis_zh="價格極端偏離（|Z| > 2）+ RSI 確認超買超賣時，提供流動性反向進場。",
        signal=_sig_mean_reversion_mm,
    ),
    ResearchRule(
        rule_id="volatility_mm",
        label_zh="波動率做市",
        family="market_making",
        thesis_zh="根據 ATR 變化調整報價寬度：收縮時積極，擴張時保守（HV < 50th）。",
        signal=_sig_volatility_mm,
    ),
    # MB-18: crypto-specific rules
    ResearchRule(
        rule_id="session_momentum",
        label_zh="時段動量",
        family="crypto_specific",
        thesis_zh="亞洲（UTC 0-8）或美國（UTC 13-21）時段內，4 根報酬 > 2% 且 RSI 確認方向時順勢進場。",
        signal=_sig_session_momentum,
    ),
    ResearchRule(
        rule_id="volatility_breakout_crypto",
        label_zh="加密波動突破",
        family="crypto_specific",
        thesis_zh="ATR 擴張 > 2× 均值 + 價格突破 20 根高低點 + 成交量 > 1.5× 均值時順勢進場。",
        signal=_sig_volatility_breakout_crypto,
    ),
    ResearchRule(
        rule_id="liquidity_cycle",
        label_zh="流動性週期",
        family="crypto_specific",
        thesis_zh="成交量 < 0.5× 均值（流動性枯竭）+ 布林帶突破（|Z| > 2）時順突破方向進場。",
        signal=_sig_liquidity_cycle,
    ),
    ResearchRule(
        rule_id="extreme_reversal",
        label_zh="極值反轉",
        family="crypto_specific",
        thesis_zh="6 根漲跌 > 5% + RSI 極值（> 80 或 < 20）+ 成交量下降時反向進場。",
        signal=_sig_extreme_reversal,
    ),
    # MB-18 TASK 2: arbitrage-proxy rules
    ResearchRule(
        rule_id="intra_bar_arbitrage",
        label_zh="K線內套利",
        family="arbitrage_proxy",
        thesis_zh="收盤價顯著偏離 K 線中點 (H+L)/2，且成交量確認時，做均值回歸交易。",
        signal=_sig_intra_bar_arbitrage,
    ),
    ResearchRule(
        rule_id="volatility_ratio_arb",
        label_zh="波動率比率套利",
        family="arbitrage_proxy",
        thesis_zh="ATR% 相對歷史波動率極端偏高（> 2×），配合 RSI/ROC 方向做波動率回歸交易。",
        signal=_sig_volatility_ratio_arb,
    ),
    ResearchRule(
        rule_id="momentum_divergence_arb",
        label_zh="動量背離套利",
        family="arbitrage_proxy",
        thesis_zh="短期動量 (ROC) 與中期動量 (MACD histogram) 背離時，做動量收斂交易。",
        signal=_sig_momentum_divergence_arb,
    ),
    # MB-20: day-trading rules for 當日沖銷
    ResearchRule(
        rule_id="vwap_bounce",
        label_zh="VWAP 反彈",
        family="day_trading",
        thesis_zh="價格觸及 VWAP 後反彈 + 成交量 > 1.2× 均量時進場（日內強弱分界線交易）。",
        signal=_sig_vwap_bounce,
    ),
    ResearchRule(
        rule_id="previous_day_breakout",
        label_zh="前日高低點突破",
        family="day_trading",
        thesis_zh="收盤突破前日高點或跌破前日低點 + 成交量 > 1.5× 均量時順勢進場。",
        signal=_sig_previous_day_breakout,
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
    # MB-18: crypto-specific exit configs
    ExitConfig(
        exit_id="crypto_balanced",
        label_zh="加密平衡",
        target_atr=Decimal("2.0"),
        stop_atr=Decimal("1.5"),
        time_stop_bars=12,
        thesis_zh="加密貨幣波動較大，目標 2 倍 ATR、停損 1.5 倍 ATR、持有上限 12 根。",
    ),
    ExitConfig(
        exit_id="crypto_trend",
        label_zh="加密順勢",
        target_atr=Decimal("3.0"),
        stop_atr=Decimal("1.5"),
        time_stop_bars=16,
        thesis_zh="讓加密趨勢單跑更遠，目標 3 倍 ATR、停損 1.5 倍 ATR、持有上限 16 根。",
    ),
    ExitConfig(
        exit_id="crypto_conservative",
        label_zh="加密保守",
        target_atr=Decimal("1.5"),
        stop_atr=Decimal("1.5"),
        time_stop_bars=8,
        thesis_zh="加密保守退出，目標與停損同為 1.5 倍 ATR、持有上限 8 根。",
    ),
    ExitConfig(
        exit_id="crypto_aggressive",
        label_zh="加密激進",
        target_atr=Decimal("2.5"),
        stop_atr=Decimal("2.0"),
        time_stop_bars=10,
        thesis_zh="加密激進配置，目標 2.5 倍 ATR、停損 2 倍 ATR、持有上限 10 根。",
    ),
    # MB-18 TASK 2: arbitrage-proxy exit configs
    ExitConfig(
        exit_id="arb_tight",
        label_zh="套利緊縮",
        target_atr=Decimal("0.8"),
        stop_atr=Decimal("0.8"),
        time_stop_bars=4,
        thesis_zh="套利快速進出，目標與停損同為 0.8 倍 ATR、持有上限 4 根。",
    ),
    ExitConfig(
        exit_id="arb_standard",
        label_zh="套利標準",
        target_atr=Decimal("1.2"),
        stop_atr=Decimal("1.0"),
        time_stop_bars=6,
        thesis_zh="套利標準配置，目標 1.2 倍 ATR、停損 1.0 倍 ATR、持有上限 6 根。",
    ),
    # MB-20: day-trading exit configs for 當日沖銷
    ExitConfig(
        exit_id="day_scalp",
        label_zh="日內剝頭皮",
        target_atr=Decimal("0.5"),
        stop_atr=Decimal("0.5"),
        time_stop_bars=4,
        thesis_zh="日內快進快出，目標與停損同為 0.5 倍 ATR、持有上限 4 根。",
    ),
    ExitConfig(
        exit_id="day_standard",
        label_zh="日內標準",
        target_atr=Decimal("1.0"),
        stop_atr=Decimal("0.75"),
        time_stop_bars=6,
        thesis_zh="日內標準配置，目標 1.0 倍 ATR、停損 0.75 倍 ATR、持有上限 6 根。",
    ),
    ExitConfig(
        exit_id="day_conservative",
        label_zh="日內保守",
        target_atr=Decimal("0.8"),
        stop_atr=Decimal("0.5"),
        time_stop_bars=8,
        thesis_zh="日內保守配置，目標 0.8 倍 ATR、停損 0.5 倍 ATR、持有上限 8 根。",
    ),
    # MB-21: ultra-wide exits for high-fee environments (Kraken entry 1.4% round-trip)
    ExitConfig(
        exit_id="ultra_wide_conservative",
        label_zh="超寬保守",
        target_atr=Decimal("5.0"),
        stop_atr=Decimal("2.0"),
        time_stop_bars=24,
        thesis_zh="MB-21: 超寬目標 5 倍 ATR 覆蓋高費用，停損 2 倍 ATR，持有上限 24 根。",
    ),
    ExitConfig(
        exit_id="ultra_wide_standard",
        label_zh="超寬標準",
        target_atr=Decimal("7.0"),
        stop_atr=Decimal("2.5"),
        time_stop_bars=36,
        thesis_zh="MB-21: 超寬目標 7 倍 ATR 覆蓋高費用，停損 2.5 倍 ATR，持有上限 36 根。",
    ),
    ExitConfig(
        exit_id="ultra_wide_aggressive",
        label_zh="超寬激進",
        target_atr=Decimal("10.0"),
        stop_atr=Decimal("3.0"),
        time_stop_bars=48,
        thesis_zh="MB-21: 超寬目標 10 倍 ATR 覆蓋高費用，停損 3 倍 ATR，持有上限 48 根。",
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
        target_distance_pct = exit_config.target_atr * atr_value / close
        if target_distance_pct < settings.cost_multiple * settings.round_trip_cost_rate:
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

    # Aggregate signal-level stats across all folds for G1 observability
    total_signal_stats = {
        "scanned": 0,
        "signals": 0,
        "blocked_by_cost_gate": 0,
        "blocked_by_range": 0,
    }
    for fold in walk_forward_folds:
        if "stats" in fold:
            for key in total_signal_stats:
                total_signal_stats[key] += fold["stats"].get(key, 0)

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

    oos_summary = summarize(out_of_sample, starting_cash=settings.starting_cash)
    
    # Add signal-level stats for G1 observability
    oos_summary.update(total_signal_stats)
    oos_summary["unlabeled"] = (
        total_signal_stats["signals"]
        - total_signal_stats["blocked_by_cost_gate"]
        - total_signal_stats["blocked_by_range"]
        - oos_summary["trades"]
    )
    oos_summary["accounting_ok"] = oos_summary["unlabeled"] >= 0

    winning_folds = sum(
        1
        for fold in walk_forward_folds
        if fold.get("test") and Decimal(fold["test"]["net_pnl"]) > 0
    )
    tested_folds = sum(1 for fold in walk_forward_folds if fold.get("test"))

    if ind.hv_basis["available"]:
        hv_disclosure = (
            f"Historical volatility is annualized by sqrt({ind.hv_basis['observations_per_year']} "
            f"observations/year) measured from this series' own bar cadence "
            f"(method {ind.hv_basis['method_version']}); no equity-trading-day constant is assumed."
        )
        hv_disclosure_zh = (
            f"歷史波動率以本序列自身 K 線節奏量出的每年觀測數（"
            f"{ind.hv_basis['observations_per_year']}）開方年化（方法 "
            f"{ind.hv_basis['method_version']}），不沿用股票交易日常數。"
        )
    else:
        hv_disclosure = (
            f"Historical volatility is not annualized in this study: {ind.hv_basis['reason']}"
        )
        hv_disclosure_zh = f"本次研究的歷史波動率未做年化：{ind.hv_basis['reason']}"

    payload = {
        "lane": LANE,
        "research_version": RESEARCH_VERSION,
        "batch_metadata": {
            "fingerprint": None,  # Will be set by API layer from dataset
            "first_open_time": bars[0].open_time.isoformat(),
            "last_open_time": bars[-1].open_time.isoformat(),
            "bars": len(bars),
            "interval_minutes": bars[0].interval_minutes,
            "research_version": RESEARCH_VERSION,
            "rules_hash": _hash_rules(RULES),
            "exit_configs_hash": _hash_exit_configs(EXIT_CONFIGS),
            "settings_hash": _hash_settings(settings),
        },
        "bars": {
            "count": len(bars),
            "first_open_time": bars[0].open_time.isoformat(),
            "last_open_time": bars[-1].open_time.isoformat(),
            "interval_minutes": bars[0].interval_minutes,
        },
        "annualization": {
            **ind.hv_basis,
            "applies_to": "hv (annualized historical volatility over a 20-bar window)",
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
            hv_disclosure,
        ],
        "disclosures_zh": [
            "此為模擬研究：不接券商、不付款、不動用真實資金。",
            "訊號只讀已完成的 K 線，並在下一根開盤成交。",
            "若同一根同時觸及停損與停利，一律先假設停損成交。",
            "規則與出場組合以走步前推（walk-forward）選擇；只有樣本外區塊可作為證據。",
            "全樣本網格僅供對照，不構成優勢證據。",
            "勝率不是目標：小目標配寬停損可以推高勝率，同時拉低期望值。",
            "行情資料對外重新分發尚未取得授權，僅供內部研究。",
            hv_disclosure_zh,
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
