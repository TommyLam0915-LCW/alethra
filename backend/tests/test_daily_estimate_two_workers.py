"""#209, the other half: two OS processes, one window, one bill per business key.

Why this file exists
--------------------
``tests/test_daily_model_estimate.py::
test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice``
proves the *interleaving* is answered correctly: worker A re-reads the billing
ledger inside its own transaction and steps back from an item worker B has
already paid for. That node runs two transactions in one process, because what
it tests is which read the decision follows. It says nothing about two real
processes on a real server, and the difference is not cosmetic — the seat that
stops the second worker is a ``pg_advisory_xact_lock``, which SQLite has no way
to take. This file is where that lock is raced.

The nodes in this file, and what each one can prove
---------------------------------------------------
``test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier``
    No database at all. It pins the machinery: the spawn start method really
    starts two OS processes (different pids from each other and from the test),
    and the barrier and the events do synchronise them. Runs on every lane,
    because a race harness whose own process plumbing is unproven is a race
    harness that cannot fail.

``test_the_race_verdict_*``, ``test_every_race_criterion_has_been_seen_to_refuse_something``
    The judgement, tested against captures this file writes out rather than
    against a server: one accepted capture and sixteen mutated ones, plus the
    gate that every criterion in ``CRITERIA`` appears in at least one of those
    sixteen. They run on every lane, and they are the only place the A6
    criteria can be shown to bite before a PostgreSQL run is paid for: a
    criterion nobody has seen refuse is a criterion nobody has seen. The
    accepted capture is one of them, because a judgement that refuses
    everything is as useless as one that accepts everything.

``test_two_processes_on_one_window_bill_each_business_key_once``
    PostgreSQL-only, and **prepared but NOT RUN**: no PostgreSQL lane has been
    approved for this candidate, so nothing asserts the row-lock behaviour today.
    It skips on the SQLite lane by name, which is why the SQLite pass count does
    not contain it.

The synchronisation method, exactly
-----------------------------------
Each process waits on one ``multiprocessing.Barrier`` immediately before it
calls the tick, then wraps :func:`app.services.daily_estimates.claim_estimate`
so that it publishes an ``Event`` the moment it is standing at the claim and
blocks until the *peer's* event is set. Both workers are therefore inside
``_estimates_for_owner``, past their tick-open ledger read, before either has
priced anything. Only then does the real claim run, and whichever one arrives
second waits on ``pg_advisory_xact_lock`` for the first one's billing commit.

That wait is the thing being claimed, so it is what gets measured — and a
timestamp at the door of a function does not measure it. A process that returns
late from a call has only been shown to return late; it has not been shown to
have blocked, and it could have reached the same answer from a ledger read that
had nothing to do with its peer. The test process therefore keeps a third
connection open for the whole race and samples ``pg_locks``,
``pg_blocking_pids()`` and ``pg_stat_activity.wait_event_type`` *together with*
the billing ledger, one frame per instant, and the frame series is judged by
:func:`tests.daily_estimate_race.assess_race`. The worker that takes the seat
also holds it for :data:`_HOLD_S` before pricing: that sleep is a timing change
to this file's own wrapper and nothing else, it decides whether the observer can
see the contention rather than who wins it, and a run that still shows no wait
is judged a refusal instead of a pass.

The timestamps the processes recorded are kept, but demoted to what they are —
an index into the capture. ``max(claim_enter) < min(estimate_enter)`` is still
asserted, as the precondition that the two really overlapped; the verdict comes
from the frames.

What is asserted, and what is deliberately not added
----------------------------------------------------
The verdict is the durable ledger, grouped by the business key
(``owner, skill_unit_id, instrument_id, as_of_date``): each declared instrument
has exactly one ``via=daily_service`` billing row, each billing row has exactly
one matching ``settled`` reservation, and no reservation is left in a
non-terminal state. Each process's own ``learning_jobs`` summary must name the
items it served and refuse the ones it lost, by instrument id, with
``already_estimated_this_window``. The two summaries' ``billed_in_window``
figures are *not* summed — each is a reading of the one ledger at a different
instant — so the only window total quoted here is the row count.

Quota is read for one reason: to prove it did not do the stopping. The refusal
the lane reports has to name the billing ledger, because a unit turned away by a
ceiling would leave this node with a tidy one-bill ledger and no evidence at all
that the seat exists — the distinction A6 asks to keep, and the one a run with
``ai_usage_limit = 1`` cannot make.

Cleanup, and its limits
-----------------------
Under PostgreSQL the autouse session fixture cannot roll this node back: the
tick and the fixture both commit on their own connections. So the node deletes
its own owner-scoped rows in ``finally`` and reads the counts back on a fresh
session, failing on any residue. It removes the rows this lane writes; it does
not touch ``market_data_bars``, which the fixture seeds for a shared test
instrument and which has no owner column to scope a delete by.
"""

from __future__ import annotations

import copy
import json
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
import warnings
from uuid import UUID

import pytest

# ``tests.conftest`` is imported before anything in ``app`` on purpose. A spawn
# child re-imports this module to reach :func:`_worker`, and conftest pins
# ``DATABASE_URL`` to the lane's disposable target (and runs the identity gate)
# at import time; app.database builds its engine from that variable the first
# time it is imported. The order is a safety property, not tidiness.
from tests.conftest import TEST_ENGINE, TestSession

from sqlalchemy import event, func, select, text

from app.models.audit_event import AuditEvent
from app.models.billing import UsageRecord
from app.models.inference import InferenceRun
from app.models.learning_job import LearningJob
from app.models.model_research import ModelResearchRecord, ModelVersion
from app.models.paper_portfolio import DecisionRecord, PaperPortfolio
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.personal_model_selection import PersonalModelSelection
from app.models.personalization import PersonalizationConsent
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.training import TrainingJob
from app.models.user import User
from tests.daily_estimate_race import (
    CAPTURE_SQL,
    CRITERIA,
    LEDGER_REASON,
    QUOTA_REASONS,
    START_CRITERIA,
    assess_race,
    assess_race_start,
)
from tests.test_daily_model_estimate import (
    FRESH,
    SECOND_INSTRUMENT,
    TEST_INSTRUMENT,
    _declare,
    _jobs,
    _open_env,
    _reservations,
    _served,
    _set_quota,
    _tick,
)
from tests.test_successful_inference import (  # noqa: F401  (fixture import)
    personal_env,
    seed_daily_bars,
)

# (model, owner column), in child-before-parent deletion order: the selections
# and the snapshots point at the consents and portfolios below them, and
# ``model_versions`` is cleared by its record ids in :func:`_purge_owner`.
_LANE_ROWS = (
    (UsageRecord, UsageRecord.user_id),
    (QuotaReservation, QuotaReservation.user_id),
    (QuotaCounter, QuotaCounter.user_id),
    (PilotEntitlement, PilotEntitlement.user_id),
    (LearningJob, LearningJob.user_id),
    (InferenceRun, InferenceRun.user_id),
    (TrainingJob, TrainingJob.user_id),
    (DecisionRecord, DecisionRecord.owner_id),
    (PersonalizationDatasetSnapshot, PersonalizationDatasetSnapshot.owner_id),
    (PersonalModelSelection, PersonalModelSelection.owner_id),
    (PersonalizationConsent, PersonalizationConsent.owner_id),
    (PaperPortfolio, PaperPortfolio.owner_id),
)

# Every wait in a child is bounded, so a process that never meets its peer
# raises inside itself instead of leaving the suite holding an open transaction.
_TIMEOUT_S = 60

# How long the worker that took the seat holds it before it prices anything.
# The advisory lock is released by the billing commit, so this is the whole
# window in which the contention is visible to an outside connection; the
# observer polls at :data:`_POLL_S` and needs several frames inside it. The
# sleep sits in this file's wrapper around ``_estimate_one`` and changes no
# product timing: whoever wins the race still wins it by getting the lock.
_HOLD_S = 3.0
_POLL_S = 0.05


def _purge_owner(owner_id: str) -> dict:
    """Delete what this node committed, then count it back from a fresh session.

    The return value is the read-back, not a delete count: a table that still
    holds a row for this owner appears here with a non-zero value, and the node
    fails on it rather than leaving the next PostgreSQL test to trip over it.
    """
    key = UUID(str(owner_id))
    db = TestSession()
    try:
        record_ids = list(
            db.scalars(
                select(ModelResearchRecord.id).where(
                    ModelResearchRecord.owner_id == key
                )
            ).all()
        )
        if record_ids:
            # ``model_research_records.active_version_id`` and
            # ``model_versions.model_research_record_id`` reference each other, so
            # no delete order can empty both: the cycle is broken by nulling the
            # pointer first.
            db.query(ModelResearchRecord).filter(
                ModelResearchRecord.id.in_(record_ids)
            ).update(
                {ModelResearchRecord.active_version_id: None},
                synchronize_session=False,
            )
        for model, column in _LANE_ROWS:
            db.query(model).filter(column == key).delete(synchronize_session=False)
        if record_ids:
            # After the lane rows, because ``personal_model_selections.version_id``
            # and ``inference_runs.model_version_id`` hold the versions below them.
            db.query(ModelVersion).filter(
                ModelVersion.model_research_record_id.in_(record_ids)
            ).delete(synchronize_session=False)
        db.query(ModelResearchRecord).filter(
            ModelResearchRecord.owner_id == key
        ).delete(synchronize_session=False)
        # ``audit_events.actor_id`` points at the user below it, and this lane's
        # own consent and disclosure writes append two such rows per owner.
        db.query(AuditEvent).filter(
            AuditEvent.actor_id == key
        ).delete(synchronize_session=False)
        db.query(User).filter(User.id == key).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    check = TestSession()
    try:
        left = {}
        for model, column in _LANE_ROWS + ((ModelResearchRecord, ModelResearchRecord.owner_id),):
            count = check.scalar(
                select(func.count()).select_from(model).where(column == key)
            )
            if count:
                left[model.__tablename__] = count
        if check.scalar(select(func.count()).select_from(User).where(User.id == key)):
            left["users"] = 1
        return left
    finally:
        check.close()


def _bound_child_waits(engine) -> None:
    """Give the child's connections the same timeouts the test engine sets.

    A blocked advisory lock is the point of the node, but a *leaked* one would
    hold its transaction open across the rest of the lane, so an idle
    in-transaction worker is terminated by the server instead of surviving.
    """

    @event.listens_for(engine, "connect")
    def _timeouts(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET lock_timeout = '30s'")
            cursor.execute("SET statement_timeout = '120s'")
            cursor.execute("SET idle_in_transaction_session_timeout = '90s'")
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# the machinery, proven on its own
# ---------------------------------------------------------------------------


def _harness_worker(slot, ready, arrived, peer_arrived, out):
    """Stand at the barrier, announce it, wait for the peer, report the times."""
    entered = time.time()
    ready.wait(timeout=_TIMEOUT_S)
    arrived.set()
    met = peer_arrived.wait(timeout=_TIMEOUT_S)
    out.put({"slot": slot, "pid": os.getpid(), "entered": entered, "met": met})


def test_the_race_harness_starts_two_os_processes_that_meet_at_a_barrier():
    """The two-process plumbing below is real before it is load-bearing.

    No database, no service: only ``multiprocessing``. If spawn did not give two
    processes, or the barrier and the events did not actually synchronise them,
    the PostgreSQL node would be a test that can never fail.
    """
    ctx = mp.get_context("spawn")
    ready = ctx.Barrier(2)
    arrived = (ctx.Event(), ctx.Event())
    out = ctx.Queue()

    procs = [
        ctx.Process(
            target=_harness_worker,
            args=(slot, ready, arrived[slot], arrived[1 - slot], out),
            name=f"harness-{slot}",
        )
        for slot in (0, 1)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=_TIMEOUT_S)

    assert [p.exitcode for p in procs] == [0, 0], [
        (p.name, p.exitcode, p.is_alive()) for p in procs
    ]
    results = [out.get(timeout=10) for _ in procs]
    pids = {row["pid"] for row in results}
    assert len(pids) == 2, pids
    assert os.getpid() not in pids, "the two sides must not be the test process"
    assert all(row["met"] for row in results), "each side met a live peer at the event"
    # The barrier held: whoever got there first waited for the other one, so the
    # two entered the measured region within one wait of each other.
    assert abs(results[0]["entered"] - results[1]["entered"]) < _TIMEOUT_S, results


# ---------------------------------------------------------------------------
# the race itself — PostgreSQL only, prepared and NOT RUN
# ---------------------------------------------------------------------------


def _observe_frames(engine, owner_id, window, instruments, alive, deadline_s=_TIMEOUT_S):
    """The server's own account of the race, one instant per frame.

    Each frame is one read-only connection: ``pg_locks`` joined to
    ``pg_stat_activity`` for the advisory locks of *this* database, and the
    billing rows for this owner and window, counted from the same instant. The
    connection is opened per frame on purpose -- a pooled read snapshot would
    keep answering "nothing billed yet" after the bill became durable, and the
    release-then-re-read ordering this lane is judged on would be measured
    against a fiction.

    Nothing here asserts. It records, and :func:`assess_race` decides, so that
    the same judgement can be put to a synthetic capture on a lane with no
    server at all.
    """
    frames = []
    deadline = time.time() + deadline_s
    dead_tail = 0
    while True:
        with engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(text(CAPTURE_SQL)).mappings().all()]
            billed = conn.execute(
                select(UsageRecord.metadata_json).where(
                    UsageRecord.user_id == UUID(str(owner_id))
                )
            ).all()
        counts = {instrument: 0 for instrument in instruments}
        for (meta,) in billed:
            meta = meta or {}
            if meta.get("via") != "daily_service" or meta.get("as_of_date") != window:
                continue
            instrument = meta.get("instrument_id")
            if instrument in counts:
                counts[instrument] += 1
        frames.append(
            {
                "t": time.time(),
                "rows": rows,
                "ledger": [
                    {"instrument": instrument, "count": count}
                    for instrument, count in counts.items()
                ],
            }
        )
        # Stop when the work is visibly finished; but a child that died early
        # must not turn the sampling loop into a four-minute wait, so a handful
        # of frames after the last worker exits is enough of a tail to be sure
        # the release was seen, and the assertions in the node then say what
        # actually happened to that child.
        finished = all(count >= 1 for count in counts.values())
        dead_tail = 0 if alive() else dead_tail + 1
        if (not alive() and (finished or dead_tail >= 10)) or time.time() >= deadline:
            return frames
        time.sleep(_POLL_S)


