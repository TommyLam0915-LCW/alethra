"""Execution containment tests — bounded offline-only round (2026-10-27).

EVIDENCE LAYER: fake transport / injected stub ``ib`` objects only. Nothing in
this file opens a network connection, logs in to a broker, or starts the agent
service. These tests prove the *containment policy* (no broker side effect is
reachable); they are NOT evidence about IB Gateway behaviour, paper vs live
account environments, or real order routing.

PROVENANCE: the failures these tests were written to expose were observed on the
pre-containment **working-tree snapshot** (HEAD was ``6bedafa`` while unrelated
uncommitted work was also present in the tree) — not on a pristine ``6bedafa``
checkout. Results here are agent-executed evidence; independent review pending.

Gap under test (read-only inspection of that pre-patch snapshot):
- ``agent.main()`` selected ``IBKRBrokerTransport`` when ``ALETHRA_BROKER_TRANSPORT``
  was unset (implicit real-broker startup default).
- ``IBKRBrokerTransport.connect()`` called ``ib.connect(host, port, ...)`` with no
  check at all, and ``submit_order()`` called ``ib.placeOrder()`` as soon as
  ``is_connected()`` was true — the only paper/live discriminator was an
  operator-typed port.
- The platform's agent poll returns every ``authorized`` intent, and intents have
  no execution scope / account binding, so ``authorize`` alone placed an intent
  in a queue that a broker-connected agent would submit.

Fixtures therefore build "missing/unknown scope" intents with ``setattr`` instead
of a constructor keyword: that keeps every test in this file constructible
against the pre-patch code, so each gap surfaces as a failed assertion with real
side-effect call counts, not as a TypeError or a collection error.
"""
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.execution_agent import agent as agent_module
from app.execution_agent import broker_transport as bt_module
from app.execution_agent.alethra_client import OrderIntentDTO
from app.execution_agent.broker_transport import (
    FakeBrokerTransport,
    IBKRBrokerTransport,
)
from app.execution_agent.ibkr_client import IBKRClient
from app.execution_agent.config import AgentConfig
from app.execution_agent.agent import ExecutionAgent
from app.lib.timeutil import utcnow
from app.models.execution import FillRecord, FillSource, OrderIntent
from tests.conftest import TestSession


# ── Repository-root resolution for the operator-guide assertion ──────────
#
# The guide lives in the repository's ``docs/`` directory, which is outside the
# backend image. Walking up from ``__file__`` only works when the test runs from
# a host checkout; a runner that mounts just ``backend/`` must say where the
# repository is. Markers keep the walk honest, and a missing guide fails with
# the fix in the message — the containment wording in that guide is part of
# what this round asserts, so it must never be skipped.

_REPO_ROOT_MARKERS = ("backend/app", "docs")
_GUIDE_RELATIVE_PATH = "docs/ibkr-execution-agent-setup-guide.md"


def _looks_like_repo_root(candidate: Path) -> bool:
    return all((candidate / marker).is_dir() for marker in _REPO_ROOT_MARKERS)


