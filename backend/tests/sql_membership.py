"""One rule for "this column's value is one of these ids", on either lane.

Why this exists is measured, not stylistic. ``= ANY(:ids)`` is the spelling
several test files use, and it is PostgreSQL-only: on the SQLite lane the
statement dies before any row is read —

    sqlite3.OperationalError: no such function: ANY

measured on ``tests/test_api_login_with_fixture.py`` (``_capture_all_ids``,
first site ``:127``), which runs on this lane and therefore was red.

Two portable alternatives exist and both were measured against a real SQLite
database before choosing:

    construct                          1 id      2 ids     empty list
    ``IN :ids`` + expanding bindparam  matches   matches   matches nothing
    hand-built ``IN (:a0, :a1, …)``    matches   matches   needs a guard

The second is what ``tests/api_test_fixtures.py`` does today, and it is why all
16 of its sites sit behind ``if ids:``. An unguarded empty hand-built list
renders ``IN ()`` — this lane accepts that and returns nothing (measured, a
SQLite tolerance, not a portable property; on PostgreSQL the same string is a
syntax error, which is not verifiable here because this host has no server),
while the expanding form renders ``IN (SELECT 1 FROM (SELECT 1) WHERE 1!=1)``,
measured above, which is the engine's own always-false shape and is emitted
identically whatever the list length. So the expanding form needs no guard —
and the guard is the expensive part, because an ``if``-wrapped residual check
is unfalsifiable exactly when its collection comes back empty. That is the
same blind-pass shape U5 removed from the uuid predicates.

``IN (:name)`` with the caller's own parentheses is not a stylistic choice
either: it executes as ``IN ((?, ?))`` and dies with ``row value misused``,
measured — hence one spelling, asserted in the gate.

The statement and its binds are produced together and returned as one
executable object, for the reason ``uuid_identity`` already gives: the failure
mode this module exists to prevent is a call site that gets one half right.
"""
from sqlalchemy import bindparam, text


def list_sql(statement, *, name, values):
    """Return ``statement`` ready to execute, with ``:name`` expanded.

    ``statement`` must contain ``IN :name`` — no parentheses. SQLAlchemy's
    postcompile step supplies them; writing ``IN (:name)`` instead renders
    ``IN ((…))``, a row constructor, measured on both dialects.
    """
    return text(statement).bindparams(
        bindparam(name, expanding=True, value=list(values))
    )
