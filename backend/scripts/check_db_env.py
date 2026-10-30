"""One answer to "which database is this test run pointed at?".

The test tree reads four separate DB variables:

    DATABASE_URL                app config, tests/conftest.py
    ALETHRA_TEST_DB             tests/conftest.py, stored-market/intraday fixtures
    ALETHRA_PG_DATABASE_URL     PostgreSQL-only modules (paper orders, intraday,
                                harness connection-leak regression)
    ALETHRA_TEST_POSTGRES_URL   tests/test_quota_concurrency.py (module level)

Setting some but not others silently turns whole PostgreSQL modules into
module-level skips. A skip does not fail a run, so the count reads as green
while most of the PostgreSQL surface never executed. This module is the single
place that decides whether the environment is coherent, so the runner, the
contract test and any future entry point cannot disagree.

Nothing here echoes a value: messages carry variable names, hosts, ports and
database names only — never a password or a full URL.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

DB_VARS = (
    "DATABASE_URL",
    "ALETHRA_TEST_DB",
    "ALETHRA_PG_DATABASE_URL",
    "ALETHRA_TEST_POSTGRES_URL",
)
PG_ONLY = ("ALETHRA_PG_DATABASE_URL", "ALETHRA_TEST_POSTGRES_URL")


@dataclass(frozen=True)
class Analysis:
    mode: str  # "sqlite" | "postgresql" | "unknown"
    errors: tuple
    summary: str
    database: Optional[str] = None


def _signature(url):
    return (url.host, url.port, url.database)


def analyse(
    env: Mapping[str, str],
    required_prefix: Optional[str] = None,
) -> Analysis:
    """Classify a DB environment as coherent sqlite, coherent postgresql, or broken."""
    parsed = {}
    errors = []
    for name in DB_VARS:
        raw = env.get(name)
        if not raw:
            continue
        try:
            parsed[name] = make_url(raw)
        except Exception:
            errors.append(f"{name} is set but is not a parseable database URL")
    if errors:
        return Analysis("unknown", tuple(errors), "unparseable environment")

    postgres = {n: u for n, u in parsed.items() if u.get_backend_name() == "postgresql"}

    if not postgres:
        stray = [n for n in PG_ONLY if n in parsed]
        if stray:
            errors.append(
                "PostgreSQL-only variables are set without a PostgreSQL target: "
                + ", ".join(stray)
            )
        name = None
        for var in ("ALETHRA_TEST_DB", "DATABASE_URL"):
            if var in parsed:
                name = parsed[var].database
                break
        return Analysis("sqlite", tuple(errors), f"sqlite: {name or '?'}", name)

    missing = [n for n in DB_VARS if n not in parsed]
    if missing:
        errors.append(
            "PostgreSQL run without all four DB variables set: " + ", ".join(missing)
        )
    mixed = [
        f"{n}={parsed[n].get_backend_name()}"
        for n in DB_VARS
        if n in parsed and parsed[n].get_backend_name() != "postgresql"
    ]
    if mixed:
        errors.append("mixed backends: " + ", ".join(mixed))
    if len(postgres) < len(DB_VARS) and any(n in postgres for n in PG_ONLY):
        # Name the variable that flipped this run into PostgreSQL: without it,
        # the modules that key off that one variable would run while the rest
        # of the tree stayed elsewhere.
        errors.append(
            "PostgreSQL-only variable(s) "
            + ", ".join(n for n in PG_ONLY if n in postgres)
            + " are set without all four variables pointing at one PostgreSQL database"
        )
    if errors:
        return Analysis("postgresql", tuple(errors), "incomplete PostgreSQL environment")

    signatures = {n: _signature(u) for n, u in parsed.items()}
    targets = set(signatures.values())
    if len(targets) != 1:
        errors.append(
            "DB variables disagree: "
            + ", ".join(f"{n}->{h}:{p}/{d}" for n, (h, p, d) in signatures.items())
        )
        return Analysis("postgresql", tuple(errors), "disagreeing targets")

    host, port, database = targets.pop()
    if required_prefix and not (database or "").startswith(required_prefix):
        errors.append(
            f"database {database!r} does not start with the required disposable "
            f"prefix {required_prefix!r}"
        )
    return Analysis(
        "postgresql",
        tuple(errors),
        f"postgresql: {host}:{port}/{database}",
        database,
    )


def connection_problem(raw_url: str, expected_database: str) -> Optional[str]:
    """Return a value-free description of why the database is unusable, or None."""
    engine = None
    try:
        engine = create_engine(raw_url, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            actual = conn.exec_driver_sql("SELECT current_database()").scalar()
    except Exception as exc:
        # Only the exception type: a driver message can carry the URL, and the
        # URL carries the password.
        return f"cannot connect to {expected_database!r} ({type(exc).__name__})"
    finally:
        if engine is not None:
            engine.dispose()
    if actual != expected_database:
        return f"connected to {actual!r}, expected {expected_database!r}"
    return None