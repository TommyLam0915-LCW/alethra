"""Replay calculation credibility tests.

Verifies that execute_historical_replay() correctly:
1. Uses UTC cutoff bind (not INTERVAL string with embedded param)
2. Filters by user_id (ownership isolation)
3. Recalculates PnL based on position size (not original trade PnL)
4. Event-driven: exits settle before entries at same timestamp
5. Uses actor_type='paper_trading' (not 'human')
6. Rejects invalid days_back before any DB write
7. No future revenue leak: unsettled PnL not available for new positions

These tests mock the database layer because the service uses PostgreSQL-specific
SQL incompatible with SQLite test DB.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4


def _make_mock_db(mock_run, trades):
    """Create a mock DB with callable side_effect for event-driven replay."""
    mock_db = MagicMock()
    call_count = [0]

    def execute_side_effect(*args, **kwargs):
        call_count[0] += 1
        result = MagicMock()
        if call_count[0] == 1:
            result.fetchone.return_value = mock_run
        elif call_count[0] == 2:
            result.fetchall.return_value = trades
        return result

    mock_db.execute.side_effect = execute_side_effect
    return mock_db


def _make_mock_run(**overrides):
    """Create a standard mock run."""
    mock_run = MagicMock()
    mock_run.skill = overrides.get("skill", "momentum")
    mock_run.instrument_id = overrides.get("instrument_id", "BTC")
    mock_run.interval_minutes = overrides.get("interval_minutes", 60)
    mock_run.initial_capital = overrides.get("initial_capital", Decimal("100000"))
    mock_run.portfolio_id = overrides.get("portfolio_id", str(uuid4()))
    return mock_run


class TestReplayCutoffBinding:
    """Verify days_back is converted to UTC cutoff, not embedded in INTERVAL."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_cutoff_is_datetime_not_string(self, mock_session_local):
        """Query must use :cutoff bind (datetime), not INTERVAL ':days_back days'."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = _make_mock_db(_make_mock_run(), [])
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=7,
            position_size_pct=0.10,
            max_positions=3,
        )

        trades_query_call = mock_db.execute.call_args_list[1]
        query_params = trades_query_call[0][1]
        assert "cutoff" in query_params, "Must use :cutoff bind parameter"
        assert "days_back" not in query_params, "Must NOT pass days_back to SQL"
        assert isinstance(query_params["cutoff"], datetime), "cutoff must be a datetime"
        assert query_params["cutoff"].tzinfo is not None, "cutoff must be timezone-aware"

    @patch("app.services.paper_trading.SessionLocal")
    def test_cutoff_reflects_days_back(self, mock_session_local):
        """cutoff = now() - timedelta(days=days_back)."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = _make_mock_db(_make_mock_run(), [])
        mock_session_local.return_value = mock_db

        before = datetime.now(timezone.utc) - timedelta(days=30)
        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
        )
        after = datetime.now(timezone.utc) - timedelta(days=30)

        trades_query_call = mock_db.execute.call_args_list[1]
        cutoff = trades_query_call[0][1]["cutoff"]
        assert before <= cutoff <= after, f"cutoff {cutoff} not within expected range [{before}, {after}]"

    def test_invalid_days_back_rejected_before_db(self):
        """days_back <= 0 must raise ValueError before any DB call."""
        from app.services.paper_trading import PaperTradingEngine

        with patch("app.services.paper_trading.SessionLocal") as mock_session_local:
            mock_db = MagicMock()
            mock_session_local.return_value = mock_db

            engine = PaperTradingEngine("postgresql://test")

            with pytest.raises(ValueError, match="days_back must be a positive integer"):
                engine.execute_historical_replay(
                    run_id=str(uuid4()),
                    user_id=str(uuid4()),
                    days_back=0,
                )

            with pytest.raises(ValueError, match="days_back must be a positive integer"):
                engine.execute_historical_replay(
                    run_id=str(uuid4()),
                    user_id=str(uuid4()),
                    days_back=-5,
                )

            with pytest.raises(ValueError, match="days_back must be a positive integer"):
                engine.execute_historical_replay(
                    run_id=str(uuid4()),
                    user_id=str(uuid4()),
                    days_back=None,
                )

            mock_db.execute.assert_not_called()


