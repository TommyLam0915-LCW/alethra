"""P-4: why a paper-trading run stopped is recorded, and what is not recorded.

``paper_trading_runs.failure_reason`` did not exist before revision
``f2a3b4c5d6e7``, so the writer is one branch of one statement and every other
state has to leave it empty.  These nodes run the real
``execute_historical_replay`` against the database conftest binds and read the
row back; none of them writes the column by hand except the one node that is
explicitly about a pre-migration row.

What each node proves, and what it does not:

* a crash after execution acquisition stores the exception's summary, so a
  'failed' run is reportable instead of just red;
* the summary that lands is the scrubbed one.  ``tests/test_error_response_
  safety.py`` already proves ``scrub_text`` redacts; that is not re-litigated
  here — what is new is that the value *reaching the column* went through it,
  because the writer passes ``safe_error_message(exc)`` rather than ``str(exc)``;
* a cancel that lands before the crash stores NULL, not the death text: the run
  was stopped on purpose and recording why it died would claim a failure;
* a re-run clears the previous attempt's reason in the acquisition statement, so
  the page cannot show an old crash under a run that is executing again;
* cancelling a run that had already failed clears it too, so a non-empty reason
  always belongs to a run that is *currently* 'failed';
* NULL and "" are different claims, and the reader must not collapse them;
* the reason moves inside the same atomic UPDATE the terminal state already
  uses — no SELECT-then-UPDATE was added to get it there.

The two-real-connection form of the cancel window is not covered here, and is
not covered by the SQLite lane at all (``tests/test_paper_trading_cancel_
atomic.py`` records why: the suite shares one ``StaticPool`` connection).
"""

import re
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine as app_engine
from app.lib.error_safety import scrub_text
from app.services.paper_trading import PaperTradingEngine
from tests.replay_fixture import (  # noqa: F401  (fixture registered by import)
    capture_statements,
    calls_mentioning,
    replay_case,
)

ONE_LONG_TRADE = (0, 1, Decimal("50000"), Decimal("51000"))
ZERO_COSTS = {"commission_rate": Decimal(0), "slippage_rate": Decimal(0)}

# Carries one of each secret class scrub_text() is contracted to remove, plus a
# sentence that must survive: the node below reads the row back and checks both.
RAW_DEATH = (
    "ledger write refused: postgresql+psycopg2://alethra:sup3rs3cr3t@127.0.0.1:5432"
    "/ai_investment_lab while opening /Users/tommylam0915/.secrets/prod.key with token=abc123"
)


class InjectedReasonDeath(RuntimeError):
    """The worker died after taking ownership, carrying ``RAW_DEATH``.

    A dedicated class so a node cannot pass by consuming an unrelated service
    error in place of the injection — the message is what is under test here,
    so it must come from the exception the handler actually caught.
    """


def _die_after_acquisition(message=RAW_DEATH, on_acquired=None):
    """Session factory whose worker raises ``message`` on the first ledger write.

    ``execute_historical_replay`` opens its sessions through ``session_factory``,
    the only seam that reaches the worker's own transaction; arming on the first
    ``INSERT`` after the acquisition ``UPDATE`` puts the death inside the loop.
    ``on_acquired`` runs once in the window after that UPDATE committed, which is
    where a real cancel can arrive.
    """
    state = {"acquired": False, "hook_ran": False, "crashed": False}

    def factory():
        db = SessionLocal()
        real_execute, real_commit = db.execute, db.commit

        def execute(*args, **kwargs):
            sql = " ".join(str(args[0]).split()).lower() if args else ""
            if "update paper_trading_runs" in sql and "'queued', 'pending', 'failed'" in sql:
                state["acquired"] = True
            elif state["acquired"] and not state["crashed"] and sql.startswith("insert"):
                state["crashed"] = True
                raise InjectedReasonDeath(message)
            return real_execute(*args, **kwargs)

        def commit(*args, **kwargs):
            result = real_commit(*args, **kwargs)
            if on_acquired is not None and state["acquired"] and not state["hook_ran"]:
                state["hook_ran"] = True
                on_acquired()
            return result

        db.execute, db.commit = execute, commit
        return db

    return factory, state


def _run_row(run_id):
    """(status, failure_reason) read back on a session that wrote nothing."""
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT status, failure_reason FROM paper_trading_runs WHERE id = :id"),
            {"id": run_id},
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "the replay's own row disappeared"
    return row


def _read_column():
    """(nullable, type) of the live test schema's failure_reason column."""
    columns = {
        column["name"]: column
        for column in inspect(app_engine).get_columns("paper_trading_runs")
    }
    assert "failure_reason" in columns, (
        f"the test schema has no failure_reason column; schema holds {sorted(columns)}"
    )
    return columns["failure_reason"]


