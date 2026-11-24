"""Q4: a stored run names the rows it priced off, and can be re-checked later.

``research_runs.dataset_snapshot_id`` used to stay NULL while the engine put a
freshly minted ``uuid4`` in its result dict — an identifier written nowhere, so
no reader could follow it. The engine no longer invents one; instead the
terminal transaction of ``execute_backtest_run`` registers the stored series in
``datasets``/``dataset_snapshots`` under an identifier derived from the content
checksum, and stores that id on the run.

Two consequences are pinned here. The first is traceability: the id lands, it
repeats for the same rows, and it changes when the rows change. The second is
the reason a NULL is *not* an oversight — each unfilled case names itself in
``result_json.dataset_snapshot_reason``.

These bars are deterministic synthetic fixtures. The 184-row/two-grain shape is
the one that produced two checksums for one window in the isolated lane, so it
is reproduced here as a rule, and the lane measurement is separate evidence.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pandas as pd
import pytest

from app.engine.backtest import (
    _compute_stored_checksum,
    _stored_bars_to_dataframe,
    run_backtest,
)
from app.models.dataset import Dataset, DatasetSnapshot, SourceType
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.research_run import ResearchRun
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import DAILY_INTERVAL_MINUTES, MarketDataStorage
from app.services.research_runs import execute_backtest_run
from tests.conftest import TestSession

ASSET = "US:AAPL"
OTHER = "US:MSFT"
PROVIDER = "synthetic"
SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": [ASSET],
    "frequency": "1d",
    "start_date": "2024-01-02",
    "end_date": "2024-03-15",
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}
# The window the run declares, and the rows that fall inside it. The seeded
# series deliberately runs past ``end_date``, so every count below is taken
# from this pair rather than from the length of the table: a coverage number
# that equalled the whole table would prove nothing about the window.
WINDOW_START = date.fromisoformat(SPEC["start_date"])
WINDOW_END = date.fromisoformat(SPEC["end_date"])


def _in_window(days):
    return [day for day in days if WINDOW_START <= day <= WINDOW_END]


def _weekdays(start: date, count: int):
    days, cursor = [], start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, asset=ASSET, base_price=150.0):
    return [
        OHLCVBar(
            date=day,
            open=base_price + i,
            high=base_price + i + 2,
            low=base_price + i - 1,
            close=base_price + i + 1,
            volume=1_000_000,
        )
        for i, day in enumerate(days)
    ]


@pytest.fixture()
def stored_dataset():
    # 55 weekdays from 2024-01-02: the run's window closes on 2024-03-15, so
    # the series holds one bar the declared window does not price.
    days = _weekdays(date(2024, 1, 2), 55)
    db = TestSession()
    try:
        MarketDataStorage(db).store_bars(ASSET, PROVIDER, _bars(days), "synthetic")
        db.commit()
    finally:
        db.close()
    return days


@pytest.fixture()
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


def _create_experiment(client, headers, spec=None):
    resp = client.post(
        "/api/v1/experiments/",
        json={
            "title": "Provenance slice",
            "hypothesis": "A stored run can name the rows it priced.",
            "strategy_spec": spec or SPEC,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["current_version_id"])


def _objects(db, version_id):
    version = (
        db.query(ExperimentVersion).filter(ExperimentVersion.id == version_id).one()
    )
    experiment = (
        db.query(Experiment).filter(Experiment.id == version.experiment_id).one()
    )
    user = db.query(User).filter(User.email == "test@alethra.io").one()
    return user, version, experiment


def _run(db, client, auth_headers, *, data_source="stored", spec=None, version_id=None):
    """One real run through the customer's own durable lane."""
    version_id = version_id or _create_experiment(client, auth_headers, spec)
    user, version, experiment = _objects(db, version_id)
    return execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        data_source=data_source,
        provider_id=PROVIDER if data_source == "stored" else None,
        actor_type="test",
    )


def _readback(run_id):
    session = TestSession()
    try:
        run = session.query(ResearchRun).filter(ResearchRun.id == run_id).one()
        snapshot = (
            session.get(DatasetSnapshot, run.dataset_snapshot_id)
            if run.dataset_snapshot_id
            else None
        )
        dataset = (
            session.get(Dataset, snapshot.dataset_id) if snapshot is not None else None
        )
        return run, snapshot, dataset
    finally:
        session.close()


# -- the id now exists ------------------------------------------------------


