"""Tests for stored market data query endpoint."""
import pytest
from datetime import date, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
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
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


class TestStoredBarsEndpoint:
    def test_get_stored_bars_empty(self, client, db_session):
        response = client.get("/instruments/US:AAPL/bars/stored")
        assert response.status_code == 200
        data = response.json()
        assert data["instrument_id"] == "US:AAPL"
        assert data["source"] == "storage"
        assert data["bar_count"] == 0
        assert data["bars"] == []
        assert data["metadata"] is None
        assert "message" in data

    def test_get_stored_bars_with_data(self, client, storage, sample_bars, db_session):
        storage.store_bars(
            instrument_id="US:AAPL",
            provider_id="demo",
            bars=sample_bars,
            data_nature="synthetic",
            adjustment_status="not_applicable",
            timezone="America/New_York",
            currency="USD",
        )

        response = client.get("/instruments/US:AAPL/bars/stored")
        assert response.status_code == 200
        data = response.json()
        assert data["instrument_id"] == "US:AAPL"
        assert data["source"] == "storage"
        assert data["bar_count"] == 5
        assert len(data["bars"]) == 5
        assert data["bars"][0]["date"] == "2024-01-01"
        assert data["bars"][0]["open"] == 100.0
        assert data["bars"][0]["provider_id"] == "demo"
        assert data["data_quality"] is not None
        assert data["data_quality"]["data_nature"] == "synthetic"
        assert data["data_quality"]["timezone"] == "America/New_York"

    def test_get_stored_bars_with_date_filter(self, client, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        response = client.get(
            "/instruments/US:AAPL/bars/stored",
            params={"start_date": "2024-01-02", "end_date": "2024-01-04"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bar_count"] == 3
        assert data["bars"][0]["date"] == "2024-01-02"
        assert data["bars"][-1]["date"] == "2024-01-04"

    def test_get_stored_bars_with_provider_filter(self, client, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        response = client.get(
            "/instruments/US:AAPL/bars/stored",
            params={"provider_id": "demo"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bar_count"] == 5

        response = client.get(
            "/instruments/US:AAPL/bars/stored",
            params={"provider_id": "lseg"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bar_count"] == 0

    def test_get_stored_bars_with_limit(self, client, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        response = client.get(
            "/instruments/US:AAPL/bars/stored",
            params={"limit": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bar_count"] == 3

    def test_get_stored_bars_invalid_instrument(self, client, db_session):
        response = client.get("/instruments/INVALID:SYMBOL/bars/stored")
        assert response.status_code == 404

    def test_get_stored_bars_includes_metadata(self, client, storage, sample_bars, db_session):
        storage.store_bars(
            "US:AAPL",
            "demo",
            sample_bars,
            "synthetic",
            provider_note="Test data",
            provider_note_zh="測試數據",
        )

        response = client.get("/instruments/US:AAPL/bars/stored")
        assert response.status_code == 200
        data = response.json()
        assert data["data_quality"]["provider_note"] == "Test data"
        assert data["data_quality"]["provider_note_zh"] == "測試數據"
        assert data["data_quality"]["first_bar_date"] == "2024-01-01"
        assert data["data_quality"]["last_bar_date"] == "2024-01-05"
        assert data["data_quality"]["total_bars"] == 5
        assert data["data_quality"]["health_status"] == "ok"

    def test_get_stored_bars_staleness_tracking(self, client, storage, sample_bars, db_session):
        storage.store_bars("US:AAPL", "demo", sample_bars, "synthetic")

        response = client.get("/instruments/US:AAPL/bars/stored")
        assert response.status_code == 200
        data = response.json()
        assert "is_stale" in data["data_quality"]
        assert "bar_age_days" in data["data_quality"]
        assert data["data_quality"]["is_stale"] is True
