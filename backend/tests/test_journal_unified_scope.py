"""G3-5 — the unified journal's filters, counts, ordering and export share one range.

The unified view assembled its own SQL beside the journal service's, and the two
had drifted apart in five ways that each make a number on the screen mean
something other than what it says:

* ``summary`` added up the rows on the current page, so ``total_count`` changed
  when the caller changed ``limit``;
* each leg was fetched with its own ``LIMIT :limit OFFSET :offset``, so a merged
  page skipped rows that belonged in it and repeated rows that did not;
* ``rule_id``/``pnl_min``/``pnl_max`` were accepted, applied to the research leg
  only, and reported as if they had been applied;
* the paper date filter compared ``bar_timestamp`` alone, which is NULL for every
  daily fill — so a dated range deleted those rows and the Date cell of the same
  row rendered empty;
* the unified tab's Export button fetched ``/journal/trades.csv``, a research-only
  file beside a table that showed both sources.

One fixture holds four research trades and five fills, deliberately interleaved
across two instruments and two time granularities, because every one of those
defects only shows when the two legs are compared against each other.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.auth.jwt import create_access_token
from app.lib.export_headers import (
    EXPORT_ROW_LIMIT_HEADER,
    EXPORT_ROWS_EXPORTED_HEADER,
    EXPORT_ROWS_MATCHED_HEADER,
    EXPORT_TRUNCATED_HEADER,
)
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderType,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperDecisionActorType,
    PaperDecisionType,
)
from app.models.user import User
from app.services import journal_service as svc
from tests.conftest import TestSession
from tests.test_journal_api import ANCHOR, _run, _trade

INSTRUMENT_BTC = "G35:KRAKEN:BTC-USD"
INSTRUMENT_ETH = "G35:KRAKEN:ETH-USD"

# Ids are namespaced per fixture call because the harness database is a file that
# outlives a pytest session and already holds rows written by other lanes.
ASSET_SUFFIX = uuid.uuid4().hex[:8]


def _day(offset_days: int, hour: int = 0, minute: int = 0) -> datetime:
    return ANCHOR + timedelta(days=offset_days, hours=hour, minutes=minute)


def _date_text(offset_days: int) -> str:
    return _day(offset_days).strftime("%Y-%m-%d")


def _headers(user_id) -> dict:
    return {"Authorization": f"Bearer {create_access_token(data={'sub': str(user_id)})}"}


# ---------------------------------------------------------------------------
# Fixture: 4 research trades + 5 paper fills, interleaved
# ---------------------------------------------------------------------------

# (execution_date offset, rule, bar-timestamp offset or None, net_cash_change)
_PAPER_SEED = (
    (2, "breakout_trend", (2, 15, 30), "-1000.5"),   # p0 intraday, mid-window
    (7, "range_fade", None, "500"),                   # p1 daily
    (35, "breakout_trend", None, "-20"),              # p2 daily, past the month
    (21, "breakout_trend", None, "10"),               # p3 daily, on ETH
    (2, "breakout_trend", None, "5"),                 # p4 daily, p0's day
)

_RESEARCH_SEED = (
    (0, "breakout_trend", "100", "signal_flip", INSTRUMENT_BTC, 1440),
    (5, "breakout_trend", "-50", "stop_loss", INSTRUMENT_BTC, 1440),
    (10, "range_fade", "25", "signal_flip", INSTRUMENT_BTC, 1440),
    (19, "range_fade", "10", "time_stop", INSTRUMENT_ETH, 240),
)

# Newest first, by (day, instant, source, id): p2 11-05, p3 10-22, t3 10-20,
# t2 10-11, p1 10-08, t1 10-06, p0 10-03 15:30, p4 10-03 (no clock time), t0 10-01.
FULL_ORDER = ["p2", "p3", "t3", "t2", "p1", "t1", "p0", "p4", "t0"]


class Fixture:
    def __init__(self, user_id, research, paper):
        self.user_id = user_id
        self.research = research  # label -> trade id
        self.paper = paper       # label -> fill id

    def ids(self, labels) -> list:
        table = {**self.research, **self.paper}
        return [table[label] for label in labels]


@pytest.fixture()
def journal_fixture():
    """Seeds the nine rows and returns their ids keyed by the names used below."""
    db = TestSession()
    try:
        user = User(
            id=uuid.uuid4(),
            email=f"g35-{uuid.uuid4().hex[:12]}@example.test",
            password_hash="test-only",
            display_name="G35 unified scope",
        )
        db.add(user)
        db.flush()
        uid = user.id

        runs = {}
        research_ids = {}
        for index, (offset, rule_id, net_pnl, exit_reason, instrument, interval) in enumerate(
            _RESEARCH_SEED
        ):
            key = (instrument, interval)
            if key not in runs:
                runs[key] = _run(
                    db, uid, instrument=instrument, interval=interval, trade_count=4
                )
            trade = _trade(
                db, runs[key], index=index, rule_id=rule_id, net_pnl=net_pnl,
                entry_day_offset=offset, exit_reason=exit_reason,
                outcome="win" if Decimal(net_pnl) > 0 else "loss",
            )
            db.flush()
            research_ids[f"t{index}"] = str(trade.id)

        # The replay stamps the upstream study rule onto the decision that
        # ordered the fill, so a rule filter can reach a fill at all.
        btc_run = runs[(INSTRUMENT_BTC, 1440)]
        portfolio = PaperPortfolio(
            id=uuid.uuid4(),
            owner_id=uid,
            currency="USD",
            initial_virtual_cash=100000.0,
            current_virtual_cash=99000.0,
        )
        db.add(portfolio)
        db.flush()

        paper_ids = {}
        for i, (offset, rule_id, bar_offset, net_cash_change) in enumerate(_PAPER_SEED):
            instrument = INSTRUMENT_ETH if i == 3 else INSTRUMENT_BTC
            decision = DecisionRecord(
                id=uuid.uuid4(),
                owner_id=uid,
                paper_portfolio_id=portfolio.id,
                actor_type=PaperDecisionActorType.system,
                decision_type=(
                    PaperDecisionType.exit if i == 1 else PaperDecisionType.entry
                ),
                decision_time=_day(offset),
                source_rule_id=rule_id,
                source_run_id=btc_run.id,
            )
            db.add(decision)
            order = PaperOrder(
                id=uuid.uuid4(),
                portfolio_id=portfolio.id,
                decision_id=decision.id,
                asset_id=instrument,
                side=OrderSide.BUY if i % 2 == 0 else OrderSide.SELL,
                quantity=1.0,
                order_type=OrderType.market,
                created_at=_day(offset),
            )
            db.add(order)
            db.flush()
            fill = PaperFill(
                id=uuid.uuid4(),
                paper_order_id=order.id,
                execution_date=_date_text(offset),
                observed_price=50000.0,
                execution_price_before_costs=50000.0,
                slippage_amount=0.5,
                commission_amount=1.0,
                quantity=1.0,
                gross_amount=50000.0,
                net_cash_change=float(net_cash_change),
                # p4's reason starts with "=" so the text guard has something to
                # catch in this file rather than only in the research export.
                simulated_fill_reason="=SUM(A1:A2)" if i == 4 else "replay_bar_fill",
                bar_timestamp=_day(*bar_offset) if bar_offset else None,
            )
            db.add(fill)
            db.flush()
            paper_ids[f"p{i}"] = str(fill.id)

        db.commit()
        return Fixture(uid, research_ids, paper_ids)
    finally:
        db.close()


def _page_as(client, user_id, **params):
    resp = client.get("/api/v1/journal/unified", headers=_headers(user_id), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _page(client, fixture, **params):
    return _page_as(client, fixture.user_id, **params)


def _page_ids(body) -> list:
    return [row["id"] for row in body["trades"]]


def _counts(body) -> dict:
    summary = body["summary"]
    return {
        "total": summary["total_count"],
        "research": summary["research_count"],
        "paper": summary["paper_count"],
    }


# ---------------------------------------------------------------------------
# A. The summary counts the filter, the page counts the page
# ---------------------------------------------------------------------------

class TestSummaryIsPopulationScoped:
    def test_total_counts_every_matching_row_not_the_page(self, client, journal_fixture):
        body = _page(client, journal_fixture, limit=3)
        assert _page_ids(body) == journal_fixture.ids(FULL_ORDER[:3])
        assert body["page"]["returned"] == 3
        assert _counts(body) == {"total": 9, "research": 4, "paper": 5}

    def test_summary_is_identical_at_every_limit(self, client, journal_fixture):
        """The number beside a filter must not depend on how many rows were asked for."""
        base = _page(client, journal_fixture, limit=100)["summary"]
        for limit in (1, 2, 7, 50):
            assert _page(client, journal_fixture, limit=limit)["summary"] == base, limit

    def test_research_pnl_and_win_rate_cover_the_full_set(self, client, journal_fixture):
        body = _page(client, journal_fixture, limit=1)
        summary = body["summary"]
        # 100 - 50 + 25 + 10 over all four trades; a page of one cannot hold it.
        assert Decimal(summary["research_total_pnl"]) == Decimal("85")
        assert summary["research_wins"] == 3
        assert summary["research_win_rate"] == pytest.approx(0.75)

    def test_win_rate_stays_a_fraction_of_measured_trades(self, client, journal_fixture):
        """Percent points would print 7500% through formatPercentFraction."""
        assert 0 <= _page(client, journal_fixture)["summary"]["research_win_rate"] <= 1

    def test_empty_filter_yields_no_win_rate_rather_than_zero(self, client, journal_fixture):
        body = _page(client, journal_fixture, rule_id="no_such_rule_anywhere")
        assert body["summary"]["research_count"] == 0
        assert body["summary"]["research_win_rate"] is None


# ---------------------------------------------------------------------------
# B. The merged page is a slice of one ordering
# ---------------------------------------------------------------------------

class TestMergedPagination:
    def test_pages_neither_repeat_nor_skip_rows(self, client, journal_fixture):
        """Per-leg LIMIT/OFFSET made a merged page drop rows that belonged in it."""
        walked = []
        offset = 0
        while True:
            body = _page(client, journal_fixture, limit=2, offset=offset)
            ids = _page_ids(body)
            walked.extend(ids)
            if len(ids) < 2:
                break
            offset += 2
        assert walked == journal_fixture.ids(FULL_ORDER)
        assert len(set(walked)) == len(walked)

    def test_newest_first_across_both_sources(self, client, journal_fixture):
        body = _page(client, journal_fixture, limit=100)
        days = [
            (row["entry_time"] or row["execution_date"])[:10]
            for row in body["trades"]
        ]
        assert days == sorted(days, reverse=True)

    def test_a_daily_fill_sorts_on_its_day_and_after_that_days_intraday_fills(self,
                                                                              client,
                                                                              journal_fixture):
        """p4 (day only) sits after p0 (same day, 15:30) and before t0 (previous day)."""
        ids = _page_ids(_page(client, journal_fixture, limit=100))
        assert ids.index(journal_fixture.paper["p0"]) < ids.index(journal_fixture.paper["p4"])
        assert ids.index(journal_fixture.paper["p4"]) < ids.index(journal_fixture.research["t0"])

    def test_ordering_is_stable_between_requests(self, client, journal_fixture):
        first = _page_ids(_page(client, journal_fixture, limit=100))
        second = _page_ids(_page(client, journal_fixture, limit=100))
        assert first == second


# ---------------------------------------------------------------------------
# C. Every filter reaches at least one leg, and says which
# ---------------------------------------------------------------------------

class TestFiltersReachBothLegs:
    def test_rule_id_reaches_paper_fills_through_the_decision(self, client, journal_fixture):
        body = _page(client, journal_fixture, rule_id="breakout_trend", limit=100)
        assert _counts(body) == {"total": 6, "research": 2, "paper": 4}
        assert set(_page_ids(body)) == set(
            journal_fixture.ids(["t0", "t1", "p0", "p2", "p3", "p4"])
        )

    def test_rule_family_reaches_paper_fills(self, client, journal_fixture):
        body = _page(client, journal_fixture, rule_family="trend", limit=100)
        assert _counts(body) == {"total": 6, "research": 2, "paper": 4}

    def test_a_family_with_no_rules_matches_nothing_not_everything(self, client, journal_fixture):
        """An unmapped family expands to an empty id list; that must not read as "no filter"."""
        body = _page(client, journal_fixture, rule_family="__no_such_family__", limit=100)
        assert _counts(body) == {"total": 0, "research": 0, "paper": 0}
        assert body["trades"] == []

    def test_instrument_id_reaches_both_legs(self, client, journal_fixture):
        body = _page(client, journal_fixture, instrument_id=INSTRUMENT_BTC, limit=100)
        assert _counts(body) == {"total": 7, "research": 3, "paper": 4}
        assert all(row["instrument_id"] == INSTRUMENT_BTC for row in body["trades"])

    def test_research_only_keys_do_not_fabricate_an_empty_paper_leg(self, client, journal_fixture):
        """interval/pnl/exit_reason narrow the research leg and leave paper whole.

        Dropping the paper rows would look like a filter that worked; keeping them
        unfiltered and saying so is the honest half.
        """
        for key, value in (
            ("interval_minutes", 1440),
            ("pnl_min", "0"),
            ("exit_reason", "stop_loss"),
        ):
            body = _page(client, journal_fixture, limit=100, **{key: value})
            assert body["summary"]["paper_count"] == 5, key
            assert key in body["filter_scope"]["ignored_for_paper"], key
            assert any(key in note for note in body["limitations"]), key

    def test_nothing_is_declared_ignored_when_nothing_was_ignored(self, client, journal_fixture):
        body = _page(client, journal_fixture, limit=100)
        assert body["filter_scope"]["ignored_for_paper"] == []
        assert not any(
            "filtered the research leg only" in note for note in body["limitations"]
        )

    def test_research_only_keys_do_not_fabricate_a_paper_leg_that_is_not_in_scope(
        self, client, journal_fixture
    ):
        body = _page(client, journal_fixture, source="research", interval_minutes=1440, limit=100)
        assert body["filter_scope"]["ignored_for_paper"] == ["interval_minutes"]
        # ...but with the paper leg not requested at all, nothing was silently skipped.
        assert not any("filtered the research leg only" in n for n in body["limitations"])

    def test_filters_applied_echoes_every_key_the_caller_sent(self, client, journal_fixture):
        body = _page(client, journal_fixture, rule_family="trend", pnl_min="1", limit=100)
        applied = body["filters_applied"]
        assert set(applied) >= {
            "source", "instrument_id", "rule_id", "rule_family", "interval_minutes",
            "date_from", "date_to", "pnl_min", "pnl_max", "exit_reason",
        }
        assert Decimal(applied["pnl_min"]) == Decimal("1")
        assert applied["rule_family"] == "trend"


# ---------------------------------------------------------------------------
# D. Date bounds honour the granularity each fill stores
# ---------------------------------------------------------------------------

class TestDateBoundsKeepDailyFills:
    def test_a_dated_range_keeps_fills_that_recorded_only_a_day(self, client, journal_fixture):
        """The defect: ``bar_timestamp >= :from`` is NULL for a daily fill, so it vanished."""
        body = _page(
            client, journal_fixture,
            date_from="2026-10-05T00:00:00+00:00",
            date_to="2026-10-31T00:00:00+00:00",
            limit=100,
        )
        assert _counts(body) == {"total": 5, "research": 3, "paper": 2}
        assert set(_page_ids(body)) == set(journal_fixture.ids(["p1", "p3", "t1", "t2", "t3"]))

    def test_the_upper_bound_uses_the_instant_when_there_is_one_and_the_day_when_not(
        self, client, journal_fixture
    ):
        """date_to = 03 Oct midnight: p0 (15:30 that day) is out, p4 (no clock time) is in."""
        body = _page(client, journal_fixture, date_to="2026-10-03T00:00:00+00:00", limit=100)
        ids = set(_page_ids(body))
        assert ids == set(journal_fixture.ids(["t0", "p4"]))
        assert journal_fixture.paper["p0"] not in ids

    def test_the_lower_bound_mirrors_the_upper_one(self, client, journal_fixture):
        body = _page(client, journal_fixture, date_from="2026-10-03T16:00:00+00:00", limit=100)
        ids = set(_page_ids(body))
        assert journal_fixture.paper["p0"] not in ids
        assert journal_fixture.paper["p4"] in ids

    def test_day_granularity_is_stated_whenever_a_date_bound_is_sent(self, client, journal_fixture):
        body = _page(client, journal_fixture, date_from="2026-10-05T00:00:00+00:00", limit=100)
        assert any("calendar day" in note for note in body["limitations"])
        assert body["filters_applied"]["date_from"].startswith("2026-10-05")

    def test_no_date_semantics_note_is_invented_without_a_date_bound(self, client, journal_fixture):
        body = _page(client, journal_fixture, limit=100)
        assert not any("calendar day" in note for note in body["limitations"])

    def test_date_only_fill_keeps_its_day_visible_after_the_time_cell_is_empty(
        self, client, journal_fixture
    ):
        row = next(
            r for r in _page(client, journal_fixture, limit=100)["trades"]
            if r["id"] == journal_fixture.paper["p1"]
        )
        assert row["entry_time"] is None
        assert row["execution_date"] == _date_text(7)
        assert row["time_precision"] == "day"


# ---------------------------------------------------------------------------
# E. What a fill is, and what it is not
# ---------------------------------------------------------------------------

class TestFillContract:

    def test_a_fill_carries_no_per_fill_pnl_and_says_so(self, client, journal_fixture):
        rows = [r for r in _page(client, journal_fixture, limit=100)["trades"]
                if r["source"] == "paper"]
        assert rows, "the fixture produced no paper rows"
        assert all(r["net_pnl"] is None for r in rows)
        assert all(r["pnl_status"] == "fill_level_pnl_not_recorded" for r in rows)

    def test_research_rows_report_a_settled_pnl(self, client, journal_fixture):
        rows = [r for r in _page(client, journal_fixture, limit=100)["trades"]
                if r["source"] == "research"]
        assert all(r["pnl_status"] == "settled" for r in rows)
        assert all(Decimal(r["net_pnl"]) == Decimal(r["net_pnl"]) for r in rows)

    def test_enum_columns_are_names_not_python_reprs(self, client, journal_fixture):
        rows = [r for r in _page(client, journal_fixture, limit=100)["trades"]
                if r["source"] == "paper"]
        assert {r["side"] for r in rows} <= {"BUY", "SELL"}
        assert {r["order_type"] for r in rows} == {"market"}
        assert {r["decision_type"] for r in rows} <= {"entry", "exit"}

    def test_source_filter_drops_the_other_legs_aggregates_rather_than_zeroing_them(
        self, client, journal_fixture
    ):
        paper_only = _page(client, journal_fixture, source="paper", limit=100)
        assert paper_only["summary"]["research_count"] is None
        assert paper_only["summary"]["research_total_pnl"] is None
        assert paper_only["summary"]["total_count"] == 5

        research_only = _page(client, journal_fixture, source="research", limit=100)
        assert research_only["summary"]["paper_count"] is None
        assert research_only["summary"]["total_count"] == 4

    def test_rows_belong_to_the_calling_owner_only(self, client, journal_fixture):
        other = uuid.uuid4()
        db = TestSession()
        try:
            db.add(User(
                id=other,
                email=f"g35-other-{uuid.uuid4().hex[:10]}@example.test",
                password_hash="test-only",
                display_name="G35 other owner",
            ))
            db.commit()
        finally:
            db.close()
        body = _page_as(client, other, limit=100)
        assert body["trades"] == []
        assert _counts(body) == {"total": 0, "research": 0, "paper": 0}


# ---------------------------------------------------------------------------
# F. The research half of the unified view *is* the trades endpoint
# ---------------------------------------------------------------------------

class TestUnifiedMatchesTradesEndpoint:
    @pytest.mark.parametrize("params", [
        {},
        {"rule_id": "breakout_trend"},
        {"rule_family": "mean_reversion"},
        {"instrument_id": INSTRUMENT_ETH},
        {"pnl_min": "20"},
    ], ids=["none", "rule_id", "rule_family", "instrument", "pnl_min"])
    def test_same_filter_same_rows_same_numbers(self, client, journal_fixture, params):
        unified = _page(client, journal_fixture, limit=100, **params)
        trades = client.get(
            "/api/v1/journal/trades", headers=_headers(journal_fixture.user_id),
            params={**params, "limit": 100},
        )
        assert trades.status_code == 200
        body = trades.json()

        unified_research = [r for r in unified["trades"] if r["source"] == "research"]
        assert [r["id"] for r in unified_research] == [t["trade_id"] for t in body["trades"]]
        assert [r["net_pnl"] for r in unified_research] == [t["net_pnl"] for t in body["trades"]]
        assert unified["summary"]["research_count"] == body["total_count"]

    def test_family_predicate_is_shared_so_two_copies_cannot_drift(self):
        """The filter is written down once and both readers take it from there."""
        conditions = svc._journal_conditions(user_id=uuid.uuid4(), rule_family="trend")
        joined = " ".join(str(condition) for condition in conditions)
        assert "rule_id IN" in joined
        assert len(conditions) == 2  # user scope + family, nothing silently dropped

    def test_an_unmapped_family_becomes_a_condition_and_not_the_absence_of_one(self):
        """The empty id list has to reach SQL as "matches nothing".

        The zero-row proof lives in
        ``test_a_family_with_no_rules_matches_nothing_not_everything``; this is
        the other half, because a build that dropped the filter instead of
        falsifying it would still return zero rows here if the fixture happened
        to hold none of that family.
        """
        assert svc._family_rule_ids("__none__") == []
        with_family = svc._journal_conditions(user_id=uuid.uuid4(), rule_family="__none__")
        without_family = svc._journal_conditions(user_id=uuid.uuid4())
        assert len(with_family) == len(without_family) + 1


# ---------------------------------------------------------------------------
# G. The export covers the range the table shows
# ---------------------------------------------------------------------------

def _export(client, path, user_id, **params):
    resp = client.get(path, headers=_headers(user_id), params=params)
    assert resp.status_code == 200, resp.text
    rows = list(csv.reader(io.StringIO(resp.text)))
    return rows[0], rows[1:], resp.headers


class TestUnifiedCsvExport:
    def test_export_carries_both_sources_the_table_shows(self, client, journal_fixture):
        header, rows, headers = _export(client, "/api/v1/journal/unified.csv",
                                       journal_fixture.user_id)
        sources = {row[header.index("source")] for row in rows}
        assert sources == {"research", "paper"}
        assert len(rows) == 9
        assert headers[EXPORT_ROWS_MATCHED_HEADER] == "9"
        assert headers[EXPORT_ROWS_EXPORTED_HEADER] == "9"
        assert headers[EXPORT_TRUNCATED_HEADER] == "false"
        assert headers[EXPORT_ROW_LIMIT_HEADER] == str(svc.CSV_EXPORT_ROW_LIMIT)

    def test_export_honours_the_same_filters_as_the_page(self, client, journal_fixture):
        header, rows, headers = _export(
            client, "/api/v1/journal/unified.csv", journal_fixture.user_id,
            rule_id="breakout_trend",
        )
        assert len(rows) == 6
        assert headers[EXPORT_ROWS_MATCHED_HEADER] == "6"
        page = _page(client, journal_fixture, rule_id="breakout_trend", limit=100)
        exported_ids = {row[header.index("id")] for row in rows}
        assert exported_ids == set(_page_ids(page))

    def test_a_daily_fill_exports_its_day_instead_of_an_empty_time(self, client, journal_fixture):
        header, rows, _ = _export(client, "/api/v1/journal/unified.csv",
                                  journal_fixture.user_id)
        by_id = {row[header.index("id")]: row for row in rows}
        daily = by_id[journal_fixture.paper["p1"]]
        assert daily[header.index("entry_time")] == ""
        assert daily[header.index("execution_date")] == _date_text(7)
        assert daily[header.index("time_precision")] == "day"

    def test_money_columns_stay_parseable_numbers(self, client, journal_fixture):
        """A guarded ``-1000.5`` is left-aligned, un-addable text."""
        header, rows, _ = _export(client, "/api/v1/journal/unified.csv",
                                  journal_fixture.user_id)
        by_id = {row[header.index("id")]: row for row in rows}
        cell = by_id[journal_fixture.paper["p0"]][header.index("net_cash_change")]
        assert cell == "-1000.5"
        assert not cell.startswith("'")
        assert Decimal(cell) == Decimal("-1000.5")

        loss = by_id[journal_fixture.research["t1"]][header.index("net_pnl")]
        assert Decimal(loss) == Decimal("-50")

    def test_every_numeric_column_of_the_export_is_rendered_numeric(self, client, journal_fixture):
        """The renderer picks number-vs-text by column name, so the names must be registered.

        Asserted on the file's own cells rather than on the registry: a registry
        that had been emptied or renamed would otherwise agree with itself.
        """
        header, rows, _ = _export(client, "/api/v1/journal/unified.csv",
                                  journal_fixture.user_id)
        columns = [name for name in header if name in svc._UNIFIED_CSV_NUMERIC_COLUMNS]
        assert "net_pnl" in columns and "net_cash_change" in columns, (
            "the unified export has no money column, so this sweep is vacuous"
        )
        guarded = [
            (name, row[header.index(name)])
            for row in rows
            for name in columns
            if row[header.index(name)].startswith("'")
        ]
        assert guarded == [], guarded
        for row in rows:
            for name in columns:
                cell = row[header.index(name)]
                if cell:
                    assert Decimal(cell).is_finite(), (name, cell)

    def test_text_columns_keep_their_formula_guard(self, client, journal_fixture):
        header, rows, _ = _export(client, "/api/v1/journal/unified.csv",
                                  journal_fixture.user_id)
        by_id = {row[header.index("id")]: row for row in rows}
        cell = by_id[journal_fixture.paper["p4"]][header.index("fill_reason")]
        assert cell == "'=SUM(A1:A2)"

    def test_trades_csv_stays_research_only_so_the_two_buttons_are_distinguishable(
        self, client, journal_fixture
    ):
        """The unified button used to fetch this file; the scopes now differ on purpose."""
        _, rows, headers = _export(client, "/api/v1/journal/trades.csv",
                                   journal_fixture.user_id)
        assert headers[EXPORT_ROWS_MATCHED_HEADER] == "4"
        assert len(rows) == 4

    def test_export_refuses_more_rows_than_it_would_hide(self, client, journal_fixture, monkeypatch):
        """The truncation headers are the only way the file can admit it is short."""
        monkeypatch.setattr(svc, "CSV_EXPORT_ROW_LIMIT", 3)
        header, rows, headers = _export(client, "/api/v1/journal/unified.csv",
                                        journal_fixture.user_id)
        assert len(rows) == 3
        assert headers[EXPORT_ROWS_MATCHED_HEADER] == "9"
        assert headers[EXPORT_ROWS_EXPORTED_HEADER] == "3"
        assert headers[EXPORT_TRUNCATED_HEADER] == "true"
        # And what it wrote is still the newest three, not an arbitrary slice.
        exported_ids = {row[header.index("id")] for row in rows}
        assert exported_ids == set(journal_fixture.ids(FULL_ORDER[:3]))
