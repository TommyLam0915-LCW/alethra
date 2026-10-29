"""add decision labels and personalization dataset snapshots

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-10-29

Additive-only:
* decision_labels — append-only owner review revisions over recorded human
  decisions. The underlying decision event is never modified.
* personalization_dataset_snapshots — the exact rows the personal trainer
  consumes, with consent scope, feature schema, exclusion reasons and a
  checksum.
* personal_decision_model — a new model type for real scikit-learn artifacts
  trained on the owner's confirmed decisions (never a rule spec).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'p0q1r2s3t4u5'
down_revision = 'o9p0q1r2s3t4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # PostgreSQL enums gain values outside the row transaction.
        bind.execute(
            sa.text(
                "ALTER TYPE modeltype ADD VALUE IF NOT EXISTS "
                "'personal_decision_model'"
            )
        )

    op.create_table(
        'decision_labels',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('decision_event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column(
            'state',
            sa.Enum('confirmed', 'excluded', name='decisionlabelstate'),
            nullable=False,
        ),
        sa.Column('action_label', sa.String(length=8), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'exclusion_reason',
            sa.Enum(
                'data_entry_error',
                'test_trade',
                'outside_strategy',
                'other',
                name='decisionexclusionreason',
            ),
            nullable=True,
        ),
        sa.Column('author_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('supersedes_label_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            'metadata_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['decision_event_id'], ['human_decision_events.id']),
        sa.ForeignKeyConstraint(['author_id'], ['users.id']),
        sa.ForeignKeyConstraint(['supersedes_label_id'], ['decision_labels.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'decision_event_id',
            'revision_number',
            name='uq_decision_label_event_revision',
        ),
    )
    op.create_index(
        'ix_decision_labels_event',
        'decision_labels',
        ['decision_event_id'],
    )

    op.create_table(
        'personalization_dataset_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('strategy_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('consent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('consent_version', sa.String(length=64), nullable=False),
        sa.Column(
            'status',
            sa.Enum('ready', 'failed', name='personalizationsnapshotstatus'),
            nullable=False,
        ),
        sa.Column(
            'rows_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'feature_names_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'label_definition_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'exclusion_breakdown_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            'class_distribution_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column('included_count', sa.Integer(), nullable=False),
        sa.Column('excluded_count', sa.Integer(), nullable=False),
        sa.Column('date_range_start', sa.String(length=10), nullable=True),
        sa.Column('date_range_end', sa.String(length=10), nullable=True),
        sa.Column('data_cutoff_at', sa.String(length=10), nullable=True),
        sa.Column('checksum', sa.String(length=64), nullable=False),
        sa.Column('failure_reason', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.ForeignKeyConstraint(['strategy_version_id'], ['experiment_versions.id']),
        sa.ForeignKeyConstraint(['consent_id'], ['personalization_consents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_personalization_snapshot_owner_strategy',
        'personalization_dataset_snapshots',
        ['owner_id', 'strategy_version_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_personalization_snapshot_owner_strategy',
        table_name='personalization_dataset_snapshots',
    )
    op.drop_table('personalization_dataset_snapshots')
    op.drop_index('ix_decision_labels_event', table_name='decision_labels')
    op.drop_table('decision_labels')
    # PostgreSQL cannot remove an enum value; the added value is inert if the
    # tables above are dropped, so the downgrade leaves it in place.
