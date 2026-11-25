"""The one place that decides whether a node has a database to run against.

Two lanes exist and neither borrows the other's verdict: ``run_tests.sh
sqlite`` runs the default suite on a per-run SQLite file, ``run_tests.sh pg``
runs on a disposable PostgreSQL database named by ``ALETHRA_PG_TEST_URL``.
Nodes that need the second one used to discover that in three different ways,
all of them wrong in a different direction:

* ``get_test_db_url()`` raised, so the node reported ERROR -- an infrastructure
  refusal wearing the shape of a product failure, and the suite exit code
  could not be read as a product result.
* ``if not os.environ.get("ALETHRA_PG_TEST_URL"): pytest.skip(...)`` -- which
  only asks whether the name has a value. ``tests/conftest.py`` used to copy
  ``ALETHRA_TEST_DB`` into that name unconditionally, so on the SQLite lane
  the answer was "yes, PostgreSQL is configured" and the node then ran
  PostgreSQL-only SQL against SQLite.
* ``pytest.skip(..., allow_module_level=True)`` behind a bare ``except
  Exception`` at import time, which removed offline-verifiable nodes along
  with the PostgreSQL ones and swallowed real import errors as skips.

The single predicate below asks the question the earlier versions skipped: is
the value a PostgreSQL URL? Anything else leaves ``ALETHRA_PG_TEST_URL``
unset as far as this module is concerned, which is what makes the skips below
honest and what keeps ``get_test_db_url()`` failing closed.

A skip recorded here is a statement that a node did not run. It is not a pass,
and the reason says so and names the command that would produce the result.
"""

import os
import sys

import pytest

PG_URL_ENV = "ALETHRA_PG_TEST_URL"

#: Every skip this module produces starts with this token, so a later count can
#: separate "skipped because it needs PostgreSQL" from "skipped for a reason a
#: reader has to be told about".
MARKER = "PG-ONLY"

#: Written out rather than derived, because a skip that cannot be acted on is a
#: way of forgetting a test. The angle brackets are literal: the URL names a
#: disposable database and is never committed. Node ids come from
#: ``--collect-only -q`` on the same selection, or from a ``-v`` run.
PG_LANE_COMMAND = (
    "ALETHRA_PG_TEST_URL='<postgresql://…/alethra_test…>' "
    "bash ./backend/scripts/run_tests.sh pg <node ids>"
)


def _call_site():
    """Where this module was asked to skip, as ``path:line``.

    ``-rs`` prints the location that raised, which from here is always this
    file -- twelve skipped nodes would collapse into one indistinguishable
    line and the skip inventory would stop naming anything. Walking out to the
    first frame that is not this module recovers the node's own line number.
    """
    frame = sys._getframe(1)
    here = os.path.abspath(__file__)
    while frame is not None and os.path.abspath(frame.f_code.co_filename) == here:
        frame = frame.f_back
    if frame is None:
        return "<unknown call site>"
    path = frame.f_code.co_filename
    separator = f"{os.sep}tests{os.sep}"
    index = path.rfind(separator)
    if index != -1:
        path = path[index + 1:]
    return f"{path}:{frame.f_lineno}"


def is_postgresql_url(url):
    """True only for a URL whose dialect is PostgreSQL.

    Deliberately narrower than "the variable is non-empty". Unparseable values
    count as absent: a URL this module cannot classify is not a database this
    module may hand to a test.
    """
    if not url:
        return False
    from sqlalchemy.engine import make_url

    try:
        drivername = make_url(url).drivername
    except Exception:
        return False
    return drivername == "postgresql" or (drivername or "").startswith("postgresql+")


def postgresql_available():
    """Whether this process was pointed at a PostgreSQL test database."""
    return is_postgresql_url(os.environ.get(PG_URL_ENV))


def skip_reason(prerequisite, at=None):
    """The one skip sentence, so every PostgreSQL-only node says the same thing.

    ``at`` names the node's own file and line; a reason twelve nodes share is
    only auditable if each copy says which node it belongs to.
    """
    return (
        f"{MARKER} [{at or _call_site()}]: {prerequisite}. {PG_URL_ENV} is unset "
        f"or is not a PostgreSQL URL, so this node has no database to run "
        f"against -- NOT RUN, not passed. To execute it: {PG_LANE_COMMAND}"
    )


def requires_postgresql(prerequisite):
    """Skip the calling node unless a PostgreSQL test database is configured.

    ``prerequisite`` names what specifically the node cannot do without a live
    server -- ``SELECT current_database()``, a real row lock, ``GRANT`` -- so
    the skip inventory can be read as a list of unmet preconditions rather
    than as one repeated sentence.
    """
    if not postgresql_available():
        pytest.skip(skip_reason(prerequisite, _call_site()))


#: Nodes that have a PostgreSQL database but not one shaped the way the node
#: assumed. Kept apart from ``MARKER`` because the two mean different things:
#: one is "this lane cannot run PostgreSQL at all", the other is "the approved
#: test database does not look the way this test hard-codes".
SETUP_MARKER = "PG-SETUP"


def unmet_setup(what):
    """Skip a node whose precondition is the shape of the configured target.

    Distinct from ``requires_postgresql``: here PostgreSQL is available and the
    node still cannot run, because it builds a deliberately-wrong variant of
    the configured URL and that variant only exists if the URL has the piece it
    means to corrupt.
    """
    pytest.skip(
        f"{SETUP_MARKER} [{_call_site()}]: {what}. Nothing was asserted "
        f"against a live database -- NOT RUN, not passed."
    )


def postgresql_test_url(prerequisite):
    """Return the configured PostgreSQL test URL, or skip the node.

    The value is returned unvalidated on purpose. Deciding whether a URL is an
    *approved* target belongs to ``verify_test_target`` and the safety gate,
    which check the live server behind the name; substituting a skip here
    would turn an unapproved-but-reachable database into a green lane, which
    is the opposite of failing closed.
    """
    url = os.environ.get(PG_URL_ENV)
    if not is_postgresql_url(url):
        pytest.skip(skip_reason(prerequisite, _call_site()))
    return url
