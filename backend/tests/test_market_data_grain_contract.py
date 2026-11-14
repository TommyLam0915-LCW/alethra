"""The four-column bar key, one rule per write that could confuse two series.

``market_data_bars`` identifies a row by (instrument_id, provider_id,
interval_minutes, bar_timestamp), and a daily bar and a four-hour bar both open
at the same midnight. ``store_bars`` used to look a row up by three of those
four columns and take ``.first()``, so a 1440 write could land on a 240 row and
an unlabelled write could re-stamp a labelled series — with the winner picked by
whatever order the query happened to return.

Each test below is one clause of the grain contract, run against a real
(in-memory, synthetic) database. Refusals are judged by the rows left behind,
not by the exception text alone.
"""
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import (
    DAILY_INTERVAL_MINUTES,
    GrainConflictError,
    MarketDataStorage,
)

DAILY = DAILY_INTERVAL_MINUTES
FOUR_H = 240
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
PROVIDER = "kraken"
DAY_A = date(2026, 1, 5)
DAY_B = date(2026, 1, 6)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def storage(db_session):
    return MarketDataStorage(db_session)


def bar(day: date, price: float) -> OHLCVBar:
    return OHLCVBar(
        date=day.isoformat(),
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1000,
    )


def insert_raw(session, day: date, *, interval_minutes, price: float, provider=PROVIDER) -> None:
    """Put a row on the table without going through ``store_bars``."""
    session.add(MarketDataBar(
        instrument_id=INSTRUMENT,
        provider_id=provider,
        bar_date=day,
        bar_timestamp=datetime.combine(day, datetime.min.time()),
        interval_minutes=interval_minutes,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000,
    ))
    session.commit()


def rows_on(session, day: date, provider=PROVIDER):
    """(grain, close) for one provider's rows at that midnight, in a fixed order."""
    midnight = datetime.combine(day, datetime.min.time())
    found = session.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == INSTRUMENT,
        MarketDataBar.provider_id == provider,
        MarketDataBar.bar_timestamp == midnight,
    ).all()
    return sorted(
        ((row.interval_minutes, row.close) for row in found),
        key=lambda pair: (pair[0] is None, pair[0] or 0, pair[1]),
    )


class TestCoexistence:
    """Rule 1: 1440 and 240 on one timestamp are two rows, not two candidates."""

    def test_each_grain_keeps_its_own_row(self, storage, db_session):
        assert storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)],
                                  interval_minutes=DAILY) == 1
        assert storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 200.0)],
                                  interval_minutes=FOUR_H) == 1

        assert rows_on(db_session, DAY_A) == [(FOUR_H, 200.0), (DAILY, 100.0)]

    def test_writing_a_second_day_of_one_grain_does_not_disturb_the_other(
        self, storage, db_session
    ):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 200.0)], interval_minutes=FOUR_H)

        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_B, 210.0)], interval_minutes=FOUR_H)

        assert rows_on(db_session, DAY_A) == [(FOUR_H, 200.0), (DAILY, 100.0)]
        assert rows_on(db_session, DAY_B) == [(FOUR_H, 210.0)]


class TestReImport:
    """Rule 2: the same four columns again is an update, not a second row."""

    def test_same_key_updates_in_place(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 150.0)], interval_minutes=DAILY)

        assert rows_on(db_session, DAY_A) == [(DAILY, 150.0)]

    def test_legacy_re_import_updates_only_the_null_row(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)])
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 120.0)])

        assert rows_on(db_session, DAY_A) == [(None, 120.0)]


class TestRefusal:
    """Rules 3-5: a write never adopts, overwrites or re-stamps another grain's row."""

    def test_typed_write_refuses_an_unlabelled_row(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)])

        with pytest.raises(GrainConflictError, match="already holds grain \\[None\\]"):
            storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 150.0)],
                               interval_minutes=DAILY)

        assert rows_on(db_session, DAY_A) == [(None, 100.0)]

    def test_unlabelled_write_refuses_a_typed_row(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)

        with pytest.raises(GrainConflictError, match="already holds grain \\[1440\\]"):
            storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 150.0)])

        assert rows_on(db_session, DAY_A) == [(DAILY, 100.0)]

    def test_the_key_decides_the_conflict_not_the_instrument_alone(
        self, storage, db_session
    ):
        """A row is only in the way when it shares all four key columns. Another
        provider's row for the same midnight is a separate series and stays
        separate — a refusal that spilled over would be its own bug."""
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)
        storage.store_bars(INSTRUMENT, "twelvedata", [bar(DAY_A, 77.0)], interval_minutes=DAILY)

        assert rows_on(db_session, DAY_A) == [(DAILY, 100.0)]
        assert db_session.query(MarketDataBar).filter_by(provider_id="twelvedata").one().close == 77.0


