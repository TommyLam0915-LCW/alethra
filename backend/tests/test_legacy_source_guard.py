"""Test legacy source guard - unconfirmed sessions cannot execute."""
import pytest
from datetime import date, datetime
from uuid import UUID
from app.models.market_data import MarketDataBar
from app.models.simulation import SimulationSession, SimulationStatus


class TestLegacySourceGuard:
    """Test that unconfirmed sessions cannot execute manual decisions."""

    def _create_unconfirmed_session(self, client, auth_headers, db_session):
        """Helper: Create a session with provider_confirmed=False (simulating legacy backfill)."""
        # Create and lock experiment
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Legacy Guard Test",
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

        # Insert test data
        for i in range(5):
            bar_date = date(2024, 1, 2 + i)
            bar = MarketDataBar(
                instrument_id='US:AAPL',
                bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                bar_date=bar_date,
                open=180.0 + i,
                high=181.0 + i,
                low=179.0 + i,
                close=180.5 + i,
                volume=50000000,
                provider_id='twelvedata',
            )
            db_session.add(bar)
        db_session.commit()

        # Create session directly in DB with provider_confirmed=False
        session = SimulationSession(
            experiment_version_id=version_id,
            owner_id=auth_headers['_user_id'],
            initial_virtual_cash=10000.0,
            data_cutoff_at='2024-01-30',
            status=SimulationStatus.pending,
            simulation_policy_version='1.0.0',
            provider_id='twelvedata',
            provider_confirmed=False,  # Legacy session - not confirmed
            paper_equity=10000.0,
            total_decisions=0,
            observed_days=0,
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        
        return session.id

    def test_unconfirmed_session_rejects_manual_decision(self, client, auth_headers, setup_db):
        """Unconfirmed session cannot execute manual decisions."""
        from app.database import get_db
        from app.models.user import User
        db_session = next(get_db())
        
        try:
            # Get user from database
            user = db_session.query(User).filter(User.email == "test@alethra.io").first()
            assert user is not None
            user_id = user.id
            
            # Create unconfirmed session
            resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
                "title": "Legacy Guard Test",
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
            version_id = resp.json()["current_version_id"]

            # Lock experiment
            resp = client.put(f"/api/v1/experiments/{resp.json()['id']}", headers=auth_headers, json={
                "scope": {"status": "locked"}
            })
            assert resp.status_code == 200

            # Insert test data
            for i in range(5):
                bar_date = date(2024, 1, 2 + i)
                bar = MarketDataBar(
                    instrument_id='US:AAPL',
                    bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                    bar_date=bar_date,
                    open=180.0 + i,
                    high=181.0 + i,
                    low=179.0 + i,
                    close=180.5 + i,
                    volume=50000000,
                    provider_id='twelvedata',
                )
                db_session.add(bar)
            db_session.commit()

            # Create session directly in DB with provider_confirmed=False
            session = SimulationSession(
                experiment_version_id=UUID(version_id),
                owner_id=user_id,
                initial_virtual_cash=10000.0,
                data_cutoff_at='2024-01-30',
                status=SimulationStatus.pending,
                simulation_policy_version='1.0.0',
                provider_id='twelvedata',
                provider_confirmed=False,  # Legacy session - not confirmed
                paper_equity=10000.0,
                total_decisions=0,
                observed_days=0,
            )
            db_session.add(session)
            db_session.commit()
            db_session.refresh(session)
            
            session_id = session.id

            # Try to execute manual decision
            resp = client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={
                    "action": "buy",
                    "quantity": 100,
                    "idempotency_key": "test-unconfirmed-001",
                    "rationale": "Test unconfirmed session"
                }
            )

            # Should be rejected
            assert resp.status_code == 400
            assert "provider not confirmed" in resp.json()["detail"].lower()
        finally:
            db_session.close()

    def test_confirmed_session_allows_manual_decision(self, client, auth_headers, setup_db):
        """Confirmed session can execute manual decisions."""
        from app.database import get_db
        db_session = next(get_db())
        
        try:
            # Create and lock experiment
            resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
                "title": "Confirmed Session Test",
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
            version_id = resp.json()["current_version_id"]

            # Lock experiment
            resp = client.put(f"/api/v1/experiments/{resp.json()['id']}", headers=auth_headers, json={
                "scope": {"status": "locked"}
            })
            assert resp.status_code == 200

            # Insert test data
            for i in range(5):
                bar_date = date(2024, 1, 2 + i)
                bar = MarketDataBar(
                    instrument_id='US:AAPL',
                    bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                    bar_date=bar_date,
                    open=180.0 + i,
                    high=181.0 + i,
                    low=179.0 + i,
                    close=180.5 + i,
                    volume=50000000,
                    provider_id='twelvedata',
                )
                db_session.add(bar)
            db_session.commit()

            # Create session with explicit provider (confirmed)
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
            
            # Verify provider_confirmed is True
            assert resp.json()["provider_confirmed"] is True

            # Execute manual decision
            resp = client.post(
                f"/api/v1/simulations/{session_id}/manual-decision",
                headers=auth_headers,
                json={
                    "action": "buy",
                    "quantity": 100,
                    "idempotency_key": "test-confirmed-001",
                    "rationale": "Test confirmed session"
                }
            )

            # Should succeed
            assert resp.status_code == 200
        finally:
            db_session.close()
