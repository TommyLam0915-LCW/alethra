"""The seed input card B was missing, tested on the isolated SQLite lane only.

Round 22 item 3 asks for a *thing an operator can approve*: the decided synthetic
material (205 confirmed decisions with their sessions and labels, 140 daily bars
for each of two instruments) as a bounded plan, a writer that does nothing until
told, and a readiness answer that names the business keys this window still owes.
The shape is not invented here — it is the fixture this repository has used since
the personal-learning lanes were written, lifted out of a pytest process and made
inspectable, with the corrected ceilings §16.4 records (a session per decision,
so 205 and not 41).

What the nodes below refuse to do:

* **they press no consent on anyone's behalf.** Two nodes grant consents inside
  this process's own temporary database, for branch evidence about the readiness
  report, exactly as ``tests/test_daily_estimate_prep_readiness.py`` does; the
  tool under test writes zero consent rows on every path, and the nodes that
  claim that count the rows themselves.
* **they run no tick.** ``assess_readiness`` is read before anything is billed,
  ``natural_tick_observed`` stays False, and no node calls
  ``POST …/daily-estimate/run``.
* **they touch no PostgreSQL.** The non-SQLite node builds an engine object it
  never connects to, to exercise the branch that refuses such a target.

The target checks are the load-bearing part: a prep writer that creates its own
database, or lands its bars on top of the ones a fixture already wrote, turns an
approval of "the seed" into a write nobody approved. Each of those has its own
node here, each beside the case where the write is allowed.
"""

import json
import sys
import uuid
from datetime import date, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from app.models.billing import UsageRecord
from app.models.decision_label import DecisionLabel
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.models.model_research import ModelVersion
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.personalization import PersonalizationConsent
from app.models.quota import QuotaCounter
from app.models.simulation import SimulationSession
from app.models.training import TrainingJob
from app.models.user import User
from app.services.daily_estimates import DAILY_SERVICE_VIA, estimate_window
from app.services.decision_labels import record_label
from tests.conftest import TestSession
from tests.test_daily_estimate_prep_readiness import (
    SECOND_INSTRUMENT,
    WINDOW_INSTANT,
    _grant_daily,
    _units,
)
from tests.test_personal_model_comparison import _model_for_job, _start_training
from tests.test_personal_strategy_learning import (
    BARS,
    INSTRUMENT,
    _build_snapshot,
    _create_experiment,
    _grant_consent,
    _open_skill_unit,
    _personal_artifacts,
    _seed_bars,
    _seed_confirmed_decisions,
    _select_for_skill,
    _user_id,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
# ``scripts/`` is not a package. The path goes in for the import and comes back
# out immediately — leaving it at sys.path[0] would redirect every later plain
# ``import X`` in the session into this directory.
sys.path.insert(0, str(BACKEND_DIR / "scripts"))
import card_b_seed as seed  # noqa: E402
sys.path.remove(str(BACKEND_DIR / "scripts"))

LOCK_REASON = "測試：凍結策略版本，作為卡 B 種子輸入的歸屬"
TOTAL_ROWS = sum(seed.CAPS.values())  # 897 across the five tables


def _db():
    return TestSession()


def _target_file():
    """The SQLite file this process's own test database is bound to."""
    db = _db()
    try:
        return db.get_bind().url.database
    finally:
        db.close()


def _rows_in_target():
    """Every row in the five tables the seed writes, counted across the target."""
    db = _db()
    try:
        return seed._table_counts(db)
    finally:
        db.close()


def _frozen_version(client, auth_headers, title="Card B seed"):
    """An experiment plus a locked version, through the formal doors."""
    owner_id = _user_id()
    experiment_id, strategy_version_id = _create_experiment(
        client, auth_headers, title=title
    )
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": LOCK_REASON},
    )
    assert locked.status_code == 200, locked.text
    return owner_id, strategy_version_id


def _consent_purposes(owner_id):
    db = _db()
    try:
        return {
            row.purpose
            for row in db.query(PersonalizationConsent)
            .filter(PersonalizationConsent.owner_id == owner_id)
            .all()
        }
    finally:
        db.close()


def _daily_bills(owner_id):
    """The billing rows the daily lane wrote, and only those."""
    db = _db()
    try:
        return [
            row
            for row in db.query(UsageRecord)
            .filter(UsageRecord.user_id == owner_id)
            .all()
            if (row.metadata_json or {}).get("via") == DAILY_SERVICE_VIA
        ]
    finally:
        db.close()


# ── the plan: bounded, reproducible, and not a write ─────────────────────────


def test_the_plan_is_bounded_deterministic_and_writes_nothing(setup_db):
    """The whole input readable at once: 280/2/205/205/205 and one digest.

    The counts are the corrected ones §16.4 records — a session per decision, so
    205 sessions and not 41 — and they are measured against the ceiling rather
    than asserted beside it. ``build_plan`` then has to leave the target exactly
    as it found it: a plan an operator can approve is only useful if reading it
    cannot itself be the write.
    """
    owner_id = uuid.uuid4()
    version_id = uuid.uuid4()
    first = seed.build_plan(owner_id, version_id)
    second = seed.build_plan(uuid.UUID(str(owner_id)), uuid.UUID(str(version_id)))

    assert first["counts"] == {
        "market_data_bars": 280,
        "market_data_metadata": 2,
        "simulation_sessions": 205,
        "human_decision_events": 205,
        "decision_labels": 205,
    }, first["counts"]
    assert first["caps"] == seed.CAPS
    assert first["within_caps"] is True, first["over_caps"]
    assert first["over_caps"] == {}
    assert first["written"] is False
    assert first["rows_sha256"] == second["rows_sha256"]
    assert first["rows_sha256"] != seed.build_plan(
        uuid.uuid4(), version_id
    )["rows_sha256"], "the digest must follow the owner it is planned for"

    # Deterministic identity, so a retry collides with its own rows — unlike the
    # fixture this replaces, whose idempotency keys were ``uuid4().hex``.
    assert [r["id"] for r in first["rows"]["human_decision_events"]] == [
        r["id"] for r in second["rows"]["human_decision_events"]
    ]
    keys = [r["idempotency_key"] for r in first["rows"]["human_decision_events"]]
    assert len(set(keys)) == 205
    assert all(key.startswith(seed.SEED_VERSION) for key in keys)
    assert seed.business_days(date(2024, 1, 1), 3) == [
        date(2024, 1, 1),
        date(2024, 1, 2),
        date(2024, 1, 3),
    ]

    assert all(count == 0 for count in _rows_in_target().values()), _rows_in_target()


