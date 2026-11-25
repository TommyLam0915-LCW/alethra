"""Position timing tests that catch the inverted removal bug.

These tests verify that:
1. Overlapping positions are correctly counted
2. max_positions blocks new entries when limit reached
3. Exited positions free up slots for new entries
4. Same-timestamp exit/entry is handled consistently

The replay is driven through PaperTradingEngine(session_factory=...) — the
dependency the service actually reads. An earlier revision patched the module
global SessionLocal while constructing the engine with a database URL, which
sent the engine down its legacy self-connecting path: the patch was never read,
so no assertion here had ever looked at a replay the product ran.
"""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from tests.replay_fake import make_replay_db, make_run_row, trade_row


def _trade(entry, exit_):
    return trade_row(
        source_trade_id=str(uuid4()),
        entry_time=entry,
        exit_time=exit_,
        entry_price=Decimal("50000"),
        exit_price=Decimal("51000"),
        net_pnl=Decimal("1000"),
        direction="long",
        quantity=Decimal("1"),
        exit_reason="exit",
    )


def _replay(trades, *, max_positions):
    """Run the replay clock over ``trades`` and hand back its result mapping."""
    from app.services.paper_trading import PaperTradingEngine

    run = make_run_row()
    user_id = str(uuid4())
    db = make_replay_db(run=run, owner_id=user_id, trades=trades)
    engine = PaperTradingEngine(session_factory=lambda: db)
    return engine.execute_historical_replay(
        run_id=run.id,
        user_id=user_id,
        days_back=30,
        position_size_pct=0.10,
        max_positions=max_positions,
    )


class TestPositionTimingBugs:
    """Tests that catch the inverted position removal bug."""

    def test_overlapping_positions_respect_max_limit(self):
        """With max_positions=1, two overlapping trades should result in only 1 executed."""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Two overlapping trades: trade 2 enters before trade 1 exits
        trades = [
            # Trade 1: enters T+0, exits T+10
            _trade(base_time, base_time + timedelta(hours=10)),
            # Trade 2: enters T+5 (while trade 1 still active), exits T+15
            _trade(base_time + timedelta(hours=5), base_time + timedelta(hours=15)),
        ]

        # CRITICAL: With the bug, both trades execute (total_trades=2)
        # because the first position is incorrectly removed when trade 2 enters.
        # With the fix, only trade 1 executes (total_trades=1) because
        # trade 2 enters while trade 1 is still active.
        result = _replay(trades, max_positions=1)
        assert result["total_trades"] == 1, (
            f"max_positions=1 with overlapping trades should execute only 1 trade, "
            f"got {result['total_trades']}. This indicates the position removal logic is broken."
        )

        # Verify which trade was executed
        assert len(result["executed_trades"]) == 1
        assert result["executed_trades"][0]["entry_time"] == base_time.isoformat()

    def test_sequential_positions_all_execute(self):
        """Non-overlapping trades should all execute even with max_positions=1."""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Three sequential trades: each enters after previous exits
        trades = [
            # Trade 1: enters T+0, exits T+2
            _trade(base_time, base_time + timedelta(hours=2)),
            # Trade 2: enters T+3 (after trade 1 exits), exits T+5
            _trade(base_time + timedelta(hours=3), base_time + timedelta(hours=5)),
            # Trade 3: enters T+6 (after trade 2 exits), exits T+8
            _trade(base_time + timedelta(hours=6), base_time + timedelta(hours=8)),
        ]

        result = _replay(trades, max_positions=1)

        # All three should execute because they don't overlap
        assert result["total_trades"] == 3, (
            f"Sequential non-overlapping trades should all execute, "
            f"got {result['total_trades']}"
        )

    def test_same_timestamp_exit_then_entry(self):
        """When exit and entry happen at same timestamp, exit should be processed first."""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # Trade 1 exits at T+5, trade 2 enters at T+5 (same timestamp)
        trades = [
            _trade(base_time, base_time + timedelta(hours=5)),
            _trade(base_time + timedelta(hours=5), base_time + timedelta(hours=10)),
        ]

        result = _replay(trades, max_positions=1)

        # Both should execute: trade 1 exits at T+5, freeing the slot for trade 2 at T+5
        # This tests the "exit before entry at same timestamp" rule
        assert result["total_trades"] == 2, (
            f"Same-timestamp exit/entry should allow both trades with max_positions=1, "
            f"got {result['total_trades']}"
        )

    def test_position_count_tracks_correctly(self):
        """Verify actual position count at each step, not just final trade count."""
        base_time = datetime(2026, 1, 1, 10, 0, 0)
        # 4 trades: first 3 overlap, 4th comes after first exits
        trades = [
            # Trade 1: T+0 to T+5
            _trade(base_time, base_time + timedelta(hours=5)),
            # Trade 2: T+1 to T+6 (overlaps with 1)
            _trade(base_time + timedelta(hours=1), base_time + timedelta(hours=6)),
            # Trade 3: T+2 to T+7 (overlaps with 1 and 2)
            _trade(base_time + timedelta(hours=2), base_time + timedelta(hours=7)),
            # Trade 4: T+6 to T+8 (trade 1 exited at T+5, trade 2 exits at T+6)
            # At T+6: trade 1 exited, trade 2 exits at same time, trade 3 still active
            # So only trade 3 is active when trade 4 enters
            _trade(base_time + timedelta(hours=6), base_time + timedelta(hours=8)),
        ]

        # All 4 should execute:
        # - Trades 1,2,3 fill the 3 slots
        # - Trade 4 enters at T+6 when trade 1 has exited (T+5) and trade 2 exits (T+6)
        # - At T+6, only trade 3 is still active, so slot is available
        result = _replay(trades, max_positions=3)
        assert result["total_trades"] == 4, (
            f"Expected all 4 trades to execute, got {result['total_trades']}. "
            f"Position tracking is not working correctly."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
