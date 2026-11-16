"""What a research run whose process is gone does — and does not — get done to it.

The lane under test is the one a customer drives: ``POST /api/v1/versions/{id}/runs``
→ a ``ResearchRun`` row → quota reserved then settled. This file writes the two
states an interrupted attempt leaves:

* a ``running`` row beside a ``reserved`` quota row — availability is counted as
  ``used + reserved``, so a leak spends the lifetime allowance;
* a second submit of the same version while the first is still live.

Evidence scope, stated because it changed on 2026-11-15. Before that change the
request path did not produce either state: ``execute_backtest_run`` inserts the
run, reserves and commits once, when the engine has already returned, so a
process that dies mid-engine takes the whole transaction with it and leaves **no
row and no reservation**. Durable admission (``_admit_research_run``) moved that
commit to before execution, so a ``running`` row beside a ``reserved`` unit is a
state the platform now really creates — and one that stays until an outcome is
written.

Recovery is fail-closed in the strong sense: this module has no release entry at
all. ``reclaim_abandoned_research_runs`` reads the aged rows, lists them as
candidates and changes nothing, and the function that used to release units for
runs a caller named is gone rather than guarded — a caller-supplied list of run
ids is a claim about a transaction, and the previous revision of this file
released a unit for a run its own test had never closed. Ownership of a unit is
now held by one act only: the conditional terminal write inside
``execute_backtest_run``, which is why the tests below read a refusal as "the
formal lane still settles what it owns" and never as "a sweep can free it".
``_seed_attempt`` writes both ledger shapes: the default is an unattributed pair
(``research_run_id`` NULL), and ``link=True`` stamps the run the way
``_admit_research_run`` does. The state a real interrupted attempt leaves — a
running row beside a unit that names it — is produced by the formal path in
``test_a_process_that_dies_after_admission_leaves_a_run_and_a_held_unit`` here
and in ``test_research_run_admission.py``.

Persistence, restart and account isolation are exercised at this layer only
(SQLite, whose test engine shares one connection through ``StaticPool``, so no
cross-transaction interleaving is possible here and "another session" is not an
independent PostgreSQL connection). A real process kill, a PostgreSQL run and a
browser are separate evidence levels and are not claimed.
"""

from datetime import date, timedelta
from uuid import UUID

import pytest

