"""G3-2 — a paper replay is bound to the upstream population it read.

Before this family existed, ``execute_historical_replay`` selected upstream
study trades by ``(instrument, interval, rule, user, wall-clock window)``,
kept the rows only in memory, and wrote nothing that identified them. A run
that had already priced 20 trades could therefore be re-run against a
different population — rows edited underneath it, rows added inside the same
window, or rows deleted — and nothing on the record said which trades had been
consumed at all. The replay's numbers stayed, its blood-line did not.

The ruling this family enforces is 「paper replay 綁定精確的上游
study runs/trades、版本與內容摘要；重播後上游改動不可靜默改寫既有血緣」.
Each test below names one of those clauses:

* the exact ids (trade, run) and the ``research_version`` of every run the
  attempt priced (:func:`test_completed_replay_names_the_exact_upstream_population`);
* a content digest over the fields that decide the arithmetic, not a row count
  (:class:`TestDigestIsContentAddressed`);
* growth after the record cannot rewrite the record
  (:func:`test_growth_after_the_record_cannot_rewrite_the_blood_line`);
* deletion and repricing are reported as drift, never absorbed silently
  (:class:`TestUpstreamDriftIsRefused`);
* "nothing was read yet" and "the population was empty" stay two different
  values (:func:`test_empty_upstream_records_emptiness_not_absence`).

Nothing here fabricates an economic event to satisfy an assertion: the drift
cases assert the *absence* of ledger side effects, which is the point.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database import SessionLocal
from app.services.paper_trading import (
    REPLAY_LINEAGE_SCHEMA,
    REPLAY_PRICED_FIELDS,
    UPSTREAM_TRADES_SQL,
    capture_replay_lineage,
    record_replay_lineage,
    replay_bound_trades,
    replay_lineage_digest,
)
from app.services.paper_trading_exceptions import UpstreamLineageDrift
from tests.replay_fixture import (
    INSTRUMENT,
    INTERVAL_MINUTES,
    RULE_ID,
    capture_statements,
    lineage_reread_queries,
    replay_case,  # noqa: F401  (registers the fixture on this module)
    trades_query,
)
from tests.test_paper_trading_ownership import _make_user_headers
from tests.test_replay_credibility import PROFITABLE_LONG

# Two round trips inside the seeded window: one winner, one loser. Kept to two
# so the growth step below is an unambiguous "+1".
PAIR = [
    PROFITABLE_LONG,
    (2, 3, Decimal("50000"), Decimal("49000")),
]
# Added after the replay has already recorded its lineage. Entry sits behind the
# 30-day cutoff, exit behind ``as_of``, so the only thing that makes this trade
# different from the pair above is that it appeared later.
LATE_ARRIVAL = (5, 6, Decimal("50000"), Decimal("52000"))

# The documented replay request body, spelled out because ``POST /replay/{run}``
# takes ``ReplayRequest`` as a body, not as query defaults. Same values the
# service itself defaults to, so the HTTP lane and the service lane price alike.
REPLAY_BODY = {"days_back": 30, "position_size_pct": 0.10, "max_positions": 3}

SHA256_HEX = re.compile(r"^sha256:[0-9a-f]{64}$")


def raw_lineage(run_id: str):
    """Read ``source_lineage`` straight from the row, bypassing the service.

    Returns the decoded object, or ``None`` when the column is NULL. Compared as
    a decoded object rather than as stored text on purpose: PostgreSQL's
    ``jsonb`` reorders keys and strips whitespace, so the text of two
    semantically identical captures is not byte-equal across drivers — while a
    decoded dict is.
    """
    db = SessionLocal()
    try:
        stored = db.execute(
            text("SELECT source_lineage FROM paper_trading_runs WHERE id = :i"),
            {"i": run_id},
        ).scalar()
    finally:
        db.close()
    return json.loads(stored) if isinstance(stored, str) else stored


def ledger_snapshot(portfolio_id: str, run_id: str) -> dict:
    """Every ledger counter a replay attempt could move, read independently."""
    db = SessionLocal()
    try:
        return {
            "orders": db.execute(
                text("SELECT COUNT(*) FROM paper_orders WHERE portfolio_id = :p"),
                {"p": portfolio_id},
            ).scalar(),
            "fills": db.execute(
                text("SELECT COUNT(*) FROM paper_fills WHERE paper_order_id IN "
                     "(SELECT id FROM paper_orders WHERE portfolio_id = :p)"),
                {"p": portfolio_id},
            ).scalar(),
            "positions": db.execute(
                text("SELECT COUNT(*) FROM paper_positions WHERE portfolio_id = :p"),
                {"p": portfolio_id},
            ).scalar(),
            "decisions": db.execute(
                text("SELECT COUNT(*) FROM decision_records WHERE paper_portfolio_id = :p"),
                {"p": portfolio_id},
            ).scalar(),
            "daily_metrics": db.execute(
                text("SELECT COUNT(*) FROM paper_trading_daily_metrics WHERE run_id = :r"),
                {"r": run_id},
            ).scalar(),
            "cash": str(db.execute(
                text("SELECT current_virtual_cash FROM paper_portfolios WHERE id = :p"),
                {"p": portfolio_id},
            ).scalar()),
        }
    finally:
        db.close()


def priced_trade_ids(portfolio_id: str) -> set:
    """Upstream trade ids this portfolio's own decisions name."""
    db = SessionLocal()
    try:
        return {
            str(row[0])
            for row in db.execute(
                text("SELECT DISTINCT source_trade_id FROM decision_records "
                     "WHERE paper_portfolio_id = :p AND source_trade_id IS NOT NULL"),
                {"p": portfolio_id},
            )
        }
    finally:
        db.close()


