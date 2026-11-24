"""Reconcile the research book against the paper book, and account for the gap.

Two books raise two separate questions.

**Does each book agree with itself?** A book that agrees with the other book
but not with its own columns is not reconciled — it is two mistakes that happen
to point the same way. So the study rows are checked against the identities the
study owes (gross against the price move it records, total cost against its two
components, net against gross less cost), and the replay's fills are checked
against the identities the replay owes (fill amount against quantity times
price, cash change against amount less costs, one buy leg against one sell leg
at the same quantity, terminal cash against opening capital plus booked net).
Neither check borrows a number from the other book.

**Why do the two books differ?** They do differ, and "they are two books" is not
an explanation. Every source row in scope lands in exactly one of three buckets
— entered, refused, or accounted for by neither — and the gap is written out in
measured terms:

    paper_net - research_net =
        sum_entered (notional scale)
      + sum_entered (price basis)
      + sum_entered (research costs - paper costs)
      - sum_refused  research_net
      - sum_unaccounted research_net

The first term is "we bought a different size", the second "we bought at a
different price", and they are kept apart because they are different defects:
folded together, a sizing bug and a stale-price bug hide each other. Writing
scale as ``(paper_qty - research_qty) x research_move`` and price basis as
``(paper_move - research_move) x paper_qty`` makes their sum equal the gross
difference exactly, with no division and no rounding step to absorb error.

What is left after the named terms is ``residual``, and it is reported rather
than folded into the nearest term. A future engine that books a round trip a
different way would otherwise move the gap without naming the cause; the
residual catches that, and the decomposition stays a measurement instead of
becoming a place to park numbers.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import text
from sqlalchemy.orm import Session


TOLERANCE = Decimal("0.000001")


def _dec(value: Any) -> Decimal:
    """Read one stored amount.

    The same logical column arrives as ``Decimal``, ``float`` or ``str``
    depending on dialect and column type, and ``None`` where a row records no
    amount. Converting through ``str`` rather than ``float`` keeps a Decimal
    column's exact value instead of introducing binary error.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class ScopeError(Exception):
    """The reconciliation cannot say which rows the replay was working over."""


def _ts(value: Any):
    """One stored timestamp, as a ``datetime``, whichever lane wrote it.

    These columns are read through plain SQL rather than the ORM, so nothing
    converts them for us: PostgreSQL hands back a ``datetime`` and SQLite hands
    back the text it stores. Parsing on the way in and binding a ``datetime``
    on the way out is what keeps the window predicate the engine itself used
    identical on both — re-binding the raw text would compare an engine-stamped
    cutoff against a study row whose own format carries a timezone suffix.
    """
    if value is None or hasattr(value, "year"):
        return value
    return datetime.fromisoformat(str(value))


class RunScope(NamedTuple):
    run_id: str
    portfolio_id: str
    owner_id: str
    instrument: str
    interval_minutes: int
    rule_id: str
    initial_capital: Decimal
    cutoff: Any
    as_of: Any


