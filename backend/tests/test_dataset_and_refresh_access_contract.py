"""Access-contract regressions for the dataset catalog and market-data refresh.

Work Package A contract under test:

* dataset catalog — any authenticated user may browse approved datasets and
  snapshots; only an admin may create datasets/snapshots; ``user_uploaded``
  is unsupported and explicitly rejected; snapshot paths are relative and
  internal absolute paths are never returned to ordinary users.
* refresh — unauthenticated and ordinary users are rejected server-side;
  an admin may refresh only after date validation and rate limiting. Every
  rejection must perform zero provider calls and leave market data unchanged.
"""
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.providers.data_provider import OHLCVBar
from tests.conftest import TestSession

DATASETS_URL = "/api/v1/datasets"
REFRESH_URL = (
    "/api/v1/instruments/US:AAPL/bars/refresh"
    "?start_date=2024-01-02&end_date=2024-01-03"
)


# --- helpers ---------------------------------------------------------------


def _dataset_state():
    from app.models.audit_event import AuditEvent
    from app.models.dataset import Dataset, DatasetSnapshot

    db = TestSession()
    try:
        return (
            db.query(Dataset).count(),
            db.query(DatasetSnapshot).count(),
            db.query(AuditEvent)
            .filter(AuditEvent.event_type == "dataset_created")
            .count(),
        )
    finally:
        db.close()


def _market_data_state(instrument_id="US:AAPL"):
    from app.models.market_data import MarketDataBar, MarketDataMetadata

    db = TestSession()
    try:
        return (
            db.query(MarketDataBar)
            .filter(MarketDataBar.instrument_id == instrument_id)
            .count(),
            db.query(MarketDataMetadata)
            .filter(MarketDataMetadata.instrument_id == instrument_id)
            .count(),
        )
    finally:
        db.close()


def _mock_provider(bars=None):
    provider = MagicMock()
    provider.provider_name = "Demo"
    provider.provider_id = "demo"
    provider.feed_mode = "offline"
    provider.get_bars.return_value = (
        bars
        if bars is not None
        else [
            OHLCVBar(
                date="2024-01-02",
                open=150.0,
                high=152.0,
                low=149.0,
                close=151.0,
                volume=1000000,
            )
        ]
    )
    provider.get_data_quality.return_value = {
        "data_nature": "synthetic",
        "adjustment": "not_applicable",
        "note": "Demo data",
        "note_zh": "演示數據",
    }
    provider.get_status.return_value = {"health": "ok"}
    return provider


def _create_dataset(client, headers, source_type="demo"):
    return client.post(
        DATASETS_URL,
        json={"name": "US demo catalog", "provider": "demo", "source_type": source_type},
        headers=headers,
    )


# --- dataset catalog -------------------------------------------------------


def test_anonymous_cannot_browse_catalog(client):
    resp = client.get(DATASETS_URL)
    assert resp.status_code == 401


