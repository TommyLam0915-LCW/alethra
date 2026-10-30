"""Candle-series integrity: forming vs completed, dedup, gaps, staleness.

These are the rules that stop an unfinished bar or a silent gap from reaching
strategy logic. They run on plain rows, no network and no database.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.crypto_series import (
    RawCandle,
    build_series,
    stale_after_seconds,
)

INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
NOW = datetime(2026, 10, 30, 12, 0, 30, tzinfo=timezone.utc)


def raw(open_time: datetime, close: str = "100", volume: str = "1.5") -> RawCandle:
    c = Decimal(close)
    return RawCandle(
        open_time=open_time,
        open=c,
        high=c + Decimal("1"),
        low=c - Decimal("1"),
        close=c,
        volume=Decimal(volume),
        trade_count=3,
    )


def minute(n: int) -> datetime:
    # n minutes before NOW's minute bucket, aligned to the 1-minute grid.
    return deltatime(n).replace(second=0, microsecond=0)


def deltatime(n: int) -> datetime:
    return datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc) - timedelta(minutes=n)


def test_forming_candle_is_separated_from_completed():
    rows = [raw(minute(3)), raw(minute(2)), raw(minute(1)), raw(minute(0))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)

    # 12:00 bucket closes at 12:01, still open at 12:00:30.
    assert series.forming is not None
    assert series.forming.open_time == minute(0)
    assert series.forming.is_final is False
    assert all(c.is_final for c in series.completed)
    assert minute(0) not in {c.open_time for c in series.completed}


def test_bars_for_strategy_never_include_forming():
    rows = [raw(minute(2)), raw(minute(1)), raw(minute(0))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)
    assert series.bars_for_strategy == series.completed
    assert all(c.is_final for c in series.bars_for_strategy)


def test_duplicate_timestamp_keeps_last_and_is_reported():
    first = raw(minute(1), close="100")
    second = raw(minute(1), close="105")
    series = build_series(INSTRUMENT, 1, [first, second], now=NOW)

    assert len(series.completed) == 1
    assert series.completed[0].close == Decimal("105")
    assert series.duplicate_timestamps == (minute(1),)
    assert series.integrity_ok is False


def test_off_grid_timestamp_is_rejected():
    off = datetime(2026, 10, 30, 11, 58, 30, tzinfo=timezone.utc)
    series = build_series(INSTRUMENT, 1, [raw(off), raw(minute(1))], now=NOW)

    assert [c.open_time for c in series.completed] == [minute(1)]
    assert series.off_grid_timestamps == (off,)
    assert series.integrity_ok is False


def test_invalid_ohlc_is_rejected():
    broken = RawCandle(
        open_time=minute(1),
        open=Decimal("100"),
        high=Decimal("99"),   # high below open
        low=Decimal("98"),
        close=Decimal("100"),
        volume=Decimal("1"),
    )
    series = build_series(INSTRUMENT, 1, [broken, raw(minute(2))], now=NOW)

    assert [c.open_time for c in series.completed] == [minute(2)]
    assert series.invalid_timestamps == (minute(1),)


def test_negative_volume_is_invalid():
    bad = raw(minute(1), volume="-1")
    series = build_series(INSTRUMENT, 1, [bad, raw(minute(2))], now=NOW)
    assert series.invalid_timestamps == (minute(1),)


def test_gap_is_listed_with_exact_missing_count():
    # Present 5,4, 2,1 -> minute 3 missing.
    rows = [raw(minute(5)), raw(minute(4)), raw(minute(2)), raw(minute(1))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)

    assert series.gaps == ((minute(3), minute(3)),)
    assert series.missing_intervals == 1
    assert series.integrity_ok is False


def test_multiple_gaps_count_once_per_run():
    # Present 6, 4, 3, 1 -> minutes 5 and 2 missing (two runs of one).
    rows = [raw(minute(6)), raw(minute(4)), raw(minute(3)), raw(minute(1))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)

    assert series.missing_intervals == 2
    assert len(series.gaps) == 2
    assert series.gaps[0] == (minute(5), minute(5))
    assert series.gaps[1] == (minute(2), minute(2))


def test_no_gaps_when_contiguous():
    rows = [raw(minute(n)) for n in range(1, 6)]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)
    assert series.gaps == ()
    assert series.missing_intervals == 0
    assert series.integrity_ok is True


def test_out_of_order_rows_are_counted_but_sorted():
    # minute(n) is n minutes before 12:00, so this arrives 11:59, 11:57, 11:58.
    rows = [raw(minute(1)), raw(minute(3)), raw(minute(2))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)
    assert series.out_of_order_rows == 1
    assert [c.open_time for c in series.completed] == [minute(3), minute(2), minute(1)]


def test_only_the_newest_open_bucket_is_exposed_as_forming():
    # Two buckets lie after NOW; only the newest may be shown as forming, the
    # older open bucket is recorded invalid rather than exposed as a live quote.
    future1 = datetime(2026, 10, 30, 12, 1, 0, tzinfo=timezone.utc)
    future2 = datetime(2026, 10, 30, 12, 2, 0, tzinfo=timezone.utc)
    series = build_series(INSTRUMENT, 1, [raw(minute(1)), raw(future1), raw(future2)], now=NOW)

    assert series.forming is not None
    assert series.forming.open_time == future2
    assert future1 in series.invalid_timestamps
    assert series.integrity_ok is False


def test_staleness_is_measured_against_interval_not_calendar():
    rows = [raw(minute(1))]
    series = build_series(INSTRUMENT, 1, rows, now=NOW)
    # last completed closes at 12:00; now is 12:00:30 -> 30s, under 120s.
    assert series.stale_after_seconds == 120
    assert series.stale_seconds == 30
    assert series.is_stale is False

    later = NOW + timedelta(minutes=10)
    stale = build_series(INSTRUMENT, 1, rows, now=later)
    assert stale.is_stale is True
    assert stale.stale_seconds > stale.stale_after_seconds


def test_empty_rows_is_stale_and_not_ok_to_trade():
    series = build_series(INSTRUMENT, 1, [], now=NOW)
    assert series.completed == ()
    assert series.forming is None
    assert series.fingerprint  # deterministic hash of nothing
    assert series.is_stale is True
    assert series.last_completed_close_time is None


def test_stale_after_seconds_floor():
    assert stale_after_seconds(1) == 120
    assert stale_after_seconds(0) == 60  # floor guards tiny/zero intervals
    assert stale_after_seconds(60) == 7200


def test_fingerprint_is_stable_and_sensitive():
    rows = [raw(minute(2)), raw(minute(1))]
    a = build_series(INSTRUMENT, 1, rows, now=NOW)
    b = build_series(INSTRUMENT, 1, rows, now=NOW)
    assert a.fingerprint == b.fingerprint

    rows2 = [raw(minute(2)), raw(minute(1), close="101")]
    c = build_series(INSTRUMENT, 1, rows2, now=NOW)
    assert c.fingerprint != a.fingerprint


def test_fingerprint_excludes_forming_candle():
    completed_only = build_series(INSTRUMENT, 1, [raw(minute(1))], now=NOW)
    with_forming = build_series(
        INSTRUMENT, 1, [raw(minute(1)), raw(minute(0), close="999")], now=NOW
    )
    # The forming minute changes nothing a strategy would have consumed.
    assert with_forming.fingerprint == completed_only.fingerprint


def test_close_time_is_open_plus_interval():
    c = build_series(INSTRUMENT, 5, [raw(minute(10))], now=NOW).completed[0]
    assert c.close_time == c.open_time + timedelta(minutes=5)


def test_interval_must_be_positive():
    with pytest.raises(ValueError):
        build_series(INSTRUMENT, 0, [], now=NOW)


def test_naive_datetimes_are_treated_as_utc():
    naive = datetime(2026, 10, 30, 11, 59, 0)
    series = build_series(INSTRUMENT, 1, [raw(naive)], now=NOW)
    assert series.completed[0].open_time.tzinfo is timezone.utc
