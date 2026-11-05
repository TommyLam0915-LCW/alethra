"""The crypto bar warehouse keeps what the venue only lends (PostgreSQL only).

The upsert is a PostgreSQL ``ON CONFLICT DO NOTHING``, so SQLite cannot
exercise it. The module runs only against a disposable PostgreSQL database::

    ALETHRA_TEST_DB=postgresql+psycopg2://… ./backend/scripts/run_tests.sh pg \
        tests/test_crypto_bar_harvest_pg.py
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("ALETHRA_TEST_DB", "").startswith("postgresql"):
    pytest.skip(
        "PostgreSQL not configured (run via scripts/run_tests.sh pg)",
        allow_module_level=True,
    )

from tests.conftest import TestSession
from app.data.crypto_instruments import KRAKEN_BTC_USD
from app.models.market_data import MarketDataBar
from app.services.crypto_bar_harvest import harvest_interval, load_stored_bars
from app.services.crypto_series import RawCandle, build_series

INSTRUMENT = KRAKEN_BTC_USD.instrument_id
NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


class StubProvider:
    def __init__(self, rows):
        self._rows = rows

    def get_history_candle_series(self, instrument_id, *, interval_minutes, start=None):
        return build_series(
            instrument_id=instrument_id,
            interval_minutes=interval_minutes,
            rows=self._rows,
            now=NOW,
        )


def Decimalish(value):  # noqa: N802 - tiny helper keeps the fixture readable
    from decimal import Decimal
    return Decimal(str(value))


def grid_rows(interval_minutes, count, *, start=None):
    """``count`` consecutive candles, aligned to the interval's own grid."""
    step = timedelta(minutes=interval_minutes)
    if start is None:
        # build_series drops anything off the interval grid, and a daily bar is
        # only on-grid at 00:00 UTC — the same alignment Kraken itself returns.
        start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        start -= step * count
    return [
        RawCandle(
            open_time=start + i * step,
            open=Decimalish(100 + i),
            high=Decimalish(101 + i),
            low=Decimalish(99 + i),
            close=Decimalish(100 + i),
            volume=Decimalish(7),
            trade_count=3,
        )
        for i in range(count)
    ]


def daily_rows(count):
    return grid_rows(1440, count)


def clear(db):
    db.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == INSTRUMENT
    ).delete()
    db.commit()


def test_harvest_stores_every_completed_candle_once():
    rows = daily_rows(10)
    with TestSession() as db:
        report = harvest_interval(db, StubProvider(rows), INSTRUMENT, 1440)
        assert report["bars_read"] == 10
        assert report["bars_new"] == 10
        assert report["bars_already_stored"] == 0
        assert report["integrity_ok"] is True
        assert report["data_nature"] == "live"

        again = harvest_interval(db, StubProvider(rows), INSTRUMENT, 1440)
        assert again["bars_new"] == 0
        assert again["bars_already_stored"] == 10
        assert db.query(MarketDataBar).filter(
            MarketDataBar.instrument_id == INSTRUMENT
        ).count() == 10
        clear(db)


def test_warehouse_read_drops_rows_of_an_unknown_or_wrong_grain():
    rows = daily_rows(6)
    with TestSession() as db:
        harvest_interval(db, StubProvider(rows), INSTRUMENT, 1440)
        # Two rows a grain-blind read would swallow: one labelled daily but
        # stamped off the daily grid, and one carrying no grain at all (the
        # shape every pre-revision row in the table has). build_series would
        # never store either, but the read must not trust the label alone.
        db.add(MarketDataBar(
            instrument_id=INSTRUMENT, provider_id="kraken", interval_minutes=1440,
            bar_date=(NOW - timedelta(hours=13)).date(),
            bar_timestamp=(NOW - timedelta(hours=13)).replace(tzinfo=None),
            open=1.0, high=2.0, low=0.5, close=1.5,
            volume_decimal=Decimalish(1), is_adjusted="raw",
            fetched_at=NOW.replace(tzinfo=None),
        ))
        db.add(MarketDataBar(
            instrument_id=INSTRUMENT, provider_id="kraken", interval_minutes=None,
            bar_date=(NOW - timedelta(days=3)).date(),
            bar_timestamp=(NOW - timedelta(days=3)).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None),
            open=1.0, high=2.0, low=0.5, close=1.5,
            volume_decimal=Decimalish(1), is_adjusted="raw",
            fetched_at=NOW.replace(tzinfo=None),
        ))
        db.flush()
        bars = load_stored_bars(db, INSTRUMENT, 1440)
        assert len(bars) == 6
        assert [b.open_time for b in bars] == sorted(b.open_time for b in bars)
        assert all(b.interval_minutes == 1440 for b in bars)
        assert all(b.open_time.tzinfo is not None for b in bars)
        clear(db)


def test_two_grains_over_the_same_window_do_not_collide():
    """The reason ``interval_minutes`` exists.

    A daily candle and a four-hour candle open at the same instant. Before the
    grain was part of the row identity, a harvest of daily then four-hour bars
    let the two grains share one key, and the four-hour read returned both:
    1,304 "four-hour" bars out of a 700-bar fetch, half of them daily candles.
    """
    daily = grid_rows(1440, 4)
    four_hour = grid_rows(240, 24, start=daily[0].open_time)
    with TestSession() as db:
        d_report = harvest_interval(db, StubProvider(daily), INSTRUMENT, 1440)
        h_report = harvest_interval(db, StubProvider(four_hour), INSTRUMENT, 240)
        assert d_report["bars_new"] == 4
        assert h_report["bars_new"] == 24, (
            "a four-hour bar sharing midnight with a daily bar is a different "
            "row, not a conflict")
        daily_bars = load_stored_bars(db, INSTRUMENT, 1440)
        four_bars = load_stored_bars(db, INSTRUMENT, 240)
        assert len(daily_bars) == 4
        assert len(four_bars) == 24
        midnight = daily_bars[0].open_time
        assert midnight.hour == 0 and midnight in {b.open_time for b in four_bars}
        assert {b.open_time for b in daily_bars} <= {b.open_time for b in four_bars}
        assert db.query(MarketDataBar).filter(
            MarketDataBar.instrument_id == INSTRUMENT).count() == 28
        clear(db)
