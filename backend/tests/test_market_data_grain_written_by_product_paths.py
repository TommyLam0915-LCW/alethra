"""The grain is declared by the product paths that write, not only by fixtures.

``market_data_bars`` keys a row on (instrument, provider, interval_minutes,
bar_timestamp), and the storage layer has refused a cross-grain write since
``test_market_data_grain_contract.py`` pinned that rule. That contract said
nothing about the callers: three product paths called ``store_bars`` with no
grain at all, so every row they cached was written unlabelled — the same shape
the 26 unexpected demo rows arrived in through
``GET /instruments/{id}/bars`` on a workspace page load.

Declaring the grain makes ``GrainConflictError`` reachable over HTTP for the
first time, and that is the second half of this file. The error is a
``RuntimeError``, so where nothing catches it the generic handler reports a
503 provider outage although the provider answered perfectly well — and on the
read route a refused cache write must not end the request at all.

Every case drives the real route or the real service function and reads the
rows back from the table. The undeclared control write below is what turns the
``1440`` readbacks into evidence rather than a tautology: the same storage
call, instrument and provider, with the grain left undeclared, leaves NULL.

Labelling those rows did not stop them arriving, and the read route caching a
series its own provider calls synthetic is the reason the 26 did. So the file
now covers both halves of what a product path may write: the grain it
declares, and whether it may write at all. ``data_nature`` is the only input
that separates ``LiveProvider`` from ``FakeProvider`` — same instrument, same
provider id, same three bars — so a grain case and a refusal case that differ
in nothing else say which of the two decides the write.
"""
import pytest

from app.models.market_data import MarketDataBar
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import (
    DAILY_INTERVAL_MINUTES,
    MarketDataStorage,
    SyntheticCacheWriteError,
    refuse_synthetic_cache_write,
)
from app.services.ml_training import _get_bars_with_storage
from tests.conftest import TestSession


INSTRUMENT = "US:AAPL"
PROVIDER = "demo"
DAYS = ["2024-01-02", "2024-01-03", "2024-01-04"]
PRICES = [150.0, 151.0, 152.0]
START, END = DAYS[0], DAYS[-1]
BARS_URL = f"/api/v1/instruments/{INSTRUMENT}/bars?start_date={START}&end_date={END}"
REFRESH_URL = f"/api/v1/instruments/{INSTRUMENT}/bars/refresh?start_date={START}&end_date={END}"


class FakeProvider:
    """A provider that answers normally, so any refusal seen is the route's own.

    It declares ``synthetic`` because that is what the demo default does, and
    the shape the 26 rows came in under. The grain cases below use
    ``LiveProvider`` instead, because a synthetic series is no longer written
    by these paths at all.
    """

    provider_name = "Demo"
    provider_id = PROVIDER
    feed_mode = "offline"
    is_live = False
    data_nature = "synthetic"

    def get_bars(self, instrument_id, start_date, end_date, timeframe="1d"):
        return [
            OHLCVBar(
                date=day,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price,
                volume=1_000_000,
            )
            for day, price in zip(DAYS, PRICES)
        ]

    def get_data_quality(self, instrument_id):
        return {
            "data_nature": self.data_nature,
            "adjustment": "not_applicable",
            "note": "Demo data",
            "note_zh": "演示數據",
        }

    def get_status(self):
        return {"health": "ok"}

    def is_available(self):
        return True


class LiveProvider(FakeProvider):
    """The same series, declared real — the only nature these routes still write."""

    data_nature = "live"


def _erase():
    """Start from no rows for this instrument, on either test backend.

    ``store_bars`` commits, and the PostgreSQL test harness leaves committed
    writes behind, so a readback that did not clear first would be counting
    another test's rows.
    """
    db = TestSession()
    try:
        db.query(MarketDataBar).filter(
            MarketDataBar.instrument_id == INSTRUMENT
        ).delete()
        db.commit()
    finally:
        db.close()


def _written_grains():
    """(date, grain) for every row this instrument's key space now holds."""
    db = TestSession()
    try:
        return sorted(
            (row.bar_date.isoformat(), row.interval_minutes)
            for row in db.query(MarketDataBar).filter(
                MarketDataBar.instrument_id == INSTRUMENT,
                MarketDataBar.provider_id == PROVIDER,
            ).all()
        )
    finally:
        db.close()