def test_the_bar_window_and_the_decision_days_come_from_one_calendar(setup_db):
    """Two instruments, 140 consecutive weekdays each, decisions inside that span.

    The plan's dates and the rows' dates have to be the same measured calendar: a
    140-bar window whose last bar is not one of the rows is a window the
    freshness read will refuse to see.
    """
    plan = seed.build_plan(uuid.uuid4(), uuid.uuid4())
    by_instrument = {}
    for row in plan["rows"]["market_data_bars"]:
        by_instrument.setdefault(row["instrument_id"], []).append(row["bar_date"])

    assert sorted(by_instrument) == ["US:AAPL", "US:MSFT"]
    expected = [day.isoformat() for day in BARS]
    for instrument, days in by_instrument.items():
        assert len(days) == 140, (instrument, len(days))
        assert days == expected, instrument
        assert days == sorted(days)
        assert all(date.fromisoformat(d).weekday() < 5 for d in days)

    aapl_days = set(by_instrument["US:AAPL"])
    decision_days = sorted(
        {row["server_snapshot_date"] for row in plan["rows"]["human_decision_events"]}
    )
    assert len(decision_days) == 41, len(decision_days)
    assert set(decision_days) <= aapl_days, set(decision_days) - aapl_days
    assert decision_days[0] == BARS[30].isoformat()
    assert decision_days[-1] == BARS[30 + 40 * 2].isoformat()
    # The second instrument carries bars only: spreading 205 decisions over it
    # would be a different fixture than the settled one this tool is a copy of.
    assert {
        row["server_instrument_id"] for row in plan["rows"]["human_decision_events"]
    } == {"US:AAPL"}
    # One session per decision, which is the §16.4 correction in row form.
    sessions = plan["rows"]["simulation_sessions"]
    assert len(sessions) == 205
    assert len({row["id"] for row in sessions}) == 205


def test_the_writer_is_inert_until_the_operator_passes_execute(
    setup_db, client, auth_headers
):
    """Without ``execute`` the receipt says so and the target is unchanged.

    The flag is the whole difference between a review and a write, so the node
    runs both halves: the same plan inert leaves every count where it found it,
    and the same plan again with the flag writes all 897 rows.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    before = _rows_in_target()
    assert all(count == 0 for count in before.values()), before

    db = _db()
    try:
        receipt = seed.write_seed(db, plan)
        assert receipt["written"] == 0
        assert receipt["reason"] == "inert_by_default"
        assert receipt["executed"] is False
        assert receipt["caps"] == seed.CAPS
    finally:
        db.close()
    assert _rows_in_target() == before, "the inert path wrote something"

    db = _db()
    try:
        written = seed.write_seed(db, plan, execute=True)
    finally:
        db.close()
    assert written["reason"] == "written"
    assert written["written"] == TOTAL_ROWS == sum(plan["counts"].values())
    assert written["seed_rows_read_back"] == plan["counts"], written
    assert written["target_table_counts"] == plan["counts"]
    assert written["consent_rows_written"] == 0
    assert _rows_in_target() == plan["counts"], _rows_in_target()


def test_every_seed_row_says_it_is_a_seed(setup_db, client, auth_headers):
    """Provenance written into the columns that exist, not into a comment.

    ``actor_type`` is CHECK-constrained to ``human|agent|unknown`` and ``human``
    means a password-authenticated owner session. This tool has no such session,
    so all 615 decision-side rows have to read ``unknown`` and carry the marker;
    the series says ``synthetic`` in the two places the schema records that.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    db = _db()
    try:
        seed.write_seed(db, plan, execute=True)
    finally:
        db.close()

    db = _db()
    try:
        events = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.owner_id == owner_id)
            .all()
        )
        assert len(events) == 205, len(events)
        assert {e.actor_type for e in events} == {"unknown"}
        assert {e.execution_status.value for e in events} == {"record_only"}
        assert {e.server_strategy_version_id for e in events} == {
            uuid.UUID(strategy_version_id)
        }
        assert {e.linked_inference_id for e in events} == {None}
        assert {(e.metadata_json or {}).get("seed_version") for e in events} == {
            seed.SEED_VERSION
        }
        assert all((e.metadata_json or {}).get("owner_confirmed") is False for e in events)

        labels = db.query(DecisionLabel).all()
        assert len(labels) == 205, len(labels)
        assert {row.actor_type for row in labels} == {"unknown"}
        assert {row.state.value for row in labels} == {"confirmed"}
        # The review service's own fields survive the stamp: the label is the
        # service's record with the marker added, not a row the tool built.
        assert all((row.metadata_json or {}).get("recorded_action") for row in labels)
        assert all(
            (row.metadata_json or {}).get("nature") == "synthetic_prep_seed"
            for row in labels
        )
        assert {row.revision_number for row in labels} == {1}

        sessions = db.query(SimulationSession).all()
        assert len(sessions) == 205, len(sessions)
        assert {s.provider_id for s in sessions} == {"synthetic"}
        assert {s.owner_id for s in sessions} == {owner_id}

        meta = db.query(MarketDataMetadata).all()
        assert len(meta) == 2, meta
        assert {m.data_nature for m in meta} == {"synthetic"}
        assert {m.provider_id for m in meta} == {"synthetic"}
        for row in meta:
            assert row.provider_note and seed.SEED_VERSION in row.provider_note
            assert "not market history" in row.provider_note

        bars = db.query(MarketDataBar).all()
        assert len(bars) == 280
        assert {b.provider_id for b in bars} == {"synthetic"}
        assert {b.instrument_id for b in bars} == {"US:AAPL", "US:MSFT"}
    finally:
        db.close()


