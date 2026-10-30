"""Read-only crypto market API, offline.

The provider is replaced with an in-memory transport, so these tests exercise
the routes, permission split and storage write path without a socket. They are
the contract for "live venue feed, never synthetic fallback" at the HTTP layer.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.providers.crypto_registry import reset_crypto_provider, set_crypto_provider
from app.providers.kraken_provider import KrakenProvider

BASE = "/api/v1/market/crypto"
INSTRUMENT = "CRYPTO:KRAKEN:BTC-USD"
NOW = datetime(2026, 10, 30, 12, 0, 30, tzinfo=timezone.utc)
LAST_CLOSED = datetime(2026, 10, 30, 11, 59, 0, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        entry = self._routes[path]
        if callable(entry):
            return entry(params)
        if isinstance(entry, Exception):
            raise entry
        return entry


def _row(ts, close="100.0", volume="0.5"):
    c = Decimal(close)
    timestamp = int(ts.timestamp()) if isinstance(ts, datetime) else int(ts)
    return [timestamp, str(c), str(c + Decimal("1")), str(c - Decimal("1")), str(c), str(c), volume, 4]


def _ohlc_rows(count=4, include_forming=True):
    start = LAST_CLOSED - timedelta(minutes=count - 1)
    rows = [_row(start + timedelta(minutes=i)) for i in range(count)]
    if include_forming:
        rows.append(_row(LAST_CLOSED + timedelta(minutes=1)))
    return rows


def _ohlc_payload(rows):
    return {"error": [], "result": {"XXBTZUSD": rows, "last": "unused"}}


def _install(routes):
    provider = KrakenProvider(
        transport=FakeTransport(routes),
        clock=lambda: NOW,
        min_request_interval=0,
    )
    set_crypto_provider(provider)
    return provider


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_crypto_provider()
    yield
    reset_crypto_provider()


# -- directory --------------------------------------------------------------

def test_overview_declares_lane_and_terms(client):
    _install({})
    resp = client.get(BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["lane"] == "crypto_research_mode"
    assert body["stage"] == "local_or_internal_research"
    assert body["read_only"] is True
    assert body["orders_supported"] is False
    assert body["redistribution"] == "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
    assert body["terms_note_zh"]
    assert body["instruments"][0]["instrument_id"] == INSTRUMENT
    assert body["instruments"][0]["quote_asset"] == "USD"
    assert body["provider"]["declared_pairs"] == [INSTRUMENT]
    # Nothing has been fetched yet, so the venue is not claimed as live.
    assert body["provider"]["available"] is False


def test_instruments_endpoint_lists_venue_and_pair(client):
    resp = client.get(f"{BASE}/instruments")
    assert resp.status_code == 200
    instruments = resp.json()["instruments"]
    assert len(instruments) == 1
    inst = instruments[0]
    assert inst["exchange"] == "KRAKEN"
    assert inst["pair"] == "BTC/USD"
    assert inst["currency"] == "USD"
    assert inst["read_only"] is True
    assert inst["orders_supported"] is False


# -- series -----------------------------------------------------------------

def test_series_separates_completed_and_forming(client):
    _install({"/0/public/OHLC": _ohlc_payload(_ohlc_rows(count=4, include_forming=True))})
    resp = client.get(f"{BASE}/series", params={"instrument_id": INSTRUMENT, "interval": "1m"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["forming_excluded_from_strategy"] is True
    assert len(body["completed"]) == 4
    assert all(c["is_final"] for c in body["completed"])
    assert body["forming"] is not None
    assert body["forming"]["is_final"] is False
    assert body["forming"]["open_time"] not in {c["open_time"] for c in body["completed"]}
    assert body["provenance"]["redistribution"] == "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
    assert body["provenance"]["data_nature"] == "live"
    assert body["mode"] == "live_realtime"
    # Values cross the API as exact decimal strings, not rounded floats.
    assert body["completed"][0]["close"] == "100.0"


def test_series_unknown_instrument_is_404(client):
    _install({})
    resp = client.get(f"{BASE}/series", params={"instrument_id": "CRYPTO:KRAKEN:ETH-USD"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["reason"] == "unknown_crypto_instrument"


def test_series_bad_interval_is_400(client):
    _install({})
    resp = client.get(f"{BASE}/series", params={"instrument_id": INSTRUMENT, "interval": "2m"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "unsupported_interval"


def test_series_venue_outage_is_503_and_never_falls_back(client):
    _install({"/0/public/OHLC": ConnectionError("network down")})
    resp = client.get(f"{BASE}/series", params={"instrument_id": INSTRUMENT, "interval": "1m"})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["available"] is False
    assert detail["reason"] == "venue_unreachable"
    assert "no_fallback" in detail
    assert detail["no_fallback"]


# -- snapshot permissions ---------------------------------------------------

def test_snapshot_requires_authentication(client):
    _install({"/0/public/OHLC": _ohlc_payload(_ohlc_rows())})
    resp = client.post(f"{BASE}/snapshot", params={"instrument_id": INSTRUMENT, "interval": "1m"})
    assert resp.status_code == 401


def test_snapshot_requires_admin(client, auth_headers):
    _install({"/0/public/OHLC": _ohlc_payload(_ohlc_rows())})
    resp = client.post(
        f"{BASE}/snapshot",
        params={"instrument_id": INSTRUMENT, "interval": "1m"},
        headers=auth_headers,
    )
    assert resp.status_code == 403


def test_snapshot_by_admin_persists_completed_only(client, admin_headers):
    provider = _install(
        {"/0/public/OHLC": _ohlc_payload(_ohlc_rows(count=4, include_forming=True))}
    )
    resp = client.post(
        f"{BASE}/snapshot",
        params={"instrument_id": INSTRUMENT, "interval": "1m", "limit": 10},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stored"
    assert body["stored"] == 4
    assert body["updated"] == 0
    assert body["integrity"]["completed_count"] == 4
    assert body["metadata"]["data_nature"] == "live"
    assert body["metadata"]["total_bars"] == 4

    stored = client.get(f"{BASE}/stored", params={"instrument_id": INSTRUMENT})
    assert stored.status_code == 200
    stored_body = stored.json()
    assert stored_body["provider_id"] == provider.provider_id
    assert stored_body["count"] == 4

    forming_iso = (LAST_CLOSED + timedelta(minutes=1)).isoformat() + "Z"
    open_times = {bar["open_time"] for bar in stored_body["stored_bars"]}
    assert forming_iso not in open_times
    # Fractional crypto volume is preserved exactly, not rounded to an integer.
    for bar in stored_body["stored_bars"]:
        assert Decimal(bar["volume"]) == Decimal("0.5")
    assert stored_body["metadata"]["currency"] == "USD"
    assert stored_body["metadata"]["timezone"] == "UTC"


def test_snapshot_is_idempotent(client, admin_headers):
    _install({"/0/public/OHLC": _ohlc_payload(_ohlc_rows(count=3, include_forming=True))})
    params = {"instrument_id": INSTRUMENT, "interval": "1m"}
    first = client.post(f"{BASE}/snapshot", params=params, headers=admin_headers).json()
    assert first["stored"] == 3 and first["updated"] == 0

    second = client.post(f"{BASE}/snapshot", params=params, headers=admin_headers).json()
    assert second["stored"] == 0 and second["updated"] == 3

    stored = client.get(f"{BASE}/stored", params={"instrument_id": INSTRUMENT}).json()
    assert stored["count"] == 3


def test_stored_is_empty_before_any_snapshot(client):
    _install({})
    resp = client.get(f"{BASE}/stored", params={"instrument_id": INSTRUMENT})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["stored_bars"] == []


def test_stored_unknown_instrument_is_404(client):
    _install({})
    resp = client.get(f"{BASE}/stored", params={"instrument_id": "CRYPTO:KRAKEN:ETH-USD"})
    assert resp.status_code == 404
