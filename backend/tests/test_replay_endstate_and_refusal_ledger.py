"""Group-1 replay accounting: end-of-horizon settlement, refusals, and the counts.

Four contracts, each of which used to be a silently-wrong number rather than a
failure:

* **End state** (G1-1). A position could still be open when the event loop
  ended. The old handler moved cash and appended an executed trade while leaving
  ``paper_positions.status='open'``, writing no exit decision/order/fill, and
  paying no exit cost — so the run reported a closed round trip that the books
  still carried as open, and the cash it booked was net of costs that appear
  nowhere. This suite drives the one input that reaches that branch — a study row
  whose ``exit_time`` is not later than its ``entry_time``, so the exit event is
  consumed before the entry that opens the position — and requires the full
  four-row settlement. The row is a real, already-stored economic event, so
  settling it is not "补造无对应经济事件的成交"; the source row's own exit price
  and timestamp are what get booked.
* **Quantity separation** (G1-2). Cash, position cost basis, position market
  value, realized and unrealized P&L and equity were all one number called
  ``final_capital``, and ``paper_portfolios.current_virtual_cash`` was written
  with equity. Here they are reported apart, with definitions, and the cash
  column holds cash.
* **Refusals** (G1-4). A guard that declined an entry appended to an in-memory
  ``event_log`` that ``ReplayResponse`` drops, so the durable record said the
  signal had never arrived. Each refusal now leaves a ``no_action`` decision, a
  ``rejected`` order carrying the proposal that was declined, and an audit event
  — and, asserted as strictly as the rows themselves, no fill, no position, no
  cash movement and no settlement counter.
* **Counts** (G1-5). ``total_trades`` and ``paper_trading_daily_metrics`` were
  two unlabelled numbers, with no statement of what either counted. They are
  defined apart, derived from one settlement list, and reconciled per event
  rather than forced equal.

The independent recomputation in :meth:`TestTerminalLedgerSeparation
.test_every_terminal_quantity_reproduces_from_the_source_row_by_hand` uses the
cost rule as written (0.1% commission, 0.05% slippage, both legs) on prices chosen
so every step is exact in Decimal: 10 000 notional at 50 000 is 0.2 units, and
15.3 exit costs on a 10 200 gross is a number no rounding step can hide.
"""

from decimal import Decimal
import json

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from tests.replay_fixture import replay_case  # noqa: F401  (fixture registered by import)

COMMISSION = Decimal("0.001")
SLIPPAGE = Decimal("0.0005")
COSTS = {"commission_rate": COMMISSION, "slippage_rate": SLIPPAGE}

# One long round trip: 10% of 100 000 capital = 10 000 notional, 0.2 units at
# 50 000, +1 000 per unit price move. entry/exit hours are offsets from the
# fixture's base time (30 h ago); (0, 1) is a normal signal exit and (3, 1) is
# the inverted row that reaches the end-of-horizon settlement.
SIGNAL_EXIT = (0, 1, Decimal("50000"), Decimal("51000"))
INVERTED = (3, 1, Decimal("50000"), Decimal("51000"))

# Hand-derived from the two rows above and the rates, never copied from the
# engine: entry costs 10 + 5 = 15, exit costs 10.2 + 5.1 = 15.3, so a +200 gross
# round trip nets 169.7 and the cash column ends at 100 000 + 169.7.
EXPECTED_ENTRY_COSTS = Decimal("15")
EXPECTED_EXIT_COSTS = Decimal("15.3")
EXPECTED_NET_PNL = Decimal("169.7")
EXPECTED_CASH = Decimal("100169.7")


def _read(db, sql, **params):
    return db.execute(text(sql), params).fetchall()


def _one(db, sql, **params):
    rows = _read(db, sql, **params)
    assert len(rows) == 1, f"expected exactly one row, got {len(rows)}: {rows}"
    return rows[0]


def _json(value):
    """Parse a JSON column read back through raw SQL.

    PostgreSQL hands a decoded object and SQLite hands the stored text, so the
    assertion has to say which representation it means rather than assume one.
    """
    return json.loads(value) if isinstance(value, str) else value


