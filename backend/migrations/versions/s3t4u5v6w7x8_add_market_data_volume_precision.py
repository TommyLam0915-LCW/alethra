"""widen market_data_bars.volume for fractional crypto volumes

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
Create Date: 2026-10-30

Additive-only, non-destructive:
* ``volume`` becomes nullable; existing equity rows keep their integer value.
* ``volume_decimal`` (NUMERIC(30,12)) is added for venues that trade
  fractional amounts. Crypto rows leave ``volume`` NULL and store the exact
  amount here, so a 0.00076182 BTC bar is never rounded to 0 or 1.

Nothing is backfilled and no existing row is rewritten. Downgrade restores
NOT NULL, so it must not be run while NULL volumes exist.
"""
from alembic import op
import sqlalchemy as sa


revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("market_data_bars") as batch:
        batch.alter_column("volume", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column("volume_decimal", sa.Numeric(30, 12), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("market_data_bars") as batch:
        batch.drop_column("volume_decimal")
        batch.alter_column("volume", existing_type=sa.BigInteger(), nullable=False)
