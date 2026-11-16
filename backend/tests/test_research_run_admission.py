"""Durable admission of a research run: what is committed, when, and by whom.

The lane under test is the customer's own: ``POST /api/v1/versions/{id}/runs``
→ ``execute_backtest_run`` → admission → engine → terminal write. Since
2026-11-15 the attempt is committed **before** the engine starts
(``services/research_runs.py:_admit_research_run``) and the outcome is committed
with its settlement or release afterwards. This file binds each of those three
transactions separately, because the failures worth catching live on the
borders:

* an admission that is refused or that breaks mid-way must leave no run row, no
  reservation and no audit event — nothing a later commit could publish;
* the unit a run bills must be found by the linkage written at admission, never
  by a timestamp, so a robustness study's unit stays the study's;
* the completed commit is refused when that linkage does not support it — a
  billed run with no unit attributed to it, one whose unit is already closed,
  and an unbilled run that nevertheless has one are all not published;
* a settlement or audit that raises must not publish a partially completed run;
* a write-off that itself raises must be reported, not swallowed into a success.

Evidence scope, stated because this lane cannot state it by itself. These are
SQLite tests and the test engine shares one DBAPI connection between sessions
(``tests/conftest.py:133-137``, ``StaticPool``), so ``SELECT ... FOR UPDATE`` is
a no-op here and no two transactions genuinely interleave. What is proven is
therefore (a) that each write sits on the documented side of its commit — the
``in_transaction`` / ``rollback`` probes below are falsifiable, because an
uncommitted admission would vanish when the probe rolls back — and (b) the
decision rules. Cross-connection mutual exclusion, a PostgreSQL run, a
cross-process race and a real ``SIGKILL`` are a different evidence level and are
NOT RUN here; nor has the migration that adds the linkage column been executed
against PostgreSQL. The foreign key is not exercised either: this lane turns
``PRAGMA foreign_keys`` off, so ``RESTRICT`` is asserted as schema intent only.
"""

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.auth.entitlements import (
    FREE_PLAN_ENTITLEMENT,
    _get_reserved,
    consume_quota,
    get_quota_used,
    release_reservation,
)
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.research_run import ResearchRun, RunStatus
from app.models.robustness import RobustnessStudy
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services import research_runs as research_run_service
from app.services.market_data_storage import MarketDataStorage
from app.services.research_runs import (
    _linked_reservation,
    execute_backtest_run,
    find_live_research_run,
)
from tests.conftest import TestSession

_SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["US:AAPL"],
    "frequency": "1d",
    "start_date": "2024-01-02",
    "end_date": "2024-03-15",
    "initial_cash": 100000.0,
    "commission_rate": 0.001,
    "slippage_rate": 0.0,
}

_PROVIDER = "synthetic"
_QUOTA = "research_run"
_FREE_RUNS = FREE_PLAN_ENTITLEMENT["research_run_limit"]


