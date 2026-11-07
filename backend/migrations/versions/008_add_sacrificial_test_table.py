"""Add sacrificial test table for privilege enforcement tests.

This table is owned by the migration role (postgres) and used to verify
that the limited-permission runtime role cannot execute DDL operations.

The table name prefix _test_ signals it's safe to drop in production
if needed, but it should only exist in test environments.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_sacrificial_test_table'
down_revision = 'drop_old_source_constraints'
branch_labels = None
depends_on = None


def upgrade():
    # Create sacrificial table for privilege tests
    # Owned by migration role (postgres), not runtime role
    op.create_table(
        '_test_sacrificial_',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('data', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    
    # Insert one row so table is non-empty
    op.execute("INSERT INTO _test_sacrificial_ (data) VALUES ('test_data')")


def downgrade():
    op.drop_table('_test_sacrificial_')