class TestEndOfHorizonSettlement:
    """G1-1: a position still open at the end of the run is settled, not half-closed."""

    def test_the_inverted_source_row_is_the_route_that_leaves_a_position_open(self, replay_case):
        """Coverage first: this input must reach the branch at all.

        Without this node the settlement assertions below could be passing
        because the loop happened to close everything, which is the normal path
        and proves nothing about the end-of-horizon block.
        """
        case = replay_case([INVERTED])
        result = case.replay(**COSTS)

        # The exit event was consumed before the entry opened the position, so
        # the in-loop exit branch never saw this idx; exactly one settlement is
        # recorded and it is the horizon-end one.
        assert result["counting"]["run_settlements"] == 1
        assert result["counting"]["events_processed"] == 2
        assert [t["settled_at_horizon_end"] for t in result["executed_trades"]] == [True]
        assert result["terminal_ledger"]["open_positions"] == 0

    def test_settlement_writes_decision_order_fill_and_closes_the_position(self, replay_case):
        """The four rows a witnessed settlement owes, read back from the DB."""
        case = replay_case([INVERTED])
        case.replay(**COSTS)

        db = SessionLocal()
        try:
            decision = _one(
                db,
                "SELECT decision_type, actor_type, rationale FROM decision_records "
                "WHERE paper_portfolio_id = :p AND decision_type = 'exit'",
                p=case.portfolio_id,
            )
            assert decision.decision_type == "exit"
            assert decision.actor_type == "system"
            assert "End-of-horizon settlement" in decision.rationale

            # Both legs of the round trip are on the books: the BUY that opened
            # it and the SELL that settled it. Only the settlement is new here;
            # asserting the pair is what shows the horizon block wrote an exit
            # order rather than re-labelled the entry.
            orders = _read(
                db,
                "SELECT side, status, actor_type FROM paper_orders "
                "WHERE portfolio_id = :p AND status = 'filled' ORDER BY side",
                p=case.portfolio_id,
            )
            # Long round trip: the closing leg is a SELL.
            assert [o.side for o in orders] == ["BUY", "SELL"]
            assert {o.actor_type for o in orders} == {"paper_trading"}

            fill = _one(
                db,
                "SELECT f.commission_amount, f.slippage_amount, f.gross_amount, "
                "f.net_cash_change, f.simulated_fill_reason "
                "FROM paper_fills f JOIN paper_orders o ON f.paper_order_id = o.id "
                "WHERE o.portfolio_id = :p AND f.simulated_fill_reason = :reason",
                p=case.portfolio_id,
                reason="end_of_horizon_settlement",
            )
            assert Decimal(str(fill.commission_amount)) == Decimal("10.2")
            assert Decimal(str(fill.slippage_amount)) == Decimal("5.1")
            assert Decimal(str(fill.gross_amount)) == Decimal("10200")
            # Net proceeds after exit costs — the leg the old handler omitted.
            assert Decimal(str(fill.net_cash_change)) == Decimal("10200") - EXPECTED_EXIT_COSTS

            position = _one(
                db,
                "SELECT status, realized_pnl, closed_at FROM paper_positions WHERE portfolio_id = :p",
                p=case.portfolio_id,
            )
            assert position.status == "closed"
            assert position.closed_at is not None
            assert Decimal(str(position.realized_pnl)) == EXPECTED_NET_PNL
        finally:
            db.close()

    def test_nothing_is_left_open_on_the_books(self, replay_case):
        case = replay_case([INVERTED])
        case.replay(**COSTS)

        db = SessionLocal()
        try:
            open_rows = _read(
                db,
                "SELECT id FROM paper_positions WHERE portfolio_id = :p AND status = 'open'",
                p=case.portfolio_id,
            )
            assert open_rows == [], f"settlement left {len(open_rows)} positions open"
        finally:
            db.close()

    def test_horizon_settlement_and_signal_exit_share_one_arithmetic(self, replay_case):
        """The two settlement paths must not disagree about what an exit costs.

        Same source row, only the timestamp order differs, so every economic
        column has to come out identical. A second implementation of the cost
        rule in the end-of-horizon block is exactly the drift that made the
        ledger and the money move apart.
        """
        signal = replay_case([SIGNAL_EXIT]).replay(**COSTS)
        horizon = replay_case([INVERTED]).replay(**COSTS)

        for key in ("gross_pnl", "entry_commission", "entry_slippage",
                    "exit_commission", "exit_slippage", "net_pnl"):
            assert signal["executed_trades"][0][key] == pytest.approx(
                horizon["executed_trades"][0][key], abs=1e-12
            ), f"{key} diverges between the two settlement paths"

        assert "settled_at_horizon_end" not in signal["executed_trades"][0]

    def test_a_settled_position_does_not_block_the_next_round_trip(self, replay_case):
        """Settling at the horizon leaves the run flat, reconciled and countable.

        ``execute_historical_replay`` raises on a reconciliation break, so
        returning a completed result at all is part of the evidence — but the
        second trade here is what shows the first one's capital came back into
        the sizing pool instead of staying locked as an open position. The two
        round trips have different notionals (the second sizes off the equity
        the first one's costs reduced), so the closed-form check is the ledger
        identity, not a hand-derived dollar figure.
        """
        case = replay_case([INVERTED, (10, 11, Decimal("50000"), Decimal("51000"))])
        result = case.replay(**COSTS)
        ledger = result["terminal_ledger"]
        settled = result["executed_trades"]

        assert result["reconciliation_status"] == "passed"
        assert result["reconciliation_diff"] == pytest.approx(0.0, abs=1e-9)
        assert result["counting"]["run_settlements"] == 2
        assert ledger["open_positions"] == 0
        assert len({t["entry_time"] for t in settled}) == 2, "the two trades are distinct entries"

        realized = sum((Decimal(str(t["net_pnl"])) for t in settled), Decimal("0"))
        assert Decimal(str(ledger["realized_pnl_net_of_costs"])) == realized
        assert Decimal(str(ledger["equity"])) == Decimal("100000") + realized
        assert Decimal(str(ledger["cash_available"])) == Decimal(str(ledger["equity"]))


