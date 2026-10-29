"""Seed the disposable personal-learning E2E database (test-only, synthetic).

Creates one labelled synthetic account, one locked strategy version, synthetic
daily bars for one instrument and 205 already-reviewed human decisions spread
over 41 distinct decision instants. That is what the Playwright personal
learning journey needs: the review → snapshot → job → version → activate →
predict flow starts from reviewed decisions, not from decision authoring.

Every bar, decision and label is generated here and is a SYNTHETIC FIXTURE.
It proves the engineering chain works; it says nothing about model quality.

Refuses to run unless DATABASE_URL targets a disposable personal-learning E2E
database, so it can never seed over a review, shared runtime or development
database. Row identities derive from uuid5(E2E_MARKER), so re-running replaces
only the rows this script owns.
"""
import math
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_MARKER = "alethra_personal_e2e"
E2E_MARKER = "alethra-personal-learning-e2e"

if not DB.startswith("postgresql"):
    sys.exit("Refusing: DATABASE_URL must be a postgresql URL.")
if E2E_DB_MARKER not in DB:
    sys.exit(
        f"Refusing: DATABASE_URL must target a disposable '{E2E_DB_MARKER}' database."
    )

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, delete, inspect, update
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.models.audit_event import AuditEvent
from app.models.billing import UsageRecord
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
from app.models.model_research import (
    ModelEvaluation,
    ModelResearchRecord,
    ModelVersion,
)
from app.models.personal_dataset import PersonalizationDatasetSnapshot
from app.models.personal_model_selection import (
    PersonalModelComparisonRun,
    PersonalModelSelection,
)
from app.models.personalization import PersonalizationConsent
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.simulation import SimulationSession, SimulationStatus
from app.models.training import TrainingJob
from app.models.user import User, UserType
from app.services.decision_labels import record_label
from app.services.personalization import grant_consent

EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")
if not EMAIL or not PASSWORD:
    sys.exit("Refusing: E2E_EMAIL and E2E_PASSWORD must come from the environment.")

INSTRUMENT = "US:AAPL"
PROVIDER = "synthetic"
DECISION_START_INDEX = 30
DECISION_STEP = 2
DECISION_DAYS = 41
DECISIONS_PER_DAY = 5


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


engine = create_engine(DB)
tables = inspect(engine).get_table_names()
for needed in (
    "users",
    "experiments",
    "experiment_versions",
    "simulation_sessions",
    "human_decision_events",
    "decision_labels",
    "market_data_bars",
):
    assert needed in tables, f"table {needed} missing - migrations did not run"

user_id = sid("user")