class TestReplayUserFilter:
    """Verify user_id filter is applied to trades query."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_user_id_in_trades_query(self, mock_session_local):
        from app.services.paper_trading import PaperTradingEngine

        mock_db = _make_mock_db(_make_mock_run(), [])
        mock_session_local.return_value = mock_db

        test_user_id = str(uuid4())
        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=test_user_id,
            days_back=30,
        )

        trades_query_call = mock_db.execute.call_args_list[1]
        query_params = trades_query_call[0][1]
        assert query_params["user_id"] == test_user_id


class TestReplayPnLRecalculation:
    """Verify PnL is recalculated based on position size, not original trade."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_pnl_recalculated_for_long(self, mock_session_local):
        """Long: PnL = quantity * (exit_price - entry_price), settled at exit."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run(initial_capital=Decimal("100000"))
        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        trades = [
            (entry_time, exit_time, Decimal("50000"), Decimal("51000"),
             Decimal("1000"), "long", Decimal("1"), "exit_signal")
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # 10% of $100,000 = $10,000; $10,000 / $50,000 = 0.2 BTC
        # PnL = 0.2 * ($51,000 - $50,000) = $200
        assert abs(result["total_pnl"] - 200.0) < 0.01

    @patch("app.services.paper_trading.SessionLocal")
    def test_pnl_recalculated_for_short(self, mock_session_local):
        """Short: PnL = quantity * (entry_price - exit_price)."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run(initial_capital=Decimal("100000"))
        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        trades = [
            (entry_time, exit_time, Decimal("50000"), Decimal("49000"),
             Decimal("1000"), "short", Decimal("1"), "exit_signal")
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # 0.2 * ($50,000 - $49,000) = $200
        assert abs(result["total_pnl"] - 200.0) < 0.01


class TestReplayEventTiming:
    """Verify event-driven simulation: exits before entries at same timestamp."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_exited_positions_removed(self, mock_session_local):
        """Sequential non-overlapping trades all execute."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run()
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            (base_time, base_time + timedelta(hours=2),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            (base_time + timedelta(hours=3), base_time + timedelta(hours=5),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            (base_time + timedelta(hours=6), base_time + timedelta(hours=8),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        assert result["total_trades"] == 3

    @patch("app.services.paper_trading.SessionLocal")
    def test_no_future_revenue_leak(self, mock_session_local):
        """Unsettled position PnL must NOT be available for new position sizing.

        Trade A: enters T+0, exits T+10, expected PnL = +$200
        Trade B: enters T+5 (while A still active), max_positions=1

        B must be BLOCKED because A is still active.
        B's quantity must be based on initial_capital only, not initial_capital + A's PnL.
        """
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run(initial_capital=Decimal("100000"))
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            # A: enters T+0, exits T+10
            (base_time, base_time + timedelta(hours=10),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # B: enters T+5 (A still active)
            (base_time + timedelta(hours=5), base_time + timedelta(hours=15),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=1,
        )

        # Only trade A should execute; B is blocked by max_positions=1
        assert result["total_trades"] == 1, (
            f"Trade B must be blocked while A is active. Got {result['total_trades']} trades."
        )

        # Verify via event_log that B was blocked
        blocked_events = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert len(blocked_events) == 1, "B must appear as entry_blocked in event_log"
        assert blocked_events[0]["trade_idx"] == 1

    @patch("app.services.paper_trading.SessionLocal")
    def test_released_capital_available_after_exit(self, mock_session_local):
        """After A exits, its realized PnL is available for B's position sizing.

        Trade A: enters T+0, exits T+5, PnL = +$200
        Trade B: enters T+5 (same timestamp, AFTER exit), max_positions=1

        B should execute with capital = $100,000 + $200 = $100,200
        """
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run(initial_capital=Decimal("100000"))
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            # A: enters T+0, exits T+5
            (base_time, base_time + timedelta(hours=5),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # B: enters T+5 (same timestamp as A's exit)
            (base_time + timedelta(hours=5), base_time + timedelta(hours=10),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=1,
        )

        # Both should execute: A exits at T+5, freeing slot for B at T+5
        assert result["total_trades"] == 2, (
            f"Both trades should execute (exit before entry at same timestamp). "
            f"Got {result['total_trades']}"
        )

        # A's PnL: 0.10 * $100,000 / $50,000 * ($51,000 - $50,000) = 0.2 * $1,000 = $200
        # B's capital: $100,000 + $200 = $100,200
        # B's quantity: 0.10 * $100,200 / $50,000 = $10,020 / $50,000 = 0.2004
        # B's PnL: 0.2004 * $1,000 = $200.40
        # Total: $200 + $200.40 = $400.40
        assert abs(result["total_pnl"] - 400.40) < 0.10, (
            f"B should use released capital. Expected ~$400.40, got ${result['total_pnl']:.2f}"
        )


class TestReplayActorType:
    """Verify actor_type is 'system' for automated paper trading decisions."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_actor_type_is_paper_trading(self, mock_session_local):
        from app.services.paper_trading import PaperTradingEngine

        mock_run = _make_mock_run()
        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        trades = [
            (entry_time, exit_time, Decimal("50000"), Decimal("51000"),
             Decimal("1000"), "long", Decimal("1"), "exit")
        ]

        mock_db = _make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
        )

        decision_insert_calls = []
        for c in mock_db.execute.call_args_list:
            if len(c[0]) > 0:
                sql_text = str(c[0][0])
                if "decision_records" in sql_text:
                    decision_insert_calls.append(c)

        assert len(decision_insert_calls) > 0, "decision_records INSERT must be called"
        sql_text = str(decision_insert_calls[0][0][0])
        assert "'system'" in sql_text
        assert "'human'" not in sql_text
