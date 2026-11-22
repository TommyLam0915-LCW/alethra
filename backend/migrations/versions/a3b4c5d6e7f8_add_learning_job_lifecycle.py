"""give learning jobs an owner-visible lifecycle

``POST /learning/jobs/trigger`` filed a ``pending`` row that no executor ever
claimed, so a successful request left a record that stayed pending forever, and
the scheduler's own ticks attributed that work to ``select(User).limit(1)`` —
a row owned by whoever the database happened to return first.

Adds the five columns one row needs to carry a whole operation: ``origin`` (who
put it here — ``user`` or ``schedule``; NULL only on rows that predate this
field, because no evidence records who requested them), ``operation_key`` plus
``request_fingerprint`` (a resubmission is recognised in the database rather
than by a disabled button), and ``claimed_by``/``claimed_at`` (which executor
won the row, and when its lease expires).

The unique index is per owner and covers only keyed rows, so two tenants
minting the same key cannot see or block each other while an unkeyed row stays
unconstrained. The status and origin CHECKs are created with validation: an
existing row outside the vocabulary fails this upgrade rather than letting the
constraint be recorded as enforced when it is not.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_TABLE = "learning_jobs"
_INDEX = "uq_learning_job_owner_operation"
_STATUS_CHECK = "ck_learning_jobs_status"
_ORIGIN_CHECK = "ck_learning_jobs_origin"


def _table_exists(bind) -> bool:
    return _TABLE in inspect(bind).get_table_names()


def _columns(bind) -> set:
    return {c["name"] for c in inspect(bind).get_columns(_TABLE)}


def _indexes(bind) -> set:
    return {i["name"] for i in inspect(bind).get_indexes(_TABLE)}


def _check_constraints(bind) -> set:
    return {
        c["name"]
        for c in inspect(bind).get_check_constraints(_TABLE)
        if c.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        return

    cols = _columns(bind)
    if "origin" not in cols:
        # Nullable: a row written before this field existed has no recorded
        # requester, and 'user' would be a guess about who pressed the button.
        op.add_column(
            _TABLE,
            sa.Column("origin", sa.String(16), nullable=True),
        )
    if "operation_key" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("operation_key", sa.String(128), nullable=True),
        )
    if "request_fingerprint" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("request_fingerprint", sa.String(64), nullable=True),
        )
    if "claimed_by" not in cols:
        op.add_column(_TABLE, sa.Column("claimed_by", sa.String(64), nullable=True))
    if "claimed_at" not in cols:
        op.add_column(
            _TABLE,
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if _INDEX not in _indexes(bind):
        op.create_index(
            _INDEX,
            _TABLE,
            ["user_id", "operation_key"],
            unique=True,
            postgresql_where=sa.text("operation_key IS NOT NULL"),
            sqlite_where=sa.text("operation_key IS NOT NULL"),
        )

    checks = _check_constraints(bind)
    if _STATUS_CHECK not in checks:
        op.create_check_constraint(
            _STATUS_CHECK,
            _TABLE,
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
        )
    if _ORIGIN_CHECK not in checks:
        op.create_check_constraint(
            _ORIGIN_CHECK,
            _TABLE,
            "origin IN ('user', 'schedule')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind):
        return

    checks = _check_constraints(bind)
    if _ORIGIN_CHECK in checks:
        op.drop_constraint(_ORIGIN_CHECK, _TABLE, type_="check")
    if _STATUS_CHECK in checks:
        op.drop_constraint(_STATUS_CHECK, _TABLE, type_="check")
    if _INDEX in _indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)

    cols = _columns(bind)
    for name in ("claimed_at", "claimed_by", "request_fingerprint", "operation_key", "origin"):
        if name in cols:
            op.drop_column(_TABLE, name)
