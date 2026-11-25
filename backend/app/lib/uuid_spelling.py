"""Reading rows a UUID column may still hold in the dashed spelling.

The product's own raw-SQL writers — ``POST /api/v1/paper-trading/create`` and
the live trading daemon — used to bind ``str(uuid4())``, the dashed
36-character text, into ``paper_portfolios.id``/``owner_id`` and
``paper_trading_runs.portfolio_id``, while the ORM bound its own UUID type and
stored 32 characters with no dashes. On PostgreSQL the column is a real ``uuid``
and both bind strings parse to the same value, so the difference was invisible
there; on this project's SQLite lane the column is text, the two spellings are
different strings, and an identity-key read could not name the rows the create
route had just made.

Those writers are normalised now (``app/lib/uuid_bind.py`` binds each column's
own type), so rows created from here on are in the spelling every reader already
uses, and the manual-order path that used to fail with ``StaleDataError`` runs on
the lane. What this module covers is the other half: a row that is *already*
stored dashed. No deployed database has been inspected for such rows -- the
protected ones were not touched -- so the read tolerance below stays until that
is measured and ruled on, and rewriting existing rows is a separate decision
rather than something this file assumes was needed.

Two forms, chosen by what the read is:

``dashed_text``
    A second chance at one row by id, used only after the indexed identity
    comparison found nothing. The fallback never runs on PostgreSQL, so the
    locking reads keep their index range scan there.

``stored_as``
    One predicate for a read that must cover a whole set (a count, a list),
    where no spelling can be assumed. On PostgreSQL the second limb matches
    exactly the rows the first already matched, so the union is that row once.
"""

import uuid

from sqlalchemy import String, cast, or_


def _dashed(value) -> str:
    return str(uuid.UUID(str(value)))


def dashed_text(column, value):
    """``column`` equal to ``value`` in the dashed 36-character spelling."""
    return cast(column, String) == _dashed(value)


def stored_as(column, value):
    """``column`` equal to ``value`` in whichever spelling it was stored."""
    return or_(column == uuid.UUID(str(value)), dashed_text(column, value))
