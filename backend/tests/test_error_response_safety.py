"""User-facing error text must not carry paths, DSNs or credentials.

Provider failures are returned in an HTTP 503 body and job/run failures are
rendered verbatim by the UI, so a raw ``str(exc)`` reaches a person.
"""

import pytest

from app.lib.error_safety import safe_error_message, safe_provider_status


class TestSafeErrorMessage:
    def test_strips_absolute_paths(self):
        msg = safe_error_message(
            RuntimeError("cannot write /Users/owner/private/artifacts/x/model.joblib")
        )
        assert "/Users/" not in msg
        assert "model.joblib" not in msg
        assert "<path>" in msg

    def test_redacts_database_url(self):
        msg = safe_error_message(
            RuntimeError("could not connect postgresql+psycopg2://postgres:hunter2@db:5432/alethra")
        )
        assert "hunter2" not in msg
        assert "<redacted-url>" in msg

    def test_redacts_assigned_secrets_and_bearer_tokens(self):
        msg = safe_error_message(
            RuntimeError("provider rejected api_key=abc123 token: zyx9 Authorization: Bearer sk-live-999")
        )
        for secret in ("abc123", "zyx9", "sk-live-999"):
            assert secret not in msg

    def test_redacts_prefixed_credential_names(self):
        # Broker and gateway clients name their fields with a prefix, so the
        # value must be removed even though the credential word is not a
        # standalone token in the message.
        msg = safe_error_message(
            RuntimeError(
                "gateway config api_token=sk-live-abc123 "
                "access_token=bearer-xyz "
                "client_secret=cs-42 "
                "apiToken=camel-77 "
                "x-api-key=header-91"
            )
        )
        for secret in ("sk-live-abc123", "bearer-xyz", "cs-42", "camel-77", "header-91"):
            assert secret not in msg
        # The name stays visible so the line remains diagnosable.
        for name in ("api_token", "access_token", "client_secret", "apiToken"):
            assert name in msg
        assert msg.count("<redacted>") >= 5

    def test_keeps_plain_message_for_the_user(self):
        assert safe_error_message(RuntimeError("simulated training crash")) == "simulated training crash"
        assert safe_error_message(ValueError("研究執行額度不足，此嘗試沒有執行。")) == "研究執行額度不足，此嘗試沒有執行。"

    def test_truncates_long_text(self):
        msg = safe_error_message(RuntimeError("x" * 500))
        assert len(msg) <= 200
        assert msg.endswith("…")

    def test_provider_status_last_error_is_scrubbed(self):
        status = {
            "provider_id": "fake",
            "available": False,
            "last_error": "upstream refused api_key=abc123",
        }
        clean = safe_provider_status(status)
        assert clean["provider_id"] == "fake"
        assert "abc123" not in clean["last_error"]
        # caller's dict is not mutated
        assert "abc123" in status["last_error"]


@pytest.fixture()
def leaking_provider(monkeypatch):
    class LeakyProvider:
        provider_id = "fake"
        provider_name = "Fake"
        feed_mode = "offline"

        def get_bars(self, *args, **kwargs):
            raise RuntimeError(
                "upstream refused /Users/owner/secret/creds.json api_key=abc123"
            )

        def is_available(self):
            return False

        def get_status(self):
            return {
                "provider_id": "fake",
                "available": False,
                "last_error": "connection refused token=abc123",
            }

    provider = LeakyProvider()
    monkeypatch.setattr("app.api.instruments.get_data_provider", lambda: provider)
    monkeypatch.setattr("app.api.demo_assets.get_data_provider", lambda: provider)
    monkeypatch.setattr(
        "app.providers.provider_registry.get_data_provider", lambda: provider
    )
    return provider