class TestTerminalLedgerSeparation:
    """G1-2: cash, cost basis, market value, realized and unrealized P&L are separate."""

    def test_every_terminal_quantity_is_named_apart_and_defined(self, replay_case):
        case = replay_case([SIGNAL_EXIT])
        ledger = case.replay(**COSTS)["terminal_ledger"]

        for key in ("cash_available", "position_cost_basis", "position_market_value",
                    "realized_pnl_net_of_costs", "equity", "open_positions",
                    "unrealized_pnl", "currency"):
            assert key in ledger, f"{key} missing from terminal_ledger: {sorted(ledger)}"

        # The expectation is read off the ledger instead of hand-listed beside it:
        # a quantity added without a definition, or a definition describing a
        # quantity that was dropped, both fail here. "記錄單位與定義" is only met
        # when neither can happen, and a hand-copied list of five names lets the
        # first happen silently.
        reported = {k for k in ledger if k != "definitions"}
        defined = set(ledger["definitions"])
        assert reported, "the ledger reports no quantity, so this check is vacuous"
        assert defined == reported, (
            f"reported without definition: {sorted(reported - defined)}; "
            f"defined without reporting: {sorted(defined - reported)}"
        )

    def test_the_unit_is_the_portfolio_row_and_not_a_restated_literal(self, replay_case):
        """The ledger says which currency it means, and gets it from the record.

        Hardcoding "USD" beside a portfolio row that also says "USD" passes for
        the wrong reason — the value never travels. Re-booking the row to EUR
        before the replay is what makes the two claims distinguishable.
        """
        case = replay_case([SIGNAL_EXIT])
        db = SessionLocal()
        try:
            moved = db.execute(
                text("UPDATE paper_portfolios SET currency = 'EUR' "
                     "WHERE id = :p"),
                {"p": case.portfolio_id},
            )
            db.commit()
            assert moved.rowcount == 1, "the fixture portfolio row was not found to re-book"
        finally:
            db.close()

        ledger = case.replay(**COSTS)["terminal_ledger"]
        assert ledger["currency"] == "EUR", (
            "the ledger restated its own literal instead of reporting the row's unit"
        )

        db = SessionLocal()
        try:
            row = _one(db, "SELECT currency FROM paper_portfolios WHERE id = :p",
                       p=case.portfolio_id)
            assert row.currency == ledger["currency"]
        finally:
            db.close()

    def test_every_terminal_quantity_reproduces_from_the_source_row_by_hand(self, replay_case):
        """Independent recomputation along the actual cost rule.

        Nothing here is read back from the engine: the numbers are derived from
        the seeded prices, the sizing parameter and the two rates.
        """
        case = replay_case([SIGNAL_EXIT], initial_capital=Decimal("100000"))
        result = case.replay(position_size_pct=0.10, **COSTS)
        ledger = result["terminal_ledger"]

        notional = Decimal("100000") * Decimal("0.10")
        quantity = notional / Decimal("50000")
        gross = quantity * (Decimal("51000") - Decimal("50000"))
        entry_costs = notional * (COMMISSION + SLIPPAGE)
        exit_gross = quantity * Decimal("51000")
        exit_costs = exit_gross * (COMMISSION + SLIPPAGE)
        net = gross - entry_costs - exit_costs

        assert entry_costs == EXPECTED_ENTRY_COSTS
        assert exit_costs == EXPECTED_EXIT_COSTS
        assert net == EXPECTED_NET_PNL

        assert Decimal(str(ledger["cash_available"])) == Decimal("100000") - notional - entry_costs + (
            notional + gross - exit_costs
        )
        assert Decimal(str(ledger["cash_available"])) == EXPECTED_CASH
        assert Decimal(str(ledger["position_cost_basis"])) == Decimal("0")
        assert Decimal(str(ledger["position_market_value"])) == Decimal("0")
        assert Decimal(str(ledger["unrealized_pnl"])) == Decimal("0")
        assert Decimal(str(ledger["realized_pnl_net_of_costs"])) == net
        assert Decimal(str(ledger["equity"])) == net + Decimal("100000")
        assert Decimal(str(result["total_pnl"])) == net
        # Realized P&L equals run P&L exactly when nothing is open; with a
        # position outstanding the two would differ and only equity would cover
        # both.
        assert Decimal(str(ledger["realized_pnl_net_of_costs"])) == Decimal(str(result["total_pnl"]))

    def test_portfolio_cash_column_holds_cash_not_equity(self, replay_case):
        """The column is named for cash, so it must be bound to the cash balance."""
        case = replay_case([SIGNAL_EXIT])
        ledger = case.replay(**COSTS)["terminal_ledger"]

        db = SessionLocal()
        try:
            row = _one(
                db,
                "SELECT current_virtual_cash, initial_virtual_cash FROM paper_portfolios WHERE id = :p",
                p=case.portfolio_id,
            )
        finally:
            db.close()

        assert Decimal(str(row.current_virtual_cash)) == Decimal(str(ledger["cash_available"]))
        assert Decimal(str(row.initial_virtual_cash)) == Decimal("100000")

    def test_absent_market_value_is_none_not_a_cost_basis_wearing_the_label(self, replay_case):
        """Unmeasured must not be written as zero-with-a-different-name.

        This replay has no price source outside the study rows, so a position
        left open would have no market value at all. The settlement path closes
        everything, so the measured zero is what belongs here — and the
        conditional is stated in the ledger rather than implied.
        """
        case = replay_case([SIGNAL_EXIT])
        ledger = case.replay(**COSTS)["terminal_ledger"]

        assert ledger["open_positions"] == 0
        assert ledger["position_market_value"] == 0.0
        assert ledger["unrealized_pnl"] == 0.0
        assert "None when nothing is open" in ledger["definitions"]["position_market_value"]


