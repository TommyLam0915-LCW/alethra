"""Feature engineering for ML model training.

Converts OHLCV bars into feature vectors for prediction.
All features are computed from historical data only (no future leakage).
"""

from typing import Dict, List, Optional, Tuple
import math


def compute_returns(closes: List[float]) -> List[Optional[float]]:
    """Daily log returns."""
    result: List[Optional[float]] = [None]
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            result.append(math.log(closes[i] / closes[i - 1]))
        else:
            result.append(None)
    return result


def compute_sma(values: List[float], window: int) -> List[Optional[float]]:
    """Simple moving average."""
    result: List[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
        else:
            result.append(sum(values[i - window + 1: i + 1]) / window)
    return result


def compute_volatility(returns: List[Optional[float]], window: int) -> List[Optional[float]]:
    """Rolling standard deviation of returns."""
    result: List[Optional[float]] = []
    for i in range(len(returns)):
        if i < window - 1:
            result.append(None)
            continue
        window_vals = [r for r in returns[i - window + 1: i + 1] if r is not None]
        if len(window_vals) < 2:
            result.append(None)
            continue
        mean = sum(window_vals) / len(window_vals)
        variance = sum((x - mean) ** 2 for x in window_vals) / (len(window_vals) - 1)
        result.append(math.sqrt(variance))
    return result


def compute_rsi(closes: List[float], window: int = 14) -> List[Optional[float]]:
    """Relative Strength Index."""
    result: List[Optional[float]] = [None] * window
    for i in range(window, len(closes)):
        gains = []
        losses = []
        for j in range(i - window + 1, i + 1):
            change = closes[j] - closes[j - 1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100.0 - (100.0 / (1.0 + rs)))
    return result


def compute_macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """MACD line and signal line."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line: List[Optional[float]] = []
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line.append(ema_fast[i] - ema_slow[i])
        else:
            macd_line.append(None)

    macd_values = [v for v in macd_line if v is not None]
    signal_ema = _ema(macd_values, signal) if macd_values else []

    signal_line: List[Optional[float]] = [None] * (len(closes) - len(signal_ema))
    signal_line.extend(signal_ema)

    return macd_line, signal_line


def _ema(values: List[float], span: int) -> List[Optional[float]]:
    """Exponential moving average."""
    result: List[Optional[float]] = []
    multiplier = 2.0 / (span + 1)
    prev_ema: Optional[float] = None

    for i, v in enumerate(values):
        if i < span - 1:
            result.append(None)
        elif i == span - 1:
            prev_ema = sum(values[:span]) / span
            result.append(prev_ema)
        else:
            prev_ema = (v - prev_ema) * multiplier + prev_ema  # type: ignore
            result.append(prev_ema)
    return result


def build_feature_matrix(
    bars: List[Dict],
    lookback_windows: Optional[List[int]] = None,
) -> Tuple[List[List[float]], List[str], List[str]]:
    """Build feature matrix from OHLCV bars.

    Returns:
        (features, feature_names, dates) — aligned lists.
        Rows with any None feature are dropped.
    """
    if lookback_windows is None:
        lookback_windows = [5, 10, 20]

    closes = [b["close"] for b in bars]
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b.get("volume") or 0 for b in bars]
    dates = [b["date"] for b in bars]

    n = len(bars)
    returns = compute_returns(closes)
    rsi_14 = compute_rsi(closes, 14)

    raw_features: Dict[str, List[Optional[float]]] = {}

    for w in lookback_windows:
        raw_features[f"sma_ratio_{w}"] = [
            closes[i] / sma if sma and sma > 0 else None
            for i, sma in enumerate(compute_sma(closes, w))
        ]
        raw_features[f"ret_mean_{w}"] = _rolling_mean(returns, w)
        raw_features[f"volatility_{w}"] = compute_volatility(returns, w)

    raw_features["rsi_14"] = rsi_14

    raw_features["high_low_range"] = [
        (highs[i] - lows[i]) / closes[i] if closes[i] > 0 else None
        for i in range(n)
    ]
    raw_features["open_close_ratio"] = [
        (closes[i] - opens[i]) / opens[i] if opens[i] > 0 else None
        for i in range(n)
    ]
    raw_features["volume_ratio_5"] = [
        volumes[i] / (sum(volumes[max(0, i-4):i+1]) / 5) if sum(volumes[max(0, i-4):i+1]) > 0 else None
        for i in range(n)
    ]
    raw_features["daily_return"] = returns

    feature_names = sorted(raw_features.keys())

    min_warmup = max(lookback_windows) + 1
    features: List[List[float]] = []
    valid_dates: List[str] = []

    for i in range(min_warmup, n):
        row: List[float] = []
        valid = True
        for fname in feature_names:
            val = raw_features[fname][i]
            if val is None or math.isnan(val) or math.isinf(val):
                valid = False
                break
            row.append(val)
        if valid:
            features.append(row)
            valid_dates.append(dates[i])

    return features, feature_names, valid_dates


def _rolling_mean(values: List[Optional[float]], window: int) -> List[Optional[float]]:
    result: List[Optional[float]] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
            continue
        window_vals = [v for v in values[i - window + 1: i + 1] if v is not None]
        if not window_vals:
            result.append(None)
        else:
            result.append(sum(window_vals) / len(window_vals))
    return result


def build_target_direction(
    bars: List[Dict],
    valid_dates: List[str],
) -> List[int]:
    """Binary target: 1 if next-day close > current close, else 0.

    Aligned with valid_dates — only returns targets for dates where
    the next day's data exists.
    """
    date_to_idx = {b["date"]: i for i, b in enumerate(bars)}
    targets: List[int] = []
    for d in valid_dates:
        idx = date_to_idx.get(d)
        if idx is None or idx + 1 >= len(bars):
            continue
        targets.append(1 if bars[idx + 1]["close"] > bars[idx]["close"] else 0)
    return targets