from app.auth.entitlements import (
    _get_reserved,
    get_quota_used,
)
from app.config import get_settings
from app.lib.timeutil import utcnow
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.quota import QuotaReservation
from app.models.research_run import ResearchRun, RunStatus
from app.models.robustness import RobustnessStudy
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services import research_runs as research_run_service
from app.services.market_data_storage import MarketDataStorage
from app.services.research_runs import (
    execute_backtest_run,
    find_live_research_run,
    reclaim_abandoned_research_runs,
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

# Older than any configured stale window, so a seeded row is unambiguously
# abandoned; the test suite never waits for recovery.
_ABANDONED_AGE = timedelta(hours=2)

# Younger than the default 900 s window (``research_run_stale_after_seconds``),
# so a seeded row is still a run that may be executing.
_LIVE_AGE = timedelta(seconds=250)


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
            "password": "recovery-pass-123",
            "display_name": "Recovery Tester",
            "user_type": "individual",
        },
    )
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "recovery-pass-123"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_experiment(client, headers):
    resp = client.post(
        "/api/v1/experiments/",
        json={
            "title": "Recovery slice",
            "hypothesis": "An interrupted run must end in a stated outcome.",
            "strategy_spec": _SPEC,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    # A UUID, not the JSON string: the ORM bind processor for the id columns
    # needs the object, and every comparison below is against an ORM value.
    return UUID(resp.json()["current_version_id"])


def _start_run(client, headers, version_id):
    return client.post(
        f"/api/v1/versions/{version_id}/runs",
        params={"data_source": "stored", "provider_id": _PROVIDER},
        headers=headers,
    )


def _user_id(email="test@alethra.io"):
    db = TestSession()
    try:
        return db.query(User).filter(User.email == email).first().id
    finally:
        db.close()


def _version_and_experiment_ids(version_id):
    db = TestSession()
    try:
        version = db.query(ExperimentVersion).filter(
            ExperimentVersion.id == version_id
        ).first()
        return version.id, version.experiment_id
    finally:
        db.close()


def _seed_attempt(
    db, *, version_id, user_id, started_at, reserve=True, reserved_at=None, link=False
):
    """Write a ``running``+``reserved`` pair, unlinked unless ``link`` is asked for.

    Durable admission makes this shape reachable by killing a process
    mid-backtest — the row and the unit are committed before the engine reads a
    bar — but these tests still write it by hand, because nothing here kills a
    process. ``link=False`` (the default) leaves ``research_run_id`` NULL: an
    unattributed pair, which is the state the release rule must refuse and the
    shape a pre-linkage ledger has. ``link=True`` stamps the run id the way
    ``_admit_research_run`` does, which is the only shape a caller can prove
    ownership of. Within the seeded shape the order mirrors the admission:
    reserve, then stamp ``started_at``, row stays ``running`` until a result is
    published — the reservation is written a moment before the stamp, not after.
    The gap is a parameter because nothing bounds it: a process can reserve and
    then be descheduled before the stamp lands.
    """
    run = ResearchRun(
        experiment_version_id=version_id,
        engine_version="test",
        config_hash="0" * 64,
        status=RunStatus.running,
        started_at=started_at,
    )
    db.add(run)
    db.flush()
    reservation = None
    if reserve:
        reservation = QuotaReservation(
            user_id=user_id,
            quota_type=_QUOTA,
            amount=1,
            status="reserved",
            research_run_id=run.id if link else None,
            created_at=reserved_at or started_at - timedelta(seconds=1),
        )
        db.add(reservation)
    db.commit()
    return run, reservation


def _runs_for(db, version_id):
    return (
        db.query(ResearchRun)
        .filter(ResearchRun.experiment_version_id == version_id)
        .all()
    )


def _write_an_outcome(db, run_id, *, status=RunStatus.failed):
    """A terminal write, by the predicate the publishing path uses.

    This is the stand-in for "the writer that owns the row came back", not a
    recovery implementation: it is the only act that unblocks a version whose
    admitted row is stuck, and several tests below need it to show that the
    refusal is what an outcome ends, not what a sweep guesses.
    """
    flipped = (
        db.query(ResearchRun)
        .filter(
            ResearchRun.id == run_id,
            ResearchRun.status.in_(research_run_service.NON_TERMINAL_RUN_STATUSES),
        )
        .update(
            {"status": status, "completed_at": utcnow()},
            synchronize_session=False,
        )
    )
    db.commit()
    return flipped


def test_a_clean_run_leaves_no_open_reservation(db, client, auth_headers, stored_dataset):
    """Settling is atomic in the committed state: the unit moves reserved→used."""
    version_id = _create_experiment(client, auth_headers)
    resp = _start_run(client, auth_headers, version_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"

    user_id = _user_id()
    assert _get_reserved(db, user_id, _QUOTA) == 0
    assert get_quota_used(db, user_id, _QUOTA) == 1
    assert find_live_research_run(db, version_id=version_id) is None


def test_the_stale_window_is_configuration_and_the_recovery_knob_is_gone():
    """No hard-coded age, and no switch that would turn recovery back on.

    The window is kept as a named setting because the refusing pass still reads
    it to decide what counts as aged. ``research_run_recovery_enabled`` was
    deleted with its startup sweep: a setting nobody on a formal path reads is
    how ``LIVE_TRADING_ENABLED`` became a false affordance, and a flag that looks
    like it would resume closing rows is exactly the thing a fail-closed lane
    must not offer.
    """
    assert get_settings().research_run_stale_after_seconds == 900
    assert not hasattr(get_settings(), "research_run_recovery_enabled")


def test_reclaim_refuses_an_abandoned_run_and_moves_nothing(
    db, client, auth_headers, stored_dataset
):
    """The aged row is listed, not written off.

    Closing it on age alone would record a ``failed`` outcome nobody observed,
    hand back a unit whose process may still be computing, and report a cleanup
    to the caller that was never performed. Both halves of the old behaviour are
    asserted absent here: the summary says it refused, and every row the
    interruption left is still in the state it was left in.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    run, reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )
    assert _get_reserved(db, user_id, _QUOTA) == 1

    summary = reclaim_abandoned_research_runs(db, version_id=version_id)

    assert summary["refused"] is True
    assert summary["candidate_run_ids"] == [run.id], "the aged row is at least visible"
    assert summary["run_ids"] == [], "no run was closed"
    assert summary["released_reservation_ids"] == [], "no unit was freed"
    assert "no run was closed" in summary["reason"].lower()
    db.expire_all()

    assert run.status == RunStatus.running
    assert run.error_details is None, "a refusal writes no outcome, not even a failed one"
    assert run.metrics is None
    assert run.completed_at is None
    assert reservation.status == "reserved"
    assert _get_reserved(db, user_id, _QUOTA) == 1, "still held"
    assert get_quota_used(db, user_id, _QUOTA) == 0, "and still not spent"
    assert (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_type == "run", AuditEvent.entity_id == str(run.id))
        .count()
        == 0
    ), "a refusal leaves no audit trail to misread as an observed failure"
    assert find_live_research_run(db, version_id=version_id) is not None, (
        "and the version is still blocked, which is the cost of the refusal"
    )


def test_a_refusal_is_stable_and_leaves_a_recent_attempt_unlisted(
    db, client, auth_headers, stored_dataset
):
    """Idempotence, restated for a pass that acts.

    The old assertion was "the second sweep finds nothing to do", which was only
    true because the first sweep had already destroyed the evidence. Refusing is
    trivially repeatable: the same candidate is listed again and again, and a row
    inside the window is never listed at all — the two facts a caller needs
    before it decides to escalate anything.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    abandoned, abandoned_reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )

    first = reclaim_abandoned_research_runs(db)
    assert first["candidate_run_ids"] == [abandoned.id]

    live_run, live_reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - timedelta(seconds=5)
    )
    second = reclaim_abandoned_research_runs(db)

    assert second["candidate_run_ids"] == [abandoned.id], "the young row is not aged"
    assert second["refused"] is True
    assert second["run_ids"] == [] and second["released_reservation_ids"] == []
    db.expire_all()
    assert _run_status(db, abandoned.id) == RunStatus.running
    assert _reservation_status(db, abandoned_reservation.id) == "reserved"
    assert live_run.status == RunStatus.running
    assert live_reservation.status == "reserved"


def test_no_entry_takes_a_run_id_list_and_releases_its_unit(
    db, client, auth_headers, stored_dataset
):
    """A run id is not a closure proof, so there is no entry to hand one to.

    Two admitted attempts, each with its own linked unit, arranged so that any
    recency rule picks the wrong one: the unit belonging to the **live** run is
    the newest open unit, and the aged run's unit is the older one. The previous
    revision of this file let a caller pass ``closed_run_ids`` and released the
    unit naming one of them — and its own test demonstrated the hazard by
    releasing a unit for a run nobody had closed. A list of ids is a claim about
    a transaction, not evidence of one, so the entry is gone instead of guarded:
    only the writer whose conditional UPDATE closed the run may touch the unit
    that names it, and a dead process leaves no such writer.

    The pass is called with a window short enough to age both rows, which is the
    strongest version of the temptation. Nothing moves, and the refusal says so.
    """
    import inspect

    version_id = _create_experiment(client, auth_headers)
    other_version = _create_experiment(client, auth_headers)
    user_id = _user_id()
    started_b = utcnow() - _ABANDONED_AGE
    run_a, unit_a = _seed_attempt(
        db,
        version_id=version_id,
        user_id=user_id,
        started_at=utcnow() - _LIVE_AGE,
        reserved_at=utcnow() - _LIVE_AGE,
        link=True,
    )
    run_b, unit_b = _seed_attempt(
        db,
        version_id=other_version,
        user_id=user_id,
        started_at=started_b,
        reserved_at=started_b - timedelta(seconds=5),
        link=True,
    )
    # The trap, stated as data: the unit a recency rule would grab first belongs
    # to the run that is still executing.
    assert unit_a.created_at > unit_b.created_at
    assert run_a.started_at > run_b.started_at

    assert not hasattr(
        research_run_service, "_release_orphaned_research_reservations"
    ), "the release entry the sweep used must not be there to be loosened again"
    assert "closed_run_ids" not in inspect.getsource(research_run_service), (
        "a caller-supplied run list must not appear in this module at all: it "
        "cannot be evidence of anything the caller did in another transaction"
    )

    summary = reclaim_abandoned_research_runs(db, stale_after_seconds=0)

    assert set(summary["candidate_run_ids"]) == {run_a.id, run_b.id}, (
        "both aged rows are at least visible"
    )
    assert summary["refused"] is True
    assert summary["run_ids"] == [] and summary["released_reservation_ids"] == []
    assert _reservation_status(db, unit_a.id) == "reserved", (
        "the live run's unit survived the sweep that used to spend it"
    )
    assert _reservation_status(db, unit_b.id) == "reserved", (
        "and naming the aged run frees nothing either, because nobody proved "
        "its process is gone"
    )
    assert _get_reserved(db, user_id, _QUOTA) == 2
    assert get_quota_used(db, user_id, _QUOTA) == 0


def test_an_unlinked_reservation_is_never_released(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """NULL linkage is UNKNOWN, and UNKNOWN is not a candidate.

    A unit with no ``research_run_id`` predates the linkage column or came from
    an admission that never attributed it. There is no reading of it that says
    which run holds it, so the unit stays reserved for a human to decide about.
    Freeing it is what turned a stalled study into a 500 in its settlement path,
    because ``release_reservation`` leaves a closed row that
    ``settle_reservation`` refuses.

    The control keeps this from being an always-empty assertion, and it is the
    one release this lane genuinely performs: a run whose engine fails closes
    itself and hands back **its own** attributed unit. So a unit does move when
    its owner acts, and the three units below stay reserved because nobody owns
    them in a way the ledger can read — not because releases never happen.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    _abandoned, unlinked = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )
    linked_run, linked_unit = _seed_attempt(
        db,
        version_id=_create_experiment(client, auth_headers),
        user_id=user_id,
        started_at=utcnow() - _ABANDONED_AGE,
        link=True,
    )
    assistant = QuotaReservation(
        user_id=user_id, quota_type="ai_usage", amount=1, status="reserved"
    )
    db.add(assistant)
    db.commit()
    assert unlinked.research_run_id is None

    refused = reclaim_abandoned_research_runs(db, stale_after_seconds=0)

    assert set(refused["candidate_run_ids"]) == {linked_run.id, _abandoned.id}
    assert refused["released_reservation_ids"] == []
    assert _reservation_status(db, unlinked.id) == "reserved", (
        "UNKNOWN is left for a human, not freed by a sweep"
    )
    assert _reservation_status(db, linked_unit.id) == "reserved", (
        "and an aged row that names its unit is still not proof its process died"
    )
    assert _reservation_status(db, assistant.id) == "reserved", (
        "this lane does not own ai_usage, in flight or stranded"
    )

    # Control: the formal path does release, and only its own unit.
    def explode(spec, *args, **kwargs):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(research_run_service, "run_backtest", explode)
    failing_version = _create_experiment(client, auth_headers)
    resp = _start_run(client, auth_headers, failing_version)
    assert resp.status_code == 201, resp.text
    owned = (
        db.query(QuotaReservation.id, QuotaReservation.status)
        .filter(QuotaReservation.research_run_id == UUID(resp.json()["id"]))
        .one()
    )
    assert owned.status == "released", (
        "the writer that won the terminal write handed its own unit back"
    )
    assert _reservation_status(db, unlinked.id) == "reserved"
    assert _reservation_status(db, linked_unit.id) == "reserved", (
        "and it did not reach anybody else's"
    )


def test_the_refusal_holds_nothing_for_any_quota_type(
    db, client, auth_headers, stored_dataset
):
    """The pass declines, so nothing moves in either lane.

    The old version of this test called the release "stranded" the moment no
    live run was visible and asserted four unattributed units went to
    ``released``. That was the dangerous behaviour written down as a
    requirement, and it is what this round removes: a sweep cannot make an
    UNKNOWN into a released unit by waiting for the live rows to disappear.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    abandoned, abandoned_reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )
    assistant = QuotaReservation(
        user_id=user_id, quota_type="ai_usage", amount=1, status="reserved"
    )
    db.add(assistant)
    db.commit()

    summary = reclaim_abandoned_research_runs(db)

    assert summary["candidate_run_ids"] == [abandoned.id]
    assert summary["run_ids"] == []
    assert summary["released_reservation_ids"] == []
    assert _reservation_status(db, abandoned_reservation.id) == "reserved"
    assert _reservation_status(db, assistant.id) == "reserved"
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert _run_status(db, abandoned.id) == RunStatus.running


def test_an_orm_assignment_would_flip_a_terminal_row_back(db, client, auth_headers, stored_dataset):
    """Why the terminal write is a statement, not an attribute.

    SQLAlchemy only touches columns it sees as dirty, and an interrupted run
    never wrote ``completed_at`` in this session — so setting ``status`` on the
    loaded object and committing produces an ``UPDATE ... WHERE id = ?`` with no
    status predicate, which reopens a row another process already closed. The
    guard has to live in the statement that writes.

    The closed state here is written by ``_write_an_outcome``, the conditional
    statement the publishing path uses. Recovery cannot supply it any more: it
    refuses to close rows, so a test that needs a terminal row has to say who
    wrote it.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    run, reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )
    assert _write_an_outcome(db, run.id) == 1

    stale = db.query(ResearchRun).filter(ResearchRun.id == run.id).one()
    assert stale.status == RunStatus.failed
    stale.status = RunStatus.completed
    db.commit()

    assert _run_status(db, run.id) == RunStatus.completed, (
        "the shape this test wrote is the one the publishing path used to have"
    )


def test_the_publishing_write_cannot_reopen_a_closed_run(db, client, auth_headers, stored_dataset):
    """The same row, written by the guard this lane now uses.

    ``completed_values`` is built the way ``execute_backtest_run`` builds it and
    written with the same predicate; because the interrupted run left
    ``completed_at`` null, that column is dirty-free for an ORM write and only a
    statement-level ``WHERE status IN (pending, running)`` refuses the flip.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    run, reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )

    assert _write_an_outcome(db, run.id) == 1

    completed_values = {
        "status": RunStatus.completed,
        "metrics": {"total_return_pct": 1.0, "total_trades": 1},
        "warnings": [],
        "result_json": {"transactions": []},
        "completed_at": utcnow(),
    }
    published = (
        db.query(ResearchRun)
        .filter(
            ResearchRun.id == run.id,
            ResearchRun.status.in_(research_run_service.NON_TERMINAL_RUN_STATUSES),
        )
        .update(completed_values, synchronize_session=False)
    )
    db.commit()

    assert published == 0, "a closed run is not this writer's to publish"
    stayed = (
        db.query(ResearchRun.status, ResearchRun.result_json)
        .filter(ResearchRun.id == run.id)
        .one()
    )
    assert stayed[0] == RunStatus.failed
    assert stayed[1] is None



