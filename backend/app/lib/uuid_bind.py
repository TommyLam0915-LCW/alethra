"""Binding UUIDs in raw SQL the way the column's own type does.

Measured on this project's SQLite lane (SQLAlchemy 2.0.36, sqlite lib 3.53.1),
against ``postgresql.UUID(as_uuid=True)`` as recompiled by
``app/database.py:15-17`` to ``VARCHAR(36)``:

* an ORM write (``db.add``, every mapped insert including ``users.id``) renders
  **32-character hex** -- the type's bind processor is ``value.hex``, and it
  raises ``AttributeError: 'str' object has no attribute 'hex'`` for a ``str``,
  so a mapped column cannot store the dashed form at all;
* a raw ``text()`` INSERT with an untyped ``str(uuid4())`` bind stores the
  **dashed 36-character** text, because the value reaches sqlite3 as-is;
* a raw ``text()`` bind holding an untyped ``uuid.UUID`` object is refused
  outright (``sqlite3.ProgrammingError: Error binding parameter 1: type 'UUID'
  is not supported``);
* a raw ``text()`` bind declared with the column's own type and given a
  ``uuid.UUID`` stores exactly what the ORM stores, and a read shaped the same
  way matches those rows and only those rows;
* ``WHERE id = :id`` with an untyped string matches only the spelling stored.

So the two spellings are not a cosmetic difference: they decide whether a row is
visible to the ORM's identity key, to an owner-scoped join, and to an ORM update
of a row somebody else loaded. On PostgreSQL the column is a native ``uuid`` and
every bind above parses to the same value, which is why this never showed up
there -- and why no assumption is made here that existing PostgreSQL rows carry a
spelling problem; they cannot.

``uuid_binds`` + :func:`to_uuid` are that one contract for the raw SQL that keys
on these columns: the value is parsed into a ``uuid.UUID`` and bound with the
column's type, so each dialect renders what its own type would render. It is
deliberately not a read-side fallback -- it matches one spelling, the one the
write produces.
"""

import uuid

from sqlalchemy import bindparam
from sqlalchemy.dialects.postgresql import UUID

UUID_TYPE = UUID(as_uuid=True)


def to_uuid(value):
    """The ``uuid.UUID`` for a UUID-shaped value, or ``None`` if it is not one.

    Accepts the ``uuid.UUID`` itself and both text spellings, so a caller that
    got the value from a path parameter and one that read it back from the
    database bind the same thing. ``None`` binds as ``NULL``, which matches no
    row -- the same answer a malformed identifier gets on the routes this is for.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def uuid_binds(*names):
    """``bindparam`` declarations typing ``names`` as the column's own UUID type."""
    return [bindparam(name, type_=UUID_TYPE) for name in names]


def as_id_text(value) -> str:
    """The one caller-facing spelling of a UUID read back by raw SQL.

    A ``SELECT`` that names no column types hands the driver what the column
    holds: on PostgreSQL a ``uuid.UUID``, whose ``str()`` is the dashed 36
    characters, and on this lane the 32 hex characters the mapped type rendered
    on the way in. A response field cannot be allowed to differ between the two
    -- the value comes back to the route as a path parameter, and a client that
    was handed one spelling and accepted by the other has no error message to
    explain it. Parsing first makes ``str(row.id)`` mean the same thing on either
    backend, which is also what the mapped columns and the create responses
    already say.
    """
    return str(uuid.UUID(str(value)))
