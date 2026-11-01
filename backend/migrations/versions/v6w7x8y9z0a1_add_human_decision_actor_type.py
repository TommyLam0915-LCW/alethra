"""record the verified authentication channel on human decision events

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-11-01

Additive-only. ``actor_type`` is one of:

* ``human``   — written through a password-authenticated session
* ``agent``   — written through an API key
* ``unknown`` — any other channel, including tokens without an auth-method claim

Existing rows default to ``unknown``. They are not rewritten to ``human``:
nobody verified that channel at the time, and a batch update would turn an
unverified label into an owner-confirmed one.
"""
from alembic import op
import sqlalchemy as sa


revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "human_decision_events",
        sa.Column(
            "actor_type",
            sa.String(length=16),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.create_check_constraint(
        "ck_human_decision_actor_type",
        "human_decision_events",
        "actor_type IN ('human', 'agent', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_human_decision_actor_type",
        "human_decision_events",
        type_="check",
    )
    op.drop_column("human_decision_events", "actor_type")