class TestProviderFailureResponse:
    def test_instrument_bars_failure_hides_internals(self, client, auth_headers, leaking_provider):
        resp = client.get(
            "/api/v1/instruments/US:AAPL/bars",
            params={"start_date": "2025-01-02", "end_date": "2025-03-31"},
            headers=auth_headers,
        )
        assert resp.status_code == 503
        body = resp.text
        assert "/Users/" not in body
        assert "abc123" not in body
        assert "creds.json" not in body
        payload = resp.json()["detail"]
        assert "US:AAPL" in payload["error"]
        assert "RuntimeError" not in payload["error"]
        assert payload["provider_status"]["provider_id"] == "fake"
        # get_status() carries the last upstream error too.
        assert "abc123" not in payload["provider_status"]["last_error"]

    def test_demo_asset_failure_hides_internals(self, client, leaking_provider):
        resp = client.get(
            "/api/v1/market-data/bars/AAPL",
            params={"start_date": "2024-01-02", "end_date": "2024-02-13"},
        )
        assert resp.status_code == 503
        body = resp.text
        assert "/Users/" not in body
        assert "abc123" not in body

    def test_provider_status_endpoint_hides_internals(self, client, leaking_provider):
        resp = client.get("/api/v1/market-data/status")
        assert resp.status_code == 200
        body = resp.text
        assert "/Users/" not in body
        assert "abc123" not in body
        assert resp.json()["status"]["provider_id"] == "fake"


def _create_experiment_with_version(client, auth_headers):
    resp = client.post(
        "/api/v1/experiments/",
        headers=auth_headers,
        json={
            "title": "Backtest leak probe",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-03-01",
                "initial_cash": 100000.0,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["current_version_id"]


class TestBacktestFailureResponse:
    def test_ad_hoc_backtest_failure_hides_internals(
        self, client, auth_headers, monkeypatch
    ):
        experiment_id, version_id = _create_experiment_with_version(
            client, auth_headers
        )

        def leaky_run_backtest(*args, **kwargs):
            raise RuntimeError(
                "cannot read /Users/owner/private/artifacts/x/model.joblib "
                "api_key=abc123 "
                "postgresql+psycopg2://postgres:hunter2@db:5432/alethra"
            )

        monkeypatch.setattr("app.api.experiments.run_backtest", leaky_run_backtest)

        resp = client.post(
            f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 500
        body = resp.text
        for secret in ("/Users/", "model.joblib", "abc123", "hunter2"):
            assert secret not in body
        detail = resp.json()["detail"]
        assert detail.startswith("Backtest execution failed:")
        assert "<path>" in detail
        assert "<redacted-url>" in detail


class TestExecutionAgentRejectionReason:
    """The agent forwards whatever its broker client raised. That text is
    persisted as ``rejection_reason`` and rendered back to the owner, so the
    storage boundary must scrub it like any other user-facing error."""

    def _authorized_intent(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 100000.0,
            "currency": "USD",
        })
        assert resp.status_code == 201, resp.text
        portfolio_id = resp.json()["id"]

        resp = client.post("/api/v1/execution/intents", headers=auth_headers, json={
            "portfolio_id": portfolio_id,
            "symbol": "US:AAPL",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
        })
        assert resp.status_code == 201, resp.text
        intent = resp.json()

        resp = client.post(
            f"/api/v1/execution/intents/{intent['intent_id']}/authorize",
            headers=auth_headers,
            json={"authorization_token": intent["authorization_token"]},
        )
        assert resp.status_code == 200, resp.text
        return intent["intent_id"]

    def test_broker_error_text_is_scrubbed_before_the_owner_sees_it(self, client, auth_headers):
        intent_id = self._authorized_intent(client, auth_headers)
        raw = (
            "connect failed at /Users/owner/ibgateway/conf.yaml "
            "api_key=abc123 "
            "postgresql+psycopg2://postgres:hunter2@db:5432/alethra"
        )

        resp = client.post(
            f"/api/v1/execution-agent/order-intents/{intent_id}/rejected",
            headers=auth_headers,
            json={"reason": raw},
        )
        assert resp.status_code == 200, resp.text
        for secret in ("/Users/", "conf.yaml", "abc123", "hunter2"):
            assert secret not in resp.text

        owner_view = client.get(
            f"/api/v1/execution/intents/{intent_id}", headers=auth_headers
        )
        assert owner_view.status_code == 200, owner_view.text
        detail = owner_view.json()["rejection_reason"]
        assert "<path>" in detail
        assert "<redacted-url>" in detail
        assert "abc123" not in detail
        assert "hunter2" not in detail