def resolve_scope(db: Session, *, run_id: str, user_id: str) -> RunScope:
    """Bind the reconciliation to one run and the one (cutoff, as_of) pair it used.

    The replay stamps its own query window onto every decision it writes, so the
    window is recovered from the run's records rather than re-guessed from
    wall-clock — re-guessing would reconcile against a row set the replay never
    saw. More than one distinct pair means the portfolio was replayed under two
    windows and there is no honest single answer, so this fails closed instead of
    picking one.
    """
    run = db.execute(
        text("""
            SELECT r.portfolio_id, r.instrument_id, r.interval_minutes,
                   r.skill, r.initial_capital, p.owner_id
            FROM paper_trading_runs r
            JOIN paper_portfolios p ON p.id = r.portfolio_id
            WHERE r.id = :id
        """),
        {"id": run_id},
    ).fetchone()
    if run is None:
        raise ScopeError(f"no paper trading run {run_id}")
    if str(run.owner_id) != str(user_id):
        raise ScopeError(
            f"run {run_id} belongs to portfolio owner {run.owner_id}, not {user_id}"
        )

    bounds = db.execute(
        text("""
            SELECT DISTINCT cutoff_timestamp, as_of_timestamp
            FROM decision_records
            WHERE paper_portfolio_id = :p AND source_trade_id IS NOT NULL
        """),
        {"p": run.portfolio_id},
    ).fetchall()
    if not bounds:
        raise ScopeError(
            f"run {run_id} recorded no source-pinned decision, so its query window "
            "is unknown; there is nothing to reconcile a paper book against"
        )
    if len(bounds) > 1:
        raise ScopeError(
            f"run {run_id} has {len(bounds)} distinct (cutoff, as_of) windows across "
            "its decisions; reconciling one of them would silently exclude rows "
            "reached under the others"
        )
    return RunScope(
        run_id=run_id,
        portfolio_id=str(run.portfolio_id),
        owner_id=str(run.owner_id),
        instrument=run.instrument_id,
        interval_minutes=run.interval_minutes,
        rule_id=run.skill,
        initial_capital=_dec(run.initial_capital),
        cutoff=_ts(bounds[0].cutoff_timestamp),
        as_of=_ts(bounds[0].as_of_timestamp),
    )


def source_rows(db: Session, scope: RunScope) -> list:
    """The research rows the replay's own WHERE clause selects, re-run verbatim.

    Same predicates, same join: a reconciliation scoped differently from the
    engine compares two different populations and then reports a delta nobody
    caused.
    """
    return db.execute(
        text("""
            SELECT
                t.id AS source_trade_id,
                t.direction,
                t.entry_price,
                t.exit_price,
                t.quantity,
                t.gross_pnl,
                t.commission,
                t.slippage_cost,
                t.total_cost,
                t.net_pnl
            FROM strategy_study_trades t
            JOIN strategy_study_runs r ON t.run_id = r.id
            WHERE r.instrument_id = :instrument
              AND r.interval_minutes = :interval
              AND t.rule_id = :rule_id
              AND r.user_id = :user_id
              AND t.entry_time >= :cutoff
              AND t.exit_time <= :as_of
            ORDER BY t.entry_time ASC
        """),
        {
            "instrument": scope.instrument,
            "interval": scope.interval_minutes,
            "rule_id": scope.rule_id,
            "user_id": scope.owner_id,
            "cutoff": scope.cutoff,
            "as_of": scope.as_of,
        },
    ).fetchall()


def _violation(name: str, actual: Decimal, expected: Decimal, **detail) -> dict | None:
    """A record when two amounts that must agree do not, else None."""
    if abs(actual - expected) <= TOLERANCE:
        return None
    return {"identity": name, "actual": str(actual), "expected": str(expected),
            "difference": str(actual - expected), **detail}


def reconcile_research_book(rows: list) -> dict:
    """Check the study rows against their own columns, without the paper book."""
    violations: list[dict] = []
    residuals: list[Decimal] = []
    gross_sum = cost_sum = net_sum = Decimal("0")
    directions: dict[str, int] = {}

    for row in rows:
        quantity = _dec(row.quantity)
        move = _dec(row.exit_price) - _dec(row.entry_price)
        if (row.direction or "").lower() == "short":
            move = -move
        directions[row.direction] = directions.get(row.direction, 0) + 1

        gross = _dec(row.gross_pnl)
        total_cost = _dec(row.total_cost)
        net = _dec(row.net_pnl)
        components = _dec(row.commission) + _dec(row.slippage_cost)
        where = {"source_trade_id": str(row.source_trade_id)}

        for found in (
            _violation("gross_pnl == quantity x (exit_price - entry_price)",
                       gross, quantity * move, **where),
            _violation("total_cost == commission + slippage_cost",
                       total_cost, components, **where),
            _violation("net_pnl == gross_pnl - total_cost",
                       net, gross - total_cost, **where),
        ):
            if found:
                violations.append(found)
                residuals.append(Decimal(found["difference"]))

        gross_sum += gross
        cost_sum += total_cost
        net_sum += net

    return {
        "rows": len(rows),
        "directions": directions,
        "gross_pnl": float(gross_sum),
        "total_cost": float(cost_sum),
        "net_pnl": float(net_sum),
        # The same total in the book's own exact arithmetic. ``net_pnl`` above
        # is for display and has already been through ``float``; a delta added
        # to it would inherit that rounding and could miss a real gap of the
        # same size, so the decomposition reads this instead.
        "net_pnl_exact": str(net_sum),
        # Summed rather than averaged, and never the difference of the two
        # sums: two rows whose errors cancel still leave two violations here.
        "violation_residual": float(sum(residuals, Decimal("0"))),
        "violation_count": len(violations),
        "violations": violations,
        "status": "reconciled" if not violations else "does_not_reconcile",
    }


