"""Run the paper-order PostgreSQL integration tests on a disposable database.

Why this exists: the money-path defects these tests cover (numpy scalars
reaching Float columns, lost updates under concurrent cash mutation) are
invisible on SQLite, and the review database must never be touched by a test
run that drops tables.

Run it from inside a container that already has the app dependencies and a
``DATABASE_URL`` pointing at PostgreSQL:

    docker exec alethra-e2e-backend-1 python scripts/verify_paper_orders_on_postgres.py

Any further arguments are passed straight to pytest, so one case can be re-run and
its printed observation captured:

    docker exec alethra-e2e-backend-1 python scripts/verify_paper_orders_on_postgres.py \
        -k TestTwoDueOrdersShareOneCashBalance -s

Exit code 0 only when both the migration chain and every test pass.
Credential values are never printed: database URLs are reported masked.
"""

import os
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

FRESH_DB = "alethra_pgfresh_26"
TEST_DB = "alethra_pgtest_26"


def _scrub(text: str) -> str:
    """Never echo the real password, whatever a subprocess prints."""
    password = _maintenance_url().password
    return text.replace(password, "***") if password else text


def _masked(url) -> str:
    return url.render_as_string(hide_password=True)


def _maintenance_url():
    src = make_url(os.environ["DATABASE_URL"])
    if src.get_backend_name() != "postgresql":
        raise SystemExit(f"DATABASE_URL must be PostgreSQL, got {_masked(src)}")
    return src.set(database="postgres")


def _create(dbname: str) -> None:
    eng = create_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        conn.exec_driver_sql(f'CREATE DATABASE "{dbname}"')
    eng.dispose()


def _drop(dbname: str) -> None:
    eng = create_engine(_maintenance_url(), isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
    eng.dispose()


def _url(dbname: str) -> str:
    """Full URL for a subprocess env var.

    ``str(url)`` is deliberately not used: SQLAlchemy masks the password there,
    and the resulting "***" authenticates as a wrong credential.
    """
    src = _maintenance_url()
    rendered = src.set(database=dbname).render_as_string(hide_password=False)
    assert src.password in rendered and "***" not in rendered, "database password was redacted"
    return rendered


def phase_migration_chain() -> int:
    """Upgrade an empty database through the full revision chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    url = _url(FRESH_DB)
    print(f"[1/3] alembic upgrade head on fresh {_url_target(FRESH_DB)}")
    env = dict(os.environ, DATABASE_URL=url)
    result = subprocess.run(
        ["alembic", "upgrade", "head"], env=env, capture_output=True, text=True
    )
    applied = sum(
        1 for line in (result.stdout + result.stderr).splitlines() if "Running upgrade" in line
    )
    if result.returncode != 0:
        tail = "\n".join(_scrub(result.stdout + result.stderr).splitlines()[-25:])
        print(f"  FAIL exit={result.returncode}\n{tail}")
        return 1

    # Compare against the revision graph itself, not a literal: a hardcoded head
    # goes stale the moment a revision is appended and would then report the
    # chain as broken when it is fine (or hide a genuinely short upgrade).
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    expected_heads = set(script.get_heads())

    eng = create_engine(url)
    with eng.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        paper_cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='paper_orders'"
                )
            )
        }
        decision_cols = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='decision_records'"
                )
            )
        }
        index = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_paper_order_portfolio_idempotency'")
        ).scalar()
    eng.dispose()

    print(f"  applied={applied} revisions, head={version}, graph_heads={sorted(expected_heads)}")
    print(f"  paper_orders new columns={sorted(paper_cols & {'idempotency_key', 'actor_type'})}")
    print(f"  decision_records.paper_portfolio_id={'paper_portfolio_id' in decision_cols}")
    print(f"  index={index}")

    failures = []
    if {version} != expected_heads:
        failures.append(f"database head {version} != revision graph heads {sorted(expected_heads)}")
    if not {"idempotency_key", "actor_type"} <= paper_cols:
        failures.append("paper_orders is missing idempotency_key/actor_type")
    if "paper_portfolio_id" not in decision_cols:
        failures.append("decision_records is missing paper_portfolio_id (journal cannot be scoped)")
    if not index:
        failures.append("partial unique index uq_paper_order_portfolio_idempotency is absent")
    if failures:
        print("  FAIL " + "; ".join(failures))
        return 1
    print("  PASS")
    return 0


def _url_target(dbname: str) -> str:
    host = _maintenance_url().host
    return f"{host}/{dbname}"


def phase_tests(extra_args=None) -> int:
    url = _url(TEST_DB)
    print(f"[2/3] pytest on disposable {_url_target(TEST_DB)}")
    env = dict(os.environ, ALETHRA_TEST_DB=url, ALETHRA_PG_DATABASE_URL=url)
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q",
            "tests/test_paper_order_postgres_integration.py",
            "-p", "no:cacheprovider",
            # Caller-supplied pytest args (e.g. ``-k TestTwoDue… -s``) travel last,
            # so one case can be re-run and its printed observation captured without
            # changing what the default invocation runs.
            *(extra_args or []),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    # stdout and stderr are reported separately on purpose: stdout carries pytest's
    # report (including a passing test's printed observation), while stderr fills up
    # with import-time warnings that would push that report out of one tail window if
    # the two streams were concatenated before slicing.
    out = _scrub(result.stdout)
    print("\n".join(out.splitlines()[-40:]))
    err_lines = [line for line in _scrub(result.stderr).splitlines() if line.strip()]
    if err_lines:
        print("  stderr(tail): " + " | ".join(err_lines[-3:]))
    return 0 if result.returncode == 0 else 1


def main() -> int:
    for candidate in (FRESH_DB, TEST_DB):
        try:
            _create(candidate)
        except Exception as exc:  # noqa: BLE001 - report, then fail the run
            print(f"cannot create disposable database {candidate}: "
                  f"{_scrub(f'{type(exc).__name__}: {exc}')}")
            return 2

    rc_migration = phase_migration_chain()
    rc_tests = phase_tests(sys.argv[1:])

    for candidate in (FRESH_DB, TEST_DB):
        try:
            _drop(candidate)
        except Exception as exc:  # noqa: BLE001
            print(f"warning: could not drop {candidate}: {type(exc).__name__}")

    print(f"[3/3] results: migration_chain={'PASS' if rc_migration == 0 else 'FAIL'}, "
          f"tests={'PASS' if rc_tests == 0 else 'FAIL'}")
    return 0 if rc_migration == 0 and rc_tests == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
