"""Prove the two newest revisions upgrade a database that already holds data.

Distinct from a fresh `alembic upgrade head`: here a NEW disposable database is
built only up to the pre-integration head `h2b3c4d5e6f7`, filled with synthetic
rows valid under that older schema, and then upgraded through `8616c0c9aa3e`
(dataset binding) and `h8i9j0k1l2m3` (human_decision_events).

Refuses to run anywhere except the disposable `alethra_e2e` container database,
so it can never point at the review or development databases. The check database
it creates is dropped again at the end.
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime

DB = os.environ.get("DATABASE_URL", "")
CHECK_DB = "alethra_e2e_upgrade_check"
PREDECESSOR_HEAD = "h2b3c4d5e6f7"
EXPECTED_HEAD = "h8i9j0k1l2m3"
# Two indexes from the revision, plus the index PostgreSQL builds for the
# unique constraint (which reflection also reports as an index).
DECISION_INDEXES = {
    "ix_human_decision_events_owner_id",
    "ix_human_decision_events_session_id",
    "uq_human_decision_owner_session_key",
}

if "alethra_e2e" not in DB or CHECK_DB in DB:
    sys.exit("Refusing: run inside the disposable alethra_e2e container database.")

sys.path.insert(0, "/app")
sys.stdout.reconfigure(line_buffering=True)

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every model
from app.auth.jwt import get_password_hash
from app.models.experiment_version import ExperimentVersion

MAINTENANCE_URL = DB.rsplit("/", 1)[0] + "/alethra_e2e"
CHECK_URL = DB.rsplit("/", 1)[0] + f"/{CHECK_DB}"
PREDECESSOR_PURPOSE = "Predecessor saved comparison (synthetic)"


def alembic(*revision: str) -> str:
    target = list(revision) or ["head"]
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", *target],
        cwd="/app",
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": CHECK_URL},
    )
    if proc.returncode != 0:
        sys.exit(f"alembic upgrade {' '.join(target)} failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    return f"{proc.stdout}\n{proc.stderr}"


def alembic_version(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


maintenance = create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT")
with maintenance.connect() as conn:
    conn.execute(text(f"DROP DATABASE IF EXISTS {CHECK_DB}"))
    conn.execute(text(f"CREATE DATABASE {CHECK_DB}"))
print(f"created disposable check database: {CHECK_DB}")

out = alembic(PREDECESSOR_HEAD)
applied = out.count("Running upgrade")
print(f"built predecessor schema at {PREDECESSOR_HEAD} ({applied} revisions applied)")
check_engine = create_engine(CHECK_URL)
assert alembic_version(check_engine) == PREDECESSOR_HEAD, "predecessor stamp mismatch"

def reflect(name: str) -> Table:
    return Table(name, MetaData(), autoload_with=check_engine)


def insert(table: Table, **values: object) -> None:
    """Insert only the columns that exist under the predecessor schema.

    The ORM would emit today's model columns (including the dataset-binding
    columns added by 8616c0c9aa3e), which do not exist yet at this point.
    """
    present = {c.name for c in table.columns}
    omitted = sorted(set(values) - present)
    for column in omitted:
        print(f"  {table.name}: skipped column absent at {PREDECESSOR_HEAD}: {column}")
    for required in table.columns:
        if (
            required.name not in omitted
            and not required.nullable
            and required.default is None
            and required.server_default is None
            and values.get(required.name) is None
        ):
            sys.exit(f"FAILED: {table.name}.{required.name} is NOT NULL but unsupplied")
    with check_engine.begin() as conn:
        conn.execute(table.insert().values(**{k: v for k, v in values.items() if k in present}))


user_id = uuid.uuid4()
experiment_id = uuid.uuid4()
version_id = uuid.uuid4()
run_a_id = uuid.uuid4()
run_b_id = uuid.uuid4()
comparison_id = uuid.uuid4()
strategy_spec = {"template_type": "buy_and_hold", "asset_ids": ["DEMO-ALPHA"], "synthetic": True}
now = datetime(2024, 3, 1, 8, 0)

insert(
    reflect("users"),
    id=user_id,
    email="predecessor.upgrade@alethra.io",
    password_hash=get_password_hash("synthetic-row-not-used-for-login"),
    display_name="Predecessor row (synthetic)",
    user_type="individual",
    is_active=True,
    is_admin=False,
    created_at=now,
    updated_at=now,
)
insert(
    reflect("experiments"),
    id=experiment_id,
    owner_id=user_id,
    title="Predecessor experiment (synthetic)",
    research_type="quantitative",
    status="locked",
    visibility="private",
    created_at=now,
    updated_at=now,
)
insert(
    reflect("experiment_versions"),
    id=version_id,
    experiment_id=experiment_id,
    version_number=1,
    strategy_spec=strategy_spec,
    created_by=user_id,
    created_at=now,
    updated_at=now,
)
with check_engine.begin() as conn:
    conn.execute(
        text("UPDATE experiments SET current_version_id = :vid WHERE id = :eid"),
        {"vid": version_id, "eid": experiment_id},
    )
for run_id, config_hash, started, completed in (
    (run_a_id, "predecessor-a", datetime(2024, 3, 1, 9, 0), datetime(2024, 3, 1, 9, 1)),
    (run_b_id, "predecessor-b", datetime(2024, 3, 1, 9, 5), datetime(2024, 3, 1, 9, 6)),
):
    insert(
        reflect("research_runs"),
        id=run_id,
        experiment_version_id=version_id,
        engine_version="predecessor-synthetic-1.0.0",
        config_hash=config_hash,
        status="completed",
        metrics={"total_return_pct": 5.0, "max_drawdown_pct": -2.0},
        started_at=started,
        completed_at=completed,
        created_at=now,
        updated_at=now,
    )
insert(
    reflect("saved_comparisons"),
    id=comparison_id,
    author_id=user_id,
    experiment_id=experiment_id,
    run_a_id=run_a_id,
    run_b_id=run_b_id,
    purpose=PREDECESSOR_PURPOSE,
    notes="written before the two new revisions existed",
    created_at=now,
    updated_at=now,
)

insp = inspect(check_engine)
before_columns = {c["name"] for c in insp.get_columns("experiment_versions")}
assert "dataset_id" not in before_columns, "predecessor build already had the new columns"

out = alembic()
head = alembic_version(check_engine)
print(f"upgraded predecessor -> head: {head} ({out.count('Running upgrade')} revisions applied)")
assert head == EXPECTED_HEAD, f"head mismatch: {head}"

insp = inspect(check_engine)
tables = insp.get_table_names()
new_columns = {c["name"] for c in insp.get_columns("experiment_versions")}
expected_columns = {
    "dataset_id",
    "data_source",
    "data_fetch_date_range",
    "data_gaps_notes",
    "data_timezone",
}
decision_columns = {c["name"] for c in insp.get_columns("human_decision_events")}
decision_indexes = {ix["name"] for ix in insp.get_indexes("human_decision_events")}
decision_unique = sorted(
    uc["name"] for uc in insp.get_unique_constraints("human_decision_events") if uc["name"]
)

with Session(check_engine) as session:
    counts = {
        "users": session.scalar(text("SELECT count(*) FROM users")),
        "experiments": session.scalar(text("SELECT count(*) FROM experiments")),
        "experiment_versions": session.scalar(text("SELECT count(*) FROM experiment_versions")),
        "research_runs": session.scalar(text("SELECT count(*) FROM research_runs")),
        "saved_comparisons": session.scalar(text("SELECT count(*) FROM saved_comparisons")),
    }
    stored_spec = session.scalar(
        text("SELECT strategy_spec::text FROM experiment_versions WHERE id = :vid"),
        {"vid": version_id},
    )
    stored_purpose = session.scalar(text("SELECT purpose FROM saved_comparisons"))

with Session(check_engine) as session:
    version_row = session.get(ExperimentVersion, version_id)
    assert version_row.dataset_id is None and version_row.data_source is None

maintenance.dispose()
check_engine.dispose()

report = {
    "head": head,
    "predecessor_revisions_applied": applied,
    "upgrade_revisions_applied": out.count("Running upgrade"),
    "human_decision_events_table": "human_decision_events" in tables,
    "new_version_columns_present": sorted(new_columns & expected_columns),
    "decision_event_columns": len(decision_columns),
    "decision_event_indexes": sorted(decision_indexes),
    "decision_event_unique_constraints": decision_unique,
    "row_counts_after_upgrade": counts,
    "strategy_spec_preserved": stored_spec is not None and "buy_and_hold" in stored_spec,
    "saved_comparison_preserved": stored_purpose == PREDECESSOR_PURPOSE,
}

print("\nPREDECESSOR-DATA UPGRADE RESULT")
for key, value in report.items():
    print(f"  {key}: {value}")

ok = (
    head == EXPECTED_HEAD
    and report["human_decision_events_table"]
    and sorted(new_columns & expected_columns) == sorted(expected_columns)
    and len(decision_columns) == 17
    and decision_indexes == DECISION_INDEXES
    and decision_unique == ["uq_human_decision_owner_session_key"]
    and counts["users"] == 1
    and counts["experiments"] == 1
    and counts["experiment_versions"] == 1
    and counts["research_runs"] == 2
    and counts["saved_comparisons"] == 1
    and report["strategy_spec_preserved"]
    and report["saved_comparison_preserved"]
)

try:
    with create_engine(MAINTENANCE_URL, isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {CHECK_DB}"))
    print(f"dropped disposable check database: {CHECK_DB}")
except Exception as exc:  # noqa: BLE001 - cleanup must not mask the verdict
    print(f"WARNING: could not drop {CHECK_DB}: {exc}")

if not ok:
    sys.exit("FAILED: predecessor-data upgrade assertions not satisfied")
print("PASS: fresh-schema predecessor data survived the two new revisions")
