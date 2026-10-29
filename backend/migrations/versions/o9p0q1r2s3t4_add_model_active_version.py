"""add active model version pointer

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-10-29

Additive-only: one nullable column on model_research_records naming the
version the owner currently uses. The pointer never rewrites version history;
switching or rolling back only moves it, so a rollback stays an auditable
event instead of a destructive edit.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'o9p0q1r2s3t4'
down_revision = 'n8o9p0q1r2s3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('model_research_records') as batch_op:
        batch_op.add_column(
            sa.Column('active_version_id', postgresql.UUID(as_uuid=True), nullable=True)
        )
        batch_op.create_foreign_key(
            'fk_model_research_records_active_version',
            'model_versions',
            ['active_version_id'],
            ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('model_research_records') as batch_op:
        batch_op.drop_constraint(
            'fk_model_research_records_active_version', type_='foreignkey'
        )
        batch_op.drop_column('active_version_id')