def _legs_by_source(db: Session, scope: RunScope) -> dict[str, list]:
    """Every fill the replay booked for a source row, grouped by that row."""
    legs = db.execute(
        text("""
            SELECT
                dr.source_trade_id,
                dr.decision_type,
                po.side,
                pf.quantity,
                pf.gross_amount,
                pf.net_cash_change,
                pf.commission_amount,
                pf.slippage_amount,
                pf.execution_price_before_costs
            FROM paper_fills pf
            JOIN paper_orders po ON po.id = pf.paper_order_id
            JOIN decision_records dr ON dr.id = po.decision_id
            WHERE dr.paper_portfolio_id = :portfolio
              AND dr.cutoff_timestamp = :cutoff
              AND dr.as_of_timestamp = :as_of
              AND dr.source_trade_id IS NOT NULL
        """),
        {
            "portfolio": scope.portfolio_id,
            "cutoff": scope.cutoff,
            "as_of": scope.as_of,
        },
    ).fetchall()
    by_source: dict[str, list] = {}
    for leg in legs:
        by_source.setdefault(str(leg.source_trade_id), []).append(leg)
    return by_source


def _refusals(db: Session, scope: RunScope) -> dict[str, str]:
    """Refused source rows and their reason, from the durable refusal record."""
    rows = db.execute(
        text("""
            SELECT source_trade_id, risk_flags
            FROM decision_records
            WHERE paper_portfolio_id = :portfolio
              AND decision_type = 'no_action'
              AND cutoff_timestamp = :cutoff
              AND as_of_timestamp = :as_of
              AND source_trade_id IS NOT NULL
        """),
        {
            "portfolio": scope.portfolio_id,
            "cutoff": scope.cutoff,
            "as_of": scope.as_of,
        },
    ).fetchall()
    reasons: dict[str, str] = {}
    for row in rows:
        flags = row.risk_flags
        if isinstance(flags, str):
            flags = json.loads(flags)
        reasons[str(row.source_trade_id)] = (flags or {}).get("refusal_reason", "unrecorded")
    return reasons


def _round_trip(legs: list) -> dict:
    """Collapse one source row's fill legs into the paper book's round trip.

    The legs are told apart by the decision that caused them, not by their
    side: ``entry``/``exit`` is what the replay stamps, and a BUY/SELL pair
    looks the same whichever way the position went. The replay is long-only,
    so the entry leg is expected to be the BUY — and that expectation is
    checked in :func:`reconcile_paper_book` rather than assumed here, because
    quietly applying the long sign to a short trip would produce a number that
    reconciles and means the opposite.
    """
    entry = next((l for l in legs if (l.decision_type or "") == "entry"), None)
    exit_ = next((l for l in legs if (l.decision_type or "") == "exit"), None)
    costs = sum((_dec(l.commission_amount) + _dec(l.slippage_amount) for l in legs),
                Decimal("0"))
    trip = {
        "legs": len(legs),
        "sides": sorted((l.side or "").upper() for l in legs),
        "entry": entry,
        "exit": exit_,
        "costs": costs,
    }
    if entry is None or exit_ is None:
        return trip
    entry_price = _dec(entry.execution_price_before_costs)
    exit_price = _dec(exit_.execution_price_before_costs)
    gross = _dec(exit_.gross_amount) - _dec(entry.gross_amount)
    trip.update({
        "quantity": _dec(entry.quantity),
        "exit_quantity": _dec(exit_.quantity),
        "gross_pnl": gross,
        "net_pnl": gross - costs,
        "move": exit_price - entry_price,
    })
    return trip


