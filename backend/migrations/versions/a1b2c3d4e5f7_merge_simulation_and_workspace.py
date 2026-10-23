"""merge simulation and workspace branch heads

Revision ID: a1b2c3d4e5f7
Revises: a8d9e0f1b2c3, g1a2b3c4d5e6, add_state_version
Create Date: 2026-10-23

Merges three branch heads into a single head:
 - a8d9e0f1b2c3: ai_conversations (workspace branch)
 - g1a2b3c4d5e6: run_result_json (workspace branch)
 - add_state_version: state_version (simulation branch, reparented
   from None to e3f4a5b6c7d8)

This is a no-op merge: no schema changes, only graph convergence.
"""
from alembic import op

revision = 'a1b2c3d4e5f7'
down_revision = ('a8d9e0f1b2c3', 'g1a2b3c4d5e6', 'add_state_version')
branch_labels = None
depends_on = None

def upgrade():
    pass

def downgrade():
    pass