def run_state(run_id: str) -> dict:
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT status, failure_reason FROM paper_trading_runs WHERE id = :i"),
            {"i": run_id},
        ).fetchone()
    finally:
        db.close()
    return {"status": row[0], "failure_reason": row[1]}


def delete_upstream_trade(trade_id: str) -> None:
    db = SessionLocal()
    try:
        deleted = db.execute(
            text("DELETE FROM strategy_study_trades WHERE id = :t"), {"t": trade_id}
        ).rowcount
        db.commit()
    finally:
        db.close()
    assert deleted == 1, f"fixture did not own exactly one trade {trade_id}"


def reprice_upstream_trade(trade_id: str, entry_price: Decimal) -> None:
    """Change one priced field without changing the row count or the ids."""
    db = SessionLocal()
    try:
        updated = db.execute(
            text("UPDATE strategy_study_trades SET entry_price = :e WHERE id = :t"),
            {"e": entry_price, "t": trade_id},
        ).rowcount
        db.commit()
    finally:
        db.close()
    assert updated == 1, f"fixture did not own exactly one trade {trade_id}"


class TestLineageIsAbsentUntilSomethingIsRead:
    def test_column_is_null_before_any_attempt(self, replay_case):  # noqa: F401
        case = replay_case(PAIR)

        assert raw_lineage(case.run_id) is None, (
            "a run that has not read anything must not look like it read an "
            "empty population"
        )

    def test_failed_to_acquire_attempt_records_nothing(self, replay_case):  # noqa: F401
        """An attempt that never got execution rights priced nothing to bind.

        The capture is taken after the conditional UPDATE that grants the right
        to run; recording before it would let a refused attempt stamp a
        blood-line onto a run whose ledger it never touched.
        """
        case = replay_case(PAIR)
        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET status = 'completed' WHERE id = :i"),
                {"i": case.run_id},
            )
            db.commit()
        finally:
            db.close()

        from app.services.paper_trading_exceptions import InvalidStateError

        with pytest.raises(InvalidStateError):
            case.replay(days_back=30)

        assert raw_lineage(case.run_id) is None


