"""Seed the disposable E2E order-lifecycle database (test-only, synthetic).

Everything the tracked Playwright order-lifecycle journey needs, at the DATABASE
level only, so every product step it asserts can be a real click:

  * one synthetic account (get-or-create by E2E_EMAIL)
  * one locked experiment + version whose strategy is deliberately PASSIVE
    (price_change_fixed_hold with threshold_pct=0.99 — a 99% drop in three
    sessions never happens in this fixture), so the only orders in the book can
    be the human ones the journey submits
  * its OWN MarketDataBar rows for DEMO-ALPHA over 2024-02-01..2024-02-13
  * a watchlist containing DEMO-ALPHA, because the workspace switches the plotted
    symbol through the watchlist, and ⑦ only accepts a submission when the
    session's instrument equals that symbol

Why this window and not the rule-flow seed's January one:

  * 2024-01-02..2024-01-31 (provider demo) belongs to ``seed_e2e_rule_flow.py``
    and 2024-03-04..03-06 (provider demo-5m) to ``seed_e2e_intraday_flow.py``.
    This seed writes 2024-02-01..2024-02-13 under provider ``demo``: disjoint from
    both, so re-seeding one journey never deletes another journey's bars, and the
    Feb gaps (Feb 03/04/10/11 are weekends) give the lifecycle a real calendar to
    wait for — a decision taken on Feb 01 is due on Feb 02, and settlement lands
    on the NEXT stored session, never on a guessed calendar.
  * The stored calendar has 9 bars, which leaves room to walk the cursor, submit
    an order after it, cancel one before execution, fill one, and still have a
    pending order left over when the dataset runs out (the expiry case).

``MarketDataMetadata`` is one row per (instrument, provider) and is shared with
the daily rule-flow seed, so this seed never deletes it: it upserts the row from
a COUNT/MIN/MAX over the bars that are actually in the database. Deleting it (or
recreating it with only this seed's window) would mislabel the other seed's bars.

Provenance is printed: timeframe, provider, timezone and a sha256 checksum over
the exact bar series written. The bars mirror app.data.demo_data.DEMO_DATA so the
stored calendar and the prices the replay books agree; they are synthetic and are
never presented as real-market data.

Refuses to run unless DATABASE_URL points at the disposable E2E database. Ids
derive from uuid5(E2E_MARKER) and every run deletes the rows it owns (fills ->
positions -> orders -> paper/simulation decision records -> portfolios -> sessions
-> version -> experiment -> bars) in FK-safe order before recreating them, so the
seed is idempotent and also clears leftover lifecycle state from a previous run.
"""
import hashlib
import os
import sys
import uuid
from datetime import date, datetime

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_NAME = "alethra_e2e"
E2E_MARKER = "alethra-e2e-order-lifecycle"

if not DB.startswith("postgresql"):
    sys.exit("Refusing: DATABASE_URL must be a postgresql URL.")
if E2E_DB_NAME not in DB:
    sys.exit(f"Refusing: DATABASE_URL must target the disposable '{E2E_DB_NAME}' database.")

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, delete, inspect, select, update
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.data.demo_data import DEMO_DATA
from app.models.experiment import Experiment, ExperimentStatus, ResearchType, Visibility
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.paper_portfolio import (
    DecisionRecord,
    OutcomeEvaluation,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
)
from app.models.simulation import SimulationDecision, SimulationSession
from app.models.user import User, UserType
from app.models.watchlist import Watchlist, WatchlistItem
from e2e_shared_metadata import upsert_shared_metadata

EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")
if not EMAIL or not PASSWORD:
    sys.exit("Refusing: E2E_EMAIL and E2E_PASSWORD must come from the environment.")

INSTRUMENT = "DEMO-ALPHA"
PROVIDER = "demo"
TIMEFRAME = "1d"          # the forward replay advances one stored session per step
TIMEZONE = "UTC"          # bar_date is a naive calendar date; bar_timestamp is midnight UTC
START_DATE = date(2024, 2, 1)
END_DATE = date(2024, 2, 13)
INITIAL_CASH = 100000
COMMISSION_RATE = 0.001
SLIPPAGE_RATE = 0.001
EXPERIMENT_TITLE = "E2E 訂單生命週期流程（僅供自動化測試）"
WATCHLIST_NAME = "E2E 生命週期"

engine = create_engine(DB)
tables = set(inspect(engine).get_table_names())
for needed in (
    "users", "experiments", "experiment_versions", "simulation_sessions",
    "simulation_decisions", "paper_portfolios", "paper_orders", "paper_fills",
    "decision_records", "market_data_bars", "market_data_metadata",
    "watchlists", "watchlist_items",
):
    assert needed in tables, f"table {needed} missing - migrations did not run"


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{E2E_MARKER}:{name}")