def test_completed_stored_run_lands_a_snapshot_that_can_be_read_back(
    db, client, auth_headers, stored_dataset
):
    run = _run(db, client, auth_headers)
    assert run.status == "completed", run.error_details

    run_row, snapshot, dataset = _readback(run.id)

    assert run_row.dataset_snapshot_id is not None
    assert snapshot is not None, "the run names a snapshot row that does not exist"
    assert snapshot.checksum == run_row.dataset_checksum
    assert snapshot.dataset_id == dataset.id
    assert dataset.source_type == SourceType.venue_stored
    # The lineage row states its rights position instead of leaving a NULL that
    # a reader has to interpret. ``synthetic`` has no terms record at all, and
    # the row says that — it does not borrow another venue's clearance.
    assert dataset.license_status == (
        "TERMS_NOT_RECORDED;claim_basis=no_terms_record_for_this_provider"
    )
    # The locator addresses rows, not a file: nothing was exported to disk.
    assert snapshot.data_file_path.startswith("db:market_data_bars?provider=")
    assert PROVIDER in snapshot.data_file_path
    assert f"{SPEC['start_date']}..{SPEC['end_date']}" in snapshot.data_file_path
    assert f"instruments={ASSET}" in snapshot.missing_data_notes
    # The registry writes no adjustment finding, and the column cannot hold one:
    # its only reader renders it with ``bool()``, so an unknown and a False are
    # the same answer there. What distinguishes the two is the note.
    assert snapshot.corporate_actions_adjusted is False
    assert "not a finding" in snapshot.missing_data_notes
    assert run_row.result_json["dataset_snapshot_reason"] == "registered_stored_bars"


def test_the_engine_no_longer_invents_an_identifier(stored_dataset):
    result = run_backtest(
        SPEC,
        db_session=TestSession(),
        data_source="stored",
        provider_id=PROVIDER,
    )

    assert result["run_status"] == "completed", result["errors"]
    assert "dataset_snapshot_id" not in result, (
        "the engine returned an id that no table stores"
    )
    assert result["dataset_checksum"]


def test_the_same_stored_series_resolves_to_one_snapshot(
    db, client, auth_headers, stored_dataset
):
    first = _run(db, client, auth_headers)
    second = _run(db, client, auth_headers)

    assert first.dataset_snapshot_id == second.dataset_snapshot_id
    _, snapshot, dataset = _readback(first.id)
    session = TestSession()
    try:
        siblings = (
            session.query(DatasetSnapshot)
            .filter(DatasetSnapshot.dataset_id == dataset.id)
            .count()
        )
        datasets = session.query(Dataset).filter(Dataset.name.like("alethra:%")).count()
    finally:
        session.close()
    assert siblings == 1, "one series registered twice"
    assert datasets == 1


def test_an_edited_series_lands_on_a_different_snapshot(
    db, client, auth_headers, stored_dataset
):
    first = _run(db, client, auth_headers)

    # The edit goes through the session the runs themselves use: under SQLite a
    # second session shares this engine's single connection, so a commit from
    # elsewhere would not be an independent write of the rows.
    #
    # And it is taken on the last bar *inside the declared window*. The seeded
    # series runs past ``end_date``, and editing a bar the run never reads
    # changes nothing about the priced series — the same snapshot id is the
    # correct answer there, so it is the priced rows this test has to move.
    edited = (
        db.query(MarketDataBar)
        .filter(
            MarketDataBar.instrument_id == ASSET,
            MarketDataBar.bar_date <= WINDOW_END,
        )
        .order_by(MarketDataBar.bar_timestamp.desc())
        .first()
    )
    assert edited.bar_date == _in_window(stored_dataset)[-1]
    edited.close = float(edited.close) + 5.0
    db.commit()

    second = _run(db, client, auth_headers)

    assert second.dataset_checksum != first.dataset_checksum
    assert second.dataset_snapshot_id != first.dataset_snapshot_id
    first_row, first_snapshot, _ = _readback(first.id)
    assert first_row.result_json["dataset_checksum"] == first_snapshot.checksum


# -- why a NULL is not an oversight ----------------------------------------


def test_a_run_fetched_from_the_provider_names_that_instead_of_leaving_a_gap(
    db, client, auth_headers, stored_dataset
):
    run = _run(db, client, auth_headers, data_source="provider")

    assert run.status == "completed", run.error_details
    assert run.dataset_snapshot_id is None
    assert run.result_json["dataset_snapshot_reason"] == "provider_only"
    assert run.result_json["data_source_used"] == "provider"


