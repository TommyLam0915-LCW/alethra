"""add skill-unit association columns for unified tracing

Revision ID: b2c3d4e5f6g7
Revises: f2a3b4c5d6e7
Create Date: 2026-11-17

The unified skill model (§16.6 A2 → §17 design) requires tracing from owner
through skill unit to strategy version, dataset, and model activation. This
migration adds the columns that make that chain queryable:

- ``paper_trading_runs.strategy_version_id``: which experiment version this run uses
- ``paper_trading_runs.dataset_id``: which dataset feeds this run (denormalized)
- ``personal_model_selections.skill_unit_id``: which portfolio this selection serves
- ``agent_model_activation_approvals.skill_unit_id``: which portfolio this activation targets

Training jobs and model versions are NOT at the skill-unit level — they belong
to experiments and model research records respectively. A training job trains a
strategy (experiment), then multiple skill units may use versions of that
strategy. Adding skill_unit_id to those tables would misrepresent the product
design.

All columns are NULLABLE because:
1. These columns are added to existing tables with rows that predate the feature.
2. Existing rows cannot be backfilled reliably — attributing an old selection to
   a skill unit would be guessing, and guessing ownership violates the provenance
   invariant.
3. The entry-point validation (see ``app/api/paper_portfolio.py`` POST decisions
   and ``app/api/paper_trading.py`` POST create) enforces consistency for new
   writes without requiring old rows to change.

No FK constraints are added at the DB level because:
1. These columns are added to existing tables with rows that predate the feature.
2. ``paper_trading_runs.strategy_version_id`` points to ``experiment_versions``,
   but existing runs have no version and cannot be backfilled; a FK would block
   those rows from existing.
3. The ``skill_unit_id`` columns point to ``paper_portfolios``, but existing
   selections/activations predate the skill-unit concept; a FK would require
   dropping or faking those rows.

Instead, the application validates references at write time (see the entry-point
contracts in the design doc). This keeps the schema permissive while the service
layer enforces the consistency rule.

Additive-only. Every precondition is checked before anything is created, and
any partial state is refused by name with nothing changed. It does not repair
and it does not drop and recreate.

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b2c3d4e5f6g7"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists in a table (SQLite + PostgreSQL compatible)."""
    inspector = inspect(conn)
    columns = [col["name"] for col in inspector.get_columns(table)]
    return column in columns


def _require_absent(conn, table: str, column: str):
    """Refuse if the column already exists — additive-only policy."""
    if _column_exists(conn, table, column):
        raise RuntimeError(
            f"Precondition failed: {table}.{column} already exists. "
            f"This migration is additive-only and will not overwrite existing state."
        )


def upgrade() -> None:
    conn = op.get_bind()

    # Phase 1: Verify all preconditions before any write
    _require_absent(conn, "paper_trading_runs", "strategy_version_id")
    _require_absent(conn, "paper_trading_runs", "dataset_id")
    _require_absent(conn, "personal_model_selections", "skill_unit_id")
    _require_absent(conn, "agent_model_activation_approvals", "skill_unit_id")

    # Phase 2: Add columns (all nullable, no FK constraints at DB level)
    op.add_column(
        "paper_trading_runs",
        sa.Column("strategy_version_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "paper_trading_runs",
        sa.Column("dataset_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "personal_model_selections",
        sa.Column("skill_unit_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "agent_model_activation_approvals",
        sa.Column("skill_unit_id", sa.UUID(), nullable=True),
    )

    # Phase 3: Add indexes for query performance (not uniqueness — the
    # application enforces consistency, not the schema)
    op.create_index(
        "ix_paper_trading_runs_strategy_version_id",
        "paper_trading_runs",
        ["strategy_version_id"],
    )
    op.create_index(
        "ix_paper_trading_runs_dataset_id",
        "paper_trading_runs",
        ["dataset_id"],
    )
    op.create_index(
        "ix_personal_model_selections_skill_unit_id",
        "personal_model_selections",
        ["skill_unit_id"],
    )
    op.create_index(
        "ix_agent_model_activation_approvals_skill_unit_id",
        "agent_model_activation_approvals",
        ["skill_unit_id"],
    )


def downgrade() -> None:
    # Drop indexes first, then columns — reverse of upgrade order
    op.drop_index(
        "ix_agent_model_activation_approvals_skill_unit_id",
        "agent_model_activation_approvals",
    )
    op.drop_index(
        "ix_personal_model_selections_skill_unit_id", "personal_model_selections"
    )
    op.drop_index("ix_paper_trading_runs_dataset_id", "paper_trading_runs")
    op.drop_index(
        "ix_paper_trading_runs_strategy_version_id", "paper_trading_runs"
    )

    op.drop_column("agent_model_activation_approvals", "skill_unit_id")
    op.drop_column("personal_model_selections", "skill_unit_id")
    op.drop_column("paper_trading_runs", "dataset_id")
    op.drop_column("paper_trading_runs", "strategy_version_id")
