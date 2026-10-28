"""Intraday (5-minute) per-bar replay driven through the REAL HTTP routes.

PostgreSQL-only. ``SQLite 通過不能當作 PostgreSQL 通過``: the per-bar replay
binds one stored intraday dataset, rebuilds engine state over every prior bar,
and books paper fills with row-level timestamps and cash that only a real
PostgreSQL backend exercises end to end. The whole module skips unless a
disposable PostgreSQL database is supplied, and a guard test fails loudly if the
harness is misconfigured so that ``ALETHRA_PG_DATABASE_URL`` is set but the test
engine is still SQLite (which would prove nothing).

What this covers that the engine unit tests (``test_intraday_opening_range.py``)
do NOT: those drive ``OpeningRangeSession`` directly. This drives
``create_simulation`` + ``advance_simulation`` over HTTP, so it proves the
*wiring* — that a session binds exactly one stored dataset and the advance path
reads that same dataset (never a silently-swapped fixture), that each bar becomes
one stored ``SimulationDecision`` carrying its ``bar_timestamp``, that every rule
fill is mirrored into the linked paper portfolio through the shared
``apply_rule_decision`` path, and that the CSV/JSON journal carries the same
trade ids, per-bar times and values.

The bars are a clearly-labelled SYNTHETIC, deterministic 5-minute fixture (the
same series ``scripts/seed_e2e_intraday_flow.py`` owns). Passing tests here
demonstrate the mechanics are correct and wired; they are NOT evidence that the
rule is profitable on real markets. Historical real-market replay and live
paper trading remain separately BLOCKED on provider credentials.

Run against a disposable PostgreSQL database (never the shared runtime DB):

    ALETHRA_TEST_DB=postgresql+psycopg://u:p@127.0.0.1:PORT/alethra_intraday_test \
    ALETHRA_PG_DATABASE_URL=postgresql+psycopg://u:p@127.0.0.1:PORT/alethra_intraday_test \
        python -m pytest tests/test_intraday_simulation_api.py -p no:cacheprovider
"""

import csv
import io
import os
import uuid
from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import TEST_ENGINE, TestSession

pytestmark = pytest.mark.skipif(
    not os.environ.get("ALETHRA_PG_DATABASE_URL"),
    reason=(
        "requires a disposable PostgreSQL database (ALETHRA_PG_DATABASE_URL); "
        "the intraday per-bar replay must be proven on PostgreSQL, not SQLite"
    ),
)

# ── the synthetic 5-minute fixture this module owns ──────────────────────
INSTRUMENT = "DEMO-ALPHA"
PROVIDER = "demo-5m"          # timeframe-isolation key; distinct from daily "demo"
TIMEFRAME = "5m"
TIMEZONE = "America/New_York"  # naive exchange-local; EST (UTC-5), pre-2024-03-10 DST
START_DATE = date(2024, 3, 4)
END_DATE = date(2024, 3, 6)
SESSION_OPEN = time(9, 30)
STEP = timedelta(minutes=5)
INITIAL_CASH = 100000.0