def _ai_usage_used(owner_id) -> int:
    """The one lifetime ``ai_usage`` figure, or 0 when the account spent none."""
    db = TestSession()
    try:
        return int(
            db.scalar(
                select(func.coalesce(func.sum(QuotaCounter.used), 0)).where(
                    QuotaCounter.user_id == UUID(str(owner_id)),
                    QuotaCounter.quota_type == "ai_usage",
                )
            )
            or 0
        )
    finally:
        db.close()


#: More units than this node could ever bill: two declared instruments, two
#: workers, so four charges in the worst (defective) case. The ceiling is set
#: above that on purpose -- a run whose quota was plentiful cannot have been
#: deduplicated by ``reserve_quota`` refusing, so whatever the ledger says is
#: the ledger's own doing. See the module docstring's paragraph on quota.
_AI_CEILING = 5


def _raise_ai_ceiling(owner_id, limit: int) -> None:
    """Give the test owner enough quota that the ceiling cannot be the stopper.

    Update-or-insert rather than insert: the account arrives from the shared
    fixture, and a second entitlement row for one user would leave the gate
    reading one of two figures and this lane reporting the other.
    """
    db = TestSession()
    try:
        rows = (
            db.query(PilotEntitlement)
            .filter(PilotEntitlement.user_id == UUID(str(owner_id)))
            .all()
        )
        if not rows:
            db.add(PilotEntitlement(user_id=UUID(str(owner_id)), ai_usage_limit=limit))
        else:
            for row in rows:
                row.ai_usage_limit = limit
        db.commit()
    finally:
        db.close()


def _claims(results) -> list:
    """Each worker's own account of every claim, with the backend that made it.

    ``pid`` is the OS process and ``backend_pid`` the PostgreSQL backend that ran
    the claim's advisory lock: two namespaces, and only the second one joins to
    ``pg_locks.pid``. A claim that never reached the lock carries ``None``.
    """
    return [
        {
            "slot": row["slot"],
            "pid": row["pid"],
            "backend_pid": mark.get("backend_pid"),
            "instrument": mark["instrument"],
            "already_billed": mark["already_billed"],
            "t": mark["t"],
            "blocked_s": mark["blocked_s"],
        }
        for row in results
        for mark in row["marks"]
        if mark["event"] == "claim_exit"
    ]


def _refusal_reasons(jobs) -> list:
    """Why each run said no, as its own summary wrote it."""
    return [
        str(entry.get("reason"))
        for job in jobs
        for entry in (job["result"].get("refused") or [])
        if isinstance(entry, dict) and entry.get("reason")
    ]


#: Every field a ``claim_enter`` mark is supposed to carry, named here so the
#: capture's schema is one list rather than a copy of whatever a child happened
#: to send. A mark missing its pre-call stamp arrives with that key as ``None``,
#: which the start gate reads as a refusal -- not as a time invented for it.
_START_FIELDS = (
    "seq",
    "owner_id",
    "skill_unit_id",
    "window",
    "instrument",
    "t",
    "call_start_t",
)


def _starts(results) -> list:
    """Every arrival at the claim door, from the worker that stood there."""
    rows = []
    for row in results:
        for mark in row["marks"]:
            if mark.get("event") != "claim_enter":
                continue
            entry = {"slot": row["slot"], "pid": row["pid"]}
            entry.update({field: mark.get(field) for field in _START_FIELDS})
            rows.append(entry)
    return rows


def _estimates(results) -> list:
    """Every arrival at the pricing step, which is what arrivals are ordered against."""
    rows = []
    for row in results:
        seq = 0
        for mark in row["marks"]:
            if mark.get("event") != "estimate_enter":
                continue
            rows.append(
                {
                    "slot": row["slot"],
                    "pid": row["pid"],
                    "seq": seq,
                    "instrument": mark.get("instrument"),
                    "t": mark.get("t"),
                }
            )
            seq += 1
    return rows


def _worker_signals(procs, results) -> list:
    """What each child was last seen doing: pid, exit code, alive, error.

    Read straight after the join, while both are still true. A node that dies at
    its first assertion still hands the capture the two processes it did get,
    which is the difference between "the run failed" and "the run failed and
    here is what the workers did".
    """
    by_slot = {row.get("slot"): row for row in results}
    rows = []
    for slot, proc in enumerate(procs):
        row = by_slot.get(slot) or {}
        rows.append({
            "slot": slot,
            "name": proc.name,
            "pid": row.get("pid", proc.pid),
            "exitcode": proc.exitcode,
            "alive": bool(proc.is_alive()),
            "error": row.get("error"),
        })
    return rows


def _harvest_ledger(owner_key, observation, quota_before) -> None:
    """Fill in the durable books for a run that died before it read them.

    Reading rows is not a judgement. This never calls :func:`assess_race`, so a
    capture assembled here still has its twelve ledger criteria as
    ``not_evaluated`` and ``proven`` false whichever way the rows fall: the point
    is that the failure can be looked at after the purge, not that looking turns
    it into a pass. Keys the body already wrote are left alone.
    """
    if "bills" not in observation:
        observation["bills"] = [
            {
                "instrument": row["metadata"]["instrument_id"],
                "bill_id": str(row["id"]),
                "inference_id": row["metadata"].get("inference_id"),
            }
            for row in _served(owner_key)
            if row["metadata"].get("as_of_date") == FRESH
        ]
    if "reservation_statuses" not in observation:
        observation["reservation_statuses"] = _reservations(owner_key)
    if "refusal_reasons" not in observation:
        observation["refusal_reasons"] = _refusal_reasons(_jobs(owner_key))
    if observation.get("quota_consumed") is None:
        observation["quota_consumed"] = _ai_usage_used(owner_key) - quota_before
    observation["ledger_harvested_in_finally"] = True


#: ``schema`` changes when a capture's meaning changes, not when it grows a key.
#: Version 1 held only a verdict that had already been reached; version 2 has to
#: survive a run that never reached one; version 3 says what each of the seventeen
#: criteria *is* -- version 2 called a criterion ``passed`` for any run that never
#: judged it, so its ``passed`` and this one's do not mean the same thing.
CAPTURE_SCHEMA = "alethra-daily-estimate-race/3"


def _live_criteria(start, verdict) -> dict:
    """Each of the seventeen live criteria as ``passed``/``failed``/``not_evaluated``.

    The five arrival criteria come from :func:`assess_race_start` and the twelve
    ledger criteria from :func:`assess_race`, and only from those two calls on
    this capture. The offline variants in this file decide whether the criteria
    *can* refuse; they are deliberately absent here and can never fill a slot
    that a live judgement left un-evaluated.

    A criterion is credited only when the judgement says it read the evidence for
    it. ``notes["evaluated"]`` is therefore the entry condition, and the absence
    of a refusal is not: the old reading inferred ``passed`` from "nobody named
    you", so a capture that never reached the ledger during the wait reported
    ``key_unbilled_while_blocked`` as held -- five criteria green on a run that
    asked none of their questions. A judgement that carries no ``evaluated`` list
    at all (an older capture, or a verdict assembled by hand) is read as having
    judged nothing, because the alternative is to default a missing key to a pass.
    """
    states: dict = {}
    for judgement, criteria in ((start, START_CRITERIA), (verdict, CRITERIA)):
        reached = []
        if isinstance(judgement, dict):
            named = (judgement.get("notes") or {}).get("evaluated")
            if isinstance(named, list):
                reached = named
            refused = {
                entry.get("id") for entry in (judgement.get("refused") or [])
            }
            for criterion_id, _says in criteria:
                if criterion_id not in reached:
                    states[criterion_id] = "not_evaluated"
                elif criterion_id in refused:
                    states[criterion_id] = "failed"
                else:
                    states[criterion_id] = "passed"
        else:
            for criterion_id, _says in criteria:
                states[criterion_id] = "not_evaluated"
    return states


def _capture_document(
    owner_id, observation, start, verdict, stage, failure, signals, cleanup=None
) -> dict:
    """One race, as far as the run got, with the numbers left unjudged if they were."""
    criteria = _live_criteria(start, verdict)
    tally = {
        state: sum(1 for value in criteria.values() if value == state)
        for state in ("passed", "failed", "not_evaluated")
    }
    declared = len(START_CRITERIA) + len(CRITERIA)
    return {
        "schema": CAPTURE_SCHEMA,
        "owner_id": str(owner_id),
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stage": stage,
        "failure": failure,
        "worker_signals": signals,
        "live_criteria": criteria,
        "criteria_tally": tally,
        "start": start,
        "verdict": verdict,
        # The conjunction of the two live judgements and of nothing else. Either
        # one missing makes this false, so a capture written by a run that died
        # before ``assess_race`` -- or before the start gate -- cannot carry a
        # proven it never earned, whatever the offline controls say below. The
        # tally is re-checked here rather than trusted from the judgements' own
        # flags: seventeen named slots, none failed, none left un-read. A green
        # that rests on questions nobody asked is not a narrower claim than a
        # green that rests on a question somebody answered wrongly.
        "proven": bool(
            start is not None
            and start["proven"]
            and verdict is not None
            and verdict["proven"]
            and len(criteria) == declared
            and tally["failed"] == 0
            and tally["not_evaluated"] == 0
            and tally["passed"] == declared
        ),
        "proven_basis": (
            "proven is true only when assess_race_start and assess_race both ran on "
            "this capture, every one of the "
            f"{declared} criteria is named as evaluated by the judgement that owns "
            "it, and none of them refused; an un-evaluated criterion is counted and "
            "never credited, and a judgement that names no evaluated set is read as "
            "having judged nothing"
        ),
        "cleanup": cleanup,
        "observation": observation,
    }


#: The only connection facts a diagnostic may carry. Each names *where* a run was
#: pointed at without naming *how it was authenticated*.
_CONNECTION_FACTS = ("dialect", "driver", "database", "host", "port")


def _connection_facts(url_text) -> dict:
    """The allow-listed shape of a target, or a refusal that says nothing else.

    The facts come from the parsed string, never from ``get_dialect()``: that call
    loads a dialect plugin -- a side effect a diagnostic has no business causing --
    and it raises for the ``postgres`` alias, which is one of the three spellings a
    real run can carry. Nothing here renders, splits or echoes the authority: the
    username, the password and every query parameter stay unread, so this function
    has no path to re-emitting them. A string it cannot parse is reported as
    refused, because the parse failure's own message quotes the string back.
    """
    if not url_text:
        return {"read": False, "reason": "no target in this environment"}
    try:
        from sqlalchemy.engine import make_url

        url = make_url(str(url_text))
        dialect, _, driver = (url.drivername or "").partition("+")
    except Exception as exc:  # noqa: BLE001 - only the exception's name is safe
        return {"read": False, "reason": f"refused by {type(exc).__name__}"}
    return {
        "read": True,
        "dialect": dialect or None,
        "driver": driver or None,
        "database": url.database,
        "host": url.host,
        "port": url.port,
    }


def _diagnostic(exc: BaseException, *, stage: str) -> dict:
    """A failure reduced to the fields a lane is allowed to print.

    This replaces ``repr(exc)`` and ``str(exc)`` at every point where a capture, a
    warning, or an outcome record could pick up a connection string: a driver
    quotes the authority it was connecting to and SQLAlchemy's URL parser quotes
    the whole string it could not read, so an exception's text is not a channel an
    author can make safe by taking something out of it. What is kept is the
    exception's *identity* -- class, module, the line it surfaced at, and the
    PostgreSQL ``sqlstate`` when the driver carries one -- plus the target in the
    five fields of :data:`_CONNECTION_FACTS`. ``withheld`` says a message exists
    and was deliberately not copied, so a reader can tell a narrow diagnostic from
    an empty one.
    """
    site = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return {
        "class": type(exc).__name__,
        "module": type(exc).__module__,
        "stage": stage,
        "line": site[-1].lineno if site else None,
        "sqlstate": str(sqlstate) if sqlstate is not None else None,
        "connection": _connection_facts(os.environ.get("DATABASE_URL")),
        "withheld": "message and repr, which may carry the full DSN",
    }


