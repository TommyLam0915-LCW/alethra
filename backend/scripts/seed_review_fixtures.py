"""Rebuild an isolated, synthetic owner-review database (no real data).

Creates a brand-new disposable SQLite DB and loads ONLY synthetic fixtures:
one test user, one experiment, one version, two completed backtest runs. It
never reads, copies, or modifies any existing development or private database.

Usage (from the backend directory, project venv):
    DATABASE_URL="sqlite:////tmp/review.db" REVIEW_PASSWORD="<optional>" \
        python scripts/seed_review_fixtures.py

If REVIEW_PASSWORD is not provided, a random one is generated and printed.
Schema is built through the app's real startup path (Base.metadata.create_all),
which is how this project acquires tables; the Alembic history is not proven to
replay cleanly on SQLite, so do not run `alembic upgrade head` against a live DB
without separate verification.
"""
import os
import sys
import uuid

DB_PATH = os.environ.get("DATABASE_URL")
if not DB_PATH or not DB_PATH.startswith("sqlite"):
    sys.exit("Set DATABASE_URL to a NEW disposable sqlite path, e.g. sqlite:////tmp/review.db")
os.environ["DATABASE_URL"] = DB_PATH

sys.path.insert(0, os.getcwd())

from sqlalchemy import create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Session


@compiles(JSONB, "sqlite")
def _jsonb(element, compiler, **kw):
    return "JSON"


@compiles(UUID, "sqlite")
def _uuid(element, compiler, **kw):
    return "VARCHAR(36)"


from app.database import Base
import app.models  # register every model, incl. SavedComparison
from app.models.user import User, UserType
from app.models.experiment import Experiment, ExperimentStatus, Visibility, ResearchType
from app.models.experiment_version import ExperimentVersion
from app.models.research_run import ResearchRun, RunStatus
from app.auth.jwt import get_password_hash

TEST_PASSWORD = os.environ.get("REVIEW_PASSWORD") or uuid.uuid4().hex[:14]
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _fk_off(dbapi_conn, _):
    c = dbapi_conn.cursor(); c.execute("PRAGMA foreign_keys=OFF"); c.close()


Base.metadata.create_all(bind=engine, checkfirst=True)
assert "saved_comparisons" in engine.dialect.get_table_names(engine.connect())

user = User(id=uuid.uuid4(), email="owner-review@alethra.io",
            password_hash=get_password_hash(TEST_PASSWORD),
            display_name="Owner Review", user_type=UserType.individual)
exp = Experiment(id=uuid.uuid4(), owner_id=user.id,
                 title="Saved-comparison review (synthetic)",
                 research_type=ResearchType.quantitative,
                 status=ExperimentStatus.draft, visibility=Visibility.private)
ver = ExperimentVersion(id=uuid.uuid4(), experiment_id=exp.id, version_number=1,
                        strategy_spec={"synthetic": True}, created_by=user.id)
run_a = ResearchRun(id=uuid.uuid4(), experiment_version_id=ver.id, engine_version="synthetic-1.0.0",
                    config_hash="aaaa", status=RunStatus.completed,
                    metrics={"total_return_pct": 12.5, "max_drawdown_pct": -8.1,
                             "total_trades": 42, "sharpe_ratio": 1.3})
run_b = ResearchRun(id=uuid.uuid4(), experiment_version_id=ver.id, engine_version="synthetic-1.0.0",
                    config_hash="bbbb", status=RunStatus.completed,
                    metrics={"total_return_pct": 10.2, "max_drawdown_pct": -6.4,
                             "total_trades": 30, "sharpe_ratio": 1.1})
exp.current_version_id = ver.id

with Session(engine) as s:
    s.add_all([user, exp, ver, run_a, run_b])
    s.commit()
    print("\n".join([
        "Synthetic review DB ready (test-only; no real data):",
        f"  login email : {user.email}",
        f"  login password: {TEST_PASSWORD}",
        f"  experiment id : {exp.id}",
        f"  run A: {run_a.id} (total_return_pct=12.5)",
        f"  run B: {run_b.id} (total_return_pct=10.2)",
    ]))
