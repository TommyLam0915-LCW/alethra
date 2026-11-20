"""Replay calculation credibility tests.

Verifies that execute_historical_replay() correctly:
1. Uses a UTC cutoff bind (not an INTERVAL string with an embedded param)
2. Filters the source history by user_id (ownership isolation)
3. Recalculates PnL from position size, not the source trade's PnL
4. Event-driven: exits settle before entries at the same timestamp
5. Writes actor_type='system' (not 'human')
6. Rejects invalid days_back / cost rates before touching the database
7. Leaks no future revenue: unsettled PnL is not available to new positions
8. Fails closed on a short trade before writing any ledger row

Every node runs the real service against the database conftest binds.  The
bind-shape nodes read the emitted statements through ``capture_statements()``
(a ``before_execute`` listener), which is the only way to see a parameter that
never reached SQL; the rest assert on the replay result and on rows read back.
tests/replay_fixture.py records why the previous MagicMock scaffolding could
not verify any of this.

One node is renamed rather than kept: ``test_pnl_recalculated_for_short``
asserted a short position's profit, which the product's long-only guard now
rejects, so the direction-handling intent it stood behind is covered by
``test_short_trade_rejected_before_ledger_write`` instead.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from tests.replay_fixture import (  # noqa: F401  (fixture registered by import)
    capture_statements,
    find_call,
    replay_case,
    trades_query,
)

ZERO_COSTS = {"commission_rate": Decimal(0), "slippage_rate": Decimal(0)}

# One round trip, long from 50 000 to 51 000, sized at 10% of 100 000 capital:
# quantity = 0.10 * 100 000 / 50 000 = 0.2, PnL = 0.2 * 1 000 = 200.
# Entry/exit offsets are hours after the seeded base time (30 h in the past), so
# every exit sits behind as_of and every entry inside days_back=30.
PROFITABLE_LONG = (0, 1, Decimal("50000"), Decimal("51000"))


class TestReplayCutoffBinding:
    """Verify days_back is converted to a UTC cutoff, not embedded in INTERVAL."""

    def test_cutoff_is_datetime_not_string(self, replay_case):
        """Query must use a :cutoff bind holding a datetime."""
        case = replay_case()

        with capture_statements() as calls:
            case.replay(days_back=7, position_size_pct=0.10, max_positions=3)

        query = trades_query(calls)
        assert "cutoff" in query.parameters, "Must use :cutoff bind parameter"
        assert "days_back" not in query.parameters, "Must NOT pass days_back to SQL"
        assert isinstance(query.parameters["cutoff"], datetime), "cutoff must be a datetime"
        assert query.parameters["cutoff"].tzinfo is not None, "cutoff must be timezone-aware"
        assert not re.search(r"INTERVAL\s+['\":]", query.statement, re.IGNORECASE), (
            f"cutoff must not be reassembled into an INTERVAL expression: {query.statement}"
        )

    def test_cutoff_reflects_days_back(self, replay_case):
        """cutoff = now() - timedelta(days=days_back)."""
        case = replay_case()

        before = datetime.now(timezone.utc) - timedelta(days=30)
        with capture_statements() as calls:
            case.replay(days_back=30)
        after = datetime.now(timezone.utc) - timedelta(days=30)

        cutoff = trades_query(calls).parameters["cutoff"]
        assert before <= cutoff <= after, (
            f"cutoff {cutoff} not within expected range [{before}, {after}]"
        )

    def test_invalid_days_back_rejected_before_db(self, replay_case):
        """days_back <= 0 must raise ValueError before any statement at all."""
        case = replay_case([PROFITABLE_LONG])

        with capture_statements() as calls:
            for bad in (0, -5, None):
                with pytest.raises(ValueError, match="days_back must be a positive integer"):
                    case.replay(days_back=bad)

        assert calls == [], f"validation reached the database: {[c.statement[:60] for c in calls]}"

    def test_non_decimal_cost_rates_rejected_before_db(self, replay_case):
        """commission_rate / slippage_rate must be non-negative Decimals, not floats."""
        case = replay_case([PROFITABLE_LONG])

        with capture_statements() as calls:
            with pytest.raises(ValueError, match="commission_rate must be a non-negative Decimal"):
                case.replay(commission_rate=0.001)
            with pytest.raises(ValueError, match="slippage_rate must be a non-negative Decimal"):
                case.replay(slippage_rate=Decimal("-1"))

        assert calls == [], f"cost validation reached the database: {len(calls)} statements"


class TestReplayUserFilter:
    """Verify the user_id filter is applied to the source-history query."""

    def test_user_id_in_trades_query(self, replay_case):
        case = replay_case()

        with capture_statements() as calls:
            case.replay(days_back=30)

        params = trades_query(calls).parameters
        assert params["user_id"] == case.user_id
        assert params["instrument"] == "CRYPTO:KRAKEN:REPLAY/USD"
        assert params["rule_id"] == "replay_rule"
        assert params["interval"] == 60

    def test_other_users_history_is_not_replayed(self, replay_case):
        """The user_id predicate is load-bearing: a second owner replays zero trades.

        Both cases use the same instrument, rule and interval, so only study-run
        ownership distinguishes them.  Drop ``r.user_id = :user_id`` from the
        service and this replay reads the first user's profitable trade and
        returns 1.
        """
        seeded = replay_case([PROFITABLE_LONG])
        other = replay_case()
        assert other.user_id != seeded.user_id
        assert other.run_id != seeded.run_id

        foreign = other.replay(**ZERO_COSTS)

        assert foreign["total_trades"] == 0, (
            f"replay as user {other.user_id} read {foreign['total_trades']} trade(s) "
            f"belonging to user {seeded.user_id}"
        )

        # Positive control: the same rows must be replayable by their owner, or
        # the zero above would only prove the seed did not land.
        own = seeded.replay(**ZERO_COSTS)
        assert own["total_trades"] == 1, (
            f"seeded history is not readable by its own user, so the foreign-user "
            f"zero above is vacuous: {own['event_log']}"
        )


class TestReplayPnLRecalculation:
    """Verify PnL is recalculated from position size, not the source trade."""

    def test_pnl_recalculated_for_long(self, replay_case):
        """Long: PnL = quantity * (exit_price - entry_price), settled at exit.

        The source row carries net_pnl = 1 000 000 (1 000 units over a 1 000
        move).  The replay sizes 0.2 units, so copying the source PnL instead of
        recalculating would report 1 000 000, not 200.
        """
        case = replay_case([PROFITABLE_LONG])

        result = case.replay(position_size_pct=0.10, max_positions=3, **ZERO_COSTS)

        assert result["total_trades"] == 1
        assert Decimal(str(result["total_pnl"])) == Decimal("200"), (
            f"expected the resized 200, got {result['total_pnl']}"
        )
        trade = result["executed_trades"][0]
        assert Decimal(str(trade["gross_pnl"])) == Decimal("200")
        assert trade["entry_commission"] == 0.0 and trade["exit_slippage"] == 0.0

    def test_pnl_scales_with_position_size(self, replay_case):
        """Half the size must halve the PnL: sizing comes from equity, not storage."""
        case = replay_case([PROFITABLE_LONG])

        result = case.replay(position_size_pct=0.05, max_positions=3, **ZERO_COSTS)

        assert Decimal(str(result["total_pnl"])) == Decimal("100"), (
            f"0.05 * 100 000 / 50 000 * 1 000 = 100, got {result['total_pnl']}"
        )

    def test_short_trade_rejected_before_ledger_write(self, replay_case):
        """A short source trade fails closed, with no ledger rows and no run claim.

        The long-only guard runs after the source SELECT and before the
        execution-acquisition UPDATE, so a rejected replay must leave the run
        queued and the order/position/decision tables empty for this portfolio.
        """
        short = (0, 1, Decimal("50000"), Decimal("49000"), "short")
        case = replay_case([short])

        with pytest.raises(ValueError, match="Long-only mode: cannot replay short trade"):
            case.replay(position_size_pct=0.10, max_positions=3, **ZERO_COSTS)

        db = SessionLocal()
        try:
            for table, column in (
                ("paper_orders", "portfolio_id"),
                ("paper_positions", "portfolio_id"),
                ("decision_records", "paper_portfolio_id"),
            ):
                count = db.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {column} = :pid"),
                    {"pid": case.portfolio_id},
                ).scalar()
                assert count == 0, f"long-only rejection still wrote {count} rows to {table}"

            status = db.execute(
                text("SELECT status FROM paper_trading_runs WHERE id = :rid"),
                {"rid": case.run_id},
            ).scalar()
            assert status == "queued", (
                f"run acquired execution rights before the side guard rejected: {status}"
            )
        finally:
            db.close()


class TestReplayEventTiming:
    """Verify event-driven simulation: exits before entries at same timestamp."""

    def test_exited_positions_removed(self, replay_case):
        """Sequential non-overlapping trades all execute."""
        case = replay_case([
            (0, 2, Decimal("50000"), Decimal("51000")),
            (3, 5, Decimal("50000"), Decimal("51000")),
            (6, 8, Decimal("50000"), Decimal("51000")),
        ])

        result = case.replay(position_size_pct=0.10, max_positions=3, **ZERO_COSTS)

        assert result["total_trades"] == 3, result["event_log"]

    def test_no_future_revenue_leak(self, replay_case):
        """Unsettled position PnL must NOT be available for new position sizing.

        Trade A: enters T+0, exits T+10, PnL +200
        Trade B: enters T+5 (while A is still open), max_positions=1

        B must be blocked because A is still active, so the replay settles
        exactly A's PnL and sizes nothing off unrealised gains.
        """
        case = replay_case([
            (0, 10, Decimal("50000"), Decimal("51000")),
            (5, 15, Decimal("50000"), Decimal("51000")),
        ])

        result = case.replay(position_size_pct=0.10, max_positions=1, **ZERO_COSTS)

        assert result["total_trades"] == 1, (
            f"Trade B must be blocked while A is active. Got {result['total_trades']} trades."
        )
        blocked = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked) == 1, f"B must appear as entry_blocked exactly once: {result['event_log']}"
        assert blocked[0]["trade_idx"] == 1
        assert blocked[0]["reason"] == "max_positions"
        assert Decimal(str(result["total_pnl"])) == Decimal("200")

    def test_released_capital_available_after_exit(self, replay_case):
        """After A exits, its realized PnL is available for B's position sizing.

        Trade A: enters T+0, exits T+5, PnL = 0.2 * 1 000 = +200
        Trade B: enters T+5 (same timestamp, after the exit), max_positions=1

        B sizes off 100 000 + 200 = 100 200:
        quantity = 0.10 * 100 200 / 50 000 = 0.2004, PnL = 0.2004 * 1 000 = 200.40
        Total = 200 + 200.40 = 400.40, and B's extra 0.40 is the released capital.
        """
        case = replay_case([
            (0, 5, Decimal("50000"), Decimal("51000")),
            (5, 10, Decimal("50000"), Decimal("51000")),
        ])

        result = case.replay(position_size_pct=0.10, max_positions=1, **ZERO_COSTS)

        assert result["total_trades"] == 2, (
            f"Both trades should execute (exit before entry at same timestamp). "
            f"Got {result['total_trades']}"
        )
        assert Decimal(str(result["executed_trades"][1]["gross_pnl"])) == Decimal("200.4")
        assert Decimal(str(result["total_pnl"])) == Decimal("400.4"), (
            f"B should use released capital. Expected 400.40, got {result['total_pnl']}"
        )


class TestReplayActorType:
    """Verify actor_type is 'system' for automated paper trading decisions."""

    def test_actor_type_is_paper_trading(self, replay_case):
        case = replay_case([PROFITABLE_LONG])

        with capture_statements() as calls:
            result = case.replay(**ZERO_COSTS)

        assert result["total_trades"] == 1
        decision_write = find_call(calls, "INSERT INTO decision_records")
        assert "'system'" in decision_write.statement, (
            f"decision INSERT must carry the system actor literal: {decision_write.statement}"
        )
        assert "'human'" not in decision_write.statement

        db = SessionLocal()
        try:
            actor_types = [
                row[0]
                for row in db.execute(
                    text("SELECT actor_type FROM decision_records WHERE paper_portfolio_id = :pid"),
                    {"pid": case.portfolio_id},
                ).fetchall()
            ]
        finally:
            db.close()

        assert len(actor_types) == 2, f"entry + exit decisions expected, got {actor_types}"
        assert set(actor_types) == {"system"}, f"non-system actor written: {actor_types}"
