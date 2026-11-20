"""Gate for the id-membership rule that tests/sql_membership.py encodes.

Three of the six nodes here are the *before* cases, kept executable so the
class cannot come back quietly: the PostgreSQL-only operator, the parenthesised
placeholder form, and the unguarded empty list. All three are run against the
lane's real database, so what fails or succeeds is what the driver reported,
not what a docstring claims.
"""
import pathlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import bindparam, event, text

from tests.conftest import TEST_ENGINE, TestSession, _IS_SQLITE
from tests.sql_membership import list_sql

# Files whose `= ANY(:ids)` sites were measured to sit behind a PostgreSQL
# gate: `test_cleanup_utils.py` is a helper module with no nodes of its own,
# `test_paper_trading_cancel_atomic.py` runs 23 passed / 30 skipped on this
# lane with 0 failures so none of its ANY sites execute here, and
# `live_stack_env.py` is a psycopg2 probe helper that refuses any DSN that is
# not `postgresql+psycopg2://` before it opens a connection. Each set of
# grounds is re-measured by a node below rather than trusted from this comment.
PG_GATED_FILES = {
    "test_cleanup_utils.py",
    "test_paper_trading_cancel_atomic.py",
    "live_stack_env.py",
}

requires_sqlite_driver = pytest.mark.skipif(
    not _IS_SQLITE,
    reason=(
        "These nodes read the SQL string the driver actually received, which is "
        "this lane's paramstyle; on PostgreSQL the rendering differs and the "
        "string being asserted is not the one that lane sends."
    ),
)


def _seed_users(db, count):
    """Insert `count` users raw and return the ids exactly as the column stores them."""
    ids = []
    now = datetime.now(timezone.utc)
    for _ in range(count):
        user_id = str(uuid4())
        db.execute(
            text(
                "INSERT INTO users (id, email, password_hash, display_name, "
                "created_at, updated_at, is_active, is_admin) "
                "VALUES (:id, :email, 'nope', :display_name, :now, :now, 1, 0)"
            ),
            {
                "id": user_id,
                "email": f"{user_id}@example.test",
                "display_name": user_id,
                "now": now,
            },
        )
        ids.append(user_id)
    db.commit()
    return ids


def _members(db, values):
    """Count users rows whose id is one of `values`, through the helper."""
    return db.execute(
        list_sql(
            "SELECT id FROM users WHERE id IN :uids", name="uids", values=values
        )
    ).fetchall()


