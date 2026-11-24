"""The read-side half of the bar contract: one ordering, and three refusals.

``market_data_bars`` rows are identified by (instrument, provider, grain,
timestamp). The write side refuses to confuse two of those series; this file
holds the read side, which had no rule at all. Every reader used to say
``order_by(bar_date).first()`` — a key that is not a total order — so whenever
two rows qualified for one instant, the price that reached a fill, a position
mark or a P&L series was whichever row the database happened to return first.
That is not a policy, and it is not reproducible across engines or planner
versions.

The policy has three parts, and each is pinned by what it refuses as well as
by what it returns:

1. ``bar_order_keys`` is the only ordering a stored-bar read may use, so one
   series is the same sequence everywhere. ``_refuse_prior_order`` guards the
   entry to it, because ``Query.order_by()`` extends instead of replacing: a
   caller that sorted by ``bar_date`` first would keep its own non-total key in
   front and silently re-open the tie.
2. One instant that genuinely holds two different answers — two providers, or a
   daily and a four-hour bar — is legitimate for a series read and refused for
   a single-price read, because the second kind has to say which market or
   which series it means.
3. One instant that holds two rows with the *same* identity is a doubling the
   unique constraint cannot see while ``interval_minutes`` is NULL. Readers
   refuse it by row id and leave both rows in place: deleting the loser would
   destroy the evidence, and taking one would price the result on return order.
"""
from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models.market_data import MarketDataBar
from app.services.market_data_storage import (
    AmbiguousBarGrainError,
    AmbiguousBarProviderError,
    BarRowConflictError,
    MarketDataStorage,
    bar_row_conflicts,
    order_bars,
    pick_bar,
    refuse_bar_row_conflicts,
)

DAILY = 1440
FOUR_H = 240
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
KRAKEN = "kraken"
COINBASE = "coinbase"
DAY_A = date(2026, 1, 5)
DAY_B = date(2026, 1, 6)
DAY_C = date(2026, 1, 7)

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time())


def insert(
    session,
    day: date,
    price: float,
    *,
    provider: str = KRAKEN,
    grain: int | None = DAILY,
    instrument: str = INSTRUMENT,
) -> str:
    """Put one row on the table without going through any storage helper."""
    row = MarketDataBar(
        instrument_id=instrument,
        provider_id=provider,
        bar_date=day,
        bar_timestamp=midnight(day),
        interval_minutes=grain,
        open=price,
        high=price + 1.0,
        low=price - 1.0,
        close=price,
        volume=1000,
    )
    session.add(row)
    session.commit()
    return str(row.id)


def bars(session, *conditions):
    """The filtered, unsorted query every read helper in this file accepts."""
    query = session.query(MarketDataBar).filter(
        MarketDataBar.instrument_id == INSTRUMENT
    )
    for condition in conditions:
        query = query.filter(condition)
    return query


def series(session, *conditions):
    """What a wired series read does: canonical order, then the duplicate gate."""
    rows = order_bars(bars(session, *conditions)).all()
    return refuse_bar_row_conflicts(rows, reader="test_series")


def closes(rows):
    return [row.close for row in rows]


def identities(rows):
    return [
        (row.provider_id, row.interval_minutes, row.bar_timestamp, row.close)
        for row in rows
    ]


# -- 1. one ordering, everywhere --------------------------------------------


