"""Paper-trading ownership isolation, through the HTTP API and the verifier itself.

Four different layers are covered here, and they do not have the same
prerequisites:

* ``TestHTTPOwnershipIsolation`` drives the full chain (register → create portfolio
  → read/stop as another user) through the formal endpoints, on this lane. It did
  not used to: ``POST /paper-trading/create`` answered 500 ``creation_failed`` here
  because its money values reached raw SQL untyped and sqlite3 refuses a Decimal
  bind, so six of the nine nodes were held off as PostgreSQL-only on a prerequisite
  that was the product's own defect rather than the lane's limit. The binds now
  carry their column types, the endpoint completes, and the nodes run: a create
  that regressed fails one of them instead of skipping past the ownership question.
* ``TestVerifyRunOwnershipDirect`` calls the actual ``_verify_run_ownership()``
  against rows seeded by ``_seed_owned_run()``, so the ownership query and its
  refusals are checked on every lane.
* ``TestStatusReadContract`` compares the explicit column list of the status reads
  against the names the service body takes from them, which needs no database at
  all -- it is the check that keeps the named read honest as the two drift.

What has not been executed against PostgreSQL is stated per node, not per file.
The migration and the disposable database it would run on are not approved
operations, so whatever is genuinely server-only -- a row lock, a native uuid
column's behaviour -- remains a measurement this lane cannot make.
"""

import os
import pytest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.lib.uuid_bind import to_uuid, uuid_binds


def _safe_dsn(url_str: str) -> str:
    """Return a loggable DSN with password redacted."""
    try:
        u = make_url(url_str)
        if u.password:
            return str(u.set(password="***"))
        # No password to redact does not mean nothing to show: falling through to
        # the unparseable branch hid the host and database name in the one message
        # that exists to say which target was refused.
        return str(u.render_as_string(hide_password=True))
    except Exception:
        return "<unparseable DSN>"


# Safety: refuse to run against production database
_test_db_url = os.environ.get("ALETHRA_TEST_DB", "")
if _test_db_url.startswith("sqlite"):
    # The SQLite lane is the lane this suite runs on by default, and four of this
    # file's nodes are ownership checks that need nothing but a database they can
    # write a row into, so the module as a whole is not skipped. This is not a
    # looser gate: an unset ALETHRA_TEST_DB still raises below rather than
    # skipping, a driver that is neither SQLite nor PostgreSQL still raises, and
    # every PostgreSQL target still has to clear the host, name and production
    # checks before a single node runs. All that changed is that the nodes which
    # need a real server now skip individually instead of taking the offline
    # coverage with them.
    pass
elif _test_db_url:
    _parsed = make_url(_test_db_url)
    _db_name = _parsed.database or ""
    _host = _parsed.host or ""

    # Must be PostgreSQL
    if _parsed.drivername not in ("postgresql", "postgresql+psycopg2"):
        raise RuntimeError(
            f"SAFETY: Integration tests require PostgreSQL, got {_parsed.drivername!r}"
        )

    # Host must be localhost
    if _host and _host not in ("localhost", "127.0.0.1", "::1", "/tmp"):
        raise RuntimeError(
            f"SAFETY: Integration tests require localhost, got host={_host!r}. "
            f"DSN: {_safe_dsn(_test_db_url)}"
        )

    # Database name must contain 'test'
    if "test" not in _db_name.lower():
        raise RuntimeError(
            f"SAFETY: Test DB name must contain 'test', got {_db_name!r}. "
            f"DSN: {_safe_dsn(_test_db_url)}"
        )

    # Must not be production
    if _db_name in ("ai_investment_lab", "alethra_production", "alethra_prod"):
        raise RuntimeError(
            f"SAFETY: Refusing to use production database {_db_name!r}. "
            f"DSN: {_safe_dsn(_test_db_url)}"
        )
else:
    raise RuntimeError(
        "SAFETY: ALETHRA_TEST_DB environment variable is required for integration tests"
    )