def _plant_unlabelled_series():
    """Write these days the way the pre-fix callers did: no grain declared.

    Doubles as the positive control — an HTTP case that reads back 1440 cannot
    have been satisfied by this call — and as the legacy dataset a
    grain-conflict is raised against.
    """
    db = TestSession()
    try:
        MarketDataStorage(db).store_bars(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bars=FakeProvider().get_bars(INSTRUMENT, START, END),
            data_nature="synthetic",
        )
    finally:
        db.close()
    return _written_grains()


@pytest.fixture
def unlabelled_series():
    _erase()
    planted = _plant_unlabelled_series()
    assert planted == [(day, None) for day in DAYS], planted
    yield planted


def test_an_undeclared_call_is_the_null_grain_the_fix_replaces():
    """The control: nothing on the column defaults it, and no fixture stamps it.

    If this reads back anything but NULL, the 1440 assertions below prove
    nothing about the routes — they would be reading a default.
    """
    _erase()

    assert _plant_unlabelled_series() == [(day, None) for day in DAYS]


def test_the_bars_get_writes_the_daily_grain(client, monkeypatch):
    """What this route does cache, it caches at a grain — the series is real."""
    _erase()
    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", LiveProvider
    )

    resp = client.get(BARS_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json()["bar_count"] == len(DAYS)
    assert "storage_persist_refused" not in resp.json()

    grains = _written_grains()
    assert grains == [(day, DAILY_INTERVAL_MINUTES) for day in DAYS], grains


def test_the_bars_get_serves_history_it_cannot_cache_and_says_so(
    client, monkeypatch, unlabelled_series
):
    """A refused cache write is neither an outage nor a re-stamp of the series.

    The rows on this key predate grain labelling. Re-stamping them would
    relabel a series some reader already selects as unlabelled, so the write is
    dropped, the bars still go out, and the response names the refusal.
    """
    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", LiveProvider
    )

    resp = client.get(BARS_URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bar_count"] == len(DAYS)
    assert "storage_persist_refused" in body
    refused = body["storage_persist_refused"]
    assert "grain" in refused and str(DAILY_INTERVAL_MINUTES) in refused, refused

    assert _written_grains() == unlabelled_series, "the refusal changed the rows"


def test_the_admin_refresh_writes_the_daily_grain(client, monkeypatch, admin_headers):
    """Here persisting is the service asked for, so the grain is the deliverable.

    The provider is the synthetic one the read route refuses: an operator
    naming storage is the difference, and it is the boundary that keeps this
    guard a scope rule rather than a ban on writing market data.
    """
    _erase()
    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", FakeProvider
    )

    resp = client.post(REFRESH_URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["bars_stored"] == len(DAYS)

    grains = _written_grains()
    assert grains == [(day, DAILY_INTERVAL_MINUTES) for day in DAYS], grains


def test_the_admin_refresh_refuses_a_grain_conflict_as_409_not_503(
    client, monkeypatch, admin_headers, unlabelled_series
):
    """The refusal must not be relabelled a provider outage by the outer handler.

    A 503 here sends an operator to check credentials and provider health while
    the dataset key is the whole problem, and it is exactly what an uncaught
    ``RuntimeError`` subclass produces.
    """
    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", FakeProvider
    )

    resp = client.post(REFRESH_URL, headers=admin_headers)
    assert resp.status_code == 409, resp.text
    assert resp.status_code != 503
    detail = resp.json()["detail"]
    assert detail["reason"] == "grain_conflict"
    assert detail["bars_stored"] == 0
    assert detail["instrument_id"] == INSTRUMENT

    assert _written_grains() == unlabelled_series, "the refused write touched rows"


def test_ml_training_provider_fallback_writes_the_daily_grain():
    """The third writer: a cache fill from the provider, not a re-read."""
    _erase()
    db = TestSession()
    try:
        bars, source, _nature = _get_bars_with_storage(
            db, LiveProvider(), INSTRUMENT, START, END
        )
    finally:
        db.close()

    assert source == "provider"
    assert len(bars) == len(DAYS)
    grains = _written_grains()
    assert grains == [(day, DAILY_INTERVAL_MINUTES) for day in DAYS], grains


def test_ml_training_cache_fill_leaves_a_legacy_series_alone(unlabelled_series):
    """A best-effort fill that is refused stays best-effort: no rows, no relabel.

    This call site swallows the error by design, so the only readable contract
    is the table — and it has to be the untouched table.
    """
    db = TestSession()
    try:
        _get_bars_with_storage(db, LiveProvider(), INSTRUMENT, START, END)
    finally:
        db.close()

    assert _written_grains() == unlabelled_series