# (open, high, low, close, volume) per bar, 09:30 open, 5-minute steps. Each day
# is engineered so one locked spec exercises a different exit branch:
#   Day 1 (03-04): OR breakout entry at bar5 open -> TARGET exit on bar6.
#   Day 2 (03-05): per-day OR reset, fresh entry -> SESSION-END forced close.
#   Day 3 (03-06): entry, next bar trades through the stop -> STOP exit.
DAYS = {
    date(2024, 3, 4): [
        (100.0, 101.0, 99.0, 100.5, 1000),
        (100.5, 102.0, 100.0, 101.0, 1000),
        (101.0, 101.5, 100.0, 101.0, 1000),
        (101.0, 103.0, 101.0, 102.5, 2000),   # signal bar: close > OR high 102 on volume
        (102.5, 103.0, 102.0, 102.8, 1000),   # entry fills here at the open 102.5
        (102.8, 105.0, 102.5, 104.9, 1000),   # target 104.55 reached intra-bar
    ],
    date(2024, 3, 5): [
        (104.0, 105.0, 103.5, 104.5, 1000),
        (104.5, 106.0, 104.0, 105.0, 1000),
        (105.0, 105.5, 104.0, 105.0, 1000),
        (105.0, 107.0, 105.0, 106.5, 2000),   # signal: close > Day-2 OR high 106
        (106.5, 107.0, 106.0, 106.8, 1000),   # entry at the open 106.5
        (106.8, 107.2, 106.4, 107.0, 1000),   # last bar of the day -> session-end close
    ],
    date(2024, 3, 6): [
        (107.0, 108.0, 106.5, 107.5, 1000),
        (107.5, 109.0, 107.0, 108.0, 1000),
        (108.0, 108.5, 107.0, 108.0, 1000),
        (108.0, 110.0, 108.0, 109.5, 2000),   # signal: close > Day-3 OR high 109
        (109.5, 110.0, 109.0, 109.8, 1000),   # entry at the open 109.5
        (109.0, 109.5, 103.0, 104.0, 1000),   # low 103 trades through stop 104.025
    ],
}


def _bars():
    """The 18 synthetic bars in chronological order, as (timestamp, o,h,l,c,v)."""
    out = []
    for day in sorted(DAYS):
        t = datetime.combine(day, SESSION_OPEN)
        for (o, h, l, c, v) in DAYS[day]:
            out.append((t, o, h, l, c, v))
            t += STEP
    return out


BARS = _bars()
BAR_TS = [b[0] for b in BARS]

# Expected per-day outcomes, derived from the fixture and the declared rule
# (opening_range_bars=3, volume_window_bars=2, volume_min_ratio=1.0,
#  stop_pct=0.05, target_pct=0.02, zero commission/slippage).
ENTRY_MIN = time(9, 50)   # bar5: the next executable event after the bar4 signal
EXIT_MIN = time(9, 55)    # bar6
DAY1 = {"date": date(2024, 3, 4), "entry": 102.5, "qty": 975, "exit": 104.55, "reason": "target"}
DAY2 = {"date": date(2024, 3, 5), "entry": 106.5, "qty": 957, "exit": 107.0, "reason": "session_end"}
DAY3 = {"date": date(2024, 3, 6), "entry": 109.5, "qty": 935, "exit": 104.025, "reason": "stop"}
FINAL_CASH = 97358.125
DECISION_TYPE_COUNTS = {
    "no_action": 12, "simulated_entry": 3, "simulated_exit": 2, "forced_exit": 1,
}


def _spec(instrument=INSTRUMENT, provider=PROVIDER, start=START_DATE, end=END_DATE,
          timeframe=TIMEFRAME, **overrides):
    spec = {
        "template_type": "opening_range_breakout",
        "timeframe": timeframe,
        "asset_ids": [instrument],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "initial_cash": int(INITIAL_CASH),
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "signal_parameters": {
            "opening_range_bars": 3,
            "volume_window_bars": 2,
            "volume_min_ratio": 1.0,
        },
        "risk_parameters": {"stop_pct": 0.05, "target_pct": 0.02},
        "synthetic": True,
        "dataset": {
            "provider": provider, "timeframe": timeframe, "timezone": TIMEZONE,
            "checksum": "sha256:synthetic-test-fixture", "bar_count": 0,
        },
    }
    spec.update(overrides)
    return spec


