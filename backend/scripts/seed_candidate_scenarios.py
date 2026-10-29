"""Seed the two additional first-use candidate accounts (test-only, synthetic).

Creates:
  * an EMPTY account: no strategy, no decisions, no consent, no model.
  * a THIN account: one locked strategy version and five recorded decisions
    (3 confirmed across buy/sell, 1 excluded, 1 left unreviewed) on synthetic
    AAPL bars - far below the 200 confirmed-decision training threshold - and
    NO personalization consent, so the first-use consent flow is exercised
    through the UI rather than pre-seeded.

Everything here is a SYNTHETIC FIXTURE. It proves that the product surfaces
render and recover with sparse data; it says nothing about model quality.

Refuses to run unless DATABASE_URL targets a disposable personal-learning E2E
database. Create-if-missing: an existing account is never cleared or rewritten,
so a participant's progress on the candidate stack survives a resume.
"""
import math
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_MARKER = "alethra_personal_e2e"
E2E_MARKER = "alethra-candidate-first-use"

if not DB.startswith("postgresql"):
    sys.exit("Refusing: DATABASE_URL must be a postgresql URL.")
if E2E_DB_MARKER not in DB:
    sys.exit(
        f"Refusing: DATABASE_URL must target a disposable '{E2E_DB_MARKER}' database."
    )

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.models.decision_label import DecisionLabel
from app.models.experiment import (
    Experiment,
    ExperimentStatus,
    ResearchType,
    Visibility,
)
from app.models.experiment_version import ExperimentVersion
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.models.simulation import SimulationSession, SimulationStatus
from app.models.user import User, UserType
from app.services.decision_labels import record_label

EMPTY_EMAIL = os.environ.get("E2E_EMPTY_EMAIL")
EMPTY_PASSWORD = os.environ.get("E2E_EMPTY_PASSWORD")
THIN_EMAIL = os.environ.get("E2E_THIN_EMAIL")
THIN_PASSWORD = os.environ.get("E2E_THIN_PASSWORD")
if not all((EMPTY_EMAIL, EMPTY_PASSWORD, THIN_EMAIL, THIN_PASSWORD)):
    sys.exit(
        "Refusing: E2E_EMPTY_EMAIL, E2E_EMPTY_PASSWORD, E2E_THIN_EMAIL and "
        "E2E_THIN_PASSWORD must come from the environment."
    )

INSTRUMENT = "US:AAPL"
PROVIDER = "synthetic"


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{E2E_MARKER}:{name}")


def business_days(start: date, count: int):
    days = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


BARS = business_days(date(2024, 1, 1), 140)


def price(i: int) -> float:
    return 100.0 + 0.05 * i + 6.0 * math.sin(i / 4.0) + 0.25 * (i % 7)


def ensure_bars(session: Session) -> None:
    existing = session.execute(
        select(MarketDataBar.id).where(
            MarketDataBar.instrument_id == INSTRUMENT,
            MarketDataBar.provider_id == PROVIDER,
        ).limit(1)
    ).first()
    if existing is not None:
        return
    for index, day in enumerate(BARS):
        close = price(index)
        session.add(
            MarketDataBar(
                instrument_id=INSTRUMENT,
                provider_id=PROVIDER,
                bar_date=day,
                bar_timestamp=datetime.combine(day, datetime.min.time()),
                open=close - 0.4,
                high=close + 0.8,
                low=close - 0.9,
                close=close,
                volume=1_000_000 + (index % 5) * 10_000,
            )
        )
    session.add(
        MarketDataMetadata(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            data_nature="synthetic",
            first_bar_date=BARS[0],
            last_bar_date=BARS[-1],
            total_bars=len(BARS),
        )
    )
    session.flush()


def make_user(session: Session, user_id: uuid.UUID, email: str, password: str,
              display_name: str) -> User:
    user = User(
        id=user_id,
        email=email,
        password_hash=get_password_hash(password),
        display_name=display_name,
        user_type=UserType.individual,
    )
    session.add(user)
    session.flush()
    return user


engine = create_engine(DB)

empty_id = sid("empty:user")
thin_id = sid("thin:user")

