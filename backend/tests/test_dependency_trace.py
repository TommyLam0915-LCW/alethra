"""Dependency-injection wiring: HTTP requests and test sessions share one DB.

This file started life as a print-only diagnostic for a "table not found"
mystery and asserted nothing, so it could only ever pass by not crashing — and
it did crash, on ``Session.is_closed`` (no such attribute in SQLAlchemy 2.0.36).
The diagnosis it produced is kept here as assertions: the ``get_db`` override is
registered against the same function the routes depend on, both the HTTP path
and a fresh session bind to TEST_ENGINE, and a row created over HTTP is visible
to raw SQL from an independent session.
"""
import pytest
from sqlalchemy import text
from uuid import UUID

from app.database import get_db as app_get_db
from app.main import app
from app.models import Experiment
from tests.conftest import TEST_ENGINE, TestSession

STRATEGY_SPEC = {
    "template_type": "buy_and_hold",
    "asset_ids": ["US:AAPL"],
    "start_date": "2024-01-02",
    "end_date": "2024-01-31",
    "initial_cash": 100000,
}


class TestDependencyTrace:
    def test_trace_http_dependency(self, client, auth_headers, setup_db):
        """A row written through HTTP is readable from a separate session."""
        # Step 1: the override is keyed on the very function the routes depend on.
        assert app_get_db in app.dependency_overrides, (
            "tests override app.database.get_db; routes must depend on that same symbol"
        )

        # Step 2: the test session factory is bound to the engine the schema was
        # created on, otherwise reads and writes would land in different files.
        assert TestSession.kw["bind"] is TEST_ENGINE

        resp = client.post(
            "/api/v1/experiments/",
            headers=auth_headers,
            json={
                "title": "Dependency Trace Test",
                "research_type": "quantitative",
                "strategy_spec": STRATEGY_SPEC,
            },
        )
        assert resp.status_code == 201, f"HTTP create failed: {resp.status_code} {resp.text}"
        exp_id = resp.json()["id"]

        session = TestSession()
        try:
            assert session.bind is TEST_ENGINE

            via_orm = session.query(Experiment).filter(Experiment.id == UUID(exp_id)).first()
            assert via_orm is not None, "ORM read from a fresh session missed the HTTP-written row"
            assert via_orm.title == "Dependency Trace Test"

            # Look the row up by a non-UUID predicate: experiments.id is a
            # postgresql.UUID column, and on SQLite it is stored dash-less while
            # the API returns the dashed form, so a raw ``id = :string`` bind
            # would not match. Comparing parsed UUIDs keeps the assertion true on
            # both backends.
            row = session.execute(
                text("SELECT id, title FROM experiments WHERE title = :title"),
                {"title": "Dependency Trace Test"},
            ).fetchone()
            assert row is not None, "raw SQL from a fresh session missed the HTTP-written row"
            assert str(UUID(str(row[0]))) == str(UUID(exp_id))
            assert row[1] == "Dependency Trace Test"
        finally:
            session.close()

    def test_http_session_and_fresh_session_see_the_same_database(self, client, auth_headers, setup_db):
        """Two independent sessions over the same engine agree on row counts."""
        resp = client.post(
            "/api/v1/experiments/",
            headers=auth_headers,
            json={
                "title": "Shared Database Check",
                "research_type": "quantitative",
                "strategy_spec": STRATEGY_SPEC,
            },
        )
        assert resp.status_code == 201

        first = TestSession()
        second = TestSession()
        try:
            count_first = first.query(Experiment).filter(Experiment.title == "Shared Database Check").count()
            count_second = second.query(Experiment).filter(Experiment.title == "Shared Database Check").count()
            assert count_first == count_second == 1, (
                f"sessions disagree on durability: {count_first} vs {count_second}"
            )
        finally:
            first.close()
            second.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