def _seed_locked_version(bars, *, instrument=INSTRUMENT, provider=PROVIDER,
                         start=START_DATE, end=END_DATE, spec=None):
    """Create a locked experiment+version owned by the test user, plus stored bars.

    The intraday spec (``opening_range_breakout`` / ``5m``) cannot be authored
    through ``POST /experiments`` — the frozen ``StrategySpec`` schema only admits
    the three daily templates and ``frequency ^1d$``. So, exactly like
    ``seed_e2e_intraday_flow.py``, the locked version is written through the ORM.
    """
    from app.models.experiment import Experiment, ExperimentStatus, ResearchType, Visibility
    from app.models.experiment_version import ExperimentVersion
    from app.models.market_data import MarketDataBar, MarketDataMetadata
    from app.models.user import User

    spec = spec or _spec(instrument, provider, start, end)
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        exp = Experiment(
            id=uuid.uuid4(), owner_id=user.id,
            title="Intraday rule-flow API test (synthetic)",
            hypothesis="5-minute opening-range breakout, auto entry/target/stop/session-end",
            research_type=ResearchType.quantitative,
            status=ExperimentStatus.locked,
            visibility=Visibility.private,
            current_version_id=None,
        )
        db.add(exp)
        db.flush()
        version = ExperimentVersion(
            id=uuid.uuid4(), experiment_id=exp.id, version_number=1,
            strategy_spec=spec, created_by=user.id,
            locked_at=datetime(2024, 3, 7, 9, 0, 0), locked_by=user.id,
            lock_reason="rules frozen before replay",
            dataset_cutoff_at=end.isoformat(), data_timezone=TIMEZONE,
            strategy_config_hash="synthetic",
        )
        db.add(version)
        db.flush()
        exp.current_version_id = version.id
        for (ts, o, h, l, c, v) in bars:
            db.add(MarketDataBar(
                id=uuid.uuid4(), instrument_id=instrument, provider_id=provider,
                bar_date=ts.date(), bar_timestamp=ts,
                open=o, high=h, low=l, close=c, volume=v,
            ))
        db.add(MarketDataMetadata(
            id=uuid.uuid4(), instrument_id=instrument, provider_id=provider,
            data_nature="synthetic", timezone=TIMEZONE, currency="USD",
            first_bar_date=bars[0][0].date(), last_bar_date=bars[-1][0].date(),
            total_bars=len(bars), health_status="ok",
        ))
        db.commit()
        return str(version.id)
    finally:
        db.close()


