"""Print the daily-estimate readiness report for one owner. Reads; never writes.

This is the hand-off tool for the isolated daily-estimate run: after the owner has
granted the daily consent, the operator runs this against the target database and
gets a four-item answer (effective scope, selection, instruments, quota) before a
scheduler is started and before any estimate can be billed. It is not a diagnostic
for a run that already happened — the lane's own ``learning_jobs`` summary answers
that.

Read-only in the strict sense, not the "probably" sense: the report is built by
:func:`app.services.daily_estimate_readiness.assess_readiness`, whose every
statement is a ``SELECT``, and the session is closed with ``rollback()`` so an
unflushed change cannot reach the database even if one were added later. Compare
``app/api/personalization.py``'s own status route, which the lane commits beside.

Usage::

    DATABASE_URL=… .venv/bin/python scripts/daily_estimate_readiness.py --owner-id <uuid>

Exits ``0`` on READY, ``3`` on NOT_READY (a valid answer, not a crash), ``2`` on a
usage or target error. The database URL is taken from the process environment and
the resolved dialect/host/database is printed with the report — never a password,
and never the full URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def _target_description(raw_url: str) -> dict:
    """What the operator needs to see before trusting a report: where it read."""
    url = make_url(raw_url)
    return {
        "dialect": url.get_backend_name(),
        "database": url.database,
        "host": url.host,
        "port": url.port,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--owner-id", required=True, help="user uuid to report on")
    parser.add_argument(
        "--indent", type=int, default=2, help="json indent; 0 prints one line"
    )
    args = parser.parse_args(argv)

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        sys.stderr.write(
            "Refusing: DATABASE_URL is unset. This script reads the environment "
            "only, so a local .env cannot silently retarget the report.\n"
        )
        return 2
    try:
        owner_id = UUID(str(args.owner_id))
    except ValueError:
        sys.stderr.write(f"Refusing: --owner-id is not a uuid: {args.owner_id!r}\n")
        return 2

    from app.models.user import User
    from app.services.daily_estimate_readiness import assess_readiness

    engine = create_engine(raw_url)
    session = Session(engine)
    try:
        if session.query(User).filter(User.id == owner_id).one_or_none() is None:
            sys.stderr.write(
                f"Refusing: no user {owner_id} in this database. A report about an "
                "owner who is not here would read as NOT_READY for the wrong reason.\n"
            )
            return 2
        report = assess_readiness(session, owner_id=owner_id)
        report["target"] = _target_description(raw_url)
    finally:
        # Nothing is pending on a SELECT-only session; this is what makes that
        # statement true rather than a hope.
        session.rollback()
        session.close()
        engine.dispose()

    print(json.dumps(report, indent=args.indent or None, sort_keys=True))
    return 0 if report["billing_observation_allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