with Session(engine) as session:
    empty_existing = session.get(User, empty_id)
    if empty_existing is None:
        make_user(
            session,
            empty_id,
            EMPTY_EMAIL,
            EMPTY_PASSWORD,
            "Candidate empty account (synthetic)",
        )
        print("created empty account (no strategy, decisions, consent or model)")
    else:
        print("empty account already exists - left untouched")

    thin_existing = session.get(User, thin_id)
    if thin_existing is not None:
        print("thin account already exists - left untouched")
    else:
        ensure_bars(session)
        thin = make_user(
            session,
            thin_id,
            THIN_EMAIL,
            THIN_PASSWORD,
            "Candidate thin account (synthetic)",
        )

        experiment = Experiment(
            id=sid("thin:experiment"),
            owner_id=thin.id,
            title="C 首次使用示範（合成資料，資料量刻意不足）",
            hypothesis="以少量已覆核決策驗證資料不足時的狀態與下一步指引",
            research_type=ResearchType.quantitative,
            status=ExperimentStatus.locked,
            visibility=Visibility.private,
            current_version_id=None,
        )
        session.add(experiment)
        session.flush()

        version = ExperimentVersion(
            id=sid("thin:version"),
            experiment_id=experiment.id,
            version_number=1,
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": [INSTRUMENT],
                "start_date": BARS[0].isoformat(),
                "end_date": BARS[-1].isoformat(),
                "initial_cash": 100000,
                "synthetic": True,
                "candidate_scenario": "thin",
            },
            created_by=thin.id,
        )
        session.add(version)
        session.flush()
        experiment.current_version_id = version.id

        decision_days = [BARS[30], BARS[32], BARS[34], BARS[36], BARS[38]]
        session_row = SimulationSession(
            id=sid("thin:session"),
            experiment_version_id=version.id,
            owner_id=thin.id,
            initial_virtual_cash=100000,
            started_at=datetime.combine(decision_days[0], time(8, 30)),
            data_cutoff_at=decision_days[-1].isoformat(),
            current_snapshot_date=decision_days[-1].isoformat(),
            status=SimulationStatus.active,
            provider_id=PROVIDER,
            provider_confirmed=True,
            total_decisions=len(decision_days),
            observed_days=len(decision_days),
        )
        session.add(session_row)
        session.flush()

        # 3 confirmed (buy/sell/buy), 1 excluded, 1 deliberately unreviewed.
        plan = [
            ("buy", "confirmed", None),
            ("sell", "confirmed", None),
            ("buy", "confirmed", None),
            ("sell", "excluded", "test_trade"),
            ("buy", None, None),
        ]
        for index, (action, label_state, exclusion_reason) in enumerate(plan):
            day = decision_days[index]
            event = HumanDecisionEvent(
                id=sid(f"thin:decision-{index}"),
                owner_id=thin.id,
                simulation_session_id=session_row.id,
                idempotency_key=f"candidate-thin-{index}",
                action=HumanDecisionAction(action),
                quantity=10.0 + index,
                rationale=f"synthetic thin-fixture decision #{index}",
                server_strategy_version_id=version.id,
                server_instrument_id=INSTRUMENT,
                server_step_number=index + 1,
                server_snapshot_date=day.isoformat(),
                server_data_cutoff_at=day.isoformat(),
                decision_instant=datetime.combine(day, datetime.min.time()),
                execution_status=HumanDecisionExecutionStatus.record_only,
                created_at=datetime.combine(day, time(9, 0)) + timedelta(minutes=index),
            )
            session.add(event)
            session.flush()
            if label_state is not None:
                record_label(
                    session,
                    event=event,
                    author=thin,
                    state=label_state,
                    action_label=None,
                    note=None,
                    exclusion_reason=exclusion_reason,
                )

        print(
            "created thin account (1 version, 5 decisions: 3 confirmed / "
            "1 excluded / 1 unreviewed, no consent granted)"
        )

    session.commit()

print("Candidate first-use scenario accounts ready")
print(f"  empty account id : {empty_id} ({EMPTY_EMAIL})")
print(f"  thin account id  : {thin_id} ({THIN_EMAIL})")
print(f"  thin experiment  : {sid('thin:experiment')}")
print(f"  thin version     : {sid('thin:version')}")
print(f"  bars             : {len(BARS)} synthetic {INSTRUMENT} bars ({BARS[0]} -> {BARS[-1]})")