def _make_user(client, email: str, is_admin: bool = False):
    """Register a user and return (auth_headers, user_id)."""
    from app.auth.jwt import get_password_hash, create_access_token
    from app.models.user import User
    from tests.conftest import TestSession

    db = TestSession()
    try:
        user = User(
            email=email,
            password_hash=get_password_hash("testpass123"),
            display_name=email.split("@")[0],
            is_admin=is_admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(data={"sub": str(user.id)})
        user_id = str(user.id)
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}, user_id


def _create_portfolio_via_api(client, headers, skill="momentum", instrument_id="BTC-USD"):
    """Create a portfolio via the formal API and return (portfolio_id, run_id)."""
    resp = client.post(
        "/api/v1/paper-trading/create",
        headers=headers,
        json={
            "skill": skill,
            "instrument_id": f"CRYPTO:KRAKEN:{instrument_id}",
            "interval_minutes": 60,
            "exit_config": "exit_1",
            "initial_capital": 100000.0,
        },
    )
    assert resp.status_code == 200, f"Portfolio creation failed: {resp.json()}"
    data = resp.json()
    return data["portfolio_id"], data["run_id"]


def _seed_owned_run(owner_id: str, *, skill: str = "momentum", instrument_id: str = "BTC-USD") -> str:
    """Insert a portfolio plus a queued run owned by ``owner_id``; return run_id.

    The two INSERTs copy the column set and the literal values of the product's
    creation path (app/services/paper_trading.py:create_portfolio) with the money
    values as floats rather than Decimals, since the SQLite driver refuses an
    untyped Decimal bind.

    Each identity key goes in typed with its column's own UUID type, which is
    what the product's create lane does now. It used to go in as the dashed 36
    characters raw SQL carried, and this file seeded the same way so that
    ``_verify_run_ownership()`` and the status/history reads -- which bound the
    caller's text untyped -- could resolve the row. Both sides of that pair moved
    together: the writers store the ORM's representation, and these readers bind
    the column type, so the row is still found. The positive controls below are
    what would notice if one side moved alone: a seeded row no reader can name
    turns every 404 assertion into a pass that tests nothing about ownership.

    This seeds a fixture for the verifier. It does not stand in for the product's
    create capability, and no node here claims that capability works.
    """
    from tests.conftest import TestSession

    portfolio_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    db = TestSession()
    try:
        db.execute(
            text("""
            INSERT INTO paper_portfolios (id, owner_id, currency, initial_virtual_cash, current_virtual_cash, status, created_at)
            VALUES (:id, :owner_id, 'USD', :initial, :current, 'active', CURRENT_TIMESTAMP)
        """).bindparams(*uuid_binds("id", "owner_id")),
            {
                "id": to_uuid(portfolio_id),
                "owner_id": to_uuid(owner_id),
                "initial": 100000.0,
                "current": 100000.0,
            },
        )
        db.execute(
            text("""
            INSERT INTO paper_trading_runs (
                id, portfolio_id, skill, instrument_id, interval_minutes, exit_config,
                start_date, status, initial_capital, total_pnl, total_trades
            )
            VALUES (
                :id, :portfolio_id, :skill, :instrument, :interval, :exit_config,
                CURRENT_TIMESTAMP, 'queued', :initial, 0, 0
            )
        """).bindparams(*uuid_binds("id", "portfolio_id")),
            {
                "id": to_uuid(run_id),
                "portfolio_id": to_uuid(portfolio_id),
                "skill": skill,
                "instrument": f"CRYPTO:KRAKEN:{instrument_id}",
                "interval": 60,
                "exit_config": "exit_1",
                "initial": 100000.0,
            },
        )
        db.commit()
    finally:
        db.close()
    return run_id