def _now():
    """Naive UTC, the shape ``started_at`` and ``created_at`` are stored in."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _weekdays(start: date, count: int):
    days = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _bars(days, base_price=150.0):
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
    db = TestSession()
    try:
        days = _weekdays(date(2024, 1, 2), 55)
        MarketDataStorage(db).store_bars(
            "US:AAPL", _PROVIDER, _bars(days), "synthetic"
        )
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


def _headers(client, email):
    client.post(
        "/api/auth/register",
        json={
            "risk_acknowledged": True,
            "email": email,
            "password": "admission-pass-123",
            "display_name": "Admission Tester",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "admission-pass-123"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_experiment(client, headers):
    resp = client.post(
        "/api/v1/experiments/",
        json={
            "title": "Admission slice",
            "hypothesis": "An attempt is admitted whole or not at all.",
            "strategy_spec": _SPEC,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return UUID(resp.json()["current_version_id"])


def _start_run(client, headers, version_id):
    return client.post(
        f"/api/v1/versions/{version_id}/runs",
        params={"data_source": "stored", "provider_id": _PROVIDER},
        headers=headers,
    )


def _user_id(db, email="test@alethra.io"):
    return db.query(User).filter(User.email == email).first().id


def _objects(db, version_id, user_id):
    """The three handles ``execute_backtest_run`` is called with."""
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id
    ).one()
    experiment = db.query(Experiment).filter(
        Experiment.id == version.experiment_id
    ).one()
    user = db.query(User).filter(User.id == user_id).one()
    return user, version, experiment


# ── reads taken through a session the writer does not own ────────────────────


def _run_row(run_id):
    def read(session):
        return (
            session.query(
                ResearchRun.id,
                ResearchRun.status,
                ResearchRun.started_at,
                ResearchRun.completed_at,
                ResearchRun.metrics,
                ResearchRun.result_json,
                ResearchRun.error_details,
            )
            .filter(ResearchRun.id == run_id)
            .one()
        )

    return _elsewhere(read)


def _runs_for(version_id):
    def read(session):
        return [
            row[0]
            for row in session.query(ResearchRun.id).filter(
                ResearchRun.experiment_version_id == version_id
            ).all()
        ]

    return _elsewhere(read)


def _linked_unit(run_id):
    """The unit this run owns, read the way the ledger path reads it."""

    def read(session):
        return (
            session.query(QuotaReservation.id, QuotaReservation.status)
            .filter(
                QuotaReservation.research_run_id == run_id,
                QuotaReservation.quota_type == _QUOTA,
            )
            .one_or_none()
        )

    return _elsewhere(read)


def _units(user_id):
    def read(session):
        return session.query(
            QuotaReservation.id,
            QuotaReservation.status,
            QuotaReservation.research_run_id,
        ).filter(
            QuotaReservation.user_id == user_id,
            QuotaReservation.quota_type == _QUOTA,
        ).all()

    return _elsewhere(read)


def _event_types(run_id):
    def read(session):
        return {
            row[0]
            for row in session.query(AuditEvent.event_type).filter(
                AuditEvent.entity_type == "run",
                AuditEvent.entity_id == str(run_id),
            ).all()
        }

    return _elsewhere(read)


def _elsewhere(read):
    session = TestSession()
    try:
        return read(session)
    finally:
        session.close()


def _committed_elsewhere(mutate):
    """Write through a session the run's own writer does not own, and commit.

    Used to put the ledger into a state the admission did not leave it in, at
    the moment the engine is running — the shape a second process produces. On
    this lane it is one DBAPI connection behind two sessions, so it demonstrates
    the decision the writer makes about a state it did not write, not two
    PostgreSQL connections racing for it.
    """
    session = TestSession()
    try:
        mutate(session)
        session.commit()
    finally:
        session.close()


# ── 一、admission commits before anything computes ───────────────────────────


def test_admission_is_committed_before_the_engine_reads_a_bar(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """The attempt is durable the moment the engine is called.

    The probe runs in place of the engine, so what it sees is the state the
    request path leaves at the exact moment execution begins. Two reads make
    that falsifiable instead of a matter of reading order in the source:
    ``in_transaction()`` says the admission's own transaction is finished, and
    the ``rollback()`` here would erase the row, the unit and the audit event if
    any of them were still uncommitted.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    original = research_run_service.run_backtest
    seen = {}

    def probe(spec, *args, **kwargs):
        session = kwargs["db_session"]
        seen["in_transaction"] = session.in_transaction()
        session.rollback()
        rows = _runs_for(version_id)
        seen["rows"] = len(rows)
        if rows:
            run_id = rows[0]
            run = _run_row(run_id)
            unit = _linked_unit(run_id)
            seen["status"] = run.status
            seen["started_at"] = run.started_at
            seen["metrics"] = run.metrics
            seen["unit_status"] = None if unit is None else unit.status
            seen["events"] = _event_types(run_id)
        out = original(spec, *args, **kwargs)
        seen["in_transaction_after_engine"] = session.in_transaction()
        return out

    monkeypatch.setattr(research_run_service, "run_backtest", probe)

    resp = _start_run(client, auth_headers, version_id)
    assert resp.status_code == 201, resp.text

    assert seen["in_transaction"] is False, (
        "the engine ran inside the admission transaction, so nothing observed "
        "below had been committed"
    )
    # Execution writes nothing, and that is all the boundary claim covers: the
    # engine's own dataset SELECTs open a fresh transaction on this session
    # after the admission commit. Pinning the measured value here keeps
    # "execution opens no transaction" from ever being written again.
    assert seen["in_transaction_after_engine"] is True, (
        "the engine read no dataset through this session, so this lane is not "
        "measuring the execution boundary it claims to"
    )
    assert seen["rows"] == 1, "the admitted row is not durable before execution"
    assert seen["status"] == RunStatus.running
    assert seen["started_at"] is not None
    assert seen["metrics"] is None, "an active run has no outcome, not a zero one"
    assert seen["unit_status"] == "reserved"
    assert seen["events"] == {"research_run_started"}

    run_id = UUID(resp.json()["id"])
    assert _run_row(run_id).status == RunStatus.completed
    assert get_quota_used(db, user_id, _QUOTA) == 1
    assert _get_reserved(db, user_id, _QUOTA) == 0
    assert _linked_unit(run_id).status == "settled"