def _resolve_repo_root() -> Path:
    override = os.environ.get("ALETHRA_REPO_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not _looks_like_repo_root(root):
            pytest.fail(
                f"ALETHRA_REPO_ROOT={override!r} is not a repository root: "
                f"expected directories {', '.join(_REPO_ROOT_MARKERS)} under it.",
                pytrace=False,
            )
        return root

    for candidate in Path(__file__).resolve().parents:
        if _looks_like_repo_root(candidate):
            return candidate

    pytest.fail(
        f"Could not locate the repository root from {Path(__file__).resolve()}: "
        f"no parent directory contains {' and '.join(_REPO_ROOT_MARKERS)}. "
        "Run this test from a host checkout, or mount the repository into the "
        "test container and set ALETHRA_REPO_ROOT to that mount point.",
        pytrace=False,
    )


# ── Broker side-effect boundary recorders ────────────────────────────────
#
# Every assertion below counts calls at the *actual* primitive that touches a
# broker (``ib.connect`` / ``ib.placeOrder`` / ``ib.cancelOrder``) or at the
# transport methods the agent uses. A returned boolean is never the evidence.

class RecordingIb:
    """ib_insync.IB stand-in that records each real side-effect boundary."""

    def __init__(self, connected=True):
        self.connect_calls = []
        self.place_orders = []
        self.cancel_orders = []
        self._connected = connected

    def connect(self, host, port, clientId=None, timeout=None):
        self.connect_calls.append((host, port, clientId, timeout))
        return self._connected

    def disconnect(self):
        self._connected = False

    def isConnected(self):
        return self._connected

    def placeOrder(self, contract, order):
        order.orderId = len(self.place_orders) + 1
        trade = SimpleNamespace(
            contract=contract,
            order=order,
            orderStatus=SimpleNamespace(
                status="Submitted", filled=0, totalQuantity=order.totalQuantity
            ),
            fills=[],
            log=[SimpleNamespace(time=utcnow())],
        )
        self.place_orders.append((contract, order, trade))
        return trade

    def cancelOrder(self, order):
        self.cancel_orders.append(order)

    def trades(self):
        return [t for (_c, _o, t) in self.place_orders]


class SpyBrokerTransport(FakeBrokerTransport):
    """Fake transport that counts every boundary the agent can drive."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.boundary_calls = {
            "connect": 0,
            "submit_order": 0,
            "cancel_order": 0,
            "get_recent_orders": 0,
        }

    def connect(self):
        self.boundary_calls["connect"] += 1
        super().connect()

    def submit_order(self, *args, **kwargs):
        self.boundary_calls["submit_order"] += 1
        return super().submit_order(*args, **kwargs)

    def cancel_order(self, broker_order_id):
        self.boundary_calls["cancel_order"] += 1
        return super().cancel_order(broker_order_id)

    def get_recent_orders(self, since):
        self.boundary_calls["get_recent_orders"] += 1
        return super().get_recent_orders(since)


def _broker_calls(ib: RecordingIb) -> dict:
    return {
        "connect": len(ib.connect_calls),
        "placeOrder": len(ib.place_orders),
        "cancelOrder": len(ib.cancel_orders),
    }


def _is_containment_refusal(exc) -> bool:
    """A refusal must name the policy; any error is not enough."""
    message = str(exc).lower()
    return "containment" in message and "disabled" in message


# ── Intent fixtures ──────────────────────────────────────────────────────

def _intent(
    intent_id="ct-intent-001",
    symbol="US:AAPL",
    side="BUY",
    quantity=10,
    order_type="MARKET",
    limit_price=None,
    status="authorized",
    broker_ref=None,
    expires_in_minutes=5,
    execution_scope="__unset__",
):
    """Build an OrderIntentDTO.

    ``execution_scope`` defaults to a sentinel meaning "do not set the attribute
    at all", which is how a platform intent arrives both before and after the
    patch (the platform has no execution-scope column).
    """
    dto = OrderIntentDTO(
        intent_id=intent_id,
        user_id="user-1",
        portfolio_id="portfolio-1",
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        status=status,
        expires_at=(utcnow() + timedelta(minutes=expires_in_minutes)).isoformat(),
        created_at=utcnow().isoformat(),
        broker_ref=broker_ref,
    )
    if execution_scope != "__unset__":
        setattr(dto, "execution_scope", execution_scope)
    return dto


def _alethra_mock(intents=None):
    client = MagicMock()
    client.poll_order_intents.return_value = list(intents or [])
    client.get_control_status.return_value = {"kill_switch_active": False}
    client.get_claimed_broker_refs.return_value = []
    return client


def _agent(transport, alethra_client, *, trading_mode="paper", allow_live=False):
    config = AgentConfig(
        alethra_api_key="containment-test-key",
        alethra_api_url="http://127.0.0.1:0",
        poll_interval_seconds=1,
        reconnect_interval_seconds=0,
        max_reconnect_attempts=1,
        trading_mode=trading_mode,
        allow_live=allow_live,
    )
    return ExecutionAgent(config, IBKRClient(transport), alethra_client)


# ── Platform fixtures (offline HTTP simulation path) ─────────────────────

def _create_portfolio(client, auth_headers):
    resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": 100000.0,
        "currency": "USD",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_authorize_intent(client, auth_headers, portfolio_id, **overrides):
    payload = {
        "portfolio_id": portfolio_id,
        "symbol": "US:AAPL",
        "side": "BUY",
        "quantity": 10,
        "order_type": "MARKET",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
    assert resp.status_code == 201
    created = resp.json()
    resp = client.post(
        f"/api/v1/execution/intents/{created['intent_id']}/authorize",
        headers=auth_headers,
        json={"authorization_token": created["authorization_token"]},
    )
    assert resp.status_code == 200
    return resp.json()


# ════════════════════════════════════════════════════════════════════════
# A. Startup: no implicit broker transport, nothing env can flip on
# ════════════════════════════════════════════════════════════════════════

class TestTransportSelectionIsContained:

    def _build(self, monkeypatch, env_value):
        """Call the startup transport factory (extracted from main()).

        Pre-patch this helper fails with a plain assertion, exposing that the
        choice of broker transport was inlined behind an ``ibkr`` default.
        """
        assert hasattr(agent_module, "build_transport"), (
            "no startup transport factory to test — main() still selects the "
            "broker transport inline with an implicit default"
        )
        if env_value is None:
            monkeypatch.delenv("ALETHRA_BROKER_TRANSPORT", raising=False)
        else:
            monkeypatch.setenv("ALETHRA_BROKER_TRANSPORT", env_value)
        config = AgentConfig(alethra_api_key="k", alethra_api_url="http://127.0.0.1:0")
        return agent_module.build_transport(config)

    def test_unset_transport_does_not_select_ibkr(self, monkeypatch):
        ctor = MagicMock()
        monkeypatch.setattr(bt_module, "IBKRBrokerTransport", ctor)
        with pytest.raises(SystemExit):
            self._build(monkeypatch, None)
        assert ctor.call_count == 0

    def test_unknown_transport_does_not_select_ibkr(self, monkeypatch):
        ctor = MagicMock()
        monkeypatch.setattr(bt_module, "IBKRBrokerTransport", ctor)
        for value in ("", "  ", "tastytrade", "IBKR-Gateway", "real"):
            with pytest.raises(SystemExit):
                self._build(monkeypatch, value)
        assert ctor.call_count == 0

    def test_explicit_ibkr_value_is_still_refused_this_round(self, monkeypatch):
        ctor = MagicMock()
        monkeypatch.setattr(bt_module, "IBKRBrokerTransport", ctor)
        with pytest.raises(SystemExit) as excinfo:
            self._build(monkeypatch, "ibkr")
        assert ctor.call_count == 0
        assert _is_containment_refusal(excinfo.value) or "containment" in str(
            " ".join(str(a) for a in excinfo.value.args)
        ).lower()

    def test_live_env_switches_cannot_unlock_the_broker(self, monkeypatch):
        ctor = MagicMock()
        monkeypatch.setattr(bt_module, "IBKRBrokerTransport", ctor)
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
        monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
        with pytest.raises(SystemExit):
            self._build(monkeypatch, "ibkr")
        assert ctor.call_count == 0

    def test_fake_is_the_only_runnable_transport(self, monkeypatch):
        transport = self._build(monkeypatch, "fake")
        assert isinstance(transport, FakeBrokerTransport)
        assert not isinstance(transport, IBKRBrokerTransport)

    def test_main_never_constructs_an_ibkr_transport_when_env_unset(self, monkeypatch):
        """Startup path, not just the factory: nothing is built or connected."""
        ctor = MagicMock()
        monkeypatch.setattr(bt_module, "IBKRBrokerTransport", ctor)
        monkeypatch.delenv("ALETHRA_BROKER_TRANSPORT", raising=False)
        monkeypatch.setattr(
            agent_module,
            "load_config_from_env",
            lambda: AgentConfig(
                alethra_api_key="containment-test-key",
                alethra_api_url="http://127.0.0.1:0",
            ),
        )
        run = MagicMock()
        monkeypatch.setattr(ExecutionAgent, "run", run)
        with pytest.raises(SystemExit):
            agent_module.main()
        assert ctor.call_count == 0
        assert run.call_count == 0


# ════════════════════════════════════════════════════════════════════════
# B. Transport boundary: connect / submit / cancel are refused outright
# ════════════════════════════════════════════════════════════════════════

class TestIbkrTransportRefusals:

    def test_connect_never_reaches_the_gateway(self):
        ib = RecordingIb()
        transport = IBKRBrokerTransport(host="127.0.0.1", port=4002, client_id=3, ib=ib)
        with pytest.raises(Exception) as excinfo:
            transport.connect()
        assert _is_containment_refusal(excinfo.value)
        assert _broker_calls(ib) == {"connect": 0, "placeOrder": 0, "cancelOrder": 0}

    def test_direct_submit_is_refused_even_with_a_connected_gateway_stub(self):
        ib = RecordingIb(connected=True)
        transport = IBKRBrokerTransport(ib=ib)
        with pytest.raises(Exception) as excinfo:
            transport.submit_order("US:AAPL", "BUY", 10)
        assert _is_containment_refusal(excinfo.value)
        assert len(ib.place_orders) == 0

    def test_submit_through_the_ibkr_client_wrapper_is_refused(self):
        """The exact call shape the agent uses (market/limit via IBKRClient)."""
        ib = RecordingIb(connected=True)
        client = IBKRClient(IBKRBrokerTransport(ib=ib))
        with pytest.raises(Exception) as market_exc:
            client.submit_market_order(instrument_id="US:AAPL", side="BUY", quantity=10)
        with pytest.raises(Exception) as limit_exc:
            client.submit_limit_order(
                instrument_id="US:AAPL", side="SELL", quantity=5, limit_price=160.0
            )
        assert _is_containment_refusal(market_exc.value)
        assert _is_containment_refusal(limit_exc.value)
        assert len(ib.place_orders) == 0

    def test_cancel_never_reaches_cancel_order(self):
        ib = RecordingIb(connected=True)
        transport = IBKRBrokerTransport(ib=ib)
        transport._trades["7"] = SimpleNamespace(
            order=SimpleNamespace(orderId="7"),
            orderStatus=SimpleNamespace(status="Submitted", filled=0, totalQuantity=10),
            fills=[],
        )
        with pytest.raises(Exception) as excinfo:
            transport.cancel_order("7")
        assert _is_containment_refusal(excinfo.value)
        assert len(ib.cancel_orders) == 0

    def test_live_config_and_ports_do_not_relax_the_refusal(self, monkeypatch):
        """TRADING_MODE/ALLOW_LIVE_TRADING are not consulted by the guard, and a
        paper-looking port is never treated as proof of an account environment."""
        monkeypatch.setenv("TRADING_MODE", "live")
        monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")
        for port in (4001, 4002, 7496, 7497):
            ib = RecordingIb(connected=True)
            config = AgentConfig(
                alethra_api_key="containment-test-key",
                alethra_api_url="http://127.0.0.1:0",
                ib_gateway_port=port,
                trading_mode="live",
                allow_live=True,
            )
            transport = IBKRBrokerTransport(
                host=config.ib_gateway_host, port=config.ib_gateway_port,
                client_id=config.ib_client_id, ib=ib,
            )
            with pytest.raises(Exception) as connect_exc:
                transport.connect()
            with pytest.raises(Exception) as submit_exc:
                transport.submit_order("US:AAPL", "BUY", 10)
            assert _is_containment_refusal(connect_exc.value)
            assert _is_containment_refusal(submit_exc.value)
            assert _broker_calls(ib) == {
                "connect": 0, "placeOrder": 0, "cancelOrder": 0
            }, f"port {port}"

    def test_refusal_message_is_diagnosable_and_safe(self):
        ib = RecordingIb(connected=True)
        transport = IBKRBrokerTransport(
            host="sensitive-host.example", port=4001, client_id=9, ib=ib
        )
        secret_marker = "sk_live_SUPER_SENSITIVE_VALUE"
        with pytest.raises(Exception) as excinfo:
            transport.submit_order("US:AAPL", "BUY", 10)
        message = str(excinfo.value)
        assert _is_containment_refusal(excinfo.value)
        assert secret_marker not in message
        assert "sensitive-host.example" not in message
        assert "4001" not in message
        assert "9" not in message.split()  # no client id dumped as a bare token
        assert "approval" in message.lower() or "disabled" in message.lower()

    def test_read_only_adapters_stay_usable(self):
        """Containment blocks side effects, not status/fill reads, so restart
        reconciliation of an already-mapped order keeps working."""
        ib = RecordingIb(connected=True)
        transport = IBKRBrokerTransport(ib=ib)
        assert transport.is_connected() is True
        assert transport.get_recent_orders(utcnow() - timedelta(minutes=1)) == []
        assert transport.get_fills("999") == []
        assert _broker_calls(ib) == {"connect": 0, "placeOrder": 0, "cancelOrder": 0}


# ════════════════════════════════════════════════════════════════════════
# C. Intent isolation: no trusted execution scope ⇒ never touches a broker
# ════════════════════════════════════════════════════════════════════════

class TestIntentScopeIsolation:

    def test_scope_missing_intent_makes_zero_broker_calls(self):
        transport = SpyBrokerTransport()
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()

        agent._process_single_intent(_intent(status="authorized"))

        assert transport.boundary_calls == {
            "connect": 1,  # only the agent's own startup connect in this test
            "submit_order": 0,
            "cancel_order": 0,
            "get_recent_orders": 0,
        }
        alethra.report_order_submitted.assert_not_called()
        alethra.report_fill.assert_not_called()

    def test_scope_missing_intent_never_reaches_place_order_on_the_real_transport(self):
        """Literal boundary evidence: the agent's own submission path against
        IBKRBrokerTransport leaves ``ib.placeOrder`` at zero calls.

        The stub ``ib`` reports itself connected, so the pre-patch code had
        nothing standing between the authorized intent and a real order.
        """
        ib = RecordingIb(connected=True)
        alethra = _alethra_mock([_intent()])
        agent = _agent(IBKRBrokerTransport(host="127.0.0.1", port=4002, ib=ib), alethra)

        agent.process_order_intents(kill_switch_active=False)

        assert _broker_calls(ib) == {"connect": 0, "placeOrder": 0, "cancelOrder": 0}
        alethra.report_order_submitted.assert_not_called()
        alethra.report_fill.assert_not_called()

    @pytest.mark.parametrize("scope", ["paper", "live"])
    def test_real_transport_place_order_stays_zero_for_untrusted_labels(self, scope):
        ib = RecordingIb(connected=True)
        agent = _agent(
            IBKRBrokerTransport(host="127.0.0.1", port=4002, ib=ib),
            _alethra_mock(),
            trading_mode="live",
            allow_live=True,
        )

        agent._process_single_intent(
            _intent(intent_id=f"ct-real-{scope}", execution_scope=scope)
        )

        assert _broker_calls(ib) == {"connect": 0, "placeOrder": 0, "cancelOrder": 0}

    def test_scope_missing_intent_reports_nothing_to_the_platform(self):
        """Refusal is non-terminal: no false submitted/filled, and no rejection
        event written for a policy refusal."""
        transport = SpyBrokerTransport()
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)

        agent._process_single_intent(_intent(status="authorized"))

        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_rejected.assert_not_called()
        alethra.report_order_uncertain.assert_not_called()
        alethra.report_order_cancelled.assert_not_called()
        alethra.report_fill.assert_not_called()
        assert transport.boundary_calls["submit_order"] == 0

    @pytest.mark.parametrize("scope", ["paper", "live", "PAPER", "", " ", "sandbox", None])
    def test_untrusted_or_inferred_scope_values_are_refused(self, scope):
        """Scope is never inferred, and bare 'paper'/'live' labels carry no
        verified account binding, so they are refused as well."""
        transport = SpyBrokerTransport()
        agent = _agent(transport, _alethra_mock())
        agent.connect()

        agent._process_single_intent(
            _intent(intent_id=f"ct-scope-{scope!r}", execution_scope=scope)
        )

        assert transport.boundary_calls["submit_order"] == 0, scope
        assert transport.boundary_calls["get_recent_orders"] == 0, scope

    def test_broker_ref_prefix_is_not_treated_as_scope(self):
        transport = SpyBrokerTransport()
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()

        agent._process_single_intent(
            _intent(status="authorized", broker_ref="OFFLINE-deadbeef", execution_scope="paper")
        )

        assert transport.boundary_calls["submit_order"] == 0
        assert transport.boundary_calls["get_recent_orders"] == 0
        alethra.report_order_submitted.assert_not_called()

    def test_authorized_queue_alone_does_not_submit(self, client, auth_headers):
        """Invariant 3, through the real platform contract: authorize puts the
        intent in the agent poll queue, and that is still not a submission."""
        portfolio_id = _create_portfolio(client, auth_headers)
        authorized = _create_authorize_intent(client, auth_headers, portfolio_id)

        resp = client.get(
            "/api/v1/execution-agent/order-intents", headers=auth_headers
        )
        assert resp.status_code == 200
        polled = resp.json()
        assert [i["intent_id"] for i in polled] == [authorized["intent_id"]]
        assert polled[0]["status"] == "authorized"

        # Parsed exactly the way AlethraAPIClient.poll_order_intents() does.
        dto = OrderIntentDTO(**polled[0])
        assert getattr(dto, "execution_scope", None) is None, (
            "the platform poll response must not claim an execution scope until "
            "a server-side binding exists"
        )

        transport = SpyBrokerTransport()
        agent = _agent(transport, _alethra_mock())
        agent.connect()
        agent._process_single_intent(dto)

        assert transport.boundary_calls["submit_order"] == 0
        assert transport.boundary_calls["get_recent_orders"] == 0

        state = client.get(
            f"/api/v1/execution/intents/{authorized['intent_id']}", headers=auth_headers
        ).json()
        assert state["status"] == "authorized"

    def test_retry_and_restart_do_not_bypass_and_do_not_repeat_events(self, caplog):
        transport = SpyBrokerTransport()
        alethra = _alethra_mock([_intent()])
        agent = _agent(transport, alethra)
        agent.connect()

        for _ in range(3):
            agent.process_order_intents(kill_switch_active=False)

        assert transport.boundary_calls["submit_order"] == 0
        assert alethra.report_order_rejected.call_count == 0
        first_process = [
            r for r in caplog.records if "execution scope" in r.getMessage().lower()
        ]
        assert len(first_process) == 1, (
            "a policy refusal must be diagnosable once per intent per process, "
            "not spammed on every poll"
        )

        # Restart: a fresh agent process sees the same authorized intent again.
        caplog.clear()
        restarted = SpyBrokerTransport()
        restarted_alethra = _alethra_mock([_intent()])
        restarted_agent = _agent(restarted, restarted_alethra)
        restarted_agent.connect()
        restarted_agent.process_order_intents(kill_switch_active=False)

        assert restarted.boundary_calls["submit_order"] == 0
        assert restarted_alethra.report_order_rejected.call_count == 0
        assert len([
            r for r in caplog.records if "execution scope" in r.getMessage().lower()
        ]) == 1

    def test_explicit_fake_scoped_intent_still_executes(self):
        """Containment is a scope gate, not a dead agent: an intent explicitly
        bound to the fake-transport scope still flows end to end."""
        transport = SpyBrokerTransport(fill_mode="immediate", fill_price=150.0)
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()

        agent._process_single_intent(
            _intent(intent_id="ct-scoped-001", execution_scope="fake-transport")
        )

        assert transport.boundary_calls["submit_order"] == 1
        alethra.report_order_submitted.assert_called_once()
        alethra.report_fill.assert_called_once()


# ════════════════════════════════════════════════════════════════════════
# D. Offline HTTP simulation stays intact — and is never also broker-sent
# ════════════════════════════════════════════════════════════════════════

class TestOfflineSimulationPreserved:

    def test_offline_http_submit_still_simulates_exactly_one_fill(
        self, client, auth_headers
    ):
        portfolio_id = _create_portfolio(client, auth_headers)
        authorized = _create_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = authorized["intent_id"]

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "filled"

        fills = client.get(
            f"/api/v1/execution/intents/{intent_id}/fills", headers=auth_headers
        ).json()
        assert len(fills) == 1
        assert sum(f["quantity"] for f in fills) == 10

        # Re-submitting a terminal intent must not create a second fill.
        repeat = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit", headers=auth_headers
        )
        assert repeat.status_code == 400
        fills_after = client.get(
            f"/api/v1/execution/intents/{intent_id}/fills", headers=auth_headers
        ).json()
        assert len(fills_after) == 1
        assert sum(f["quantity"] for f in fills_after) == 10

    def test_one_intent_is_never_both_broker_submitted_and_simulated(
        self, client, auth_headers
    ):
        """Invariant 2: the agent refuses the unscoped intent, then the offline
        path simulates it — one fill, no broker call, no double accounting."""
        portfolio_id = _create_portfolio(client, auth_headers)
        authorized = _create_authorize_intent(client, auth_headers, portfolio_id)
        intent_id = authorized["intent_id"]

        transport = SpyBrokerTransport(fill_mode="immediate", fill_price=999.0)
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()
        agent._process_single_intent(OrderIntentDTO(**{
            "intent_id": intent_id,
            "user_id": str(authorized.get("user_id") or "user-1"),
            "portfolio_id": str(portfolio_id),
            "symbol": "US:AAPL",
            "side": "BUY",
            "quantity": 10,
            "order_type": "MARKET",
            "limit_price": None,
            "status": "authorized",
            "expires_at": authorized["expires_at"],
            "created_at": authorized["created_at"],
            "broker_ref": None,
            "rejection_reason": None,
        }))

        assert transport.boundary_calls["submit_order"] == 0
        alethra.report_order_submitted.assert_not_called()

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "filled"
        # Offline fill price comes from the portfolio's stored price, never from
        # the fake transport's 999.0 fill price.
        assert str(resp.json().get("broker_ref", "")).startswith("OFFLINE-")

        fills = client.get(
            f"/api/v1/execution/intents/{intent_id}/fills", headers=auth_headers
        ).json()
        assert len(fills) == 1
        assert sum(f["quantity"] for f in fills) == 10

        db = TestSession()
        try:
            rows = (
                db.query(FillRecord)
                .join(OrderIntent, FillRecord.order_intent_id == OrderIntent.id)
                .filter(OrderIntent.intent_id == intent_id)
                .all()
            )
            assert len(rows) == 1
            assert sum(r.quantity for r in rows) == 10
            assert all(r.fill_source == FillSource.simulated for r in rows)
        finally:
            db.close()

    def test_kill_switch_control_path_is_unchanged(self):
        """Fail-closed control plane must survive the containment patch."""
        transport = SpyBrokerTransport()
        alethra = _alethra_mock([_intent(execution_scope="fake-transport")])
        agent = _agent(transport, alethra)
        agent.connect()

        agent.process_order_intents(kill_switch_active=True)
        assert transport.boundary_calls["submit_order"] == 0

        alethra.get_control_status.side_effect = RuntimeError("platform unreachable")
        assert agent._check_kill_switch() is True

    def test_expired_intent_keeps_a_single_terminal_rejection(self):
        transport = SpyBrokerTransport()
        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()

        agent._process_single_intent(
            _intent(intent_id="ct-expired-001", expires_in_minutes=-1,
                    execution_scope="fake-transport")
        )

        alethra.report_order_rejected.assert_called_once()
        assert "expired" in alethra.report_order_rejected.call_args.kwargs["reason"].lower()
        assert transport.boundary_calls["submit_order"] == 0


# ════════════════════════════════════════════════════════════════════════
# E. Recovery regressions stay reachable for fake-scoped intents
# ════════════════════════════════════════════════════════════════════════

class TestRecoveryRegressionsPreserved:

    def test_lost_acknowledgement_adopts_without_resubmitting(self):
        transport = SpyBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        # A live order from a previous process, with the acknowledgement lost.
        transport._orders.clear()
        transport._order_counter = 0
        previous = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        previous.connect()
        adopted = previous.submit_order("US:AAPL", "BUY", 10, "MARKET", None)
        for state in previous.get_recent_orders(utcnow() - timedelta(minutes=1)):
            transport._orders[state.order_id] = state

        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()
        before = transport.boundary_calls["submit_order"]

        agent._process_single_intent(
            _intent(intent_id="ct-lostack-001", execution_scope="fake-transport")
        )

        assert transport.boundary_calls["submit_order"] == before
        alethra.report_order_submitted.assert_called_once()
        assert (
            alethra.report_order_submitted.call_args.kwargs["broker_ref"]
            == adopted.broker_order_id
        )

    def test_ambiguous_broker_state_is_uncertain_not_submitted(self):
        transport = SpyBrokerTransport(fill_mode="immediate", fill_price=150.0)
        transport.connect()
        transport._orders.clear()
        previous = FakeBrokerTransport(fill_mode="immediate", fill_price=150.0)
        previous.connect()
        previous.submit_order("US:AAPL", "BUY", 10, "MARKET", None)
        previous.submit_order("US:AAPL", "BUY", 10, "MARKET", None)
        for state in previous.get_recent_orders(utcnow() - timedelta(minutes=1)):
            transport._orders[state.order_id] = state

        alethra = _alethra_mock()
        agent = _agent(transport, alethra)
        agent.connect()
        before = transport.boundary_calls["submit_order"]

        agent._process_single_intent(
            _intent(intent_id="ct-ambiguous-001", execution_scope="fake-transport")
        )

        assert transport.boundary_calls["submit_order"] == before
        alethra.report_order_submitted.assert_not_called()
        alethra.report_order_uncertain.assert_called_once()


# ════════════════════════════════════════════════════════════════════════
# F. Policy constants must not be openable from the environment
# ════════════════════════════════════════════════════════════════════════

class TestPolicyIsNotEnvOpenable:

    def test_no_environment_variable_lifts_the_transport_guard(self, monkeypatch):
        for name, value in (
            ("ALETHRA_BROKER_TRANSPORT", "ibkr"),
            ("TRADING_MODE", "live"),
            ("ALLOW_LIVE_TRADING", "true"),
            ("BROKER_EXECUTION_CONTAINED", "false"),
            ("IBKR_EXECUTION_ENABLED", "true"),
            ("DISABLE_CONTAINMENT", "1"),
        ):
            monkeypatch.setenv(name, value)

        contained = getattr(bt_module, "BROKER_EXECUTION_CONTAINED", False)
        assert contained is True, (
            "the containment flag is still importable-but-False, or an env "
            "variable flipped it"
        )

        ib = RecordingIb(connected=True)
        transport = IBKRBrokerTransport(ib=ib)
        with pytest.raises(Exception) as excinfo:
            transport.submit_order("US:AAPL", "BUY", 10)
        assert _is_containment_refusal(excinfo.value)
        assert len(ib.place_orders) == 0

    def test_operator_guide_states_the_containment_policy(self):
        root = _resolve_repo_root()
        guide = root / _GUIDE_RELATIVE_PATH
        if not guide.is_file():
            pytest.fail(
                f"Operator guide missing at {guide} (repository root resolved to "
                f"{root}). The runner must expose the repository's docs/ "
                "directory, e.g. mount the repository read-only and set "
                "ALETHRA_REPO_ROOT to the mount point.",
                pytrace=False,
            )
        text = guide.read_text(encoding="utf-8").lower()
        assert "containment" in text
        assert "separate approval" in text, (
            "the guide must say plainly that broker execution needs its own "
            "approval, not just that it is configured"
        )
        assert "alethra_broker_transport" in text
        assert "no longer defaults" in text or "no default" in text, (
            "the guide must correct the old implicit ibkr startup default"
        )
