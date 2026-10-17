"""Contract: AlethraAPIClient URL paths must match routes the FastAPI app serves.

The execution agent polls and reports over HTTP. If the client's path prefix
drifts from the app's mounting (``/api/v1/execution-agent/...``), the agent
fails closed at every poll — silent in offline tests (they inject a fake
client) and fatal in deployment. This test instantiates the real client,
captures the URL each method would request, and asserts both the exact
expected path and that the app exposes that route.
"""
from datetime import datetime

import pytest

from app.execution_agent.alethra_client import AlethraAPIClient, FillReport

BASE = "http://testserver"
PREFIX = f"{BASE}/api/v1/execution-agent"


class _Captured(Exception):
    def __init__(self, url):
        self.url = url


@pytest.fixture()
def app_paths():
    from app.main import app

    return set(app.openapi()["paths"].keys())


@pytest.fixture()
def capture_url(monkeypatch):
    urls = []

    def fake_get(url, **kwargs):
        raise _Captured(url)

    def fake_post(url, **kwargs):
        raise _Captured(url)

    monkeypatch.setattr("app.execution_agent.alethra_client.requests.get", fake_get)
    monkeypatch.setattr("app.execution_agent.alethra_client.requests.post", fake_post)
    return urls


def _client():
    return AlethraAPIClient(BASE, "token")


def _expect(method, expected_path, expected_template, app_paths):
    try:
        method()
    except _Captured as captured:
        assert captured.url == expected_path, (
            f"Client URL drifted from the platform API: {captured.url} != {expected_path}"
        )
        path = expected_template.split(BASE, 1)[1]
        assert path in app_paths, f"App does not expose {path}"
        return
    raise AssertionError("client method did not issue an HTTP request")


def test_poll_order_intents_url(capture_url, app_paths):
    _expect(_client().poll_order_intents, f"{PREFIX}/order-intents",
            f"{PREFIX}/order-intents", app_paths)


def test_report_submitted_url(capture_url, app_paths):
    _expect(
        lambda: _client().report_order_submitted("i", "ref"),
        f"{PREFIX}/order-intents/i/submitted",
        f"{PREFIX}/order-intents/{{intent_id}}/submitted",
        app_paths,
    )


def test_report_fill_url(capture_url, app_paths):
    fill = FillReport(
        intent_id="i", quantity=1, price=1.0,
        fill_time=datetime.now(), broker_ref="ref",
    )
    _expect(
        lambda: _client().report_fill(fill),
        f"{PREFIX}/order-intents/i/fills",
        f"{PREFIX}/order-intents/{{intent_id}}/fills",
        app_paths,
    )


def test_report_rejected_url(capture_url, app_paths):
    _expect(
        lambda: _client().report_order_rejected("i", "reason"),
        f"{PREFIX}/order-intents/i/rejected",
        f"{PREFIX}/order-intents/{{intent_id}}/rejected",
        app_paths,
    )


def test_report_uncertain_url(capture_url, app_paths):
    _expect(
        lambda: _client().report_order_uncertain("i", "reason"),
        f"{PREFIX}/order-intents/i/uncertain",
        f"{PREFIX}/order-intents/{{intent_id}}/uncertain",
        app_paths,
    )


def test_report_cancelled_url(capture_url, app_paths):
    _expect(
        lambda: _client().report_order_cancelled("i", "reason"),
        f"{PREFIX}/order-intents/i/cancelled",
        f"{PREFIX}/order-intents/{{intent_id}}/cancelled",
        app_paths,
    )


def test_control_status_url(capture_url, app_paths):
    _expect(_client().get_control_status, f"{PREFIX}/control-status",
            f"{PREFIX}/control-status", app_paths)


def test_claimed_broker_refs_url(capture_url, app_paths):
    _expect(_client().get_claimed_broker_refs, f"{PREFIX}/claimed-broker-refs",
            f"{PREFIX}/claimed-broker-refs", app_paths)


def test_heartbeat_url(capture_url, app_paths):
    _expect(
        lambda: _client().report_agent_heartbeat(connected=True, ib_gateway_host="h", ib_gateway_port=1),
        f"{PREFIX}/heartbeat",
        f"{PREFIX}/heartbeat",
        app_paths,
    )