class TestCanonicalOrder:
    def test_the_series_follows_identity_not_insertion_order(self, db_session):
        # Seeded newest-first, so the natural return order runs opposite to the
        # policy: a reader that only looked sorted because the rows were
        # written in order would pass without this arrangement.
        insert(db_session, DAY_C, 300.0)
        insert(db_session, DAY_B, 200.0)
        insert(db_session, DAY_A, 100.0)

        assert closes(series(db_session)) == [100.0, 200.0, 300.0]

    def test_at_one_instant_provider_then_grain_then_row_id_decides(self, db_session):
        row_ids = [
            insert(db_session, DAY_A, 103.0, grain=FOUR_H),
            insert(db_session, DAY_A, 101.0, provider=COINBASE),
            insert(db_session, DAY_A, 102.0),
        ]

        ordered = series(db_session)

        assert identities(ordered) == [
            (COINBASE, DAILY, midnight(DAY_A), 101.0),
            (KRAKEN, FOUR_H, midnight(DAY_A), 103.0),
            (KRAKEN, DAILY, midnight(DAY_A), 102.0),
        ]
        # The seed order was 103/101/102, so the sequence above is neither the
        # insertion order nor a walk of the row ids.
        assert {str(row.id) for row in ordered} == set(row_ids)

    def test_an_unlabelled_grain_sorts_last_at_its_instant(self, db_session):
        insert(db_session, DAY_A, 105.0, grain=None)
        insert(db_session, DAY_A, 100.0, grain=DAILY)

        # NULL grains sort last on purpose: SQLite and PostgreSQL disagree
        # where NULLs land, and a series whose order depends on the driver is
        # not deterministic.
        assert closes(series(db_session)) == [100.0, 105.0]

    def test_get_bars_and_a_direct_ordered_query_agree(self, db_session):
        for day, price in ((DAY_C, 300.0), (DAY_A, 100.0), (DAY_B, 200.0)):
            insert(db_session, day, price)

        from_storage = MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN)

        assert identities(from_storage) == identities(series(db_session))

    def test_a_limit_keeps_the_newest_rows_and_still_ascending(self, db_session):
        insert(db_session, DAY_A, 100.0)
        insert(db_session, DAY_B, 200.0)
        insert(db_session, DAY_C, 300.0)

        window = MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN, limit=2)

        # Truncating the head of the range would drop the bar every caller
        # reads as the latest price.
        assert closes(window) == [200.0, 300.0]

    def test_a_limit_does_not_widen_for_a_second_grain_at_the_cut(self, db_session):
        insert(db_session, DAY_A, 100.0, grain=DAILY)
        insert(db_session, DAY_A, 120.0, grain=FOUR_H)
        insert(db_session, DAY_B, 200.0, grain=DAILY)

        window = MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN, limit=1)

        assert closes(window) == [200.0]


class TestPriorOrderIsRefused:
    """``Query.order_by()`` extends, so a caller-side sort wins by position."""

    def test_order_bars_refuses_a_query_that_already_sorted(self, db_session):
        insert(db_session, DAY_A, 100.0)
        pre_sorted = bars(db_session).order_by(MarketDataBar.bar_date)

        with pytest.raises(ValueError, match="already chose an ordering") as caught:
            order_bars(pre_sorted)

        assert "order_bars" in str(caught.value)

    def test_pick_bar_refuses_a_query_that_already_sorted(self, db_session):
        insert(db_session, DAY_A, 100.0)
        pre_sorted = bars(db_session).order_by(MarketDataBar.close.desc())

        with pytest.raises(ValueError, match="already chose an ordering") as caught:
            pick_bar(pre_sorted, reader="test_site")

        assert "pick_bar" in str(caught.value)

    def test_the_same_query_passes_when_it_carries_only_filters(self, db_session):
        """Positive control: the refusals above are not a broken helper."""
        insert(db_session, DAY_A, 100.0)

        assert pick_bar(bars(db_session), reader="test_site").close == 100.0
        assert len(order_bars(bars(db_session)).all()) == 1


# -- 2. two answers at one instant ------------------------------------------


