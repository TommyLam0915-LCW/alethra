"""record which approved artifact produced each agent-model paper decision

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-11-22

The screening slice could already score a candidate (``POST
/training/jobs/{id}/inference``, which writes nothing) and record an owner's
activation approval (``PUT /training/jobs/{id}/activation``), and the paper
lane could book an order — but no row anywhere joined the three. An approval
that no execution consulted could not be revoked in any sense a customer could
read back, and a paper order booked from a model could not say which artifact
produced it. This table is that row, written at the moment of execution from
identities the server re-read for itself.

Why a new table rather than a column on an existing one:

* ``decision_records.linked_inference_id`` is documented at export as "the
  ``usage_records.id`` of the model execution behind the decision", and
  ``app/services/decision_lineage.py`` refuses a value that is not such a row.
  An agent job id written there would read back as a personal billing row that
  does not exist.
* ``source_run_id`` / ``source_trade_id`` name a research run and a trade.
* ``risk_flags`` is free JSON with no key contract and no read path, so it
  cannot carry an authorization the platform has to prove.

Every identity column is a foreign key to the record that carried it — job,
snapshot, strategy version, approval, candidate, and the three paper rows when a
trade was booked — so a formal read answers "which model, which artifact, which
approval, which skill account" without trusting the request that produced it.
The FKs are safe at DB level here (unlike b2c3d4e5f6g7's retrofitted columns)
because the table is new: no row predates the feature, so nothing has to be
backfilled or guessed, and a NULL in ``decision_record_id`` / ``order_id`` /
``fill_id`` means exactly what it says — the model declined, or the risk policy
did, so no trade exists to point at.

Uniqueness is the business key ``(skill_unit_id, job_id, candidate_id)``: one
skill account consults one artifact about one candidate once, so a retried
request returns the row that already exists instead of spending a second intent.

Additive-only, and it creates one table: nothing existing is read, altered or
repaired. Every precondition is checked before anything is created, and a table
that is already present — whole or half — is refused by name with nothing
changed. It does not drop and recreate.

This revision has not been executed against any PostgreSQL database. Writing a
migration is not running one; the isolated SQLite lane in ``tests/`` builds this
shape from the model with ``create_all``, which proves the model and the test
agree, not that this file does.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "e5f6g7h8i9j0"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None

TABLE = "agent_paper_executions"

# Every table a column of the new table points at. Listed oldest-dependency
# first so a refusal names the earliest thing missing.
REFERENCES = (
    "users",
    "paper_portfolios",
    "paper_orders",
    "paper_fills",
    "decision_records",
    "experiment_versions",
    "agent_candidates",
    "agent_dataset_snapshots",
    "agent_strategy_training_jobs",
    "agent_model_activation_approvals",
)

UNIQUE = "uq_agent_paper_execution_intent"
# Each index names its own columns. A positional zip of two separate tuples
# keeps working -- and builds the wrong index -- the moment one tuple is
# reordered, and the test lane (create_all, from the model) would stay green.
INDEXES = (
    ("ix_agent_paper_execution_owner", ["owner_id"]),
    ("ix_agent_paper_execution_job", ["job_id"]),
    ("ix_agent_paper_execution_skill", ["skill_unit_id"]),
)


def upgrade() -> None:
    conn = op.get_bind()
    existing = inspect(conn).get_table_names()

    if TABLE in existing:
        raise RuntimeError(
            f"Precondition failed: {TABLE} already exists. This migration is "
            "additive-only and will not overwrite or repair an existing table."
        )

    missing = [name for name in REFERENCES if name not in existing]
    if missing:
        raise RuntimeError(
            f"Precondition failed: {TABLE} has foreign keys to tables that do "
            f"not exist here: {', '.join(missing)}. Nothing was created."
        )

    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "decision_record_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fill_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("signal_date", sa.String(length=10), nullable=False),
        sa.Column("execution_date", sa.String(length=10), nullable=True),
        sa.Column("split_membership", sa.String(length=16), nullable=False),
        sa.Column("model_decision", sa.String(length=8), nullable=False),
        sa.Column("positive_probability", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column(
            "inference_policy_version", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "training_contract_version", sa.String(length=64), nullable=False
        ),
        sa.Column("dataset_checksum", sa.String(length=64), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "execution_policy_version", sa.String(length=64), nullable=False
        ),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("observed_price", sa.Float(), nullable=True),
        sa.Column("execution_price", sa.Float(), nullable=True),
        sa.Column("commission_rate", sa.Float(), nullable=True),
        sa.Column("execution_status", sa.String(length=16), nullable=False),
        sa.Column("blocked_reason", sa.String(length=64), nullable=True),
        sa.Column("risk_check_json", postgresql.JSONB(), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        # No server default: the model sets this in Python, and a default here
        # would make the migration describe a shape create_all does not.
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["skill_unit_id"], ["paper_portfolios.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["agent_strategy_training_jobs.id"]),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["agent_dataset_snapshots.id"]
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"], ["experiment_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["agent_model_activation_approvals.id"]
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["agent_candidates.id"]),
        sa.ForeignKeyConstraint(["decision_record_id"], ["decision_records.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"]),
        sa.ForeignKeyConstraint(["fill_id"], ["paper_fills.id"]),
        sa.UniqueConstraint(
            "skill_unit_id", "job_id", "candidate_id", name=UNIQUE
        ),
    )
    for name, columns in INDEXES:
        op.create_index(name, TABLE, columns)


def downgrade() -> None:
    conn = op.get_bind()

    if TABLE not in inspect(conn).get_table_names():
        raise RuntimeError(
            f"Downgrade precondition: {TABLE} does not exist. Cannot drop a "
            "table that is not present."
        )

    present = {i["name"] for i in inspect(conn).get_indexes(TABLE)}
    for name, _columns in INDEXES:
        if name in present:
            op.drop_index(name, table_name=TABLE)
    op.drop_table(TABLE)