class TestEntryRefusalTraceability:
    """G1-4: a declined entry leaves a traceable refusal and changes no money."""

    # Four entries opened before any exit, against the default max_positions=3:
    # the fourth is refused, the first three settle normally.
    OVER_CAPACITY = [
        (1, 10, Decimal("50000"), Decimal("51000")),
        (2, 11, Decimal("50000"), Decimal("51000")),
        (3, 12, Decimal("50000"), Decimal("51000")),
        (4, 13, Decimal("50000"), Decimal("51000")),
    ]

    def test_refusal_writes_decision_order_and_audit_rows(self, replay_case):
        case = replay_case(self.OVER_CAPACITY)
        result = case.replay(**COSTS)

        assert len(result["refused_entries"]) == 1
        refusal = result["refused_entries"][0]

        db = SessionLocal()
        try:
            decision = _one(
                db,
                "SELECT decision_type, actor_type, risk_flags, rationale, source_trade_id "
                "FROM decision_records WHERE id = :id",
                id=refusal["decision_id"],
            )
            assert decision.decision_type == "no_action"
            assert decision.actor_type == "system"
            assert _json(decision.risk_flags)["refusal_reason"] == "max_positions"
            assert _json(decision.risk_flags)["active_positions"] == 3
            assert "max_positions" in decision.rationale

            order = _one(
                db,
                "SELECT status, risk_check_status, side, quantity, decision_id "
                "FROM paper_orders WHERE id = :id",
                id=refusal["refused_order_id"],
            )
            assert order.status == "rejected"
            assert order.risk_check_status == "failed"
            assert order.side == "BUY"
            # The proposal that was declined is recorded, so the reader can see
            # what the engine would have bought.
            assert Decimal(str(order.quantity)) > Decimal("0")
            assert str(order.decision_id) == refusal["decision_id"]

            audit = _one(
                db,
                "SELECT entity_type, event_type, actor_type, payload_json "
                "FROM audit_events WHERE entity_type = 'paper_order' AND entity_id = :id",
                id=refusal["refused_order_id"],
            )
            assert audit.event_type == "paper_entry_refused"
            assert audit.actor_type == "system"
            assert _json(audit.payload_json)["reason"] == "max_positions"
            assert str(_json(audit.payload_json)["decision_id"]) == refusal["decision_id"]
        finally:
            db.close()

    def test_the_refusal_is_traceable_to_the_study_trade_it_declined(self, replay_case):
        """A refusal that cannot name its source is a lost signal, not a declined one."""
        case = replay_case(self.OVER_CAPACITY)
        result = case.replay(**COSTS)

        db = SessionLocal()
        try:
            refused_source = _one(
                db,
                "SELECT id FROM strategy_study_trades WHERE run_id = :r "
                "ORDER BY entry_time DESC LIMIT 1",
                r=case.study_run_id,
            )
            decision = _one(
                db,
                "SELECT source_trade_id, source_run_id, source_rule_id, source_exit_config, "
                "cutoff_timestamp, as_of_timestamp FROM decision_records WHERE id = :id",
                id=result["refused_entries"][0]["decision_id"],
            )
        finally:
            db.close()

        assert str(decision.source_trade_id) == str(refused_source.id)
        assert str(decision.source_run_id) == case.study_run_id
        # The whole provenance set travels with a refusal, exactly as with a fill.
        assert decision.source_rule_id == "replay_rule"
        assert decision.source_exit_config == "replay_exit"
        assert decision.cutoff_timestamp is not None
        assert decision.as_of_timestamp is not None

    def test_refusal_writes_no_fill_and_no_position(self, replay_case):
        """The line between "declined" and "traded but unstated" is these two counts."""
        case = replay_case(self.OVER_CAPACITY)
        result = case.replay(**COSTS)
        refused_order_id = result["refused_entries"][0]["refused_order_id"]

        db = SessionLocal()
        try:
            fills_for_refusal = _read(
                db,
                "SELECT id FROM paper_fills WHERE paper_order_id = :o",
                o=refused_order_id,
            )
            positions_for_refusal = _read(
                db,
                "SELECT id FROM paper_positions WHERE portfolio_id = :p "
                "AND source_trade_id = (SELECT source_trade_id FROM decision_records WHERE id = :d)",
                p=case.portfolio_id,
                d=result["refused_entries"][0]["decision_id"],
            )
            # Three settled round trips = 3 entry fills + 3 exit fills. The
            # refusal contributes neither leg.
            fill_count = _one(
                db,
                "SELECT COUNT(*) AS n FROM paper_fills f "
                "JOIN paper_orders o ON f.paper_order_id = o.id WHERE o.portfolio_id = :p",
                p=case.portfolio_id,
            )
            position_count = _one(
                db,
                "SELECT COUNT(*) AS n FROM paper_positions WHERE portfolio_id = :p",
                p=case.portfolio_id,
            )
        finally:
            db.close()

        assert fills_for_refusal == []
        assert positions_for_refusal == []
        assert fill_count.n == 6
        assert position_count.n == 3

    def test_refusal_moves_no_cash_and_adds_no_settlement_counter(self, replay_case):
        """The refused fourth trade is worth exactly nothing, measured.

        Control pair: run A trades the first three rows; run B trades the same
        three and is refused the fourth. Every economic number the two runs
        report must be identical, which is a stronger statement than "the
        refusal contributed no P&L" — it says the declined entry left no mark
        anywhere on the books, sizing included.
        """
        admitted = [row for row in self.OVER_CAPACITY[:3]]
        run_a = replay_case(admitted).replay(**COSTS)
        run_b = replay_case(self.OVER_CAPACITY).replay(**COSTS)

        assert run_b["counting"]["entries_refused"] == 1
        assert run_b["counting"]["refusals_by_reason"] == {"max_positions": 1}

        # Run A never reaches the capacity guard, so it has nothing to refuse.
        assert run_a["counting"]["entries_refused"] == 0
        ledger_a = dict(run_a["terminal_ledger"])
        ledger_b = dict(run_b["terminal_ledger"])
        definitions_a = ledger_a.pop("definitions")
        assert ledger_b.pop("definitions") == definitions_a
        assert ledger_a.keys() == ledger_b.keys()
        for key, value in ledger_a.items():
            other = ledger_b[key]
            if isinstance(value, float):
                assert other == pytest.approx(value), f"{key} differs between the pair"
            else:
                assert other == value, f"{key} differs between the pair"
        assert run_a["counting"]["run_settlements"] == run_b["counting"]["run_settlements"] == 3
        assert run_a["total_pnl"] == pytest.approx(run_b["total_pnl"])
        assert run_a["final_capital"] == pytest.approx(run_b["final_capital"])

    @pytest.mark.parametrize(
        "trades,expected_reason",
        [
            (OVER_CAPACITY, "max_positions"),
            ([(0, 1, Decimal("0"), Decimal("51000"))], "invalid_price"),
        ],
        ids=["capacity", "invalid_price"],
    )
    def test_the_recorded_reason_is_the_guard_that_fired(self, replay_case, trades, expected_reason):
        """Two different guards produce two different reasons.

        A single hard-coded reason string would pass a test that only ever
        exercised one guard, which is how "the engine declined it" stops meaning
        anything specific.
        """
        case = replay_case(trades)
        result = case.replay(**COSTS)

        assert [r["reason"] for r in result["refused_entries"]] == [expected_reason]
        assert result["counting"]["refusals_by_reason"] == {expected_reason: 1}

    def test_a_clean_run_records_no_refusal_rows(self, replay_case):
        """Negative control: refusals are written because of refusals, not always."""
        case = replay_case([SIGNAL_EXIT])
        result = case.replay(**COSTS)

        assert result["refused_entries"] == []
        assert result["counting"]["entries_refused"] == 0
        assert result["counting"]["refusals_by_reason"] == {}

        db = SessionLocal()
        try:
            no_action = _one(
                db,
                "SELECT COUNT(*) AS n FROM decision_records "
                "WHERE paper_portfolio_id = :p AND decision_type = 'no_action'",
                p=case.portfolio_id,
            )
            rejected = _one(
                db,
                "SELECT COUNT(*) AS n FROM paper_orders "
                "WHERE portfolio_id = :p AND status = 'rejected'",
                p=case.portfolio_id,
            )
            audit = _one(
                db,
                "SELECT COUNT(*) AS n FROM audit_events WHERE event_type = 'paper_entry_refused'",
            )
        finally:
            db.close()

        assert (no_action.n, rejected.n, audit.n) == (0, 0, 0)