def _write_capture(
    owner_id, observation, start, verdict, stage, failure, signals, cleanup=None
) -> dict:
    """Put one race's capture on disk and read it back, when the runner named a path.

    ``pg_locks`` and ``pg_stat_activity`` describe sessions that no longer exist
    by the time this node finishes, and the rows the verdict counted are purged
    in its ``finally``. The frames survive only if they were written while the
    rows were still readable, which is why the node calls this from its
    ``finally`` rather than on the way past the last assertion: a run that died
    at the start gate has to leave the same evidence a passing one does, and
    leaves it with its un-run criteria named as un-run.

    With no ``ALETHRA_RACE_CAPTURE_JSON`` in the environment nothing is written
    and nothing is created: the SQLite lane, and every lane that was not told
    where to put it, leaves no file behind.
    """
    path = os.environ.get("ALETHRA_RACE_CAPTURE_JSON")
    document = _capture_document(
        owner_id, observation, start, verdict, stage, failure, signals, cleanup
    )
    if not path:
        return {"attempted": False, "written": False, "reason": "no capture path in this lane"}
    with open(path, "w") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    with open(path) as handle:
        again = json.load(handle)
    # The two figures that a reader would be misled by, checked through the file
    # rather than assumed from the object that produced it.
    if again.get("schema") != CAPTURE_SCHEMA or again.get("proven") != document["proven"]:
        raise RuntimeError(
            f"the capture at {path} does not read back as written: "
            f"schema={again.get('schema')!r} proven={again.get('proven')!r}"
        )
    return {
        "attempted": True,
        "written": True,
        "path": path,
        "bytes": os.path.getsize(path),
        "proven": document["proven"],
        "stage": stage,
        "criteria_tally": document["criteria_tally"],
    }


def _report_capture(meta: dict, cleanup) -> None:
    """Say out loud what the disk did not take, without replacing the test's failure.

    A warning is the only channel here that cannot mask the exception this node is
    already raising, and a lane that was told where to write and got nothing must
    not be able to read as clean. The deferred check in the node's ``finally``
    turns this into a failure when nothing else went wrong.
    """
    trouble = []
    if meta.get("attempted") and not meta.get("written"):
        trouble.append(
            "capture not written: "
            + json.dumps(meta.get("error") or meta, sort_keys=True, default=str)
        )
    if cleanup and (cleanup.get("residual") or cleanup.get("error")):
        trouble.append(
            "cleanup left "
            + json.dumps(cleanup.get("residual"), sort_keys=True, default=str)
            + " after "
            + json.dumps(cleanup.get("error"), sort_keys=True, default=str)
        )
    for entry in trouble:
        warnings.warn(f"[race-capture] {entry}", stacklevel=2)
    return trouble


def _worker(payload, slot, ready, arrived, out):
    """One estimate tick: its own process, its own engine, its own transaction.

    Runs in a fresh interpreter with no pytest in it, so everything it needs
    arrives in ``payload`` as plain values and everything it learns goes back on
    ``out`` the same way. The database target is taken from the inherited
    ``ALETHRA_TEST_DB`` — never from an argument — and is put through the same
    identity gate the suite's conftest uses before a row is touched.
    """
    marks: list = []
    try:
        import sys

        # What the child binds to is ``DATABASE_URL`` (``app.database`` reads it at
        # import), so that is the value the gate is given. The declared disposable
        # target has to agree with it: whichever of the two names the runner used,
        # a child whose engine points somewhere else must refuse, not guess.
        target = os.environ.get("DATABASE_URL")
        declared = os.environ.get("ALETHRA_TEST_DB") or os.environ.get(
            "ALETHRA_PG_TEST_URL"
        )
        if not target or not declared:
            raise RuntimeError("no disposable test target in the child environment")
        if target != declared:
            raise RuntimeError(
                "the child's DATABASE_URL is not the declared test target; refusing to run"
            )
        sys.path.insert(0, payload["backend_dir"])
        from test_safety_gate import verify_test_database

        verify_test_database(target)

        from app.database import SessionLocal, engine as app_engine
        from app.services import daily_estimates as de

        real_claim = de.claim_estimate
        real_estimate = de._estimate_one

        # Which backend is running this worker's lock is only knowable at the
        # statement that takes it. ``pg_locks.pid`` is the pid of the *backend*
        # serving a connection -- a different number from this process's own OS
        # pid on the same machine (a live run measured 24594/24595 for the
        # workers against 24607/24608 in the lock table) -- and the session's
        # connection is not pinned across the commit the product makes at
        # ``app/services/daily_estimates.py:592``, so a read taken after the call
        # returns could name a connection the pool has since reissued. The
        # ``before_cursor_execute`` handler runs on the very cursor about to
        # carry ``pg_advisory_xact_lock``, which is the same transaction that
        # waits for or holds it.
        lock_backends: list = []

        def _stamp_lock_backend(
            conn, cursor, statement, parameters, context, executemany
        ):
            if "pg_advisory_xact_lock" not in str(statement):
                return
            cursor.execute("SELECT pg_backend_pid()")
            row = cursor.fetchone()
            if row and row[0] is not None:
                lock_backends.append(int(row[0]))

        if app_engine.dialect.name == "postgresql":
            _bound_child_waits(app_engine)
            event.listen(app_engine, "before_cursor_execute", _stamp_lock_backend)

        # This worker's own arrival records, in its own order, held by reference
        # so the pre-call boundary can be written into the mark that is already
        # in ``marks`` rather than beside it.
        enters: list = []

        def traced_claim(db, owner_id, window, skill_unit_id, instrument_id):
            enter = {
                "event": "claim_enter",
                # The business key exactly as the product handed it to the
                # wrapper, not as this file believes it was configured: the gate
                # that reads these has to be shown the seat the call was made
                # for, and a mark that only names the instrument cannot say so.
                "owner_id": str(owner_id),
                "skill_unit_id": str(skill_unit_id),
                "window": str(window),
                "instrument": instrument_id,
                "seq": len(enters),
                "t": time.time(),
            }
            enters.append(enter)
            marks.append(enter)
            arrived[slot].set()
            if not arrived[1 - slot].wait(timeout=_TIMEOUT_S):
                raise RuntimeError("the peer never reached the claim")
            started = time.monotonic()
            # Stamped after the barrier and on the line above the call, so an
            # arrival that turns out to sit after this instant is caught by the
            # gate instead of counting as a claim that was still waiting to get
            # in. The barrier is above the stamp, never below it.
            enter["call_start_t"] = time.time()
            del lock_backends[:]
            billed = real_claim(db, owner_id, window, skill_unit_id, instrument_id)
            marks.append(
                {
                    "event": "claim_exit",
                    "instrument": instrument_id,
                    "t": time.time(),
                    "already_billed": billed,
                    "blocked_s": time.monotonic() - started,
                    # The backend that ran this claim's advisory lock, stamped
                    # before the wait began. ``None`` means this claim never
                    # reached that statement, and the judgement refuses on it
                    # instead of guessing from ``pid``.
                    "backend_pid": lock_backends[-1] if lock_backends else None,
                }
            )
            return billed

        def traced_estimate(db, owner, portfolio, instrument_id, as_of, **kwargs):
            marks.append(
                {
                    "event": "estimate_enter",
                    "instrument": instrument_id,
                    "t": time.time(),
                }
            )
            # Whoever took the seat holds the advisory lock until the billing
            # commit, so this sleep *is* the window the observer polls inside.
            # It belongs to this wrapper and to no product timing: no billing
            # decision reads it, and it does not decide who wins -- it only
            # decides whether a wait that happened can be seen to have happened.
            time.sleep(payload["hold_s"])
            marks.append(
                {"event": "hold_slept", "instrument": instrument_id, "t": time.time()}
            )
            answer = real_estimate(
                db, owner, portfolio, instrument_id, as_of, **kwargs
            )
            marks.append(
                {"event": "estimate_exit", "instrument": instrument_id, "t": time.time()}
            )
            return answer

        de.claim_estimate = traced_claim
        de._estimate_one = traced_estimate

        ready.wait(timeout=_TIMEOUT_S)

        outcome = de.run_daily_estimate_tick(
            scope=payload["scope"],
            window=payload["window"],
            user_id=UUID(payload["owner_id"]),
            only_skill_unit_id=UUID(payload["skill_unit_id"]),
        )
        jobs = outcome.get("jobs") or []
        if not jobs:
            raise RuntimeError(f"the tick served nobody: skipped={outcome.get('skipped')}")
        db = SessionLocal()
        try:
            row = (
                db.query(LearningJob)
                .filter(LearningJob.id == UUID(jobs[0]["job_id"]))
                .one()
            )
            summary = {
                "job_id": str(row.id),
                "status": row.status,
                "result": dict(row.result_json or {}),
            }
        finally:
            db.close()
        out.put(
            {
                "slot": slot,
                "pid": os.getpid(),
                "marks": marks,
                "skipped": outcome.get("skipped"),
                "job": summary,
            }
        )
    except BaseException as exc:  # noqa: BLE001 - reported, never swallowed
        out.put(
            {
                "slot": slot,
                "pid": os.getpid(),
                "marks": marks,
                "error": _diagnostic(exc, stage="worker"),
            }
        )
        raise


