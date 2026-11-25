"""Replay calculation credibility tests.

Verifies that execute_historical_replay() correctly:
1. Uses a UTC cutoff bind (not an INTERVAL string with an embedded param)
2. Filters by user_id and checks portfolio ownership before reading trades
3. Recalculates PnL from position size (not the stored trade's own net_pnl)
4. Event-driven: exits settle before entries at the same timestamp
5. Records automated decisions with actor_type='system'
6. Rejects invalid days_back before any DB statement
7. No future revenue leak: unsettled PnL is not available for new positions

These tests mock the database layer because the replay SQL uses PostgreSQL
syntax (FOR UPDATE, RETURNING) that SQLite cannot parse. The mock dispatches on
statement content -- see tests/replay_fake.py -- because these assertions read
back the statements the service issued, and an assertion indexed by call number
silently follows a query that moved.

The expected PnL figures are hand-derived from the documented cost model, not
copied from a run: 10% of equity is committed, entry costs are 0.10% commission
+ 0.05% slippage on committed notional, exit costs the same rates on exit
proceeds. For the reference leg (entry 50,000 -> exit 51,000 on 100,000 capital):

    committed  = 100,000 * 0.10           = 10,000
    quantity   = 10,000 / 50,000          = 0.2
    gross      = 0.2 * (51,000 - 50,000)  = 200.00
    entry cost = 10,000 * (0.001+0.0005)  =  15.00
    exit cost  = (0.2 * 51,000) * 0.0015  =  15.30
    net        = 200.00 - 15.00 - 15.30   = 169.70
"""

from collections import namedtuple

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from tests.replay_fake import (
    CANCEL_POLL,
    CANCEL_WRITE,
    TERMINAL_COMPLETION,
    TERMINAL_UPDATE,
    TRADES_QUERY,
    TRADE_COLUMNS,
    PollRow,
    make_replay_db,
    make_run_row,
    params_of,
    trade_row,
)

REFERENCE_NET_PNL = Decimal("169.70")

ENTRY_TIME = datetime(2026, 1, 1, 10, 0, 0)
EXIT_TIME = datetime(2026, 1, 1, 11, 0, 0)

Wiring = namedtuple("Wiring", ("engine", "db", "run", "user_id"))


def _replay(trades, *, days_back=30, position_size_pct=0.10, max_positions=3,
            commission_rate=Decimal("0.001"), slippage_rate=Decimal("0.0005"), **wiring):
    """Execute a replay over ``trades`` and hand back (result, recorded db)."""
    w = _wired(trades, **wiring)
    result = w.engine.execute_historical_replay(
        run_id=w.run.id,
        user_id=w.user_id,
        days_back=days_back,
        position_size_pct=position_size_pct,
        max_positions=max_positions,
        commission_rate=commission_rate,
        slippage_rate=slippage_rate,
    )
    return result, w.db


def _wired(trades, *, run=None, user_id=None, owner_id=None, **session):
    """Engine, recording session, run row and caller, with ownership bound.

    ``owner_id`` defaults to the caller. Every test but one needs the two to
    agree, and generating them independently leaves a test asserting on an
    authorisation refusal instead of on the behaviour its name claims.
    """
    from app.services.paper_trading import PaperTradingEngine

    run = run or make_run_row()
    user_id = user_id or str(uuid4())
    db = make_replay_db(run=run, owner_id=owner_id or user_id, trades=trades, **session)
    return Wiring(PaperTradingEngine(session_factory=lambda: db), db, run, user_id)


def _trade(entry, exit_, *, direction="long", entry_price="50000", exit_price="51000"):
    return trade_row(
        entry_time=entry,
        exit_time=exit_,
        entry_price=Decimal(entry_price),
        exit_price=Decimal(exit_price),
        net_pnl=Decimal("1000"),
        direction=direction,
        quantity=Decimal("1"),
        exit_reason="exit",
    )


def _legs(count):
    """``count`` non-overlapping round trips, two events each.

    The cancel poll fires once per 100 events, so one leg stops only after the
    final status lock while fifty legs stop inside the event loop.
    """
    return [
        _trade(
            ENTRY_TIME + timedelta(minutes=2 * i),
            ENTRY_TIME + timedelta(minutes=2 * i + 1),
        )
        for i in range(count)
    ]


