"""add human_decision_events table

Record-only human decision events for auditable replay-mode decision
tracking. No execution, no order/fill creation.

Rebased onto 8616c0c9aa3e (add_dataset_binding) so the unified worktree's
migration chain extends handoff head h2b3c4d5e6f7 → 8616c0c9aa3e →
h8i9j0k1l2m3 without pulling in B's fill_commission revision
(g7h8i9j0k1l2), which handoff does not model.

Revision ID: h8i9j0k1l2m3
Revises: 8616c0c9aa3e
Create Date: 2026-10-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy import inspect


revision = 'h8i9j0k1l2m3'
down_revision = '8616c0c9aa3e'
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def upgrade():
    bind = op.get_bind()
    if _table_exists(bind, 'human_decision_events'):
        return

    op.create_table(
        'human_decision_events',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('owner_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('simulation_session_id', UUID(as_uuid=True), sa.ForeignKey('simulation_sessions.id'), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('quantity', sa.Float(), nullable=True),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('server_strategy_version_id', UUID(as_uuid=True), nullable=False),
        sa.Column('server_instrument_id', sa.String(64), nullable=False),
        sa.Column('server_step_number', sa.Integer(), nullable=False),
        sa.Column('server_snapshot_date', sa.String(10), nullable=False),
        sa.Column('server_data_cutoff_at', sa.String(10), nullable=False),
        sa.Column('execution_status', sa.String(20), nullable=False, server_default='record_only'),
        sa.Column('supersedes_event_id', UUID(as_uuid=True), sa.ForeignKey('human_decision_events.id'), nullable=True),
        sa.Column('metadata_json', JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.UniqueConstraint('owner_id', 'simulation_session_id', 'idempotency_key', name='uq_human_decision_owner_session_key'),
    )
    op.create_index('ix_human_decision_events_owner_id', 'human_decision_events', ['owner_id'])
    op.create_index('ix_human_decision_events_session_id', 'human_decision_events', ['simulation_session_id'])


def downgrade():
    bind = op.get_bind()
    if not _table_exists(bind, 'human_decision_events'):
        return
    op.drop_index('ix_human_decision_events_session_id', table_name='human_decision_events')
    op.drop_index('ix_human_decision_events_owner_id', table_name='human_decision_events')
    op.drop_table('human_decision_events')
