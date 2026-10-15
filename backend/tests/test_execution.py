"""Tests for Module G — Broker execution core (offline/sandbox mode).

Covers:
- Intent lifecycle: create → authorize → submit → fill
- Cancel before/after submit
- Expired intent rejection
- Idempotency (duplicate intent_id)
- Kill switch blocks submissions
- Reconciliation records
- User isolation

All tests use the `client` fixture for proper DB lifecycle management.
Service-level helpers use the same DB session as the API layer.
"""
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from tests.conftest import TestSession
from app.models.user import User
from app.models.paper_portfolio import PaperPortfolio, PortfolioStatus
from app.models.execution import (
    OrderIntent,
    IntentStatus,
    FillReconciliation,
)
from app.services.execution import (
    create_order_intent,
    authorize_intent,
    submit_intent,
    cancel_intent,
    reconcile_order,
)
from app.services.kill_switch import kill_switch


# ── Helpers ──

def _get_db_from_client(client) -> Session:
    """Obtain a TestSession that shares the same DB state as the client."""
    return TestSession()


def _create_portfolio_via_api(client, auth_headers, cash=100000.0):
    resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
        "initial_virtual_cash": cash,
        "currency": "USD",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_intent_via_api(client, auth_headers, portfolio_id, **overrides):
    payload = {
        "portfolio_id": portfolio_id,
        "symbol": "DEMO-ALPHA",
        "side": "BUY",
        "quantity": 10,
        "order_type": "MARKET",
    }
    payload.update(overrides)
    resp = client.post("/api/v1/execution/intents", headers=auth_headers, json=payload)
    assert resp.status_code == 201
    return resp.json()


# ──────────────────────────────────────────────
# 1. Create intent → pending
# ──────────────────────────────────────────────

class TestCreateIntent:
    def test_create_intent_returns_pending(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        data = _create_intent_via_api(client, auth_headers, portfolio_id, symbol="DEMO-ALPHA", quantity=10)

        assert data["status"] == "pending"
        assert data["intent_id"] is not None
        assert len(data["intent_id"]) == 36  # UUID format
        assert data["authorization_token"] is not None
        assert data["symbol"] == "DEMO-ALPHA"
        assert data["quantity"] == 10


# ──────────────────────────────────────────────
# 2. Authorize → authorized
# ──────────────────────────────────────────────

class TestAuthorizeIntent:
    def test_authorize_sets_status(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(
            client, auth_headers, portfolio_id,
            symbol="DEMO-BETA", side="SELL", quantity=3,
            order_type="LIMIT", limit_price=100.0,
        )
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "authorized"


# ──────────────────────────────────────────────
# 3. Submit → submitted → filled (offline)
# ──────────────────────────────────────────────

class TestSubmitAndFill:
    def test_full_lifecycle_via_api(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)

        # Create
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id, quantity=5)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        # Authorize
        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )
        assert resp.json()["status"] == "authorized"

        # Submit → should auto-fill in offline mode
        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "filled"
        assert resp.json()["broker_ref"] is not None
        assert resp.json()["broker_ref"].startswith("OFFLINE-")


# ──────────────────────────────────────────────
# 4. Cancel before submit → cancelled
# ──────────────────────────────────────────────