class TestReplayCutoffBinding:
    """Verify days_back becomes a UTC cutoff bind, never an INTERVAL literal."""

    def test_cutoff_is_datetime_not_string(self):
        """The query must bind a timezone-aware :cutoff, not a days_back value."""
        _, db = _replay([], days_back=7)

        query_params = params_of(db, TRADES_QUERY)
        assert "cutoff" in query_params, "Must use :cutoff bind parameter"
        assert "days_back" not in query_params, "Must NOT pass days_back to SQL"
        assert isinstance(query_params["cutoff"], datetime), "cutoff must be a datetime"
        assert query_params["cutoff"].tzinfo is not None, "cutoff must be timezone-aware"

    def test_trades_sql_has_no_interval_literal(self):
        """The window must be a bound datetime, never SQL-side date arithmetic.

        The defect this replaces spelled the window as
        ``entry_time >= NOW() - INTERVAL ':days_back days'``, which PostgreSQL
        cannot bind and which silently matched no trades.
        """
        _, db = _replay([], days_back=7)

        sql = [s for s, _ in db.calls if TRADES_QUERY in s][0].lower()
        assert "t.entry_time >= :cutoff" in sql, "window must come from a bound datetime"
        assert "days_back" not in sql, "days_back must not reach the SQL text at all"
        for spelled_window in ("interval '", "make_interval", "now(", "current_date", "::"):
            assert spelled_window not in sql, (
                f"{spelled_window!r} means the replay window is computed in SQL: {sql}"
            )

    def test_cutoff_reflects_days_back(self):
        """cutoff = now() - timedelta(days=days_back)."""
        before = datetime.now(timezone.utc) - timedelta(days=30)
        _, db = _replay([], days_back=30)
        after = datetime.now(timezone.utc) - timedelta(days=30)

        cutoff = params_of(db, TRADES_QUERY)["cutoff"]
        assert before <= cutoff <= after, (
            f"cutoff {cutoff} not within expected range [{before}, {after}]"
        )

    def test_as_of_excludes_unsettled_trades(self):
        """Only trades that closed before the replay started may be replayed."""
        _, db = _replay([], days_back=30)

        assert params_of(db, TRADES_QUERY)["as_of"].tzinfo is not None
        sql = [s for s, _ in db.calls if TRADES_QUERY in s][0]
        assert "t.exit_time <= :as_of" in sql, (
            "Without the exit_time bound, unrealised trades leak into the ledger"
        )


class TestReplayOwnership:
    """Verify the caller must own the portfolio behind the run."""

    def test_user_id_bound_into_trades_query(self):
        """The caller's own id, bound through the column's UUID type.

        Text equality is the ownership claim; the typed bind is the spelling
        claim. ``app/lib/uuid_bind.py`` measures that an untyped ``str`` reaching
        a raw SQLite bind matches only the dashed rows, while every ORM write
        stores 32-char hex — so a bare string here is the invisibility bug this
        lane was written for, not an acceptable spelling of the same id.
        """
        test_user_id = str(uuid4())
        _, db = _replay([], user_id=test_user_id)

        bound = params_of(db, TRADES_QUERY)["user_id"]
        assert str(bound) == test_user_id
        assert isinstance(bound, UUID), (
            f"user_id reached the trades query as {type(bound).__name__}, not the "
            "column's own UUID type that paper_trading.py binds it with"
        )

    def test_foreign_user_rejected_before_trades_query(self):
        """Ownership is checked before any trade is read, so a cross-user replay
        cannot even learn how many trades another user has."""
        from app.services.paper_trading_exceptions import AuthorizationError

        w = _wired([], user_id=str(uuid4()), owner_id=str(uuid4()))

        with pytest.raises(AuthorizationError):
            w.engine.execute_historical_replay(run_id=w.run.id, user_id=w.user_id)

        assert not any(TRADES_QUERY in sql for sql, _ in w.db.calls), (
            "trades must not be read for a run the caller does not own"
        )