def reconcile_paper_book(scope: RunScope, trips: dict[str, dict],
                        cash_available: Decimal) -> dict:
    """Check the replay's fills against their own columns and against capital."""
    violations: list[dict] = []
    gross_sum = cost_sum = net_sum = Decimal("0")

    for source_id, trip in trips.items():
        if trip["legs"] != 2 or trip["sides"] != ["BUY", "SELL"]:
            violations.append({
                "identity": "one BUY leg and one SELL leg per settled source row",
                "actual": trip["sides"], "source_trade_id": source_id,
            })
            continue

        # The arithmetic below signs the trip as a long, which is what the
        # replay books. A short leg pair reaching this point did not come from
        # a replay that honoured its own long-only guard, so it is named.
        if (trip["entry"].side or "").upper() != "BUY":
            violations.append({
                "identity": "the entry leg is the BUY (replay is long-only)",
                "actual": trip["entry"].side, "source_trade_id": source_id,
            })
            continue

        gross_sum += trip["gross_pnl"]
        cost_sum += trip["costs"]
        net_sum += trip["net_pnl"]

        for leg in (trip["entry"], trip["exit"]):
            found = _violation(
                "gross_amount == quantity x execution_price_before_costs",
                _dec(leg.gross_amount),
                _dec(leg.quantity) * _dec(leg.execution_price_before_costs),
                source_trade_id=source_id, side=leg.side,
            )
            if found:
                violations.append(found)

        if trip["quantity"] != trip["exit_quantity"]:
            violations.append({
                "identity": "both legs fill the same quantity",
                "actual": [str(trip["quantity"]), str(trip["exit_quantity"])],
                "source_trade_id": source_id,
            })

        # An entry leg takes money out (amount plus its costs); an exit leg
        # brings money in (amount less its costs). Both signs are stated rather
        # than derived from one multiplier, because a leg whose cash change was
        # written with the wrong sign is exactly what this check is for.
        entry = trip["entry"]
        found = _violation(
            "entry net_cash_change == -(gross_amount + entry costs)",
            _dec(entry.net_cash_change),
            -(_dec(entry.gross_amount)
              + _dec(entry.commission_amount) + _dec(entry.slippage_amount)),
            source_trade_id=source_id, side=entry.side,
        )
        if found:
            violations.append(found)

        exit_ = trip["exit"]
        found = _violation(
            "exit net_cash_change == gross_amount - exit costs",
            _dec(exit_.net_cash_change),
            _dec(exit_.gross_amount)
            - _dec(exit_.commission_amount) - _dec(exit_.slippage_amount),
            source_trade_id=source_id, side=exit_.side,
        )
        if found:
            violations.append(found)

    expected_cash = scope.initial_capital + net_sum
    cash_difference = cash_available - expected_cash
    if abs(cash_difference) > TOLERANCE:
        violations.append({
            "identity": "terminal cash == opening capital + booked net",
            "actual": str(cash_available), "expected": str(expected_cash),
            "difference": str(cash_difference),
        })

    return {
        "settled_round_trips": len(trips),
        "gross_pnl": float(gross_sum),
        "total_cost": float(cost_sum),
        "net_pnl": float(net_sum),
        "net_pnl_exact": str(net_sum),
        "opening_capital": float(scope.initial_capital),
        "cash_available": float(cash_available),
        # The third leg: a column the fills are not read through. A settlement
        # that moved cash without booking it, or booked it without moving cash,
        # shows up here instead of cancelling inside the fill arithmetic.
        "cash_identity": {
            "expected": float(expected_cash),
            "actual": float(cash_available),
            "difference": float(cash_difference),
        },
        "violation_count": len(violations),
        "violations": violations,
        "status": "reconciled" if not violations else "does_not_reconcile",
    }