def test_an_admission_that_cannot_reserve_leaves_no_run_reservation_or_audit(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """A reservation that fails takes the attempt with it.

    ``reserve_quota`` raises after the run row was already written in this
    transaction, so the rollback branch has to walk the whole attempt back. The
    failure is injected at the call site in the admission rather than by
    spending the allowance: on the formal path a spent allowance is answered
    upstream (the next test), so nothing here would exercise this branch without
    the injection. What is bound is the branch, not the arithmetic inside
    ``entitlements``, which the two-phase quota suite already covers.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    user, version, experiment = _objects(db, version_id, user_id)

    def refuse_to_reserve(*args, **kwargs):
        raise HTTPException(status_code=429, detail="quota_exceeded")

    monkeypatch.setattr(
        research_run_service, "reserve_quota", refuse_to_reserve
    )

    with pytest.raises(HTTPException):
        execute_backtest_run(
            db,
            user=user,
            version=version,
            experiment=experiment,
            data_source="stored",
            provider_id=_PROVIDER,
        )

    assert _runs_for(version_id) == [], "a refused attempt writes no run"
    assert _units(user_id) == [], "and no reservation, settled or otherwise"
    assert (
        _elsewhere(
            lambda session: session.query(AuditEvent).filter(
                AuditEvent.entity_type == "run"
            ).count()
        )
        == 0
    ), "and no audit event for a run that was never admitted"
    assert get_quota_used(db, user_id, _QUOTA) == 0

    monkeypatch.undo()
    assert _start_run(client, auth_headers, version_id).status_code == 201, (
        "a refusal is not a lock: the version is admittable again, with nothing "
        "to reclaim first"
    )
    assert find_live_research_run(db, version_id=version_id) is None


def test_a_spent_allowance_is_answered_before_admission(
    db, client, auth_headers, stored_dataset
):
    """Where the 429 actually comes from on the formal path.

    ``require_entitlement(quota_type="research_run")`` re-checks availability at
    the request boundary, so a spent allowance is refused before this service is
    entered and no attempt is ever written. Recorded here because it bounds what
    the admission's own 429 branch can be reached by — and because "the ledger
    says no" and "the attempt was admitted and rolled back" are different
    statements that must not be merged into one passing assertion.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    # Committed, not merely flushed: ``entitlements`` never commits, and a
    # pending write here would be discarded by the request session's rollback —
    # the two sessions share one connection in this lane, so the refusal would
    # have been produced by state that never existed for anybody else.
    consume_quota(db, user_id, _QUOTA, _FREE_RUNS)
    db.commit()
    assert get_quota_used(db, user_id, _QUOTA) == _FREE_RUNS

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 429, resp.text
    assert _runs_for(version_id) == []
    assert _units(user_id) == []

    # Free one unit with no reclaim and no repair: the same request admits.
    freed = db.query(QuotaCounter).filter(
        QuotaCounter.user_id == user_id,
        QuotaCounter.quota_type == _QUOTA,
    ).update({"used": _FREE_RUNS - 1}, synchronize_session=False)
    db.commit()
    assert freed == 1, "the counter the refusal read is the one being adjusted"

    admitted = _start_run(client, auth_headers, version_id)
    assert admitted.status_code == 201, admitted.text
    assert len(_runs_for(version_id)) == 1
    assert get_quota_used(db, user_id, _QUOTA) == _FREE_RUNS


def test_a_broken_admission_transaction_rolls_the_whole_attempt_back(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """The audit event is part of the admission, not a note added after it.

    ``emit_event`` flushes, so a failure there raises inside the same
    transaction as the run row and the reservation. The rollback branch has to
    remove all three; a ``running`` row beside no unit, or a unit beside no
    row, is exactly the half-set this slice must not leave behind.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    user, version, experiment = _objects(db, version_id, user_id)

    def audit_unavailable(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(research_run_service, "emit_event", audit_unavailable)

    with pytest.raises(RuntimeError):
        execute_backtest_run(
            db,
            user=user,
            version=version,
            experiment=experiment,
            data_source="stored",
            provider_id=_PROVIDER,
        )

    assert _runs_for(version_id) == []
    assert _units(user_id) == []
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert (
        _elsewhere(
            lambda session: session.query(AuditEvent).filter(
                AuditEvent.entity_type == "run"
            ).count()
        )
        == 0
    )

    monkeypatch.undo()
    assert _start_run(client, auth_headers, version_id).status_code == 201


# ── 二、the unit a run bills is the one it linked ────────────────────────────


def test_a_study_cell_holds_no_unit_and_cannot_reach_the_study_one(
    db, client, auth_headers, stored_dataset
):
    """``charge_quota=False`` means no linkage, and no linkage means unreachable.

    A robustness study bills one authorization unit for the whole plan and
    settles it itself (``api/robustness.py:361-383``). Its cells are real runs —
    same rows, same engine, same terminal write — so the only thing keeping a
    cell out of that ledger is that nothing links the study's unit to a run.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    user, version, experiment = _objects(db, version_id, user_id)

    study_reservation = QuotaReservation(
        user_id=user_id, quota_type=_QUOTA, amount=1, status="reserved"
    )
    db.add(study_reservation)
    db.flush()
    study = RobustnessStudy(
        experiment_id=experiment.id,
        version_id=version_id,
        user_id=user_id,
        kind="cost_scenarios",
        status="running",
        config={},
        plan={"cells": []},
        # After the flush, so the claim names a generated id: an unset claim
        # would leave this unit looking like an ordinary unattributed row.
        authorization_reservation_id=study_reservation.id,
        started_at=_now(),
    )
    db.add(study)
    db.commit()
    assert study_reservation.research_run_id is None, (
        "a study unit is attributed to the study, never to a run"
    )

    run = execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        data_source="stored",
        provider_id=_PROVIDER,
        charge_quota=False,
    )

    assert run.status == RunStatus.completed
    assert _linked_unit(run.id) is None
    assert _linked_reservation(db, run_id=run.id) is None
    rows = _units(user_id)
    assert [row.status for row in rows] == ["reserved"], (
        "the cell neither settled nor released the study's unit"
    )
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 1


def test_one_run_bills_its_own_unit_and_never_the_stranded_one(
    db, client, auth_headers, stored_dataset
):
    """Linkage replaces the pairing that timestamps got wrong.

    The other open unit is stamped just *before* the run's ``started_at``, which
    is the interleave that made the pre-linkage rule misattribute — nothing
    bounds the reserve→stamp gap. Nothing here can fall back to that ordering:
    settlement reads the linkage written in the admission transaction, so the
    ordering of the timestamps is not an argument here — and since 2026-11-15
    this lane has no timestamp-based release entry left to fall back to at all
    (see ``test_research_run_recovery.py``).
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    resp = _start_run(client, auth_headers, version_id)
    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    started = _run_row(run_id).started_at

    stranded = QuotaReservation(
        user_id=user_id,
        quota_type=_QUOTA,
        amount=1,
        status="reserved",
        created_at=started - timedelta(seconds=5),
    )
    db.add(stranded)
    db.commit()

    linked = _linked_unit(run_id)
    assert linked is not None, "the admission wrote no linkage"
    assert linked.status == "settled"
    assert linked.id != stranded.id
    by_id = {row.id: row.status for row in _units(user_id)}
    assert by_id[stranded.id] == "reserved", (
        "an unattributed unit is UNKNOWN, and UNKNOWN is not this run's to bill"
    )
    assert by_id[linked.id] == "settled"
    assert get_quota_used(db, user_id, _QUOTA) == 1


def test_the_linkage_index_allows_exactly_one_unit_per_run(
    db, client, auth_headers, stored_dataset
):
    """The billing contract is a constraint, not a convention.

    ``uq_quota_reservation_run_unit`` is partial on
    ``quota_type = 'research_run'``, so a second research unit for one run
    cannot be written, while another quota type naming the same run id is not
    this index's business. Both halves matter: unique without the predicate
    would over-block, and the predicate without unique would be a comment.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    run_id = UUID(_start_run(client, auth_headers, version_id).json()["id"])
    first = _linked_unit(run_id)

    duplicate = QuotaReservation(
        user_id=user_id,
        quota_type=_QUOTA,
        amount=1,
        status="reserved",
        research_run_id=run_id,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    assert [row.id for row in _units(user_id)] == [first.id]

    other_type = QuotaReservation(
        user_id=user_id,
        quota_type="export",
        amount=1,
        status="reserved",
        research_run_id=run_id,
    )
    db.add(other_type)
    db.flush()
    db.rollback()


# ── 二b、the schema that makes the contract enforceable ────────────────────
#
# The migration has not been run against PostgreSQL, so these are the parts of
# it that can be decided without a database: that Alembic can load at all, and
# that the two files which describe the same constraint still describe the same
# constraint. Both are read from the source text — this interpreter has no
# alembic installed, and a gate that cannot run is not a gate.

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
LINKAGE_REVISION = "d0e1f2a3b4c5"
LINKAGE_FILE = f"{LINKAGE_REVISION}_add_quota_reservation_run_link.py"


def _revision_rows():
    """(file, revision, down_revisions) for every migration script."""
    rows = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        source = path.read_text()
        rev = re.search(r"^\s*revision\b[^=\n]*=\s*[\"']([A-Za-z0-9_]+)[\"']", source, re.M)
        if not rev:
            continue
        down = re.search(r"^\s*down_revision\b[^=\n]*=\s*(.+)$", source, re.M)
        parents = re.findall(r"[\"']([A-Za-z0-9_]+)[\"']", down.group(1)) if down else []
        rows.append((path.name, rev.group(1), parents))
    return rows


def test_the_linkage_revision_is_unique_and_is_the_only_head():
    """Two files sharing a revision id is not a branch — it is a load failure.

    The id this revision first shipped with (``e5f6a7b8c9d0``) was already used
    mid-chain by ``add_intent_cancel_requested``, which would have made every
    ``alembic upgrade`` die on ``DuplicateRevision`` before touching the
    database. Nothing in the suite caught that until this test existed.
    """
    rows = _revision_rows()
    assert rows, "no migration scripts found: this test would pass on an empty scan"

    ids = [revision for _, revision, _ in rows]
    duplicates = sorted({revision for revision in ids if ids.count(revision) > 1})
    assert duplicates == [], f"duplicate revision ids, alembic would refuse to load: {duplicates}"

    referenced = {parent for _, _, parents in rows for parent in parents}
    heads = sorted(set(ids) - referenced)
    assert heads == [LINKAGE_REVISION], (
        f"expected one head, the linkage revision; got {heads}"
    )

    parents = [row for row in rows if row[1] == LINKAGE_REVISION][0][2]
    assert parents == ["c9d0e1f2a3b4"], (
        "the linkage revision must sit on the head this branch started from, "
        f"not branch off it: {parents}"
    )


def test_the_linkage_migration_and_the_model_declare_one_contract():
    """Names, predicates and the delete rule are equal in both files, or fail.

    ``create_all`` (this lane) builds the constraint from the model and
    ``alembic upgrade`` (PostgreSQL) builds it from the migration, so a
    difference between them means the tested schema and the shipped schema
    enforce different things — and the suite would stay green either way.
    """
    source = (_MIGRATIONS_DIR / LINKAGE_FILE).read_text()
    const = {
        match.group(1): match.group(3)
        for match in re.finditer(r"^([A-Z_]+)\s*=\s*([\"'])(.*?)\2$", source, re.M)
    }
    table = QuotaReservation.__table__
    by_name = {index.name: index for index in table.indexes}

    assert const["TABLE"] == table.name
    assert const["COLUMN"] == "research_run_id"

    for name_key, predicate_key, unique in (
        ("INDEX_NAME", "NOT_NULL_PREDICATE", False),
        ("UNIQUE_NAME", "ONE_UNIT_PER_RUN_PREDICATE", True),
    ):
        index = by_name[const[name_key]]
        assert index.unique is unique, const[name_key]
        for dialect in ("postgresql_where", "sqlite_where"):
            assert str(index.dialect_kwargs[dialect]) == const[predicate_key], (
                f"{const[name_key]}: the model declares "
                f"{str(index.dialect_kwargs[dialect])!r} for {dialect}, the "
                f"migration writes {const[predicate_key]!r}"
            )
        assert [c.name for c in index.columns] == ["research_run_id"]

    linkage_fk = next(
        foreign_key for foreign_key in table.foreign_keys
        if foreign_key.parent.name == const["COLUMN"]
    )
    assert linkage_fk.target_fullname == "research_runs.id"
    assert linkage_fk.ondelete == "RESTRICT"
    assert "ondelete='RESTRICT'" in source
    assert const["FK_NAME"] == "fk_quota_reservation_research_run"

    upgrade_body = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert not re.search(r"\bUPDATE\b|\bDELETE\b|backfill", upgrade_body, re.I), (
        "the upgrade must add structure and attribute nothing; a statement that "
        "moves existing rows would be claiming ownership the ledger never recorded"
    )


# ── 三、the terminal transaction is one transaction ─────────────────────────


def test_completion_settles_bills_and_audits_in_one_outcome(
    db, client, auth_headers, stored_dataset
):
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    run_id = UUID(_start_run(client, auth_headers, version_id).json()["id"])

    run = _run_row(run_id)
    assert run.status == RunStatus.completed
    assert run.metrics is not None and run.completed_at is not None
    events = _event_types(run_id)
    assert {"research_run_started", "research_run_completed"} <= events
    assert "research_run_failed" not in events, (
        "the only other event a completed run may carry is its warnings"
    )
    assert _linked_unit(run_id).status == "settled"
    assert get_quota_used(db, user_id, _QUOTA) == 1
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_an_engine_failure_closes_the_run_and_hands_the_unit_back(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """Failed means terminal, released and audited — in one commit.

    The admitted row is already durable, so this is what a customer sees after
    an error: a terminal run, no held quota, and a trail that says the attempt
    happened and produced no result.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    def explode(spec, *args, **kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(research_run_service, "run_backtest", explode)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "failed"
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed
    assert run.metrics is None and run.result_json is None
    assert run.error_details
    assert _event_types(run_id) == {"research_run_started", "research_run_failed"}
    assert _linked_unit(run_id).status == "released"
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_a_refused_backtest_is_closed_not_published_empty(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """``run_status: error`` is a refusal, and a refusal is not evidence.

    Publishing it as completed with no transactions would read as a finding
    about the strategy and bill the run for it.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    def refuse(spec, *args, **kwargs):
        return {"run_status": "error", "errors": ["no bars for US:AAPL"]}

    monkeypatch.setattr(research_run_service, "run_backtest", refuse)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed
    assert run.result_json is None and run.metrics is None
    assert "no bars" in run.error_details
    assert _linked_unit(run_id).status == "released"
    assert get_quota_used(db, user_id, _QUOTA) == 0


def test_a_billed_run_with_no_attributed_unit_is_not_published_as_completed(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """A completed write that cannot be billed is not a completed write.

    Admission reserves, attributes and commits the unit in the same transaction
    it commits the row in, so a billed run whose attribution is gone by the time
    the result is ready describes work the ledger cannot charge for. Publishing
    it anyway would put a finding on record with no billing behind it — and no
    timestamp can name a unit that stopped claiming to be this run's.

    The attribution is removed by another session while the engine computes,
    which is the shape a second process produces. Measured here: the completed
    write is discarded, the run ends failed carrying that reason, and the
    orphaned unit is left exactly as the other writer left it — UNKNOWN is not
    this writer's to bill or to hand back.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    original = research_run_service.run_backtest

    def drop_attribution(spec, *args, **kwargs):
        run_id = _runs_for(version_id)[0]

        def mutate(session):
            session.query(QuotaReservation).filter(
                QuotaReservation.research_run_id == run_id,
                QuotaReservation.quota_type == _QUOTA,
            ).update({"research_run_id": None}, synchronize_session=False)

        _committed_elsewhere(mutate)
        return original(spec, *args, **kwargs)

    monkeypatch.setattr(research_run_service, "run_backtest", drop_attribution)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed, (
        "a billed run with no attributed unit cannot be published as completed"
    )
    assert run.metrics is None and run.result_json is None
    assert "holds no research_run quota unit attributed to it" in run.error_details
    assert _linked_unit(run_id) is None
    assert [
        (row.status, row.research_run_id) for row in _units(user_id)
    ] == [("reserved", None)], (
        "the refusal neither settled nor released a unit it cannot attribute"
    )
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert _event_types(run_id) == {"research_run_started", "research_run_failed"}


def test_a_billed_run_whose_unit_is_already_closed_is_not_published(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """Nothing to settle, so nothing to publish — even with the row still open.

    The attribution survives and only the state moved: another writer's
    transaction has already handed this run's unit back. This is the case that
    a status check alone would sail past, because the linkage still names a row
    and the run was still active when the write was taken. Settling a released
    unit raises, so the alternative to refusing is committing a result beside a
    ledger that will never be billed; that disagreement is reported instead.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    original = research_run_service.run_backtest

    def close_the_unit(spec, *args, **kwargs):
        run_id = _runs_for(version_id)[0]
        unit_id = _linked_unit(run_id).id

        def mutate(session):
            release_reservation(session, unit_id)

        _committed_elsewhere(mutate)
        return original(spec, *args, **kwargs)

    monkeypatch.setattr(research_run_service, "run_backtest", close_the_unit)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed, (
        "an already-closed unit cannot be settled by the writer that found it"
    )
    assert run.metrics is None and run.result_json is None
    assert "is not in reserved" in run.error_details
    assert _linked_unit(run_id).status == "released", (
        "the other writer's decision stands; this one neither re-opens it nor "
        "releases anything of its own"
    )
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_an_unbilled_run_that_a_unit_claims_is_refused_not_billed(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """``charge_quota=False`` buys no unit and takes nobody else's either.

    A study cell is admitted unbilled, so no linkage is written and the study's
    own unit is unreachable by construction. Here that construction is broken
    from outside: a unit is stamped with the cell's run id while the engine
    runs. The cell then has two dishonest options — bill a unit its admission
    refused to reserve, or publish a result and leave that unit held forever —
    and refuses both. The study's unit, which is the row the pre-linkage rule
    used to reach by timestamp, is not touched by any of it.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    user, version, experiment = _objects(db, version_id, user_id)

    study_reservation = QuotaReservation(
        user_id=user_id, quota_type=_QUOTA, amount=1, status="reserved"
    )
    db.add(study_reservation)
    db.flush()
    study = RobustnessStudy(
        experiment_id=experiment.id,
        version_id=version_id,
        user_id=user_id,
        kind="cost_scenarios",
        status="running",
        config={},
        plan={"cells": []},
        authorization_reservation_id=study_reservation.id,
        started_at=_now(),
    )
    db.add(study)
    db.commit()
    study_id = study_reservation.id
    original = research_run_service.run_backtest

    def claim_the_cell(spec, *args, **kwargs):
        run_id = _runs_for(version_id)[0]

        def mutate(session):
            session.add(
                QuotaReservation(
                    user_id=user_id,
                    quota_type=_QUOTA,
                    amount=1,
                    status="reserved",
                    research_run_id=run_id,
                )
            )

        _committed_elsewhere(mutate)
        return original(spec, *args, **kwargs)

    monkeypatch.setattr(research_run_service, "run_backtest", claim_the_cell)

    run = execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        data_source="stored",
        provider_id=_PROVIDER,
        charge_quota=False,
    )

    assert run.status == RunStatus.failed, (
        "an unbilled run with an attributed unit is a disagreement, not a bill"
    )
    assert run.metrics is None and run.result_json is None
    assert "admitted as unbilled" in run.error_details
    by_id = {row.id: (row.status, row.research_run_id) for row in _units(user_id)}
    assert by_id[study_id] == ("reserved", None), (
        "the study's unit stayed the study's throughout"
    )
    assert [
        status for status, named in by_id.values() if named == run.id
    ] == ["reserved"], "the claim was neither settled nor released"
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 2


def test_a_broken_settlement_does_not_publish_a_completed_run(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """No partial result escapes, and nothing is billed for one.

    The settlement raises after the completed write, so that write and its
    result are discarded and the attempt is closed as failed with its unit
    released. A completed row beside an unsettled unit is the disagreement this
    branch exists to prevent.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    def ledger_unavailable(*args, **kwargs):
        raise RuntimeError("quota ledger unavailable")

    monkeypatch.setattr(
        research_run_service, "settle_reservation", ledger_unavailable
    )

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed, (
        "an unbilled run cannot be published as completed"
    )
    assert run.metrics is None and run.result_json is None
    assert "quota ledger unavailable" in run.error_details, (
        "the reason the ledger refused is the reason on record"
    )
    assert _event_types(run_id) == {"research_run_started", "research_run_failed"}
    assert _linked_unit(run_id).status == "released"
    assert get_quota_used(db, user_id, _QUOTA) == 0


def test_a_broken_completion_audit_leaves_nothing_billed(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """The outcome and its audit are one commit, or neither.

    Here the engine returned, the result was written and the unit settled
    cleanly — then the audit write failed. Everything after the last commit has
    to go together: a completed run with no completion event, or a settled unit
    beside a run nobody billed, are both ledger-versus-work disagreements.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    original_emit = research_run_service.emit_event

    def emit_except_completion(*args, **kwargs):
        if kwargs.get("event_type") == "research_run_completed":
            raise RuntimeError("audit store unavailable")
        return original_emit(*args, **kwargs)

    monkeypatch.setattr(research_run_service, "emit_event", emit_except_completion)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    run_id = UUID(resp.json()["id"])
    run = _run_row(run_id)
    assert run.status == RunStatus.failed
    assert run.metrics is None and run.result_json is None
    assert "audit store unavailable" in run.error_details
    assert _event_types(run_id) == {"research_run_started", "research_run_failed"}
    assert _linked_unit(run_id).status == "released"
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_a_run_that_cannot_be_written_off_is_reported_not_masked(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """A failure this path cannot record is raised, not returned as success.

    The engine already failed and the release fails too, so there is no honest
    terminal state to write. The only acceptable answers are an exception and
    nothing published: the admitted row stays active and keeps its unit, which
    is a true statement, rather than becoming completed or failed on the strength
    of a write that did not happen.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    def explode(spec, *args, **kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(research_run_service, "run_backtest", explode)

    def release_unavailable(*args, **kwargs):
        raise RuntimeError("quota ledger unavailable")

    monkeypatch.setattr(
        research_run_service, "release_reservation", release_unavailable
    )

    with pytest.raises(RuntimeError):
        _start_run(client, auth_headers, version_id)

    rows = _runs_for(version_id)
    assert len(rows) == 1
    run = _run_row(rows[0])
    assert run.status == RunStatus.running, (
        "a write-off that failed must not flip the row anyway"
    )
    assert run.metrics is None and run.completed_at is None
    run_id = rows[0]
    assert _linked_unit(run_id).status == "reserved"
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert _event_types(run_id) == {"research_run_started"}


# ── 四、who may admit, and what a refusal says ───────────────────────────────


def test_a_committed_active_run_blocks_a_second_admission(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """The re-check happens after the lock, against the committed state.

    The second request is attempted from another session while the first is
    inside the engine — a window the old single-transaction path could not
    express at all, because there was no committed row to find. What this binds
    is the decision rule (a committed active row refuses, and refusing writes
    nothing). It does not show two PostgreSQL connections serialised on the
    version row lock: this lane has one connection for every session, so the
    mutual-exclusion half is NOT RUN.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    original = research_run_service.run_backtest
    seen = {}

    def probe(spec, *args, **kwargs):
        nested = TestSession()
        try:
            nested_user = nested.query(User).filter(User.id == user_id).one()
            nested_version = nested.query(ExperimentVersion).filter(
                ExperimentVersion.id == version_id
            ).one()
            nested_experiment = nested.query(Experiment).filter(
                Experiment.id == nested_version.experiment_id
            ).one()
            try:
                execute_backtest_run(
                    nested,
                    user=nested_user,
                    version=nested_version,
                    experiment=nested_experiment,
                    data_source="stored",
                    provider_id=_PROVIDER,
                )
                seen["refused"] = False
            except HTTPException as exc:
                seen.update(
                    refused=True, status_code=exc.status_code, detail=exc.detail
                )
            seen["rows"] = len(
                nested.query(ResearchRun.id).filter(
                    ResearchRun.experiment_version_id == version_id
                ).all()
            )
            seen["reserved"] = _get_reserved(nested, user_id, _QUOTA)
        finally:
            nested.close()
        return original(spec, *args, **kwargs)

    monkeypatch.setattr(research_run_service, "run_backtest", probe)

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 201, resp.text
    assert seen["refused"] is True
    assert seen["status_code"] == 409
    assert str(UUID(resp.json()["id"])) in seen["detail"]
    assert seen["rows"] == 1, "a refusal admits nothing"
    assert seen["reserved"] == 1, "and holds no second unit"
    assert len(_runs_for(version_id)) == 1
    assert get_quota_used(db, user_id, _QUOTA) == 1
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_a_terminal_run_leaves_the_version_admittable_again(
    db, client, auth_headers, stored_dataset
):
    """Two finished attempts are two runs and two units — no silent reuse.

    The refusal exists to stop the same work being billed twice at the same
    time, not to lock a version forever. Each new attempt links its own unit,
    which is what keeps the ledger reconcilable afterwards.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    first = _start_run(client, auth_headers, version_id)
    second = _start_run(client, auth_headers, version_id)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_id = UUID(first.json()["id"])
    second_id = UUID(second.json()["id"])
    assert second_id != first_id

    units = [_linked_unit(run_id) for run_id in (first_id, second_id)]
    assert all(unit is not None and unit.status == "settled" for unit in units)
    assert len({unit.id for unit in units}) == 2, "one unit cannot answer for both"
    assert len(_runs_for(version_id)) == 2
    assert get_quota_used(db, user_id, _QUOTA) == 2
    assert _get_reserved(db, user_id, _QUOTA) == 0


def test_a_resubmitted_request_is_a_new_run_not_a_replay(
    db, client, auth_headers, stored_dataset
):
    """Persisted idempotency is NOT IMPLEMENTED in this slice, said out loud.

    A client that lost the response and repeats an identical request cannot be
    recognised — nothing about the request is stored as a key — so the second
    call admits a new run and bills a new unit. This test pins that gap rather
    than papering over it. The three duplicate shapes are therefore: an active
    run → 409 (the test above); a terminal run then an intentional re-run → new
    run, new unit; a lost response then a resubmit → new run, new unit, which is
    what is measured here and is indistinguishable from the second case without
    a durable Idempotency-Key lane. That lane is a separate bounded work item,
    not something this slice quietly builds.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)
    params = {"data_source": "stored", "provider_id": _PROVIDER}

    first = client.post(
        f"/api/v1/versions/{version_id}/runs", params=params, headers=auth_headers
    )
    replay = client.post(
        f"/api/v1/versions/{version_id}/runs", params=params, headers=auth_headers
    )

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] != first.json()["id"]
    assert get_quota_used(db, user_id, _QUOTA) == 2, (
        "no key is persisted, so a replay is billed as new work"
    )


def test_another_account_cannot_read_an_active_run_id(
    db, client, auth_headers, stored_dataset
):
    """Ownership is answered before admission, so a 409 is not an oracle.

    The refusal detail carries a run id, which is why the duplicate check cannot
    move in front of the ownership test: another account must get 403 and learn
    nothing about this user's rows.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id(db)

    assert _start_run(client, auth_headers, version_id).status_code == 201
    settled_run_id = _runs_for(version_id)[0]

    # A still-active attempt of the same version, which is the id a refusal
    # would otherwise hand to a stranger.
    live = ResearchRun(
        experiment_version_id=version_id,
        engine_version="test",
        config_hash="0" * 64,
        status=RunStatus.running,
        started_at=_now(),
    )
    db.add(live)
    db.commit()

    other = _headers(client, "other-admission@alethra.io")
    resp = _start_run(client, other, version_id)

    assert resp.status_code == 403, resp.text
    assert str(settled_run_id) not in resp.text
    assert str(live.id) not in resp.text
    assert len(_runs_for(version_id)) == 2, "a refusal adds no row"
    assert _get_reserved(db, user_id, _QUOTA) == 0


# ── 五、every lane that admits goes through this lock ────────────────────────
def test_only_the_admission_constructs_a_run_row():
    """One constructor, so one lock and one billing decision.

    The 409 and the linked unit are worth nothing if another lane can put a
    ``research_runs`` row in beside them. This scans the package the app ships
    (not the tests, which seed rows by hand on purpose): the name occurs twice —
    the ``class`` statement in ``models/`` and one instantiation in ``services/``
    — and that one sits inside the function that takes the version lock. Both
    formal entries, the API route and a robustness study cell, reach it through
    ``execute_backtest_run``, which is the only caller.

    A new lane that writes its own run row fails here until it goes through the
    admission. That is the shared protocol enforced by a measurement instead of
    by a paragraph in a handoff.
    """
    import inspect

    from app.api import runs as runs_api
    from app.services import robustness as robustness_service

    # .../app/services/research_runs.py -> .../app, so the scan covers the
    # package and not just the directory the service happens to sit in.
    app_dir = Path(inspect.getsourcefile(research_run_service)).resolve().parent.parent
    sites = {}
    for path in sorted(app_dir.rglob("*.py")):
        count = len(re.findall(r"\bResearchRun\(", path.read_text(encoding="utf-8")))
        if count:
            sites[path.relative_to(app_dir).as_posix()] = count

    assert sites == {
        "models/research_run.py": 1,  # the class statement itself
        "services/research_runs.py": 1,
    }, f"a run row is constructed outside the admission: {sites}"

    admit_source = inspect.getsource(research_run_service._admit_research_run)
    assert "ResearchRun(" in admit_source
    assert "with_for_update" in admit_source, (
        "the row and the version lock are written by the same function, or the "
        "lock guards nothing"
    )

    lanes = {
        "POST /versions/{id}/runs": runs_api.create_run,
        "robustness study cell": robustness_service._run_cell,
    }
    for name, lane in lanes.items():
        assert "execute_backtest_run(" in inspect.getsource(lane), (
            f"{name} no longer reaches the admission it is billed by"
        )
