"""runstatus gains 'cancelled': a research run can be closed by decision

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-16

Why a new state instead of a timeout: ``research_runs`` admits durably, so an
interrupted attempt leaves a committed non-terminal row that holds its version
(409) and its quota unit. Recovery refuses to close it on age, because a slow
backtest and a killed one are the same row. ``POST /api/v1/runs/{id}/cancel``
lets the account that started the work record an outcome it actually knows —
that it gave up — and hands the unit back. That outcome needs a value of its
own: writing it as ``failed`` would tell the reader a computation broke.

The value is added, never reinterpreted. Rows already in ``pending``,
``running``, ``completed`` or ``failed`` keep their meaning, and the two
predicates that decide who may publish an outcome (``status IN
('pending','running')``) are unchanged, so a cancelled row stays closed to a
late writer without any backfill here.

PostgreSQL only: on SQLite the column is VARCHAR, so the same statement would
have nothing to alter. A value cannot be removed from a PostgreSQL enum, so
``downgrade`` leaves it in place — the code that reads it goes back, the label
stays, and the row values that use it are the only thing that can disappear with
the rows themselves.
"""
from typing import List, Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUM_TYPE = "runstatus"
NEW_VALUE = "cancelled"


def _labels(bind) -> Union[List[str], None]:
    """The enum's values in declaration order, or None if the type is absent."""
    if bind.dialect.name != "postgresql":
        return None
    found = bind.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_type t "
            "JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname = :name ORDER BY e.enumsortorder"
        ),
        {"name": ENUM_TYPE},
    ).fetchall()
    return None if not found else [row[0] for row in found]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    labels = _labels(bind)
    if labels is None:
        raise RuntimeError(
            f"the {ENUM_TYPE} enum type does not exist, so this revision has no "
            "state to add a value to. Nothing was changed."
        )
    if NEW_VALUE in labels:
        raise RuntimeError(
            f"{ENUM_TYPE} already carries '{NEW_VALUE}'. This revision adds that "
            "value and expects it to be absent; it refuses instead of assuming "
            "that whatever put it there means the same thing this lane means by "
            "it. Nothing was changed."
        )

    # ALTER TYPE ... ADD VALUE takes effect for other sessions only once this
    # transaction commits, which is exactly when the code that writes the value
    # starts running.
    bind.execute(
        sa.text(f"ALTER TYPE {ENUM_TYPE} ADD VALUE IF NOT EXISTS '{NEW_VALUE}'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    labels = _labels(bind) or []
    print(
        f"runstatus keeps {labels} on downgrade: PostgreSQL cannot remove a "
        "value from an enum type, and no stored row is rewritten by this "
        "revision. The application that reads 'cancelled' goes back with the "
        "code, not with the type."
    )
