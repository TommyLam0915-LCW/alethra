"""Seed the disposable E2E research-flow database (test-only, synthetic).

Creates one labelled synthetic account, one experiment, one version and four
backtest runs (three completed, one still running) so the Playwright
research-flow regression has deterministic data to drive the real UI.

Refuses to run unless DATABASE_URL points at the disposable E2E database, so it
can never seed over a review or development database. Record identities derive
from uuid5(E2E_MARKER), so re-running replaces only the rows this script owns.
"""
import os
import sys
import uuid
from datetime import datetime

DB = os.environ.get("DATABASE_URL", "")
E2E_DB_NAME = "alethra_e2e"
E2E_MARKER = "alethra-e2e-research-flow"

if not DB.startswith("postgresql"):
    sys.exit("Refusing: DATABASE_URL must be a postgresql URL.")
if E2E_DB_NAME not in DB:
    sys.exit(f"Refusing: DATABASE_URL must target the disposable '{E2E_DB_NAME}' database.")

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, delete, inspect
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.models.experiment import Experiment, ExperimentStatus, ResearchType, Visibility
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.models.saved_comparison import SavedComparison
from app.models.user import User, UserType

EMAIL = os.environ.get("E2E_EMAIL")
PASSWORD = os.environ.get("E2E_PASSWORD")
if not EMAIL or not PASSWORD:
    sys.exit("Refusing: E2E_EMAIL and E2E_PASSWORD must come from the environment.")

engine = create_engine(DB)
tables = inspect(engine).get_table_names()
for needed in ("users", "experiments", "experiment_versions", "research_runs", "saved_comparisons"):
    assert needed in tables, f"table {needed} missing - migrations did not run"


def sid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{E2E_MARKER}:{name}")


RUNS = (
    ("run-a", RunStatus.completed, 12.5, -8.1, 42, 1.30, 95.4),
    ("run-b", RunStatus.completed, 10.2, -6.4, 30, 1.10, 78.2),
    ("run-c", RunStatus.completed, 7.4, -4.9, 18, 0.90, 55.1),
    ("run-d", RunStatus.running, None, None, None, None, None),
)

with Session(engine) as session:
    # Replace only the rows this script owns, keyed on its deterministic ids.
    existing_user = session.get(User, sid("user"))
    if existing_user is not None:
        exp_ids = [
            row.id
            for row in session.query(Experiment).filter(Experiment.owner_id == existing_user.id).all()
        ]
        ver_ids = [
            row.id
            for row in session.query(ExperimentVersion)
            .filter(ExperimentVersion.experiment_id.in_(exp_ids))
            .all()
        ] if exp_ids else []
        if exp_ids:
            session.execute(delete(SavedComparison).where(SavedComparison.experiment_id.in_(exp_ids)))
        if ver_ids:
            session.execute(delete(ResearchRun).where(ResearchRun.experiment_version_id.in_(ver_ids)))
        if exp_ids:
            session.execute(delete(ExperimentVersion).where(ExperimentVersion.id.in_(ver_ids)))
            session.execute(delete(Experiment).where(Experiment.id.in_(exp_ids)))
        session.execute(delete(User).where(User.id == existing_user.id))
        session.commit()

    user = User(
        id=sid("user"),
        email=EMAIL,
        password_hash=get_password_hash(PASSWORD),
        display_name="E2E research flow (synthetic)",
        user_type=UserType.individual,
    )
    session.add(user)
    session.flush()

    experiment = Experiment(
        id=sid("experiment"),
        owner_id=user.id,
        title="E2E 合成研究實驗（僅供自動化測試）",
        hypothesis="比較兩個合成 runs 嘅回報與回撤",
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
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
            "synthetic": True,
            "e2e_marker": E2E_MARKER,
        },
        created_by=user.id,
    )
    session.add(version)
    session.flush()

    for idx, (name, status, ret, dd, trades, sharpe, ann) in enumerate(RUNS):
        metrics = (
            None
            if ret is None
            else {
                "total_return_pct": ret,
                "max_drawdown_pct": dd,
                "total_trades": trades,
                "sharpe_ratio": sharpe,
                "annualized_return_pct": ann,
            }
        )
        session.add(
            ResearchRun(
                id=sid(name),
                experiment_version_id=version.id,
                engine_version=f"e2e-synthetic-1.0.0-{name}",
                config_hash=uuid.uuid5(uuid.NAMESPACE_URL, f"{E2E_MARKER}:{name}:config").hex[:8],
                status=status,
                metrics=metrics,
                started_at=datetime(2024, 2, 1, 9, idx, 0),
                completed_at=None if status is RunStatus.running else datetime(2024, 2, 1, 9, idx, 30),
                result_json={
                    "start_date": "2024-01-02",
                    "end_date": "2024-01-31",
                    "data_provider": "demo",
                    "dataset_checksum": f"sha256:{name}",
                    "synthetic": True,
                },
            )
        )

    experiment.current_version_id = version.id
    session.commit()

    print("E2E research-flow seed complete (disposable database):")
    print(f"  account label : {EMAIL}")
    print("  password      : (from E2E_PASSWORD env, not printed)")
    print(f"  experiment id : {experiment.id}")
    print(f"  version id    : {version.id}")
    for name, status, ret, _dd, _t, _s, _a in RUNS:
        print(f"  run {name}     : {sid(name)} status={status.value} total_return_pct={ret}")
