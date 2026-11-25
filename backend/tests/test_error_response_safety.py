"""User-facing error text must not carry paths, DSNs or credentials.

Provider failures are returned in an HTTP 503 body and job/run failures are
rendered verbatim by the UI, so a raw ``str(exc)`` reaches a person. Scrubbing
covers those. A third channel takes the opposite remedy: the paper-trading
routes' catch-all 500 handlers report an *unexpected* failure, for which no
scrubbed exception text is safe to show at all, so they answer a fixed sentence
and leave the traceback in the log.
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


class TestPaperTradingUnexpectedFailure:
    """The six paper-trading 500 handlers say what failed, never how.

    Every route in ``app/api/paper_trading.py`` ends in an ``except Exception``
    that used to put ``str(exc)`` in ``detail.message``. §12.5 measured what that
    means in practice: the status read crashed on an untyped SQLite column, and
    the caller's 500 body carried the Python AttributeError --
    ``'str' object has no attribute 'isoformat'`` -- as its explanation. Each
    handler now logs the traceback and answers one fixed sentence with a
    machine-readable reason code.

    The check is that the body does not track the exception, which a copy of the
    product's message constant here could never show: the same endpoint is driven
    twice with two different internal failures and must answer identically, once
    with a payload carrying a path, a credential and a DSN, and once with the
    exact text this lane really leaked.
    """

    INTERNAL_FAILURES = (
        "cannot read /Users/owner/private/artifacts/model.joblib "
        "api_key=abc123 "
        "postgresql+psycopg2://postgres:hunter2@db:5432/alethra",
        "'str' object has no attribute 'isoformat'",
    )
    LEAK_MARKERS = (
        "/Users/", "model.joblib", "artifacts", "abc123", "hunter2",
        "isoformat", "postgresql+psycopg2", "<redacted",
    )

    # (reason token, http method, path template, request body)
    ENGINE_ENDPOINTS = [
        pytest.param(
            "creation_failed", "post", "/api/v1/paper-trading/create",
            {"skill": "momentum", "instrument_id": "CRYPTO:KRAKEN:BTC-USD",
             "interval_minutes": 60, "exit_config": "exit_1",
             "initial_capital": 100000.0},
            id="creation_failed",
        ),
        pytest.param(
            "status_failed", "get",
            "/api/v1/paper-trading/status/{run_id}", None, id="status_failed",
        ),
        pytest.param(
            "report_failed", "get",
            "/api/v1/paper-trading/daily-report/{run_id}", None, id="report_failed",
        ),
        pytest.param(
            "replay_failed", "post", "/api/v1/paper-trading/replay/{run_id}",
            {"days_back": 30}, id="replay_failed",
        ),
        pytest.param(
            "stop_failed", "post", "/api/v1/paper-trading/stop/{run_id}",
            None, id="stop_failed",
        ),
    ]

    @pytest.fixture
    def ownership_granted(self, monkeypatch):
        """Ownership is proved elsewhere; here the request must reach the engine."""
        monkeypatch.setattr(
            "app.api.paper_trading._verify_run_ownership",
            lambda *args, **kwargs: None,
        )

    def _drive(self, client, auth_headers, monkeypatch, reason, method, path, body):
        import uuid as _uuid

        from app.services.paper_trading import PaperTradingEngine

        failures = []

        for internal in self.INTERNAL_FAILURES:
            def leaky(*args, payload=internal, **kwargs):
                raise RuntimeError(payload)

            for name in (
                "create_portfolio", "get_status", "generate_daily_report",
                "execute_historical_replay", "cancel_run",
            ):
                monkeypatch.setattr(PaperTradingEngine, name, leaky)

            resp = getattr(client, method)(
                path.format(run_id=str(_uuid.uuid4())),
                headers=auth_headers,
                **({} if body is None else {"json": body}),
            )
            assert resp.status_code == 500, (
                f"{reason}: expected the injected engine failure to answer 500, "
                f"got {resp.status_code}: {resp.text[:200]}"
            )
            detail = resp.json()["detail"]
            assert detail["reason"] == reason, detail
            for marker in self.LEAK_MARKERS:
                assert marker not in resp.text, (
                    f"{reason}: {marker!r} from the internal failure reached the "
                    f"response body: {resp.text[:200]}"
                )
            failures.append(detail["message"])

        assert failures[0] == failures[1], (
            "the 500 body tracks the exception text, which is the leak: "
            f"{failures!r}"
        )
        return failures[0]

    @pytest.mark.parametrize("reason,method,path,body", ENGINE_ENDPOINTS)
    def test_engine_failure_body_is_generic(
        self, client, auth_headers, ownership_granted, monkeypatch,
        reason, method, path, body,
    ):
        message = self._drive(
            client, auth_headers, monkeypatch, reason, method, path, body
        )
        assert message.strip(), "an empty message is not a usable error either"

    def test_history_route_failure_body_is_generic(self, client, auth_headers):
        """/history has no engine call, so its 500 comes from the session.

        Driving it through ``get_db`` keeps the injection at the boundary the route
        actually uses instead of patching the handler, and this is the route where
        the untyped-column crash of §12.5 lived inside the API layer itself. The
        fake has to be the real session with one statement made to fail, because
        the auth dependency shares the same override -- a stand-in object broke the
        token lookup before the route ever ran.
        """
        import tests.conftest as conftest
        from app.main import app

        internal = self.INTERNAL_FAILURES[0]

        class LeakingHistoryQuery:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, statement, *args, **kwargs):
                # r.run_type appears only in the history SELECT, so a discriminator
                # this narrow means the 500 below can only come from that route.
                if "r.run_type" in str(statement):
                    raise RuntimeError(internal)
                return self._inner.execute(statement, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        def leaky_db():
            for session in conftest.override_get_db():
                yield LeakingHistoryQuery(session)

        saved = app.dependency_overrides.get(conftest._ORIGINAL_GET_DB)
        app.dependency_overrides[conftest._ORIGINAL_GET_DB] = leaky_db
        try:
            resp = client.get("/api/v1/paper-trading/history", headers=auth_headers)
            assert resp.status_code == 500, (
                f"expected the injected session failure to answer 500, got "
                f"{resp.status_code}: {resp.text[:200]}"
            )
            detail = resp.json()["detail"]
            assert detail["reason"] == "history_failed", detail
            for marker in self.LEAK_MARKERS:
                assert marker not in resp.text, (
                    f"{marker!r} from the internal failure reached the body: "
                    f"{resp.text[:200]}"
                )
        finally:
            if saved is None:
                app.dependency_overrides.pop(conftest._ORIGINAL_GET_DB, None)
            else:
                app.dependency_overrides[conftest._ORIGINAL_GET_DB] = saved

