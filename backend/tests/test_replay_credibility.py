"""Replay calculation credibility tests.

Verifies that execute_historical_replay() correctly:
1. Respects days_back parameter (not hardcoded 365)
2. Filters by user_id (ownership isolation)
3. Recalculates PnL based on position size (not original trade PnL)
4. Removes exited positions (max_positions works correctly)
5. Uses actor_type='paper_trading' (not 'human')

These tests mock the database layer because the service uses PostgreSQL-specific
SQL (NOW(), INTERVAL, CURRENT_DATE) incompatible with SQLite test DB.
"""

import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4


def _make_user_headers(client, email: str, is_admin: bool = False):
    """Create a user and return (auth_headers, user_id)."""
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User
    from app.database import get_db

    db = next(get_db())
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name=email.split("@")[0],
            is_admin=is_admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(data={"sub": str(user.id)})
        user_id = str(user.id)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}, user_id


class TestReplayDaysBackParameter:
    """Verify days_back parameter is used, not hardcoded 365."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_days_back_7_used_in_query(self, mock_session_local):
        """days_back=7 should appear in SQL query, not 365."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        # Mock run lookup
        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run

        # Mock trades query (empty result)
        mock_db.execute.return_value.fetchall.return_value = []

        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=7,
            position_size_pct=0.10,
            max_positions=3,
        )

        # Verify the trades query was called with days_back=7
        calls = mock_db.execute.call_args_list
        trades_query_call = calls[1]  # Second execute call is the trades query
        query_params = trades_query_call[0][1]
        assert query_params["days_back"] == 7, "days_back parameter must be passed to query"

    @patch("app.services.paper_trading.SessionLocal")
    def test_days_back_30_used_in_query(self, mock_session_local):
        """days_back=30 should appear in SQL query."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run
        mock_db.execute.return_value.fetchall.return_value = []

        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        calls = mock_db.execute.call_args_list
        trades_query_call = calls[1]
        query_params = trades_query_call[0][1]
        assert query_params["days_back"] == 30


class TestReplayUserFilter:
    """Verify user_id filter is applied to trades query."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_user_id_in_trades_query(self, mock_session_local):
        """Trades query must filter by user_id."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run
        mock_db.execute.return_value.fetchall.return_value = []

        test_user_id = str(uuid4())
        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=test_user_id,
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        calls = mock_db.execute.call_args_list
        trades_query_call = calls[1]
        query_params = trades_query_call[0][1]
        assert query_params["user_id"] == test_user_id, "user_id must be passed to query"


class TestReplayPnLRecalculation:
    """Verify PnL is recalculated based on position size, not original trade."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_pnl_recalculated_for_long(self, mock_session_local):
        """Long position PnL = quantity * (exit_price - entry_price)."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run

        # Mock a trade: entry=$50000, exit=$51000, original net_pnl=$1000
        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        mock_db.execute.return_value.fetchall.return_value = [
            (entry_time, exit_time, Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit_signal")
        ]

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,  # 10% of $100,000 = $10,000
            max_positions=3,
        )

        # Position size: $10,000 / $50,000 = 0.2 BTC
        # Recalculated PnL: 0.2 * ($51,000 - $50,000) = 0.2 * $1,000 = $200
        # NOT the original $1,000
        expected_quantity = 10000.0 / 50000.0  # 0.2
        expected_pnl = expected_quantity * (51000.0 - 50000.0)  # $200
        assert abs(result["total_pnl"] - expected_pnl) < 0.01, f"Expected ${expected_pnl}, got ${result['total_pnl']}"
        assert abs(result["total_pnl"] - 200.0) < 0.01, "PnL must be recalculated, not original"

    @patch("app.services.paper_trading.SessionLocal")
    def test_pnl_recalculated_for_short(self, mock_session_local):
        """Short position PnL = quantity * (entry_price - exit_price)."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run

        # Mock a short trade: entry=$50000, exit=$49000, original net_pnl=$1000
        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        mock_db.execute.return_value.fetchall.return_value = [
            (entry_time, exit_time, Decimal("50000"), Decimal("49000"), Decimal("1000"), "short", Decimal("1"), "exit_signal")
        ]

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # Position size: $10,000 / $50,000 = 0.2 BTC
        # Recalculated PnL (short): 0.2 * ($50,000 - $49,000) = 0.2 * $1,000 = $200
        expected_pnl = 0.2 * 1000.0
        assert abs(result["total_pnl"] - expected_pnl) < 0.01


class TestReplayPositionRemoval:
    """Verify exited positions are removed, allowing new positions."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_exited_positions_removed(self, mock_session_local):
        """After position exits, slot opens for new position."""
        from app.services.paper_trading import PaperTradingEngine

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())
        mock_db.execute.return_value.fetchone.return_value = mock_run

        # 4 trades: first 3 overlap, 4th comes after first exits
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        trades = [
            # Trade 1: enters at T+0, exits at T+2
            (base_time, base_time + timedelta(hours=2), Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 2: enters at T+1, exits at T+3
            (base_time + timedelta(hours=1), base_time + timedelta(hours=3), Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 3: enters at T+1.5, exits at T+4
            (base_time + timedelta(hours=1, minutes=30), base_time + timedelta(hours=4), Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 4: enters at T+3 (after trade 1 exits), should be allowed
            (base_time + timedelta(hours=3), base_time + timedelta(hours=5), Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]
        mock_db.execute.return_value.fetchall.return_value = trades

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,  # Only 3 concurrent positions allowed
        )

        # All 4 trades should execute because trade 1 exits before trade 4 enters
        assert result["total_trades"] == 4, f"Expected 4 trades, got {result['total_trades']}"


class TestReplayActorType:
    """Verify actor_type is 'paper_trading', not 'human'."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_actor_type_is_paper_trading(self, mock_session_local):
        """Decision records must use actor_type='paper_trading'."""
        from app.services.paper_trading import PaperTradingEngine
        from sqlalchemy import text

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())

        entry_time = datetime(2026, 1, 1, 10, 0, 0)
        exit_time = datetime(2026, 1, 1, 11, 0, 0)
        trades = [
            (entry_time, exit_time, Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit")
        ]

        # Mock execute() to return run lookup first, then trades query
        mock_result_run = MagicMock()
        mock_result_run.fetchone.return_value = mock_run
        mock_result_trades = MagicMock()
        mock_result_trades.fetchall.return_value = trades

        mock_db.execute.side_effect = [mock_result_run, mock_result_trades] + [MagicMock() for _ in range(100)]

        engine = PaperTradingEngine("postgresql://test")
        engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # Find the decision_records INSERT call by checking the SQL text
        decision_insert_calls = []
        for c in mock_db.execute.call_args_list:
            if len(c[0]) > 0:
                sql_arg = c[0][0]
                # sql_arg is a TextClause object, convert to string
                sql_text = str(sql_arg)
                if "decision_records" in sql_text:
                    decision_insert_calls.append(c)

        assert len(decision_insert_calls) > 0, "decision_records INSERT must be called"

        # Check the SQL contains 'paper_trading' not 'human'
        decision_call = decision_insert_calls[0]
        sql_text = str(decision_call[0][0])
        assert "paper_trading" in sql_text, f"actor_type must be 'paper_trading', got: {sql_text}"
        assert "'human'" not in sql_text, "actor_type must NOT be 'human'"
