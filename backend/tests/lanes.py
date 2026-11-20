"""Lane gates: report a node whose environment is absent as a named skip.

A PostgreSQL-only test that finds no PG target currently dies in fixture setup,
so it lands in the error column and reads like product breakage. A skip with a
stable reason string says which environment is missing instead, and stays out of
the pass count.

This does not soften the safety rule: a PG-lane node never falls back to SQLite.
It skips.

``mask_dsn`` lives here too because "how a DSN is reported" and "which lane a
node runs on" are the same question told to different audiences, and the
masking rule had already been hand-copied three times with three outcomes.
"""
import os

import pytest
from sqlalchemy.engine import make_url

PG_REASON = "BLOCKED_ENV_NOT_AVAILABLE: no PostgreSQL test target configured"
UNPARSEABLE = "<unparseable DSN>"


def mask_dsn(url: str) -> str:
    """Render a DSN so it can be printed or logged without carrying a password.

    The whole message is the password-free rendering SQLAlchemy itself produces
    for a URL, so the string an operator reads is the string the engine will
    connect with, minus the credential. The previous copies diverged in ways
    that mattered: one rebuilt a ``ParseResult`` with a field it did not have
    and therefore returned the fallback for *every* input, another treated
    "URL has no password" as "URL is unparseable", which is the most common
    input and the one where the name of the database is the whole diagnostic.

    The fallback is only for a string SQLAlchemy cannot parse at all. It is not
    a redaction — a caller that prints it has lost the database identity, which
    is why the gate nodes assert for ``***`` rather than accepting either.
    """
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return UNPARSEABLE


def lane_db_url():
    """The database this run actually executes against.

    conftest resolves the lane once and writes it to DATABASE_URL before any
    test module is imported, so that is the authority for which dialect a node
    will meet — not whichever variable happens to be set in the shell.
    """
    return os.environ.get("DATABASE_URL") or ""


def _is_postgresql(url: str) -> bool:
    try:
        return make_url(url).get_backend_name() == "postgresql"
    except Exception:
        return False


def _pg_url():
    url = os.environ.get("ALETHRA_PG_TEST_URL") or ""
    return url if _is_postgresql(url) else None


def has_pg_target():
    """True only when this run executes against PostgreSQL.

    Keyed on the resolved lane dialect rather than on the variable existing.
    conftest used to mirror any ALETHRA_TEST_DB — SQLite included — into
    ALETHRA_PG_TEST_URL, so gates that only asked "is the variable set" opened
    on the SQLite lane and PG-only nodes died inside PostgreSQL-only SQL
    (``no such function: current_database``). conftest now mirrors PostgreSQL
    values only, and this gate still reads the lane dialect, so a node cannot
    be unlocked by a stray environment variable.
    """
    return _is_postgresql(lane_db_url())


def pg_test_url_or_skip():
    """Return the configured PG test URL, or skip this node with a named reason."""
    if not has_pg_target():
        pytest.skip(PG_REASON)
    url = _pg_url()
    if url is None:
        pytest.skip(PG_REASON)
    return url


pg_only = pytest.mark.skipif(
    not has_pg_target(),
    reason=PG_REASON,
)