def decompose_delta(research: dict, paper: dict, rows: list,
                    trips: dict[str, dict], refusals: dict[str, str]) -> dict:
    """Write the gap between the two books as named, measured terms.

    Both book totals are read from their ``net_pnl_exact`` strings, so the
    residual is the difference of two exact sums rather than of two floats.
    """
    by_id = {str(row.source_trade_id): row for row in rows}
    settled = {k: v for k, v in trips.items() if "net_pnl" in v}

    # Three buckets and no fourth: a row is settled when its round trip is
    # complete, refused when a guard wrote the refusal, and unaccounted when
    # neither happened. Testing membership against ``rows`` for the third case
    # would always be false — by_id is built from rows — so the bucket is the
    # complement of the other two.
    unaccounted = sorted(
        str(row.source_trade_id) for row in rows
        if str(row.source_trade_id) not in settled
        and str(row.source_trade_id) not in refusals
    )
    orphan = sorted(set(trips) - set(by_id))

    scale = price_basis = cost_rule = residual = Decimal("0")
    per_row: dict[str, dict] = {}

    for source_id, trip in settled.items():
        row = by_id.get(source_id)
        if row is None:
            # A fill whose source row the research scope does not contain. No
            # term can absorb it honestly — naming one would be inventing a
            # cause — so it is listed in ``orphan_fill_source_trade_ids`` and
            # left out of the named terms. Its P&L then shows up in the
            # residual, and because the per-row sum below never sees it either,
            # ``buckets_disagree`` fires: two independent computations of the
            # same gap no longer match.
            continue
        research_quantity = _dec(row.quantity)
        research_move = _dec(row.exit_price) - _dec(row.entry_price)
        if (row.direction or "").lower() == "short":
            research_move = -research_move
        research_net = _dec(row.net_pnl)

        scale_term = (trip["quantity"] - research_quantity) * research_move
        price_term = (trip["move"] - research_move) * trip["quantity"]
        cost_term = _dec(row.total_cost) - trip["costs"]
        gap = trip["net_pnl"] - research_net
        row_residual = gap - (scale_term + price_term + cost_term)

        scale += scale_term
        price_basis += price_term
        cost_rule += cost_term
        residual += row_residual
        per_row[source_id] = {
            "research_net_pnl": float(research_net),
            "paper_net_pnl": float(trip["net_pnl"]),
            "research_quantity": float(research_quantity),
            "paper_quantity": float(trip["quantity"]),
            "notional_scale": float(scale_term),
            "price_basis": float(price_term),
            "cost_rule": float(cost_term),
            "residual": float(row_residual),
        }

    refused_net = sum((_dec(by_id[k].net_pnl) for k in refusals if k in by_id), Decimal("0"))
    unaccounted_net = sum((_dec(by_id[k].net_pnl) for k in unaccounted), Decimal("0"))
    # Both totals are the books' own exact sums. The research total still
    # contains the refused and unaccounted rows — they are real study rows in
    # the window — while the paper total contains only fills, so the two sides
    # of the gap are made to speak about one population by the last two terms
    # below, not by filtering the rows here.
    total = _dec(paper["net_pnl_exact"]) - _dec(research["net_pnl_exact"])
    named = scale + price_basis + cost_rule - refused_net - unaccounted_net

    def component(name: str, amount: Decimal, basis: str, count: int | None = None) -> dict:
        """One named term, in both shapes.

        ``amount`` is the float a reader or a chart consumes; ``amount_exact``
        is the same number as the Decimal that produced it, so a reader can add
        the line back up without inheriting float ordering, and a test can
        require the sum to reproduce ``named_total`` to the last digit.
        """
        entry = {"term": name, "amount": float(amount), "amount_exact": str(amount),
                 "basis": basis}
        if count is not None:
            entry["count"] = count
        return entry

    return {
        # The books' own reported totals, restated here so the arithmetic line
        # at the top of this function can be read against them without opening
        # the two book objects side by side.
        "research_net_pnl": research["net_pnl"],
        "paper_net_pnl": paper["net_pnl"],
        "total_delta": float(total),
        "total_delta_exact": str(total),
        "components": [
            component("notional_scale", scale,
                      "quantity the paper book filled less quantity the study "
                      "assumed, at the study's own per-unit price move"),
            component("price_basis", price_basis,
                      "per-unit move the paper book paid less the study's, at the "
                      "quantity the paper book filled"),
            component("cost_rule", cost_rule,
                      "costs the study charged less costs the replay charged "
                      "(commission and slippage on both legs)"),
            component("refused_entries", -refused_net,
                      "research net P&L of signals a guard declined; the paper book "
                      "holds no fill for them", len(refusals)),
            component("unaccounted_rows", -unaccounted_net,
                      "research net P&L of in-scope rows that produced neither an "
                      "entry nor a refusal — a coverage failure, not a costing choice",
                      len(unaccounted)),
        ],
        "named_total": float(named),
        "named_total_exact": str(named),
        # The gap less the named terms. Algebraically this is the sum of the
        # per-row residuals below — the two are computed independently, one from
        # the book totals and one row by row, so a bucket that double-counts or
        # drops a row separates them instead of quietly agreeing.
        # Zero to within tolerance is the whole claim of this module, and the
        # number is reported whether or not it is zero.
        "residual": float(total - named),
        "residual_exact": str(total - named),
        "residual_within_tolerance": abs(total - named) <= TOLERANCE,
        "per_row_residual": float(residual),
        "buckets_disagree": abs((total - named) - residual) > TOLERANCE,
        "tolerance": float(TOLERANCE),
        "entered": len(settled),
        "refused": len(refusals),
        "refusal_reasons": {
            reason: sum(1 for r in refusals.values() if r == reason)
            for reason in sorted(set(refusals.values()))
        },
        "double_bucketed_source_trade_ids": sorted(set(settled) & set(refusals)),
        "unaccounted_source_trade_ids": unaccounted,
        "orphan_fill_source_trade_ids": orphan,
        "per_row": per_row,
    }