def test_a_real_owner_confirmation_still_reads_as_different(setup_db, client, auth_headers):
    """The separation the brief asks for, measured on one table.

    A record the owner actually confirmed and a prep seed have to be tellable
    apart afterwards, or the seed would be indistinguishable from training
    evidence. So the node writes the seed, appends one decision with
    ``actor_type="human"`` through the same review service the review door uses,
    and counts the two populations.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    db = _db()
    try:
        seed.write_seed(db, plan, execute=True)
    finally:
        db.close()

    db = _db()
    try:
        author = db.query(User).filter(User.id == owner_id).one()
        version = (
            db.query(ExperimentVersion)
            .filter(ExperimentVersion.id == uuid.UUID(strategy_version_id))
            .one()
        )
        real_session = SimulationSession(
            experiment_version_id=version.id,
            owner_id=owner_id,
            initial_virtual_cash=100_000.0,
            started_at=datetime.combine(BARS[-1], time(8, 30)),
            data_cutoff_at=BARS[-1].isoformat(),
            provider_id="demo",
        )
        db.add(real_session)
        db.flush()
        real_event = HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=real_session.id,
            idempotency_key=uuid.uuid4().hex,
            action=HumanDecisionAction.buy,
            quantity=5.0,
            rationale="owner's own confirmed decision",
            server_strategy_version_id=version.id,
            server_instrument_id=INSTRUMENT,
            server_step_number=1,
            server_snapshot_date=BARS[-1].isoformat(),
            server_data_cutoff_at=BARS[-1].isoformat(),
            execution_status=HumanDecisionExecutionStatus.record_only,
            actor_type="human",
        )
        db.add(real_event)
        db.flush()
        record_label(
            db,
            event=real_event,
            author=author,
            state="confirmed",
            action_label=None,
            note=None,
            exclusion_reason=None,
            actor_type="human",
        )
        db.commit()

        events = dict(
            db.query(HumanDecisionEvent.actor_type, func.count(HumanDecisionEvent.id))
            .group_by(HumanDecisionEvent.actor_type)
            .all()
        )
        assert events == {"human": 1, "unknown": 205}, events
        labels = dict(
            db.query(DecisionLabel.actor_type, func.count(DecisionLabel.id))
            .group_by(DecisionLabel.actor_type)
            .all()
        )
        assert labels == {"human": 1, "unknown": 205}, labels
        # Only the seed's rows carry the marker; the owner's own record is not
        # swept into it by whatever reads provenance later.
        marked = [
            row
            for row in db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.metadata_json.isnot(None))
            .all()
        ]
        assert len(marked) == 205, len(marked)
        assert real_event.id not in {row.id for row in marked}
        assert real_session.provider_id == "demo"
    finally:
        db.close()


# ── the target, refused before anything lands ────────────────────────────────


def test_a_target_that_is_not_the_named_file_stops_before_any_write(setup_db):
    """Dialect and identity both have to match the operator's ``--target-db``.

    The mismatch is what makes this a target check instead of a connection: a
    session bound to one file, asked to verify another, is the shape of "I
    approved the seed into the isolated database and it landed somewhere else".
    """
    path = _target_file()
    resolved = Path(path).resolve()
    db = _db()
    try:
        assert seed.target_state(db, path) == {
            "dialect": "sqlite",
            "database": str(resolved),
        }
        with pytest.raises(seed.SeedRefused) as mismatch:
            seed.target_state(db, str(resolved.parent / "another-isolated.db"))
        assert mismatch.value.code == "target_database_mismatch"
        assert set(mismatch.value.detail) == {"expected", "bound"}
    finally:
        db.close()

    # An engine object that is never connected to — no PostgreSQL is contacted,
    # and the refusal happens on the dialect the URL names.
    pg_engine = create_engine("postgresql+psycopg2://nobody@127.0.0.1:1/never_used")
    pg_session = Session(bind=pg_engine)
    try:
        with pytest.raises(seed.SeedRefused) as refused:
            seed.target_state(pg_session, path)
        assert refused.value.code == "target_not_sqlite"
        assert refused.value.detail == {"dialect": "postgresql"}
    finally:
        pg_session.close()
        pg_engine.dispose()

    class _NoFileBind:
        """A bind with no file behind it, so there is no path to compare."""

        dialect = type("D", (), {"name": "sqlite"})()
        url = type("U", (), {"database": None})()

    class _NoFileSession:
        def get_bind(self):
            return _NoFileBind()

    with pytest.raises(seed.SeedRefused) as nofile:
        seed.target_state(_NoFileSession(), path)
    assert nofile.value.code == "target_has_no_database_file"
    assert all(count == 0 for count in _rows_in_target().values())


def test_a_file_without_the_platform_schema_stops_before_any_write(tmp_path):
    """A readable SQLite file is not the same thing as a platform database.

    The named missing tables are the answer; the alternative is a partial write
    into a database nothing reads. The control at the end runs the same check on
    the real test target and passes.
    """
    foreign = tmp_path / "foreign.db"
    engine = create_engine(f"sqlite:///{foreign}")
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE unrelated_note (id INTEGER PRIMARY KEY)")
        conn.commit()
    session = Session(bind=engine)
    try:
        target = seed.target_state(session, str(foreign))
        assert target["database"] == str(foreign.resolve())
        with pytest.raises(seed.SeedRefused) as refused:
            seed.require_target_schema(session)
        assert refused.value.code == "target_has_no_platform_schema"
        missing = refused.value.detail["missing_tables"]
        assert "market_data_bars" in missing and "decision_labels" in missing
        assert "unrelated_note" not in missing
    finally:
        session.close()
        engine.dispose()

    db = _db()
    try:
        assert seed.require_target_schema(db) == {
            "tables_present": len(seed.REQUIRED_TABLES)
        }
    finally:
        db.close()


def test_the_owner_and_the_frozen_version_have_to_already_be_there(
    setup_db, client, auth_headers
):
    """No prerequisite is invented by the seed.

    Three stops and the state they leave behind: an unknown owner, a version that
    is not in this target, and a version that exists but was never locked. The
    positive control runs last, on the same session, against the real rows.
    """
    stranger = uuid.uuid4()
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    version_uuid = uuid.UUID(strategy_version_id)

    db = _db()
    try:
        with pytest.raises(seed.SeedRefused) as refused:
            seed.require_owner_and_version(db, stranger, version_uuid)
        assert refused.value.code == "owner_not_in_target"

        with pytest.raises(seed.SeedRefused) as refused:
            seed.require_owner_and_version(db, owner_id, uuid.uuid4())
        assert refused.value.code == "strategy_version_not_in_target"

        _, open_version_id = _create_experiment(client, auth_headers, title="Unlocked")
        with pytest.raises(seed.SeedRefused) as refused:
            seed.require_owner_and_version(db, owner_id, uuid.UUID(open_version_id))
        assert refused.value.code == "strategy_version_not_locked"

        subject = seed.require_owner_and_version(db, owner_id, version_uuid)
        assert subject["strategy_version_id"] == strategy_version_id
        assert subject["locked_at"]
        assert subject["owner_email_domain"] == "alethra.io"
        assert subject["version_number"] == 1
    finally:
        db.close()
    assert all(count == 0 for count in _rows_in_target().values())


def test_a_second_execute_is_a_stop_and_writes_no_second_intent(
    setup_db, client, auth_headers
):
    """Re-running the approved write must not double the input.

    The plan's ids are deterministic for exactly this: the second call sees its
    own rows and refuses with a code, and the refusal carries the counts it found
    so an operator can tell "already seeded" from "partially seeded".
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    db = _db()
    try:
        assert seed.write_seed(db, plan, execute=True)["reason"] == "written"
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, plan, execute=True)
        assert refused.value.code == "target_already_holds_seed_rows"
        assert refused.value.detail["existing"] == plan["counts"]
    finally:
        db.close()
    after = _rows_in_target()
    assert after == plan["counts"], after
    assert after["human_decision_events"] == 205
    assert after["simulation_sessions"] == 205