class TestRecordedIdentity:
    def test_completed_replay_names_the_exact_upstream_population(self, replay_case):  # noqa: F401
        case = replay_case(PAIR)
        result = case.replay(days_back=30)

        lineage = raw_lineage(case.run_id)
        assert lineage is not None, "a completed replay left no blood-line"
        assert lineage["schema"] == REPLAY_LINEAGE_SCHEMA
        assert lineage["rule_id"] == RULE_ID

        # The exact ids, in the order the window read returned them.
        assert [str(i) for i in case.trade_ids] == lineage["upstream_trade_ids"], (
            f"recorded {lineage['upstream_trade_ids']}, seeded {case.trade_ids}"
        )
        assert lineage["upstream_run_ids"] == [str(case.study_run_id)]
        assert lineage["upstream_research_versions"] == ["v1.0"], (
            "the research version of the upstream run is part of the identity"
        )
        assert lineage["trade_count"] == len(case.trade_ids) == 2
        assert SHA256_HEX.match(lineage["content_digest"]), lineage["content_digest"]

        # The window it was read under, bound to the request parameter.
        cutoff = datetime.fromisoformat(lineage["cutoff"])
        as_of = datetime.fromisoformat(lineage["as_of"])
        assert cutoff.tzinfo is not None and as_of.tzinfo is not None
        span = as_of - cutoff
        assert timedelta(days=29, hours=23) < span < timedelta(days=30, hours=1), (
            f"days_back=30 recorded a {span} window"
        )

        # The caller is told the same thing the row holds.
        assert result["source_lineage"] == lineage

    def test_attempt_that_refused_entries_still_records_what_it_read(self, replay_case):  # noqa: F401
        """Refusal is a decision about the ledger, not about the blood-line.

        Two round trips overlap, and capacity is one, so the second entry is
        refused — yet the replay still read it. The lineage says what this run
        was priced *from*, which is the population, not the subset that fitted.
        """
        case = replay_case([
            (0, 4, Decimal("50000"), Decimal("51000")),
            (1, 5, Decimal("50000"), Decimal("50500")),
        ])
        result = case.replay(days_back=30, max_positions=1)

        lineage = raw_lineage(case.run_id)
        assert lineage["trade_count"] == 2, (
            "the population read is recorded, not the subset that executed"
        )
        assert [str(i) for i in case.trade_ids] == lineage["upstream_trade_ids"], (
            "a refused trade was left out of the blood-line"
        )
        assert result["total_trades"] == 1, result["counting"]
        assert [r["reason"] for r in result["refused_entries"]] == ["max_positions"]
        assert lineage["trade_count"] != result["total_trades"], (
            "this case is only meaningful if read and priced counts differ"
        )


class TestEmptyVersusAbsent:
    def test_empty_upstream_records_emptiness_not_absence(self, replay_case):  # noqa: F401
        replayed = replay_case()
        idle = replay_case()

        replayed.replay(days_back=30)

        recorded = raw_lineage(replayed.run_id)
        assert recorded is not None, (
            "an empty population is a finding, and must not be stored as NULL"
        )
        assert recorded["trade_count"] == 0
        assert recorded["upstream_trade_ids"] == []
        assert recorded["upstream_run_ids"] == []
        assert raw_lineage(idle.run_id) is None

    def test_empty_lineage_is_not_dereferenced(self, replay_case):  # noqa: F401
        """Zero ids name zero rows, so the bound re-read must not widen the read.

        An ``IN ()`` over an empty list is either a syntax error or a
        tautology depending on the driver; the product skips the read, and this
        asserts it does — otherwise the lineage's "empty" would silently become
        "everything in scope".
        """
        case = replay_case()
        with capture_statements() as calls:
            case.replay(days_back=30)

        assert len(lineage_reread_queries(calls)) == 0, (
            f"unexpected bound re-reads: {[c.statement[:80] for c in lineage_reread_queries(calls)]}"
        )