def test_the_daily_grain_is_the_twenty_four_hour_one():
    """The routes write ``DAILY_INTERVAL_MINUTES``; this pins which number that is.

    A reader that filters ``interval_minutes == 1440`` and a writer that
    declares a different constant would both stay green without it.
    """
    assert DAILY_INTERVAL_MINUTES == 1440


def test_a_labelled_series_is_not_rewritten_as_unlabelled(client, monkeypatch):
    """The mirror of the control: the fixed route cannot produce the old rows.

    Plant a correctly labelled daily series, re-read it over HTTP, and the key
    space must still hold exactly one row per midnight at the daily grain —
    neither a second unlabelled row nor a re-stamped one.
    """
    _erase()
    db = TestSession()
    try:
        MarketDataStorage(db).store_bars(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bars=LiveProvider().get_bars(INSTRUMENT, START, END),
            data_nature="live",
            interval_minutes=DAILY_INTERVAL_MINUTES,
        )
    finally:
        db.close()
    before = _written_grains()
    assert before == [(day, DAILY_INTERVAL_MINUTES) for day in DAYS], before

    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", LiveProvider
    )
    resp = client.get(BARS_URL)
    assert resp.status_code == 200, resp.text
    assert "storage_persist_refused" not in resp.json()

    assert _written_grains() == before, "a re-read of its own series changed the rows"


# --- what these paths may write at all -------------------------------------
#
# The grain fix made the 26 rows legible. This is the fix that stops them
# arriving: the read route and the training cache-fill both persist as a side
# effect of a request that never mentioned storage, and the series they
# persisted is one their own provider calls synthetic.
#
# The positive controls are the cases above, which differ from these only in
# ``data_nature``: the get and the ml fallback both write through a
# live-declared provider, so the empty tables below are a verdict on nature and
# not a route that stopped storing. ``store_bars`` itself still takes a
# synthetic series, because the operator import scripts call it deliberately,
# and the admin refresh stores the very series the read route refuses, because
# an operator asked for it.

def test_the_bars_get_refuses_to_cache_what_its_provider_calls_synthetic(
    client, monkeypatch
):
    """A chart load is not a request to fill the history table.

    This is the route that seeded the 26 rows. The bars still go out — the
    caller asked to see a series, not to store one — and the refusal is in the
    response rather than only in a log line.
    """
    _erase()
    monkeypatch.setattr(
        "app.api.instruments.get_data_provider", FakeProvider
    )

    resp = client.get(BARS_URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bar_count"] == len(DAYS), "refusing the cache also withheld the chart"
    refused = body.get("storage_persist_refused")
    assert refused is not None, "the refusal was swallowed"
    assert "synthetic" in refused, refused

    assert _written_grains() == [], "synthetic bars were written anyway"


def test_ml_training_returns_synthetic_bars_without_caching_them():
    """Training on the demo feed is allowed; leaving demo history behind is not.

    ``source`` and ``nature`` are what the caller prices its provenance off, so
    the refusal must not change them into a claim that the run read storage.
    """
    _erase()
    db = TestSession()
    try:
        bars, source, nature = _get_bars_with_storage(
            db, FakeProvider(), INSTRUMENT, START, END
        )
    finally:
        db.close()

    assert len(bars) == len(DAYS)
    assert (source, nature) == ("provider", "synthetic")
    assert _written_grains() == []


def test_the_rule_reads_declared_nature_and_nothing_else():
    """Only a declared-synthetic series is refused — live, delayed, undeclared.

    ``unknown`` is the pass-through case worth naming: a provider that declares
    nothing still caches, and the row keeps ``data_nature="unknown"`` so it is
    traceable afterwards. That is the difference from the 26 rows, which were
    written under no grain at all and could not be told apart from real
    history by anything in the table.
    """
    for nature in ("live", "delayed", "unknown", None):
        refuse_synthetic_cache_write(
            nature, instrument_id=INSTRUMENT, provider_id=PROVIDER
        )

    with pytest.raises(SyntheticCacheWriteError) as exc:
        refuse_synthetic_cache_write(
            "synthetic", instrument_id=INSTRUMENT, provider_id=PROVIDER
        )
    assert "synthetic" in str(exc.value)
    assert INSTRUMENT in str(exc.value)