class TestHTTPOwnershipIsolation:
    """Test ownership isolation through the actual HTTP API."""

    def test_user_can_read_own_run_status(self, client):
        """User A can read status of their own run."""

        headers_a, user_id_a = _make_user(client, "owner_a@test.com")
        portfolio_id, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_a)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        assert resp.json()["run_id"] == run_id

    def test_user_cannot_read_other_users_run_status(self, client):
        """User B gets 404 when reading User A's run status."""

        headers_a, _ = _make_user(client, "owner_a2@test.com")
        headers_b, _ = _make_user(client, "owner_b2@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 for User A's run, got {resp.status_code}"
        )

    def test_user_cannot_read_other_users_daily_report(self, client):
        """User B gets 404 when reading User A's daily report."""

        headers_a, _ = _make_user(client, "owner_a3@test.com")
        headers_b, _ = _make_user(client, "owner_b3@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.get(f"/api/v1/paper-trading/daily-report/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 for User A's daily report, got {resp.status_code}"
        )

    def test_user_cannot_stop_other_users_run(self, client):
        """User B gets 404 when trying to stop User A's run."""

        headers_a, _ = _make_user(client, "owner_a4@test.com")
        headers_b, _ = _make_user(client, "owner_b4@test.com")
        _, run_id = _create_portfolio_via_api(client, headers_a)

        resp = client.post(f"/api/v1/paper-trading/stop/{run_id}", headers=headers_b)
        assert resp.status_code == 404, (
            f"User B must get 404 when stopping User A's run, got {resp.status_code}"
        )

    def test_nonexistent_run_returns_404_not_500(self, client):
        """Non-existent run returns 404, not 500.

        This is not a vacuous refusal: every error inside the lookup is mapped to
        500 status_failed at app/api/paper_trading.py, so a 404 here means the
        ownership SQL ran, bound the dashed id and matched no row.

        The opposite leg -- the same query resolving a row that does exist and
        answering 200 -- is the node below, and it now runs on this lane. It used
        to be impossible offline because the read behind the 200 was
        ``engine.get_status()``'s ``SELECT *``, which carries no column types, so
        SQLite handed ``start_date`` back as TEXT and ``.isoformat()`` raised
        AttributeError -> 500 before any assertion could run.
        """
        headers_a, _ = _make_user(client, "owner_a5@test.com")
        fake_run_id = str(uuid.uuid4())

        resp = client.get(f"/api/v1/paper-trading/status/{fake_run_id}", headers=headers_a)
        assert resp.status_code == 404, (
            f"Non-existent run must return 404, got {resp.status_code}: {resp.json()}"
        )

    def test_owner_reads_a_seeded_run_status_offline(self, client):
        """The 200 leg of the same lookup, on a row this lane can actually see.

        ``get_status`` used to read the run with ``SELECT *``, which applies no
        column types: PostgreSQL's driver returns a ``datetime`` for the
        ``start_date`` column, SQLite returns the TEXT that was stored, and
        ``.isoformat()`` on that string raised AttributeError -- a 500 for a run
        that exists and belongs to the caller. The read now names the columns it
        answers from and converts the two date columns through one helper, so the
        same statement produces ISO text on either backend.

        The row is seeded by ``_seed_owned_run`` rather than by the create
        endpoint, so what this node proves is the read and the typing, not the
        creation capability.
        """
        headers_a, user_id_a = _make_user(client, "owner_status_leg@test.com")
        run_id = _seed_owned_run(user_id_a)

        resp = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_a)
        assert resp.status_code == 200, (
            f"the owner's own seeded run must be readable, got "
            f"{resp.status_code}: {resp.json()}"
        )
        body = resp.json()
        assert body["run_id"] == run_id
        assert body["status"] == "queued"
        # The defect was this field: an untyped read made it a TEXT value with
        # no isoformat, so a passing node has to consume it as a date.
        datetime.fromisoformat(body["start_date"])
        assert body["end_date"] is None, "a queued run has not ended"

    def test_owner_reads_seeded_run_history_offline(self, client):
        """The sibling of the same defect, on the list route inside the API layer.

        ``GET /history`` builds its own rows from a ``text()`` read of the same
        two columns, so it carried the identical ``.isoformat()`` crash without
        going through the service at all -- fixing ``get_status`` alone would have
        left this endpoint answering 500 history_failed for every owner who has a
        run. An empty list proves nothing here, so the node first asserts the
        seeded run is present.
        """
        headers_a, user_id_a = _make_user(client, "owner_history_leg@test.com")
        run_id = _seed_owned_run(user_id_a)

        resp = client.get("/api/v1/paper-trading/history", headers=headers_a)
        assert resp.status_code == 200, (
            f"the owner's run list must be readable, got "
            f"{resp.status_code}: {resp.json()}"
        )
        rows = {row["run_id"]: row for row in resp.json()}
        assert run_id in rows, f"seeded run absent from its owner's history: {list(rows)}"
        datetime.fromisoformat(rows[run_id]["start_date"])
        assert rows[run_id]["end_date"] is None

    def test_rejection_has_no_side_effects(self, client):
        """After User B's rejected access attempt, User A's run is unchanged."""

        headers_a, user_id_a = _make_user(client, "owner_a6@test.com")
        headers_b, _ = _make_user(client, "owner_b6@test.com")
        portfolio_id, run_id = _create_portfolio_via_api(client, headers_a)

        # User B tries to access User A's run (should fail)
        resp_b = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_b)
        assert resp_b.status_code == 404

        # User A's run should still be accessible and unchanged
        resp_a = client.get(f"/api/v1/paper-trading/status/{run_id}", headers=headers_a)
        assert resp_a.status_code == 200
        assert resp_a.json()["run_id"] == run_id

        # User B should have no portfolios or runs
        resp_b_history = client.get("/api/v1/paper-trading/history", headers=headers_b)
        assert resp_b_history.status_code == 200
        b_runs = resp_b_history.json()
        assert len(b_runs) == 0, (
            f"User B must have 0 runs after rejected access, got {len(b_runs)}"
        )

    def test_multiple_runs_per_user_isolated(self, client):
        """User with multiple runs can access all; other user can access none."""

        headers_a, _ = _make_user(client, "owner_a7@test.com")
        headers_b, _ = _make_user(client, "owner_b7@test.com")

        _, run_a1 = _create_portfolio_via_api(client, headers_a, skill="momentum")
        _, run_a2 = _create_portfolio_via_api(client, headers_a, skill="mean_reversion")

        # User A can access both
        assert client.get(f"/api/v1/paper-trading/status/{run_a1}", headers=headers_a).status_code == 200
        assert client.get(f"/api/v1/paper-trading/status/{run_a2}", headers=headers_a).status_code == 200

        # User B cannot access either
        assert client.get(f"/api/v1/paper-trading/status/{run_a1}", headers=headers_b).status_code == 404
        assert client.get(f"/api/v1/paper-trading/status/{run_a2}", headers=headers_b).status_code == 404