@pytest.mark.skipif(
    TEST_ENGINE.dialect.name == "sqlite",
    reason=(
        "the seat being raced is pg_advisory_xact_lock, which SQLite has no way "
        "to take; the deterministic two-transaction interleaving is "
        "tests/test_daily_model_estimate.py::"
        "test_an_item_another_worker_bills_after_the_snapshot_is_not_billed_twice"
    ),
)
def test_two_processes_on_one_window_bill_each_business_key_once(client, personal_env):
    """Two workers, one owner, one window: each servable item is billed once.

    Selected by the formal runner as its own file, not by a keyword::

        scripts/run_tests.sh pg tests/test_daily_estimate_two_workers.py

    A refusal here is a PostgreSQL finding, not a fixture finding: the identity
    gate runs inside each child before either of them touches a row.

    Two claims are made, in order, and neither one stands in for the other.
    :func:`assess_race_start` reads the arrival records and decides only that two
    workers walked up to one seat before either began pricing -- the start of a
    contest, which is all a time ordering can ever decide. :func:`assess_race`
    reads the observer's ``pg_locks`` frames and the durable ledger and decides
    what the server did while they stood there. Wherever the run stops, the
    ``finally`` writes the capture before it purges anything, so a failure leaves
    behind the worker signals, exit codes, ledger rows and reservation states a
    pass does -- with the criteria it never reached named as never reached rather
    than counted as satisfied.

    The ceiling is deliberately generous. Five units for two declared
    instruments means a *failed* dedup would still be allowed to spend, so the
    one-bill-per-key result below cannot have been produced by quota refusing
    the second charge -- which is the only way this node could look green while
    the seat did nothing. The refusal reasons are asserted separately, and a
    run whose second worker was stopped by ``quota_exceeded`` is refused.
    """
    env = _open_env(client, personal_env)
    owner_key = env["user"].id
    owner_id = str(owner_key)
    skill = str(env["portfolio"].id)

    db = TestSession()
    try:
        seed_daily_bars(db, instrument_id=SECOND_INSTRUMENT)
        db.commit()
    finally:
        db.close()
    # Two declared instruments, so the race is over more than one business key and
    # a worker that stopped after its first item could not pass by doing nothing.
    _declare(env, instruments=[TEST_INSTRUMENT, SECOND_INSTRUMENT])

    _raise_ai_ceiling(owner_key, _AI_CEILING)
    quota_before = _ai_usage_used(owner_key)

    payload = {
        "backend_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "owner_id": owner_id,
        "skill_unit_id": skill,
        "window": FRESH,
        "scope": "daily",
        "hold_s": _HOLD_S,
    }
    ctx = mp.get_context("spawn")
    ready = ctx.Barrier(2)
    arrived = (ctx.Event(), ctx.Event())
    out = ctx.Queue()
    procs = [
        ctx.Process(
            target=_worker,
            args=(payload, slot, ready, arrived, out),
            name=f"estimate-worker-{slot}",
        )
        for slot in (0, 1)
    ]
    results: list = []
    frames: list = []
    # Everything the capture needs is held out here, so the ``finally`` can write
    # up a run that died at its first assertion with the same fields a passing one
    # has -- each one filled by whichever stage was reached, and no further.
    observation: dict = {"frames": [], "claims": [], "starts": [], "estimates": []}
    stage = "spawn"
    start = None
    verdict = None
    failure = None
    signals: list = []
    try:
        for proc in procs:
            proc.start()
        # The observer samples *during* the race, from the test process and its
        # own connection, and it needs the frames while the sessions behind them
        # still exist: a lock read taken after the join names a backend the
        # server has already forgotten, and pg_locks has nothing left to say
        # about it. The pid in that column is the *backend* serving a connection,
        # not the worker's own OS pid -- the two are different numbers on one
        # machine, so no amount of sampling lets ``proc.pid`` be matched against
        # it. Each child therefore stamps the backend of the cursor that runs its
        # advisory lock, and the judgement joins the frames on that.
        stage = "observe"
        frames = _observe_frames(
            TEST_ENGINE,
            owner_key,
            FRESH,
            [TEST_INSTRUMENT, SECOND_INSTRUMENT],
            lambda: any(p.is_alive() for p in procs),
            deadline_s=4 * _TIMEOUT_S,
        )
        observation["frames"] = frames
        stage = "join"
        for proc in procs:
            proc.join(timeout=4 * _TIMEOUT_S)
        for _ in procs:
            try:
                results.append(out.get(timeout=10))
            except queue.Empty:  # pragma: no cover - PG only
                break
        # Read before the assertions below, not after them: these are the worker
        # signals the capture has to hold even when one of those assertions is the
        # thing that ends the run.
        stage = "worker_signals"
        signals = _worker_signals(procs, results)
        observation["worker_signals"] = signals
        observation["starts"] = _starts(results)
        observation["estimates"] = _estimates(results)
        observation["claims"] = _claims(results)
        assert not any(p.is_alive() for p in procs), [
            p.name for p in procs if p.is_alive()
        ]
        assert [p.exitcode for p in procs] == [0, 0], [
            (p.name, p.exitcode) for p in procs
        ]
        assert len(results) == 2, results
        for row in results:
            assert "error" not in row, row

        pids = {row["pid"] for row in results}
        assert len(pids) == 2 and os.getpid() not in pids, pids

        # The proposition being corrected. What used to sit here compared *every*
        # ``claim_enter`` in the run against the first ``estimate_enter``: the
        # product prices inside the loop it claims in (see
        # ``app/services/daily_estimates.py:1062-1085``), so each worker's second
        # arrival necessarily follows somebody's first price and the comparison
        # was false by construction, in three runs out of three. The claim this
        # node can actually make is about each worker's *first* claim, and
        # :func:`assess_race_start` judges it from the arrival records themselves:
        # two OS workers, one business key, every arrival written before the
        # product call it belongs to, both ahead of the first price. That is the
        # whole of what it says. None of it is evidence that PostgreSQL made
        # either worker wait -- the frames below are the only route to that, and a
        # run that clears this gate and fails those still fails.
        stage = "start_gate"
        start = assess_race_start(observation)
        assert start["proven"], (
            "the run never showed two workers meeting at one seat first: "
            + json.dumps(start, indent=2, sort_keys=True, default=str)
        )
        item = start["item"]
        assert item in (TEST_INSTRUMENT, SECOND_INSTRUMENT), (item, start["notes"])
        outcomes = sorted(
            (
                m["already_billed"],
                m["t"],
            )
            for row in results
            for m in row["marks"]
            if m["event"] == "claim_exit" and m["instrument"] == item
        )
        assert [o[0] for o in outcomes] == [False, True], outcomes
        winner_priced = [
            m["t"]
            for row in results
            for m in row["marks"]
            if m["event"] == "estimate_enter" and m["instrument"] == item
        ]
        assert len(winner_priced) == 1, winner_priced
        assert outcomes[1][1] > winner_priced[0], (outcomes, winner_priced)

        # The durable ledger, grouped by the business key. One bill per item —
        # not one per process, and not one per summary.
        stage = "ledger"
        bills = [
            row
            for row in _served(owner_key)
            if row["metadata"].get("as_of_date") == FRESH
        ]
        by_instrument: dict = {}
        for row in bills:
            by_instrument.setdefault(row["metadata"]["instrument_id"], []).append(
                str(row["id"])
            )
        assert sorted(by_instrument) == sorted([TEST_INSTRUMENT, SECOND_INSTRUMENT]), (
            by_instrument
        )
        assert all(len(ids) == 1 for ids in by_instrument.values()), by_instrument

        # One settlement per bill, and nothing left in a non-terminal state.
        statuses = _reservations(owner_key)
        assert statuses.count("settled") == len(bills), statuses
        assert statuses.count("reserved") == 0, statuses
        assert len(statuses) == len(bills), statuses

        jobs = _jobs(owner_key)
        assert len(jobs) == 2, [job["id"] for job in jobs]
        served_by_run = {
            job["id"]: [entry["instrument_id"] for entry in job["result"]["results"]]
            for job in jobs
        }
        refused_by_run = {
            job["id"]: [
                entry["instrument_id"]
                for entry in job["result"]["refused"]
                if entry.get("reason") == "already_estimated_this_window"
            ]
            for job in jobs
        }
        for instrument_id in (TEST_INSTRUMENT, SECOND_INSTRUMENT):
            won = [
                run for run, items in served_by_run.items() if instrument_id in items
            ]
            lost = [
                run for run, items in refused_by_run.items() if instrument_id in items
            ]
            assert len(won) == 1, (instrument_id, served_by_run)
            assert len(lost) == 1, (instrument_id, refused_by_run)
            assert won[0] != lost[0], (instrument_id, served_by_run, refused_by_run)
        # Between them the two summaries account for every item exactly once.
        assert sum(len(items) for items in served_by_run.values()) == 2, served_by_run

        # ``billed_in_window`` is one ledger read at two different instants; the
        # two figures describe the same rows and must never be added. What has to
        # hold is that neither run over-counted its own window.
        #
        # ``billed_before_this_run`` is *not* one of those figures to interrogate.
        # It is the tick-open snapshot (``app/services/daily_estimates.py:963``,
        # reported at :1167), and this node releases both workers from the same
        # barrier before either has priced anything, so both snapshots are taken
        # ahead of every bill the race writes: zero is what the barrier promises,
        # and a gate that wanted a nonzero here wanted the past to have been the
        # future. Read it as a premise check instead -- a nonzero here means the
        # window was already billed before this node started, i.e. the pair was a
        # retry and not a race, and nothing below it can be trusted.
        for job in jobs:
            assert 1 <= job["result"]["billed_in_window"] <= len(bills), job["result"]
            assert job["result"]["billed_before_this_run"] == 0, job["result"]
        # "Nobody was charged twice" is a statement about the books, so it is
        # read off the books: the two runs together priced exactly the rows that
        # exist. One key priced by both would overshoot; a price that never
        # reached a bill would undershoot.
        assert sum(job["result"]["estimated"] for job in jobs) == len(bills), [
            (job["id"], job["result"]["estimated"]) for job in jobs
        ]
        # And the run that lost each key lost it to the billing ledger -- the one
        # reason that means the owner's quota did not go out the door a second
        # time -- for a key the *other* run is holding the bill for.
        for job in jobs:
            lost = [
                entry
                for entry in job["result"]["refused"]
                if entry.get("instrument_id")
            ]
            assert len(lost) == 1, (job["id"], job["result"]["refused"])
            assert lost[0]["reason"] == LEDGER_REASON, lost[0]
            assert lost[0].get("decided_by") == "billing_ledger", lost[0]
            peer = [other for other in jobs if other["id"] != job["id"]][0]
            assert lost[0]["instrument_id"] in served_by_run[peer["id"]], (
                job["id"],
                served_by_run,
            )
        assert not (set(_refusal_reasons(jobs)) & set(QUOTA_REASONS)), (
            _refusal_reasons(jobs)
        )

        # The verdict, on everything above and on the frames taken while both
        # workers were inside their claims: a real advisory-lock wait, named
        # blocker, an empty ledger during the wait and a durable bill after the
        # release, one ledger row per business key, matching settlements, and a
        # second worker that the *ledger* refused. The assertions before this one
        # already hold the same facts about the rows; what they cannot say is
        # what happened inside the server, and that is what is decided here.
        stage = "race_verdict"
        observation.update(
            {
                "bills": [
                    {
                        "instrument": row["metadata"]["instrument_id"],
                        "bill_id": str(row["id"]),
                        "inference_id": row["metadata"].get("inference_id"),
                    }
                    for row in bills
                ],
                "reservation_statuses": statuses,
                "quota_consumed": _ai_usage_used(owner_key) - quota_before,
                "refusal_reasons": _refusal_reasons(jobs),
            }
        )
        verdict = assess_race(observation)
        if not verdict["proven"]:
            pytest.fail(
                "the race was captured, and the capture does not prove the seat: "
                + json.dumps(verdict, indent=2, sort_keys=True, default=str)
            )

        # A third worker, arriving after both of them, still buys nothing.
        stage = "third_worker"
        assert _tick(window=FRESH, user_id=owner_key)["skipped"] is None
        assert len(_served(owner_key)) == len(bills)
        stage = "completed"
    except BaseException as exc:  # noqa: BLE001 - named, then raised on unchanged
        # The class and the stage are the two facts a reader needs to tell a
        # product finding from a fixture finding, and both are lost the moment
        # the traceback scrolls past. Recording costs nothing: the exception is
        # re-raised as it stands, so the report the runner writes is the original
        # -- and the original's *text* is what never enters this capture, because
        # a driver or URL parser may have quoted the connection string inside it.
        failure = _diagnostic(exc, stage=stage)
        raise
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=10)
        if failure is not None:
            # The books this node was about to read are still the books that
            # answer the question, so read them -- without judging them. Filling
            # ``bills`` here cannot make ``proven`` true, because ``verdict`` is
            # still None and its twelve criteria stay not_evaluated.
            try:
                _harvest_ledger(owner_key, observation, quota_before)
            except BaseException as harvest_exc:  # noqa: BLE001 - reported, never raised
                observation["ledger_harvest_error"] = _diagnostic(
                    harvest_exc, stage="ledger_harvest"
                )
        # Written while the rows still exist: the purge below is what makes the
        # frames unrecoverable, so the capture cannot wait for a clean exit.
        capture = {"attempted": False, "written": False, "error": "not attempted"}
        try:
            capture = _write_capture(
                owner_id, observation, start, verdict, stage, failure, signals
            )
        except BaseException as write_exc:  # noqa: BLE001 - never over the run's own failure
            capture = {
                "attempted": True,
                "written": False,
                "error": _diagnostic(write_exc, stage="capture_write"),
            }
        residual: dict = {}
        cleanup_error = None
        try:
            residual = _purge_owner(owner_id)
        except BaseException as purge_exc:  # noqa: BLE001 - reported on its own terms
            cleanup_error = _diagnostic(purge_exc, stage="purge")
        cleanup = {"residual": residual, "error": cleanup_error, "capture": capture}
        try:
            _write_capture(
                owner_id, observation, start, verdict, stage, failure, signals, cleanup
            )
        except BaseException as write_exc:  # noqa: BLE001 - the first copy is already on disk
            cleanup["capture_update_error"] = _diagnostic(
                write_exc, stage="capture_update"
            )
        trouble = _report_capture(capture, cleanup)
        # Only a run that reached its own end is still standing to be told about
        # the plumbing: raising here would replace the finding this node exists to
        # report with a complaint about the file that recorded it.
        if trouble and sys.exc_info()[0] is None:
            pytest.fail(
                "the race node reached its own end, and its evidence trail did not: "
                + "; ".join(trouble)
            )


# ---------------------------------------------------------------------------
# the judgement, put to captures this file writes itself
# ---------------------------------------------------------------------------

#: The synthetic races below are shaped exactly like a real capture: two pids in
#: each of the two namespaces a live run has to keep apart -- the OS process and
#: the PostgreSQL backend serving its connection -- one advisory object, two
#: declared keys, and arrival records carrying the whole business key. The
#: numbers themselves are arbitrary -- ``assess_race`` matches the lock object
#: *between* the rows it is comparing and never recomputes it from
#: ``hashtextextended``, so nothing here depends on how PostgreSQL packs a
#: 64-bit advisory key into ``classid``/``objid``/``objsubid``, and nothing here
#: depends on a UUID either. The two namespaces are deliberately far apart: a
#: capture that keyed its lock rows on the OS pid has to be refused, and it can
#: only be refused if the numbers it would have to match are not the ones the
#: claims bind.
_WINNER_PID = 1001
_LOSER_PID = 1002
_WINNER_BACKEND = 9101
_LOSER_BACKEND = 9102
_ADVISORY_OBJECT = (1234, 5678, 0)
_RACED_KEY = "AAA"
_SECOND_KEY = "BBB"
_OWNER = "u-1"
_SKILL = "s-1"
_WINDOW = "2026-11-24"


def _lock_row(pid, *, granted, blockers=(), wait_event_type="Client"):
    classid, objid, objsubid = _ADVISORY_OBJECT
    waiting = wait_event_type == "Lock"
    return {
        "pid": pid,
        "locktype": "advisory",
        "classid": classid,
        "objid": objid,
        "objsubid": objsubid,
        "granted": granted,
        "blockers": list(blockers),
        "wait_event_type": wait_event_type,
        "wait_event": "transactionadvisorylock" if waiting else "ClientRead",
    }


def _frame(t, *, rows, raced_count, second_count):
    return {
        "t": t,
        "rows": list(rows),
        "ledger": [
            {"instrument": _RACED_KEY, "count": raced_count},
            {"instrument": _SECOND_KEY, "count": second_count},
        ],
    }


def _start(slot, pid, seq, instrument, t, call_start_t, **over) -> dict:
    """One arrival at the claim door, shaped exactly like ``_starts`` writes it."""
    row = {
        "slot": slot,
        "pid": pid,
        "seq": seq,
        "owner_id": _OWNER,
        "skill_unit_id": _SKILL,
        "window": _WINDOW,
        "instrument": instrument,
        "t": t,
        "call_start_t": call_start_t,
    }
    row.update(over)
    return row