class TestAmbiguousSinglePrice:
    """A series may hold both; a fill price has to be one of them."""

    def test_two_providers_at_one_instant_are_two_rows_of_a_series(self, db_session):
        insert(db_session, DAY_A, 100.0, provider=KRAKEN)
        insert(db_session, DAY_A, 104.0, provider=COINBASE)

        # coinbase sorts ahead of kraken, so the sequence is the policy's and
        # not the order the rows were written in.
        assert closes(series(db_session)) == [104.0, 100.0]

    def test_two_providers_refuse_a_single_bar_price_read(self, db_session):
        insert(db_session, DAY_A, 100.0, provider=KRAKEN)
        insert(db_session, DAY_A, 104.0, provider=COINBASE)

        with pytest.raises(AmbiguousBarProviderError) as caught:
            pick_bar(bars(db_session), reader="value_open_positions")

        assert caught.value.reason == "ambiguous_bar_providers"
        assert KRAKEN in str(caught.value) and COINBASE in str(caught.value)

    def test_binding_a_provider_resolves_the_price(self, db_session):
        insert(db_session, DAY_A, 100.0, provider=KRAKEN)
        insert(db_session, DAY_A, 104.0, provider=COINBASE)

        picked = pick_bar(
            bars(db_session, MarketDataBar.provider_id == COINBASE),
            reader="value_open_positions",
        )

        assert picked.close == 104.0

    def test_daily_and_four_hour_at_one_midnight_refuse_a_single_price(self, db_session):
        insert(db_session, DAY_A, 100.0, grain=DAILY)
        insert(db_session, DAY_A, 120.0, grain=FOUR_H)

        with pytest.raises(AmbiguousBarGrainError) as caught:
            pick_bar(bars(db_session), reader="next_execution_window", newest=False)

        assert caught.value.reason == "ambiguous_bar_grains"
        # Neither close was taken, so nothing downstream can price off the
        # finer grain by default: the message names both, the result is empty.
        assert "1440" in str(caught.value) and "240" in str(caught.value)

    def test_binding_a_grain_resolves_the_price(self, db_session):
        insert(db_session, DAY_A, 100.0, grain=DAILY)
        insert(db_session, DAY_A, 120.0, grain=FOUR_H)

        picked = pick_bar(
            bars(db_session, MarketDataBar.interval_minutes == FOUR_H),
            reader="next_execution_window",
        )

        assert picked.close == 120.0

    def test_two_instants_of_two_grains_are_not_one_ambiguous_instant(
        self, db_session
    ):
        insert(db_session, DAY_A, 100.0, grain=DAILY)
        insert(db_session, DAY_B, 120.0, grain=FOUR_H)

        assert pick_bar(bars(db_session), reader="next_execution_window").close == 120.0


# -- 3. one identity doubled ------------------------------------------------


def _double(db_session, price_a=100.0, price_b=111.0):
    """Two rows, one identity, NULL grain: the doubling the constraint misses."""
    first = insert(db_session, DAY_A, price_a, grain=None)
    second = insert(db_session, DAY_A, price_b, grain=None)
    return first, second


