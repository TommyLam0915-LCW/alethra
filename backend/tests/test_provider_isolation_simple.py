"""Provider isolation edge cases for the stored-data reads.

The mock-query-chain version of this file asserted on ``'provider_id' in
str(mock.call_args)``, which cannot see a SQLAlchemy expression passed
positionally to ``filter``; two of its three cases failed at HEAD while the
third passed vacuously. These cases run against the real test database and
assert on the rows that come back, covering the boundaries the sibling file
(test_provider_read_isolation.py) does not: emptiness, date windows,
cross-instrument leakage and date deduplication.
"""
from datetime import date, datetime

import pytest

from app.api.simulations import _get_stored_trade_dates, _load_stored_ohlcv
from app.models.market_data import MarketDataBar
from tests.conftest import TestSession

SYMBOL = "US:AAPL"
OTHER_SYMBOL = "US:MSFT"
TWELVEDATA_OPEN = 190.94
DEMO_OPEN = 200.176


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _bar(instrument_id: str, provider_id: str, day: int, open_: float) -> MarketDataBar:
    bar_date = date(2024, 1, day)
    return MarketDataBar(
        instrument_id=instrument_id,
        provider_id=provider_id,
        bar_date=bar_date,
        bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
        open=open_,
        high=open_ + 1,
        low=open_ - 1,
        close=open_ + 0.5,
        volume=1_000_000,
    )


def _seed(db):
    """twelvedata 01-29/01-30/02-01, demo 01-29/01-30, plus another instrument."""
    db.add(_bar(SYMBOL, "twelvedata", 29, TWELVEDATA_OPEN))
    db.add(_bar(SYMBOL, "twelvedata", 30, TWELVEDATA_OPEN + 1))
    db.add(_bar(SYMBOL, "twelvedata", 31, TWELVEDATA_OPEN + 2))
    db.add(_bar(SYMBOL, "demo", 29, DEMO_OPEN))
    db.add(_bar(SYMBOL, "demo", 30, DEMO_OPEN + 1))
    db.add(_bar(OTHER_SYMBOL, "twelvedata", 29, 400.0))
    db.commit()


class TestProviderIsolationEdgeCases:
    def test_load_stored_ohlcv_stays_empty_for_a_provider_without_data(self, db):
        db.add(_bar(SYMBOL, "demo", 29, DEMO_OPEN))
        db.commit()

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-30", db, provider_id="twelvedata")

        assert df.empty, "a provider with no stored bars must not borrow another provider's rows"

    def test_load_stored_ohlcv_respects_the_date_window(self, db):
        _seed(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-30", db, provider_id="twelvedata")

        # 01-31 is stored for twelvedata but outside the requested window.
        assert sorted(df.index.tolist()) == ["2024-01-29", "2024-01-30"]
        assert df["open"].tolist() == [TWELVEDATA_OPEN, TWELVEDATA_OPEN + 1]

    def test_load_stored_ohlcv_inclusive_bounds_return_the_edge_bars(self, db):
        _seed(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-29", db, provider_id="twelvedata")

        assert df.index.tolist() == ["2024-01-29"]

    def test_load_stored_ohlcv_does_not_leak_other_instruments(self, db):
        _seed(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id="twelvedata")

        assert len(df) == 3
        assert 400.0 not in df["open"].tolist(), "US:MSFT bars leaked into a US:AAPL read"

    def test_load_stored_ohlcv_without_provider_keeps_every_provider_row(self, db):
        _seed(db)

        df = _load_stored_ohlcv(SYMBOL, "2024-01-29", "2024-01-30", db, provider_id=None)

        # Unfiltered reads intentionally keep both providers, so 01-29 and 01-30
        # each appear twice. A session must therefore carry an explicit provider.
        assert len(df) == 4
        assert df.index.tolist().count("2024-01-29") == 2
        assert TWELVEDATA_OPEN + 2 not in df["open"].tolist(), "01-31 leaked past the end date"

    def test_get_stored_trade_dates_filters_by_provider(self, db):
        _seed(db)

        dates = _get_stored_trade_dates(SYMBOL, "2024-01-29", "2024-01-31", db, provider_id="twelvedata")

        assert dates == ["2024-01-29", "2024-01-30", "2024-01-31"]

    def test_get_stored_trade_dates_deduplicates_shared_dates(self, db):
        _seed(db)

        dates = _get_stored_trade_dates(SYMBOL, "2024-01-29", "2024-01-30", db, provider_id=None)

        # Both providers own 01-29 and 01-30; distinct() must collapse them into a
        # single trading calendar rather than a repeated one.
        assert dates == ["2024-01-29", "2024-01-30"]
        assert len(dates) == len(set(dates))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