def _good_capture() -> dict:
    """One race in which the seat held, in exactly the shape the observer writes.

    Backend 9101 -- the connection process 1001 was using -- takes the advisory
    lock on ``AAA`` and holds it through the billing commit; backend 9102, the
    one process 1002 claims the seat with, is standing in front of that same
    object, ungranted and blocked by 9101, in the two frames taken before the
    release. Those two frames find nothing in the ledger; the frames after the
    release find the row. Then 1002 serves ``BBB`` and 1001 is refused it, which
    is why the judgement reads the *first* claim of each worker rather than any
    key that ended up both billed and refused.

    The arrival times are the same race seen from the door, and they are the
    reason the two judgements disagree on purpose: both workers were at ``AAA``
    (99.9 and 99.95) before anybody priced (100.05), while the *last* arrival in
    the run -- 1001 reaching ``BBB`` at 103.08 -- sits long after it. A gate that
    compares every arrival against the first price refuses this capture, which is
    a capture where the seat demonstrably held.
    """
    holder = _lock_row(_WINNER_BACKEND, granted=True)
    waiter = _lock_row(
        _LOSER_BACKEND,
        granted=False,
        blockers=(_WINNER_BACKEND,),
        wait_event_type="Lock",
    )
    waiting_rows = [holder, waiter]
    return {
        "frames": [
            _frame(101.0, rows=waiting_rows, raced_count=0, second_count=0),
            _frame(102.0, rows=waiting_rows, raced_count=0, second_count=0),
            _frame(103.2, rows=[holder], raced_count=1, second_count=0),
            _frame(104.0, rows=[], raced_count=1, second_count=1),
        ],
        # Grouped by worker rather than by time, because that is the order an
        # observer writes when it walks one child's marks and then the next. The
        # two readings describe the same race, and both judgements sort by time
        # before they compare, so the grouping is only about looking like a
        # capture some run actually produced.
        "starts": [
            _start(0, _WINNER_PID, 0, _RACED_KEY, 99.9, 99.99),
            _start(0, _WINNER_PID, 1, _SECOND_KEY, 103.08, 103.1),
            _start(1, _LOSER_PID, 0, _RACED_KEY, 99.95, 100.0),
            _start(1, _LOSER_PID, 1, _SECOND_KEY, 103.02, 103.03),
        ],
        # ``seq`` is a worker's own count of its own steps, so both of these are
        # the first estimate of the process that made them.
        "estimates": [
            {"slot": 0, "pid": _WINNER_PID, "seq": 0, "instrument": _RACED_KEY, "t": 100.05},
            {"slot": 1, "pid": _LOSER_PID, "seq": 0, "instrument": _SECOND_KEY, "t": 103.15},
        ],
        "worker_signals": [
            {"slot": 0, "name": "estimate-worker-0", "pid": _WINNER_PID,
             "exitcode": 0, "alive": False, "error": None},
            {"slot": 1, "name": "estimate-worker-1", "pid": _LOSER_PID,
             "exitcode": 0, "alive": False, "error": None},
        ],
        # ``backend_pid`` is what the lock rows are joined on; ``pid`` only says
        # which OS process reported the claim. The two keys a worker claims are
        # separate transactions, so they get separate numbers here even though no
        # frame holds a row for the second one: the gate is meant to read the
        # backend bound to *the claim under judgement*, and a capture in which
        # every claim of a worker shares one backend cannot show that.
        "claims": [
            {"slot": 0, "pid": _WINNER_PID, "backend_pid": _WINNER_BACKEND,
             "instrument": _RACED_KEY,
             "already_billed": False, "t": 100.0, "blocked_s": 0.01},
            {"slot": 0, "pid": _WINNER_PID, "backend_pid": 9103,
             "instrument": _SECOND_KEY,
             "already_billed": True, "t": 106.0, "blocked_s": 2.9},
            {"slot": 1, "pid": _LOSER_PID, "backend_pid": _LOSER_BACKEND,
             "instrument": _RACED_KEY,
             "already_billed": True, "t": 103.0, "blocked_s": 3.0},
            {"slot": 1, "pid": _LOSER_PID, "backend_pid": 9104,
             "instrument": _SECOND_KEY,
             "already_billed": False, "t": 103.1, "blocked_s": 0.01},
        ],
        "bills": [
            {"instrument": _RACED_KEY, "bill_id": "b1", "inference_id": "i1"},
            {"instrument": _SECOND_KEY, "bill_id": "b2", "inference_id": "i2"},
        ],
        "reservation_statuses": ["settled", "settled"],
        "quota_consumed": 2,
        "refusal_reasons": [LEDGER_REASON, LEDGER_REASON],
    }


def _waiting_rows(capture):
    """The loser's own ungranted advisory requests, frame by frame.

    Matched on the backend pid, because that is the namespace ``pg_locks.pid``
    reports and the namespace :func:`tests.daily_estimate_race.assess_race` joins
    on. A mutation that reaches only the OS-pid rows is no mutation at all.
    """
    for frame in capture["frames"]:
        for row in frame["rows"]:
            if int(row["pid"]) == _LOSER_BACKEND and row["granted"] is False:
                yield row


def _no_frames_at_all(capture):
    capture["frames"] = []


def _lock_rows_blank_everything_else_intact(capture):
    for frame in capture["frames"]:
        frame["rows"] = []


def _blocker_not_named(capture):
    for row in _waiting_rows(capture):
        row["blockers"] = []


def _waiting_on_an_object_the_holder_does_not_hold(capture):
    for row in _waiting_rows(capture):
        row["objid"] += 1


def _session_reported_a_wait_of_another_kind(capture):
    for row in _waiting_rows(capture):
        row["wait_event_type"] = "IO"
        row["wait_event"] = "DataFileExtend"


def _ledger_already_named_the_key(capture):
    capture["frames"][0]["ledger"][0]["count"] = 1


def _nothing_found_after_the_release(capture):
    for frame in capture["frames"][2:]:
        frame["ledger"][0]["count"] = 0


def _nobody_took_the_seat(capture):
    for claim in capture["claims"]:
        if claim["instrument"] == _RACED_KEY:
            claim["already_billed"] = True


def _both_workers_were_served(capture):
    """The dedup failing outright: two bills, and nobody ever refused."""
    for claim in capture["claims"]:
        if claim["instrument"] == _RACED_KEY and int(claim["pid"]) == _LOSER_PID:
            claim["already_billed"] = False
    _second_bill_under_a_generous_ceiling(capture)


def _second_bill_under_a_generous_ceiling(capture):
    """A second bill, with the quota and the settlements kept in step.

    Everything except the ledger's own one-row-per-key count is consistent, so
    this is the shape a run takes when the ceiling was never the thing that
    stopped the second worker and the seat let two through anyway.
    """
    capture["bills"].append(
        {"instrument": _RACED_KEY, "bill_id": "b3", "inference_id": "i3"}
    )
    capture["reservation_statuses"].append("settled")
    capture["quota_consumed"] = 3


def _one_reservation_left_open(capture):
    capture["reservation_statuses"][1] = "reserved"


def _quota_ahead_of_the_ledger(capture):
    capture["quota_consumed"] = 3


def _the_ceiling_refused_the_second_worker(capture):
    """One bill, one unit spent, and the reason the second one says ``quota``.

    Nothing else is wrong with this run: the ledger reads clean, so only the
    refusal reasons can tell the owner that the seat was never tested.
    """
    capture["bills"] = capture["bills"][:1]
    capture["reservation_statuses"] = ["settled"]
    capture["quota_consumed"] = 1
    capture["refusal_reasons"] = ["quota_exceeded", LEDGER_REASON]


def _they_met_on_two_different_keys(capture):
    for claim in capture["claims"]:
        if claim["instrument"] == _SECOND_KEY and int(claim["pid"]) == _LOSER_PID:
            claim["t"] = 100.5


def _the_lock_rows_name_the_os_pids(capture):
    """The join this harness used to make: ``pg_locks.pid`` against a Python pid.

    Nothing is invented and nothing is removed -- the frames still hold one
    granted holder and one ungranted waiter blocked by it, and the wait happened.
    Only the namespace is wrong, which is how a live run carrying 111 frames of a
    genuine 3 s ``Lock/transactionadvisorylock`` wait came to be refused for want
    of a wait.

    The rename is idempotent because the capture aliases one row dict across the
    frames in which it is still held: an unconditional ``swap[...]`` would be
    asked to translate a pid it had already translated and raise for the fixture
    rather than for the harness.
    """
    swap = {_WINNER_BACKEND: _WINNER_PID, _LOSER_BACKEND: _LOSER_PID}
    for frame in capture["frames"]:
        for row in frame["rows"]:
            row["pid"] = swap.get(int(row["pid"]), int(row["pid"]))
            row["blockers"] = [swap.get(int(pid), int(pid)) for pid in row["blockers"]]


def _the_capture_binds_no_backend_pid(capture):
    """A capture that never learned which backend ran each claim.

    Falling back to the OS pid is not available here: it has no row in
    ``pg_locks``, so the fallback would either pass the criterion on a number
    that names nothing in the server or leave it red for a reason no reader can
    tell apart from a race that never happened. The absence is named instead.
    """
    for claim in capture["claims"]:
        claim.pop("backend_pid", None)


_REFUSAL_CASES = (
    ("no_frames_at_all", _no_frames_at_all, {"observer_visibility", "wait_observed"}),
    (
        "lock_rows_blank_but_claims_and_bills_intact",
        _lock_rows_blank_everything_else_intact,
        {"observer_visibility", "wait_observed"},
    ),
    ("blocker_not_named", _blocker_not_named, {"blocker_is_holder"}),
    (
        "waiting_on_an_object_the_holder_does_not_hold",
        _waiting_on_an_object_the_holder_does_not_hold,
        {"lock_object_matches"},
    ),
    (
        "session_reported_a_wait_of_another_kind",
        _session_reported_a_wait_of_another_kind,
        {"wait_observed"},
    ),
    (
        "ledger_already_named_the_key_during_the_wait",
        _ledger_already_named_the_key,
        {"key_unbilled_while_blocked"},
    ),
    (
        "nothing_found_after_the_release",
        _nothing_found_after_the_release,
        {"bill_durable_after_release"},
    ),
    (
        "nobody_took_the_seat",
        _nobody_took_the_seat,
        {"holder_claim_was_free", "wait_observed"},
    ),
    (
        "both_workers_were_served",
        _both_workers_were_served,
        {"loser_reread_refused", "one_bill_per_business_key", "wait_observed"},
    ),
    (
        "second_bill_under_a_generous_ceiling",
        _second_bill_under_a_generous_ceiling,
        {"one_bill_per_business_key"},
    ),
    (
        "one_reservation_left_open",
        _one_reservation_left_open,
        {"settlement_matches_bills"},
    ),
    (
        "quota_ahead_of_the_ledger",
        _quota_ahead_of_the_ledger,
        {"quota_never_stands_in_place_of_ledger"},
    ),
    (
        "the_ceiling_refused_the_second_worker",
        _the_ceiling_refused_the_second_worker,
        {"refusal_reason_is_the_ledger"},
    ),
    (
        "they_met_on_two_different_keys",
        _they_met_on_two_different_keys,
        {"wait_observed"},
    ),
    (
        "lock_rows_keyed_on_the_os_pids",
        _the_lock_rows_name_the_os_pids,
        {"wait_observed"},
    ),
    (
        "claims_bind_no_backend_pid",
        _the_capture_binds_no_backend_pid,
        {"wait_observed"},
    ),
)


def test_the_race_verdict_accepts_a_capture_in_which_the_seat_held():
    """The judgement's positive control, on this lane, with no server at all.

    Without this node the sixteen refusals below prove nothing: a function that
    rejects every input would satisfy all of them.
    """
    verdict = assess_race(_good_capture())
    assert verdict["proven"], verdict["refused"]
    assert verdict["item"] == _RACED_KEY, verdict
    assert verdict["refused"] == [], verdict
    notes = verdict["notes"]
    assert notes["raced"] == [_RACED_KEY], notes
    # The wait is attributed to the backends, which are not the pids the processes
    # report: a capture that joined on the OS numbers is refused by the case below.
    assert notes["winner_backend_pid"] == _WINNER_BACKEND, notes
    assert notes["loser_backend_pid"] == _LOSER_BACKEND, notes
    assert notes["winner_pid"] == _WINNER_PID, notes
    assert notes["loser_pid"] == _LOSER_PID, notes
    # ``BBB`` also ended up both billed and refused, and the judgement still
    # measures the wait against ``AAA``: the key they met on, not the key whose
    # outcome happens to match a description.
    assert notes["contested"] == [_RACED_KEY, _SECOND_KEY], notes
    assert notes["blocked_frames"] == 2, notes
    assert notes["waited_s"] == 1.0, notes
    assert notes["bills_per_instrument"] == {_RACED_KEY: 1, _SECOND_KEY: 1}, notes


