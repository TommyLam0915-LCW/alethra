"""link robustness studies to their single authorization

A robustness study consumes one research-run authorization for the whole
study, charged once when it starts. The link column lets the run route claim
that charge atomically, so two concurrent runs of the same study cannot both
reserve a unit. Additive only: no existing table, row or value is rewritten.

Revision ID: d5f6a7b8c9d0
Revises: c4e5f6a7b8c9
Create Date: 2026-11-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import UUID


revision = "d5f6a7b8c9d0"
down_revision = "c4e5f6a7b8c9"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "robustness_studies", "authorization_reservation_id"):
        return
    op.add_column(
        "robustness_studies",
        sa.Column(
            "authorization_reservation_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "quota_reservations.id",
                ondelete="SET NULL",
                name="fk_robustness_study_authorization_reservation",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_robustness_study_authorization_reservation",
        "robustness_studies",
        ["authorization_reservation_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "robustness_studies", "authorization_reservation_id"):
        return
    op.drop_index(
        "ix_robustness_study_authorization_reservation",
        table_name="robustness_studies",
    )
    op.drop_column("robustness_studies", "authorization_reservation_id")