class TestVerifyRunOwnershipDirect:
    """Test the actual _verify_run_ownership() function (not a SQL copy)."""

    def test_verify_run_ownership_succeeds_for_owner(self, client):
        """_verify_run_ownership() returns None (success) for the owner."""
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        _, user_id_a = _make_user(client, "direct_a@test.com")
        run_id = _seed_owned_run(user_id_a)

        db = TestSession()
        try:
            # Should not raise
            _verify_run_ownership(db, run_id, user_id_a)
        finally:
            db.close()

    def test_verify_run_ownership_raises_for_non_owner(self, client):
        """_verify_run_ownership() raises ValueError for non-owner.

        The owner lookup immediately before the refusal is the positive control:
        without it, any reason the join could return no rows (an id stored in a
        representation the query cannot match, a table create_all never reached)
        would raise the same "not_found" and this node would pass while testing
        nothing about ownership.
        """
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        _, user_id_a = _make_user(client, "direct_a2@test.com")
        _, user_id_b = _make_user(client, "direct_b2@test.com")
        run_id = _seed_owned_run(user_id_a)

        db = TestSession()
        try:
            _verify_run_ownership(db, run_id, user_id_a)
            with pytest.raises(ValueError, match="not_found"):
                _verify_run_ownership(db, run_id, user_id_b)
        finally:
            db.close()

    def test_verify_run_ownership_raises_for_nonexistent(self, client):
        """_verify_run_ownership() raises ValueError for non-existent run."""
        from app.api.paper_trading import _verify_run_ownership
        from tests.conftest import TestSession

        _, user_id_a = _make_user(client, "direct_a3@test.com")
        run_id = _seed_owned_run(user_id_a)
        fake_run_id = str(uuid.uuid4())

        db = TestSession()
        try:
            # Positive control: the query does resolve a run that exists.
            _verify_run_ownership(db, run_id, user_id_a)
            with pytest.raises(ValueError, match="not_found"):
                _verify_run_ownership(db, fake_run_id, user_id_a)
        finally:
            db.close()