@pytest.mark.parametrize(
    "mutate,expected",
    [pytest.param(m, e, id=name) for name, m, e in _REFUSAL_CASES],
)
def test_the_race_verdict_refuses_a_capture_that_looks_right(mutate, expected):
    """Each way a green run can be worth nothing, and the criterion that says so.

    The control runs inside every parameterisation, so a case cannot pass by
    being a mutation that refuses an already-refused capture, and cannot pass by
    mutating nothing: the edited capture is compared against a fresh good one.
    A refusal is only credited when it names the criterion that was supposed to
    bite, and every refusal has to carry both its plain-language ``says`` and a
    ``detail`` a reader can check -- a bare criterion id is not evidence.
    """
    control = assess_race(_good_capture())
    assert control["proven"], control["refused"]

    capture = _good_capture()
    mutate(capture)
    assert capture != _good_capture(), "this case mutated nothing and proved nothing"

    verdict = assess_race(capture)
    assert not verdict["proven"], verdict
    refused = {entry["id"]: entry for entry in verdict["refused"]}
    assert expected <= set(refused), {"expected": expected, "refused": sorted(refused)}
    for criterion_id, entry in refused.items():
        assert entry["says"] == dict(CRITERIA)[criterion_id], entry
        assert entry["detail"], entry


def test_every_race_criterion_has_been_seen_to_refuse_something():
    """No criterion in ``CRITERIA`` is allowed to be decoration.

    The union of what the cases above expect to refuse has to cover the whole
    list. A criterion that no synthetic capture can trip is a criterion the
    PostgreSQL lane would never be refused by either, and the owner is right to
    treat it as unproven -- so this node fails before that can be claimed.
    """
    covered = {
        criterion_id
        for _name, _mutate, expected in _REFUSAL_CASES
        for criterion_id in expected
    }
    declared = {criterion_id for criterion_id, _says in CRITERIA}
    assert declared <= covered, {
        "never_refused": sorted(declared - covered),
        "covered": sorted(covered),
    }


# ---------------------------------------------------------------------------
# the start gate, put to the same kind of test
# ---------------------------------------------------------------------------


def _whole_set_gate(capture) -> bool:
    """The criterion this node used to assert, recomputed on the same records.

    Not dead prose: the discrimination the correction claims -- the new gate
    passes a capture the old one refused -- has to be *measured* on one piece of
    data, and a gate nobody can run cannot be shown to have been wrong. It reads
    every arrival in the run, which is exactly the mistake: the product claims
    and prices inside one loop, so a second instrument always lands after the
    first price.
    """
    enters = [row.get("t") for row in capture.get("starts") or []]
    priced = [row.get("t") for row in capture.get("estimates") or []]
    if not enters or not priced or any(row is None for row in enters + priced):
        return False
    return max(enters) < min(priced)


def _drop_slot(capture, slot) -> None:
    for key in ("starts", "estimates", "claims", "worker_signals"):
        capture[key] = [row for row in capture[key] if row.get("slot") != slot]


def _only_one_worker(capture):
    _drop_slot(capture, 1)


def _worker_never_announced_arrival(capture):
    """The process is known -- it reported an exit status and a claim -- but never
    stood at the door in the record. Its half of the ordering has no time."""
    capture["starts"] = [row for row in capture["starts"] if row["slot"] != 1]


def _they_entered_on_different_keys(capture):
    for row in capture["starts"]:
        if row["slot"] == 1 and row["seq"] == 0:
            row["instrument"] = "CCC"


def _they_entered_for_different_skills(capture):
    for row in capture["starts"]:
        if row["slot"] == 1 and row["seq"] == 0:
            row["skill_unit_id"] = "s-2"


def _arrival_after_the_first_price(capture):
    for row in capture["starts"]:
        if row["slot"] == 1 and row["seq"] == 0:
            row["t"] = 100.2
            row["call_start_t"] = 100.21


def _arrival_written_after_the_call(capture):
    """The mark exists, and it was written from inside the product call.

    A timestamp taken after ``claim_estimate`` began says where a process had got
    to, not that it was waiting to get in, which is the reading the old harness
    made and the reason this criterion exists separately from the ordering one.
    """
    for row in capture["starts"]:
        if row["slot"] == 1 and row["seq"] == 0:
            row["t"] = 100.03
            row["call_start_t"] = 100.0


def _arrival_stamp_missing(capture):
    for row in capture["starts"]:
        if row["slot"] == 1 and row["seq"] == 0:
            row.pop("call_start_t")


def _one_process_in_two_slots(capture):
    """Two slot numbers wearing one pid: a fixture's shape, not a race's."""
    for key in ("starts", "claims", "worker_signals"):
        for row in capture[key]:
            if row.get("slot") == 1:
                row["pid"] = _WINNER_PID


def _nobody_recorded_pricing(capture):
    capture["estimates"] = []


def _worker_missing_from_the_process_table(capture):
    """Both workers left arrival marks, and only one of them was a process.

    The other half of ``two_workers_reported``: a slot the run never read an exit
    status for has no OS worker behind it, however tidy its timestamps look. The
    remaining criteria still get evaluated here -- two slots are still two slots --
    so this refuses alone rather than as a cascade.
    """
    capture["worker_signals"] = [
        row for row in capture["worker_signals"] if row["slot"] != 1
    ]


_START_REFUSAL_CASES = (
    ("only_one_worker", _only_one_worker, {"two_workers_reported"}, 4),
    ("one_process_in_two_slots", _one_process_in_two_slots, {"two_workers_reported"}, 0),
    ("no_exit_status_for_the_second_worker", _worker_missing_from_the_process_table, {"two_workers_reported"}, 0),
    ("worker_never_announced_arrival", _worker_never_announced_arrival, {"each_worker_has_a_first_claim"}, 3),
    ("they_entered_on_different_keys", _they_entered_on_different_keys, {"first_claims_share_one_business_key"}, 0),
    ("they_entered_for_different_skills", _they_entered_for_different_skills, {"first_claims_share_one_business_key"}, 0),
    ("arrival_after_the_first_price", _arrival_after_the_first_price, {"both_arrived_before_the_first_estimate_began"}, 0),
    ("arrival_written_after_the_call", _arrival_written_after_the_call, {"first_claim_recorded_before_entering_the_product_claim"}, 0),
    ("arrival_stamp_missing", _arrival_stamp_missing, {"first_claim_recorded_before_entering_the_product_claim", "both_arrived_before_the_first_estimate_began"}, 0),
    ("nobody_recorded_pricing", _nobody_recorded_pricing, {"both_arrived_before_the_first_estimate_began"}, 0),
)


@pytest.mark.parametrize(
    "mutate,expected,unreached",
    [pytest.param(m, e, u, id=name) for name, m, e, u in _START_REFUSAL_CASES],
)
def test_the_start_gate_refuses_an_ordering_that_was_never_competed_for(mutate, expected, unreached):
    """Each way the two workers can have failed to meet at one seat first.

    Exact sets, not a subset: a case that refuses for the wrong reason -- or that
    buys its refusal from some other criterion's complaint about the same data --
    would tell the owner the gate can see the thing it cannot see. The fourth
    figure is how many of the five criteria were never reached at all, and a
    cascade that leaves a criterion un-evaluated is recorded as one rather than
    quietly passed by absence.
    """
    control = assess_race_start(_good_capture())
    assert control["proven"], control["refused"]

    capture = _good_capture()
    mutate(capture)
    assert capture != _good_capture(), "this case mutated nothing and proved nothing"

    start = assess_race_start(capture)
    assert not start["proven"], start
    refused = {entry["id"] for entry in start["refused"]}
    assert refused == expected, {"expected": expected, "refused": sorted(refused)}
    assert len(start["notes"]["not_evaluated"]) == unreached, start["notes"]
    for entry in start["refused"]:
        assert entry["says"] == dict(START_CRITERIA)[entry["id"]], entry
        assert entry["detail"], entry


def test_the_start_gate_accepts_the_order_a_real_race_produces():
    """The positive control, and the discrimination the correction was asked for.

    Both workers are at ``AAA`` before either prices, and the run then goes on to
    a second instrument -- the ordinary shape of the product's own loop. The new
    gate passes it. The one it replaces refused it, on this same capture, which is
    why the node was red in three runs out of three on a race that had actually
    happened.
    """
    capture = _good_capture()
    start = assess_race_start(capture)
    assert start["proven"], start["refused"]
    assert start["item"] == _RACED_KEY, start
    assert start["notes"]["not_evaluated"] == [], start["notes"]
    assert start["notes"]["margin_s"] == 0.1, start["notes"]
    assert _whole_set_gate(capture) is False, "the two gates now agree, so nothing was corrected"


def test_the_start_gate_never_claims_a_lock_wait():
    """An ordering verdict that names a lock is the old mistake in new clothing.

    The five criteria are read off timestamps the workers wrote about
    themselves. What the server did with those two sessions is in the frames, and
    only ``assess_race`` is allowed to speak of it -- so the whole of this
    judgement's own vocabulary is checked to contain no such word.
    """
    for capture in (_good_capture(),) + tuple(
        _mutated(name) for name, _m, _e, _u in _START_REFUSAL_CASES
    ):
        verdict = json.dumps(assess_race_start(capture), sort_keys=True, default=str).lower()
        for word in ("advisory", "lock", "block", "wait", "grant"):
            assert word not in verdict, (word, verdict[:400])


def _mutated(name):
    capture = _good_capture()
    by_name = {case[0]: case[1] for case in _START_REFUSAL_CASES}
    by_name[name](capture)
    return capture


def test_every_start_criterion_has_been_seen_to_refuse_something():
    """Same rule for the five as for the twelve: no criterion is decoration."""
    covered = {
        criterion_id
        for _name, _mutate, expected, _unreached in _START_REFUSAL_CASES
        for criterion_id in expected
    }
    declared = {criterion_id for criterion_id, _says in START_CRITERIA}
    assert declared <= covered, {
        "never_refused": sorted(declared - covered),
        "covered": sorted(covered),
    }


def test_the_capture_holds_the_criteria_a_failed_run_never_reached():
    """A capture written before the judgements ran says so, and says ``not proven``.

    This is the whole of requirement 3 read as data: the run that died at its
    start gate still leaves worker signals, ledger rows and reservation states
    behind, every one of the seventeen criteria is named as passed, failed or
    never reached, and ``proven`` stays false however complete the rows look. The
    sixteen variants above decide whether a criterion can refuse; they are never
    what makes a live run's capture say true.
    """
    capture = _good_capture()
    unrun = _capture_document(
        _OWNER, capture, None, None, "start_gate", None, capture["worker_signals"]
    )
    assert unrun["proven"] is False, unrun
    assert unrun["stage"] == "start_gate"
    assert unrun["criteria_tally"] == {
        "passed": 0,
        "failed": 0,
        "not_evaluated": len(START_CRITERIA) + len(CRITERIA),
    }, unrun["criteria_tally"]
    assert unrun["worker_signals"] == capture["worker_signals"]
    assert unrun["observation"]["bills"] == capture["bills"]
    assert unrun["observation"]["reservation_statuses"] == capture["reservation_statuses"]

    judged = _capture_document(
        _OWNER,
        capture,
        assess_race_start(capture),
        assess_race(capture),
        "completed",
        None,
        capture["worker_signals"],
        {"residual": {}, "error": None},
    )
    assert judged["proven"] is True, judged["start"]["refused"] + judged["verdict"]["refused"]
    assert judged["criteria_tally"] == {
        "passed": len(START_CRITERIA) + len(CRITERIA),
        "failed": 0,
        "not_evaluated": 0,
    }, judged["criteria_tally"]

    # The one figure a reader is most likely to take on trust, checked through a
    # file instead of through the object that produced it.
    assert _whole_set_gate(_good_capture()) is False
    half = _capture_document(
        _OWNER,
        capture,
        assess_race_start(capture),
        None,
        "race_verdict",
        {"class": "AssertionError", "stage": "race_verdict", "message": "boom"},
        capture["worker_signals"],
    )
    assert half["proven"] is False, half
    assert half["criteria_tally"]["not_evaluated"] == len(CRITERIA), half["criteria_tally"]
    assert half["failure"]["class"] == "AssertionError"
    assert sum(half["criteria_tally"].values()) == len(START_CRITERIA) + len(CRITERIA)


def test_the_ledger_verdict_is_indifferent_to_the_arrival_records():
    """Adding the start schema did not move what the twelve criteria decide.

    Each of the sixteen refusals is judged twice: once on the capture as the
    fixture writes it now, once with the arrival keys taken back out. The same
    criteria have to refuse in both, and the passing capture has to pass in both,
    or every PostgreSQL verdict recorded before this edit stops being comparable
    with the ones recorded after it.
    """
    start_only = ("starts", "estimates", "worker_signals")

    def refused_ids(capture):
        return {entry["id"] for entry in assess_race(capture)["refused"]}

    assert assess_race({k: v for k, v in _good_capture().items() if k not in start_only})["proven"]

    for name, mutate, _expected, _unreached in _START_REFUSAL_CASES:
        left = _good_capture()
        mutate(left)
        right = {k: v for k, v in left.items() if k not in start_only}
        assert refused_ids(left) == refused_ids(right), name

    for name, mutate, _expected in _REFUSAL_CASES:
        left = _good_capture()
        mutate(left)
        right = {k: v for k, v in left.items() if k not in start_only}
        assert refused_ids(left) == refused_ids(right), name


# ---------------------------------------------------------------------------
# the report: a criterion has to have been read before it can be credited
# ---------------------------------------------------------------------------


def _legacy_ledger_states(verdict) -> dict:
    """The rule this file applied to the twelve before the correction.

    ``refused`` was the whole test: a criterion nobody named was a criterion held.
    Kept here as code rather than as prose so the four cases below can *measure*
    how many cells the correction moved instead of asserting that it moved some.
    """
    return {
        criterion_id: (
            "failed"
            if any(entry["id"] == criterion_id for entry in verdict["refused"])
            else "passed"
        )
        for criterion_id, _says in CRITERIA
    }


