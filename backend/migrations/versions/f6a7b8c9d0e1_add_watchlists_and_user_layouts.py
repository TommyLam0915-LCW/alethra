"""add watchlists, watchlist_items, user_layouts

Shared workspace shell (TradingView/Futu-style): per-user watchlists
with ordered instrument symbols, and per-user persisted workspace
layout JSON.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-18 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return False
    return index in {i["name"] for i in inspect(bind).get_indexes(table)}


def upgrade() -> None:
    # Existence guards keep this idempotent: databases bootstrapped via
    # Base.metadata.create_all (app startup / legacy upgrade service)
    # already carry these tables.
    if not _table_exists("watchlists"):
        op.create_table(
            "watchlists",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),
        )
        op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])

    if not _table_exists("watchlist_items"):
        op.create_table(
            "watchlist_items",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("watchlist_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("added_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["watchlist_id"], ["watchlists.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "watchlist_id", "symbol", name="uq_watchlist_item_symbol"
            ),
        )
        op.create_index(
            "ix_watchlist_items_watchlist_id", "watchlist_items", ["watchlist_id"]
        )

    if not _table_exists("user_layouts"):
        op.create_table(
            "user_layouts",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("layout_key", sa.String(64), nullable=False),
            sa.Column("layout_data", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "layout_key", name="uq_user_layout_key"),
        )
        op.create_index("ix_user_layouts_user_id", "user_layouts", ["user_id"])


def downgrade() -> None:
    if _table_exists("watchlist_items"):
        if _index_exists("watchlist_items", "ix_watchlist_items_watchlist_id"):
            op.drop_index(
                "ix_watchlist_items_watchlist_id", table_name="watchlist_items"
            )
        op.drop_table("watchlist_items")
    if _table_exists("watchlists"):
        if _index_exists("watchlists", "ix_watchlists_user_id"):
            op.drop_index("ix_watchlists_user_id", table_name="watchlists")
        op.drop_table("watchlists")
    if _table_exists("user_layouts"):
        if _index_exists("user_layouts", "ix_user_layouts_user_id"):
            op.drop_index("ix_user_layouts_user_id", table_name="user_layouts")
        op.drop_table("user_layouts")
