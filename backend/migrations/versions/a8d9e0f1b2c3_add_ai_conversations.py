"""add ai_conversations, ai_messages, research_drafts.source_conversation_id

Personal AI assistant: per-user conversation threads with persisted
messages (provider/model/token accounting), plus a nullable link from
research drafts to the AI conversation that proposed them.

Revision ID: a8d9e0f1b2c3
Revises: f6a7b8c9d0e1
Create Date: 2026-10-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a8d9e0f1b2c3"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def _columns(table: str) -> set:
    bind = op.get_bind()
    if table not in inspect(bind).get_table_names():
        return set()
    return {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    # Existence guards keep this idempotent: databases bootstrapped via
    # Base.metadata.create_all (app startup / legacy upgrade service)
    # already carry these tables.
    if not _table_exists("ai_conversations"):
        op.create_table(
            "ai_conversations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("context", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ai_conversations_user_id", "ai_conversations", ["user_id"]
        )

    if not _table_exists("ai_messages"):
        op.create_table(
            "ai_messages",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("role", sa.String(16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("provider", sa.String(64), nullable=True),
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False),
            sa.Column("completion_tokens", sa.Integer(), nullable=False),
            sa.Column("cost_cents", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["conversation_id"], ["ai_conversations.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"]
        )

    if "source_conversation_id" not in _columns("research_drafts"):
        op.add_column(
            "research_drafts",
            sa.Column(
                "source_conversation_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("ai_conversations.id"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_research_drafts_source_conversation_id",
            "research_drafts",
            ["source_conversation_id"],
        )


def downgrade() -> None:
    if "source_conversation_id" in _columns("research_drafts"):
        op.drop_index(
            "ix_research_drafts_source_conversation_id",
            table_name="research_drafts",
        )
        op.drop_column("research_drafts", "source_conversation_id")
    if _table_exists("ai_messages"):
        op.drop_index("ix_ai_messages_conversation_id", table_name="ai_messages")
        op.drop_table("ai_messages")
    if _table_exists("ai_conversations"):
        op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations")
        op.drop_table("ai_conversations")