class TestStatementLane:
    def test_window_read_once_and_bound_read_once(self, replay_case):  # noqa: F401
        case = replay_case(PAIR)

        with capture_statements() as calls:
            case.replay(days_back=30)

        scope = trades_query(calls)
        rereads = lineage_reread_queries(calls)
        assert len(rereads) == 1, f"got {len(rereads)} bound re-reads"

        # The two reads are told apart by what they bind, which is the whole
        # contract: the window chooses the population once, the lineage
        # dereferences it by id.
        assert "cutoff" in scope.parameters and "as_of" in scope.parameters
        for reread in rereads:
            assert "cutoff" not in reread.parameters, (
                "the bound re-read is not time-bounded — the lineage is the window"
            )
            assert sorted(reread.parameters["trade_ids"]) == sorted(
                str(i) for i in raw_lineage(case.run_id)["upstream_trade_ids"]
            )
            for owner_key in ("instrument", "interval", "rule_id", "user_id"):
                assert owner_key in reread.parameters, (
                    f"the bound re-read dropped its {owner_key} predicate"
                )


class TestWriteOnce:
    def test_second_capture_returns_the_first_record(self, replay_case):  # noqa: F401
        case = replay_case()
        db = SessionLocal()
        try:
            first = {"schema": REPLAY_LINEAGE_SCHEMA, "marker": "first",
                     "trade_count": 0, "upstream_trade_ids": [],
                     "content_digest": "sha256:first"}
            second = {"schema": REPLAY_LINEAGE_SCHEMA, "marker": "second",
                      "trade_count": 9, "upstream_trade_ids": [str(uuid4())],
                      "content_digest": "sha256:second"}

            assert record_replay_lineage(db, case.run_id, first) is None, (
                "the first write reports that it wrote"
            )
            returned = record_replay_lineage(db, case.run_id, second)
            db.commit()

            assert returned == first, (
                "a losing attempt is handed the record it failed to overwrite"
            )
            assert raw_lineage(case.run_id) == first, (
                "the recorded blood-line was overwritten by a later attempt"
            )
        finally:
            db.rollback()
            db.close()


class TestGrowthCannotRewrite:
    def test_bound_read_returns_the_recorded_rows_after_the_window_grows(self, replay_case):  # noqa: F401
        """A trade inserted inside the window is outside the recorded population.

        Tested against ``replay_bound_trades`` rather than a second full replay
        attempt on purpose: a retry that reached the ledger again would be
        stopped by ``decision_records``' idempotency constraint, which is a
        different guard, and mixing the two would hide which one fired.
        """
        case = replay_case(PAIR)
        case.replay(days_back=30)
        recorded = raw_lineage(case.run_id)
        assert recorded["trade_count"] == 2

        late_id = case.grow(LATE_ARRIVAL)[0]

        db = SessionLocal()
        try:
            rows = replay_bound_trades(
                db, recorded, run_id=case.run_id,
                scope_params={
                    "instrument": INSTRUMENT,
                    "interval": INTERVAL_MINUTES,
                    "rule_id": RULE_ID,
                    "user_id": case.user_id,
                },
            )
        finally:
            db.close()

        assert [str(r.source_trade_id) for r in rows] == recorded["upstream_trade_ids"], (
            "the bound read followed the grown window instead of the record"
        )
        assert late_id not in [str(r.source_trade_id) for r in rows]

    def test_growth_after_an_empty_capture_cannot_reach_the_ledger(self, replay_case):  # noqa: F401
        """The empty capture is authoritative, and the retry says what it skipped.

        This is the case where a full second attempt does complete: the first
        read found nothing, so nothing was priced and nothing was written, and
        trades that appear afterwards meet no idempotency guard. What must not
        happen is the retry quietly adopting them — the run's evidence would
        then be a population it never recorded.
        """
        case = replay_case()
        case.replay(days_back=30)
        recorded = raw_lineage(case.run_id)
        assert recorded["trade_count"] == 0

        late_ids = case.grow(LATE_ARRIVAL, (7, 8, Decimal("50000"), Decimal("51000")))
        case.reopen()
        result = case.replay(days_back=30)

        assert raw_lineage(case.run_id) == recorded, (
            "trades inserted after the capture rewrote the run's blood-line"
        )
        assert result["total_trades"] == 0
        assert priced_trade_ids(case.portfolio_id) == set(), (
            "a run whose lineage records an empty population priced trades"
        )

        # Not silent: the response names what the fresh window found and the
        # lineage refused.
        delta = result["lineage_window_delta"]
        assert delta["window_trade_count"] == 2
        assert delta["bound_trade_count"] == 0
        assert sorted(delta["outside_lineage_trade_ids"]) == sorted(str(i) for i in late_ids)