class TestReplayPnLRecalculation:
    """Verify PnL is recalculated from position size, not taken from the trade."""

    def test_pnl_recalculated_for_long(self):
        result, _ = _replay([_trade(ENTRY_TIME, EXIT_TIME)])

        assert Decimal(str(result["total_pnl"])) == REFERENCE_NET_PNL, (
            "expected 169.70 = 200.00 gross less 15.00 entry and 15.30 exit costs"
        )
        # The stored trade carries net_pnl 1,000 for a full-size position. Copying
        # it instead of rescaling is the credibility failure this test exists for.
        assert result["total_pnl"] != 1000.0

    def test_position_scales_with_capital(self):
        """Doubling capital doubles committed notional, so every figure doubles."""
        trade = _trade(ENTRY_TIME, EXIT_TIME)

        small, _ = _replay([trade], run=make_run_row(initial_capital=Decimal("100000")))
        large, _ = _replay([trade], run=make_run_row(initial_capital=Decimal("200000")))

        assert Decimal(str(large["total_pnl"])) == 2 * Decimal(str(small["total_pnl"]))
        small_exit = [e for e in small["event_log"] if e["event"] == "exit"][0]
        large_exit = [e for e in large["event_log"] if e["event"] == "exit"][0]
        assert Decimal(str(large_exit["gross_pnl"])) == 2 * Decimal(str(small_exit["gross_pnl"]))

    def test_zero_costs_reproduce_gross_arithmetic(self):
        """Switching costs off must leave exactly the hand-computed gross figure,
        which pins the 169.70 above to the cost model rather than to rounding."""
        result, _ = _replay(
            [_trade(ENTRY_TIME, EXIT_TIME)],
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
        )

        assert Decimal(str(result["total_pnl"])) == Decimal("200")

    def test_short_trade_rejected_by_long_only_contract(self):
        """Paper trading is long-only by contract (paper_trading.py:281-288).

        A previous revision of this test asserted a positive short PnL, which the
        service deliberately refuses to compute; it never ran, so the contradiction
        stayed invisible. Asserting the refusal keeps the invariant under test.
        """
        trades = [_trade(ENTRY_TIME, EXIT_TIME, direction="short", exit_price="49000")]
        w = _wired(trades)

        with pytest.raises(ValueError, match="Long-only mode"):
            w.engine.execute_historical_replay(run_id=w.run.id, user_id=w.user_id)

        assert not any("SET status = 'running'" in sql for sql, _ in w.db.calls), (
            "a rejected trade set must not acquire execution rights"
        )


class TestReplayInvalidInput:
    """Verify refusals happen before anything is written."""

    def test_invalid_days_back_rejected_before_db(self):
        """days_back <= 0 must raise before any DB statement."""
        w = _wired([])

        for bad in (0, -5, None):
            with pytest.raises(ValueError, match="days_back must be a positive integer"):
                w.engine.execute_historical_replay(
                    run_id=w.run.id, user_id=w.user_id, days_back=bad,
                )

        assert w.db.calls == [], "no statement may reach the database"

    def test_completed_run_is_not_replayed(self):
        """Idempotency: a finished run must be rejected, not replayed twice."""
        from app.services.paper_trading_exceptions import InvalidStateError

        w = _wired([], run=make_run_row(status="completed"))

        with pytest.raises(InvalidStateError):
            w.engine.execute_historical_replay(run_id=w.run.id, user_id=w.user_id)

        assert not any(TRADES_QUERY in sql for sql, _ in w.db.calls)


class TestReplayEventTiming:
    """Verify the event clock: exits release capital before later entries."""

    def test_exited_positions_removed(self):
        base = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            _trade(base, base + timedelta(hours=2)),
            _trade(base + timedelta(hours=3), base + timedelta(hours=5)),
            _trade(base + timedelta(hours=6), base + timedelta(hours=8)),
        ]

        result, _ = _replay(trades)

        assert result["total_trades"] == 3
        # Each leg compounds the previous one's realised profit, so the total must
        # clear three reference legs rather than equal them.
        assert Decimal(str(result["total_pnl"])) > 3 * REFERENCE_NET_PNL

    def test_no_future_revenue_leak(self):
        """Trade B, opening while A is still open, is blocked -- it is not sized on
        equity that would include A's unrealised gain."""
        base = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            _trade(base, base + timedelta(hours=10)),
            _trade(base + timedelta(hours=5), base + timedelta(hours=15)),
        ]

        result, _ = _replay(trades, max_positions=1)

        assert result["total_trades"] == 1, (
            f"Trade B must be blocked while A is active. Got {result['total_trades']} trades."
        )
        blocked = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked) == 1, "B must appear as entry_blocked in event_log"
        assert blocked[0]["trade_idx"] == 1
        assert blocked[0]["reason"] == "max_positions"
        # Only A's realised leg may reach the ledger.
        assert Decimal(str(result["total_pnl"])) == REFERENCE_NET_PNL

    def test_released_capital_available_after_exit(self):
        """A exits at T+5; B, entering at the same T+5, must size on equity that
        already includes A's realised 169.70, i.e. 10% of 100,169.70."""
        base = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            _trade(base, base + timedelta(hours=5)),
            _trade(base + timedelta(hours=5), base + timedelta(hours=10)),
        ]

        result, _ = _replay(trades, max_positions=1)

        assert result["total_trades"] == 2, (
            f"Both trades should execute (exit before entry at same timestamp). "
            f"Got {result['total_trades']}"
        )
        # B's committed notional: 100,169.70 * 0.10 = 10,016.97 -> quantity 0.2003394
        #   gross 200.3394, entry cost 15.025455, exit cost 15.3259641  -> 169.9879809
        expected = REFERENCE_NET_PNL + Decimal("169.9879809")
        assert Decimal(str(result["total_pnl"])) == expected
        assert result["total_pnl"] > 2 * float(REFERENCE_NET_PNL), (
            "B must be sized on the released capital, not the original 100,000"
        )


