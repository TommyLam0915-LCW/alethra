"""add personalization training consent

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-10-29

Additive-only: a new table. Consent is stored separately from research data
and AI-context opt-ins so that saving data can never be mistaken for consent
to train on it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'm7n8o9p0q1r2'
down_revision = 'l6m7n8o9p0q1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'personalization_consents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('purpose', sa.String(length=64), nullable=False),
        sa.Column('consent_version', sa.String(length=64), nullable=False),
        sa.Column('scope_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('granted_at', sa.DateTime(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_personalization_consent_owner_purpose',
        'personalization_consents',
        ['owner_id', 'purpose'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_personalization_consent_owner_purpose',
        table_name='personalization_consents',
    )
    op.drop_table('personalization_consents')