class TestMembershipOnRealDatabase:
    def test_membership_matches_one_and_many_ids(self):
        """One id and several ids both return exactly the rows they name.

        Compared as sets: the statement has no ORDER BY, and inventing an
        ordering assumption here would be a claim about SQL this lane never
        made.
        """
        db = TestSession()
        try:
            ids = _seed_users(db, 3)
            assert {r[0] for r in _members(db, ids[:1])} == set(ids[:1])
            assert {r[0] for r in _members(db, ids[:2])} == set(ids[:2])
            assert {r[0] for r in _members(db, ids)} == set(ids)
            assert _members(db, [str(uuid4())]) == []
        finally:
            db.close()

    def test_empty_list_matches_nothing_without_a_guard(self):
        """An empty collection returns nothing instead of erroring or matching all.

        This is the invariant that lets a residual check stay unconditional: the
        caller no longer has to choose between `if ids:` (blind when ids is empty)
        and `IN ()` (a list of zero placeholders).
        """
        db = TestSession()
        try:
            ids = _seed_users(db, 2)
            assert _members(db, []) == []
            assert _members(db, ids) != []
        finally:
            db.close()

    @requires_sqlite_driver
    def test_statement_binds_one_placeholder_per_value(self):
        """The expansion happens in core, before the dialect sees the statement.

        The listener records what the driver was handed. One placeholder per
        value, and an empty list becomes the engine's own always-false subquery
        rather than an empty parenthesis — so the same call works on either lane.
        """
        seen = []

        @event.listens_for(TEST_ENGINE, "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):
            seen.append((statement, parameters))

        db = TestSession()
        try:
            ids = _seed_users(db, 2)
            _members(db, ids[:1])
            assert seen[-1][0].rstrip().endswith("IN (?)"), seen[-1][0]
            assert seen[-1][1] == (ids[0],), seen[-1][1]

            _members(db, ids)
            assert seen[-1][0].rstrip().endswith("IN (?, ?)"), seen[-1][0]
            assert seen[-1][1] == (ids[0], ids[1]), seen[-1][1]

            _members(db, [])
            assert "IN (SELECT 1 FROM (SELECT 1) WHERE 1!=1)" in seen[-1][0], seen[-1][0]
            assert seen[-1][1] == (), seen[-1][1]
        finally:
            event.remove(TEST_ENGINE, "before_cursor_execute", record)
            db.close()


class TestFormsThatDoNotWork:
    """The before cases, executable. Each one is a spelling a call site could reach."""

    @requires_sqlite_driver
    def test_pg_only_operator_fails_on_this_lane(self):
        """`= ANY(:ids)` is why the chain node this unit fixed was red.

        Recorded verbatim because it is the failure a future reader will meet
        first: the statement dies before any row is read, so no assertion in
        the test ever runs.
        """
        db = TestSession()
        try:
            ids = _seed_users(db, 1)
            with pytest.raises(Exception) as excinfo:
                db.execute(
                    text("SELECT id FROM users WHERE id = ANY(:uids)"), {"uids": ids}
                )
            assert "no such function: ANY" in str(excinfo.value), str(excinfo.value)
        finally:
            db.close()

    @requires_sqlite_driver
    def test_parenthesised_placeholder_renders_a_row_constructor(self):
        """`IN (:ids)` is not the same statement as `IN :ids`.

        Needs two values to show: with one, the extra parentheses are just
        grouping and the statement runs, so a single-id call could hide the
        defect until the list grew.
        """
        db = TestSession()
        try:
            ids = _seed_users(db, 2)
            statement = text(
                "SELECT id FROM users WHERE id IN (:uids)"
            ).bindparams(bindparam("uids", expanding=True))
            with pytest.raises(Exception) as excinfo:
                db.execute(statement, {"uids": ids})
            assert "IN ((?, ?))" in str(excinfo.value), str(excinfo.value)
            assert "row value misused" in str(excinfo.value), str(excinfo.value)
        finally:
            db.close()

    @requires_sqlite_driver
    def test_empty_parenthesis_is_accepted_here_but_is_not_a_portable_property(self):
        """`IN ()` returns nothing on SQLite; nothing here claims PostgreSQL does.

        Kept as a node because the tolerance is the reason the hand-built
        spelling survived this long on the lane that runs, and the reason its
        16 `if ids:` guards must not be read as evidence of safety.
        """
        db = TestSession()
        try:
            ids = _seed_users(db, 2)
            rows = db.execute(text("SELECT id FROM users WHERE id IN ()")).fetchall()
            assert rows == []
            assert _members(db, ids) != []
        finally:
            db.close()


class TestPgOnlyOperatorIsConfined:
    """`= ANY(:ids)` may only appear in files that never run it on this lane."""

    def test_any_operator_appears_only_in_pg_gated_files(self):
        """A new ANY site in a SQLite-running file fails here, not in the chain."""
        tests_dir = pathlib.Path(__file__).parent
        offenders = set()
        helpers = {"sql_membership.py", "test_sql_membership.py"}
        for path in sorted(tests_dir.rglob("*.py")):
            if path.name in helpers or "__pycache__" in path.parts:
                continue
            if "= ANY(" in path.read_text(encoding="utf-8", errors="replace"):
                offenders.add(path.name)
        assert offenders == PG_GATED_FILES, (
            f"`= ANY(:ids)` appeared outside the PG-gated set: "
            f"{sorted(offenders - PG_GATED_FILES)}; use tests.sql_membership.list_sql "
            f"or add the file only with measured evidence that none of its nodes "
            f"execute on the SQLite lane. Missing from the set: "
            f"{sorted(PG_GATED_FILES - offenders)}"
        )

    def test_helper_module_still_has_no_nodes_of_its_own(self):
        """The first half of the allowlist's grounds, re-measured each run.

        `test_cleanup_utils.py` is allowlisted as a helper module, not as a test
        file — if that ever stops being true, its ANY sites run somewhere and
        the allowlist has to be re-argued.
        """
        source = (pathlib.Path(__file__).parent / "test_cleanup_utils.py").read_text(
            encoding="utf-8"
        )
        assert "\ndef test_" not in source, (
            "test_cleanup_utils.py now contains test functions; re-measure whether "
            "its `= ANY(` sites execute on the SQLite lane before allowing them"
        )

    def test_probe_helper_still_refuses_a_sqlite_dsn(self):
        """The second half: `live_stack_env.py` will not open this lane's database.

        Its `= ANY(%s::uuid[])` statements are psycopg2-level and target the
        simulation tables, so the allowlist stands only while the helper keeps
        refusing a non-PostgreSQL DSN before it connects. `parse_target` is a
        pure function (its own docstring: no network, no filesystem), so this
        node reads the refusal without enabling or running any probe.
        """
        from tests import live_stack_env

        with pytest.raises(live_stack_env.ProbeConfigRefusal) as opt_in:
            live_stack_env.parse_target({})
        assert f"{live_stack_env.OPT_IN_VAR} is not set to 1" in str(opt_in.value), (
            f"live_stack_env no longer requires explicit opt-in: {opt_in.value}"
        )

        with pytest.raises(live_stack_env.ProbeConfigRefusal) as excinfo:
            live_stack_env.parse_target(
                {
                    live_stack_env.OPT_IN_VAR: "1",
                    live_stack_env.ORIGIN_VAR: "http://127.0.0.1:8000",
                    live_stack_env.DB_URL_VAR: "sqlite:///./not-the-probe-db.db",
                }
            )
        assert "must be a postgresql+psycopg2:// URL" in str(
            excinfo.value
        ), f"live_stack_env no longer refuses a SQLite DSN: {excinfo.value}"