# ── HTTP helpers (mirror test_rule_triggered_paper_execution.py) ─────────
def _start_session(client, headers, version_id, cash=INITIAL_CASH, provider_id=PROVIDER):
    resp = client.post(
        f"/api/v1/simulations/versions/{version_id}", headers=headers,
        json={"initial_virtual_cash": cash, "provider_id": provider_id},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _link_portfolio(client, headers, session_id, cash=INITIAL_CASH):
    resp = client.post("/api/v1/paper-portfolio", headers=headers, json={
        "initial_virtual_cash": cash, "currency": "USD",
        "simulation_session_id": session_id,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _advance(client, headers, session_id):
    return client.post(f"/api/v1/simulations/{session_id}/advance", headers=headers)


def _run_to_completion(client, headers, session_id, limit=30):
    """Advance one bar at a time until the replay reports completion (HTTP 400).

    No sleeping: each step is one completed request over a fixed bar series, so
    the loop is deterministic and terminates on the replay's own end-of-data 400.
    Returns the list of per-bar decisions in bar order.
    """
    decisions = []
    for _ in range(limit):
        resp = _advance(client, headers, session_id)
        if resp.status_code == 400:
            assert "complete" in resp.json()["detail"].lower(), resp.json()
            return decisions
        assert resp.status_code == 200, resp.text
        decisions.append(resp.json()["decision"])
    raise AssertionError(f"replay did not complete within {limit} advances")


def _session(client, headers, session_id):
    resp = client.get(f"/api/v1/simulations/{session_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _decisions(client, headers, session_id):
    resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _portfolio(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _orders(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _fills(client, headers, portfolio_id, order_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _all_fills(client, headers, portfolio_id):
    out = []
    for o in _orders(client, headers, portfolio_id):
        for f in _fills(client, headers, portfolio_id, o["id"]):
            out.append((o, f))
    out.sort(key=lambda of: of[1]["bar_timestamp"] or "")
    return out


def _journal(client, headers, portfolio_id):
    resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _journal_csv(client, headers, portfolio_id):
    resp = client.get(
        f"/api/v1/paper-portfolio/{portfolio_id}/journal?format=csv", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8"))))
    header, body = rows[0], rows[1:]
    return header, [dict(zip(header, r)) for r in body]


def _by_type(decisions):
    out = {}
    for d in decisions:
        out.setdefault(d["decision_type"], []).append(d)
    return out


# ── fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def pg_url():
    return os.environ["ALETHRA_PG_DATABASE_URL"]


@pytest.fixture
def engine_dialect():
    return TEST_ENGINE.dialect.name


@pytest.fixture
def intraday_version(auth_headers):
    """The 18-bar synthetic dataset + its locked intraday version (auth_headers
    registers test@alethra.io and so guarantees the owner row exists)."""
    return _seed_locked_version(BARS)


class TestGuardAgainstHarnessMisconfiguration:
    """A PostgreSQL-only file must not silently run on SQLite."""

    def test_engine_is_postgres(self, pg_url, engine_dialect):
        assert engine_dialect == "postgresql", (
            "ALETHRA_PG_DATABASE_URL is set but the test engine is "
            f"{engine_dialect}; these tests would prove nothing about per-bar "
            "replay, row timestamps or PostgreSQL cash accounting"
        )


class TestDataBindingIsOneDataset:
    """Create validates stored bars and advance must read the SAME bound bars."""

    def test_session_binds_the_intraday_provider_and_timeframe(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        assert session["timeframe"] == TIMEFRAME
        assert session["provider_id"] == PROVIDER
        assert session["provider_confirmed"] is True
        assert session["status"] == "pending"
        assert session["current_bar_timestamp"] is None

    def test_create_refuses_when_several_providers_exist_and_none_is_chosen(
        self, client, auth_headers, intraday_version
    ):
        # Seed a second, DAILY provider over the same instrument/window so two
        # providers have stored data. Without an explicit provider_id the session
        # must refuse rather than guess which dataset to bind.
        from app.models.market_data import MarketDataBar
        db = TestSession()
        try:
            for day in (date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)):
                db.add(MarketDataBar(
                    id=uuid.uuid4(), instrument_id=INSTRUMENT, provider_id="demo",
                    bar_date=day, bar_timestamp=datetime.combine(day, datetime.min.time()),
                    open=999.0, high=999.0, low=999.0, close=999.0, volume=1,
                ))
            db.commit()
        finally:
            db.close()

        resp = client.post(
            f"/api/v1/simulations/versions/{intraday_version}", headers=auth_headers,
            json={"initial_virtual_cash": INITIAL_CASH},
        )
        assert resp.status_code == 400, resp.text
        assert "Multiple providers" in resp.json()["detail"]

    def test_advance_reads_the_bound_bars_not_a_swapped_daily_fixture(
        self, client, auth_headers, intraday_version
    ):
        # A decoy DAILY provider at a price (999.0) that appears nowhere in the
        # intraday fixture. The session binds demo-5m explicitly; every fill must
        # price off the 5-minute bars, never the decoy, and every decision must
        # carry a 5-minute bar timestamp (not a midnight daily stamp).
        from app.models.market_data import MarketDataBar
        db = TestSession()
        try:
            for day in (date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)):
                db.add(MarketDataBar(
                    id=uuid.uuid4(), instrument_id=INSTRUMENT, provider_id="demo",
                    bar_date=day, bar_timestamp=datetime.combine(day, datetime.min.time()),
                    open=999.0, high=999.0, low=999.0, close=999.0, volume=1,
                ))
            db.commit()
        finally:
            db.close()

        session = _start_session(client, auth_headers, intraday_version, provider_id=PROVIDER)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        decisions = _run_to_completion(client, auth_headers, session_id)

        # 18 advances over exactly the 18 bound 5-minute bars, in timestamp order.
        assert len(decisions) == 18
        assert [d["bar_timestamp"] for d in decisions] == [ts.isoformat() for ts in BAR_TS]

        fills = _all_fills(client, auth_headers, portfolio_id)
        assert len(fills) == 6
        for _order, fill in fills:
            assert fill["observed_price"] != 999.0
            assert fill["execution_price_before_costs"] != 999.0
            # A daily bar would stamp midnight; an intraday fill carries its bar time.
            assert fill["bar_timestamp"].endswith(":50:00") or fill["bar_timestamp"].endswith(":55:00")


class TestPerBarReplayChain:
    """The full per-bar replay: entry, target, session-end, stop, completion."""

    def test_every_branch_fires_at_the_right_bar_and_price(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)

        decisions = _run_to_completion(client, auth_headers, session_id)
        by_type = _by_type(decisions)
        assert {k: len(v) for k, v in by_type.items()} == DECISION_TYPE_COUNTS

        entries = sorted(by_type["simulated_entry"], key=lambda d: d["bar_timestamp"])
        assert [e["bar_timestamp"] for e in entries] == [
            datetime.combine(DAY1["date"], ENTRY_MIN).isoformat(),
            datetime.combine(DAY2["date"], ENTRY_MIN).isoformat(),
            datetime.combine(DAY3["date"], ENTRY_MIN).isoformat(),
        ]
        for entry, day in zip(entries, (DAY1, DAY2, DAY3)):
            detail = entry["decision_detail"]
            assert detail["side"] == "BUY"
            assert detail["timeframe"] == TIMEFRAME
            assert detail["execution_price"] == pytest.approx(day["entry"])
            assert detail["quantity"] == day["qty"]
            assert entry["snapshot_date"] == day["date"].isoformat()

        # Target exit (Day 1) and stop exit (Day 3) are simulated_exit; the
        # session-end close (Day 2) is a forced_exit.
        sim_exits = sorted(by_type["simulated_exit"], key=lambda d: d["bar_timestamp"])
        assert len(sim_exits) == 2
        target_exit, stop_exit = sim_exits
        assert target_exit["decision_detail"]["exit_reason"] == "target"
        assert target_exit["decision_detail"]["execution_price"] == pytest.approx(DAY1["exit"])
        assert target_exit["bar_timestamp"] == datetime.combine(DAY1["date"], EXIT_MIN).isoformat()
        assert stop_exit["decision_detail"]["exit_reason"] == "stop"
        assert stop_exit["decision_detail"]["execution_price"] == pytest.approx(DAY3["exit"])
        assert stop_exit["bar_timestamp"] == datetime.combine(DAY3["date"], EXIT_MIN).isoformat()

        forced = by_type["forced_exit"]
        assert len(forced) == 1
        assert forced[0]["decision_detail"]["exit_reason"] == "session_end"
        assert forced[0]["decision_detail"]["execution_price"] == pytest.approx(DAY2["exit"])
        assert forced[0]["bar_timestamp"] == datetime.combine(DAY2["date"], EXIT_MIN).isoformat()

        # The account mirrors the replay: six fills, flat at the end, cash exact.
        fills = _all_fills(client, auth_headers, portfolio_id)
        assert len(fills) == 6
        reasons = sorted(f["simulated_fill_reason"] for _o, f in fills)
        assert reasons == sorted([
            "rule_entry", "rule_entry", "rule_entry",
            "rule_target", "rule_session_end", "rule_stop",
        ])
        account = _portfolio(client, auth_headers, portfolio_id)
        assert account["current_virtual_cash"] == pytest.approx(FINAL_CASH, abs=1e-6)
        assert account["total_unrealized_pnl"] == pytest.approx(0.0, abs=1e-6)

    def test_signal_forms_on_a_completed_bar_and_fills_on_the_next_bar(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        _link_portfolio(client, auth_headers, session_id)
        decisions = _run_to_completion(client, auth_headers, session_id)

        # Day 1: bars 09:30-09:40 form the opening range (no entry — unqualified),
        # bar 09:45 closes above the OR high and forms the signal but does NOT
        # fill, and bar 09:50 is the next executable event that fills at its open.
        d1 = [d for d in decisions if d["snapshot_date"] == DAY1["date"].isoformat()]
        assert len(d1) == 6
        warmup, signal_bar, entry_bar = d1[0], d1[3], d1[4]
        assert warmup["decision_type"] == "no_action"          # OR not yet complete
        assert signal_bar["decision_type"] == "no_action"      # signal formed, not filled
        assert signal_bar["bar_timestamp"] == datetime.combine(DAY1["date"], time(9, 45)).isoformat()
        assert entry_bar["decision_type"] == "simulated_entry"
        # The proof is the timestamp, not the price: the entry is booked on the
        # bar AFTER the signal bar. (In this fixture bar5.open == bar4.close, so
        # price alone could not distinguish next-open from signal-close.)
        assert entry_bar["bar_timestamp"] > signal_bar["bar_timestamp"]
        assert entry_bar["bar_timestamp"] == datetime.combine(DAY1["date"], ENTRY_MIN).isoformat()
        assert entry_bar["decision_detail"]["execution_price"] == pytest.approx(DAY1["entry"])

    def test_opening_range_resets_each_day_and_never_carries_the_prior_day(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        _link_portfolio(client, auth_headers, session_id)
        decisions = _run_to_completion(client, auth_headers, session_id)

        # Day 2 opens at 104.0, already above Day 1's OR high (102). If the OR had
        # carried over, bar1 would signal and bar2 (09:35) would fill at 104.5.
        # Instead Day 2 builds its OWN OR (high 106) and only enters at 09:50.
        d2 = [d for d in decisions if d["snapshot_date"] == DAY2["date"].isoformat()]
        assert [d["decision_type"] for d in d2[:4]] == ["no_action"] * 4
        assert d2[4]["decision_type"] == "simulated_entry"
        assert d2[4]["bar_timestamp"] == datetime.combine(DAY2["date"], ENTRY_MIN).isoformat()
        # Entry price is Day 2's bar5 open (106.5), not Day 1's OR high (102) and
        # not the early Day-2 price (104.5) a carried OR would have produced.
        assert d2[4]["decision_detail"]["execution_price"] == pytest.approx(DAY2["entry"])
        assert d2[4]["decision_detail"]["execution_price"] != pytest.approx(104.5)

        # Day 3 likewise builds its own OR (high 109) and enters at 109.5.
        d3 = [d for d in decisions if d["snapshot_date"] == DAY3["date"].isoformat()]
        assert [d["decision_type"] for d in d3[:4]] == ["no_action"] * 4
        assert d3[4]["decision_detail"]["execution_price"] == pytest.approx(DAY3["entry"])

    def test_bar_timestamps_are_naive_exchange_local_and_bucket_by_local_date(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        _link_portfolio(client, auth_headers, session_id)
        decisions = _run_to_completion(client, auth_headers, session_id)

        # The dataset window (2024-03-04..06) sits entirely before the 2024 US
        # DST start (2024-03-10), so it is EST (UTC-5) with no transition inside.
        # Timestamps are naive exchange-local; per-day bucketing uses the local
        # calendar date, which is what resets the opening range.
        for d in decisions:
            ts = datetime.fromisoformat(d["bar_timestamp"])
            assert ts.tzinfo is None, "exchange-local timestamps must stay naive"
            assert ts.date().isoformat() == d["snapshot_date"]
            assert ts.time() in {time(9, m) for m in (30, 35, 40, 45, 50, 55)}
        # Six bars per local date, three local dates.
        per_date = {}
        for d in decisions:
            per_date[d["snapshot_date"]] = per_date.get(d["snapshot_date"], 0) + 1
        assert per_date == {
            DAY1["date"].isoformat(): 6,
            DAY2["date"].isoformat(): 6,
            DAY3["date"].isoformat(): 6,
        }

    def test_completion_is_durable_and_rereadable(self, client, auth_headers, intraday_version):
        # "Reload persists the same session/trades" at the storage layer: after
        # the replay completes, a fresh read of the stored state returns the same
        # session, cursor, decisions and fills (the browser reload is covered by
        # the Playwright intraday spec).
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        _run_to_completion(client, auth_headers, session_id)

        reloaded = _session(client, auth_headers, session_id)
        assert reloaded["id"] == session_id
        assert reloaded["status"] == "completed"
        assert reloaded["timeframe"] == TIMEFRAME
        assert reloaded["current_bar_timestamp"] == BAR_TS[-1].isoformat()
        assert reloaded["total_decisions"] == 18

        stored = _decisions(client, auth_headers, session_id)
        assert len(stored) == 18
        assert sorted(d["step_number"] for d in stored) == list(range(1, 19))
        assert [d["bar_timestamp"] for d in sorted(stored, key=lambda x: x["step_number"])] == [
            ts.isoformat() for ts in BAR_TS
        ]
        assert len(_all_fills(client, auth_headers, portfolio_id)) == 6


class TestSameBarAmbiguity:
    """A bar that touches both stop and target resolves stop-first, through the API."""

    def test_same_bar_stop_and_target_exits_once_at_the_stop(
        self, client, auth_headers
    ):
        # A separate one-day fixture whose final bar spans BOTH the stop (97.375)
        # and the target (104.55) after an entry at 102.5. The shared exit rule
        # resolves the ambiguity as stop-first; the API path must book exactly one
        # exit at the stop, never two fills and never the target.
        amb_bars = []
        t = datetime.combine(date(2024, 3, 7), SESSION_OPEN)
        for (o, h, l, c, v) in [
            (100.0, 101.0, 99.0, 100.5, 1000),
            (100.5, 102.0, 100.0, 101.0, 1000),
            (101.0, 101.5, 100.0, 101.0, 1000),
            (101.0, 103.0, 101.0, 102.5, 2000),   # signal
            (102.5, 103.0, 102.0, 102.8, 1000),   # entry at the open 102.5
            (103.0, 105.0, 97.0, 100.0, 1000),    # high>=target AND low<=stop -> stop-first
        ]:
            amb_bars.append((t, o, h, l, c, v))
            t += STEP
        version_id = _seed_locked_version(
            amb_bars, instrument="DEMO-AMB",
            start=date(2024, 3, 7), end=date(2024, 3, 7),
        )

        session = _start_session(client, auth_headers, version_id)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        decisions = _run_to_completion(client, auth_headers, session_id)

        exits = [d for d in decisions if d["decision_detail"].get("side") == "SELL"]
        assert len(exits) == 1, exits
        assert exits[0]["decision_detail"]["exit_reason"] == "stop"
        assert exits[0]["decision_detail"]["execution_price"] == pytest.approx(97.375)

        sells = [o for o in _orders(client, auth_headers, portfolio_id) if o["side"] == "SELL"]
        assert len(sells) == 1
        fill = _fills(client, auth_headers, portfolio_id, sells[0]["id"])[0]
        assert fill["simulated_fill_reason"] == "rule_stop"
        assert fill["execution_price_before_costs"] == pytest.approx(97.375)
        # One exit, not a stop and a target on the same bar.
        assert len(_all_fills(client, auth_headers, portfolio_id)) == 2  # 1 entry + 1 exit


class TestGranularityGuard:
    """A daily bound dataset must never be replayed as an intraday session."""

    def test_create_refuses_daily_bars_for_an_intraday_timeframe(
        self, client, auth_headers
    ):
        # One midnight bar per date is daily data; feeding it to a 5-minute engine
        # would fabricate an intraday session that never existed.
        daily_bars = [
            (datetime.combine(date(2024, 3, 4), datetime.min.time()), 100.0, 101.0, 99.0, 100.5, 1000),
            (datetime.combine(date(2024, 3, 5), datetime.min.time()), 100.5, 102.0, 100.0, 101.0, 1000),
            (datetime.combine(date(2024, 3, 6), datetime.min.time()), 101.0, 101.5, 100.0, 101.0, 1000),
        ]
        version_id = _seed_locked_version(
            daily_bars, instrument="DEMO-DAILYONLY", provider="demo",
            start=date(2024, 3, 4), end=date(2024, 3, 6),
        )
        resp = client.post(
            f"/api/v1/simulations/versions/{version_id}", headers=auth_headers,
            json={"initial_virtual_cash": INITIAL_CASH, "provider_id": "demo"},
        )
        assert resp.status_code == 400, resp.text
        assert "daily" in resp.json()["detail"].lower()


class TestJournalAndCsvConsistency:
    """The CSV/JSON journal carries the same trade ids, per-bar times and values."""

    def test_csv_rows_match_the_booked_fills_id_time_and_value(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        _run_to_completion(client, auth_headers, session_id)

        header, rows = _journal_csv(client, auth_headers, portfolio_id)
        assert "bar_timestamp" in header
        fill_rows = [r for r in rows if r["record_type"] == "fill"]
        assert len(fill_rows) == 6

        api_fills = _all_fills(client, auth_headers, portfolio_id)
        by_fill_id = {f["id"]: (o, f) for o, f in api_fills}
        for row in fill_rows:
            assert row["fill_id"] in by_fill_id, row
            order, fill = by_fill_id[row["fill_id"]]
            assert row["order_id"] == order["id"]
            assert row["side"] == order["side"]
            assert row["asset_id"] == INSTRUMENT
            assert row["actor_type"] == "system"
            # Same per-bar time as the stored fill, and same values.
            assert row["bar_timestamp"] == fill["bar_timestamp"]
            assert row["execution_date"] == fill["execution_date"]
            assert float(row["execution_price_before_costs"]) == pytest.approx(
                fill["execution_price_before_costs"]
            )
            assert float(row["observed_price"]) == pytest.approx(fill["observed_price"])
            assert float(row["quantity"]) == pytest.approx(fill["quantity"])
            assert row["simulated_fill_reason"] == fill["simulated_fill_reason"]

        # An intraday entry and its same-day exit share execution_date but are
        # told apart by bar_timestamp — the reason the column exists.
        day1_rows = [r for r in fill_rows if r["execution_date"] == DAY1["date"].isoformat()]
        assert len(day1_rows) == 2
        assert {r["bar_timestamp"] for r in day1_rows} == {
            datetime.combine(DAY1["date"], ENTRY_MIN).isoformat(),
            datetime.combine(DAY1["date"], EXIT_MIN).isoformat(),
        }

    def test_json_export_declares_the_bar_timestamp_column(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        _run_to_completion(client, auth_headers, session_id)

        resp = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/journal?format=json", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "bar_timestamp" in body["columns"]
        assert "bar_timestamp" in body["column_semantics"]
        assert body["row_count"] == 6
        entry = next(r for r in body["records"] if r["simulated_fill_reason"] == "rule_entry")
        assert entry["bar_timestamp"] == datetime.combine(DAY1["date"], ENTRY_MIN).isoformat()

    def test_journal_records_carry_the_system_actor_and_exit_reasons(
        self, client, auth_headers, intraday_version
    ):
        session = _start_session(client, auth_headers, intraday_version)
        session_id = session["id"]
        portfolio_id = _link_portfolio(client, auth_headers, session_id)
        _run_to_completion(client, auth_headers, session_id)

        journal = _journal(client, auth_headers, portfolio_id)
        assert len(journal) == 6
        assert all(r["actor_type"] == "system" for r in journal)
        assert all(r["simulation_session_id"] == session_id for r in journal)
        assert sorted(r["decision_type"] for r in journal) == ["entry"] * 3 + ["exit"] * 3
        exit_reasons = sorted(
            (r["risk_flags"] or {}).get("exit_reason") for r in journal
            if r["decision_type"] == "exit"
        )
        assert exit_reasons == ["session_end", "stop", "target"]


class TestBatchBacktestGuard:
    """The daily batch backtest must not pretend to run an intraday spec."""

    def test_run_backtest_refuses_an_intraday_timeframe(self):
        from app.engine.backtest import run_backtest

        result = run_backtest(_spec())
        assert result["run_status"] == "error"
        assert any("per-bar forward replay" in e for e in result["errors"]), result["errors"]
