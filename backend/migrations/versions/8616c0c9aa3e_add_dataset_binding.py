"""add dataset binding to experiment versions

Revision ID: 8616c0c9aa3e
Revises: 55a8d07a7f40
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '8616c0c9aa3e'
down_revision = '55a8d07a7f40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add dataset_id foreign key to experiment_versions
    op.add_column('experiment_versions', sa.Column('dataset_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_experiment_versions_dataset_id',
        'experiment_versions',
        'datasets',
        ['dataset_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # Add metadata columns to track data source and limitations
    op.add_column('experiment_versions', sa.Column('data_source', sa.String(255), nullable=True))
    op.add_column('experiment_versions', sa.Column('data_fetch_date_range', postgresql.JSONB(), nullable=True))
    op.add_column('experiment_versions', sa.Column('data_gaps_notes', sa.Text(), nullable=True))
    op.add_column('experiment_versions', sa.Column('data_timezone', sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column('experiment_versions', 'data_timezone')
    op.drop_column('experiment_versions', 'data_gaps_notes')
    op.drop_column('experiment_versions', 'data_fetch_date_range')
    op.drop_column('experiment_versions', 'data_source')
    op.drop_constraint('fk_experiment_versions_dataset_id', 'experiment_versions', type_='foreignkey')
    op.drop_column('experiment_versions', 'dataset_id')
