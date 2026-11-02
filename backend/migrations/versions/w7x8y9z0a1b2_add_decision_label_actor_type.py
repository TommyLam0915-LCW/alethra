"""record the authentication channel of a decision label revision

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-11-02

Additive-only. A label is the owner's explicit confirmation act, so the
channel that wrote it is part of the provenance record:

* ``human``   — written through a password-authenticated owner session
* ``agent``   — written through an API key
* ``unknown`` — any other channel, including rows written before this column

Existing revisions default to ``unknown`` and are never rewritten. They are
reported as unverified confirmations rather than promoted to owner-confirmed.
"""
from alembic import op
import sqlalchemy as sa


revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_labels",
        sa.Column(
            "actor_type",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_check_constraint(
        "ck_decision_label_actor_type",
        "decision_labels",
        "actor_type IN ('human', 'agent', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_decision_label_actor_type",
        "decision_labels",
        type_="check",
    )
    op.drop_column("decision_labels", "actor_type")