def test_a_read_spanning_storage_and_the_provider_is_not_registered(
    db, client, auth_headers, stored_dataset
):
    # ``stored`` refuses this request outright (it is the Q3 contract, pinned in
    # test_strategy_research_stored_source), so the mixed read only exists on
    # the ``auto`` lane: one asset priced from rows this database holds, the
    # other from the provider's rolling window.
    spec = {**SPEC, "asset_ids": [ASSET, OTHER]}
    run = _run(db, client, auth_headers, data_source="auto", spec=spec)

    assert run.status == "completed", run.error_details
    assert run.result_json["data_source_used"] == "mixed"
    assert run.dataset_snapshot_id is None
    assert run.result_json["dataset_snapshot_reason"] == "mixed_sources_not_registered"


def test_a_run_that_was_never_published_leaves_no_snapshot(db, client, auth_headers):
    # No stored rows at all. ``execute_backtest_run`` does not raise for a
    # refusal: it writes the run off as failed in its own terminal transaction,
    # and the registry sits after the write that publishes an outcome — so a
    # failed run has nothing to register.
    version_id = _create_experiment(client, auth_headers)
    user, version, experiment = _objects(db, version_id)

    run = execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        data_source="stored",
        provider_id=PROVIDER,
        actor_type="test",
    )

    assert run.status == "failed"
    assert "No stored data" in (run.error_details or "")
    assert run.dataset_snapshot_id is None

    session = TestSession()
    try:
        assert session.query(DatasetSnapshot).count() == 0
        assert session.query(Dataset).count() == 0
    finally:
        session.close()


# -- what was asked, and what was read -------------------------------------


def test_a_durable_run_stores_the_source_it_was_asked_to_use(
    db, client, auth_headers, stored_dataset
):
    run = _run(db, client, auth_headers, data_source="stored")

    assert run.status == "completed", run.error_details
    row, _, _ = _readback(run.id)
    assert row.result_json["data_source_requested"] == "stored"
    assert row.result_json["data_source_used"] == "stored"


def test_auto_that_found_storage_is_not_recorded_as_storage_asked_for(
    db, client, auth_headers, stored_dataset
):
    """``used`` alone cannot tell a chosen source from a policy that landed there.

    Both runs price the same rows and both read as ``stored`` in
    ``data_source_used``, so the two records would be identical but for
    ``data_source_requested``: one caller asked for storage, the other asked the
    platform to decide and storage happened to cover it.
    """
    asked = _run(db, client, auth_headers, data_source="stored")
    settled = _run(db, client, auth_headers, data_source="auto")

    assert asked.status == "completed", asked.error_details
    assert settled.status == "completed", settled.error_details
    asked_row, _, _ = _readback(asked.id)
    settled_row, _, _ = _readback(settled.id)

    assert asked_row.result_json["data_source_used"] == "stored"
    assert settled_row.result_json["data_source_used"] == "stored"
    assert asked_row.result_json["data_source_requested"] == "stored"
    assert settled_row.result_json["data_source_requested"] == "auto"
    assert (
        asked_row.result_json["data_source_requested"]
        != settled_row.result_json["data_source_requested"]
    ), "the two different asks collapsed into one record"


def test_the_asked_for_value_keeps_the_policy_and_the_read_value_keeps_the_series(
    db, client, auth_headers, stored_dataset
):
    # One asset priced from rows this database holds, the other from the
    # provider — the only shape where the two halves cannot be the same word.
    spec = {**SPEC, "asset_ids": [ASSET, OTHER]}
    run = _run(db, client, auth_headers, data_source="auto", spec=spec)

    assert run.status == "completed", run.error_details
    row, _, _ = _readback(run.id)
    requested = row.result_json["data_source_requested"]
    used = row.result_json["data_source_used"]

    assert requested == "auto"
    assert used == "mixed"
    assert requested != used, "the row recorded the policy as if it were a series"


def test_a_refused_run_claims_neither_half_of_the_source_contract(
    db, client, auth_headers
):
    # No stored rows at all: the refusal is raised before any outcome is
    # published, so the run row holds no result payload to misread.
    run = _run(db, client, auth_headers, data_source="stored")

    assert run.status == "failed"
    row, _, _ = _readback(run.id)
    assert row.result_json is None, (
        "a run that priced nothing is storing a source for it"
    )


# -- the 184-rows-over-92-sessions case ------------------------------------


def _frame(rows):
    return pd.DataFrame(rows)


def _ohlcv(dates, asset=ASSET, base=150.0, offset=0.0):
    return [
        {
            "date": datetime.combine(day, datetime.min.time()),
            "open": base + i + offset,
            "high": base + i + 2 + offset,
            "low": base + i - 1 + offset,
            "close": base + i + 1 + offset,
        }
        for i, day in enumerate(dates)
    ]