# ---- the exact synthetic bar series this seed owns (mirrors the demo fixture) ----
BARS = []
for date_str, prices in sorted(DEMO_DATA[INSTRUMENT].items()):
    d = date.fromisoformat(date_str)
    if not (START_DATE <= d <= END_DATE):
        continue
    BARS.append({
        "date": d,
        "timestamp": datetime.combine(d, datetime.min.time()),
        "open": float(prices["open"]),
        "high": float(prices["high"]),
        "low": float(prices["low"]),
        "close": float(prices["close"]),
        "volume": int(prices.get("volume") or 1_000_000),
    })
if len(BARS) < 3:
    sys.exit(f"Refusing: demo fixture has too few {INSTRUMENT} bars in {START_DATE}..{END_DATE}.")

_checksum_src = "\n".join(
    f"{b['date'].isoformat()},{b['open']},{b['high']},{b['low']},{b['close']},{b['volume']}"
    for b in BARS
)
DATASET_CHECKSUM = "sha256:" + hashlib.sha256(_checksum_src.encode("utf-8")).hexdigest()

with Session(engine) as session:
    # ---- FK-safe reset of everything this seed owns (idempotent re-run) ----
    exp_id = sid("experiment")
    ver_ids = [
        row[0]
        for row in session.execute(
            select(ExperimentVersion.id).where(ExperimentVersion.experiment_id == exp_id)
        ).all()
    ]
    sess_ids = [
        row[0]
        for row in session.execute(
            select(SimulationSession.id).where(
                SimulationSession.experiment_version_id.in_(ver_ids)
            )
        ).all()
    ] if ver_ids else []
    pf_ids = [
        row[0]
        for row in session.execute(
            select(PaperPortfolio.id).where(
                PaperPortfolio.simulation_session_id.in_(sess_ids)
            )
        ).all()
    ] if sess_ids else []
    order_ids = [
        row[0]
        for row in session.execute(
            select(PaperOrder.id).where(PaperOrder.portfolio_id.in_(pf_ids))
        ).all()
    ] if pf_ids else []
    if order_ids:
        session.execute(delete(PaperFill).where(PaperFill.paper_order_id.in_(order_ids)))
    if pf_ids:
        session.execute(delete(PaperPosition).where(PaperPosition.portfolio_id.in_(pf_ids)))
        session.execute(delete(PaperOrder).where(PaperOrder.portfolio_id.in_(pf_ids)))
        session.execute(
            delete(OutcomeEvaluation).where(
                OutcomeEvaluation.decision_id.in_(
                    select(DecisionRecord.id).where(DecisionRecord.paper_portfolio_id.in_(pf_ids))
                )
            )
        )
        session.execute(delete(DecisionRecord).where(DecisionRecord.paper_portfolio_id.in_(pf_ids)))
    if sess_ids:
        session.execute(delete(DecisionRecord).where(DecisionRecord.simulation_session_id.in_(sess_ids)))
        session.execute(delete(PaperPortfolio).where(PaperPortfolio.simulation_session_id.in_(sess_ids)))
        session.execute(delete(SimulationDecision).where(SimulationDecision.session_id.in_(sess_ids)))
        session.execute(delete(SimulationSession).where(SimulationSession.id.in_(sess_ids)))
    # Break the circular FK experiments.current_version_id -> experiment_versions.id.
    session.execute(update(Experiment).where(Experiment.id == exp_id).values(current_version_id=None))
    if ver_ids:
        session.execute(delete(ExperimentVersion).where(ExperimentVersion.id.in_(ver_ids)))
    session.execute(delete(Experiment).where(Experiment.id == exp_id))
    # Bars: only this seed's own instrument/provider/window. Other seeds own other
    # windows on the same instrument, and metadata is shared per (instrument,
    # provider), so it is recomputed below rather than deleted here.
    session.execute(
        delete(MarketDataBar).where(
            MarketDataBar.instrument_id == INSTRUMENT,
            MarketDataBar.provider_id == PROVIDER,
            MarketDataBar.bar_date >= START_DATE,
            MarketDataBar.bar_date <= END_DATE,
        )
    )
    session.commit()

    # ---- account: get-or-create the task's own synthetic login ----
    user = session.execute(select(User).where(User.email == EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(
            id=sid("user"),
            email=EMAIL,
            password_hash=get_password_hash(PASSWORD),
            display_name="E2E order lifecycle (synthetic)",
            user_type=UserType.individual,
        )
        session.add(user)
        session.flush()

    # ---- locked experiment + the deliberately passive version ----
    experiment = Experiment(
        id=exp_id,
        owner_id=user.id,
        title=EXPERIMENT_TITLE,
        hypothesis="驗證訂單生命週期：人工掛單先 pending，市場走到下一根 K 線才成交並結算",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
        visibility=Visibility.private,
        current_version_id=None,
    )
    session.add(experiment)
    session.flush()

    strategy_spec = {
        # threshold_pct=0.99 makes the rule passive on this fixture (no 99% drop in
        # three sessions), so every order in the journey's book is a human one.
        "template_type": "price_change_fixed_hold",
        "asset_ids": [INSTRUMENT],
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "initial_cash": INITIAL_CASH,
        "signal_parameters": {"lookback_days": 3, "threshold_pct": 0.99, "holding_days": 5},
        "commission_rate": COMMISSION_RATE,
        "slippage_rate": SLIPPAGE_RATE,
        "synthetic": True,
        "e2e_marker": E2E_MARKER,
        "dataset": {
            "provider": PROVIDER,
            "timeframe": TIMEFRAME,
            "timezone": TIMEZONE,
            "checksum": DATASET_CHECKSUM,
            "bar_count": len(BARS),
        },
    }
    version = ExperimentVersion(
        id=sid("version"),
        experiment_id=experiment.id,
        version_number=1,
        strategy_spec=strategy_spec,
        created_by=user.id,
        locked_at=datetime(2024, 2, 1, 9, 0, 0),
        locked_by=user.id,
        lock_reason="E2E order-lifecycle regression: rules frozen before replay",
        dataset_cutoff_at=END_DATE.isoformat(),
        data_timezone=TIMEZONE,
        strategy_config_hash=DATASET_CHECKSUM.split(":", 1)[1][:16],
    )
    session.add(version)
    session.flush()
    experiment.current_version_id = version.id

    # ---- the seed's own stored bars (session creation validates these) ----
    for b in BARS:
        session.add(MarketDataBar(
            id=sid(f"bar:{b['date'].isoformat()}"),
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=b["date"],
            bar_timestamp=b["timestamp"],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            volume=b["volume"],
        ))

    # ---- watchlist: the workspace switches the plotted symbol by clicking a row,
    # and ⑦ only submits when that symbol is the session's instrument ----
    watchlist = session.execute(
        select(Watchlist).where(Watchlist.user_id == user.id, Watchlist.name == WATCHLIST_NAME)
    ).scalar_one_or_none()
    if watchlist is None:
        watchlist = Watchlist(id=sid("watchlist"), user_id=user.id, name=WATCHLIST_NAME)
        session.add(watchlist)
        session.flush()
    existing_item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id, WatchlistItem.symbol == INSTRUMENT
        )
    ).scalar_one_or_none()
    if existing_item is None:
        session.add(WatchlistItem(id=sid("watchlist-item"), watchlist_id=watchlist.id,
                                  symbol=INSTRUMENT, position=0))

    session.commit()

    # ---- shared (instrument, provider) metadata: only the span is ours to restate ----
    note_zh = (
        f"E2E 合成日線（provider {PROVIDER}、{TIMEFRAME}），含訂單生命週期腳本寫入的 "
        f"{START_DATE.isoformat()}..{END_DATE.isoformat()}；非真實市場數據。"
    )
    note = (
        f"E2E synthetic {TIMEFRAME} bars for provider '{PROVIDER}'; this seed wrote "
        f"{len(BARS)} bars over {START_DATE}..{END_DATE} (checksum {DATASET_CHECKSUM}); "
        "row spans every stored bar for this instrument/provider; not real-market data."
    )[:500]
    meta_result = upsert_shared_metadata(
        session,
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        note=note,
        note_zh=note_zh,
        data_nature="synthetic",
        timezone=TIMEZONE,
        currency="USD",
        new_id=sid("metadata"),
    )
    session.commit()
    bar_count, first_bar, last_bar = (
        meta_result["total_bars"],
        meta_result["first_bar_date"],
        meta_result["last_bar_date"],
    )

    print("E2E order-lifecycle seed complete (disposable database):")
    print(f"  account label : {EMAIL}")
    print("  password      : (from E2E_PASSWORD env, not printed)")
    print(f"  experiment id : {experiment.id}")
    print(f"  version id    : {version.id}  (locked)")
    print(f"  strategy      : price_change_fixed_hold (passive: threshold_pct=0.99) on {INSTRUMENT}")
    print(f"  costs         : commission_rate={COMMISSION_RATE} slippage_rate={SLIPPAGE_RATE}")
    print(f"  session cash  : {INITIAL_CASH} (UI default is 10000; the spec types this figure)")
    print(f"  bars written  : {len(BARS)} {TIMEFRAME} {PROVIDER} {BARS[0]['date']}..{BARS[-1]['date']}")
    print(f"  bar calendar  : {', '.join(b['date'].isoformat() for b in BARS)}")
    print(f"  next open     : {BARS[0]['date']} -> {BARS[1]['date']} open {BARS[1]['open']}")
    print(f"  metadata spans: {first_bar}..{last_bar} ({bar_count} stored bars for this provider)")
    if meta_result["created"]:
        print("  metadata      : created here - this seed owns health_status/data_nature")
    else:
        preserved = ", ".join(
            f"{field}={value}" for field, value in meta_result["preserved"]
            if value is not None
        )
        print("  metadata      : shared row kept - only aggregates and the note rewritten;"
              f" preserved {preserved or 'nothing else set'}")
    print(f"  watchlist     : {WATCHLIST_NAME} -> {INSTRUMENT}")
    print(f"  timeframe     : {TIMEFRAME}")
    print(f"  provider      : {PROVIDER}")
    print(f"  timezone      : {TIMEZONE}")
    print(f"  checksum      : {DATASET_CHECKSUM}")