class TestReplayActorType:
    """Automated decisions must be attributed to the system, never to the human."""

    def test_decision_records_use_system_actor_type(self):
        result, db = _replay([_trade(ENTRY_TIME, EXIT_TIME)])
        assert result["total_trades"] == 1

        decisions = [sql for sql, _ in db.calls if "decision_records" in sql]
        assert len(decisions) == 2, f"one entry and one exit decision, got {len(decisions)}"
        for sql in decisions:
            assert "'system'" in sql, f"decision INSERT is not system-attributed: {sql[:120]}"
            assert "'human'" not in sql, "replay must not attribute decisions to the user"

    def test_orders_are_marked_paper_trading(self):
        _, db = _replay([_trade(ENTRY_TIME, EXIT_TIME)])

        orders = [sql for sql, _ in db.calls if "paper_orders" in sql]
        assert len(orders) == 2
        for sql in orders:
            assert "'paper_trading'" in sql
            assert "'live'" not in sql


class TestReplayTerminalOwnership:
    """The terminal writes are the worker's to make, and only those ones.

    The completion write asked only about ``cancel_requested``, which a run
    closed by another writer still answers with FALSE, so the replay could
    republish a finished or failed run as 'completed' over the state somebody
    else recorded. The two cancel writes -- the one taken at a polling
    boundary inside the event loop and the one taken after the final status
    lock -- had the same hole in the opposite direction: each answered the
    caller with ``"status": "cancelled"`` without asking whether its own UPDATE
    had matched a row, so a run that left 'running' underneath the worker was
    reported as cancelled by a write that changed nothing.

    Every run-state write on this lane is now restricted to
    ``status = 'running'`` -- execution acquisition, both cancel sites and the
    exception handler -- and each of the three that claims a terminal state
    reads rowcount or RETURNING before it returns.
    """

    def test_the_completion_write_carries_the_source_state(self):
        _, db = _replay([_trade(ENTRY_TIME, EXIT_TIME)])

        completion = [s for s, _ in db.calls if TERMINAL_COMPLETION in s]
        assert len(completion) == 1, f"one completion write per replay: {completion}"
        assert (
            "where id = :id and status = 'running' and cancel_requested = false"
            in completion[0].lower()
        ), completion[0]

    @pytest.mark.parametrize(
        "trade_count", [1, 50], ids=["post-lock-site", "in-loop-site"]
    )
    def test_each_cancel_write_carries_the_source_state(self, trade_count):
        """Neither of the two cancel sites may write on ``WHERE id = :id`` alone.

        One trade never reaches a polling boundary, so its stop is the one
        taken after the final status lock; fifty trades (two events each) cross
        the first boundary at event 100 and take the in-loop stop instead. Both
        spell the same UPDATE, and this reads it from whichever one ran.
        """
        trades = _legs(trade_count)
        result, db = _replay(trades, cancel_requested=True)
        assert result["status"] == "cancelled", result

        cancels = [s for s, _ in db.calls if CANCEL_WRITE in s]
        assert len(cancels) == 1, f"one cancel write per replay: {cancels}"
        assert "where id = :id and status = 'running'" in cancels[0].lower(), cancels[0]

    @pytest.mark.parametrize(
        "trade_count", [1, 50], ids=["post-lock-site", "in-loop-site"]
    )
    def test_a_cancel_write_that_matched_nothing_claims_no_cancellation(
        self, trade_count
    ):
        """A cancel that updated zero rows must not return ``status='cancelled'``.

        Both sites used to commit and hand back a cancelled result on the
        strength of ``check_cancel_requested`` alone. The row having left
        'running' means some other writer already decided this run's terminal
        state, so the honest answer is a refusal, and the only write allowed to
        follow it is the exception handler's own conditional one.
        """
        from app.services.paper_trading_exceptions import InvalidStateError

        w = _wired(_legs(trade_count), cancel_requested=True, cancel_rowcount=0)
        with pytest.raises(InvalidStateError):
            w.engine.execute_historical_replay(run_id=w.run.id, user_id=w.user_id)

        journal = w.db.journal
        refused = max(
            index
            for index, entry in enumerate(journal)
            if entry[0] == "stmt" and CANCEL_WRITE in entry[1]
        )
        # The refusal is a rollback, not a commit: nothing this worker wrote
        # after losing the row may reach the database.
        assert journal[refused + 1] == ("txn", "rollback"), journal[refused + 1:]
        published = [name for kind, name in journal[refused + 2:] if kind == "stmt"]
        assert all(TERMINAL_UPDATE in sql for sql in published), (
            f"after a refused cancel only the terminal handler may write: {published}"
        )

    def test_a_completion_write_that_matched_nothing_publishes_no_outcome(self):
        """Zero rows means the run is not ours to close: nothing may be stored.

        The daily metrics and the portfolio close are in the same transaction as
        the completion write, so the refusal has to roll them back too, and the
        only statement that may follow is the exception handler's honest
        terminal write.
        """
        from app.services.paper_trading_exceptions import InvalidStateError

        w = _wired([_trade(ENTRY_TIME, EXIT_TIME)], completion_rowcount=0)
        with pytest.raises(InvalidStateError):
            w.engine.execute_historical_replay(run_id=w.run.id, user_id=w.user_id)

        assert not any(
            "UPDATE paper_portfolios" in sql for sql, _ in w.db.calls
        ), "the portfolio close belongs to a completion that happened"

        journal = w.db.journal
        cut = max(
            index
            for index, entry in enumerate(journal)
            if entry == ("txn", "rollback")
        )
        published = [name for kind, name in journal[cut + 1:] if kind == "stmt"]
        assert len(published) == 1 and TERMINAL_UPDATE in published[0], (
            f"after the refusal only the terminal state may be written: {published}"
        )