def test_a_target_holding_the_same_bars_stops_and_says_so(setup_db, client, auth_headers):
    """The collision the id-based check cannot see.

    A target whose bars came from the regular fixtures holds the same
    ``(instrument, provider, bar_timestamp)`` rows under *different* ids, so the
    seed would look absent and then die on ``uq_instrument_timestamp_provider``
    in the middle of a transaction. The stop names the rows instead, leaves the
    target exactly as it was, and is keyed on the series rather than the owner.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    db = _db()
    try:
        _seed_bars(db, INSTRUMENT)
        db.add(
            MarketDataMetadata(
                instrument_id=INSTRUMENT,
                provider_id="synthetic",
                data_nature="synthetic",
                first_bar_date=BARS[0],
                last_bar_date=BARS[-1],
                total_bars=len(BARS),
            )
        )
        db.commit()
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, plan, execute=True)
        detail = refused.value.detail
        assert refused.value.code == "target_holds_conflicting_rows"
        assert detail["market_data_bars"] == 140, detail
        assert detail["market_data_metadata"] == 1, detail
        assert detail["instruments"] == ["US:AAPL", "US:MSFT"]
        assert detail["provider_id"] == "synthetic"
    finally:
        db.close()

    # Nothing half-written: the target still holds only the fixture's rows.
    partial = _rows_in_target()
    assert partial["market_data_bars"] == 140, partial
    assert partial["market_data_metadata"] == 1, partial
    assert partial["human_decision_events"] == 0, partial
    assert partial["simulation_sessions"] == 0, partial

    # The same series blocks a second plan too: the check reads the keys, not the
    # plan identity, so nothing can seed over bars the target already holds.
    plan2 = seed.build_plan(uuid.uuid4(), uuid.UUID(strategy_version_id))
    db = _db()
    try:
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, plan2, execute=True)
        assert refused.value.code == "target_holds_conflicting_rows"
    finally:
        db.close()
    assert _rows_in_target() == partial


def test_the_cap_is_enforced_where_rows_are_written(setup_db, client, auth_headers):
    """A plan over its ceiling never reaches the insert.

    The mutation is a plan dict rather than a fixture, because the point is that
    ``write_seed`` re-reads the caps at the door instead of trusting whatever
    ``build_plan`` concluded — the only thing that makes the number a limit and
    not a label. The control runs on the same session with the real plan.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    inflated = {**plan, "within_caps": False, "over_caps": {"market_data_bars": 281}}

    db = _db()
    try:
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, inflated, execute=True)
        assert refused.value.code == "seed_exceeds_caps"
        assert refused.value.detail == {"over": {"market_data_bars": 281}}
        receipt = seed.write_seed(db, plan, execute=True)
        assert receipt["reason"] == "written"
    finally:
        db.close()
    assert _rows_in_target() == plan["counts"]