class TestAmbiguousKey:
    """Rule 6: several rows in the grain this write owns is corruption, not a choice."""

    def test_unlabelled_write_refuses_two_null_rows_on_the_key(self, storage, db_session):
        """A unique constraint does not dedupe NULL grains, so two unlabelled
        rows on one midnight is a state the shipped table can really reach.
        Picking one would silently delete the other from the series."""
        insert_raw(db_session, DAY_A, interval_minutes=None, price=100.0)
        db_session.add(MarketDataBar(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=DAY_A,
            bar_timestamp=datetime.combine(DAY_A, datetime.min.time()),
            interval_minutes=None,
            open=111.0, high=111.0, low=111.0, close=111.0, volume=1000,
        ))
        db_session.commit()

        with pytest.raises(GrainConflictError, match="has 2 rows of grain None"):
            storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 500.0)])

        assert rows_on(db_session, DAY_A) == [(None, 100.0), (None, 111.0)]

    @pytest.mark.parametrize("grain", [DAILY, None], ids=["typed", "unlabelled"])
    def test_the_rule_is_to_refuse_not_to_pick_one(self, db_session, grain):
        """Two rows of the *same* labelled grain cannot be inserted while the
        unique constraint is enforced, so the storage-level test above covers the
        reachable case. The decision itself is pinned here for both grains,
        including the rows a legacy table without the constraint can hold."""
        held = [
            MarketDataBar(interval_minutes=grain, close=100.0),
            MarketDataBar(interval_minutes=grain, close=111.0),
        ]
        with pytest.raises(GrainConflictError, match=f"has 2 rows of grain {grain!r}"):
            MarketDataStorage._resolve_grain_row(
                held,
                interval_minutes=grain,
                instrument_id=INSTRUMENT,
                provider_id=PROVIDER,
                bar_timestamp=datetime.combine(DAY_A, datetime.min.time()),
            )


class TestAtomicBatch:
    """A conflict late in a batch must not leave the earlier bars already written."""

    def test_late_conflict_leaves_no_partial_update(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)
        insert_raw(db_session, DAY_B, interval_minutes=None, price=77.0)

        # DAY_A is writable by this grain; DAY_B is held by an unlabelled row.
        with pytest.raises(GrainConflictError):
            storage.store_bars(
                INSTRUMENT,
                PROVIDER,
                [bar(DAY_A, 500.0), bar(DAY_B, 500.0)],
                interval_minutes=DAILY,
            )

        # Read before querying: autoflush would write pending state and make an
        # empty session here mean nothing. Real unit-of-work state, not a
        # rollback() spy — there is nothing staged for a caller to commit.
        staged = (list(db_session.dirty), list(db_session.new))
        assert staged == ([], []), "a refused batch must not stage any row"

        assert rows_on(db_session, DAY_A) == [(DAILY, 100.0)], (
            "a refused batch must not have updated its writable first bar"
        )
        assert rows_on(db_session, DAY_B) == [(None, 77.0)]

    def test_conflict_does_not_advance_the_provenance_counters(self, storage, db_session):
        storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_A, 100.0)], interval_minutes=DAILY)
        insert_raw(db_session, DAY_B, interval_minutes=None, price=77.0)
        metadata = db_session.query(MarketDataMetadata).filter_by(instrument_id=INSTRUMENT).one()
        assert (metadata.total_bars, metadata.last_bar_date) == (1, DAY_A)

        with pytest.raises(GrainConflictError):
            storage.store_bars(INSTRUMENT, PROVIDER, [bar(DAY_B, 500.0)],
                               interval_minutes=DAILY)

        db_session.refresh(metadata)
        assert (metadata.total_bars, metadata.last_bar_date) == (1, DAY_A)