_UNSTARTED = object()


def _reported(capture, verdict, start=_UNSTARTED) -> dict:
    """One capture as the live node would write it, judgement included.

    ``start=None`` is a different case from leaving it out: it is the shape of a
    run that died before the start gate judged anything, and it has to survive the
    helper rather than be quietly recomputed inside it.
    """
    return _capture_document(
        _OWNER,
        capture,
        assess_race_start(capture) if start is _UNSTARTED else start,
        verdict,
        "completed",
        None,
        capture.get("worker_signals") or [],
    )


def test_a_pass_is_only_ever_a_criterion_the_judgement_read():
    """The positive control of the report: read all twelve, credit all twelve.

    Without this cell the three controls below are unfalsifiable -- a
    ``_live_criteria`` that returned ``not_evaluated`` for everything would refuse
    each of them too, and the lane would have traded a green that over-claimed for
    one that could never say anything.
    """
    capture = _good_capture()
    verdict = assess_race(capture)
    assert verdict["proven"] is True, verdict["refused"]
    assert verdict["notes"]["evaluated"] == [cid for cid, _says in CRITERIA]
    assert verdict["notes"]["not_evaluated"] == []
    assert verdict["notes"]["not_evaluated_because"] == {}

    document = _reported(capture, verdict)
    assert document["proven"] is True, document
    assert document["criteria_tally"] == {
        "passed": len(START_CRITERIA) + len(CRITERIA),
        "failed": 0,
        "not_evaluated": 0,
    }, document["criteria_tally"]
    assert _legacy_ledger_states(verdict) == {
        cid: document["live_criteria"][cid] for cid, _says in CRITERIA
    }, "the correction must not move a cell that was read"


def test_a_refusal_still_names_the_criterion_that_reached_it():
    """A failed criterion is read, so it is reported as failed and not as missing."""
    capture = _good_capture()
    _blocker_not_named(capture)
    verdict = assess_race(capture)
    assert verdict["proven"] is False, verdict
    assert {entry["id"] for entry in verdict["refused"]} == {"blocker_is_holder"}
    assert "blocker_is_holder" in verdict["notes"]["evaluated"]
    assert verdict["notes"]["not_evaluated"] == [], verdict["notes"]

    document = _reported(capture, verdict)
    assert document["live_criteria"]["blocker_is_holder"] == "failed"
    assert document["criteria_tally"] == {
        "passed": len(START_CRITERIA) + len(CRITERIA) - 1,
        "failed": 1,
        "not_evaluated": 0,
    }, document["criteria_tally"]
    assert document["proven"] is False


#: The seven the judgement never reaches once there is no single key to judge, in
#: the order :data:`CRITERIA` lists them, plus the reason that closes the twelfth.
_SILENCED_WITHOUT_A_KEY = (
    "observer_visibility",
    "blocker_is_holder",
    "lock_object_matches",
    "key_unbilled_while_blocked",
    "bill_durable_after_release",
    "holder_claim_was_free",
    "loser_reread_refused",
    "refusal_reason_is_the_ledger",
)


def test_a_judgement_that_stopped_early_reports_the_questions_it_never_asked():
    """Two keys met, no single race: one refusal and eight criteria left unread.

    This is the cell the correction exists for. Nothing in the capture was found
    wrong about the seven wait criteria -- the judgement simply never looked at
    them once it had no key to look them up for -- and the old report turned that
    silence into eight holds.
    """
    capture = _good_capture()
    _they_met_on_two_different_keys(capture)
    verdict = assess_race(capture)
    assert verdict["proven"] is False, verdict
    assert {entry["id"] for entry in verdict["refused"]} == {"wait_observed"}
    assert set(verdict["notes"]["not_evaluated"]) == set(_SILENCED_WITHOUT_A_KEY)
    assert set(verdict["notes"]["not_evaluated_because"]) == set(_SILENCED_WITHOUT_A_KEY)
    # A refusal is the opposite of a silence: it can only be written by a criterion
    # whose evidence was opened. So the one criterion this run was found wrong about
    # stands in ``evaluated`` and not among the eight it never reached.
    assert verdict["notes"]["evaluated"] == [
        "wait_observed",
        "one_bill_per_business_key",
        "settlement_matches_bills",
        "quota_never_stands_in_place_of_ledger",
    ], verdict["notes"]["evaluated"]
    assert not set(verdict["notes"]["evaluated"]) & set(verdict["notes"]["not_evaluated"])

    start = assess_race_start(capture)
    assert start["proven"] is True, start["refused"]
    states = _live_criteria(start, verdict)
    assert all(states[cid] == "not_evaluated" for cid in _SILENCED_WITHOUT_A_KEY), states
    legacy = _legacy_ledger_states(verdict)
    moved = {cid for cid, _says in CRITERIA if legacy[cid] != states[cid]}
    assert moved == set(_SILENCED_WITHOUT_A_KEY), {
        "moved": sorted(moved),
        "expected": sorted(_SILENCED_WITHOUT_A_KEY),
    }

    document = _reported(capture, verdict, start)
    assert document["proven"] is False, document
    assert document["criteria_tally"] == {
        "passed": len(START_CRITERIA) + 3,
        "failed": 1,
        "not_evaluated": len(_SILENCED_WITHOUT_A_KEY),
    }, document["criteria_tally"]


def test_a_verdict_that_names_nothing_it_read_is_read_as_having_judged_nothing():
    """``refused == []`` buys no credit on its own: the key is the whole warrant.

    An older capture -- schema ``/2`` -- carries a verdict with no ``evaluated``
    list, so the report cannot tell a criterion that held from one that was never
    opened. Treating that as a pass would put the missing key in the lane's favour,
    which is the same mistake the derived ``passed`` was, one level up. The verdict
    here is a real one that *did* read all twelve; only its accounting is taken
    back out, because that is the shape the report has to survive.
    """
    capture = _good_capture()
    verdict = assess_race(capture)
    assert verdict["proven"] is True and verdict["refused"] == []
    stripped = json.loads(json.dumps(verdict, default=str))
    for key in ("evaluated", "not_evaluated", "not_evaluated_because"):
        stripped["notes"].pop(key, None)
    assert stripped["refused"] == [] and stripped["proven"] is True

    start = assess_race_start(capture)
    states = _live_criteria(start, stripped)
    assert [states[cid] for cid, _says in CRITERIA] == ["not_evaluated"] * len(CRITERIA)
    assert all(states[cid] == "passed" for cid, _says in START_CRITERIA), states

    document = _reported(capture, stripped, start)
    assert document["proven"] is False, document
    assert document["criteria_tally"] == {
        "passed": len(START_CRITERIA),
        "failed": 0,
        "not_evaluated": len(CRITERIA),
    }, document["criteria_tally"]


def test_an_empty_capture_is_credited_with_nothing_at_all():
    """No rows, no claims, no books: three refusals and fourteen criteria unread.

    The extreme of the same rule, and the one with the widest gap between the two
    reports: this capture used to read as nine criteria held out of twelve. The
    refusals it does carry are all a real finding -- nobody met on a key, the
    ledger holds no bill, no quota figure was taken -- and none of them is credit.
    """
    verdict = assess_race({})
    assert verdict["proven"] is False, verdict
    assert verdict["notes"]["evaluated"] == [
        "wait_observed",
        "one_bill_per_business_key",
        "quota_never_stands_in_place_of_ledger",
    ], verdict["notes"]["evaluated"]
    assert len(verdict["notes"]["not_evaluated"]) == len(CRITERIA) - 3
    assert verdict["notes"]["not_evaluated_because"], verdict["notes"]

    document = _reported({}, verdict, start=None)
    assert document["proven"] is False, document
    assert document["criteria_tally"] == {
        "passed": 0,
        "failed": 3,
        "not_evaluated": len(START_CRITERIA) + len(CRITERIA) - 3,
    }, document["criteria_tally"]
    legacy_passes = [
        cid for cid, state in _legacy_ledger_states(verdict).items() if state == "passed"
    ]
    assert len(legacy_passes) == len(CRITERIA) - 3, legacy_passes


# ---------------------------------------------------------------------------
# the capture itself, put to a child's own bytes
# ---------------------------------------------------------------------------


class _FakeProc:
    """The three things :func:`_worker_signals` reads off a real child."""

    def __init__(self, slot, pid, exitcode=0, alive=False):
        self.name = f"estimate-worker-{slot}"
        self.pid = pid
        self.exitcode = exitcode
        self._alive = alive

    def is_alive(self):
        return self._alive


def _child_shaped_results() -> list:
    """Two ``out.put(...)`` payloads, in the shape :func:`_worker` actually sends.

    Not a summary of it: the marks are built the way the wrapper builds them --
    the arrival dict appended first, then its own pre-call stamp written into the
    same object before the call -- and the kinds of mark the readers ignore
    (``hold_slept``, ``estimate_exit``) are present so this also shows they are
    ignored. The numbers are the same race ``_good_capture`` describes, because
    the claim this supports is that a real child's bytes reach the judgement with
    every field it asks for still there.
    """
    #: slot, pid, then one tuple per claim: instrument, arrival, the stamp taken
    #: on the line above the call, the answer's time, whether the seat was taken,
    #: how long the call held, when this worker began pricing that item, and the
    #: backend that ran this claim's advisory lock -- the numbers
    #: :func:`tests.daily_estimate_race.assess_race` joins the frames on.
    rounds = {
        0: [
            (_RACED_KEY, 99.9, 99.99, 100.0, False, 0.01, 100.05, _WINNER_BACKEND),
            (_SECOND_KEY, 103.08, 103.1, 106.0, True, 2.9, None, 9103),
        ],
        1: [
            (_RACED_KEY, 99.95, 100.0, 103.0, True, 3.0, None, _LOSER_BACKEND),
            (_SECOND_KEY, 103.02, 103.03, 103.1, False, 0.01, 103.15, 9104),
        ],
    }
    pids = {0: _WINNER_PID, 1: _LOSER_PID}
    rows = []
    for slot in (0, 1):
        marks: list = []
        entered = 0
        for (
            instrument,
            at,
            call_at,
            exit_at,
            billed,
            blocked_s,
            price_at,
            backend,
        ) in rounds[slot]:
            enter = {
                "event": "claim_enter",
                "owner_id": _OWNER,
                "skill_unit_id": _SKILL,
                "window": _WINDOW,
                "instrument": instrument,
                "seq": entered,
                "t": at,
            }
            entered += 1
            marks.append(enter)
            enter["call_start_t"] = call_at
            marks.append(
                {
                    "event": "claim_exit",
                    "instrument": instrument,
                    "t": exit_at,
                    "already_billed": billed,
                    "blocked_s": blocked_s,
                    "backend_pid": backend,
                }
            )
            if price_at is None:
                continue
            marks.append({"event": "estimate_enter", "instrument": instrument, "t": price_at})
            hold_end = price_at + _HOLD_S
            marks.append({"event": "hold_slept", "instrument": instrument, "t": hold_end})
            marks.append(
                {"event": "estimate_exit", "instrument": instrument, "t": hold_end + 0.01}
            )
        rows.append(
            {
                "slot": slot,
                "pid": pids[slot],
                "marks": marks,
                "skipped": [],
                "job": {"job_id": f"job-{slot}", "status": "completed", "result": {}},
            }
        )
    return rows


def test_the_capture_reads_a_real_childs_marks_into_start_records():
    """The bridge between the harness and the gate, with no server involved.

    :func:`_starts` is what the node's ``finally`` calls, and it has to turn what
    a spawn child sends into exactly the records the start gate was tested
    against. Equality is the whole claim: same keys, same values, nothing
    defaulted on the way, so a refusal by :func:`assess_race_start` on a live run
    cannot be the reader having lost a field.
    """
    results = _child_shaped_results()
    procs = [_FakeProc(0, _WINNER_PID), _FakeProc(1, _LOSER_PID)]
    good = _good_capture()
    read = {
        "starts": _starts(results),
        "estimates": _estimates(results),
        "claims": _claims(results),
        "worker_signals": _worker_signals(procs, results),
    }
    for key, rows in read.items():
        assert rows, key
    for row in read["starts"]:
        assert set(row) == {"slot", "pid", *_START_FIELDS}, sorted(row)
        assert row["owner_id"] == _OWNER and row["skill_unit_id"] == _SKILL
        assert row["window"] == _WINDOW
        assert isinstance(row["t"], float) and isinstance(row["call_start_t"], float)
    kinds = {mark["event"] for row in results for mark in row["marks"]}
    assert kinds == {
        "claim_enter",
        "claim_exit",
        "estimate_enter",
        "hold_slept",
        "estimate_exit",
    }, sorted(kinds)
    assert read["starts"] == good["starts"], read["starts"]
    assert read["estimates"] == good["estimates"], read["estimates"]
    assert read["claims"] == good["claims"], read["claims"]
    # Not a field the reader could default: ``assess_race`` refuses a capture
    # whose claim names no backend, so the marks a child sends have to carry one.
    assert all(row["backend_pid"] is not None for row in read["claims"]), read["claims"]
    assert read["worker_signals"] == good["worker_signals"], read["worker_signals"]

    observation = dict(good)
    observation.update(read)
    start = assess_race_start(observation)
    assert start["proven"] is True, start["refused"]
    assert start["item"] == _RACED_KEY, start
    assert assess_race(observation)["proven"] is True, assess_race(observation)["refused"]