def test_the_checksum_is_a_function_of_the_rows_not_of_the_order_they_arrive_in():
    rows = _ohlcv(_weekdays(date(2024, 1, 2), 55))
    ascending = _frame(rows)
    shuffled = _frame(list(reversed(rows)))
    rng = random.Random(7)
    out_of_order = rows[:]
    rng.shuffle(out_of_order)

    digests = {
        _compute_stored_checksum({"US:AAPL": frame}, ["US:AAPL"], "2024-01-02", "2024-03-15")
        for frame in (ascending, shuffled, _frame(out_of_order))
    }
    assert len(digests) == 1, f"one series, {len(digests)} checksums"


def test_the_order_fix_leaves_an_ascending_single_grain_digest_unchanged():
    """Byte-neutral for the digests already on record.

    The previous recipe joined the parts in frame order. For an ascending,
    one-grain series that order equals the sorted order, so a recorded digest
    still reproduces — only the frames that used to change digest when their
    rows arrived out of order now agree.
    """
    days = _weekdays(date(2024, 1, 2), 55)

    def legacy(frame):
        parts = [
            f"US:AAPL:{row['date'].strftime('%Y-%m-%d')}:{row['open']}:"
            f"{row['high']}:{row['low']}:{row['close']}"
            for _, row in frame.iterrows()
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    ascending = _frame(_ohlcv(days))
    assert _compute_stored_checksum(
        {"US:AAPL": ascending}, ["US:AAPL"], "2024-01-02", "2024-03-15"
    ) == legacy(ascending)

    # And the same window read with its rows reversed used to disagree: that is
    # the only way one stored series could ever have reported two checksums.
    assert legacy(_frame(list(reversed(_ohlcv(days))))) != legacy(ascending)


def test_two_grains_sharing_one_midnight_count_as_rows_not_sessions():
    # 92 trading days, one daily bar and one four-hour bar opened at each
    # midnight: 184 rows, 92 sessions. The old coverage block reported only
    # ``bar_count``, so 184 looked like a contradiction against a 92-day window.
    days = _weekdays(date(2024, 1, 2), 92)
    instances = []
    for i, day in enumerate(days):
        for grain, shift in ((DAILY_INTERVAL_MINUTES, 0.0), (240, 0.5)):
            instances.append(
                MarketDataBar(
                    instrument_id=ASSET,
                    provider_id=PROVIDER,
                    bar_date=day,
                    bar_timestamp=datetime.combine(day, datetime.min.time()),
                    interval_minutes=grain,
                    open=150.0 + i + shift,
                    high=152.0 + i + shift,
                    low=149.0 + i + shift,
                    close=151.0 + i + shift,
                )
            )
    random.Random(11).shuffle(instances)
    frame = _stored_bars_to_dataframe(instances)
    reversed_frame = _stored_bars_to_dataframe(list(reversed(instances)))

    assert len(frame) == 184
    assert frame["date"].nunique() == 92
    # The sort is total over each row's identity, so the price sequence the
    # engine walks does not depend on the order the rows arrived in.
    assert list(frame["close"]) == list(reversed_frame["close"])
    # One midnight's two rows are placed by grain, not by price: the finer
    # series sorts first, always. Sorting them by their own values would make
    # the sequence the engine trades a function of the prices themselves.
    assert list(frame["close"][:2]) == [151.5, 151.0]
    assert _compute_stored_checksum(
        {"US:AAPL": frame}, ["US:AAPL"], "2024-01-02", "2024-03-15"
    ) == _compute_stored_checksum(
        {"US:AAPL": reversed_frame}, ["US:AAPL"], "2024-01-02", "2024-03-15"
    )


def test_coverage_reports_rows_and_sessions_separately(
    db, client, auth_headers, stored_dataset
):
    priced = _in_window(stored_dataset)
    assert len(priced) < len(stored_dataset), (
        "the seed no longer runs past end_date, so this test can no longer "
        "tell a window-bounded count apart from a count of the whole table"
    )

    run = _run(db, client, auth_headers)

    coverage = run.result_json["dataset_coverage"][ASSET]
    assert coverage["source"] == "stored"
    assert coverage["provider_id"] == PROVIDER
    assert coverage["bar_count"] == len(priced)
    assert coverage["session_count"] == len(set(priced))
    assert coverage["data_nature"] == "synthetic"
    assert coverage["first_bar_date"] == priced[0].isoformat()
    # The bar stored past end_date is neither priced nor counted: the coverage
    # block describes the window the run declared, not the table it read.
    assert coverage["last_bar_date"] == priced[-1].isoformat()
    assert priced[-1] < stored_dataset[-1]