class TestDuplicateRows:
    def test_only_an_unlabelled_grain_can_hold_two_rows_for_one_instant(
        self, db_session
    ):
        _double(db_session)

        assert (
            db_session.query(MarketDataBar)
            .filter(MarketDataBar.bar_timestamp == midnight(DAY_A))
            .count()
            == 2
        )

        insert(db_session, DAY_B, 200.0, grain=DAILY)
        with pytest.raises(IntegrityError):
            insert(db_session, DAY_B, 210.0, grain=DAILY)
        db_session.rollback()

        # The labelled half of the window closes the hole: a doubling is only
        # reachable through NULL grain, which is what the seed lane's
        # ``US:AAPL``/``demo`` rows are.
        assert (
            db_session.query(MarketDataBar)
            .filter(MarketDataBar.bar_timestamp == midnight(DAY_B))
            .count()
            == 1
        )

    def test_a_series_read_refuses_and_names_both_rows(self, db_session):
        first, second = _double(db_session)

        with pytest.raises(BarRowConflictError) as caught:
            series(db_session)

        assert caught.value.reason == "duplicate_bar_rows"
        message = str(caught.value)
        assert first in message and second in message
        assert caught.value.conflicts[0][1] == sorted([first, second])

    def test_get_bars_refuses_the_duplicate(self, db_session):
        _double(db_session)

        with pytest.raises(BarRowConflictError):
            MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN)

    def test_a_limit_refuses_a_duplicate_split_by_the_cut(self, db_session):
        """The window alone would show one row and look like a clean series."""
        _double(db_session)
        insert(db_session, DAY_B, 200.0)

        with pytest.raises(BarRowConflictError):
            MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN, limit=2)

    def test_a_single_bar_price_read_refuses_the_duplicate(self, db_session):
        _double(db_session)

        with pytest.raises(BarRowConflictError):
            pick_bar(bars(db_session), reader="_observed_bar_close", newest=False)

    def test_the_newest_row_is_not_taken_along_with_the_refusal(self, db_session):
        """``pick_bar`` must refuse before returning, not after pricing."""
        _double(db_session)

        with pytest.raises(BarRowConflictError):
            pick_bar(bars(db_session), reader="value_open_positions")

    def test_refusing_deletes_nothing(self, db_session):
        first, second = _double(db_session)

        for _ in range(2):
            with pytest.raises(BarRowConflictError):
                pick_bar(bars(db_session), reader="value_open_positions")
            with pytest.raises(BarRowConflictError):
                series(db_session)
            with pytest.raises(BarRowConflictError):
                MarketDataStorage(db_session).get_bars(INSTRUMENT, KRAKEN, limit=2)

        survivors = db_session.query(MarketDataBar).all()
        assert {str(row.id) for row in survivors} == {first, second}
        assert sorted(row.close for row in survivors) == [100.0, 111.0]

    def test_two_grains_at_one_instant_are_not_a_duplicate(self, db_session):
        insert(db_session, DAY_A, 100.0, grain=DAILY)
        insert(db_session, DAY_A, 120.0, grain=FOUR_H)

        # The finer grain sorts first, and both rows come back: two series,
        # two answers, no conflict.
        assert closes(series(db_session)) == [120.0, 100.0]
        assert bar_row_conflicts(series(db_session)) == []

    def test_the_refusal_returns_the_caller_s_rows_untouched(self, db_session):
        insert(db_session, DAY_A, 100.0)
        insert(db_session, DAY_B, 200.0)
        rows = series(db_session)

        assert refuse_bar_row_conflicts(rows, reader="test_site") is rows

    def test_the_grain_column_name_is_the_caller_s_choice(self):
        """``grain_attr`` is the seam to the intraday table's ``timeframe``.

        That table spells the same concept under another column and keeps it
        NOT NULL, so the identity check has to be told which attribute to read;
        reading the wrong one would call every intraday instant a NULL-grain
        collision.
        """
        from types import SimpleNamespace

        instant = midnight(DAY_A)

        def row(index: int, timeframe: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=f"r{index}",
                instrument_id=INSTRUMENT,
                provider_id=KRAKEN,
                timeframe=timeframe,
                bar_timestamp=midnight(DAY_A),
            )

        doubled = [row(1, 60), row(2, 60)]
        assert [key for key, _ in bar_row_conflicts(doubled, grain_attr="timeframe")] == [
            (INSTRUMENT, KRAKEN, 60, midnight(DAY_A))
        ]
        assert bar_row_conflicts(
            [row(1, 15), row(2, 60)], grain_attr="timeframe"
        ) == []


# -- 4. the ordering rule as a source-level gate ----------------------------

PRICE_ATTRS = {"open", "high", "low", "close", "volume", "volume_decimal"}
BAR_MODELS = {"MarketDataBar", "IntradayMarketDataBar"}


def _calls_named(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == name:
                yield node


def _bar_attrs(node: ast.Call) -> set:
    """``Model.attr`` names mentioned in one call, for the bar models only."""
    found = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id in BAR_MODELS
        ):
            found.add(sub.attr)
    return found


def _literal_names(node: ast.Call) -> set:
    """String arguments and string-list entries of one call."""
    found = set()
    for arg in node.args:
        items = [arg]
        if isinstance(arg, (ast.List, ast.Tuple)):
            items = arg.elts
        for item in items:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                found.add(item.value)
    return found


def _sort_sites(root: Path):
    """Every sort in ``root`` that names a bar model or a stored-bar key.

    Returned whether or not it offends, because a refusal count of zero only
    means something next to the number of sites the walk actually opened.
    """
    sites = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _calls_named(tree, "order_by"):
            attrs = _bar_attrs(call)
            if attrs:
                sites.append((path, call.lineno, "order_by", attrs))
        if path.parent.name == "engine" and path.name == "backtest.py":
            for call in _calls_named(tree, "sort_values"):
                keys = _literal_names(call)
                if keys & {"date", "_provider_id", "_interval_minutes", "_row_id"}:
                    sites.append((path, call.lineno, "sort_values", keys))
    return sites