class TestStatusReadContract:
    """The named column list is checked against what the code consumes, not restated.

    Replacing ``SELECT *`` with an explicit list is only an improvement while the
    list and the values taken from it agree: a column dropped from the query turns
    into a ``KeyError`` at the first caller, and a column left in that nobody reads
    is the same over-read the star was. Both directions are caught by comparing the
    statement against names extracted from the function body, so neither list is a
    copy that can agree with a stale query forever.
    """

    @staticmethod
    def _selected(statement):
        head = str(statement).split(" FROM ")[0].split("SELECT", 1)[1]
        return {item.strip() for item in head.split(",")}

    @staticmethod
    def _consumed(function, row_variable):
        import inspect
        import re

        body = inspect.getsource(function)
        found = set(re.findall(rf'{row_variable}\["([a-z_]+)"\]', body))
        assert found, (
            f"no {row_variable}[...] column reads found in "
            f"{function.__qualname__} -- the read changed shape and this "
            f"contract stopped checking anything"
        )
        return found

    def test_the_run_read_selects_exactly_the_columns_get_status_uses(self):
        from app.models.paper_trading_run import PaperTradingRun
        from app.services.paper_trading import PaperTradingEngine, _STATUS_RUN_READ

        consumed = self._consumed(PaperTradingEngine.get_status, "run")

        assert self._selected(_STATUS_RUN_READ) == consumed
        # A name can agree between a stale query and a stale test and still not be
        # a column; the model is the third party that says whether it exists.
        table_columns = {c.name for c in PaperTradingRun.__table__.columns}
        assert consumed <= table_columns, consumed - table_columns

    def test_the_portfolio_read_selects_exactly_the_column_get_status_uses(self):
        from app.models.paper_portfolio import PaperPortfolio
        from app.services.paper_trading import (
            PaperTradingEngine,
            _STATUS_PORTFOLIO_READ,
        )

        consumed = self._consumed(PaperTradingEngine.get_status, "portfolio")

        assert self._selected(_STATUS_PORTFOLIO_READ) == consumed
        table_columns = {c.name for c in PaperPortfolio.__table__.columns}
        assert consumed <= table_columns, consumed - table_columns


class TestStoredDateConversion:
    """One ISO form per value, whichever driver produced it.

    Only one of the two input shapes is reachable on a given lane, so the
    equivalence is what has to be asserted here rather than inferred from the
    endpoint test on whichever database this file happens to be pointed at.
    """

    def test_a_datetime_and_the_text_sqlite_stores_agree(self):
        from app.lib.timeutil import iso_from_stored

        stored = datetime(2026, 11, 16, 12, 0, 0)
        assert iso_from_stored(stored) == iso_from_stored("2026-11-16 12:00:00")
        assert iso_from_stored(stored) == "2026-11-16T12:00:00"

    def test_a_date_and_the_text_sqlite_stores_agree(self):
        from datetime import date as _date

        from app.lib.timeutil import iso_from_stored

        assert iso_from_stored(_date(2026, 11, 16)) == iso_from_stored("2026-11-16")
        # A day must not gain a midnight time component that PostgreSQL's own
        # ``date`` value would not carry.
        assert iso_from_stored("2026-11-16") == "2026-11-16"

    def test_an_absent_value_stays_absent(self):
        from app.lib.timeutil import iso_from_stored

        assert iso_from_stored(None) is None
        # The endpoint writes CURRENT_TIMESTAMP/CURRENT_DATE into these columns
        # and leaves end_date NULL until the run closes; a queued run's absent end
        # date must read as absent rather than as the empty string SQLite would
        # hand back for a blank text value.
        assert iso_from_stored("") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
