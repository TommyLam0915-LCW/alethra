"""Seed the disposable E2E intraday (5-minute) rule-flow database.

Companion to seed_e2e_rule_flow.py (which seeds the DAILY rule flow). This seed
owns a SEPARATE locked experiment + version whose strategy_spec declares
``timeframe="5m"`` and ``template_type="opening_range_breakout"``, plus its OWN
stored 5-minute bars, so the tracked intraday browser/regression flow can drive
the REAL per-bar replay loop on a fresh disposable database.

Why a distinct provider_id ("demo-5m"): MarketDataBar has NO timeframe column and
its unique key is (instrument_id, bar_timestamp, provider_id). provider_id is
therefore the timeframe-isolation key — seeding the intraday bars under "demo-5m"
guarantees they never mix with the daily "demo" bars for the same instrument, and
create_simulation's provider auto-selection over the intraday date window resolves
to exactly one provider. The window (2024-03-04..06) also sits clear of the daily
demo fixture's January bars.

The bars are a clearly-labelled SYNTHETIC, deterministic 5-minute series engineered
so one locked spec exercises every branch the flow must show:
  * Day 1 (2024-03-04): opening range forms, a completed bar closes above the OR
    high on qualifying volume -> entry fills at the NEXT bar's open -> target exit.
  * Day 2 (2024-03-05): the opening range resets per day (no carry of Day 1's OR),
    a fresh breakout entry, then a session-end forced close on the day's last bar.
  * Day 3 (2024-03-06): a breakout entry whose next bar trades through the stop ->
    stop exit.

Timestamps are naive exchange-local (America/New_York) 09:30-open 5-minute bars.
This window is entirely before the 2024 US DST start (2024-03-10), so it is EST
(UTC-5) with no DST transition inside the dataset. These bars are synthetic and are
never presented as real-market data.

Refuses to run unless DATABASE_URL points at the disposable E2E database, so it can
never seed over a review or development database. Record identities derive from
uuid5(E2E_MARKER), and every run first deletes the rows this script owns in FK-safe
order, then recreates them — idempotent AND resets transient simulation/portfolio
state, so the intraday spec is repeatable on the same database. The daily seed's
experiment (a different marker/uuid) is never touched.
"""
import hashlib
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_NAME = "alethra_e2e"
E2E_MARKER = "alethra-e2e-intraday-flow"

if not DB.startswith("postgresql"):
    sys.exit("Refusing: DATABASE_URL must be a postgresql URL.")
if E2E_DB_NAME not in DB:
    sys.exit(f"Refusing: DATABASE_URL must target the disposable '{E2E_DB_NAME}' database.")

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, delete, inspect, select, update
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.models.experiment import Experiment, ExperimentStatus, ResearchType, Visibility
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar, MarketDataMetadata
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

EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")
if not EMAIL or not PASSWORD:
    sys.exit("Refusing: E2E_EMAIL and E2E_PASSWORD must come from the environment.")

INSTRUMENT = "DEMO-ALPHA"
PROVIDER = "demo-5m"      # distinct from the daily "demo" provider: timeframe isolation
TIMEFRAME = "5m"
TIMEZONE = "America/New_York"  # naive exchange-local timestamps; EST (UTC-5) this window
START_DATE = date(2024, 3, 4)
END_DATE = date(2024, 3, 6)
SESSION_OPEN = time(9, 30)
STEP = timedelta(minutes=5)
EXPERIMENT_TITLE = "E2E 日內規則觸發流程（5 分鐘合成，僅供自動化測試）"

engine = create_engine(DB)
tables = set(inspect(engine).get_table_names())
for needed in (
    "users", "experiments", "experiment_versions", "simulation_sessions",
    "simulation_decisions", "paper_portfolios", "paper_orders", "paper_fills",
    "decision_records", "market_data_bars",
):
    assert needed in tables, f"table {needed} missing - migrations did not run"
for col in ("timeframe", "current_bar_timestamp"):
    assert col in {c["name"] for c in inspect(engine).get_columns("simulation_sessions")}, (
        f"simulation_sessions.{col} missing - intraday migration l6m7n8o9p0q1 did not run"
    )


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{E2E_MARKER}:{name}")


# ---- the exact synthetic 5-minute series this seed owns -------------------
# (open, high, low, close, volume) per bar, 09:30 open, 5-minute steps.
DAYS = {
    # Day 1: breakout entry at bar5 open, target exit on bar6.
    date(2024, 3, 4): [
        (100.0, 101.0, 99.0, 100.5, 1000),
        (100.5, 102.0, 100.0, 101.0, 1000),
        (101.0, 101.5, 100.0, 101.0, 1000),
        (101.0, 103.0, 101.0, 102.5, 2000),
        (102.5, 103.0, 102.0, 102.8, 1000),
        (102.8, 105.0, 102.5, 104.9, 1000),
    ],
    # Day 2: per-day OR reset, fresh breakout entry, session-end forced close.
    date(2024, 3, 5): [
        (104.0, 105.0, 103.5, 104.5, 1000),
        (104.5, 106.0, 104.0, 105.0, 1000),
        (105.0, 105.5, 104.0, 105.0, 1000),
        (105.0, 107.0, 105.0, 106.5, 2000),
        (106.5, 107.0, 106.0, 106.8, 1000),
        (106.8, 107.2, 106.4, 107.0, 1000),
    ],
    # Day 3: breakout entry, next bar trades through the stop -> stop exit.
    date(2024, 3, 6): [
        (107.0, 108.0, 106.5, 107.5, 1000),
        (107.5, 109.0, 107.0, 108.0, 1000),
        (108.0, 108.5, 107.0, 108.0, 1000),
        (108.0, 110.0, 108.0, 109.5, 2000),
        (109.5, 110.0, 109.0, 109.8, 1000),
        (109.0, 109.5, 103.0, 104.0, 1000),
    ],
}

