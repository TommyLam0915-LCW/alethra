"""
Manual replay workflow integration tests.
Tests the complete flow: provider selection -> session creation -> BUY/HOLD/SELL -> persistence.
"""
import pytest
import requests
from datetime import date


BASE = "http://127.0.0.1:8000"


@pytest.fixture
def auth_headers():
    """Login and get auth headers."""
    resp = requests.post(f"{BASE}/api/auth/login", json={
        "email": "owner@test.com",
        "password": "testpass123"
    })
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def experiment_version_id():
    """Get the AAPL experiment version ID."""
    return "2a16a9b6-fa2d-430a-a75a-3920a5b84aca"


class TestProviderSelection:
    """Test 1: Explicit provider selection."""

    def test_available_providers_shows_stored_data(self, auth_headers, experiment_version_id):
        """Available providers endpoint returns stored data, not global config."""
        resp = requests.get(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}/available-providers",
            headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        
        assert data["symbol"] == "US:AAPL"
        assert data["requested_start"] == "2024-01-01"
        assert data["requested_end"] == "2024-01-31"
        
        providers = data["available_providers"]
        assert len(providers) >= 1
        
        # twelvedata should be available
        twelvedata = next((p for p in providers if p["provider_id"] == "twelvedata"), None)
        assert twelvedata is not None
        assert twelvedata["bar_count"] == 20
        assert twelvedata["min_date"] == "2024-01-02"
        assert twelvedata["max_date"] == "2024-01-30"

    def test_session_creation_with_explicit_provider(self, auth_headers, experiment_version_id):
        """Create session with explicit twelvedata provider."""
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        assert resp.status_code in (200, 201)
        session = resp.json()
        
        assert session["provider_id"] == "twelvedata"
        assert session["provider_confirmed"] is True
        assert session["status"] == "pending"


class TestManualDecisions:
    """Test 2: BUY/HOLD/SELL flow with no lookahead."""

    def test_buy_uses_first_available_date(self, auth_headers, experiment_version_id):
        """BUY decision uses first available date from stored data, not spec start."""
        # Create session
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        # Execute BUY
        resp = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Test buy",
                "idempotency_key": f"test-buy-{session_id[:8]}"
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        
        # Should use 2024-01-02 (first twelvedata bar), not 2024-01-01 (spec start)
        assert result["decision"]["snapshot_date"] == "2024-01-02"
        assert result["fill"]["observed_date"] == "2024-01-02"
        assert result["fill"]["execution_date"] == "2024-01-03"
        assert result["fill"]["execution_rule"] == "next_open_no_lookahead"

    def test_hold_creates_no_fill(self, auth_headers, experiment_version_id):
        """HOLD decision advances date but creates no fill."""
        # Create session and BUY first
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Buy first",
                "idempotency_key": f"buy-{session_id[:8]}"
            }
        )
        
        # Execute HOLD
        resp = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "hold",
                "rationale": "Hold position",
                "idempotency_key": f"hold-{session_id[:8]}"
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        
        assert result["decision"]["decision_type"] == "no_action"
        # HOLD returns fill metadata (no execution)
        assert result["fill"]["action"] == "hold"
        assert result["fill"]["execution_rule"] == "no_action_no_execution"
        # Date should advance
        assert result["decision"]["snapshot_date"] > "2024-01-02"

    def test_sell_closes_position(self, auth_headers, experiment_version_id):
        """SELL decision closes position with proper accounting."""
        # Create session, BUY, then SELL
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        # BUY
        buy_resp = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Buy",
                "idempotency_key": f"buy-{session_id[:8]}"
            }
        )
        buy_price = buy_resp.json()["fill"]["price"]
        
        # SELL
        resp = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "sell",
                "quantity": 100,
                "rationale": "Sell",
                "idempotency_key": f"sell-{session_id[:8]}"
            }
        )
        assert resp.status_code == 200
        result = resp.json()
        
        assert result["decision"]["decision_type"] == "simulated_exit"
        assert result["fill"]["action"] == "sell"
        assert result["fill"]["quantity"] == 100
        assert "gross_pnl" in result["fill"]
        assert result["session"]["current_position_json"] is None


class TestIdempotency:
    """Test 3: Idempotency and conflict detection."""

    def test_same_key_same_payload_returns_original(self, auth_headers, experiment_version_id):
        """Same idempotency key with same payload returns original decision."""
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        idem_key = f"idem-{session_id[:8]}"
        
        # First request
        resp1 = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Test",
                "idempotency_key": idem_key
            }
        )
        assert resp1.status_code == 200
        decision1 = resp1.json()["decision"]
        
        # Second request with same key and payload
        resp2 = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Test",
                "idempotency_key": idem_key
            }
        )
        assert resp2.status_code == 200
        decision2 = resp2.json()["decision"]
        
        # Should return same decision
        assert decision1["id"] == decision2["id"]

    def test_same_key_different_payload_conflicts(self, auth_headers, experiment_version_id):
        """Same idempotency key with different payload returns 409 conflict."""
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        idem_key = f"conflict-{session_id[:8]}"
        
        # First request
        requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "First",
                "idempotency_key": idem_key
            }
        )
        
        # Second request with same key but different payload
        resp = requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 200,  # Different quantity
                "rationale": "Second",
                "idempotency_key": idem_key
            }
        )
        assert resp.status_code == 409
        assert "conflict" in resp.json()["detail"].lower()


class TestPersistence:
    """Test 4: Session persistence and reopen."""

    def test_session_preserves_provider_after_reopen(self, auth_headers, experiment_version_id):
        """Session preserves provider_id and provider_confirmed after reopen."""
        # Create session
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        # Reopen session
        resp = requests.get(
            f"{BASE}/api/v1/simulations/{session_id}",
            headers=auth_headers
        )
        assert resp.status_code == 200
        session = resp.json()
        
        assert session["provider_id"] == "twelvedata"
        assert session["provider_confirmed"] is True

    def test_decisions_preserved_after_reopen(self, auth_headers, experiment_version_id):
        """All decisions preserved after session reopen."""
        # Create session and make decisions
        resp = requests.post(
            f"{BASE}/api/v1/simulations/versions/{experiment_version_id}",
            headers=auth_headers,
            json={
                "initial_virtual_cash": 100000.0,
                "provider_id": "twelvedata"
            }
        )
        session_id = resp.json()["id"]
        
        # BUY
        requests.post(
            f"{BASE}/api/v1/simulations/{session_id}/manual-decision",
            headers=auth_headers,
            json={
                "action": "buy",
                "quantity": 100,
                "rationale": "Buy",
                "idempotency_key": f"buy-{session_id[:8]}"
            }
        )
        
        # Reopen and check
        resp = requests.get(
            f"{BASE}/api/v1/simulations/{session_id}",
            headers=auth_headers
        )
        session = resp.json()
        
        assert session["total_decisions"] == 1
        assert session["current_position_json"] is not None
        assert session["current_position_json"]["shares"] == 100


class TestLegacyGuard:
    """Test 5: Legacy session guard."""

    def test_unconfirmed_session_cannot_execute(self, auth_headers):
        """Session with provider_confirmed=False cannot execute decisions."""
        # This test requires creating a session directly in DB with provider_confirmed=False
        # For now, we verify the guard exists in the code
        # Full test would require DB access
        pass