class TestReplayDoubleIsFailClosed:
    """The fake has to refuse what it was never told about.

    ``replay_fake`` documents two properties that its first revision did not
    enforce: unrecognised reads raise, and its expectations are the formal
    query's. A bare ``MagicMock`` answers an invented attribute or an undeclared
    ORM read with another mock, which is how a replay test can stay green while
    testing a path the service no longer takes.
    """

    def test_the_double_refuses_an_orm_read_the_replay_never_declares(self):
        w = _wired([])

        for name, call in (
            ("query", lambda: w.db.query(object)),
            ("get", lambda: w.db.get(object, 1)),
            ("scalars", lambda: w.db.scalars("SELECT 1")),
            ("flush", lambda: w.db.flush()),
        ):
            with pytest.raises(AssertionError, match="declared by statement content"):
                call()

    def test_the_double_refuses_an_attribute_the_session_does_not_have(self):
        w = _wired([])

        with pytest.raises(AttributeError):
            w.db.not_a_session_method

    def test_the_expected_columns_are_the_ones_the_formal_query_selects(self):
        """The fake's row shapes are checked against the statements, not restated.

        ``TradeRow`` is what the service unpacks; the SELECT list is what the
        database actually returns. Comparing the two catches a column renamed or
        dropped in the query, which a copy of the same tuple in the test file
        would agree with forever. The cancel poll is checked the same way
        because its row is a different shape from the terminal-status row: two
        columns unpacked positionally, not three read as attributes.
        """
        _, db = _replay(_legs(50), cancel_requested=True)

        sql = [s for s, _ in db.calls if TRADES_QUERY in s][0]
        assert _selected_aliases(sql) == list(TRADE_COLUMNS), sql

        poll = [s for s, _ in db.calls if CANCEL_POLL in s]
        assert poll, "the replay must have polled for a cancel"
        assert _selected_aliases(poll[0]) == list(PollRow._fields), poll[0]


def _selected_aliases(select_sql):
    """The output column names of a replay SELECT, in the order it emits them."""
    head = select_sql.split(" FROM ")[0].split("SELECT", 1)[1]
    names = []
    for item in head.split(","):
        tokens = item.strip().split()
        if len(tokens) >= 3 and tokens[-2].lower() == "as":
            names.append(tokens[-1])
        else:
            names.append(tokens[0].split(".")[-1])
    return names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