def test_a_receipt_whose_counts_disagree_with_the_rows_is_a_stop(
    setup_db, client, auth_headers
):
    """Read-back is checked, not asserted: planned 204 against 205 written rows.

    ``seed_rows_read_back`` is the number an operator approves against, so a plan
    that does not match what the writer put in the target has to fail rather than
    print a receipt. The mismatch here is manufactured by editing the plan's own
    counts, which is also how the node proves the comparison is live: no edited
    number can be reported as written.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    lying = {**plan, "counts": {**plan["counts"], "decision_labels": 204}}

    db = _db()
    try:
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, lying, execute=True)
        assert refused.value.code == "seed_rows_read_back_short"
        assert refused.value.detail["short"] == {
            "decision_labels": {"planned": 204, "read_back": 205}
        }
    finally:
        db.close()
    assert all(count == 0 for count in _rows_in_target().values()), _rows_in_target()

    # And the honest plan of the same shape writes, on a fresh target.
    db = _db()
    try:
        assert seed.write_seed(db, plan, execute=True)["reason"] == "written"
    finally:
        db.close()
    assert _rows_in_target() == plan["counts"]


# ── readiness: the keys this window owes, and the units that cover them ──────


@pytest.fixture()
def lane(client, auth_headers, tmp_path, monkeypatch):
    """A prepared account: real training, real artifact, real selection, no daily consent.

    Every entry is a route, so the version and artifact the readiness report names
    were produced rather than declared. Both declared instruments get bars, as
    the prep lane's own autouse fixture does; the daily-estimate consent is the
    owner's own press and stays absent here.
    """
    db = TestSession()
    try:
        for instrument in (INSTRUMENT, SECOND_INSTRUMENT):
            _seed_bars(db, instrument)
            db.add(
                MarketDataMetadata(
                    instrument_id=instrument,
                    provider_id="synthetic",
                    data_nature="synthetic",
                    first_bar_date=BARS[0],
                    last_bar_date=BARS[-1],
                    total_bars=len(BARS),
                )
            )
        db.commit()
    finally:
        db.close()

    owner_id = _user_id()
    experiment_id, strategy_version_id = _create_experiment(
        client, auth_headers, title="Card B readiness lane"
    )
    locked = client.post(
        f"/api/v1/experiments/{experiment_id}/versions/{strategy_version_id}/lock",
        headers=auth_headers,
        json={"confirm": True, "reason": "測試：凍結版本作為每日估計來源"},
    )
    assert locked.status_code == 200, locked.text
    _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))
    _grant_consent(client, auth_headers, strategy_version_id)
    snapshot = _build_snapshot(client, auth_headers, strategy_version_id)
    _personal_artifacts(tmp_path, monkeypatch)
    job_id = _start_training(
        client, auth_headers, snapshot["snapshot_id"], "logistic_regression"
    )
    model = _model_for_job(client, auth_headers, job_id, strategy_version_id)
    skill_unit_id = _open_skill_unit(client, auth_headers)
    _select_for_skill(client, auth_headers, model["model_version_id"], skill_unit_id)
    return {
        "owner_id": owner_id,
        "headers": auth_headers,
        "client": client,
        "strategy_version_id": strategy_version_id,
        "model_version_id": model["model_version_id"],
        "artifact_id": model["artifact_id"],
        "artifact_sha256": model["artifact_sha256"],
        "skill_unit_id": skill_unit_id,
    }


def test_readiness_without_the_owners_press_names_that_and_bills_nothing(lane):
    """The gap card B is blocked on, in the tool's own words.

    ``owner_consent_pressed`` is False, nothing is declared so no key is
    unsettled, and no row of the billing ledger exists — the report answered
    without spending anything to do it.
    """
    db = _db()
    try:
        view = seed.readiness_view(db, lane["owner_id"], now=WINDOW_INSTANT)
    finally:
        db.close()

    assert view["owner_consent_pressed"] is False
    assert view["verdict"] == "NOT_READY"
    assert view["missing"] == ["no_active_daily_estimate_consent"], view["missing"]
    assert view["unsettled_business_keys"] == []
    assert view["billed_business_keys"] == []
    assert view["refused_business_keys"] == []
    assert view["natural_tick_observed"] is False
    assert view["billing_observation_allowed"] is False
    assert view["window"] == estimate_window(WINDOW_INSTANT)
    purposes = _consent_purposes(lane["owner_id"])
    assert "personal_daily_estimate" not in purposes, purposes
    assert "personalization_training" in purposes, purposes
    assert _daily_bills(lane["owner_id"]) == []


def test_readiness_counts_the_two_unsettled_keys_and_the_units_that_cover_them(
    lane, client, auth_headers
):
    """The cell the brief asks readiness to answer, not a nominal limit.

    Two instruments declared for one account is two business keys this window
    owes, and the honest sufficiency question is about what is left to spend. So
    the node reads the report with the full allowance, then leaves one unit and
    reads it again: the nominal comparison still reports True while the spend
    comparison says the window is not covered. That disagreement is exactly what
    a reader must not be allowed to collapse into "limit >= 2, fine".

    ``_grant_daily`` is the owner's own door, pressed inside this process's
    temporary SQLite file for branch evidence about the report. It is not, and is
    not offered as, anyone's consent for a live run.
    """
    granted = _grant_daily(client, auth_headers, _units(lane["skill_unit_id"]))
    assert granted.status_code == 200, granted.text

    db = _db()
    try:
        view = seed.readiness_view(db, lane["owner_id"], now=WINDOW_INSTANT)
    finally:
        db.close()

    assert view["verdict"] == "READY", view["missing"]
    assert view["owner_consent_pressed"] is True
    keys = {
        (item["skill_unit_id"], item["instrument_id"])
        for item in view["unsettled_business_keys"]
    }
    assert keys == {
        (str(lane["skill_unit_id"]), INSTRUMENT),
        (str(lane["skill_unit_id"]), SECOND_INSTRUMENT),
    }, view["unsettled_business_keys"]
    assert view["billed_business_keys"] == []
    assert view["refused_business_keys"] == []
    quota = view["quota"]
    assert quota["units_declared"] == 2
    assert quota["units_ready_to_spend"] == 2
    assert quota["covers_unsettled_keys"] is True
    assert quota["nominal_limit_at_least_key_count"] is True
    limit = quota["limit"]
    assert limit is not None and limit >= 2, quota
    reserved = quota["reserved"]

    db = _db()
    try:
        counter = (
            db.query(QuotaCounter)
            .filter(
                QuotaCounter.user_id == lane["owner_id"],
                QuotaCounter.quota_type == "ai_usage",
                QuotaCounter.period == "lifetime",
            )
            .one_or_none()
        )
        target_used = limit - 1 - reserved
        assert target_used >= 0, (limit, reserved)
        if counter is None:
            db.add(
                QuotaCounter(
                    user_id=lane["owner_id"],
                    quota_type="ai_usage",
                    period="lifetime",
                    used=target_used,
                )
            )
        else:
            counter.used = target_used
        db.commit()
    finally:
        db.close()

    db = _db()
    try:
        short = seed.readiness_view(db, lane["owner_id"], now=WINDOW_INSTANT)
    finally:
        db.close()
    assert short["quota"]["remaining"] == 1, short["quota"]
    assert short["quota"]["units_ready_to_spend"] == 2
    assert short["quota"]["covers_unsettled_keys"] is False
    assert short["quota"]["nominal_limit_at_least_key_count"] is True
    assert short["missing"] == ["quota_exceeded"], short["missing"]
    assert short["verdict"] == "NOT_READY"
    assert short["billing_observation_allowed"] is False
    # Asking was not spending: the ledger the tick settles against is still empty.
    assert _daily_bills(lane["owner_id"]) == []


def test_the_seed_refuses_the_lane_target_and_the_write_needs_a_clear_one(lane):
    """Readiness and the seed are independent doors, and this proves the seam.

    The lane's target is READY — bars, selection, artifact, scope all present —
    yet the seed still stops on it, because those bars are already there under
    other ids. Being ready to bill is not permission to re-seed, and the tool
    that says so is the one an operator can approve without wondering what it
    would overwrite.
    """
    db = _db()
    try:
        view = seed.readiness_view(db, lane["owner_id"], now=WINDOW_INSTANT)
        assert view["missing"] == ["no_active_daily_estimate_consent"], view["missing"]
        plan = seed.build_plan(lane["owner_id"], uuid.UUID(lane["strategy_version_id"]))
        with pytest.raises(seed.SeedRefused) as refused:
            seed.write_seed(db, plan, execute=True)
        assert refused.value.code == "target_holds_conflicting_rows"
        # Not one of the seed's own rows reached the target. Counted by planned id,
        # because this database already holds 205 decisions — the lane's own — and a
        # table total would read those as if the seed had written them.
        written = seed._seed_counts(db, plan)
        assert written == {name: 0 for name in seed.CAPS}, written
    finally:
        db.close()
    counts = _rows_in_target()
    assert counts["market_data_bars"] == 280, counts
    assert counts["market_data_metadata"] == 2, counts


# ── the operator entry: the exit codes are the contract ──────────────────────


def test_the_operator_entry_plans_with_rc_0_and_writes_nothing(
    setup_db, client, auth_headers, tmp_path, capsys
):
    """``--target-db`` on its own is a review: rc 0, ``written: 0``, empty target.

    ``--out`` is the artifact an approval attaches to, so the file has to carry
    the rows the digest was taken over while the printed summary stays readable —
    and neither may contain a connection string.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    capsys.readouterr()  # drain whatever the app routes printed above
    path = _target_file()
    out = tmp_path / "plan.json"

    rc = seed.main(
        [
            "--target-db", path,
            "--owner-id", str(owner_id),
            "--strategy-version-id", strategy_version_id,
            "--out", str(out),
        ]
    )
    assert rc == 0, rc
    printed = json.loads(capsys.readouterr().out)
    assert "rows" not in printed
    assert printed["write"]["reason"] == "inert_by_default"
    assert printed["write"]["written"] == 0
    assert printed["write"]["planned_counts"] == printed["plan"]["counts"]
    assert printed["target"] == {"dialect": "sqlite", "database": str(Path(path).resolve()),
                                 "tables_present": len(seed.REQUIRED_TABLES)}
    assert printed["subject"]["strategy_version_id"] == strategy_version_id
    assert printed["plan"]["within_caps"] is True
    assert "sqlite:///" not in json.dumps(printed)

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload["rows"]) == set(seed.CAPS)
    assert len(payload["rows"]["market_data_bars"]) == 280
    assert payload["plan"]["rows_sha256"] == printed["plan"]["rows_sha256"]
    assert all(count == 0 for count in _rows_in_target().values())