def test_a_run_that_died_at_the_start_gate_still_leaves_a_readable_capture(
    tmp_path, monkeypatch
):
    """Requirement 3 as data: the failure writes the file, and the file admits it.

    A capture is worth nothing if it only exists for runs that passed, so this
    writes one the way the ``finally`` does -- verdict absent, rows still in hand
    -- and reads it back off the disk rather than trusting the object that made it.
    """
    path = tmp_path / "race-capture.json"
    monkeypatch.setenv("ALETHRA_RACE_CAPTURE_JSON", str(path))
    capture = _good_capture()
    _only_one_worker(capture)
    start = assess_race_start(capture)
    assert not start["proven"], start
    failure = {
        "class": "AssertionError",
        "stage": "start_gate",
        "line": 1,
        "message": "the run never showed two workers meeting at one seat first",
    }

    meta = _write_capture(
        _OWNER, capture, start, None, "start_gate", failure, capture["worker_signals"]
    )
    assert meta["attempted"] and meta["written"], meta
    again = json.loads(path.read_text())
    assert again["schema"] == CAPTURE_SCHEMA, again["schema"]
    assert again["proven"] is False, again["proven"]
    assert again["stage"] == "start_gate"
    assert again["failure"] == failure
    assert again["verdict"] is None
    assert again["worker_signals"] == capture["worker_signals"]
    assert [row["pid"] for row in again["worker_signals"]] == [_WINNER_PID]
    assert again["observation"]["bills"] == capture["bills"]
    assert again["observation"]["reservation_statuses"] == capture["reservation_statuses"]
    assert again["live_criteria"]["two_workers_reported"] == "failed"
    assert again["live_criteria"]["each_worker_has_a_first_claim"] == "not_evaluated"
    assert all(
        again["live_criteria"][criterion_id] == "not_evaluated"
        for criterion_id, _says in CRITERIA
    )
    assert again["criteria_tally"] == {
        "passed": 0,
        "failed": 1,
        "not_evaluated": len(START_CRITERIA) + len(CRITERIA) - 1,
    }, again["criteria_tally"]

    # The second write is the one that knows what the purge left. It cannot turn
    # the run around: the rows arriving with it are harvest, not judgement.
    cleanup = {"residual": {}, "error": None, "capture": meta}
    _write_capture(
        _OWNER,
        capture,
        start,
        None,
        "start_gate",
        failure,
        capture["worker_signals"],
        cleanup,
    )
    again = json.loads(path.read_text())
    assert again["proven"] is False
    assert again["cleanup"] == cleanup, again["cleanup"]


def test_a_capture_that_cannot_be_written_is_reported_not_raised(tmp_path, monkeypatch):
    """The evidence trail failing is a finding of its own, and never a replacement.

    ``_write_capture`` is allowed to raise -- the node catches it -- because the
    alternative is a run that silently produced no evidence and reported none of
    the consequences. What is not allowed is that report becoming the test's
    failure while a real product finding is still propagating, which is why the
    node hands it to :func:`_report_capture` and warns.
    """
    nowhere = tmp_path / "no-such-directory" / "race-capture.json"
    monkeypatch.setenv("ALETHRA_RACE_CAPTURE_JSON", str(nowhere))
    capture = _good_capture()
    with pytest.raises(FileNotFoundError):
        _write_capture(
            _OWNER,
            capture,
            assess_race_start(capture),
            None,
            "start_gate",
            None,
            capture["worker_signals"],
        )
    assert not nowhere.exists()

    meta = {"attempted": True, "written": False, "error": "FileNotFoundError()"}
    with pytest.warns(UserWarning, match="capture not written"):
        trouble = _report_capture(meta, {"residual": {}, "error": None, "capture": meta})
    assert len(trouble) == 1, trouble

    # Residue the same shape reports on its own, and a lane that was never given a
    # path reports nothing at all: silence is only correct when nothing was asked.
    with pytest.warns(UserWarning, match="cleanup left"):
        trouble = _report_capture(
            {"attempted": True, "written": True}, {"residual": {"users": 1}, "error": None}
        )
    assert len(trouble) == 1, trouble
    monkeypatch.delenv("ALETHRA_RACE_CAPTURE_JSON")
    assert _report_capture({"attempted": False, "written": False}, None) == []
    skipped = _write_capture(_OWNER, capture, None, None, "spawn", None, [])
    assert skipped == {"attempted": False, "written": False, "reason": "no capture path in this lane"}


# ---------------------------------------------------------------------------
# the diagnostic, put to a target it is not allowed to name
# ---------------------------------------------------------------------------

#: Three unreachable spellings of one target, each carrying a username, a password
#: and a query parameter no diagnostic may repeat. ``postgres`` and ``postgresql``
#: are both here on purpose: a previous cycle masked only the first scheme and so
#: printed the second in full, which is the whole reason a scheme-blind allow-list
#: has to be tested per spelling rather than once. ``.invalid`` is reserved by
#: RFC 2606 and never resolves, so nothing in this section can reach a server even
#: if a parser were changed to try.
_SENTINEL_USER = "nobody_at_all"
_SENTINEL_PASSWORD = "never-a-real-secret"
_SENTINEL_TABLE = (
    ("postgres", None, 59998, "?sslmode=require"),
    ("postgresql", None, 59999, "?connect_timeout=1"),
    ("postgresql+psycopg2", "psycopg2", 59997, "?options=-c%20x"),
)
_SENTINELS = tuple(
    f"{scheme}://{_SENTINEL_USER}:{_SENTINEL_PASSWORD}@sentinel.invalid:{port}/nope{query}"
    for scheme, _driver, port, query in _SENTINEL_TABLE
)
#: Everything a sentinel carries that is not one of the allowed facts.
_SENTINEL_SECRETS = (_SENTINEL_USER, _SENTINEL_PASSWORD, "sslmode", "connect_timeout", "options")


def _quoting_failure(sentinel: str) -> BaseException:
    """A driver-shaped exception whose own text quotes the target it could not use.

    Raised rather than constructed, so ``__traceback__`` is real and the line the
    diagnostic reports is a line this file actually contains.
    """
    try:
        raise RuntimeError(f"(psycopg.OperationalError) connection failed: {sentinel}")
    except RuntimeError as exc:
        return exc


def test_each_spelling_of_a_target_reports_only_the_allowed_fields(monkeypatch):
    """The needle is in the exception and not in what the harness reports.

    Both halves are asserted for every spelling: the first is what makes this a
    test rather than a restatement, because an exception that never carried a URL
    would prove nothing about a harness that drops the exception's text.
    """
    for (scheme, driver, port, query), sentinel in zip(_SENTINEL_TABLE, _SENTINELS):
        exc = _quoting_failure(sentinel)
        assert "://" in str(exc) and "://" in repr(exc), scheme
        assert _SENTINEL_PASSWORD in repr(exc), scheme

        facts = _connection_facts(sentinel)
        assert set(facts) == {"read"} | set(_CONNECTION_FACTS), facts
        assert facts == {
            "read": True,
            "dialect": scheme.partition("+")[0],
            "driver": driver,
            "database": "nope",
            "host": "sentinel.invalid",
            "port": port,
        }, facts

        # A run reports the target it was aimed at, which is the one in its own
        # environment -- not the one the driver happened to quote.
        monkeypatch.setenv("DATABASE_URL", sentinel)
        text = json.dumps(_diagnostic(exc, stage="spawn"), sort_keys=True)
        assert "://" not in text, scheme
        assert "sentinel.invalid" in text, text
        for secret in _SENTINEL_SECRETS + (query,):
            assert secret not in text, (scheme, secret)


def test_an_unparsable_target_is_refused_without_being_repeated(monkeypatch):
    """The error branch, which is where a parser quotes the string back at you.

    ``make_url`` answers both of these with a message containing the input, so the
    branch may name the exception and nothing else.
    """
    for bad in ("not a url at all", f"postgresql://u:{_SENTINEL_PASSWORD}@h:notaport/db"):
        facts = _connection_facts(bad)
        assert facts["read"] is False, (bad, facts)
        assert _SENTINEL_PASSWORD not in json.dumps(facts), bad

    # An ArgumentError's own text is the refusal this branch must not copy.
    assert "not a url at all" in str(ValueError("not a url at all"))
    assert ":///" not in json.dumps(_connection_facts(None)), "an absent target"
    assert _connection_facts("") == {
        "read": False,
        "reason": "no target in this environment",
    }

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert _diagnostic(_quoting_failure("x"), stage="spawn")["connection"] == {
        "read": False,
        "reason": "no target in this environment",
    }


def test_no_field_of_a_capture_written_by_a_failing_run_carries_a_url(monkeypatch):
    """Every exception-bearing slot at once, through the function that fills it.

    A live run can fail at six points, and each of them used to write ``repr(exc)``
    into the document -- the worker's own crash, the node's failure, the harvest
    that runs after it, and three complaints about the evidence trail. This puts a
    sentinel in all six and then reads the file exactly as a reader would: as the
    JSON the node writes.
    """
    sentinel = _SENTINELS[2]
    monkeypatch.setenv("DATABASE_URL", sentinel)
    capture = _good_capture()
    signals = [
        {"slot": 0, "pid": _WINNER_PID, "marks": [], "error": _diagnostic(_quoting_failure(sentinel), stage="worker")},
        {"slot": 1, "pid": _LOSER_PID, "marks": [], "error": _diagnostic(_quoting_failure(sentinel), stage="worker")},
    ]
    observation = dict(capture)
    observation["ledger_harvest_error"] = _diagnostic(_quoting_failure(sentinel), stage="ledger_harvest")
    failure = _diagnostic(_quoting_failure(sentinel), stage="barrier")
    meta = {
        "attempted": True,
        "written": False,
        "error": _diagnostic(_quoting_failure(sentinel), stage="capture_write"),
    }
    cleanup = {
        "residual": {},
        "error": _diagnostic(_quoting_failure(sentinel), stage="purge"),
        "capture": meta,
        "capture_update_error": _diagnostic(_quoting_failure(sentinel), stage="capture_update"),
    }
    document = _capture_document(
        _OWNER, observation, assess_race_start(capture), assess_race(capture), "barrier",
        failure, signals, cleanup,
    )

    # The six sites each produced a diagnostic, and every one of them is a dict.
    assert len(document["worker_signals"]) == 2
    for slot in (failure, observation["ledger_harvest_error"], cleanup["error"],
                 cleanup["capture_update_error"], meta["error"],
                 document["worker_signals"][0]["error"]):
        assert slot["class"] == "RuntimeError", slot
        assert slot["stage"] in (
            "worker", "barrier", "ledger_harvest", "capture_write", "purge", "capture_update",
        ), slot
        assert slot["line"], slot
        assert "://" not in json.dumps(slot, sort_keys=True), slot

    text = json.dumps(document, indent=2, sort_keys=True, default=str)
    assert "://" not in text
    for secret in _SENTINEL_SECRETS:
        assert secret not in text, secret
    # What survives has to be enough to act on: the stage, the class, and where it
    # was aimed -- the four fields the allow-list exists to keep.
    assert document["failure"]["connection"] == {
        "read": True, "dialect": "postgresql", "driver": "psycopg2",
        "database": "nope", "host": "sentinel.invalid", "port": 59997,
    }, document["failure"]
    assert document["failure"]["withheld"]


def test_the_console_and_the_outcome_record_of_a_failed_run_carry_no_url(tmp_path, monkeypatch):
    """The three sinks a lane writes to, from the same six diagnostics.

    ``warnings`` is what the lane's captured output and console receive, and the
    trouble list it returns is interpolated verbatim into the node's ``pytest.fail``
    -- which is the text an outcome record stores in its ``message`` attribute. So
    one list stands for both of the last two sinks, and both are read as the bytes
    they would take rather than as an object a reader would have to re-serialise.
    """
    sentinel = _SENTINELS[1]
    monkeypatch.setenv("DATABASE_URL", sentinel)
    monkeypatch.setenv("ALETHRA_RACE_CAPTURE_JSON", str(tmp_path / "race-capture.json"))
    meta = {
        "attempted": True,
        "written": False,
        "error": _diagnostic(_quoting_failure(sentinel), stage="capture_write"),
    }
    cleanup = {
        "residual": {"users": 1},
        "error": _diagnostic(_quoting_failure(sentinel), stage="purge"),
        "capture": meta,
    }
    with pytest.warns(UserWarning, match="capture not written") as records:
        trouble = _report_capture(meta, cleanup)

    assert len(trouble) == 2, trouble
    console = "\n".join(str(record.message) for record in records.list)
    outcome = "Failed: " + "; ".join(trouble)
    for sink, text in (("console", console), ("outcome record", outcome)):
        assert "://" not in text, sink
        for secret in _SENTINEL_SECRETS:
            assert secret not in text, (sink, secret)
    # The report still says what went wrong, which is the only reason to keep it.
    assert "capture not written" in console and "cleanup left" in console
    assert '"stage": "purge"' in outcome or "purge" in outcome, outcome
