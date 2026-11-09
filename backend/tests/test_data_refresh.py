"""Tests for the data refresh endpoint."""
import pytest
from unittest.mock import patch, MagicMock

from app.providers.data_provider import OHLCVBar


class TestDataRefreshEndpoint:
    """Test POST /instruments/{instrument_id}/bars/refresh endpoint."""

    @patch("app.api.instruments.get_data_provider")
    def test_refresh_success(self, mock_get_provider, client, auth_headers):
        """Successful refresh stores bars and returns metadata."""
        mock_provider = MagicMock()
        mock_provider.provider_name = "Demo"
        mock_provider.provider_id = "demo"
        mock_provider.feed_mode = "offline"
        mock_provider.get_bars.return_value = [
            OHLCVBar(date="2024-01-02", open=150.0, high=152.0, low=149.0, close=151.0, volume=1000000),
            OHLCVBar(date="2024-01-03", open=151.0, high=153.0, low=150.0, close=152.0, volume=1100000),
        ]
        mock_provider.get_data_quality.return_value = {
            "data_nature": "synthetic",
            "adjustment": "not_applicable",
            "note": "Demo data",
            "note_zh": "演示數據",
        }
        mock_provider.get_status.return_value = {"health": "ok"}
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/instruments/US:AAPL/bars/refresh?start_date=2024-01-02&end_date=2024-01-03",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "refreshed"
        assert data["bars_stored"] == 2
        assert data["provider"] == "Demo"
        assert data["instrument_id"] == "US:AAPL"
        assert "Successfully refreshed" in data["message"]

    @patch("app.api.instruments.get_data_provider")
    def test_refresh_no_data(self, mock_get_provider, client, auth_headers):
        """Refresh with no data returns appropriate status."""
        mock_provider = MagicMock()
        mock_provider.provider_name = "Demo"
        mock_provider.get_bars.return_value = []
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/instruments/US:AAPL/bars/refresh?start_date=2024-01-02&end_date=2024-01-03",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "no_data"
        assert data["bars_stored"] == 0

    def test_refresh_invalid_instrument(self, client, auth_headers):
        """Refresh for non-existent instrument returns 404."""
        response = client.post(
            "/api/v1/instruments/INVALID:SYMBOL/bars/refresh?start_date=2024-01-02&end_date=2024-01-03",
            headers=auth_headers,
        )

        assert response.status_code == 404

    @patch("app.api.instruments.get_data_provider")
    def test_refresh_provider_error(self, mock_get_provider, client, auth_headers):
        """Provider failure returns 503 with provider status."""
        mock_provider = MagicMock()
        mock_provider.provider_id = "demo"
        mock_provider.get_bars.side_effect = Exception("Provider unavailable")
        mock_provider.get_status.return_value = {"health": "disconnected"}
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/instruments/US:AAPL/bars/refresh?start_date=2024-01-02&end_date=2024-01-03",
            headers=auth_headers,
        )

        assert response.status_code == 503
        data = response.json()["detail"]
        assert "Failed to refresh" in data["error"]
        assert data["provider_status"]["health"] == "disconnected"

    @patch("app.api.instruments.get_data_provider")
    def test_refresh_updates_metadata(self, mock_get_provider, client, auth_headers):
        """Refresh updates storage metadata with quality info."""
        mock_provider = MagicMock()
        mock_provider.provider_name = "Demo"
        mock_provider.provider_id = "demo"
        mock_provider.feed_mode = "offline"
        mock_provider.get_bars.return_value = [
            OHLCVBar(date="2024-01-02", open=150.0, high=152.0, low=149.0, close=151.0, volume=1000000),
        ]
        mock_provider.get_data_quality.return_value = {
            "data_nature": "synthetic",
            "adjustment": "not_applicable",
            "note": "Demo data",
            "note_zh": "演示數據",
        }
        mock_provider.get_status.return_value = {"health": "ok"}
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/instruments/US:AAPL/bars/refresh?start_date=2024-01-02&end_date=2024-01-02",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "refreshed"
        assert data["data_quality"]["data_nature"] == "synthetic"
        assert data["storage_metadata"]["total_bars"] >= 1
        assert data["storage_metadata"]["health_status"] == "ok"
