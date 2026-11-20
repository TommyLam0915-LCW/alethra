"""Position timing tests that catch the inverted removal bug.

These tests verify that:
1. Overlapping positions are correctly counted
2. max_positions blocks new entries when the limit is reached
3. Exited positions free up slots for new entries
4. Same-timestamp exit/entry is handled consistently

They run the replay against the seeded history that tests/replay_fixture.py
builds — four source trades on one study run, all inside the ``days_back``
window — because the bug they guard (a position removed from the active map at
the wrong time) is only observable through which entries the simulation
actually admits.  Entries are priced with zero commission and slippage so the
admission decision, not the cost path, is what the counts reflect; the cost
path has its own coverage in test_ledger_reconciliation.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.replay_fixture import replay_case  # noqa: F401  (fixture registered by import)

UP = (Decimal("50000"), Decimal("51000"))
NO_COSTS = {"commission_rate": Decimal(0), "slippage_rate": Decimal(0)}


class TestPositionTimingBugs:
    """Tests that catch the inverted position removal bug."""

    def test_overlapping_positions_respect_max_limit(self, replay_case):
        """With max_positions=1, two overlapping trades execute only the first."""
        case = replay_case([
            (0, 10, *UP),   # trade 1: enters T+0, exits T+10
            (5, 15, *UP),   # trade 2: enters T+5 while trade 1 is still open
        ])

        result = case.replay(position_size_pct=0.10, max_positions=1, **NO_COSTS)

        # CRITICAL: with the bug both trades execute (total_trades=2) because
        # the first position is incorrectly removed when trade 2 enters.  With
        # the fix only trade 1 executes, because trade 2 enters while trade 1
        # is still active.
        assert result["total_trades"] == 1, (
            f"max_positions=1 with overlapping trades should execute only 1 trade, "
            f"got {result['total_trades']}. This indicates the position removal logic is broken."
        )
        assert len(result["executed_trades"]) == 1
        assert datetime.fromisoformat(result["executed_trades"][0]["entry_time"]) == case.base_time, (
            "the trade that took the only slot must be the one that entered first"
        )
        blocked = [e for e in result["event_log"] if e["event"] == "entry_blocked"]
        assert [(b["trade_idx"], b["reason"]) for b in blocked] == [(1, "max_positions")]

    def test_sequential_positions_all_execute(self, replay_case):
        """Non-overlapping trades should all execute even with max_positions=1."""
        case = replay_case([
            (0, 2, *UP),    # enters T+0, exits T+2
            (3, 5, *UP),    # enters T+3, after trade 1 exited
            (6, 8, *UP),    # enters T+6, after trade 2 exited
        ])

        result = case.replay(position_size_pct=0.10, max_positions=1, **NO_COSTS)

        assert result["total_trades"] == 3, (
            f"Sequential non-overlapping trades should all execute, "
            f"got {result['total_trades']}: {result['event_log']}"
        )
        assert not [e for e in result["event_log"] if e["event"] == "entry_blocked"]

    def test_same_timestamp_exit_then_entry(self, replay_case):
        """When exit and entry share a timestamp, the exit is processed first."""
        case = replay_case([
            (0, 5, *UP),    # trade 1 exits at T+5
            (5, 10, *UP),   # trade 2 enters at T+5
        ])

        result = case.replay(position_size_pct=0.10, max_positions=1, **NO_COSTS)

        assert result["total_trades"] == 2, (
            f"Same-timestamp exit/entry should allow both trades with max_positions=1, "
            f"got {result['total_trades']}: {result['event_log']}"
        )
        shared = [e for e in result["event_log"]
                  if e["time"] == (case.base_time + timedelta(hours=5)).isoformat()]
        assert [e["event"] for e in shared] == ["exit", "entry"], (
            f"the shared T+5 timestamp must settle the exit before admitting the entry: "
            f"{shared}"
        )

    def test_position_count_tracks_correctly(self, replay_case):
        """Verify actual position count at each step, not just the final trade count."""
        case = replay_case([
            (0, 5, *UP),    # trade 1: T+0 → T+5
            (1, 6, *UP),    # trade 2: overlaps 1
            (2, 7, *UP),    # trade 3: overlaps 1 and 2
            (6, 8, *UP),    # trade 4: enters when 1 has exited and 2 exits at T+6
        ])

        result = case.replay(position_size_pct=0.10, max_positions=3, **NO_COSTS)

        # All 4 execute: trades 1-3 fill the 3 slots; trade 4 enters at T+6 when
        # trade 1 exited (T+5) and trade 2 exits at the same instant, so only
        # trade 3 is still active and a slot is available.
        assert result["total_trades"] == 4, (
            f"Expected all 4 trades to execute, got {result['total_trades']}. "
            f"Position tracking is not working correctly: {result['event_log']}"
        )
        # The active-position count recorded at each entry is the direct evidence
        # that slots are freed by exits rather than by the entries themselves.
        entries = [e for e in result["event_log"] if e["event"] == "entry"]
        assert [e["active_positions"] for e in entries] == [1, 2, 3, 2], entries


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
