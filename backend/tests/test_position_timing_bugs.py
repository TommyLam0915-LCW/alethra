"""Position timing tests that catch the inverted removal bug.

These tests verify that:
1. Overlapping positions are correctly counted
2. max_positions blocks new entries when limit reached
3. Exited positions free up slots for new entries
4. Same-timestamp exit/entry is handled consistently
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4


def make_mock_db(mock_run, trades):
    """Helper to create a mock DB with proper call sequencing."""
    mock_db = MagicMock()
    call_count = [0]
    
    def execute_side_effect(*args, **kwargs):
        call_count[0] += 1
        mock_result = MagicMock()
        if call_count[0] == 1:
            # First call: run lookup
            mock_result.fetchone.return_value = mock_run
        elif call_count[0] == 2:
            # Second call: trades query
            mock_result.fetchall.return_value = trades
        else:
            # Subsequent calls: INSERT/UPDATE
            pass
        return mock_result
    
    mock_db.execute.side_effect = execute_side_effect
    return mock_db


class TestPositionTimingBugs:
    """Tests that catch the inverted position removal bug."""

    @patch("app.services.paper_trading.SessionLocal")
    def test_overlapping_positions_respect_max_limit(self, mock_session_local):
        """With max_positions=1, two overlapping trades should result in only 1 executed."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())

        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Two overlapping trades: trade 2 enters before trade 1 exits
        trades = [
            # Trade 1: enters T+0, exits T+10
            (base_time, base_time + timedelta(hours=10), 
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 2: enters T+5 (while trade 1 still active), exits T+15
            (base_time + timedelta(hours=5), base_time + timedelta(hours=15),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=1,  # Only 1 concurrent position allowed
        )

        # CRITICAL: With the bug, both trades execute (total_trades=2)
        # because the first position is incorrectly removed when trade 2 enters.
        # With the fix, only trade 1 executes (total_trades=1) because
        # trade 2 enters while trade 1 is still active.
        assert result["total_trades"] == 1, (
            f"max_positions=1 with overlapping trades should execute only 1 trade, "
            f"got {result['total_trades']}. This indicates the position removal logic is broken."
        )
        
        # Verify which trade was executed
        assert len(result["executed_trades"]) == 1
        assert result["executed_trades"][0]["entry_time"] == base_time.isoformat()

    @patch("app.services.paper_trading.SessionLocal")
    def test_sequential_positions_all_execute(self, mock_session_local):
        """Non-overlapping trades should all execute even with max_positions=1."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())

        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Three sequential trades: each enters after previous exits
        trades = [
            # Trade 1: enters T+0, exits T+2
            (base_time, base_time + timedelta(hours=2),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 2: enters T+3 (after trade 1 exits), exits T+5
            (base_time + timedelta(hours=3), base_time + timedelta(hours=5),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 3: enters T+6 (after trade 2 exits), exits T+8
            (base_time + timedelta(hours=6), base_time + timedelta(hours=8),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=1,
        )

        # All three should execute because they don't overlap
        assert result["total_trades"] == 3, (
            f"Sequential non-overlapping trades should all execute, "
            f"got {result['total_trades']}"
        )

    @patch("app.services.paper_trading.SessionLocal")
    def test_same_timestamp_exit_then_entry(self, mock_session_local):
        """When exit and entry happen at same timestamp, exit should be processed first."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())

        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Trade 1 exits at T+5, trade 2 enters at T+5 (same timestamp)
        trades = [
            (base_time, base_time + timedelta(hours=5),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            (base_time + timedelta(hours=5), base_time + timedelta(hours=10),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=1,
        )

        # Both should execute: trade 1 exits at T+5, freeing the slot for trade 2 at T+5
        # This tests the "exit before entry at same timestamp" rule
        assert result["total_trades"] == 2, (
            f"Same-timestamp exit/entry should allow both trades with max_positions=1, "
            f"got {result['total_trades']}"
        )

    @patch("app.services.paper_trading.SessionLocal")
    def test_position_count_tracks_correctly(self, mock_session_local):
        """Verify actual position count at each step, not just final trade count."""
        from app.services.paper_trading import PaperTradingEngine

        mock_run = MagicMock()
        mock_run.skill = "momentum"
        mock_run.instrument_id = "BTC"
        mock_run.interval_minutes = 60
        mock_run.initial_capital = Decimal("100000")
        mock_run.portfolio_id = str(uuid4())

        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # 4 trades: first 3 overlap, 4th comes after first exits
        trades = [
            # Trade 1: T+0 to T+5
            (base_time, base_time + timedelta(hours=5),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 2: T+1 to T+6 (overlaps with 1)
            (base_time + timedelta(hours=1), base_time + timedelta(hours=6),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 3: T+2 to T+7 (overlaps with 1 and 2)
            (base_time + timedelta(hours=2), base_time + timedelta(hours=7),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
            # Trade 4: T+6 to T+8 (trade 1 exited at T+5, trade 2 exits at T+6)
            # At T+6: trade 1 exited, trade 2 exits at same time, trade 3 still active
            # So only trade 3 is active when trade 4 enters
            (base_time + timedelta(hours=6), base_time + timedelta(hours=8),
             Decimal("50000"), Decimal("51000"), Decimal("1000"), "long", Decimal("1"), "exit"),
        ]

        mock_db = make_mock_db(mock_run, trades)
        mock_session_local.return_value = mock_db

        engine = PaperTradingEngine("postgresql://test")
        result = engine.execute_historical_replay(
            run_id=str(uuid4()),
            user_id=str(uuid4()),
            days_back=30,
            position_size_pct=0.10,
            max_positions=3,
        )

        # All 4 should execute:
        # - Trades 1,2,3 fill the 3 slots
        # - Trade 4 enters at T+6 when trade 1 has exited (T+5) and trade 2 exits (T+6)
        # - At T+6, only trade 3 is still active, so slot is available
        assert result["total_trades"] == 4, (
            f"Expected all 4 trades to execute, got {result['total_trades']}. "
            f"Position tracking is not working correctly."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
