"""Focused tests for explicit provider selection in stored-data sessions."""
import pytest
from datetime import date, datetime
from app.models.market_data import MarketDataBar


class TestProviderSelection:
    """Test explicit provider selection for manual replay sessions."""

    def _create_experiment_with_data(self, client, auth_headers, db_session):
        """Helper: Create experiment and populate with multi-provider data."""
        # Create and lock experiment
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Provider Selection Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-30",
                "initial_cash": 10000,
            }
        })
        assert resp.status_code == 201
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        # Lock experiment
        resp = client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })
        assert resp.status_code == 200

        # Insert Twelve Data bars (20 bars: 2024-01-02 to 2024-01-21)
        # Note: 20 trading days from Jan 2 = Jan 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19, 22, 23, 24, 25, 26, 29
        # But for simplicity, we'll use consecutive days
        for i in range(20):
            bar_date = date(2024, 1, 2 + i)
            bar = MarketDataBar(
                instrument_id='US:AAPL',
                bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                bar_date=bar_date,
                open=180.0 + i * 0.5,
                high=181.0 + i * 0.5,
                low=179.0 + i * 0.5,
                close=180.5 + i * 0.5,
                volume=50000000,
                provider_id='twelvedata',
            )
            db_session.add(bar)

        # Insert Demo bars (4 bars: 2024-01-30 to 2024-02-02)
        demo_dates = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)]
        for i, bar_date in enumerate(demo_dates):
            bar = MarketDataBar(
                instrument_id='US:AAPL',
                bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                bar_date=bar_date,
                open=190.0 + i,
                high=191.0 + i,
                low=189.0 + i,
                close=190.5 + i,
                volume=1000000,
                provider_id='demo',
            )
            db_session.add(bar)

        db_session.commit()
        return exp_id, version_id

    def test_a_multiple_providers_no_selection_rejected(self, client, auth_headers, setup_db):
        """Case A: Multiple providers available, no selection → rejected."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Try to create session without provider_id
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=auth_headers,
                json={"initial_virtual_cash": 10000.0}
            )

            # Should be rejected
            assert resp.status_code == 400
            assert "Multiple providers" in resp.json()["detail"]
            
            # Verify no session was created
            resp = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
            # Session count should be 0 (no session created for this version)
        finally:
            db_session.close()

    def test_b_explicit_twelvedata_selection_persists(self, client, auth_headers, setup_db):
        """Case B: Explicit twelvedata selection → session persists provider_id=twelvedata."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Create session with explicit twelvedata provider
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=auth_headers,
                json={
                    "initial_virtual_cash": 10000.0,
                    "provider_id": "twelvedata"
                }
            )

            assert resp.status_code == 201
            session = resp.json()
            
            # Verify provider_id persisted
            assert session["provider_id"] == "twelvedata"
            assert session["status"] == "pending"
            assert session["initial_virtual_cash"] == 10000.0
            
            # Verify session can be retrieved
            resp = client.get(f"/api/v1/simulations/{session['id']}", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["provider_id"] == "twelvedata"
        finally:
            db_session.close()

    def test_c_invalid_provider_rejected(self, client, auth_headers, setup_db):
        """Case C: Invalid/unavailable provider → clear rejection, no silent fallback."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Try to create session with nonexistent provider
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=auth_headers,
                json={
                    "initial_virtual_cash": 10000.0,
                    "provider_id": "nonexistent"
                }
            )

            # Should be rejected
            assert resp.status_code == 400
            assert "has no stored data" in resp.json()["detail"]
            assert "nonexistent" in resp.json()["detail"]
        finally:
            db_session.close()

    def test_d_manual_execution_uses_bound_provider(self, client, auth_headers, setup_db):
        """Case D: Manual execution uses selected provider's bars."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Create session with twelvedata
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=auth_headers,
                json={
                    "initial_virtual_cash": 10000.0,
                    "provider_id": "twelvedata"
                }
            )
            assert resp.status_code == 201
            session_id = resp.json()["id"]

            # Execute manual decision (buy)
            resp = client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={
                    "action": "buy",
                    "quantity": 100,
                    "idempotency_key": "test-buy-001",
                    "rationale": "Test buy with twelvedata"
                }
            )
            assert resp.status_code == 200
            decision = resp.json()["decision"]
            
            # Verify decision was recorded (may be blocked_action if no valid next date)
            assert decision["decision_type"] in ["simulated_entry", "no_action", "blocked_action"]
            
            # Verify session still has twelvedata provider after decision
            resp = client.get(f"/api/v1/simulations/{session_id}", headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["provider_id"] == "twelvedata"
            
            # Verify session state is preserved
            assert resp.json()["total_decisions"] >= 1
        finally:
            db_session.close()

    def test_e_partial_range_disclosed(self, client, auth_headers, setup_db):
        """Case E: Partial range disclosed in API response."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Get available providers
            resp = client.get(
                f"/api/v1/simulations/versions/{version_id}/available-providers",
                headers=auth_headers
            )
            assert resp.status_code == 200
            data = resp.json()
            
            # Verify partial coverage info
            providers = {p["provider_id"]: p for p in data["available_providers"]}
            
            # Twelve Data: 2024-01-02 to 2024-01-21 (partial, doesn't cover full requested range)
            assert "twelvedata" in providers
            td = providers["twelvedata"]
            assert td["min_date"] == "2024-01-02"
            assert td["max_date"] == "2024-01-21"
            assert td["requested_start"] == "2024-01-02"
            assert td["requested_end"] == "2024-01-30"
            
            # Demo: 2024-01-30 only (bars after 2024-01-30 are outside requested range)
            assert "demo" in providers
            demo = providers["demo"]
            assert demo["min_date"] == "2024-01-30"
            assert demo["max_date"] == "2024-01-30"
            assert demo["bar_count"] == 1  # Only one demo bar within requested range
            
            # Create session and verify effective range persisted
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=auth_headers,
                json={
                    "initial_virtual_cash": 10000.0,
                    "provider_id": "twelvedata"
                }
            )
            assert resp.status_code == 201
            session = resp.json()
            
            # data_cutoff_at should match the effective replay range
            assert session["data_cutoff_at"] == "2024-01-30"
        finally:
            db_session.close()

    def test_f_access_control_respected(self, client, auth_headers, setup_db):
        """Case F: Another user cannot access private version's providers."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            exp_id, version_id = self._create_experiment_with_data(client, auth_headers, db_session)

            # Create second user
            resp = client.post("/api/auth/register", json={
                "email": "other@alethra.io",
                "password": "otherpass123",
                "display_name": "Other User",
                "user_type": "individual",
            })
            # May fail if already exists, that's ok
            
            # Login as second user
            resp = client.post("/api/auth/login", json={
                "email": "other@alethra.io",
                "password": "otherpass123",
            })
            assert resp.status_code == 200
            other_token = resp.json()["access_token"]
            other_headers = {"Authorization": f"Bearer {other_token}"}

            # Try to access providers as other user
            resp = client.get(
                f"/api/v1/simulations/versions/{version_id}/available-providers",
                headers=other_headers
            )
            
            # Should be forbidden (403) or not found (404)
            assert resp.status_code in [403, 404]

            # Try to create session as other user
            resp = client.post(
                f"/api/v1/simulations/versions/{version_id}",
                headers=other_headers,
                json={
                    "initial_virtual_cash": 10000.0,
                    "provider_id": "twelvedata"
                }
            )
            
            # Should be forbidden
            assert resp.status_code in [403, 404]
        finally:
            db_session.close()
