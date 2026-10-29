"""add robustness studies and attempts

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-10-29

Additive-only: two new tables. A study stores the bounded plan it announced
before running; each attempt stores the exact spec, period and research run it
produced, including failures. ``(study_id, cell_key)`` is unique so resuming a
study reuses a cell instead of duplicating a run.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'n8o9p0q1r2s3'
down_revision = 'm7n8o9p0q1r2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'robustness_studies',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('experiment_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('aggregate', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id']),
        sa.ForeignKeyConstraint(['version_id'], ['experiment_versions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_robustness_study_experiment',
        'robustness_studies',
        ['experiment_id', 'created_at'],
    )
    op.create_index(
        'ix_robustness_study_user_request',
        'robustness_studies',
        ['user_id', 'request_id'],
    )

    op.create_table(
        'robustness_attempts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('study_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cell_key', sa.String(length=200), nullable=False),
        sa.Column('label', sa.String(length=300), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('spec', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('overrides', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('period_start', sa.String(length=10), nullable=True),
        sa.Column('period_end', sa.String(length=10), nullable=True),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('metrics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('dataset_checksum', sa.String(length=64), nullable=True),
        sa.Column('data_identity', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['study_id'], ['robustness_studies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['research_runs.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('study_id', 'cell_key', name='uq_robustness_attempt_cell'),
    )
    op.create_index(
        'ix_robustness_attempt_study',
        'robustness_attempts',
        ['study_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_robustness_attempt_study', table_name='robustness_attempts')
    op.drop_table('robustness_attempts')
    op.drop_index('ix_robustness_study_user_request', table_name='robustness_studies')
    op.drop_index('ix_robustness_study_experiment', table_name='robustness_studies')
    op.drop_table('robustness_studies')