def test_the_operator_entry_writes_then_refuses_the_second_time_with_rc_4(
    setup_db, client, auth_headers, capsys
):
    """0 for the write, 4 for the retry: the two answers must not share a code.

    A receipt with ``written: 0`` and rc 0 would read as success to anything
    watching exit codes, which is how a duplicated seed gets approved. The node
    also counts the consent rows this owner has, because the tool promises zero.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    capsys.readouterr()  # drain whatever the app routes printed above
    path = _target_file()
    argv = [
        "--target-db", path,
        "--owner-id", str(owner_id),
        "--strategy-version-id", strategy_version_id,
        "--execute",
    ]
    assert seed.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["write"]["reason"] == "written"
    assert first["write"]["seed_rows_read_back"]["human_decision_events"] == 205
    assert first["write"]["consent_rows_written"] == 0
    assert _consent_purposes(owner_id) == set()

    assert seed.main(argv) == 4
    err = json.loads(capsys.readouterr().err)
    assert err["refusal"] == "target_already_holds_seed_rows"
    assert err["written"] == 0
    assert err["existing"] == first["plan"]["counts"]
    assert _rows_in_target() == first["plan"]["counts"]


def test_the_operator_entry_names_a_target_it_cannot_accept_with_rc_3(
    setup_db, client, auth_headers, tmp_path, capsys
):
    """A typo, a malformed id and an absent owner are three different stops.

    The first two must not leave a file behind — SQLite would create one on
    connect, and a seed written into a database the tool just invented is the
    exact side effect the approval was meant to prevent.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers)
    path = _target_file()
    missing = tmp_path / "does-not-exist.db"

    assert seed.main(
        ["--target-db", str(missing), "--owner-id", str(owner_id),
         "--strategy-version-id", strategy_version_id]
    ) == 3
    assert json.loads(capsys.readouterr().err) == {
        "refusal": "target_database_missing",
        "written": 0,
    }
    assert not missing.exists(), "the refusal created the file it was refusing"

    assert seed.main(
        ["--target-db", path, "--owner-id", "not-a-uuid",
         "--strategy-version-id", strategy_version_id]
    ) == 2
    assert json.loads(capsys.readouterr().err)["refusal"] == "id_not_a_uuid"

    assert seed.main(
        ["--target-db", path, "--owner-id", str(uuid.uuid4()),
         "--strategy-version-id", strategy_version_id, "--execute"]
    ) == 3
    err = json.loads(capsys.readouterr().err)
    assert err["refusal"] == "owner_not_in_target"
    assert err["written"] == 0
    assert all(count == 0 for count in _rows_in_target().values())


# ── the formal dataset doors: what the seed unlocks, and what it never can ───