with Session(engine) as session:
    existing = session.get(User, user_id)
    if existing is not None:
        event_ids = [
            row.id
            for row in session.query(HumanDecisionEvent.id)
            .filter(HumanDecisionEvent.owner_id == user_id)
            .all()
        ]
        if event_ids:
            session.execute(
                delete(DecisionLabel).where(DecisionLabel.decision_event_id.in_(event_ids))
            )
        session.execute(
            delete(HumanDecisionEvent).where(HumanDecisionEvent.owner_id == user_id)
        )
        session.execute(
            delete(SimulationSession).where(SimulationSession.owner_id == user_id)
        )
        session.execute(
            delete(PersonalizationDatasetSnapshot).where(
                PersonalizationDatasetSnapshot.owner_id == user_id
            )
        )
        session.execute(
            delete(PersonalizationConsent).where(
                PersonalizationConsent.owner_id == user_id
            )
        )
        session.execute(
            delete(QuotaReservation).where(QuotaReservation.user_id == user_id)
        )
        session.execute(
            delete(QuotaCounter).where(QuotaCounter.user_id == user_id)
        )
        session.execute(
            delete(TrainingJob).where(TrainingJob.user_id == user_id)
        )
        # Comparison runs and selections reference model versions, so they have
        # to go first or the version delete hits a foreign key.
        session.execute(
            delete(PersonalModelSelection).where(
                PersonalModelSelection.owner_id == user_id
            )
        )
        session.execute(
            delete(PersonalModelComparisonRun).where(
                PersonalModelComparisonRun.owner_id == user_id
            )
        )
        record_ids = [
            row.id
            for row in session.query(ModelResearchRecord.id)
            .filter(ModelResearchRecord.owner_id == user_id)
            .all()
        ]
        if record_ids:
            version_ids = [
                row.id
                for row in session.query(ModelVersion.id)
                .filter(ModelVersion.model_research_record_id.in_(record_ids))
                .all()
            ]
            session.execute(
                update(ModelResearchRecord)
                .where(ModelResearchRecord.id.in_(record_ids))
                .values(active_version_id=None)
            )
            if version_ids:
                session.execute(
                    delete(ModelEvaluation).where(
                        ModelEvaluation.model_version_id.in_(version_ids)
                    )
                )
                session.execute(
                    delete(ModelVersion).where(ModelVersion.id.in_(version_ids))
                )
            session.execute(
                delete(ModelResearchRecord).where(
                    ModelResearchRecord.id.in_(record_ids)
                )
            )
        experiment_ids = [
            row.id
            for row in session.query(Experiment.id)
            .filter(Experiment.owner_id == user_id)
            .all()
        ]
        if experiment_ids:
            session.execute(
                update(Experiment)
                .where(Experiment.id.in_(experiment_ids))
                .values(current_version_id=None)
            )
            session.flush()
        for experiment_id in experiment_ids:
            version_ids = [
                row.id
                for row in session.query(ExperimentVersion.id)
                .filter(ExperimentVersion.experiment_id == experiment_id)
                .all()
            ]
            if version_ids:
                session.execute(
                    delete(ExperimentVersion).where(
                        ExperimentVersion.id.in_(version_ids)
                    )
                )
            session.execute(delete(Experiment).where(Experiment.id == experiment_id))
        session.execute(delete(AuditEvent).where(AuditEvent.actor_id == user_id))
        session.execute(delete(UsageRecord).where(UsageRecord.user_id == user_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()

    user = User(
        id=user_id,
        email=EMAIL,
        password_hash=get_password_hash(PASSWORD),
        display_name="E2E personal learning (synthetic)",
        user_type=UserType.individual,
    )
    session.add(user)
    session.flush()

    experiment = Experiment(
        id=sid("experiment"),
        owner_id=user.id,
        title="E2E 個人策略學習（合成，自動化）",
        hypothesis="以已覆核的合成決策驗證個人模型工程鏈",
        research_type=ResearchType.quantitative,
        status=ExperimentStatus.locked,
        visibility=Visibility.private,
        current_version_id=None,
    )
    session.add(experiment)
    session.flush()

    version = ExperimentVersion(
        id=sid("version"),
        experiment_id=experiment.id,
        version_number=1,
        strategy_spec={
            "template_type": "buy_and_hold",
            "asset_ids": [INSTRUMENT],
            "start_date": BARS[0].isoformat(),
            "end_date": BARS[-1].isoformat(),
            "initial_cash": 100000,
            "synthetic": True,
            "e2e_marker": E2E_MARKER,
        },
        created_by=user.id,
    )
    session.add(version)
    session.flush()
    experiment.current_version_id = version.id

    session.execute(
        delete(MarketDataBar).where(
            MarketDataBar.instrument_id == INSTRUMENT,
            MarketDataBar.provider_id == PROVIDER,
        )
    )
    session.execute(
        delete(MarketDataMetadata).where(
            MarketDataMetadata.instrument_id == INSTRUMENT,
            MarketDataMetadata.provider_id == PROVIDER,
        )
    )
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

    session_row = SimulationSession(
        id=sid("session"),
        experiment_version_id=version.id,
        owner_id=user.id,
        initial_virtual_cash=100000,
        started_at=datetime.combine(BARS[DECISION_START_INDEX], time(8, 30)),
        data_cutoff_at=BARS[DECISION_START_INDEX + (DECISION_DAYS - 1) * DECISION_STEP].isoformat(),
        current_snapshot_date=BARS[
            DECISION_START_INDEX + (DECISION_DAYS - 1) * DECISION_STEP
        ].isoformat(),
        status=SimulationStatus.active,
        provider_id=PROVIDER,
        provider_confirmed=True,
        total_decisions=DECISION_DAYS * DECISIONS_PER_DAY,
        observed_days=DECISION_DAYS,
    )
    session.add(session_row)
    session.flush()

    event_count = 0
    for d in range(DECISION_DAYS):
        bar_index = DECISION_START_INDEX + d * DECISION_STEP
        day = BARS[bar_index]
        for j in range(DECISIONS_PER_DAY):
            action = "buy" if event_count % 2 == 0 else "sell"
            event = HumanDecisionEvent(
                id=sid(f"decision-{event_count}"),
                owner_id=user.id,
                simulation_session_id=session_row.id,
                idempotency_key=f"seed-{event_count}",
                action=HumanDecisionAction(action),
                quantity=10.0 + j,
                rationale=f"synthetic fixture decision #{event_count}",
                server_strategy_version_id=version.id,
                server_instrument_id=INSTRUMENT,
                server_step_number=event_count + 1,
                server_snapshot_date=day.isoformat(),
                server_data_cutoff_at=day.isoformat(),
                decision_instant=datetime.combine(day, datetime.min.time()),
                execution_status=HumanDecisionExecutionStatus.record_only,
                created_at=datetime.combine(day, time(9, 0))
                + timedelta(minutes=j),
            )
            session.add(event)
            session.flush()
            record_label(
                session,
                event=event,
                author=user,
                state="confirmed",
                action_label=None,
                note=None,
                exclusion_reason=None,
            )
            event_count += 1

    # Candidate fixtures pre-grant consent so the owner can open one URL and
    # walk the analytical path without a separate authorisation detour. The
    # tracked E2E journey leaves this unset and grants consent through the UI.
    consent_granted = os.environ.get("E2E_GRANT_CONSENT") == "1"
    if consent_granted:
        grant_consent(session, user.id, [version.id])

    session.commit()

print("Seeded disposable personal-learning E2E data")
print(f"  owner          : {user_id} ({EMAIL})")
print(f"  experiment     : {sid('experiment')}")
print(f"  version        : {sid('version')}")
print(f"  instrument     : {INSTRUMENT} ({PROVIDER}, synthetic)")
print(f"  bars           : {len(BARS)} ({BARS[0]} -> {BARS[-1]})")
print(f"  decisions      : {event_count} across {DECISION_DAYS} decision instants")
print(f"  labels         : {event_count} confirmed")
print(f"  consent        : {'granted (fixture)' if consent_granted else 'not granted'}")
print(f"  predict_as_of  : {BARS[120].isoformat()}")
