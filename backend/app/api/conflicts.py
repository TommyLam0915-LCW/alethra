"""Name the unique constraint an ``IntegrityError`` violated, when it can be named.

The decision write doors used to answer *any* integrity failure with the same
409 about an idempotency key. That is a statement about what the caller did
twice, and a broken foreign key or a missing NOT NULL did not do it: the
customer was told their own retry collided, and the platform's own defect
disappeared into a conflict. A door may report a duplicate only when the
constraint that fired is the one it was written to expect. Anything else is
re-raised, so the request layer answers with its generic error code and the
failure stays visible instead of being renamed.

Both lanes have to be read here. The formal test lane runs on SQLite, which
names the violated index by its columns; PostgreSQL names it by its constraint.
An error neither form recognises is treated as unexpected, which is the safe
direction: an unnoticed duplicate surfaces as a 500 someone investigates, while
an invented conflict is a lie with a 409 on it.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from sqlalchemy.exc import IntegrityError

PAPER_ORDER_IDEMPOTENCY = "uq_paper_order_portfolio_idempotency"
HUMAN_DECISION_IDEMPOTENCY = "uq_human_decision_owner_session_key"
AGENT_PAPER_EXECUTION_INTENT = "uq_agent_paper_execution_intent"

# constraint name -> (table, columns in index order). These are the only
# constraints any caller may translate into a conflict response.
EXPECTED_UNIQUE: dict = {
    PAPER_ORDER_IDEMPOTENCY: ("paper_orders", ("portfolio_id", "idempotency_key")),
    HUMAN_DECISION_IDEMPOTENCY: (
        "human_decision_events",
        ("owner_id", "simulation_session_id", "idempotency_key"),
    ),
    AGENT_PAPER_EXECUTION_INTENT: (
        "agent_paper_executions",
        ("skill_unit_id", "job_id", "candidate_id"),
    ),
}

# PostgreSQL's sqlstate for unique_violation; NOT NULL is 23502 and a foreign
# key is 23503, which is what keeps those out of this branch.
_PG_UNIQUE_SQLSTATE = "23505"
_SQLITE_UNIQUE = re.compile(
    r"UNIQUE constraint failed:\s*(?P<columns>[A-Za-z_0-9.,\s]+)"
)


def expected_unique_violation(exc: BaseException) -> Optional[str]:
    """Return the expected constraint ``exc`` violated, or ``None``.

    ``None`` means "this is not a duplicate this door knows how to explain", and
    the caller must re-raise rather than choose an answer.
    """
    orig = getattr(exc, "orig", exc)
    name = _postgresql_constraint_name(orig)
    if name is not None:
        return name if name in EXPECTED_UNIQUE and _pg_confirms_unique(orig) else None
    return _sqlite_constraint_name(orig)


def _postgresql_constraint_name(orig: BaseException) -> Optional[str]:
    """The constraint or index PostgreSQL said, from its error diagnostics."""
    diag = getattr(orig, "diag", None)
    named = getattr(diag, "constraint_name", None) if diag is not None else None
    if named:
        return str(named)
    # Older drivers, or a name the diagnostic did not carry: PostgreSQL always
    # quotes it in the primary message.
    match = re.search(
        r'unique (?:constraint|index) "([^"]+)"', str(orig)
    )
    return match.group(1) if match else None


def _pg_confirms_unique(orig: BaseException) -> bool:
    diag = getattr(orig, "diag", None)
    sqlstate = getattr(diag, "sqlstate", None) if diag is not None else None
    return sqlstate in (None, _PG_UNIQUE_SQLSTATE)


def _sqlite_constraint_name(orig: BaseException) -> Optional[str]:
    """Match SQLite's column list against the expected index, column for column.

    The primary key answers in the same sentence shape, so the comparison is on
    the whole ordered column set and its table — ``paper_orders.id`` is not the
    idempotency index and stays unexpected.
    """
    message = str(orig)
    match = _SQLITE_UNIQUE.search(message)
    if not match:
        return None
    columns: Tuple[str, ...] = tuple(
        part.strip() for part in match.group("columns").split(",") if part.strip()
    )
    for name, (table, expected) in EXPECTED_UNIQUE.items():
        qualified = tuple(f"{table}.{column}" for column in expected)
        if columns == qualified:
            return name
    return None