ELIGIBILITY = "/api/v1/ml/personalization/eligibility"
PREVIEW_DOOR = "/api/v1/ml/personal/snapshots/preview"
SNAPSHOT_DOOR = "/api/v1/ml/personal/snapshots"
CONSENT_DOOR = "/api/v1/ml/personalization/consent"

# The eight exclusion legs the eligibility door reports, copied from the payload
# it builds (app/services/personalization.py:537-549). Naming them here is what
# makes "and nothing else excluded them" an assertion: a leg that disappeared
# from the response would fail the set comparison rather than widen it.
OTHER_EXCLUSIONS = {
    "superseded_revisions",
    "outside_consent_scope",
    "pending_owner_confirmation",
    "excluded_by_owner",
    "unverified_confirmation",
    "missing_reason",
    "missing_snapshot_context",
}


def _seed_under_a_frozen_version(client, auth_headers, title):
    """A locked version with the seed's own rows under it, written by the tool.

    ``seed.write_seed`` rather than a fixture, because the claim is about this
    input: the 205 rows whose ``actor_type`` is ``unknown`` are the exact rows
    the doors below have to refuse to count.
    """
    owner_id, strategy_version_id = _frozen_version(client, auth_headers, title=title)
    plan = seed.build_plan(owner_id, uuid.UUID(strategy_version_id))
    db = _db()
    try:
        assert seed.write_seed(db, plan, execute=True)["reason"] == "written"
    finally:
        db.close()
    return owner_id, strategy_version_id, plan


def _snapshot_rows(owner_id, strategy_version_id):
    """The snapshot table read on its own, so an empty answer is measured."""
    db = _db()
    try:
        return (
            db.query(PersonalizationDatasetSnapshot)
            .filter(
                PersonalizationDatasetSnapshot.owner_id == owner_id,
                PersonalizationDatasetSnapshot.strategy_version_id
                == uuid.UUID(strategy_version_id),
            )
            .all()
        )
    finally:
        db.close()


