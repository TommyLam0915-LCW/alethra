"""G1-6: two books checked against themselves, and the gap between them named.

The ruling that opened this file was that "they are two books" is not an
explanation of a difference. The reconciliation therefore owes three separate
claims, and each is tested apart from the others:

* **Each book agrees with its own columns.** The study rows are checked against
  the identities a study owes, the replay's fills against the identities a
  replay owes, and terminal cash against opening capital plus booked net.
  Neither check borrows a number from the other book, which is what makes a
  compensating pair visible:
  :meth:`TestEachBookAgreesWithItself.test_a_compensating_pair_is_two_violations_even_though_its_residual_cancels`
  moves gross +5 on a win and -5 on a loss, and the books' totals and the delta
  residual both stay at zero while two violations remain.
* **The gap is written out in measured terms** — notional scale, price basis,
  cost rule, refused signals, unaccounted rows — with the remainder reported
  rather than absorbed (``TestTheGapIsExplained``). The scale/price split is
  chosen so their sum equals the gross difference exactly, with no division and
  no rounding step to hide in, so the line can be added back up to the last
  digit and a wrong term cannot park itself in the residual.
* **The population is the one the replay worked over**, recovered from the
  ``(cutoff, as_of)`` pair the engine stamped on its own decisions instead of
  re-guessed from the wall clock (``TestTheScopeIsTheReplaysOwnWindow``). A run
  that stamped no window fails closed rather than reporting a clean empty
  reconciliation, and a portfolio holding two windows refuses to pick one.
* **A caller can actually ask for it** (``TestTheEndpointServesWhatTheBooksSay``).
  ``/journal`` and ``/learning`` were both audited as built-but-unreachable, so
  the report is read over HTTP here: the bytes served equal the bytes the books
  produced, an unowned run is indistinguishable from a non-existent one, and a
  run with no stamped window is refused rather than served as an empty
  reconciliation.

Every asserted number is derived by hand from the fixture's seeded rows
(``backend/tests/replay_fixture.py``: quantity 1000, zero study costs, capital
100 000) and the cost rule at the top of this file. Nothing is copied out of
the report being checked, and each hand-derived literal sits next to an
assertion on the input that produced it, so a fixture change turns these red
instead of quietly re-validating them.

Development lane: this pins the contracts on seeded data. It does not stand in
for the real-history acceptance run.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import json
import math
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.services.research_paper_reconciliation import (
    ScopeError,
    decompose_delta,
    reconcile_books,
    reconcile_research_book,
    resolve_scope,
    source_rows,
    terminal_cash,
)
from tests.replay_fixture import (
    INSTRUMENT,
    INTERVAL_MINUTES,
    RULE_ID,
    capture_statements,
    replay_case,  # noqa: F401  (fixture registered by import)
)

COMMISSION = Decimal("0.001")
SLIPPAGE = Decimal("0.0005")
COSTS = {"commission_rate": COMMISSION, "slippage_rate": SLIPPAGE}

# One long round trip: 10% of 100 000 capital at 50 000, so 0.2 units.
SIGNAL_EXIT = (0, 1, Decimal("50000"), Decimal("51000"))
# A win and a loss of equal size, so the study book's own totals come to zero.
WIN_AND_LOSS = [
    (0, 1, Decimal("50000"), Decimal("51000")),
    (5, 6, Decimal("50000"), Decimal("49000")),
]
# Four entries opened before any exit, against the default max_positions=3: the
# fourth is refused, the first three settle.
OVER_CAPACITY = [
    (1, 10, Decimal("50000"), Decimal("51000")),
    (2, 11, Decimal("50000"), Decimal("51000")),
    (3, 12, Decimal("50000"), Decimal("51000")),
    (4, 13, Decimal("50000"), Decimal("51000")),
]

# Hand-derived for SIGNAL_EXIT, never read back from the report.
#   study row:  1000 units x (51 000 - 50 000) = 1 000 000 gross, 0 cost.
#   replay:     10 000 notional / 50 000 = 0.2 units.
#               entry costs 10 000 x (0.001 + 0.0005) = 15
#               exit gross  0.2 x 51 000 = 10 200, costs 10.2 + 5.1 = 15.3
#               net = (10 200 - 10 000) - 15 - 15.3 = 169.7
#   scale = (0.2 - 1000) x 1 000        = -999 800
#   price = (1 000 - 1 000) x 0.2       = 0
#   cost  = 0 - 30.3                    = -30.3
#   total = 169.7 - 1 000 000           = -999 830.3 = scale + price + cost.
RESEARCH_GROSS = Decimal("1000000")
PAPER_GROSS = Decimal("200")
PAPER_COSTS = Decimal("30.3")
PAPER_NET = Decimal("169.7")
SCALE_TERM = Decimal("-999800")
COST_TERM = Decimal("-30.3")
TOTAL_TERM = Decimal("-999830.3")

# The identity names the books report, spelled once so a plant can require
# exactly the gate it is meant to open — and so renaming one breaks a named
# contract rather than a bare count.
GROSS_IDENTITY = "gross_pnl == quantity x (exit_price - entry_price)"
COST_SPLIT_IDENTITY = "total_cost == commission + slippage_cost"
NET_IDENTITY = "net_pnl == gross_pnl - total_cost"
AMOUNT_IDENTITY = "gross_amount == quantity x execution_price_before_costs"
EQUAL_QTY_IDENTITY = "both legs fill the same quantity"
ENTRY_CASH_IDENTITY = "entry net_cash_change == -(gross_amount + entry costs)"
EXIT_CASH_IDENTITY = "exit net_cash_change == gross_amount - exit costs"
TWO_LEGS_IDENTITY = "one BUY leg and one SELL leg per settled source row"
LONG_ONLY_IDENTITY = "the entry leg is the BUY (replay is long-only)"
TERMINAL_CASH_IDENTITY = "terminal cash == opening capital + booked net"


def _read(db, sql, **params):
    return db.execute(text(sql), params).fetchall()


def _stamp(value):
    """A timestamp read straight out of SQL, as a datetime.

    Raw ``text()`` selects skip the ORM's type processors, so SQLite hands back
    TEXT where PostgreSQL hands back a datetime. This helper is deliberately a
    second implementation rather than the module's own normaliser: the test
    below asserts the module's version agrees with it.
    """
    return value if hasattr(value, "year") else datetime.fromisoformat(str(value))


def _report(case):
    """One reconciliation of a replayed case, read from durable rows only."""
    db = SessionLocal()
    try:
        return reconcile_books(db, run_id=case.run_id, user_id=case.user_id)
    finally:
        db.close()


def _replayed_report(replay_case, trades, **replay_kwargs):
    return _report(_replayed(replay_case, trades, **replay_kwargs))


def _replayed(replay_case, trades, user_id=None, **replay_kwargs):
    case = replay_case(trades, user_id=user_id)
    case.replay(**COSTS, **replay_kwargs)
    return case


def _api_user(tag):
    """Register a caller the HTTP lane can authenticate, and return its header.

    ``get_current_user`` looks the id up through the ORM, and on the SQLite lane
    the ORM writes an id as 32-char hex where a raw ``text()`` INSERT writes the
    dashed form (``tests/uuid_identity.py``) — so a case seeded under the
    fixture's own raw-inserted user is a case no request can be made as. The
    users here are named by tag so a rerun of one node cannot borrow another's
    row: the unique email constraint makes a reused tag fail loudly.
    """
    from app.auth.jwt import create_access_token, get_password_hash
    from app.models.user import User
    from tests.conftest import TestSession

    db = TestSession()
    try:
        user = User(
            email=f"recon-seam-{tag}@alethra.io",
            password_hash=get_password_hash("testpass123"),
            display_name=f"recon_seam_{tag}",
            is_admin=False,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = str(user.id)
    finally:
        db.close()
    return {"Authorization": f"Bearer {create_access_token(data={'sub': user_id})}"}, user_id


def _execute(case, sql, **params):
    """Plant one defect in this case's own rows.

    Everything touched is keyed by the case's portfolio or study run, so the
    fixture's teardown removes the row, or reverts the value along with it.
    """
    db = SessionLocal()
    try:
        moved = db.execute(text(sql), params)
        db.commit()
        return moved
    finally:
        db.close()


def _leg(case, **filters):
    """The order/fill behind one stamped decision, for the plants to aim at."""
    db = SessionLocal()
    try:
        rows = _read(
            db,
            """
            SELECT dr.decision_type AS decision_type, po.side AS side,
                   po.id AS order_id, pf.id AS fill_id
            FROM paper_fills pf
            JOIN paper_orders po ON po.id = pf.paper_order_id
            JOIN decision_records dr ON dr.id = po.decision_id
            WHERE dr.paper_portfolio_id = :p
            """,
            p=case.portfolio_id,
        )
    finally:
        db.close()
    picked = [r for r in rows if all(getattr(r, k) == v for k, v in filters.items())]
    assert len(picked) == 1, f"expected one leg for {filters}, got {len(picked)}: {picked}"
    return picked[0]


def _names(book):
    return sorted(v["identity"] for v in book["violations"])


def _dec(record, key):
    return Decimal(str(record[key]))


def _component(delta, term):
    found = [c for c in delta["components"] if c["term"] == term]
    assert len(found) == 1, f"{term} appears {len(found)} times in the delta line"
    return found[0]


def _walk_json_safe(value, path="report"):
    """Fail on anything a JSON response could not carry out of the service."""
    if isinstance(value, Decimal):
        raise AssertionError(f"{path} leaks a Decimal: {value!r}")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, float):
            assert math.isfinite(value), f"{path} carries a non-finite float: {value!r}"
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f"{path} has a non-string key {key!r}"
            _walk_json_safe(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            _walk_json_safe(item, f"{path}[{idx}]")
        return
    raise AssertionError(f"{path} carries {type(value).__name__}, which is not JSON-safe")


class TestTheScopeIsTheReplaysOwnWindow:
    """The reconciliation compares the population the replay actually saw."""

    def test_the_window_is_read_back_from_the_decisions_not_re_guessed(self, replay_case):
        case = _replayed(replay_case, [SIGNAL_EXIT])

        db = SessionLocal()
        try:
            scope = resolve_scope(db, run_id=case.run_id, user_id=case.user_id)
            stamped = _read(
                db,
                "SELECT DISTINCT cutoff_timestamp, as_of_timestamp FROM decision_records "
                "WHERE paper_portfolio_id = :p AND source_trade_id IS NOT NULL",
                p=case.portfolio_id,
            )
            assert len(stamped) == 1, f"the run should have stamped one window: {stamped}"

            # The scope carries the run's own identity, not the caller's claim.
            assert scope.instrument == INSTRUMENT
            assert scope.interval_minutes == INTERVAL_MINUTES
            assert scope.rule_id == RULE_ID
            assert scope.initial_capital == Decimal("100000")
            # ...and its window is the pair already on those decisions, so the
            # rows below are the rows the replay was handed. The scope hands
            # back datetimes on both lanes — a caller that had to know which
            # lane wrote the row could not compare the two windows.
            assert isinstance(scope.cutoff, datetime), type(scope.cutoff)
            assert isinstance(scope.as_of, datetime), type(scope.as_of)
            assert (scope.cutoff, scope.as_of) == (
                _stamp(stamped[0][0]), _stamp(stamped[0][1])
            )

            rows = source_rows(db, scope)
            assert [str(r.source_trade_id) for r in rows] == case.trade_ids
            # Input fingerprints for every literal in this module.
            assert rows[0].quantity == Decimal("1000")
            assert rows[0].entry_price == Decimal("50000")
            assert rows[0].exit_price == Decimal("51000")
            assert rows[0].total_cost == Decimal("0")
            assert rows[0].net_pnl == RESEARCH_GROSS
            # Cash as the books left it, not as the engine remembered it.
            assert terminal_cash(db, scope) == Decimal("100000") + PAPER_NET
        finally:
            db.close()

    def test_rows_outside_the_window_are_out_of_both_books(self, replay_case):
        """One seeded row in, one seeded row out, on the same study run.

        The base time is 30 hours ago, so ``days_back=1`` cuts the horizon
        between the two entries. Both books must see exactly one row — if the
        reconciliation re-ran a wider predicate than the engine's, the extra
        row would land in the ``unaccounted`` bucket and be blamed on the
        engine instead of on this module's own scope.
        """
        inside = (20, 21, Decimal("50000"), Decimal("51000"))
        case = _replayed(replay_case, [SIGNAL_EXIT, inside], days_back=1)

        report = _report(case)
        assert report["scope"]["source_rows"] == 1
        assert [row_id for row_id in report["delta"]["per_row"]] == [case.trade_ids[1]]
        assert report["research_book"]["rows"] == 1
        assert report["verdict"] == "reconciled"

    def test_a_run_that_saw_no_rows_refuses_to_reconcile(self, replay_case):
        """A horizon with nothing in it is not a clean reconciliation.

        ``days_back=1`` puts the seeded row, which sits 30 hours back, outside
        the window, so the replay legitimately trades nothing and stamps no
        source-pinned decision. Reporting "reconciled" over an empty row set
        would be the same false green as reporting it over a run nobody scoped.
        """
        case = replay_case([SIGNAL_EXIT])
        result = case.replay(days_back=1, **COSTS)

        assert result["total_trades"] == 0
        assert result["counting"]["events_processed"] == 0
        assert case.trade_ids  # the row exists; it is simply out of range

        db = SessionLocal()
        try:
            with pytest.raises(ScopeError) as raised:
                reconcile_books(db, run_id=case.run_id, user_id=case.user_id)
        finally:
            db.close()
        assert "no source-pinned decision" in str(raised.value)

    def test_a_portfolio_with_two_windows_refuses_to_choose_one(self, replay_case):
        case = _replayed(replay_case, [SIGNAL_EXIT])

        db = SessionLocal()
        try:
            original = _read(
                db,
                "SELECT cutoff_timestamp FROM decision_records "
                "WHERE paper_portfolio_id = :p AND decision_type = 'entry'",
                p=case.portfolio_id,
            )[0][0]
        finally:
            db.close()
        _execute(
            case,
            "UPDATE decision_records SET cutoff_timestamp = :moved "
            "WHERE paper_portfolio_id = :p AND decision_type = 'exit'",
            moved=_stamp(original) + timedelta(days=1), p=case.portfolio_id,
        )

        db = SessionLocal()
        try:
            with pytest.raises(ScopeError) as raised:
                reconcile_books(db, run_id=case.run_id, user_id=case.user_id)
        finally:
            db.close()
        assert "2 distinct" in str(raised.value)

    def test_another_users_run_is_not_reconcilable(self, replay_case):
        mine = _replayed(replay_case, [SIGNAL_EXIT])
        other = replay_case([SIGNAL_EXIT])

        db = SessionLocal()
        try:
            with pytest.raises(ScopeError) as raised:
                reconcile_books(db, run_id=mine.run_id, user_id=other.user_id)
            # Same guard on the scope reader alone, not only on the whole report.
            with pytest.raises(ScopeError):
                resolve_scope(db, run_id=mine.run_id, user_id=other.user_id)
        finally:
            db.close()
        assert "belongs to portfolio owner" in str(raised.value)


class TestEachBookAgreesWithItself:
    """Each book is checked against its own columns, without the other book."""

    def test_the_seeded_study_rows_pass_their_own_identities(self, replay_case):
        case = _replayed(replay_case, [SIGNAL_EXIT])

        db = SessionLocal()
        try:
            scope = resolve_scope(db, run_id=case.run_id, user_id=case.user_id)
            book = reconcile_research_book(source_rows(db, scope))
        finally:
            db.close()

        assert book["status"] == "reconciled"
        assert book["rows"] == 1
        assert book["directions"] == {"long": 1}
        assert book["violations"] == []
        assert book["violation_count"] == 0
        # The fixture seeds zero costs, so gross and net are the same number —
        # a property of these rows, not of the check.
        assert _dec(book, "gross_pnl") == RESEARCH_GROSS
        assert _dec(book, "total_cost") == Decimal("0")
        assert _dec(book, "net_pnl") == RESEARCH_GROSS
        assert _dec(book, "net_pnl_exact") == RESEARCH_GROSS

    def test_a_short_study_row_is_signed_by_its_own_direction(self):
        """The short branch of the study identities, on the row's own terms.

        The replay itself refuses shorts, so no replay can reach this branch;
        the study book still has to read a short row's gross the way the study
        wrote it, or every short row would report a violation.
        """
        long_row = SimpleNamespace(
            source_trade_id="long-1", direction="long", quantity=Decimal("1000"),
            entry_price=Decimal("50000"), exit_price=Decimal("51000"),
            gross_pnl=Decimal("1000000"), commission=Decimal("0"),
            slippage_cost=Decimal("0"), total_cost=Decimal("0"),
            net_pnl=Decimal("1000000"),
        )
        short_row = SimpleNamespace(
            source_trade_id="short-1", direction="short", quantity=Decimal("1000"),
            entry_price=Decimal("50000"), exit_price=Decimal("49000"),
            gross_pnl=Decimal("2000000"), commission=Decimal("0"),
            slippage_cost=Decimal("0"), total_cost=Decimal("0"),
            net_pnl=Decimal("2000000"),
        )

        book = reconcile_research_book([long_row, short_row])
        assert book["violation_count"] == 1
        assert _names(book) == [GROSS_IDENTITY]
        assert book["violations"][0]["source_trade_id"] == "short-1"

        # The same row signed the way a short actually settles — profit when
        # the price falls, which is the convention the study writer uses —
        # passes.
        short_row.gross_pnl = Decimal("1000000")
        short_row.net_pnl = Decimal("1000000")
        assert reconcile_research_book([short_row])["status"] == "reconciled"

    def test_a_study_cost_that_does_not_split_is_one_named_violation(self, replay_case):
        """Break only the cost split, and only the cost split reports it.

        ``total_cost`` and ``net_pnl`` move together, so net still equals gross
        less cost; the row's two cost components are what stop adding up.
        """
        case = _replayed(replay_case, [SIGNAL_EXIT])
        _execute(
            case,
            "UPDATE strategy_study_trades SET total_cost = 5, net_pnl = gross_pnl - 5 "
            "WHERE run_id = :r",
            r=case.study_run_id,
        )

        report = _report(case)
        book = report["research_book"]
        assert _names(book) == [COST_SPLIT_IDENTITY]
        assert _dec(book, "violation_residual") == Decimal("5")
        assert book["status"] == "does_not_reconcile"
        # The same 5 reappears as a named term instead of an unexplained gap:
        # the study now charges 5 where the replay charges 30.3, so cost_rule is
        # 5 - 30.3 rather than -30.3, and the residual stays exactly 0.
        assert _dec(report["delta"]["per_row"][case.trade_ids[0]], "cost_rule") == Decimal("5") - PAPER_COSTS
        assert _dec(report["delta"], "residual") == Decimal("0")
        assert report["verdict"] == "does_not_reconcile"

    def test_a_study_row_whose_net_is_not_gross_less_cost_is_named(self, replay_case):
        case = _replayed(replay_case, [SIGNAL_EXIT])
        _execute(
            case, "UPDATE strategy_study_trades SET net_pnl = net_pnl + 7 WHERE run_id = :r",
            r=case.study_run_id,
        )

        report = _report(case)
        book = report["research_book"]
        assert _names(book) == [NET_IDENTITY]
        assert _dec(book, "violation_residual") == Decimal("7")
        # 7 of research net is neither scale, price nor cost, so it is exactly
        # what the residual is for: reported, not absorbed.
        assert _dec(report["delta"], "residual") == Decimal("-7")
        assert report["delta"]["buckets_disagree"] is False
        assert report["verdict"] == "does_not_reconcile"

    def test_a_compensating_pair_is_two_violations_even_though_its_residual_cancels(
        self, replay_case
    ):
        """Why a book reports a count as well as a residual.

        A win and a loss, gross pushed +5 on one row and -5 on the other. The
        book's totals are unchanged, the two rows' residuals cancel, and only
        the per-row check sees either one.
        """
        case = _replayed(replay_case, WIN_AND_LOSS)
        _execute(
            case,
            "UPDATE strategy_study_trades SET gross_pnl = gross_pnl + 5, "
            "net_pnl = net_pnl + 5 WHERE id = :id",
            id=case.trade_ids[0],
        )
        _execute(
            case,
            "UPDATE strategy_study_trades SET gross_pnl = gross_pnl - 5, "
            "net_pnl = net_pnl - 5 WHERE id = :id",
            id=case.trade_ids[1],
        )

        report = _report(case)
        book = report["research_book"]
        assert book["violation_count"] == 2
        assert _names(book) == [GROSS_IDENTITY, GROSS_IDENTITY]
        assert _dec(book, "violation_residual") == Decimal("0")
        assert _dec(book, "gross_pnl") == Decimal("0"), "the totals cannot see it"
        assert _dec(report["delta"], "residual") == Decimal("0")
        assert report["verdict"] == "does_not_reconcile"

    def test_the_clean_paper_book_hands_reproduces_from_the_seeded_row(
        self, replay_case
    ):
        report = _replayed_report(replay_case, [SIGNAL_EXIT])
        book = report["paper_book"]

        assert book["status"] == "reconciled"
        assert book["violations"] == []
        assert book["settled_round_trips"] == 1
        assert _dec(book, "gross_pnl") == PAPER_GROSS
        assert _dec(book, "total_cost") == PAPER_COSTS
        assert _dec(book, "net_pnl") == PAPER_NET
        assert _dec(book, "net_pnl_exact") == PAPER_NET
        assert _dec(book, "opening_capital") == Decimal("100000")
        assert _dec(book, "cash_available") == Decimal("100000") + PAPER_NET
        identity = book["cash_identity"]
        assert _dec(identity, "expected") == Decimal("100000") + PAPER_NET
        assert _dec(identity, "difference") == Decimal("0")
        assert report["verdict"] == "reconciled"

    @pytest.mark.parametrize(
        "plant,expected,arithmetic_still_closes",
        [
            ("entry_cash", [ENTRY_CASH_IDENTITY], True),
            ("exit_cash", [EXIT_CASH_IDENTITY], True),
            ("exit_quantity", sorted([AMOUNT_IDENTITY, EQUAL_QTY_IDENTITY]), True),
            ("terminal_cash", [TERMINAL_CASH_IDENTITY], True),
            ("entry_side", sorted([TWO_LEGS_IDENTITY, TERMINAL_CASH_IDENTITY]), False),
            ("flipped_pair", sorted([LONG_ONLY_IDENTITY, TERMINAL_CASH_IDENTITY]), False),
        ],
        ids=["entry_cash", "exit_cash", "exit_quantity", "terminal_cash",
             "entry_side", "flipped_pair"],
    )
    def test_each_plant_opens_exactly_its_own_gate(
        self, replay_case, plant, expected, arithmetic_still_closes
    ):
        """One defect at a time, and the set of gates it opens is fixed.

        ``flipped_pair`` is the case the long-only gate exists for: the legs
        still pair off as one BUY and one SELL, so a reader who took the side
        as the marker would sign the trip the wrong way and reconcile. Naming
        the entry leg by its own decision, instead, turns it into a violation
        and keeps the trip out of the sums.
        """
        case = _replayed(replay_case, [SIGNAL_EXIT])
        entry = _leg(case, decision_type="entry")
        exit_ = _leg(case, decision_type="exit")

        if plant == "entry_cash":
            _execute(case, "UPDATE paper_fills SET net_cash_change = net_cash_change + 1 "
                           "WHERE id = :id", id=entry.fill_id)
        elif plant == "exit_cash":
            _execute(case, "UPDATE paper_fills SET net_cash_change = -net_cash_change "
                           "WHERE id = :id", id=exit_.fill_id)
        elif plant == "exit_quantity":
            _execute(case, "UPDATE paper_fills SET quantity = 0.3 WHERE id = :id",
                     id=exit_.fill_id)
        elif plant == "terminal_cash":
            _execute(case, "UPDATE paper_portfolios SET current_virtual_cash = "
                           "current_virtual_cash + 7 WHERE id = :p", p=case.portfolio_id)
        elif plant == "entry_side":
            _execute(case, "UPDATE paper_orders SET side = 'SELL' WHERE id = :id",
                     id=entry.order_id)
        elif plant == "flipped_pair":
            _execute(case, "UPDATE paper_orders SET side = 'SELL' WHERE id = :id",
                     id=entry.order_id)
            _execute(case, "UPDATE paper_orders SET side = 'BUY' WHERE id = :id",
                     id=exit_.order_id)

        report = _report(case)
        book = report["paper_book"]
        assert _names(book) == expected
        assert book["status"] == "does_not_reconcile"
        assert report["verdict"] == "does_not_reconcile"
        assert (_dec(report["delta"], "residual") == Decimal("0")) is arithmetic_still_closes


class TestTheGapIsExplained:
    """The delta is a measured line, addable by hand, with its remainder shown."""

    def test_the_single_round_trip_gap_is_scale_and_cost_only(self, replay_case):
        case = _replayed(replay_case, [SIGNAL_EXIT])
        delta = _report(case)["delta"]
        row = delta["per_row"][case.trade_ids[0]]

        assert _dec(delta, "research_net_pnl") == RESEARCH_GROSS
        assert _dec(delta, "paper_net_pnl") == PAPER_NET
        assert _dec(delta, "total_delta") == TOTAL_TERM
        assert _dec(_component(delta, "notional_scale"), "amount") == SCALE_TERM
        assert _dec(_component(delta, "price_basis"), "amount") == Decimal("0")
        assert _dec(_component(delta, "cost_rule"), "amount") == COST_TERM
        assert _dec(_component(delta, "refused_entries"), "amount") == Decimal("0")
        assert _dec(_component(delta, "unaccounted_rows"), "amount") == Decimal("0")
        assert _dec(delta, "named_total") == TOTAL_TERM
        assert _dec(delta, "residual") == Decimal("0")
        assert delta["entered"] == 1 and delta["refused"] == 0
        assert delta["unaccounted_source_trade_ids"] == []
        assert delta["orphan_fill_source_trade_ids"] == []
        assert delta["buckets_disagree"] is False
        assert _report(case)["verdict"] == "reconciled"

        # The per-row line is the same three terms, so the totals cannot be
        # right by cancelling each other out.
        assert _dec(row, "notional_scale") == SCALE_TERM
        assert _dec(row, "price_basis") == Decimal("0")
        assert _dec(row, "cost_rule") == COST_TERM
        assert _dec(row, "residual") == Decimal("0")
        assert _dec(row, "research_quantity") == Decimal("1000")
        assert _dec(row, "paper_quantity") == Decimal("0.2")
        assert _dec(row, "research_net_pnl") == RESEARCH_GROSS
        assert _dec(row, "paper_net_pnl") == PAPER_NET

    def test_the_delta_line_adds_back_exactly_and_both_shapes_agree(self, replay_case):
        """The printed components reproduce the printed total to the last digit.

        Checked in ``Decimal`` on ``*_exact`` rather than on the floats, so a
        term dropped from the list, or a float and its exact string drifting
        apart, is a failure and not a rounding argument.
        """
        report = _replayed_report(replay_case, OVER_CAPACITY)
        delta = report["delta"]

        amounts = [Decimal(c["amount_exact"]) for c in delta["components"]]
        assert sum(amounts) == Decimal(delta["named_total_exact"])
        assert Decimal(delta["named_total_exact"]) + Decimal(delta["residual_exact"]) \
            == Decimal(delta["total_delta_exact"])
        for component in delta["components"]:
            assert component["amount"] == float(component["amount_exact"])
        for term in delta["per_row"].values():
            assert Decimal(term["residual"]) == Decimal("0")

    def test_price_basis_is_zero_because_the_replay_buys_the_studys_prices(
        self, replay_case
    ):
        """A live invariant on this engine, not an assumption about engines.

        The replay books the study's own entry and exit prices, so the paper
        book's per-unit move is the study's and the price term is exactly 0 —
        on one trade, on a win-and-loss pair, and on a three-position run where
        the sizes differ from each other. If a mark source is ever introduced
        between the two, this term is where the difference appears first.
        """
        for trades in ([SIGNAL_EXIT], WIN_AND_LOSS, OVER_CAPACITY):
            delta = _replayed_report(replay_case, trades)["delta"]
            assert _dec(_component(delta, "price_basis"), "amount") == Decimal("0")
            assert [Decimal(row["price_basis"]) for row in delta["per_row"].values()] \
                == [Decimal("0")] * len(delta["per_row"])

    def test_a_drifted_price_moves_price_basis_and_still_adds_up(self):
        """The price term is reachable, and the algebra survives it.

        No input in the replay lane can produce a paper price that differs from
        the study's, so the branch is exercised here on the function's own
        terms: a 5-per-unit drift at 0.2 units is 1.00 of price basis, the
        scale and cost terms are unchanged, and the residual is still exactly 0
        — which is the point of dividing scale from price.
        """
        row = SimpleNamespace(
            source_trade_id="row-1", direction="long", quantity=Decimal("1000"),
            entry_price=Decimal("50000"), exit_price=Decimal("51000"),
            total_cost=Decimal("0"), net_pnl=Decimal("1000000"),
        )
        trip = {
            "legs": 2, "sides": ["BUY", "SELL"],
            "quantity": Decimal("0.2"), "move": Decimal("1005"),
            "costs": PAPER_COSTS,
            "gross_pnl": Decimal("0.2") * Decimal("1005"),
            "net_pnl": Decimal("0.2") * Decimal("1005") - PAPER_COSTS,
        }
        paper_net = trip["net_pnl"]
        delta = decompose_delta(
            {"net_pnl": float(Decimal("1000000")), "net_pnl_exact": "1000000"},
            {"net_pnl": float(paper_net), "net_pnl_exact": str(paper_net)},
            [row], {"row-1": trip}, {},
        )

        assert _dec(_component(delta, "notional_scale"), "amount") == SCALE_TERM
        assert _dec(_component(delta, "price_basis"), "amount") == Decimal("1.0")
        assert _dec(_component(delta, "cost_rule"), "amount") == COST_TERM
        assert Decimal(delta["total_delta_exact"]) == (
            Decimal(delta["named_total_exact"]) + Decimal(delta["residual_exact"])
        )
        assert _dec(delta, "residual") == Decimal("0")

    def test_a_refused_signal_is_subtracted_exactly_once(self, replay_case):
        report = _replayed_report(replay_case, OVER_CAPACITY)
        delta = report["delta"]

        assert delta["entered"] == 3
        assert delta["refused"] == 1
        assert delta["refusal_reasons"] == {"max_positions": 1}
        refused = _component(delta, "refused_entries")
        assert refused["count"] == 1
        # The declined row is a real study row, so it is still in the research
        # total; taking its research net off once is what makes the two sides
        # of the gap describe one population.
        assert Decimal(refused["amount_exact"]) == -RESEARCH_GROSS
        assert delta["double_bucketed_source_trade_ids"] == []
        assert delta["unaccounted_source_trade_ids"] == []
        assert _dec(delta, "residual") == Decimal("0")
        assert delta["buckets_disagree"] is False
        assert report["verdict"] == "reconciled"
        # Three concurrent positions size off a falling equity, so their
        # quantities are not hand-derivable here; the line closing on them is
        # the claim, and the scale term is what carries the difference.
        assert _dec(_component(delta, "notional_scale"), "amount") != SCALE_TERM

    def test_a_row_that_traded_neither_nor_was_refused_is_a_coverage_failure(
        self, replay_case
    ):
        """The gap closes anyway, which is why the bucket is its own gate.

        Deleting the exit fill leaves one leg: no complete round trip, no
        refusal, and a research row still in the window. The arithmetic sums to
        zero because the unaccounted term is subtracted, so a verdict that
        looked only at the residual would call this reconciled.
        """
        case = _replayed(replay_case, [SIGNAL_EXIT])
        exit_ = _leg(case, decision_type="exit")
        _execute(case, "DELETE FROM paper_fills WHERE id = :id", id=exit_.fill_id)

        report = _report(case)
        delta = report["delta"]
        assert delta["entered"] == 0
        assert delta["refused"] == 0
        assert delta["unaccounted_source_trade_ids"] == [case.trade_ids[0]]
        assert Decimal(_component(delta, "unaccounted_rows")["amount_exact"]) == -RESEARCH_GROSS
        assert _dec(delta, "residual") == Decimal("0")
        assert delta["residual_within_tolerance"] is True
        assert _names(report["paper_book"]) == sorted(
            [TWO_LEGS_IDENTITY, TERMINAL_CASH_IDENTITY]
        )
        assert report["verdict"] == "does_not_reconcile"

    def test_a_fill_attached_to_a_row_outside_scope_separates_the_two_computations(
        self, replay_case
    ):
        """The cross-check's whole purpose, on one planted row.

        Re-keying both legs onto an id the research window does not contain
        leaves the fills internally consistent — both books agree with
        themselves — while the paper book now carries 169.7 that no research
        row explains. The original row is meanwhile unaccounted. The gap-side
        residual sees the 169.7 and the per-row residual, which never reaches
        an orphan, stays at 0, so the two disagree and the verdict says so
        instead of reporting a clean pair of books.
        """
        case = _replayed(replay_case, [SIGNAL_EXIT])
        ghost = str(uuid4())
        moved = _execute(
            case,
            "UPDATE decision_records SET source_trade_id = :ghost "
            "WHERE paper_portfolio_id = :p AND source_trade_id = :orig",
            ghost=ghost, p=case.portfolio_id, orig=case.trade_ids[0],
        )
        assert moved.rowcount == 2, "both legs' decisions should have re-keyed"

        report = _report(case)
        delta = report["delta"]
        assert delta["orphan_fill_source_trade_ids"] == [ghost]
        assert delta["unaccounted_source_trade_ids"] == [case.trade_ids[0]]
        assert report["paper_book"]["violations"] == []
        assert report["research_book"]["status"] == "reconciled"
        assert _dec(delta, "residual") == PAPER_NET
        assert _dec(delta, "per_row_residual") == Decimal("0")
        assert delta["buckets_disagree"] is True
        assert report["verdict"] == "does_not_reconcile"


class TestTheReportTravels:
    """The report is a reader-facing artifact: safe, silent, and repeatable."""

    def test_the_report_is_json_safe_and_the_walker_can_fire(self, replay_case):
        case = _replayed(replay_case, OVER_CAPACITY)
        report = _report(case)

        _walk_json_safe(report)
        encoded = json.dumps(report)
        assert json.loads(encoded)["verdict"] == report["verdict"]

        # Positive control: the walker is not a pass-through.
        with pytest.raises(AssertionError):
            _walk_json_safe({"leak": Decimal("1")})
        with pytest.raises(AssertionError):
            _walk_json_safe({"leak": object()})

    def test_reconciling_emits_no_write_and_the_lane_can_see_one(self, replay_case):
        case = replay_case([SIGNAL_EXIT])
        case.replay(**COSTS)

        db = SessionLocal()
        try:
            with capture_statements() as calls:
                reconcile_books(db, run_id=case.run_id, user_id=case.user_id)
        finally:
            db.close()

        verbs = [c.statement.strip().split(None, 1)[0].lower() for c in calls]
        writes = sorted({v for v in verbs if v in ("insert", "update", "delete", "drop")})
        assert writes == [], f"reconciliation emitted {writes}: {verbs}"
        # Reads have to be visible too, or "no writes" is an empty lane.
        assert verbs.count("select") >= 5, f"expected the book reads to be recorded: {verbs}"

        # Positive control: the same listener sees a write when one happens, so
        # the empty list above is a measurement and not a blind lane.
        other = replay_case([SIGNAL_EXIT])
        with capture_statements() as loud:
            other.replay(**COSTS)
        loud_verbs = [c.statement.strip().split(None, 1)[0].lower() for c in loud]
        assert "insert" in loud_verbs

    def test_reconciling_twice_gives_the_same_report(self, replay_case):
        """No wall clock, no state left behind: the report is a read.

        The cutoff is the one the run stamped, so a second reconciliation a
        moment later — or a day later — has to produce the same row set and the
        same numbers.
        """
        case = _replayed(replay_case, OVER_CAPACITY)
        first = _report(case)
        second = _report(case)

        assert first == second
        assert first["verdict"] == "reconciled"


class TestTheEndpointServesWhatTheBooksSay:
    """A module no caller can reach is a report that was never filed.

    ``/journal`` and ``/learning`` were both audited as built-but-unreachable,
    so the reconciliation is checked at its seam: the bytes a reader gets are
    the bytes the books produced, an unowned run reads exactly like a
    non-existent one, and a run that cannot be scoped is refused rather than
    reported as an empty reconciliation.
    """

    def test_the_http_body_equals_the_service_report(self, client, replay_case):
        headers, user_id = _api_user("body")
        case = _replayed(replay_case, OVER_CAPACITY, user_id=user_id)
        service = _report(case)

        response = client.get(
            f"/api/v1/paper-trading/reconciliation/{case.run_id}", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json() == service, "the seam changed the report it forwarded"
        assert response.json()["verdict"] == "reconciled"
        # The component list is the answer, so its presence at the seam is
        # checked by name and not merely by a non-empty body.
        assert [c["term"] for c in response.json()["delta"]["components"]] == [
            "notional_scale", "price_basis", "cost_rule",
            "refused_entries", "unaccounted_rows",
        ]

    def test_the_seam_declares_no_partial_response_model(self, client):
        """The equality above holds because nothing is filtered on the way out.

        A ``response_model`` naming a subset of these keys would drop the
        components and the per-row terms while still returning 200, so the
        absence of a model is itself the contract being pinned.
        """
        from app.main import app

        matches = [
            r for r in app.routes
            if getattr(r, "path", "") == "/api/v1/paper-trading/reconciliation/{run_id}"
        ]
        assert len(matches) == 1, [getattr(r, "path", "") for r in app.routes][:5]
        assert matches[0].response_model is None

    def test_an_unowned_run_reads_exactly_like_no_run_at_all(self, client, replay_case):
        owner_headers, owner_id = _api_user("owner")
        stranger_headers, _ = _api_user("stranger")
        mine = _replayed(replay_case, [SIGNAL_EXIT], user_id=owner_id)

        not_mine = client.get(
            f"/api/v1/paper-trading/reconciliation/{mine.run_id}", headers=stranger_headers
        )
        never_existed = client.get(
            f"/api/v1/paper-trading/reconciliation/{uuid4()}", headers=stranger_headers
        )

        assert not_mine.status_code == 404, not_mine.text
        assert never_existed.status_code == 404, never_existed.text
        # Same status is not the same answer: the bodies have to match too, or
        # one field's length tells a caller which run ids are real.
        assert not_mine.json() == never_existed.json()
        assert not_mine.json()["detail"]["reason"] == "not_found"

        # Positive control, or the two 404s above are one 404 measured twice.
        owned = client.get(
            f"/api/v1/paper-trading/reconciliation/{mine.run_id}", headers=owner_headers
        )
        assert owned.status_code == 200, owned.text
        assert owned.json()["verdict"] == "reconciled"

    def test_a_run_that_stamped_no_window_is_refused_not_emptied(self, client, replay_case):
        """No replay, no stamped scope — and so no honest population to report.

        An empty reconciliation reads as "the books agree, nothing to explain",
        which is the most misleading thing this endpoint could say. It answers
        409 with the reason instead.
        """
        headers, user_id = _api_user("unscoped")
        case = replay_case([], user_id=user_id)

        response = client.get(
            f"/api/v1/paper-trading/reconciliation/{case.run_id}", headers=headers
        )

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["reason"] == "scope_refused"
        assert "no source-pinned decision" in detail["message"]