def terminal_cash(db: Session, scope: RunScope) -> Decimal:
    """Cash as the durable books left it, not as the engine remembered it."""
    row = db.execute(
        text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :p"),
        {"p": scope.portfolio_id},
    ).fetchone()
    if row is None:
        raise ScopeError(f"portfolio {scope.portfolio_id} is gone")
    return _dec(row.current_virtual_cash)


def reconcile_books(db: Session, *, run_id: str, user_id: str) -> dict:
    """The two books and the gap between them, read from durable rows only."""
    scope = resolve_scope(db, run_id=run_id, user_id=user_id)
    rows = source_rows(db, scope)
    research = reconcile_research_book(rows)
    trips = {k: _round_trip(v) for k, v in _legs_by_source(db, scope).items()}
    paper = reconcile_paper_book(scope, trips, terminal_cash(db, scope))
    delta = decompose_delta(research, paper, rows,
                            trips, _refusals(db, scope))

    return {
        "scope": {
            "run_id": scope.run_id,
            "portfolio_id": scope.portfolio_id,
            "instrument_id": scope.instrument,
            "interval_minutes": scope.interval_minutes,
            "rule_id": scope.rule_id,
            "cutoff": scope.cutoff.isoformat() if scope.cutoff else None,
            "as_of": scope.as_of.isoformat() if scope.as_of else None,
            "source_rows": len(rows),
        },
        "research_book": research,
        "paper_book": paper,
        "delta": delta,
        "verdict": (
            "reconciled"
            if research["status"] == "reconciled"
            and paper["status"] == "reconciled"
            and delta["residual_within_tolerance"]
            and not delta["buckets_disagree"]
            and not delta["unaccounted_source_trade_ids"]
            and not delta["orphan_fill_source_trade_ids"]
            and not delta["double_bucketed_source_trade_ids"]
            else "does_not_reconcile"
        ),
    }


__all__ = [
    "TOLERANCE",
    "ScopeError",
    "decompose_delta",
    "reconcile_books",
    "reconcile_paper_book",
    "reconcile_research_book",
    "resolve_scope",
    "source_rows",
]
