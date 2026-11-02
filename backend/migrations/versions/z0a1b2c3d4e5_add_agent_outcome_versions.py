"""make agent candidate outcomes append-only with a selected version

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-11-02

An outcome row was previously unique per candidate, so a newly observed paper
round trip overwrote the counterfactual replay that had already been used to
build a dataset snapshot. That made a frozen snapshot non-reproducible.

This revision keeps every evaluation as its own row:

* ``version_seq`` is monotonic per candidate;
* ``is_current`` marks the single selected result, enforced by a partial
  unique index;
* ``superseded_at`` and ``selection_reason`` explain why a result stopped
  being selected.

Nothing is deleted. Existing rows become version 1 and stay selected, so a
snapshot built before this revision still resolves to the same content.

The downgrade refuses when any candidate has more than one version: collapsing
history would silently join rows that the old schema could not represent.
"""
from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None

_TABLE = "agent_candidate_outcomes"
_OLD_UNIQUE_NAME = "agent_candidate_outcomes_candidate_id_key"


def _unique_on_candidate_id(bind) -> str | None:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints(_TABLE):
        if constraint.get("column_names") == ["candidate_id"]:
            return constraint.get("name")
    for index in inspector.get_indexes(_TABLE):
        if index.get("unique") and index.get("column_names") == ["candidate_id"]:
            return index.get("name")
    return None


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "version_seq", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(_TABLE, sa.Column("superseded_at", sa.DateTime(), nullable=True))
    op.add_column(
        _TABLE, sa.Column("selection_reason", sa.String(length=64), nullable=True)
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET version_seq = 1 WHERE version_seq IS NULL"
        )
    )
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET is_current = true WHERE is_current IS NULL"
        )
    )

    existing = _unique_on_candidate_id(bind)
    if existing:
        if bind.dialect.name == "sqlite":
            # SQLite cannot drop a table-level constraint in place; batch mode
            # rebuilds the table while preserving every row.
            with op.batch_alter_table(_TABLE, recreate="always") as batch:
                batch.drop_constraint(existing, type_="unique")
        else:
            op.drop_constraint(existing, _TABLE, type_="unique")

    op.create_unique_constraint(
        "uq_agent_candidate_outcome_version",
        _TABLE,
        ["candidate_id", "version_seq"],
    )
    op.create_index(
        "uq_agent_candidate_outcome_current",
        _TABLE,
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_agent_candidate_outcome_candidate", _TABLE, ["candidate_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            f"SELECT candidate_id FROM {_TABLE} GROUP BY candidate_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Refusing to downgrade: agent_candidate_outcomes has multiple "
            "versions for at least one candidate. The old schema cannot hold "
            "that history, so dropping it would lose evaluation evidence."
        )

    op.drop_index("ix_agent_candidate_outcome_candidate", table_name=_TABLE)
    op.drop_index("uq_agent_candidate_outcome_current", table_name=_TABLE)
    op.drop_constraint(
        "uq_agent_candidate_outcome_version", _TABLE, type_="unique"
    )
    op.create_unique_constraint(_OLD_UNIQUE_NAME, _TABLE, ["candidate_id"])
    op.drop_column(_TABLE, "selection_reason")
    op.drop_column(_TABLE, "superseded_at")
    op.drop_column(_TABLE, "is_current")
    op.drop_column(_TABLE, "version_seq")
