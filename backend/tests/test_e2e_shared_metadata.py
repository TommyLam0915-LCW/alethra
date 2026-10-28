"""Regression for the shared ``market_data_metadata`` write policy (patch C-C).

``MarketDataMetadata`` is one row per ``(instrument_id, provider_id)``, written both
by the E2E seeds and by ``app.services.market_data_storage`` — which is the writer
that earns ``health_status``/``adjustment_status``/the error clock. Re-seeding the
order-lifecycle journey used to stamp ``health_status='ok'`` and
``data_nature='synthetic'`` over that row, so a degraded or real-data measurement
disappeared with no disclosure.

These tests run the module the seed imports and calls (``scripts/
e2e_shared_metadata.py``), not a copy of its logic, and then check that the seed
still routes through it.
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import app.models  # noqa: F401  registers every model, as the seed does
from app.database import Base
from app.models.market_data import MarketDataBar, MarketDataMetadata
from e2e_shared_metadata import (
    PRESERVED_ON_SHARED_ROW,
    stored_span,
    upsert_shared_metadata,
)

INSTRUMENT = "DEMO-ALPHA"
PROVIDER = "demo"
NOTE = "row spans every stored bar for this instrument/provider; not real-market data."
NOTE_ZH = "E2E 合成日線；非真實市場數據。"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    db = maker()
    yield db
    db.close()


def add_bars(db, days):
    for i, d in enumerate(days):
        db.add(MarketDataBar(
            instrument_id=INSTRUMENT,
            provider_id=PROVIDER,
            bar_date=d,
            bar_timestamp=datetime.combine(d, datetime.min.time()),
            open=10.0 + i,
            high=11.0 + i,
            low=9.0 + i,
            close=10.5 + i,
            volume=1_000_000,
        ))
    db.flush()


def call_upsert(**overrides):
    kwargs = dict(
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        note=NOTE,
        note_zh=NOTE_ZH,
        data_nature="synthetic",
        timezone="UTC",
        currency="USD",
    )
    kwargs.update(overrides)
    return kwargs


def existing_row(db, **overrides):
    """A row someone else wrote, carrying measurements this seed did not make."""
    row = MarketDataMetadata(
        instrument_id=INSTRUMENT,
        provider_id=PROVIDER,
        data_nature="real",
        adjustment_status="split_adjusted",
        timezone="Asia/Hong_Kong",
        currency="HKD",
        provider_note="written by the storage service",
        provider_note_zh="由儲存服務寫入",
        first_bar_date=date(2020, 1, 1),
        last_bar_date=date(2020, 1, 2),
        total_bars=2,
        health_status="degraded",
        last_success_at=datetime(2024, 1, 1, 0, 0, 0),
        last_error_at=datetime(2024, 1, 2, 0, 0, 0),
        consecutive_errors=3,
    )
    for field, value in overrides.items():
        setattr(row, field, value)
    db.add(row)
    db.flush()
    return row


class TestSharedRowIsPreserved:
    def test_non_ok_health_survives_the_seed(self, session):
        row = existing_row(session)
        add_bars(session, [date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 5)])

        result = upsert_shared_metadata(session, **call_upsert())
        session.commit()
        session.refresh(row)

        assert result["created"] is False
        assert row.health_status == "degraded"
        assert row.data_nature == "real"
        assert row.adjustment_status == "split_adjusted"
        assert row.timezone == "Asia/Hong_Kong"
        assert row.currency == "HKD"
        assert row.consecutive_errors == 3
        assert row.last_error_at == datetime(2024, 1, 2, 0, 0, 0)
        assert row.last_success_at == datetime(2024, 1, 1, 0, 0, 0)

    def test_every_preserved_field_is_untouched(self, session):
        row = existing_row(session)
        add_bars(session, [date(2024, 2, 1), date(2024, 2, 2)])
        before = {field: getattr(row, field) for field in PRESERVED_ON_SHARED_ROW}

        upsert_shared_metadata(session, **call_upsert())
        session.commit()
        session.refresh(row)

        assert {field: getattr(row, field) for field in PRESERVED_ON_SHARED_ROW} == before

    def test_aggregates_are_recomputed_over_every_stored_bar(self, session):
        """The span is honest about the whole pair, not just this seed's window."""
        row = existing_row(session)
        add_bars(session, [date(2024, 1, 30), date(2024, 2, 1), date(2024, 2, 13)])

        result = upsert_shared_metadata(session, **call_upsert())
        session.commit()
        session.refresh(row)

        assert (row.total_bars, row.first_bar_date, row.last_bar_date) == (
            3, date(2024, 1, 30), date(2024, 2, 13)
        )
        assert result["total_bars"] == 3
        assert row.provider_note == NOTE
        assert row.provider_note_zh == NOTE_ZH

    def test_result_reports_what_was_preserved(self, session):
        existing_row(session, health_status="degraded", data_nature="real")
        add_bars(session, [date(2024, 2, 1)])

        result = upsert_shared_metadata(session, **call_upsert())

        preserved = dict(result["preserved"])
        assert result["created"] is False
        assert preserved["health_status"] == "degraded"
        assert preserved["data_nature"] == "real"


class TestFirstWriterOwnsTheRow:
    def test_absent_row_is_created_with_the_seed_own_fields(self, session):
        add_bars(session, [date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 3)])

        result = upsert_shared_metadata(session, **call_upsert())
        session.commit()

        assert result["created"] is True
        row = session.query(MarketDataMetadata).one()
        assert (row.health_status, row.data_nature, row.timezone, row.currency) == (
            "ok", "synthetic", "UTC", "USD"
        )
        assert (row.total_bars, row.first_bar_date, row.last_bar_date) == (
            3, date(2024, 2, 1), date(2024, 2, 3)
        )

    def test_explicit_id_is_kept_so_the_seed_stays_idempotent(self, session):
        import uuid
        new_id = uuid.uuid4()
        add_bars(session, [date(2024, 2, 1)])

        upsert_shared_metadata(session, **call_upsert(new_id=new_id))
        session.commit()

        assert session.query(MarketDataMetadata).one().id == new_id


class TestRecomputeIsStable:
    def test_second_run_does_not_drift_the_span(self, session):
        existing_row(session)
        add_bars(session, [date(2024, 2, 1), date(2024, 2, 8)])

        first = upsert_shared_metadata(session, **call_upsert())
        session.commit()
        second = upsert_shared_metadata(session, **call_upsert())
        session.commit()

        assert first["total_bars"] == second["total_bars"] == 2
        assert first["first_bar_date"] == second["first_bar_date"]
        assert first["last_bar_date"] == second["last_bar_date"]

    def test_stored_span_reads_the_database_not_the_caller(self, session):
        add_bars(session, [date(2024, 2, 1), date(2024, 2, 9)])
        assert stored_span(session, INSTRUMENT, PROVIDER) == (2, date(2024, 2, 1), date(2024, 2, 9))
        assert stored_span(session, INSTRUMENT, "other-provider") == (0, None, None)


class TestSeedUsesThisPolicy:
    """The seed must route through the helper, or these tests guard dead code."""

    def test_seed_calls_the_shared_upsert_and_never_writes_health(self):
        source = (SCRIPTS_DIR / "seed_e2e_order_lifecycle.py").read_text(encoding="utf-8")
        assert "from e2e_shared_metadata import upsert_shared_metadata" in source
        assert "upsert_shared_metadata(" in source
        assert ".health_status" not in source, (
            "the seed writes health_status directly again - route it through "
            "e2e_shared_metadata.upsert_shared_metadata"
        )
        assert "data_nature = " not in source
