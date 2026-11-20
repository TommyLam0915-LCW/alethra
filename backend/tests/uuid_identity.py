"""One rule for matching a uuid identity in a raw SQL statement.

Why this exists is a measured fact, not a theory. On the SQLite lane a
``postgresql.UUID(as_uuid=True)`` column is created by
``tests/conftest.py:128`` as ``VARCHAR(36)`` — the width of a dashed UUID —
but SQLAlchemy's emulated bind processor (``sqlalchemy/sql/sqltypes.py``,
``Uuid.bind_processor``) writes ``value.hex``, 32 chars without dashes. The DDL
and the data therefore disagree, and a third form enters through any raw
``text()`` INSERT, which binds no column type and so stores the string it was
handed — the fixtures pass ``str(uuid)``, which is dashed.

The result, measured on this host against the lane's own schema:

    writer                bytes stored in the column    ``= str(uid)``    ``= uid.hex``
    ORM / Core            32-char hex                   0 rows            1 row
    raw text() INSERT     36-char dashed                1 row             0 rows

So the *same column* physically holds two different spellings depending on
which layer wrote the row, and a single-spelling equality predicate silently
returns nothing. Silently returning nothing is the expensive part: a cleanup
that probes "does this user exist" with one spelling says "no" and skips its
work, and a verification that counts residuals with one spelling reports zero
residuals for rows it cannot see. Both read as success.

PostgreSQL has no such problem — a ``uuid`` column casts both spellings to the
same value — which is why this class only shows up on the SQLite lane and why
it must be fixed here rather than waited for.

Matching both spellings is the only rule that is correct under both writers,
which is why this module does not try to normalise the storage form instead:
SQLAlchemy 2.0.36 exposes no dialect-level hook for a type's bind processor
(``dialect_impl`` does not exist in that tree — verified by grep), so
canonicalising storage would mean patching ``UUID.bind_processor`` from a test
conftest and would change what every node in the lane writes.
"""
from uuid import UUID

DASHED_SUFFIX = "d"
HEX_SUFFIX = "h"


def forms(value):
    """Return ``(dashed, hex)`` for any UUID-ish value.

    Raises on a value that is not a UUID at all: a caller that reaches this
    helper with an opaque marker string has used the wrong predicate, and
    finding out here is cheaper than finding out from an empty result set.
    """
    uuid = value if isinstance(value, UUID) else UUID(str(value))
    return str(uuid), uuid.hex


def match(column, value, *, name="uid", negate=False):
    """``(sql fragment, bind params)`` that matches one uuid identity either way.

    ``negate=True`` produces the exclusion form, which the ownership checks
    need: "not this user" must exclude both spellings of that user, or a row
    written by the other layer is reported as belonging to someone else.

    The clause and its params are returned together on purpose — the failure
    mode this module is meant to prevent is a call site that gets one half
    right.
    """
    dashed, hex_ = forms(value)
    dashed_param, hex_param = f"{name}_{DASHED_SUFFIX}", f"{name}_{HEX_SUFFIX}"
    operator = "NOT IN" if negate else "IN"
    clause = f"{column} {operator} (:{dashed_param}, :{hex_param})"
    return clause, {dashed_param: dashed, hex_param: hex_}
