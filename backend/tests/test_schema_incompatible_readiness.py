"""FB-16: a missing mapped column must be visible, classified and never charged.

The 2026-11-01 ``volume_decimal`` incident: the candidate database was reachable
and stamped at an older Alembic revision while the running code already mapped
the column. Every stored-bar read failed with ``UndefinedColumn`` (HTTP 500,
generic ``internal_error``), yet ``/health/ready`` still said ready because it
only checked connectivity, and the run path blamed the provider. These tests pin
the replacement contract:

1. the checker names the missing column using ORM metadata vs
   ``information_schema`` and issues SELECTs only;
2. readiness refuses a reachable-but-incompatible database, and fails closed
   when the check itself cannot run;
3. a stored-bar read returns the persisted OHLCV once the column exists, and
   the same rows read through storage, the ORM, the simulation loader and the
   HTTP read agree;
4. a run that hits the structural error records one failed run, releases the
   quota reservation and charges nothing;
5. the customer-facing error carries a code and a generated reference, with no
   SQL text, DSN or exception class.

The migration itself is validated on disposable PostgreSQL elsewhere; these
tests run on the offline harness.
"""

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app.api.simulations import _load_stored_ohlcv
from app.lib.timeutil import utcnow
from app.models.audit_event import AuditEvent
from app.models.experiment import Experiment
from app.models.experiment_version import ExperimentVersion
from app.models.market_data import MarketDataBar
from app.models.pilot import PilotEntitlement
from app.models.quota import QuotaCounter, QuotaReservation
from app.models.research_run import ResearchRun, RunStatus
from app.models.user import User
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import MarketDataStorage
from app.services.research_runs import execute_backtest_run
from app.services.schema_compat import check_schema_compatibility, summarise
from tests.conftest import TestSession

HEX32 = re.compile(r"^[0-9a-f]{32}$")
SECRET_DSN = "postgresql+psycopg2://postgres:hunter2@db:5432/alethra"
INSTRUMENT = "US:AAPL"
PROVIDER = "demo"


class _StubResult:
    def __init__(self, rows=(), value=None):
        self._rows = list(rows)
        self._value = value

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._value


class _StubDialect:
    name = "postgresql"


class _ReadOnlyConn:
    """Answers only the SELECTs the checker is allowed to issue."""

    def __init__(self, columns, revision):
        self.dialect = _StubDialect()
        self._columns = columns
        self._revision = revision
        self.statements = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        lowered = sql.lower()
        if lowered.startswith("select current_schema()"):
            return _StubResult(value="public")
        if "information_schema.columns" in lowered:
            return _StubResult(rows=[
                (table, column)
                for table, columns in self._columns.items()
                for column in columns
            ])
        if "from alembic_version" in lowered:
            return _StubResult(value=self._revision)
        raise AssertionError(
            f"schema check issued a statement outside its read-only contract: {sql}"
        )


def _mapped_columns():
    from app.database import Base
    import app.models  # noqa: F401  - ensure every mapped table is registered

    return {
        table.name: [column.name for column in table.columns]
        for table in Base.metadata.tables.values()
        if table.schema in (None, "public")
    }


def _incident_result():
    return {
        "status": "incompatible",
        "alembic_revision": "r2s3t4u5v6w7",
        "missing_tables": [],
        "missing_columns": {"market_data_bars": ["volume_decimal"]},
    }


class _FakeOriginal(Exception):
    def __init__(self):
        super().__init__(
            f"column market_data_bars.volume_decimal does not exist "
            f"(server {SECRET_DSN})"
        )
        self.pgcode = "42703"


def _schema_programming_error():
    return ProgrammingError(
        "SELECT market_data_bars.volume_decimal FROM market_data_bars "
        "WHERE market_data_bars.instrument_id = 'US:AAPL'",
        {},
        _FakeOriginal(),
    )


