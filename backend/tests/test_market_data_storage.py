"""Tests for market data storage layer."""
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.market_data import MarketDataBar, MarketDataMetadata
from app.providers.data_provider import OHLCVBar
from app.services.market_data_storage import MarketDataStorage
from app.lib.timeutil import utcnow


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def storage(db_session):
    return MarketDataStorage(db_session)


@pytest.fixture
def sample_bars():
    base_date = date(2024, 1, 1)
    return [
        OHLCVBar(
            date=(base_date + timedelta(days=i)).isoformat(),
            open=100.0 + i,
            high=105.0 + i,
            low=95.0 + i,
            close=102.0 + i,
            volume=1000000 + i * 1000,
        )
        for i in range(5)
    ]


class TestMarketDataStorage:
    def test_store_bars_creates_records(self, storage, sample_bars, db_session):
        count = storage.store_bars(
            instrument_id="US:AAPL",
            provider_id="demo",
            bars=sample_bars,
            data_nature="synthetic",
            adjustment_status="not_applicable",
            timezone="America/New_York",
            currency="USD",
        )

        assert count == 5

        bars = db_session.query(MarketDataBar).filter_by(instrument_id="US:AAPL").all()
        assert len(bars) == 5
        assert all(b.provider_id == "demo" for b in bars)
        assert bars[0].open == 100.0
        assert bars[4].close == 106.0

    def test_store_bars_updates_metadata(self, storage, sample_bars, db_session):
        storage.store_bars(
            instrument_id="US:AAPL",
            provider_id="demo",
            bars=sample_bars,
            data_nature="synthetic",
            adjustment_status="not_applicable",
            timezone="America/New_York",
            currency="USD",
            provider_note="Test data",
            provider_note_zh="測試數據",
        )

        metadata = db_session.query(MarketDataMetadata).filter_by(instrument_id="US:AAPL").first()
        assert metadata is not None
        assert metadata.provider_id == "demo"
        assert metadata.data_nature == "synthetic"
        assert metadata.adjustment_status == "not_applicable"
        assert metadata.timezone == "America/New_York"
        assert metadata.currency == "USD"
        assert metadata.provider_note == "Test data"
        assert metadata.provider_note_zh == "測試數據"
        assert metadata.first_bar_date == date(2024, 1, 1)
        assert metadata.last_bar_date == date(2024, 1, 5)
        assert metadata.total_bars == 5
        assert metadata.health_status == "ok"
        assert metadata.consecutive_errors == 0

    def test_store_bars_upsert_logic(self, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        updated_bars = [
            OHLCVBar(
                date=sample_bars[0].date,
                open=200.0,
                high=205.0,
                low=195.0,
                close=202.0,
                volume=2000000,
            )
        ]
        storage.store_bars("US:AAPL", "demo", updated_bars, "synthetic")

        bar_date = date.fromisoformat(sample_bars[0].date)
        bar_timestamp = datetime.combine(bar_date, datetime.min.time())
        bars = db_session.query(MarketDataBar).filter_by(
            instrument_id="US:AAPL",
            bar_timestamp=bar_timestamp,
        ).all()
        assert len(bars) == 1
        assert bars[0].open == 200.0
        assert bars[0].close == 202.0

    def test_get_bars_with_filters(self, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        all_bars = storage.get_bars("US:AAPL")
        assert len(all_bars) == 5

        filtered = storage.get_bars(
            "US:AAPL",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
        )
        assert len(filtered) == 3

        with_provider = storage.get_bars("US:AAPL", provider_id="demo")
        assert len(with_provider) == 5

        wrong_provider = storage.get_bars("US:AAPL", provider_id="lseg")
        assert len(wrong_provider) == 0

    def test_get_metadata(self, storage, sample_bars, db_session):
        storage.store_bars(
            "US:AAPL",
            "demo",
            sample_bars,
            "synthetic",
            timezone="America/New_York",
        )

        metadata = storage.get_metadata("US:AAPL")
        assert metadata is not None
        assert metadata.instrument_id == "US:AAPL"
        assert metadata.data_nature == "synthetic"

        with_provider = storage.get_metadata("US:AAPL", provider_id="demo")
        assert with_provider is not None

        wrong_provider = storage.get_metadata("US:AAPL", provider_id="lseg")
        assert wrong_provider is None

    def test_mark_error(self, storage, db_session):
        storage.mark_error("US:AAPL", "demo", "Connection timeout")

        metadata = db_session.query(MarketDataMetadata).filter_by(instrument_id="US:AAPL").first()
        assert metadata is not None
        assert metadata.consecutive_errors == 1
        assert metadata.last_error_at is not None
        assert metadata.health_status == "ok"

        storage.mark_error("US:AAPL", "demo", "Connection timeout again")
        db_session.refresh(metadata)
        assert metadata.consecutive_errors == 2

        storage.mark_error("US:AAPL", "demo", "Third error")
        db_session.refresh(metadata)
        assert metadata.consecutive_errors == 3
        assert metadata.health_status == "degraded"

    def test_get_stale_instruments(self, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        metadata = db_session.query(MarketDataMetadata).first()
        metadata.last_success_at = utcnow() - timedelta(days=10)
        db_session.commit()

        stale = storage.get_stale_instruments(max_age_days=7)
        assert len(stale) == 1
        assert stale[0].instrument_id == "US:AAPL"

        fresh = storage.get_stale_instruments(max_age_days=15)
        assert len(fresh) == 0

    def test_store_empty_bars(self, storage, db_session):
        count = storage.store_bars("US:AAPL", "demo", [], "synthetic")
        assert count == 0

        bars = db_session.query(MarketDataBar).filter_by(instrument_id="US:AAPL").all()
        assert len(bars) == 0

    def test_metadata_date_range_expansion(self, storage, db_session):
        first_batch = [
            OHLCVBar(
                date="2024-01-10",
                open=100.0, high=105.0, low=95.0, close=102.0, volume=1000000,
            )
        ]
        storage.store_bars("US:AAPL", "demo", first_batch, "synthetic")

        metadata = db_session.query(MarketDataMetadata).first()
        assert metadata.first_bar_date == date(2024, 1, 10)
        assert metadata.last_bar_date == date(2024, 1, 10)

        second_batch = [
            OHLCVBar(
                date="2024-01-05",
                open=90.0, high=95.0, low=85.0, close=92.0, volume=900000,
            ),
            OHLCVBar(
                date="2024-01-15",
                open=110.0, high=115.0, low=105.0, close=112.0, volume=1100000,
            ),
        ]
        storage.store_bars("US:AAPL", "demo", second_batch, "synthetic")

        db_session.refresh(metadata)
        assert metadata.first_bar_date == date(2024, 1, 5)
        assert metadata.last_bar_date == date(2024, 1, 15)
        assert metadata.total_bars == 3