def test_a_process_that_dies_after_admission_leaves_a_run_and_a_held_unit(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """What an interrupted attempt is now, and what it was before 2026-11-15.

    The handler in ``execute_backtest_run`` never runs on a real kill, so this
    raises ``BaseException`` from inside the engine — the closest offline
    stand-in for a process that stops. The difference from the old path is the
    whole point: the admission committed first, so the attempt survives as a
    readable ``running`` row holding a linked, still-open unit, instead of
    leaving no row and no reservation at all. Nothing here proves a process
    died; it proves the state a death would leave is now observable — which is
    also why the reclaim refuses: the row it sees is identical whether the
    process is gone or merely slow.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id
    ).one()
    experiment = db.query(Experiment).filter(
        Experiment.id == version.experiment_id
    ).one()

    class _ProcessDied(BaseException):
        """Not an ``Exception``: the lane's own handler never runs."""

    def _die(*args, **kwargs):
        raise _ProcessDied()

    monkeypatch.setattr(research_run_service, "run_backtest", _die)

    with pytest.raises(_ProcessDied):
        execute_backtest_run(
            db,
            user=db.query(User).filter(User.id == user_id).one(),
            version=version,
            experiment=experiment,
            data_source="stored",
            provider_id=_PROVIDER,
        )

    db.rollback()
    rows = _runs_for(db, version_id)
    assert len(rows) == 1, "the admitted row is no longer lost with the process"
    assert rows[0].status == RunStatus.running
    assert rows[0].metrics is None

    units = (
        db.query(QuotaReservation)
        .filter(
            QuotaReservation.user_id == user_id,
            QuotaReservation.quota_type == _QUOTA,
            QuotaReservation.status == "reserved",
        )
        .all()
    )
    assert len(units) == 1
    assert units[0].research_run_id == rows[0].id, (
        "the held unit names the run that holds it, so the ledger is readable "
        "without guessing from timestamps"
    )
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert find_live_research_run(db, version_id=version_id) is not None

    # And the version is blocked until someone writes an outcome. Asking the
    # refusing pass with a zero-length window — so this row is unarguably "aged"
    # the moment it is committed — is the strongest version of the temptation,
    # and it still closes nothing and frees nothing.
    summary = reclaim_abandoned_research_runs(
        db, version_id=version_id, stale_after_seconds=0
    )
    assert summary["candidate_run_ids"] == [rows[0].id]
    assert summary["run_ids"] == [], "the admitted row is not written off"
    assert summary["released_reservation_ids"] == [], "its unit is not handed back"
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert get_quota_used(db, user_id, _QUOTA) == 0

    resp = _start_run(client, auth_headers, version_id)
    assert resp.status_code == 409, resp.text
    assert str(rows[0].id) in resp.json()["detail"]


def _reservation_status(db, reservation_id):
    """A column read: the identity map must not answer for another writer."""
    return (
        db.query(QuotaReservation.status)
        .filter(QuotaReservation.id == reservation_id)
        .scalar()
    )


def _run_status(db, run_id):
    return (
        db.query(ResearchRun.status).filter(ResearchRun.id == run_id).scalar()
    )


def test_resubmitting_while_a_run_is_live_is_refused_and_spends_nothing(
    db, client, auth_headers, stored_dataset
):
    """The refusal is taken inside the admission, under the version row lock.

    The route used to answer it from an unlocked pre-read and then clear aged
    rows; the service now re-reads while holding the lock and refuses, which is
    the only place the two can be told apart. The observable contract is the
    same: one row, no second unit, no audit trail for a run that never was.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    live, _reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - timedelta(seconds=5)
    )

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 409, resp.text
    assert str(live.id) in resp.json()["detail"]
    rows = _runs_for(db, version_id)
    assert len(rows) == 1, "a refused submit must not add a second run"
    assert rows[0].status == RunStatus.running
    assert _get_reserved(db, user_id, _QUOTA) == 1
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert (
        db.query(AuditEvent).filter(AuditEvent.entity_type == "run").count() == 0
    ), "a refusal leaves no trail either"


def test_an_interrupted_attempt_blocks_the_version_until_an_outcome_is_written(
    db, client, auth_headers, stored_dataset
):
    """Recovery refuses, so a non-terminal row is answered with a 409.

    Before this change the request path reclaimed an aged row and then ran,
    which made an interruption invisible *and* silently re-billed the version.
    It now refuses: the aged row stays, its unit stays held, and nothing is
    added. Only an outcome written by the writer that owns the row ends it, so
    the second half of this test writes one with the same conditional statement
    the publishing path uses and shows the version becoming admittable.

    The unit is not given back by either act, and that is the price of the
    refusal rather than an oversight: this seeded unit carries no
    ``research_run_id``, so nothing can prove which closed run it belonged to,
    and a bare terminal write is not a settlement. An unattributed unit is left
    reserved for a human to decide about — the same UNKNOWN the release rule
    refuses above.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    abandoned, abandoned_reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )

    resp = _start_run(client, auth_headers, version_id)

    assert resp.status_code == 409, resp.text
    assert str(abandoned.id) in resp.json()["detail"]
    assert len(_runs_for(db, version_id)) == 1, "a refusal admits nothing"
    assert _reservation_status(db, abandoned_reservation.id) == "reserved"
    assert get_quota_used(db, user_id, _QUOTA) == 0

    summary = reclaim_abandoned_research_runs(db, version_id=version_id)
    assert summary["refused"] is True
    assert summary["candidate_run_ids"] == [abandoned.id]

    refused_again = _start_run(client, auth_headers, version_id)
    assert refused_again.status_code == 409, (
        "asking the pass did not unblock the version: it listed the row and "
        "left it alone, which is the whole point of a refusal"
    )
    assert len(_runs_for(db, version_id)) == 1

    assert _write_an_outcome(db, abandoned.id) == 1

    fresh = _start_run(client, auth_headers, version_id)
    assert fresh.status_code == 201, fresh.text
    assert fresh.json()["status"] == "completed"
    assert fresh.json()["id"] != str(abandoned.id)

    db.expire_all()
    assert abandoned.status == RunStatus.failed
    assert _get_reserved(db, user_id, _QUOTA) == 1, "the unattributed unit is still held"
    assert _reservation_status(db, abandoned_reservation.id) == "reserved"
    assert get_quota_used(db, user_id, _QUOTA) == 1, "only the finished run is billed"