class TestUpstreamDriftIsRefused:
    def test_deleting_a_pinned_trade_fails_without_ledger_side_effects(self, replay_case):  # noqa: F401
        case = replay_case(PAIR)
        case.replay(days_back=30)
        recorded = raw_lineage(case.run_id)
        before = ledger_snapshot(case.portfolio_id, case.run_id)

        victim = str(case.trade_ids[1])
        delete_upstream_trade(victim)
        case.reopen()

        with pytest.raises(UpstreamLineageDrift) as excinfo:
            case.replay(days_back=30)

        detail = excinfo.value.detail
        assert detail["recorded_trade_count"] == 2
        assert detail["readable_trade_count"] == 1

        after = ledger_snapshot(case.portfolio_id, case.run_id)
        assert after == before, (
            f"a refused retry moved the ledger: {before} -> {after}"
        )
        # ``victim`` still has a decision row from the first attempt, and that
        # history is correct; what this asserts is that the refused attempt
        # added nothing, which the equality above covers.

        # The record survives the refusal, and the run says why it died.
        assert raw_lineage(case.run_id) == recorded
        state = run_state(case.run_id)
        assert state["status"] == "failed"
        assert "is bound to 2 upstream trades" in state["failure_reason"], state["failure_reason"]

    def test_repricing_a_pinned_trade_reports_both_digests(self, replay_case):  # noqa: F401
        """Same ids, same count, different prices — the count leg cannot see this.

        This is why the capture holds a content digest at all: a study that
        re-runs and rewrites its own trade's entry price is a different dataset
        to a caller comparing two runs, even though every id still resolves.
        """
        case = replay_case(PAIR)
        case.replay(days_back=30)
        recorded = raw_lineage(case.run_id)

        reprice_upstream_trade(str(case.trade_ids[0]), Decimal("61000"))
        case.reopen()

        with pytest.raises(UpstreamLineageDrift) as excinfo:
            case.replay(days_back=30)

        detail = excinfo.value.detail
        assert detail["recorded_trade_count"] == detail["readable_trade_count"] == 2, (
            "the count leg fired, so this never tested the digest leg"
        )
        assert detail["recorded_digest"] == recorded["content_digest"]
        assert detail["current_digest"] != detail["recorded_digest"]

    def test_unreadable_record_is_reported_not_repaired(self, replay_case):  # noqa: F401
        """A capture missing its ids is a fault, not a blank slate.

        Treating a half-readable record as no record would let the next attempt
        overwrite it — the exact silent rewrite this column exists to prevent.
        """
        case = replay_case(PAIR)
        case.replay(days_back=30)

        db = SessionLocal()
        try:
            db.execute(
                text("UPDATE paper_trading_runs SET source_lineage = :v WHERE id = :i"),
                {"v": json.dumps({"schema": REPLAY_LINEAGE_SCHEMA, "trade_count": 2}),
                 "i": case.run_id},
            )
            db.commit()
        finally:
            db.close()

        case.reopen()
        with pytest.raises(UpstreamLineageDrift) as excinfo:
            case.replay(days_back=30)
        assert excinfo.value.detail["explanation"] == "lineage_missing_upstream_trade_ids"
        assert raw_lineage(case.run_id)["trade_count"] == 2, (
            "the broken record was repaired by the attempt that refused it"
        )

    def test_forged_lineage_cannot_reach_another_owner_s_trades(self, replay_case):  # noqa: F401
        """Ownership predicates survive the re-read.

        A bound read is dereferenced by id, so if the id alone decided the row,
        a caller that could name somebody else's trade id could price it. The
        instrument/interval/rule/user predicates are kept in the re-read for
        exactly this reason.
        """
        mine = replay_case(PAIR)
        theirs = replay_case([PROFITABLE_LONG])
        foreign_id = str(theirs.trade_ids[0])

        forged = {
            "schema": REPLAY_LINEAGE_SCHEMA,
            "trade_count": 1,
            "upstream_trade_ids": [foreign_id],
            "content_digest": "sha256:" + "0" * 64,
        }
        db = SessionLocal()
        try:
            with pytest.raises(UpstreamLineageDrift) as excinfo:
                replay_bound_trades(
                    db,
                    forged,
                    run_id=mine.run_id,
                    scope_params={
                        "instrument": INSTRUMENT,
                        "interval": INTERVAL_MINUTES,
                        "rule_id": RULE_ID,
                        "user_id": mine.user_id,
                    },
                )
        finally:
            db.close()

        detail = excinfo.value.detail
        assert detail["recorded_trade_count"] == 1
        assert detail["readable_trade_count"] == 0, (
            "a foreign owner's trade id was readable under this owner's scope"
        )