def _price_order_offenders(root: Path):
    """Reads that put an OHLC value ahead of the identity keys in a sort.

    Two shapes count: a SQL ``order_by(MarketDataBar.close...)`` and a pandas
    ``sort_values([... "close" ...])`` over stored bars. The second is the
    shape the backtest engine used to break a one-midnight tie by price, so
    the gate has to see it as well as the first.
    """
    return [
        f"{path.relative_to(root.parent)}:{lineno} {kind} {sorted(attrs & PRICE_ATTRS)}"
        for path, lineno, kind, attrs in _sort_sites(root)
        if attrs & PRICE_ATTRS
    ]


class TestNoPriceColumnSort:
    def test_no_production_read_orders_stored_bars_by_a_price(self):
        sites = _sort_sites(BACKEND / "app")
        offenders = _price_order_offenders(BACKEND / "app")

        # Coverage first: the walk has to be looking at real sort sites, or a
        # zero offender list would only report that it found nothing to read.
        assert len(sites) >= 3, f"the gate scanned {len(sites)} sort sites"
        assert offenders == [], (
            "a stored-bar read priced its result on an OHLC value; the "
            "ordering policy is bar_order_keys(): " + "; ".join(offenders)
        )

    def test_the_gate_fires_on_a_sql_price_sort(self, tmp_path):
        """Positive control: the same walk over source that does offend."""
        bad = tmp_path / "app" / "reader.py"
        bad.parent.mkdir(parents=True)
        bad.write_text(
            "from app.models.market_data import MarketDataBar\n"
            "row = q.order_by(MarketDataBar.close.desc()).first()\n",
            encoding="utf-8",
        )

        offenders = _price_order_offenders(tmp_path / "app")

        assert len(offenders) == 1 and "close" in offenders[0]

    def test_the_gate_fires_on_a_pandas_price_sort_in_the_engine(self, tmp_path):
        bad = tmp_path / "engine" / "backtest.py"
        bad.parent.mkdir(parents=True)
        bad.write_text(
            "df = df.sort_values(['date', 'close'], kind='mergesort')\n",
            encoding="utf-8",
        )

        offenders = _price_order_offenders(tmp_path)

        assert len(offenders) == 1 and "close" in offenders[0]

    def test_the_gate_ignores_sorts_it_is_not_about(self, tmp_path):
        root = tmp_path / "app"
        (root / "engine").mkdir(parents=True)
        (root / "reader.py").write_text(
            "from app.models.market_data import MarketDataBar\n"
            "rows = q.order_by(MarketDataBar.bar_timestamp.desc()).all()\n"
            "other = q2.order_by(User.email).all()\n",
            encoding="utf-8",
        )
        (root / "engine" / "backtest.py").write_text(
            "df = df.sort_values('date').reset_index(drop=True)\n",
            encoding="utf-8",
        )

        assert _price_order_offenders(root) == []

    def test_the_engine_orders_stored_rows_by_identity_not_arrival(self):
        """The loader's row order, measured on rows handed to it shuffled.

        This family used to read ``DataFrame.sort_values`` out of the source it
        ships with. The engine no longer asks pandas to sort: a grain column
        that mixes ``None`` with integers makes pandas' lexsort raise
        ``TypeError: 'values' is not ordered``, so the ordering moved into
        ``bar_sort_key`` and the claim worth pinning is the behaviour — one
        instant's three grains come out in the policy's order whoever fed them
        in that order.
        """
        from types import SimpleNamespace

        from app.engine.backtest import _stored_bars_to_dataframe

        def row(index: int, day: date, price: float, grain):
            return SimpleNamespace(
                id=f"r{index}",
                bar_date=day,
                bar_timestamp=midnight(day),
                provider_id=KRAKEN,
                interval_minutes=grain,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
            )

        planted = [
            row(1, DAY_A, 102.0, DAILY),
            row(2, DAY_B, 200.0, DAILY),
            row(3, DAY_A, 103.0, FOUR_H),
            row(4, DAY_A, 105.0, None),
        ]
        # Finer grain first at one instant, the unlabelled row last, and prices
        # never consulted: 105.0 is neither cheapest nor dearest.
        expected = [103.0, 102.0, 105.0, 200.0]

        assert list(_stored_bars_to_dataframe(planted)["close"]) == expected
        assert list(_stored_bars_to_dataframe(list(reversed(planted)))["close"]) == expected