class TestReasonIsRecorded:
    def test_crash_after_acquisition_stores_the_exception_summary(self, replay_case):
        """A failed run reports why, in its own row."""
        case = replay_case([ONE_LONG_TRADE])
        factory, state = _die_after_acquisition()
        engine = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            engine.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        assert state == {"acquired": True, "hook_ran": False, "crashed": True}, (
            f"the worker never passed through the window under test: {state}"
        )

        status, reason = _run_row(case.run_id)
        assert status == "failed", f"a crashed worker left the run in {status!r}"
        assert reason is not None, "the run is 'failed' but recorded no reason at all"
        # The sentence survives, which is the point of the column.
        assert "ledger write refused" in reason, reason

    def test_the_stored_reason_carries_no_credential_path_or_dsn(self, replay_case):
        """The value that reaches the column is the scrubbed one.

        The positive control is measured first: if the raw text no longer
        contains these secrets, the redaction assertions below would pass
        without the writer having done anything.
        """
        assert "sup3rs3cr3t" in RAW_DEATH and "/Users/" in RAW_DEATH, "fixture drift"

        case = replay_case([ONE_LONG_TRADE])
        factory, state = _die_after_acquisition()
        engine = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            engine.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        _, reason = _run_row(case.run_id)
        assert reason is not None
        for leak in ("sup3rs3cr3t", "abc123", "/Users/", "postgresql+psycopg2://"):
            assert leak not in reason, f"{leak} reached the column every surface reads"
        assert "<redacted-url>" in reason and "<path>" in reason, (
            f"neither placeholder is present, so the text was not scrubbed: {reason}"
        )
        assert reason == scrub_text(RAW_DEATH), (
            f"stored {reason!r} is not the scrubbed summary {scrub_text(RAW_DEATH)!r}"
        )

    def test_get_status_reports_the_recorded_reason(self, replay_case):
        """The reader the status endpoint uses returns the field, unmutated."""
        case = replay_case([ONE_LONG_TRADE])
        factory, _ = _die_after_acquisition()
        engine = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            engine.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        status = engine.get_status(case.run_id)
        _, reason = _run_row(case.run_id)
        assert "failure_reason" in status, sorted(status)
        assert status["failure_reason"] == reason, (
            f"get_status says {status['failure_reason']!r}, the row says {reason!r}"
        )

    def test_reason_moves_inside_the_existing_atomic_update(self, replay_case):
        """One statement decides status and reason together; no second write.

        Counting both is what makes this a gate rather than a grep: during a
        replay the acquisition UPDATE and the terminal UPDATE are the only two
        statements allowed to name the column, and the terminal one still
        carries the worker-ownership guard.  The third writer in the service —
        the immediate-cancel statement — is not reachable from this call and is
        pinned by the node below instead.
        """
        case = replay_case([ONE_LONG_TRADE])
        factory, _ = _die_after_acquisition()
        engine = PaperTradingEngine(session_factory=factory)

        with capture_statements() as calls:
            with pytest.raises(InjectedReasonDeath):
                engine.execute_historical_replay(
                    run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
                )

        writes = calls_mentioning(calls, "update paper_trading_runs", "failure_reason")
        assert len(writes) == 2, (
            "expected exactly the acquisition and the terminal write to name "
            f"failure_reason, got {len(writes)}: {[c.statement[:70] for c in writes]}"
        )
        terminal = [c for c in writes if "else 'failed'" in c.normalized]
        assert len(terminal) == 1, f"the terminal decision is not the single CASE write: {terminal}"
        assert "and status = 'running'" in terminal[0].normalized, (
            "the decision lost its worker-ownership guard"
        )
        assert "failure_reason = case" in terminal[0].normalized, (
            f"the reason is no longer chosen in the same statement as the state: "
            f"{terminal[0].statement}"
        )
        assert ":reason" in terminal[0].normalized
        assert str(terminal[0].parameters["id"]) == str(case.run_id)