def test_ordinary_user_can_browse_catalog_without_internal_paths(
    client, admin_headers, auth_headers
):
    created = _create_dataset(client, admin_headers)
    assert created.status_code == 201
    dataset_id = created.json()["id"]

    snapshot = client.post(
        f"{DATASETS_URL}/{dataset_id}/snapshots",
        json={
            "snapshot_date": "2024-01-02",
            "data_file_path": "datasets/demo/2024-01-02.parquet",
        },
        headers=admin_headers,
    )
    assert snapshot.status_code == 201

    listed = client.get(DATASETS_URL, headers=auth_headers)
    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()] == [dataset_id]

    detail = client.get(f"{DATASETS_URL}/{dataset_id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["snapshots"]) == 1
    path = body["snapshots"][0]["data_file_path"]
    assert path == "datasets/demo/2024-01-02.parquet"
    assert not path.startswith("/")


def test_ordinary_user_cannot_create_dataset(client, auth_headers):
    before = _dataset_state()
    resp = _create_dataset(client, auth_headers)
    assert resp.status_code == 403
    assert _dataset_state() == before


def test_anonymous_cannot_create_dataset(client):
    before = _dataset_state()
    resp = _create_dataset(client, headers={})
    assert resp.status_code == 401
    assert _dataset_state() == before


def test_user_uploaded_dataset_is_explicitly_rejected(client, admin_headers):
    before = _dataset_state()
    resp = _create_dataset(client, admin_headers, source_type="user_uploaded")
    assert resp.status_code == 400
    assert "user_uploaded" in resp.json()["detail"]
    assert _dataset_state() == before


def test_ordinary_user_cannot_create_snapshot(client, admin_headers, auth_headers):
    created = _create_dataset(client, admin_headers)
    assert created.status_code == 201
    dataset_id = created.json()["id"]
    before = _dataset_state()

    resp = client.post(
        f"{DATASETS_URL}/{dataset_id}/snapshots",
        json={
            "snapshot_date": "2024-01-02",
            "data_file_path": "datasets/demo/2024-01-02.parquet",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 403
    assert _dataset_state() == before


@pytest.mark.parametrize(
    "path",
    [
        "/srv/alethra/internal/2024-01-02.parquet",
        "../../etc/passwd",
        "~/private/2024-01-02.parquet",
        "C:\\data\\alethra\\2024-01-02.parquet",
        "datasets/../../etc/passwd",
    ],
)
def test_snapshot_rejects_server_paths(client, admin_headers, path):
    created = _create_dataset(client, admin_headers)
    assert created.status_code == 201
    dataset_id = created.json()["id"]
    before = _dataset_state()

    resp = client.post(
        f"{DATASETS_URL}/{dataset_id}/snapshots",
        json={"snapshot_date": "2024-01-02", "data_file_path": path},
        headers=admin_headers,
    )
    assert resp.status_code == 400
    assert _dataset_state() == before


def test_ordinary_user_never_receives_absolute_snapshot_path(
    client, auth_headers, admin_headers
):
    """Legacy/rows-with-absolute-paths must not leak to ordinary users."""
    from app.models.dataset import Dataset, DatasetSnapshot

    db = TestSession()
    try:
        dataset = Dataset(name="legacy catalog", provider="ops")
        db.add(dataset)
        db.flush()
        db.add(
            DatasetSnapshot(
                dataset_id=dataset.id,
                snapshot_date=date(2024, 1, 2),
                data_file_path="/srv/alethra/internal/2024-01-02.parquet",
            )
        )
        db.commit()
        dataset_id = str(dataset.id)
    finally:
        db.close()

    as_user = client.get(f"{DATASETS_URL}/{dataset_id}", headers=auth_headers)
    assert as_user.status_code == 200
    user_path = as_user.json()["snapshots"][0]["data_file_path"]
    assert "/" not in user_path
    assert user_path == "2024-01-02.parquet"

    as_admin = client.get(f"{DATASETS_URL}/{dataset_id}", headers=admin_headers)
    assert as_admin.status_code == 200
    assert (
        as_admin.json()["snapshots"][0]["data_file_path"]
        == "/srv/alethra/internal/2024-01-02.parquet"
    )


# --- market data refresh ---------------------------------------------------


@patch("app.api.instruments.get_data_provider")
def test_refresh_anonymous_rejected_without_side_effects(mock_get_provider, client):
    mock_get_provider.return_value = _mock_provider()
    before = _market_data_state()
    resp = client.post(REFRESH_URL)
    assert resp.status_code == 401
    mock_get_provider.assert_not_called()
    assert _market_data_state() == before


@patch("app.api.instruments.get_data_provider")
def test_refresh_ordinary_user_rejected_without_side_effects(
    mock_get_provider, client, auth_headers
):
    mock_get_provider.return_value = _mock_provider()
    before = _market_data_state()
    resp = client.post(REFRESH_URL, headers=auth_headers)
    assert resp.status_code == 403
    mock_get_provider.assert_not_called()
    assert _market_data_state() == before


@patch("app.api.instruments.get_data_provider")
def test_refresh_ordinary_user_api_key_has_no_extra_permission(
    mock_get_provider, client, auth_headers
):
    key_resp = client.post(
        "/api/v1/api-keys",
        json={"name": "refresh-scope-probe", "scopes": ["write"]},
        headers=auth_headers,
    )
    assert key_resp.status_code == 201
    raw_key = key_resp.json()["raw_key"]
    mock_get_provider.return_value = _mock_provider()

    before = _market_data_state()
    resp = client.post(
        REFRESH_URL, headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert resp.status_code == 403
    mock_get_provider.assert_not_called()
    assert _market_data_state() == before


@patch("app.api.instruments.get_data_provider")
def test_refresh_admin_legal_request_succeeds(mock_get_provider, client, admin_headers):
    mock_get_provider.return_value = _mock_provider()

    resp = client.post(REFRESH_URL, headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "refreshed"
    assert body["bars_stored"] == 1
    assert body["data_quality"]["data_nature"] == "synthetic"

    bars, metadata = _market_data_state()
    assert bars == 1
    assert metadata == 1


@pytest.mark.parametrize(
    "query",
    [
        "start_date=not-a-date&end_date=2024-01-03",
        "start_date=2024-01-02&end_date=2024-13-01",
        "start_date=2024-01-03&end_date=2024-01-02",
    ],
)
@patch("app.api.instruments.get_data_provider")
def test_refresh_rejects_invalid_range_before_provider(
    mock_get_provider, client, admin_headers, query
):
    mock_get_provider.return_value = _mock_provider()
    before = _market_data_state()
    resp = client.post(f"/api/v1/instruments/US:AAPL/bars/refresh?{query}", headers=admin_headers)
    assert resp.status_code == 400
    mock_get_provider.assert_not_called()
    assert _market_data_state() == before


@patch("app.api.instruments.get_data_provider")
def test_refresh_admin_is_rate_limited(
    mock_get_provider, client, admin_headers, monkeypatch
):
    import app.services.rate_limit as rate_limit_module

    monkeypatch.setitem(rate_limit_module.RATE_LIMITS, "market_data_refresh", 1)
    mock_get_provider.return_value = _mock_provider()

    first = client.post(REFRESH_URL, headers=admin_headers)
    assert first.status_code == 200
    assert mock_get_provider.return_value.get_bars.call_count == 1

    second = client.post(REFRESH_URL, headers=admin_headers)
    assert second.status_code == 429
    assert mock_get_provider.return_value.get_bars.call_count == 1

    bars, _ = _market_data_state()
    assert bars == 1
