"""add dataset binding to experiment versions

Rebased onto the unified worktree's head h2b3c4d5e6f7 (add_saved_comparisons).
The original B revision had down_revision='55a8d07a7f40' which conflicts with
the handoff topology; the operations themselves (add columns + FK to datasets)
are unchanged.

Revision ID: 8616c0c9aa3e
Revises: h2b3c4d5e6f7
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = '8616c0c9aa3e'
down_revision = 'h2b3c4d5e6f7'
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists('experiment_versions', 'dataset_id'):
        op.add_column('experiment_versions', sa.Column('dataset_id', postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            'fk_experiment_versions_dataset_id',
            'experiment_versions',
            'datasets',
            ['dataset_id'],
            ['id'],
            ondelete='SET NULL',
        )
    if not _column_exists('experiment_versions', 'data_source'):
        op.add_column('experiment_versions', sa.Column('data_source', sa.String(255), nullable=True))
    if not _column_exists('experiment_versions', 'data_fetch_date_range'):
        op.add_column('experiment_versions', sa.Column('data_fetch_date_range', postgresql.JSONB(), nullable=True))
    if not _column_exists('experiment_versions', 'data_gaps_notes'):
        op.add_column('experiment_versions', sa.Column('data_gaps_notes', sa.Text(), nullable=True))
    if not _column_exists('experiment_versions', 'data_timezone'):
        op.add_column('experiment_versions', sa.Column('data_timezone', sa.String(50), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists('experiment_versions', 'data_timezone'):
        op.drop_column('experiment_versions', 'data_timezone')
    if _column_exists('experiment_versions', 'data_gaps_notes'):
        op.drop_column('experiment_versions', 'data_gaps_notes')
    if _column_exists('experiment_versions', 'data_fetch_date_range'):
        op.drop_column('experiment_versions', 'data_fetch_date_range')
    if _column_exists('experiment_versions', 'data_source'):
        op.drop_column('experiment_versions', 'data_source')
    if _column_exists('experiment_versions', 'dataset_id'):
        try:
            op.drop_constraint('fk_experiment_versions_dataset_id', 'experiment_versions', type_='foreignkey')
        except Exception:
            pass
        op.drop_column('experiment_versions', 'dataset_id')
