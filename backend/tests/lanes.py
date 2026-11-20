"""Lane gates: report a node whose environment is absent as a named skip.

A PostgreSQL-only test that finds no PG target currently dies in fixture setup,
so it lands in the error column and reads like product breakage. A skip with a
stable reason string says which environment is missing instead, and stays out of
the pass count.

This does not soften the safety rule: a PG-lane node never falls back to SQLite.
It skips. The URL is re-read here rather than trusted from a fixture argument.
"""
import os

import pytest

PG_REASON = "BLOCKED_ENV_NOT_AVAILABLE: no PostgreSQL test target configured"


def _pg_url():
    url = os.environ.get("ALETHRA_PG_TEST_URL") or ""
    return url if url.startswith(("postgresql://", "postgresql+")) else None


def has_pg_target():
    """True when a PostgreSQL test URL is configured (used by module skipif)."""
    return _pg_url() is not None


def pg_test_url_or_skip():
    """Return the configured PG test URL, or skip this node with a named reason."""
    url = _pg_url()
    if url is None:
        pytest.skip(PG_REASON)
    return url


pg_only = pytest.mark.skipif(
    not has_pg_target(),
    reason=PG_REASON,
)
