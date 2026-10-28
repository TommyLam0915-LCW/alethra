"""Seed the disposable E2E rule-triggered-flow database (test-only, synthetic).

Provides everything the tracked Playwright rule-flow regression needs to drive
the REAL UI loop on a fresh disposable database, with no dependence on any
manually-seeded bars or any pre-existing account / session / portfolio:

  * one labelled synthetic account (get-or-create by E2E_EMAIL)
  * one locked experiment + version carrying a long-only rule strategy
    (buy_and_hold with a 5% trailing stop) so the forward replay enters, then
    exits on the stop within the window
  * its OWN MarketDataBar rows for DEMO-ALPHA over the replay window, so
    simulation-session creation (which validates stored providers) succeeds even
    on a database that has never seen the ad-hoc 20-bar demo seed

Provenance is explicit and printed: timeframe, provider, timezone and a sha256
dataset checksum over the exact bar series the seed writes. The bars mirror the
synthetic demo fixture the advance step prices off (app.data.demo_data.DEMO_DATA)
so the stored calendar and the replayed prices agree; they are synthetic and are
never presented as real-market data.

Refuses to run unless DATABASE_URL points at the disposable E2E database, so it
can never seed over a review or development database. Record identities derive
from uuid5(E2E_MARKER), and every run first deletes the rows this script owns
(experiment -> version -> sessions -> decisions -> portfolios -> orders -> fills
-> paper decision records -> bars -> metadata) in FK-safe order, then recreates
them. That makes the seed idempotent AND resets transient simulation/portfolio
state, so the spec is repeatable on the same database.
"""
import hashlib
import os
import sys
import uuid
from datetime import date, datetime

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_NAME = "alethra_e2e"
E2E_MARKER = "alethra-e2e-rule-flow"

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
PROVIDER = "demo"
TIMEFRAME = "1d"          # the live forward replay advances one calendar day per step
TIMEZONE = "UTC"          # bar_date is a naive calendar date; bar_timestamp is midnight UTC
START_DATE = date(2024, 1, 2)
END_DATE = date(2024, 1, 31)
EXPERIMENT_TITLE = "E2E 規則觸發流程（僅供自動化測試）"

engine = create_engine(DB)
tables = set(inspect(engine).get_table_names())
for needed in (
    "users", "experiments", "experiment_versions", "simulation_sessions",
    "simulation_decisions", "paper_portfolios", "paper_orders", "paper_fills",
    "decision_records", "market_data_bars",
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
        "volume": int(prices.get("volume", 1_000_000)),
    })
if not BARS:
    sys.exit(f"Refusing: demo fixture has no {INSTRUMENT} bars in {START_DATE}..{END_DATE}.")

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
    if ver_ids:
        sess_ids = [
            row[0]
            for row in session.execute(
                select(SimulationSession.id).where(
                    SimulationSession.experiment_version_id.in_(ver_ids)
                )
            ).all()
        ]
    else:
        sess_ids = []
    if sess_ids:
        pf_ids = [
            row[0]
            for row in session.execute(
                select(PaperPortfolio.id).where(
                    PaperPortfolio.simulation_session_id.in_(sess_ids)
                )
            ).all()
        ]
    else:
        pf_ids = []
    if pf_ids:
        order_ids = [
            row[0]
            for row in session.execute(
                select(PaperOrder.id).where(PaperOrder.portfolio_id.in_(pf_ids))
            ).all()
        ]
    else:
        order_ids = []
    if order_ids:
        session.execute(delete(PaperFill).where(PaperFill.paper_order_id.in_(order_ids)))
    if pf_ids:
        session.execute(delete(PaperPosition).where(PaperPosition.portfolio_id.in_(pf_ids)))
        session.execute(delete(PaperOrder).where(PaperOrder.portfolio_id.in_(pf_ids)))
    if pf_ids:
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
    # Break the circular FK experiments.current_version_id -> experiment_versions.id
    # before dropping the version, otherwise the re-seed delete violates the FK.
    session.execute(
        update(Experiment).where(Experiment.id == exp_id).values(current_version_id=None)
    )
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

    # ---- account: get-or-create the task's own synthetic login ----
    user = session.execute(select(User).where(User.email == EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(
            id=sid("user"),
            email=EMAIL,
            password_hash=get_password_hash(PASSWORD),
            display_name="E2E rule flow (synthetic)",
            user_type=UserType.individual,
        )
        session.add(user)
        session.flush()

    # ---- locked experiment + version carrying the long-only rule strategy ----
    experiment = Experiment(
        id=exp_id,
        owner_id=user.id,
        title=EXPERIMENT_TITLE,
        hypothesis="驗證規則觸發：進場後由 5% 停損自動出場，全程無需人工下單",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
        visibility=Visibility.private,
        current_version_id=None,
    )
    session.add(experiment)
    session.flush()

    strategy_spec = {
        "template_type": "buy_and_hold",
        "asset_ids": [INSTRUMENT],
        "start_date": START_DATE.isoformat(),
        "end_date": END_DATE.isoformat(),
        "initial_cash": 100000,
        "risk_parameters": {"stop_pct": 0.05},
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
        lock_reason="E2E rule-flow regression: rules frozen before replay",
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
    session.add(MarketDataMetadata(
        id=sid("metadata"),
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        data_nature="synthetic",
        timezone=TIMEZONE,
        currency="USD",
        provider_note=(
            f"E2E synthetic {TIMEFRAME} bars for the rule-flow regression; "
            f"checksum {DATASET_CHECKSUM}; not real-market data."
        ),
        provider_note_zh="E2E 合成日線，僅供規則觸發回歸測試；非真實市場數據。",
        first_bar_date=BARS[0]["date"],
        last_bar_date=BARS[-1]["date"],
        total_bars=len(BARS),
        health_status="ok",
    ))
    session.commit()

    print("E2E rule-flow seed complete (disposable database):")
    print(f"  account label : {EMAIL}")
    print("  password      : (from E2E_PASSWORD env, not printed)")
    print(f"  experiment id : {experiment.id}")
    print(f"  version id    : {version.id}  (locked)")
    print(f"  strategy      : buy_and_hold + stop_pct=0.05 on {INSTRUMENT}")
    print(f"  bars          : {len(BARS)} {TIMEFRAME} {PROVIDER} {BARS[0]['date']}..{BARS[-1]['date']}")
    print(f"  timeframe     : {TIMEFRAME}")
    print(f"  provider      : {PROVIDER}")
    print(f"  timezone      : {TIMEZONE}")
    print(f"  checksum      : {DATASET_CHECKSUM}")