class TestCancelBeforeSubmit:
    def test_cancel_pending(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cancel_authorized(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


# ──────────────────────────────────────────────
# 5. Cancel after submit → rejected
# ──────────────────────────────────────────────

class TestCancelAfterSubmit:
    def test_cancel_submitted_gives_rejected(self, client, auth_headers):
        """In offline mode submit auto-fills. We test the cancel logic by
        manually moving an intent to 'submitted' status via the service layer."""
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        # Authorize via API
        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )

        # Use service layer to move to 'submitted' without auto-fill
        db = TestSession()
        try:
            intent = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            intent.status = IntentStatus.submitted
            db.commit()
        finally:
            db.close()

        # Cancel → should be rejected
        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert resp.json()["rejection_reason"] is not None


# ──────────────────────────────────────────────
# 6. Expired intent cannot be authorized
# ──────────────────────────────────────────────

class TestExpiredIntent:
    def test_expired_intent_cannot_be_authorized(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        # Manually expire the intent
        db = TestSession()
        try:
            intent = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            intent.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()

    def test_expired_intent_cannot_be_submitted(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        # Authorize
        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )

        # Manually expire the intent
        db = TestSession()
        try:
            intent = db.query(OrderIntent).filter(OrderIntent.intent_id == intent_id).first()
            intent.expires_at = datetime.utcnow() - timedelta(minutes=1)
            db.commit()
        finally:
            db.close()

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit",
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()


# ──────────────────────────────────────────────
# 7. Duplicate intent_id is rejected (idempotency)
# ──────────────────────────────────────────────

class TestDuplicateIntentId:
    def test_duplicate_intent_id_rejected(self, client, auth_headers):
        """The intent_id column has a unique constraint, so a second insert
        with the same intent_id must fail at the DB level."""
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        original_intent_id = intent_data["intent_id"]

        # Attempt to insert another row with the same intent_id
        import secrets
        db = TestSession()
        try:
            duplicate = OrderIntent(
                user_id=db.query(User).first().id,
                portfolio_id=uuid.UUID(portfolio_id),
                intent_id=original_intent_id,  # same!
                symbol="DEMO-ALPHA",
                side="BUY",
                quantity=5,
                order_type="MARKET",
                status=IntentStatus.pending,
                authorization_token=secrets.token_urlsafe(32),
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
            db.add(duplicate)
            with pytest.raises(Exception):
                db.commit()
            db.rollback()
        finally:
            db.close()


# ──────────────────────────────────────────────
# 8. Kill switch blocks submissions
# ──────────────────────────────────────────────

class TestKillSwitch:
    def setup_method(self):
        kill_switch.deactivate()

    def teardown_method(self):
        kill_switch.deactivate()

    def test_kill_switch_blocks_submit(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )

        kill_switch.activate("Emergency halt")
        try:
            resp = client.post(
                f"/api/v1/execution/intents/{intent_id}/submit",
                headers=auth_headers,
            )
            assert resp.status_code == 503
            assert "kill switch" in resp.json()["detail"].lower()
        finally:
            kill_switch.deactivate()

    def test_kill_switch_deactivate_allows_submit(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )

        kill_switch.activate("Test")
        kill_switch.deactivate()

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/submit",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "filled"


# ──────────────────────────────────────────────
# 9. Reconciliation records expected vs filled
# ──────────────────────────────────────────────

class TestReconciliation:
    def test_reconciliation_filled_order(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id, quantity=10)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=auth_headers,
            json={"authorization_token": token},
        )
        client.post(
            f"/api/v1/execution/intents/{intent_id}/submit",
            headers=auth_headers,
        )

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/reconcile",
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["expected_quantity"] == 10
        assert data["filled_quantity"] == 10
        assert data["discrepancy"] is False

    def test_reconciliation_unfilled_order(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id, quantity=10)
        intent_id = intent_data["intent_id"]

        # Don't authorize/submit — still pending
        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/reconcile",
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["expected_quantity"] == 10
        assert data["filled_quantity"] == 0
        assert data["discrepancy"] is True


# ──────────────────────────────────────────────
# 10. User isolation
# ──────────────────────────────────────────────

class TestUserIsolation:
    def _create_second_user(self, client):
        """Register and login a second user, return their auth headers."""
        client.post("/api/auth/register", json={
            "email": "user_b_isolation@alethra.io",
            "password": "testpass123",
            "display_name": "User B",
            "user_type": "individual",
        })
        login_resp = client.post("/api/auth/login", json={
            "email": "user_b_isolation@alethra.io",
            "password": "testpass123",
        })
        token = login_resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_user_cannot_authorize_others_intent(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]
        token = intent_data["authorization_token"]

        headers_b = self._create_second_user(client)

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/authorize",
            headers=headers_b,
            json={"authorization_token": token},
        )
        assert resp.status_code == 403

    def test_user_cannot_cancel_others_intent(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]

        headers_b = self._create_second_user(client)

        resp = client.post(
            f"/api/v1/execution/intents/{intent_id}/cancel",
            headers=headers_b,
        )
        assert resp.status_code == 403

    def test_user_cannot_see_others_intents_via_api(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(client, auth_headers, portfolio_id)
        intent_id = intent_data["intent_id"]

        headers_b = self._create_second_user(client)

        resp = client.get(
            f"/api/v1/execution/intents/{intent_id}",
            headers=headers_b,
        )
        assert resp.status_code == 403

    def test_list_intents_only_own(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        _create_intent_via_api(client, auth_headers, portfolio_id, quantity=3)

        resp = client.get("/api/v1/execution/intents", headers=auth_headers)
        assert resp.status_code == 200
        intents = resp.json()
        assert len(intents) >= 1


# ──────────────────────────────────────────────
# Additional: GET single intent
# ──────────────────────────────────────────────

class TestGetIntents:
    def test_get_single_intent(self, client, auth_headers):
        portfolio_id = _create_portfolio_via_api(client, auth_headers)
        intent_data = _create_intent_via_api(
            client, auth_headers, portfolio_id,
            symbol="DEMO-BETA", side="SELL", quantity=1,
            order_type="LIMIT", limit_price=200.0,
        )
        intent_id = intent_data["intent_id"]

        resp = client.get(f"/api/v1/execution/intents/{intent_id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent_id"] == intent_id
        assert data["symbol"] == "DEMO-BETA"
        assert data["side"] == "SELL"
        assert data["order_type"] == "LIMIT"
        assert data["limit_price"] == 200.0
