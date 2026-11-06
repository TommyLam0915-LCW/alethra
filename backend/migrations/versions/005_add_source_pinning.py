"""Add source pinning columns to decision_records and paper_positions.

Revision ID: add_source_pinning
Revises: 004_add_paper_trading_unique_constraint
Create Date: 2026-11-06

Adds columns to track the source of each paper trade back to the original
research data, enabling full traceability and audit.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP

# revision identifiers, used by Alembic.
revision = 'add_source_pinning'
down_revision = '004_add_paper_trading_unique_constraint'
branch_labels = None
depends_on = None


def upgrade():
    # Add source pinning columns to decision_records
    op.add_column('decision_records', sa.Column('source_trade_id', UUID(as_uuid=True), nullable=True))
    op.add_column('decision_records', sa.Column('source_run_id', UUID(as_uuid=True), nullable=True))
    op.add_column('decision_records', sa.Column('source_rule_id', sa.String(100), nullable=True))
    op.add_column('decision_records', sa.Column('source_exit_config', sa.String(100), nullable=True))
    op.add_column('decision_records', sa.Column('source_data_version', sa.String(50), nullable=True))
    op.add_column('decision_records', sa.Column('cutoff_timestamp', TIMESTAMP(timezone=True), nullable=True))
    op.add_column('decision_records', sa.Column('as_of_timestamp', TIMESTAMP(timezone=True), nullable=True))
    
    # Add source_trade_id to paper_positions
    op.add_column('paper_positions', sa.Column('source_trade_id', UUID(as_uuid=True), nullable=True))
    
    # Add indexes for source pinning lookups
    op.create_index('idx_decision_records_source_trade', 'decision_records', ['source_trade_id'])
    op.create_index('idx_decision_records_source_run', 'decision_records', ['source_run_id'])
    op.create_index('idx_paper_positions_source_trade', 'paper_positions', ['source_trade_id'])


def downgrade():
    # Remove indexes
    op.drop_index('idx_paper_positions_source_trade', table_name='paper_positions')
    op.drop_index('idx_decision_records_source_run', table_name='decision_records')
    op.drop_index('idx_decision_records_source_trade', table_name='decision_records')
    
    # Remove columns from paper_positions
    op.drop_column('paper_positions', 'source_trade_id')
    
    # Remove columns from decision_records
    op.drop_column('decision_records', 'as_of_timestamp')
    op.drop_column('decision_records', 'cutoff_timestamp')
    op.drop_column('decision_records', 'source_data_version')
    op.drop_column('decision_records', 'source_exit_config')
    op.drop_column('decision_records', 'source_rule_id')
    op.drop_column('decision_records', 'source_run_id')
    op.drop_column('decision_records', 'source_trade_id')