def test_the_eligibility_door_counts_no_seed_row_as_a_human_confirmation(
    setup_db, client, auth_headers
):
    """``GET /ml/personalization/eligibility`` over the seed's real output.

    205 recorded, 0 eligible, and all 205 sitting in the one leg
    ``unverified_provenance`` — which is the door saying *why* (the writer was
    never a password-authenticated owner session, ``event_actor_provenance`` at
    app/auth/provenance.py:39-42) instead of a generic "not enough data". The
    other seven legs at zero is what makes that the reason and not a label put
    on a refusal caused by something else.
    """
    owner_id, strategy_version_id, plan = _seed_under_a_frozen_version(
        client, auth_headers, "Card B eligibility door"
    )
    assert plan["counts"]["human_decision_events"] == 205

    resp = client.get(ELIGIBILITY, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    dataset = body["dataset"]

    assert dataset["total_recorded_events"] == 205, dataset
    assert dataset["eligible_events"] == 0, dataset
    assert dataset["excluded"]["unverified_provenance"] == 205, dataset["excluded"]
    others = {
        leg: count
        for leg, count in dataset["excluded"].items()
        if leg != "unverified_provenance"
    }
    assert set(others) == OTHER_EXCLUSIONS, others
    assert others == {leg: 0 for leg in OTHER_EXCLUSIONS}, others
    assert (
        sum(dataset["excluded"].values()) == dataset["total_recorded_events"]
    ), dataset["excluded"]

    warnings = {item["code"]: item for item in body["warnings"]}
    provenance_warning = warnings["UNVERIFIED_DECISION_PROVENANCE"]
    assert provenance_warning["measured"] == 205, provenance_warning
    assert "不會當作本人確認的標籤" in provenance_warning["message_zh"]

    reasons = {item["code"]: item for item in body["reasons"]}
    short = reasons["INSUFFICIENT_CONFIRMED_DECISIONS"]
    assert short["measured"] == 0, short
    assert short["requirement"] == 200, short
    assert reasons["CONSENT_MISSING"]["measured"] == "missing", reasons["CONSENT_MISSING"]
    assert body["consent"]["granted"] is False, body["consent"]
    assert body["status"] == "NOT_ELIGIBLE"
    assert body["eligible"] is False
    assert len(body["strategies"]) == 1, body["strategies"]
    strategy = body["strategies"][0]
    assert strategy["strategy_version_id"] == strategy_version_id, strategy
    assert strategy["recorded_decisions"] == 205, strategy
    assert strategy["confirmed_decisions"] == 0, strategy
    assert strategy["in_consent_scope"] is False, strategy

    # Nothing was produced by any of this, and no consent was written by the tool.
    assert _snapshot_rows(owner_id, strategy_version_id) == []
    assert _consent_purposes(owner_id) == set()


def test_the_snapshot_door_refuses_the_seed_before_and_after_the_owners_consent(
    setup_db, client, auth_headers
):
    """Consent is a second, independent door: opening it does not open this one.

    The preview is the only door that answers before consent, and it answers with
    the same breakdown the write door refuses on. After the owner presses the
    formal consent door themselves — the route, not a row written here — the seed
    still yields 0 included rows, so the refusal cannot be read as "consent was
    the missing piece". The refusal that remains names the count and the floor
    (``measured`` 0 against ``requirement`` 200), which is the honest statement of
    what a reviewable input would have to supply.
    """
    owner_id, strategy_version_id, _ = _seed_under_a_frozen_version(
        client, auth_headers, "Card B snapshot door"
    )
    payload = {"strategy_version_id": strategy_version_id}

    preview = client.post(PREVIEW_DOOR, headers=auth_headers, json=payload)
    assert preview.status_code == 200, preview.text
    pre = preview.json()
    assert pre["total_current_decisions"] == 205, pre
    assert pre["duplicate_source_events_ignored"] == 0, pre
    assert pre["included_count"] == 0, pre
    assert pre["excluded_count"] == 205, pre
    assert pre["exclusion_breakdown"] == {"unverified_provenance": 205}, pre
    assert (
        pre["exclusion_labels_zh"]["unverified_provenance"]
        == "來源不是已驗證的本人密碼登入工作階段"
    ), pre["exclusion_labels_zh"]
    assert pre["class_distribution"] == {}, pre
    assert pre["can_build"] is False, pre["reasons"]
    assert [item["code"] for item in pre["reasons"]] == [
        "CONSENT_MISSING",
        "INSUFFICIENT_CONFIRMED_DECISIONS",
        "SINGLE_CLASS_DATASET",
    ], pre["reasons"]

    refused = client.post(SNAPSHOT_DOOR, headers=auth_headers, json=payload)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["code"] == "consent_missing", refused.text
    assert _snapshot_rows(owner_id, strategy_version_id) == []

    granted = client.put(
        CONSENT_DOOR,
        headers=auth_headers,
        json={"strategy_version_ids": [strategy_version_id], "confirm": True},
    )
    assert granted.status_code == 200, granted.text
    assert _consent_purposes(owner_id) == {"personalization_training"}

    after = client.post(PREVIEW_DOOR, headers=auth_headers, json=payload).json()
    assert after["consent"]["in_scope"] is True, after["consent"]
    assert after["included_count"] == 0, after
    assert after["exclusion_breakdown"] == {"unverified_provenance": 205}, after
    assert [item["code"] for item in after["reasons"]] == [
        "INSUFFICIENT_CONFIRMED_DECISIONS",
        "SINGLE_CLASS_DATASET",
    ], after["reasons"]

    still = client.post(SNAPSHOT_DOOR, headers=auth_headers, json=payload)
    assert still.status_code == 400, still.text
    detail = still.json()["detail"]
    assert detail["code"] == "insufficient_confirmed_decisions", detail
    assert detail["details"][0]["code"] == "INSUFFICIENT_CONFIRMED_DECISIONS", detail
    assert detail["details"][0]["measured"] == 0, detail
    assert detail["details"][0]["requirement"] == 200, detail

    listed = client.get(
        SNAPSHOT_DOOR, headers=auth_headers, params={"strategy_version_id": strategy_version_id}
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"snapshots": []}, listed.text
    assert _snapshot_rows(owner_id, strategy_version_id) == []

    # Nothing along the way rewrote the seed: the rows still say unknown and
    # still say the owner never confirmed them.
    db = _db()
    try:
        events = (
            db.query(HumanDecisionEvent)
            .filter(HumanDecisionEvent.owner_id == owner_id)
            .all()
        )
        assert len(events) == 205, len(events)
        assert {event.actor_type for event in events} == {"unknown"}
        assert all(
            (event.metadata_json or {}).get("owner_confirmed") is False
            for event in events
        )
    finally:
        db.close()


def test_only_human_provenance_rows_open_the_door_and_the_seed_stays_excluded(
    setup_db, client, auth_headers
):
    """The control that makes the two refusals above mean what they say.

    On one target, with the seed's rows left exactly as the tool wrote them, 205
    more decisions are appended with ``actor_type="human"`` through the review
    service the review door uses (the settled personal-learning fixture, whose
    bars the seed's own series already provides). The door then includes those
    205 and still excludes the seed's 205 — so the refusals above are about
    provenance and not about the locked version, the market data, or a door that
    cannot open at all.

    The persisted exclusion breakdown is the record the brief asks to keep: the
    formal GET re-reads ``unverified_provenance: 205`` off the snapshot row, so
    the count is server state rather than a number this test recomputed.

    What it does not do: it stops at the dataset door. No training job, no model
    version, no artifact — the seed tool, a training input, a trained artifact,
    the owner's daily consent and a natural tick stay five separate things. The
    consent it needs is pressed on the owner's own door (``_grant_consent`` is
    ``PUT /ml/personalization/consent``), never written as a row here.
    """
    owner_id, strategy_version_id, _ = _seed_under_a_frozen_version(
        client, auth_headers, "Card B provenance control"
    )
    human_ids = {str(i) for i in _seed_confirmed_decisions(owner_id, uuid.UUID(strategy_version_id))}
    assert len(human_ids) == 205, human_ids
    _grant_consent(client, auth_headers, strategy_version_id)

    body = client.get(ELIGIBILITY, headers=auth_headers).json()
    dataset = body["dataset"]
    assert dataset["total_recorded_events"] == 410, dataset
    assert dataset["eligible_events"] == 205, dataset
    assert dataset["excluded"]["unverified_provenance"] == 205, dataset["excluded"]
    assert dataset["excluded"]["pending_owner_confirmation"] == 0, dataset["excluded"]

    created = client.post(
        SNAPSHOT_DOOR,
        headers=auth_headers,
        json={"strategy_version_id": strategy_version_id},
    )
    assert created.status_code == 201, created.text
    snapshot = created.json()
    assert snapshot["included_count"] == 205, snapshot
    assert snapshot["excluded_count"] == 205, snapshot
    assert snapshot["exclusion_breakdown"] == {"unverified_provenance": 205}, snapshot
    assert snapshot["class_distribution"] == {"buy": 103, "sell": 102}, snapshot

    read_back = client.get(
        f"{SNAPSHOT_DOOR}/{snapshot['snapshot_id']}", headers=auth_headers
    )
    assert read_back.status_code == 200, read_back.text
    stored = read_back.json()
    assert stored["exclusion_breakdown"] == {"unverified_provenance": 205}, stored
    assert stored["checksum"] == snapshot["checksum"], stored
    for row in stored["rows_preview"]:
        assert row["decision_event_id"] in human_ids, row

    rows = _snapshot_rows(owner_id, strategy_version_id)
    assert len(rows) == 1, len(rows)
    assert rows[0].exclusion_breakdown_json == {"unverified_provenance": 205}
    assert rows[0].included_count == 205 and rows[0].excluded_count == 205

    db = _db()
    try:
        assert db.query(TrainingJob).filter(TrainingJob.user_id == owner_id).count() == 0
        assert db.query(ModelVersion).count() == 0, "the control trained a model"
        assert (
            db.query(HumanDecisionEvent)
            .filter(
                HumanDecisionEvent.owner_id == owner_id,
                HumanDecisionEvent.actor_type == "unknown",
            )
            .count()
            == 205
        ), "a seed row was relabelled to open the door"
    finally:
        db.close()