class TestDailyAndRunCounting:
    """G1-5: what each count means, and the per-event map between them."""

    def test_run_and_daily_counts_agree_because_both_walk_the_settlement_list(self, replay_case):
        """Not forced equal — derived from one list, then read back separately."""
        case = replay_case([SIGNAL_EXIT, (5, 6, Decimal("50000"), Decimal("49000"))])
        result = case.replay(**COSTS)
        counting = result["counting"]

        assert counting["run_settlements"] == 2
        assert counting["daily_trades_sum"] == 2

        db = SessionLocal()
        try:
            run = _one(
                db, "SELECT total_trades FROM paper_trading_runs WHERE id = :r", r=case.run_id
            )
            metrics = _read(
                db,
                "SELECT date, trades_count, wins, losses, daily_pnl "
                "FROM paper_trading_daily_metrics WHERE run_id = :r ORDER BY date",
                r=case.run_id,
            )
        finally:
            db.close()

        assert run.total_trades == counting["run_settlements"]
        assert sum(m.trades_count for m in metrics) == counting["daily_trades_sum"]

        # Per-event mapping: each settlement lands in exactly one date bucket, so
        # the daily wins/losses partition the run's own win/loss tally.
        settled = result["executed_trades"]
        assert sum(m.wins for m in metrics) == sum(1 for t in settled if t["net_pnl"] > 0)
        assert sum(m.losses for m in metrics) == sum(1 for t in settled if t["net_pnl"] <= 0)
        for metric in metrics:
            day_settlements = [
                t for t in settled if t["exit_time"][:10] == str(metric.date)
            ]
            assert metric.trades_count == len(day_settlements)
            assert Decimal(str(metric.daily_pnl)) == sum(
                (Decimal(str(t["net_pnl"])) for t in day_settlements), Decimal("0")
            )

    def test_a_refusal_is_counted_outside_the_settlement_counts(self, replay_case):
        """Daily metrics must not gain a phantom trade for a declined entry."""
        case = replay_case(TestEntryRefusalTraceability.OVER_CAPACITY)
        result = case.replay(**COSTS)
        counting = result["counting"]

        db = SessionLocal()
        try:
            metrics = _read(
                db,
                "SELECT trades_count FROM paper_trading_daily_metrics WHERE run_id = :r",
                r=case.run_id,
            )
        finally:
            db.close()

        assert counting["daily_trades_sum"] == counting["run_settlements"] == 3
        assert sum(m.trades_count for m in metrics) == 3
        assert counting["events_processed"] == 8

    def test_win_rate_counts_settlements_and_never_refusals(self, replay_case):
        """A refused entry cannot be scored as a loss.

        ``percent_ratio`` returns a proportion in percent, so the denominator is
        what this node is about: 3 wins out of 3 settlements reads 100.0, while
        counting the refusal as a fourth trade would read 75.0 and charge the run
        for a round trip it never took.
        """
        case = replay_case(TestEntryRefusalTraceability.OVER_CAPACITY)
        result = case.replay(**COSTS)

        assert result["win_rate"] == pytest.approx(100.0)
        assert result["counting"]["run_settlements"] == 3
        assert result["counting"]["entries_refused"] == 1
