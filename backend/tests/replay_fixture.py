"""Real-database scaffolding for the historical replay suites.

Both replay suites used to hand ``PaperTradingEngine`` a ``MagicMock`` session
and read ``execute.call_args_list[1]`` to find the trades query.  Three things
were wrong with that, and all three showed up as a green test:

* the engine takes its sessions from ``session_factory``, so a
  ``SessionLocal`` patch never reached it — the tests were opening a real
  connection to the DSN they passed (``"postgresql://test"``) and only looked
  mock-shaped because the assertion never got far enough to notice;
* the positional index assumed the trades query was the second statement.  The
  service now reads the portfolio owner between the run and the trades, so
  index 1 is the ownership probe;
* the mock was fed 8-element tuples for a query that selects 13 named columns,
  so "the rows came back" said nothing about the SQL that produced them.

These fixtures run the real service against the database conftest already
binds, and observe what it emits with a SQLAlchemy ``before_execute``
listener.  That event fires with the bound-parameter dictionary before the
dialect rewrites it, so ``cutoff`` is still a ``datetime`` and still keyed by
name — which is what the bind-shape assertions are actually about.  A database
can tell you which rows came out; only the statement lane can tell you that
``days_back`` never reached the SQL at all.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, event, text

from app.database import SessionLocal, engine as app_engine
from app.services.paper_trading import PaperTradingEngine
from tests import uuid_identity


INSTRUMENT = "CRYPTO:KRAKEN:REPLAY/USD"
RULE_ID = "replay_rule"
INTERVAL_MINUTES = 60

# Seeded exits must be behind ``as_of`` (= replay start) and seeded entries
# inside ``days_back``, so the whole event series sits in the past.
BASE_OFFSET = timedelta(hours=30)


@dataclass
class StatementCall:
    statement: str
    parameters: dict

    @property
    def normalized(self) -> str:
        return " ".join(self.statement.split()).lower()

    def mentions(self, *fragments: str) -> bool:
        return all(f.lower() in self.normalized for f in fragments)


@contextmanager
def capture_statements():
    """Record every statement and bind dict the app sends to the test DB."""
    calls: list[StatementCall] = []

    def _listen(conn, clause, multi, params, context):
        if isinstance(params, dict):
            recorded = dict(params)
        elif isinstance(params, (list, tuple)) and params and isinstance(params[0], dict):
            recorded = dict(params[0])
        else:
            recorded = {}
        calls.append(StatementCall(str(clause), recorded))

    event.listen(app_engine, "before_execute", _listen)
    try:
        yield calls
    finally:
        event.remove(app_engine, "before_execute", _listen)


def calls_mentioning(calls, *fragments: str) -> list[StatementCall]:
    return [c for c in calls if c.mentions(*fragments)]


def find_call(calls, fragment: str) -> StatementCall:
    """First recorded statement containing ``fragment`` — fails if there is none.

    Returning ``calls[0]`` when nothing matched would turn a missing statement
    into an assertion about some other statement, so the absence is the failure.
    """
    found = calls_mentioning(calls, fragment)
    assert found, (
        f"no recorded statement contains {fragment!r}. Saw "
        f"{[c.statement[:70] for c in calls]}"
    )
    return found[0]


def trades_query(calls) -> StatementCall:
    """The one SELECT over strategy_study_trades the replay issues."""
    found = [
        c for c in calls
        if c.mentions("from strategy_study_trades") and c.statement.strip().upper().startswith("SELECT")
    ]
    assert len(found) == 1, (
        f"expected exactly one strategy_study_trades SELECT, got {len(found)}: "
        f"{[c.statement[:80] for c in found]}"
    )
    return found[0]


@dataclass
class ReplayCase:
    """A seeded user + portfolio + run + study history, ready to replay."""

    user_id: str
    portfolio_id: str
    run_id: str
    study_run_id: str
    base_time: datetime
    trade_ids: list
    engine: PaperTradingEngine = field(repr=False, default=None)

    def replay(self, days_back: int = 30, **kwargs):
        return self.engine.execute_historical_replay(
            run_id=self.run_id,
            user_id=self.user_id,
            days_back=days_back,
            **kwargs,
        )

    def cleanup(self):
        db = SessionLocal()
        try:
            # Audit rows key on the order/decision id rather than the portfolio,
            # so they have to go before the rows that name them — otherwise a
            # refusal's audit event outlives the case and a later test counting
            # refusals database-wide reads a stale row. The ids are selected
            # first and deleted by bound key because ``id::text`` is
            # PostgreSQL-only and this fixture runs on both lanes.
            owned_ids = [
                str(row[0])
                for row in db.execute(
                    text("SELECT id FROM paper_orders WHERE portfolio_id = :p "
                         "UNION ALL SELECT id FROM decision_records WHERE paper_portfolio_id = :p"),
                    {"p": self.portfolio_id},
                )
            ]
            for table, column, value in (
                ("paper_fills", "paper_order_id", self.portfolio_id),
                ("decision_records", "paper_portfolio_id", self.portfolio_id),
                ("paper_positions", "portfolio_id", self.portfolio_id),
                ("paper_orders", "portfolio_id", self.portfolio_id),
                ("paper_trading_daily_metrics", "run_id", self.run_id),
                ("paper_trading_runs", "id", self.run_id),
                ("paper_portfolios", "id", self.portfolio_id),
                ("strategy_study_trades", "run_id", self.study_run_id),
                ("strategy_study_runs", "id", self.study_run_id),
                ("users", "id", self.user_id),
            ):
                if table == "paper_fills":
                    db.execute(
                        text("DELETE FROM paper_fills WHERE paper_order_id IN "
                             "(SELECT id FROM paper_orders WHERE portfolio_id = :v)"),
                        {"v": value},
                    )
                elif table == "users":
                    # This case owns exactly one user row, but which layer wrote
                    # it varies: here it is a raw ``text()`` INSERT, so the id
                    # sits dashed, while a test that registers the caller through
                    # the ORM gets the same identity stored as 32-char hex. One
                    # spelling would leave the other behind for the next node to
                    # count.
                    clause, params = uuid_identity.match("id", self.user_id, name="owner")
                    db.execute(text(f"DELETE FROM users WHERE {clause}"), params)
                else:
                    db.execute(text(f"DELETE FROM {table} WHERE {column} = :v"), {"v": value})
            if owned_ids:
                db.execute(
                    text("DELETE FROM audit_events WHERE entity_id IN :ids").bindparams(
                        bindparam("ids", expanding=True)
                    ),
                    {"ids": owned_ids},
                )
            db.commit()
        finally:
            db.close()


def _seed_user(db, user_id: str) -> None:
    db.execute(
        text("""
            INSERT INTO users (id, email, password_hash, display_name, is_admin, is_active, created_at, updated_at)
            VALUES (:id, :email, 'test_hash', 'replay_fixture_user', false, true, :now, :now)
        """),
        {"id": user_id, "email": f"replay_{user_id[:8]}@test.com", "now": datetime.now(timezone.utc)},
    )
    db.commit()


def _seed_study_history(db, case: "ReplayCase", trades) -> None:
    now = datetime.now(timezone.utc)
    db.execute(
        text("""
            INSERT INTO strategy_study_runs (
                id, user_id, lane, research_version, instrument_id, interval_minutes,
                provider_id, data_nature, bar_count, first_open_time, last_open_time,
                settings_json, walk_forward_json, bars_json, trade_count, created_at
            ) VALUES (
                :id, :user_id, 'strategy_research_simulated', 'v1.0', :instrument, :interval,
                'test_provider', 'simulated', 100, :now, :now,
                '{}', '{}', '[]', :trade_count, :now
            )
        """),
        {
            "id": case.study_run_id,
            "user_id": case.user_id,
            "instrument": INSTRUMENT,
            "interval": INTERVAL_MINUTES,
            "now": now,
            "trade_count": len(trades),
        },
    )

    for idx, trade in enumerate(trades):
        entry_hours, exit_hours, entry_price, exit_price = trade[:4]
        direction = trade[4] if len(trade) > 4 else "long"
        trade_id = str(uuid4())
        entry_time = case.base_time + timedelta(hours=entry_hours)
        exit_time = case.base_time + timedelta(hours=exit_hours)
        case.trade_ids.append(trade_id)
        quantity = Decimal("1000")
        gross = quantity * (Decimal(str(exit_price)) - Decimal(str(entry_price)))
        if direction != "long":
            gross = -gross
        db.execute(
            text("""
                INSERT INTO strategy_study_trades (
                    id, run_id, trade_index, rule_id, rule_label_zh, exit_id, exit_label_zh,
                    direction, exit_reason, outcome, signal_time, entry_time, exit_time,
                    entry_price, exit_price, quantity, gross_pnl, commission, slippage_cost,
                    total_cost, net_pnl, cash_after, bars_held, pnl_reason_zh, pnl_reason_en, created_at
                ) VALUES (
                    :id, :run_id, :idx, :rule_id, 'Replay Rule', 'replay_exit', 'Replay Exit',
                    :direction, 'signal', :outcome, :entry_time, :entry_time, :exit_time,
                    :entry_price, :exit_price, 1000.0, :gross, 0.0, 0.0, 0.0, :gross,
                    100000.0, 1, 'Replay', 'Replay', :now
                )
            """),
            {
                "id": trade_id,
                "run_id": case.study_run_id,
                "idx": idx,
                "rule_id": RULE_ID,
                "direction": direction,
                "outcome": "win" if gross >= 0 else "loss",
                "entry_time": entry_time,
                "exit_time": exit_time,
                "entry_price": Decimal(str(entry_price)),
                "exit_price": Decimal(str(exit_price)),
                "gross": gross,
                "now": now,
            },
        )
    db.commit()


@pytest.fixture
def replay_case():
    """Factory: seed one replay case; all rows are removed at teardown.

    Teardown deletes explicitly rather than relying on conftest's SQLite
    ``DROP TABLE`` pass, because the PostgreSQL lane has no teardown DDL —
    committed rows from this fixture outlive that lane's test transaction.
    """
    created: list[ReplayCase] = []

    def _make(trades=(), initial_capital: Decimal = Decimal("100000"), user_id=None) -> ReplayCase:
        """Seed a case under ``user_id``, or under a fresh seeded user.

        A caller that already owns a ``users`` row — an HTTP test that
        registered one through the ORM, say — passes its id and this skips
        ``_seed_user``: a raw INSERT of the same identity would leave the column
        holding two spellings of one user.
        """
        if user_id is None:
            user_id = str(uuid4())
            db = SessionLocal()
            try:
                _seed_user(db, user_id)
            finally:
                db.close()

        engine = PaperTradingEngine(session_factory=SessionLocal)
        created_case = engine.create_portfolio(
            user_id=user_id,
            skill=RULE_ID,
            instrument_id=INSTRUMENT,
            interval_minutes=INTERVAL_MINUTES,
            exit_config="replay_exit",
            initial_capital=initial_capital,
        )

        case = ReplayCase(
            user_id=user_id,
            portfolio_id=created_case["portfolio_id"],
            run_id=created_case["run_id"],
            study_run_id=str(uuid4()),
            base_time=datetime.now(timezone.utc) - BASE_OFFSET,
            trade_ids=[],
            engine=engine,
        )
        created.append(case)

        db = SessionLocal()
        try:
            _seed_study_history(db, case, list(trades))
        finally:
            db.close()
        return case

    yield _make

    for case in reversed(created):
        case.cleanup()