def test_a_finished_run_does_not_block_an_independent_rerun(
    db, client, auth_headers, stored_dataset
):
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()

    first = _start_run(client, auth_headers, version_id)
    second = _start_run(client, auth_headers, version_id)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]
    assert len(_runs_for(db, version_id)) == 2
    assert get_quota_used(db, user_id, _QUOTA) == 2


def test_another_account_cannot_read_the_in_flight_run_id(
    db, client, auth_headers, stored_dataset
):
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    live, _reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - timedelta(seconds=5)
    )

    other_headers = _headers(client, "other-recovery@alethra.io")
    resp = _start_run(client, other_headers, version_id)

    assert resp.status_code == 403, resp.text
    assert str(live.id) not in resp.text, "the refusal is not an id leak"


def test_a_late_writer_does_not_resurrect_a_closed_run(
    db, client, auth_headers, stored_dataset, monkeypatch
):
    """The run whose process outlived its own terminal write.

    Something closed the row while the engine was still working. The writer that
    finally returns must not publish over a terminal row, and — which is what
    this round changes — it must not touch the ledger or add an event either. It
    cannot decide any of that by reading the status first: another process can
    commit between the read and the write. One session plays both roles here; on
    PostgreSQL the refusal comes from the same
    ``WHERE status IN (pending, running)`` predicate the closing write uses,
    which is evaluated when the statement runs, not when it was planned.

    The close is written by ``_write_an_outcome``, which changes no quota row —
    so the unit the admission attributed is still ``reserved`` when the loser
    returns, and stays that way. The previous revision of the publishing path
    released it from the losing branch, which both spent a unit its owner may
    still settle and reported a cleanup nobody had decided. What the loser may do
    is read the outcome on record and report the state it found, so the three
    acts it must not perform are each asserted absent.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    experiment_id = _version_and_experiment_ids(version_id)[1]
    original_run_backtest = research_run_service.run_backtest
    seen: dict = {}

    def run_then_close(spec, *args, **kwargs):
        result = original_run_backtest(spec, *args, **kwargs)
        row_id, row_status = (
            db.query(ResearchRun.id, ResearchRun.status)
            .filter(ResearchRun.experiment_version_id == version_id)
            .one()
        )
        seen["run_id"] = row_id
        seen["reclaim"] = reclaim_abandoned_research_runs(
            db, version_id=version_id, stale_after_seconds=0
        )
        seen["status_after_reclaim"] = row_status
        seen["closed_by_the_outcome_write"] = _write_an_outcome(db, row_id)
        return result

    monkeypatch.setattr(research_run_service, "run_backtest", run_then_close)

    experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    version = db.query(ExperimentVersion).filter(
        ExperimentVersion.id == version_id
    ).first()
    user = db.query(User).filter(User.id == user_id).first()
    run = research_run_service.execute_backtest_run(
        db,
        user=user,
        version=version,
        experiment=experiment,
        data_source="stored",
        provider_id=_PROVIDER,
    )

    assert seen["status_after_reclaim"] == RunStatus.running, (
        "the sweep declined to close the row its engine was still publishing"
    )
    assert seen["reclaim"]["run_ids"] == []
    assert seen["reclaim"]["released_reservation_ids"] == []
    assert seen["closed_by_the_outcome_write"] == 1

    db.expire_all()
    assert run.status == RunStatus.failed
    assert run.error_details is None, "the terminal row is the stand-in's, not recovery's"
    assert run.metrics is None, "the result computed after the close was discarded"
    assert _runs_for(db, version_id)[0].status == RunStatus.failed
    assert _get_reserved(db, user_id, _QUOTA) == 1, (
        "the loser neither settled nor released the unit its own admission "
        "attributed: it is left exactly as the writer that closed the row left it"
    )
    assert get_quota_used(db, user_id, _QUOTA) == 0
    assert (
        db.query(AuditEvent.event_type)
        .filter(AuditEvent.entity_type == "run", AuditEvent.entity_id == str(seen["run_id"]))
        .all()
        == [("research_run_started",)]
    ), "a writer that lost the terminal write adds no outcome of its own"


def test_no_formal_path_calls_the_recovery_pass_and_it_cannot_write():
    """Refusing means no caller and no ability, and both are checked.

    Two entry points used to reach the pass: the ``POST`` pre-check (which cleared
    an aged row before admitting) and a startup sweep behind
    ``research_run_recovery_enabled``. Both are gone, and this checks the wiring
    rather than the setting, because a handler that exists but is never registered
    is how a paused feature comes back by accident.

    The second half is the part a wiring check cannot give: the pass itself has no
    write statement left in it, and the release entry it used to lean on is gone
    rather than guarded — a caller-supplied list of run ids is a claim about a
    transaction, not evidence of one, and this file's own previous revision
    released a unit for a run nobody had closed. So the only code in this module
    that may move a quota unit is the terminal path of the run that owns it, which
    the loop below checks by reading the source of every function the module
    defines. What remains unproven is behaviour under a real process kill on
    PostgreSQL — see the module docstring.
    """
    import inspect

    import app.main as app_main
    from app.api import runs as runs_api

    assert not hasattr(app_main, "stop_orphaned_research_runs")

    startup_names = set()
    for handler in app_main.app.router.on_startup:
        func = getattr(handler, "func", handler)
        code = getattr(func, "__code__", None)
        if code is not None:
            startup_names.update(code.co_names)
    assert "reclaim_abandoned_research_runs" not in startup_names

    route_source = inspect.getsource(runs_api.create_run)
    assert "reclaim" not in route_source
    assert "find_live_research_run" not in route_source, (
        "the duplicate decision belongs to the admission, where the lock is held"
    )

    reclaim_source = inspect.getsource(research_run_service.reclaim_abandoned_research_runs)
    for write in (".update(", "RunStatus.failed", "emit_event", "db.commit()"):
        assert write not in reclaim_source, f"{write!r} would make this pass act again"

    assert not hasattr(
        research_run_service, "_release_orphaned_research_reservations"
    ), "the sweep entry must not exist to be loosened later"
    module_source = inspect.getsource(research_run_service)
    assert "closed_run_ids" not in module_source, (
        "a run-id list handed to a release is not a closure proof"
    )

    own_definitions = [
        obj
        for obj in vars(research_run_service).values()
        if callable(obj)
        and getattr(obj, "__module__", None) == research_run_service.__name__
    ]
    assert own_definitions, "an empty scan would pass every claim below it"
    for act in ("settle_reservation(", "release_reservation("):
        owners = sorted(
            {obj.__name__ for obj in own_definitions if act in inspect.getsource(obj)}
        )
        assert owners == ["execute_backtest_run"], (
            f"{act} is called by {owners}; only the writer that won the "
            "terminal transition for the run a unit names may bill or free it"
        )


def test_a_study_claimed_reservation_is_out_of_reach_of_this_lane(
    db, client, auth_headers, stored_dataset
):
    """A study's unit is unreachable because of what it names, not a special case.

    A robustness study reserves one unit for the whole plan and settles it from
    ``_finalize_authorization``, which raises when the row is no longer
    ``reserved`` — so a research-run sweep that freed it would turn a stalled study
    into a 500 in its own settlement path. The study's unit carries NULL in
    ``quota_reservations.research_run_id`` and is claimed by the study row
    instead, so neither the settlement read nor the write-off release can reach
    it: both go through the linkage, and there is none.

    The check that keeps this from resting on an absent function is the linkage
    itself: ``_linked_reservation`` for the aged run returns None, which is the
    only route this lane has to a unit.
    """
    version_id = _create_experiment(client, auth_headers)
    user_id = _user_id()
    experiment_id = (
        db.query(ExperimentVersion)
        .filter(ExperimentVersion.id == version_id)
        .first()
        .experiment_id
    )

    abandoned, abandoned_reservation = _seed_attempt(
        db, version_id=version_id, user_id=user_id, started_at=utcnow() - _ABANDONED_AGE
    )
    study_reservation = QuotaReservation(
        user_id=user_id,
        quota_type=_QUOTA,
        amount=1,
        status="reserved",
        created_at=utcnow() - _ABANDONED_AGE,
    )
    db.add(study_reservation)
    db.flush()
    study = RobustnessStudy(
        experiment_id=experiment_id,
        version_id=version_id,
        user_id=user_id,
        kind="cost_scenarios",
        status="running",
        config={},
        plan={"cells": []},
        # After the flush, so the id is the generated one and not None: an
        # unset claim would silently put this reservation back in play.
        authorization_reservation_id=study_reservation.id,
        started_at=utcnow() - _ABANDONED_AGE,
    )
    db.add(study)
    db.commit()
    assert study_reservation.research_run_id is None

    summary = reclaim_abandoned_research_runs(db, stale_after_seconds=0)
    assert summary["candidate_run_ids"] == [abandoned.id]
    assert summary["released_reservation_ids"] == []
    assert research_run_service._linked_reservation(db, run_id=abandoned.id) is None

    db.expire_all()
    assert study_reservation.status == "reserved", "the study lane kept its unit"
    assert abandoned_reservation.status == "reserved"
    assert _run_status(db, abandoned.id) == RunStatus.running
    assert _get_reserved(db, user_id, _QUOTA) == 2