BARS = []
for day in sorted(DAYS):
    t = datetime.combine(day, SESSION_OPEN)
    for (o, h, l, c, v) in DAYS[day]:
        BARS.append({
            "date": day,
            "timestamp": t,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": int(v),
        })
        t += STEP

_checksum_src = "\n".join(
    f"{b['timestamp'].isoformat()},{b['open']},{b['high']},{b['low']},{b['close']},{b['volume']}"
    for b in BARS
)
DATASET_CHECKSUM = "sha256:" + hashlib.sha256(_checksum_src.encode("utf-8")).hexdigest()

with Session(engine) as session:
    # ---- FK-safe reset of everything this seed owns (idempotent re-run) ----
    exp_id = sid("experiment")
    ver_ids = [
        row[0] for row in session.execute(
            select(ExperimentVersion.id).where(ExperimentVersion.experiment_id == exp_id)
        ).all()
    ]
    sess_ids = [
        row[0] for row in session.execute(
            select(SimulationSession.id).where(SimulationSession.experiment_version_id.in_(ver_ids))
        ).all()
    ] if ver_ids else []
    pf_ids = [
        row[0] for row in session.execute(
            select(PaperPortfolio.id).where(PaperPortfolio.simulation_session_id.in_(sess_ids))
        ).all()
    ] if sess_ids else []
    order_ids = [
        row[0] for row in session.execute(
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
    # Bars + metadata this seed owns for the instrument/provider/window.
    session.execute(
        delete(MarketDataBar).where(
            MarketDataBar.instrument_id == INSTRUMENT,
            MarketDataBar.provider_id == PROVIDER,
            MarketDataBar.bar_date >= START_DATE,
            MarketDataBar.bar_date <= END_DATE,
        )
    )
    session.execute(
        delete(MarketDataMetadata).where(
            MarketDataMetadata.instrument_id == INSTRUMENT,
            MarketDataMetadata.provider_id == PROVIDER,
        )
    )
    session.commit()

    # ---- account: get-or-create the shared synthetic login (same email as daily) ----
    user = session.execute(select(User).where(User.email == EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(
            id=sid("user"),
            email=EMAIL,
            password_hash=get_password_hash(PASSWORD),
            display_name="E2E intraday flow (synthetic)",
            user_type=UserType.individual,
        )
        session.add(user)
        session.flush()

    # ---- locked experiment + version carrying the intraday rule ----
    experiment = Experiment(
        id=exp_id,
        owner_id=user.id,
        title=EXPERIMENT_TITLE,
        hypothesis="驗證日內規則觸發：5 分鐘開盤區間突破進場，目標／停損／收盤自動出場，全程無需人工下單",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
        visibility=Visibility.private,
        current_version_id=None,
    )
    session.add(experiment)
    session.flush()

    strategy_spec = {
        "template_type": "opening_range_breakout",
        "timeframe": TIMEFRAME,
        "asset_ids": [INSTRUMENT],
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "initial_cash": 100000,
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "signal_parameters": {
            "opening_range_bars": 3,
            "volume_window_bars": 2,
            "volume_min_ratio": 1.0,
        },
        "risk_parameters": {"stop_pct": 0.05, "target_pct": 0.02},
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
        locked_at=datetime(2024, 3, 7, 9, 0, 0),
        locked_by=user.id,
        lock_reason="E2E intraday rule-flow regression: rules frozen before replay",
        dataset_cutoff_at=END_DATE.isoformat(),
        data_timezone=TIMEZONE,
        strategy_config_hash=DATASET_CHECKSUM.split(":", 1)[1][:16],
    )
    session.add(version)
    session.flush()
    experiment.current_version_id = version.id

    # ---- the seed's own stored 5-minute bars (session creation validates these) ----
    for b in BARS:
        session.add(MarketDataBar(
            id=sid(f"bar:{b['timestamp'].isoformat()}"),
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=b["date"],
            bar_timestamp=b["timestamp"],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"],
            volume=b["volume"],
        ))
    session.add(MarketDataMetadata(
        id=sid("metadata"),
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        data_nature="synthetic",
        timezone=TIMEZONE,
        currency="USD",
        provider_note=(
            f"E2E synthetic {TIMEFRAME} bars for the intraday rule-flow regression; "
            f"exchange-local {TIMEZONE} timestamps (EST/UTC-5, pre-DST window); "
            f"checksum {DATASET_CHECKSUM}; not real-market data."
        ),
        provider_note_zh="E2E 合成 5 分鐘 K 線，僅供日內規則觸發回歸測試；交易所本地時間（EST/UTC-5，非夏令時窗口）；非真實市場數據。",
        first_bar_date=BARS[0]["date"],
        last_bar_date=BARS[-1]["date"],
        total_bars=len(BARS),
        health_status="ok",
    ))
    session.commit()

    print("E2E intraday rule-flow seed complete (disposable database):")
    print(f"  account label : {EMAIL}")
    print("  password      : (from E2E_PASSWORD env, not printed)")
    print(f"  experiment id : {experiment.id}")
    print(f"  version id    : {version.id}  (locked, timeframe={TIMEFRAME})")
    print(f"  strategy      : opening_range_breakout stop_pct=0.05 target_pct=0.02 on {INSTRUMENT}")
    print(f"  provider      : {PROVIDER}  (timeframe-isolation key; distinct from daily 'demo')")
    print(f"  bars          : {len(BARS)} {TIMEFRAME} {BARS[0]['timestamp']}..{BARS[-1]['timestamp']}")
    print(f"  timezone      : {TIMEZONE} (EST/UTC-5, pre-DST)")
    print(f"  checksum      : {DATASET_CHECKSUM}")