def _row(**overrides):
    fields = {
        "source_trade_id": "11111111-1111-1111-1111-111111111111",
        "entry_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "exit_time": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "entry_price": Decimal("50000"),
        "exit_price": Decimal("51000"),
        "net_pnl": Decimal("200"),
        "direction": "long",
        "quantity": Decimal("1000"),
        "exit_reason": "signal",
        "source_run_id": "22222222-2222-2222-2222-222222222222",
        "source_data_version": "v1.0",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestDigestIsContentAddressed:
    def test_row_order_does_not_change_the_digest(self):
        a, b = _row(), _row(source_trade_id="33333333-3333-3333-3333-333333333333")

        assert replay_lineage_digest([a, b]) == replay_lineage_digest([b, a]), (
            "the digest names the population, not the order the database returned"
        )

    def test_priced_fields_change_it_and_unpriced_fields_do_not(self):
        baseline = replay_lineage_digest([_row()])

        assert replay_lineage_digest([_row(entry_price=Decimal("50000.01"))]) != baseline
        assert replay_lineage_digest([_row(exit_time=datetime(2026, 1, 2, 1, tzinfo=timezone.utc))]) != baseline
        assert replay_lineage_digest([_row(direction="short")]) != baseline

        # exit_reason and the run identity are recorded elsewhere in the lineage;
        # they do not decide what the replay would compute, so they stay out.
        assert replay_lineage_digest([_row(exit_reason="end_of_horizon")]) == baseline
        assert replay_lineage_digest(
            [_row(source_run_id="44444444-4444-4444-4444-444444444444")]
        ) == baseline

    def test_equal_values_render_equal_across_driver_shapes(self):
        """``Decimal('50000.00')`` and ``50000.0`` are one price.

        PostgreSQL hands back Decimal, SQLite hands back what the fixture bound.
        Without normalising, a run's digest would depend on which driver read
        it, and the drift check would fire on a restart alone.
        """
        assert replay_lineage_digest([_row()]) == replay_lineage_digest(
            [_row(entry_price=50000.0, exit_price=51000.0, net_pnl=200.0, quantity=1000.0)]
        )

    def test_digest_of_nothing_is_the_empty_digest(self):
        assert replay_lineage_digest([]) == replay_lineage_digest(())
        assert SHA256_HEX.match(replay_lineage_digest([]))

    def test_priced_fields_are_the_columns_the_upstream_read_selects(self):
        """The digest list and the SELECT list must be reviewable against each other.

        If they ever disagree, a field can be digested but not selected — so the
        bound re-read computes a digest over ``None`` for it and drift goes
        unreported — or selected but not digested, and a real repricing reads
        back as unchanged. Neither looks wrong until the day it matters.
        """
        for field in REPLAY_PRICED_FIELDS:
            assert re.search(rf"\b{field}\b", UPSTREAM_TRADES_SQL), (
                f"{field} is in the content digest but the upstream read does not select it"
            )

        # The five that decide the arithmetic, named here rather than inferred
        # from the code, so widening or narrowing the list is a reviewed change.
        for required in ("source_trade_id", "entry_price", "exit_price",
                         "net_pnl", "direction", "quantity"):
            assert required in REPLAY_PRICED_FIELDS, (
                f"{required} prices the replay but is not digested"
            )

    def test_capture_dedupes_runs_and_versions_across_trades(self):
        rows = [
            _row(),
            _row(source_trade_id="55555555-5555-5555-5555-555555555555",
                 source_run_id="22222222-2222-2222-2222-222222222222",
                 source_data_version="v1.0"),
            _row(source_trade_id="66666666-6666-6666-6666-666666666666",
                 source_run_id="77777777-7777-7777-7777-777777777777",
                 source_data_version="v0.9"),
        ]
        lineage = capture_replay_lineage(
            rows, skill=RULE_ID,
            cutoff=datetime(2026, 1, 1, tzinfo=timezone.utc),
            as_of=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )

        assert lineage["trade_count"] == 3
        assert len(lineage["upstream_trade_ids"]) == 3
        assert lineage["upstream_run_ids"] == [
            "22222222-2222-2222-2222-222222222222",
            "77777777-7777-7777-7777-777777777777",
        ], "one entry per run, sorted"
        assert lineage["upstream_research_versions"] == ["v0.9", "v1.0"]
        assert lineage["content_digest"] == replay_lineage_digest(rows)


class TestHttpContract:
    """The API must answer drift with a decision the caller can act on.

    A 500 would read as "the platform broke"; the truth is "the history you are
    replaying is no longer the history this run was priced from", which is a
    conflict the caller can only resolve by starting a new run.
    """

    def test_drift_is_a_409_with_the_recorded_population(self, client, replay_case):  # noqa: F401
        headers, user_id = _make_user_headers(client, "lineage-drift@alethra.io")
        case = replay_case(PAIR, user_id=user_id)

        ok = client.post(
            f"/api/v1/paper-trading/replay/{case.run_id}", headers=headers, json=REPLAY_BODY
        )
        assert ok.status_code == 200, ok.json()
        recorded = raw_lineage(case.run_id)

        delete_upstream_trade(str(case.trade_ids[0]))
        case.reopen()

        conflict = client.post(
            f"/api/v1/paper-trading/replay/{case.run_id}", headers=headers, json=REPLAY_BODY
        )
        assert conflict.status_code == 409, conflict.json()
        detail = conflict.json()["detail"]
        assert detail["reason"] == "upstream_lineage_drift"
        assert detail["recorded_trade_count"] == 2
        assert detail["readable_trade_count"] == 1
        assert detail["recorded_digest"] == recorded["content_digest"]

    def test_success_response_carries_the_lineage_to_the_caller(self, client, replay_case):  # noqa: F401
        headers, user_id = _make_user_headers(client, "lineage-ok@alethra.io")
        case = replay_case(PAIR, user_id=user_id)

        response = client.post(
            f"/api/v1/paper-trading/replay/{case.run_id}", headers=headers, json=REPLAY_BODY
        )
        assert response.status_code == 200, response.json()
        body = response.json()
        assert body["source_lineage"] == raw_lineage(case.run_id)
        # Nothing appeared between the window read and the bound read, so the
        # delta is present and empty — not omitted, which would make "no growth"
        # and "the product stopped reporting" indistinguishable.
        assert body["lineage_window_delta"] == {
            "window_trade_count": 2,
            "bound_trade_count": 2,
            "outside_lineage_trade_ids": [],
        }