class TestReasonStaysEmpty:
    def test_cancelled_run_records_null_not_the_death_text(self, replay_case):
        """A stop requested on purpose is not a failure, and says so."""
        case = replay_case([ONE_LONG_TRADE])
        canceller = PaperTradingEngine(session_factory=SessionLocal)
        factory, state = _die_after_acquisition(
            on_acquired=lambda: canceller.cancel_run(case.run_id, case.user_id)
        )
        engine = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            engine.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        assert state["crashed"], "the worker never died, so the cancel branch was not reached"
        status, reason = _run_row(case.run_id)
        assert status == "cancelled", f"the finalizer chose {status!r}"
        assert reason is None, (
            f"a run that was cancelled on purpose stores {reason!r}; the UI would "
            "read that as a failure the user did not have"
        )

    def test_a_fresh_run_reads_null_and_not_empty(self, replay_case):
        """NULL is the pre-failure state; "" would be a different claim."""
        case = replay_case([ONE_LONG_TRADE])
        engine = PaperTradingEngine(session_factory=SessionLocal)

        status = engine.get_status(case.run_id)
        assert status["failure_reason"] is None, (
            f"a run that never failed reports {status['failure_reason']!r}"
        )
        column = _read_column()
        assert column["nullable"] is True, (
            "NOT NULL would force every pre-migration row to invent a reason"
        )

    def test_rerunning_clears_the_previous_attempt_reason(self, replay_case):
        """A run executing again must not still report the old crash.

        The first attempt is killed after acquisition, which leaves 'failed'
        plus a reason; the second runs the real service to completion, and the
        acquisition statement is the only thing that could have emptied it.
        """
        case = replay_case([ONE_LONG_TRADE])
        factory, _ = _die_after_acquisition()
        doomed = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            doomed.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        status, reason = _run_row(case.run_id)
        assert (status, reason is not None) == ("failed", True), (
            f"the first attempt did not leave a reason to clear: {status!r}, {reason!r}"
        )

        result = case.replay(days_back=30, **ZERO_COSTS)
        assert result["status"] == "completed", result

        status, reason = _run_row(case.run_id)
        assert reason is None, (
            f"a completed run still reports why the earlier attempt died: {reason!r}"
        )

    def test_cancelling_a_failed_run_clears_the_reason_it_recorded(self, replay_case):
        """`cancelled` must not keep the text that says why it *failed*.

        `cancel_run` accepts a run already in 'failed' and moves it straight to
        'cancelled' with no worker involved (`paper_trading.py:1165-1172`), and
        the terminal handler's text is still in that row at the moment the
        status changes.  Without clearing it the history read returns
        `status='cancelled'` beside a failure sentence: no surface renders that
        pair, and any count of non-empty reasons over-counts the failures.
        """
        case = replay_case([ONE_LONG_TRADE])
        factory, _ = _die_after_acquisition()
        doomed = PaperTradingEngine(session_factory=factory)

        with pytest.raises(InjectedReasonDeath):
            doomed.execute_historical_replay(
                run_id=case.run_id, user_id=case.user_id, days_back=30, **ZERO_COSTS
            )

        status, reason = _run_row(case.run_id)
        assert (status, reason is not None) == ("failed", True), (
            f"the run did not first fail with a reason to clear: {status!r}, {reason!r}"
        )

        engine = PaperTradingEngine(session_factory=SessionLocal)
        with capture_statements() as calls:
            result = engine.cancel_run(case.run_id, case.user_id)
        assert result["status"] == "cancelled", result

        clears = calls_mentioning(calls, "update paper_trading_runs", "failure_reason")
        assert len(clears) == 1, (
            "the cancel path is allowed exactly one statement naming the column, "
            f"got {len(clears)}: {[c.statement[:70] for c in clears]}"
        )
        assert "failure_reason = null" in clears[0].normalized, clears[0].statement
        assert "set status = 'cancelled'" in clears[0].normalized, clears[0].statement

        status, reason = _run_row(case.run_id)
        assert status == "cancelled", status
        assert reason is None, (
            f"a cancelled run still carries {reason!r}; the column is read as "
            "'why this run failed', and this run was stopped on purpose"
        )

    def test_a_row_that_failed_before_the_column_shipped_reads_as_null(self, replay_case):
        """The third state the UI has to distinguish, written directly.

        History is not backfilled: every run that failed under the previous
        schema has no reason, and nothing in this unit changes that.  This node
        pins the read shape the frontend message is written against.
        """
        case = replay_case([ONE_LONG_TRADE])
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET status = 'failed', failure_reason = NULL "
                     "WHERE id = :id"),
                {"id": case.run_id},
            )
            db.commit()
        finally:
            db.close()

        status, reason = _run_row(case.run_id)
        assert status == "failed"
        assert reason is None, f"a legacy failure reads {reason!r}"
        assert reason != "", "NULL and empty string must stay distinguishable"


class TestColumnIsDeclared:
    def test_the_live_test_schema_names_the_column_text(self):
        """create_all built what the migration builds.

        ``tests/test_schema_parity.py`` compares the two sources statically;
        this reads the schema the SQLite lane actually runs against, so a model
        that declares the column but cannot create it is still caught.
        """
        column = _read_column()
        assert re.search(r"text", str(column["type"]), re.IGNORECASE), (
            f"{column['type']}: a String(64) would truncate the 200-character "
            "bound safe_error_message() writes up to"
        )