@pytest.fixture()
def raw_client():
    """A client that returns the error response instead of re-raising it."""
    from app.main import app
    from app.database import get_db as orig_get_db
    from tests.conftest import override_get_db

    app.dependency_overrides[orig_get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _failing_db(exc):
    from app.main import app
    from app.database import get_db as orig_get_db

    def failing_db():
        raise exc
        yield  # pragma: no cover - unreachable, keeps this a generator

    return app, orig_get_db, failing_db


# ── 1. The checker names the missing column and never writes ────────────────


def test_checker_names_the_missing_column_and_issues_only_selects():
    columns = _mapped_columns()
    columns["market_data_bars"] = [
        name for name in columns["market_data_bars"] if name != "volume_decimal"
    ]
    conn = _ReadOnlyConn(columns, "r2s3t4u5v6w7")

    result = check_schema_compatibility(conn)

    assert result == _incident_result()
    assert all(s.lower().startswith("select") for s in conn.statements)
    assert "market_data_bars: volume_decimal" in summarise(result)


def test_checker_reports_a_complete_schema_as_compatible():
    conn = _ReadOnlyConn(_mapped_columns(), "u5v6w7x8y9z0")

    result = check_schema_compatibility(conn)

    assert result["status"] == "compatible"
    assert result["missing_columns"] == {}
    assert result["missing_tables"] == []
    assert result["alembic_revision"] == "u5v6w7x8y9z0"


# ── 2. Readiness refuses an incompatible schema and fails closed ────────────


def _patch_readiness(schema):
    return (
        patch("app.api.health._check_database",
              return_value={"status": "connected", "latency_ms": 1.0}),
        patch("app.api.health._check_redis",
              return_value={"status": "not_configured"}),
        schema,
    )


def test_readiness_blocks_a_reachable_but_incompatible_database(client):
    db_patch, redis_patch, schema_patch = _patch_readiness(
        patch("app.api.health.check_schema_compatibility",
              return_value=_incident_result())
    )
    with db_patch, redis_patch, schema_patch:
        resp = client.get("/health/ready")

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "not_ready"
    check = body["checks"]["schema"]
    assert check["status"] == "unavailable"
    assert check["reason"] == "schema_incompatible"
    assert check["missing_columns"] == {"market_data_bars": ["volume_decimal"]}
    assert check["alembic_revision"] == "r2s3t4u5v6w7"
    for forbidden in ("SELECT", "postgresql", "psycopg2", "hunter2"):
        assert forbidden not in resp.text


def test_readiness_is_ready_when_the_schema_matches(client):
    db_patch, redis_patch, schema_patch = _patch_readiness(
        patch("app.api.health.check_schema_compatibility",
              return_value={"status": "compatible",
                            "alembic_revision": "u5v6w7x8y9z0"})
    )
    with db_patch, redis_patch, schema_patch:
        resp = client.get("/health/ready")

    assert resp.status_code == 200, resp.text
    assert resp.json()["checks"]["schema"] == {
        "status": "compatible",
        "alembic_revision": "u5v6w7x8y9z0",
    }


def test_readiness_fails_closed_when_the_schema_check_cannot_run(client, caplog):
    db_patch, redis_patch, schema_patch = _patch_readiness(
        patch("app.api.health.check_schema_compatibility",
              side_effect=RuntimeError(f"boom {SECRET_DSN}"))
    )
    caplog.set_level(logging.WARNING, logger="app.api.health")
    with db_patch, redis_patch, schema_patch:
        resp = client.get("/health/ready")

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["schema"]["status"] == "unknown"
    for forbidden in ("boom", "postgresql", "hunter2"):
        assert forbidden not in resp.text


# ── 3. Stored OHLCV reads back, and every entry point sees one dataset ─────


def _seed_bars(db):
    bars = [
        OHLCVBar(date="2025-06-02", open=211.0, high=212.5, low=210.25,
                 close=212.0, volume=1_000_003),
        OHLCVBar(date="2025-06-03", open=212.0, high=213.75, low=211.5,
                 close=213.0, volume=1_000_007),
        OHLCVBar(date="2025-06-04", open=213.0, high=214.5, low=212.25,
                 close=214.0, volume=1_000_011),
    ]
    MarketDataStorage(db).store_bars(
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        bars=bars,
        data_nature="synthetic",
        adjustment_status="not_applicable",
        timezone="America/New_York",
        currency="USD",
    )
    db.commit()
    return bars


def test_stored_ohlcv_reads_back_with_every_column(client):
    db = TestSession()
    try:
        seeded = _seed_bars(db)
        stored = MarketDataStorage(db).get_bars(INSTRUMENT, provider_id=PROVIDER)
        assert [(b.bar_date.isoformat(), b.open, b.high, b.low, b.close, b.volume)
                for b in stored] == [
            (b.date, b.open, b.high, b.low, b.close, b.volume) for b in seeded
        ]
        # Crypto rows carry their exact amount in volume_decimal.
        db.add(MarketDataBar(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=date(2025, 6, 5),
            bar_timestamp=datetime(2025, 6, 5),
            open=214.0,
            high=214.0,
            low=214.0,
            close=214.0,
            volume=None,
            volume_decimal=Decimal("0.123456789012"),
        ))
        db.commit()
        decimal_row = (
            db.query(MarketDataBar)
            .filter(MarketDataBar.bar_date == date(2025, 6, 5))
            .one()
        )
        assert decimal_row.volume_decimal == Decimal("0.123456789012")
    finally:
        db.close()

    resp = client.get(f"/api/v1/instruments/{INSTRUMENT}/bars/stored")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["bar_count"] == 4
    returned = {
        bar["date"]: (bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
        for bar in body["bars"]
    }
    assert returned["2025-06-02"] == (211.0, 212.5, 210.25, 212.0, 1_000_003)
    # A NULL integer volume is reported as unknown, never as a fabricated 0.
    assert returned["2025-06-05"][-1] is None


def test_one_stored_dataset_is_the_same_at_every_entry_point(client):
    db = TestSession()
    try:
        _seed_bars(db)

        storage_rows = MarketDataStorage(db).get_bars(INSTRUMENT, provider_id=PROVIDER)
        orm_rows = (
            db.query(MarketDataBar)
            .filter(MarketDataBar.instrument_id == INSTRUMENT)
            .order_by(MarketDataBar.bar_date)
            .all()
        )
        simulation_df = _load_stored_ohlcv(
            INSTRUMENT, "2025-06-02", "2025-06-04", db, provider_id=PROVIDER
        )
    finally:
        db.close()

    api_bars = client.get(
        f"/api/v1/instruments/{INSTRUMENT}/bars/stored",
        params={"start_date": "2025-06-02", "end_date": "2025-06-04"},
    ).json()["bars"]

    expected = [
        ("2025-06-02", 211.0, 212.5, 210.25, 212.0, 1_000_003),
        ("2025-06-03", 212.0, 213.75, 211.5, 213.0, 1_000_007),
        ("2025-06-04", 213.0, 214.5, 212.25, 214.0, 1_000_011),
    ]

    def _from_storage(bars):
        return [(b.bar_date.isoformat(), b.open, b.high, b.low, b.close, b.volume)
                for b in bars]

    assert _from_storage(storage_rows) == expected
    assert _from_storage(orm_rows) == expected
    assert [
        (index, row.open, row.high, row.low, row.close, int(row.volume))
        for index, row in simulation_df.iterrows()
    ] == expected
    assert [
        (b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"])
        for b in api_bars
    ] == expected


def test_simulation_loader_does_not_turn_a_null_volume_into_zero():
    db = TestSession()
    try:
        db.add(MarketDataBar(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=date(2025, 6, 2),
            bar_timestamp=datetime(2025, 6, 2),
            open=211.0,
            high=212.0,
            low=210.0,
            close=211.5,
            volume=None,
            volume_decimal=Decimal("0.123456789012"),
        ))
        db.commit()

        df = _load_stored_ohlcv(INSTRUMENT, "2025-06-02", "2025-06-02", db)
    finally:
        db.close()

    assert df.loc["2025-06-02", "volume"] == Decimal("0.123456789012")
    assert df.loc["2025-06-02", "volume"] != 0


# ── 4. A schema failure leaves one failed run and charges no quota ──────────


def test_a_schema_failed_run_is_recorded_but_never_charged(monkeypatch):
    db = TestSession()
    try:
        user = User(
            id=uuid4(),
            email=f"schema-{uuid4()}@example.com",
            display_name="Schema Failure",
            password_hash="x",
        )
        db.add(user)
        db.flush()
        db.add(PilotEntitlement(user_id=user.id, research_run_limit=5))
        db.flush()
        experiment = Experiment(owner_id=user.id, title="FB-16 failure path")
        db.add(experiment)
        db.flush()
        version = ExperimentVersion(
            experiment_id=experiment.id,
            version_number=1,
            strategy_spec={
                "template_type": "buy_and_hold",
                "asset_ids": [INSTRUMENT],
                "start_date": "2025-06-02",
                "end_date": "2025-06-04",
                "initial_cash": 10000.0,
            },
            created_by=user.id,
        )
        db.add(version)
        db.commit()

        def explode(*args, **kwargs):
            raise _schema_programming_error()

        monkeypatch.setattr("app.services.research_runs.run_backtest", explode)

        run = execute_backtest_run(
            db, user=user, version=version, experiment=experiment,
            data_source="stored",
        )

        assert run.status == RunStatus.failed
        assert run.result_json is None
        assert db.query(ResearchRun).count() == 1

        reservation = db.query(QuotaReservation).one()
        assert reservation.status == "released"
        counter = (
            db.query(QuotaCounter)
            .filter(QuotaCounter.user_id == user.id,
                    QuotaCounter.quota_type == "research_run")
            .first()
        )
        assert counter is None or counter.used == 0

        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.entity_id == str(run.id))
            .all()
        )
        types = [event.event_type for event in events]
        assert "research_run_started" in types
        assert "research_run_failed" in types
        assert "research_run_completed" not in types

        failed = next(e for e in events if e.event_type == "research_run_failed")
        detail = str(failed.payload_json)
        for forbidden in ("SELECT", "market_data_bars", "volume_decimal",
                          "postgresql", "hunter2", "ProgrammingError"):
            assert forbidden not in detail
        assert "database structure does not match" in run.error_details
    finally:
        db.close()


# ── 5. The customer-facing error carries a code and a reference only ───────


def test_undefined_column_is_a_503_with_a_reference_and_no_internals(
    client, raw_client, auth_headers, monkeypatch, caplog
):
    app, orig_get_db, failing_db = _failing_db(_schema_programming_error())
    monkeypatch.setitem(app.dependency_overrides, orig_get_db, failing_db)
    caplog.set_level(logging.ERROR, logger="app.request_errors")

    resp = raw_client.get("/api/v1/experiments/", headers=auth_headers)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "schema_incompatible"
    assert HEX32.match(body["request_id"])
    assert body["request_id"] == resp.headers["x-request-id"]

    for forbidden in ("SELECT", "market_data_bars", "volume_decimal",
                      "postgresql", "hunter2", "psycopg2", "ProgrammingError"):
        assert forbidden not in resp.text

    assert any(
        body["request_id"] in record.getMessage()
        for record in caplog.records
        if record.name == "app.request_errors"
    )


def test_other_programming_errors_stay_500_internal_error(
    client, raw_client, auth_headers, monkeypatch
):
    original = Exception(f"syntax error at or near (server {SECRET_DSN})")
    original.pgcode = "42601"
    exc = ProgrammingError("SELECT broken", {}, original)
    app, orig_get_db, failing_db = _failing_db(exc)
    monkeypatch.setitem(app.dependency_overrides, orig_get_db, failing_db)

    resp = raw_client.get("/api/v1/experiments/", headers=auth_headers)

    assert resp.status_code == 500, resp.text
    body = resp.json()
    assert body["code"] == "internal_error"
    assert HEX32.match(body["request_id"])
    for forbidden in ("SELECT", "postgresql", "hunter2", "syntax error"):
        assert forbidden not in resp.text
