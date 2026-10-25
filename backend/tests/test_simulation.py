import pytest
from datetime import date, datetime

from tests.conftest import TestSession


_DEMO_ALPHA_TRADING_DAYS = [
    date(2024, 1, d) for d in (
        2, 3, 4, 5,
        8, 9, 10, 11, 12,
        15, 16, 17, 18, 19,
        22, 23, 24, 25, 26,
        29, 30, 31,
    )
] + [
    date(2024, 2, d) for d in (1, 2, 5, 6, 7, 8, 9, 12, 13)
]


@pytest.fixture(autouse=True)
def _seed_demo_market_data(setup_db):
    """Insert deterministic synthetic MarketDataBar rows for DEMO-ALPHA.

    Simulation session creation queries MarketDataBar and 400s if no stored
    bars exist in the version range. Advance steps then walk this series.
    Values are derived from a fixed linear ramp — no live provider calls.
    """
    from app.models.market_data import MarketDataBar

    db = TestSession()
    try:
        for i, d in enumerate(_DEMO_ALPHA_TRADING_DAYS):
            price = 50.0 + float(i % 20)  # deterministic cyclic ramp
            db.add(MarketDataBar(
                instrument_id="DEMO-ALPHA",
                provider_id="demo",
                bar_date=d,
                bar_timestamp=datetime.combine(d, datetime.min.time()),
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.5,
                volume=1_000_000,
            ))
        db.commit()
    finally:
        db.close()
    yield


class TestLockImmutability:
    """Test 1: Locked version's strategy_spec cannot change."""

    def test_locked_version_spec_immutable(self, client, auth_headers):
        # Create experiment
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Lock Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
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
        assert resp.json()["status"] == "locked"

        # Try to modify strategy_spec — should fail
        resp = client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-BETA"],
                "start_date": "2024-01-02",
                "end_date": "2024-02-13",
                "initial_cash": 20000,
            },
            "change_reason": "testing"
        })
        assert resp.status_code == 400
        assert "locked" in resp.json()["detail"].lower()


class TestVersionCreationAfterLock:
    """Test 2: Cannot create new version after lock."""

    def test_no_new_version_after_lock(self, client, auth_headers):
        # Create and lock
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Version Lock Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        # Try to update — should fail since experiment is locked
        resp = client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "strategy_spec": {
                "template_type": "sma_crossover",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
                "signal_parameters": {"short_window": 3, "long_window": 7},
            },
            "change_reason": "trying to create new version"
        })
        assert resp.status_code == 400


class TestCutoffEnforcement:
    """Test 3: Simulation cannot see data beyond cutoff."""

    def test_simulation_respects_cutoff(self, client, auth_headers):
        # Create experiment with end_date as cutoff
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Cutoff Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-15",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        # Lock
        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        # Create simulation
        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        assert resp.status_code == 201
        session_id = resp.json()["id"]
        assert resp.json()["data_cutoff_at"] == "2024-01-15"

        # Advance until completion
        completed = False
        for _ in range(20):
            resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
            if resp.status_code == 400 and "complete" in resp.json()["detail"].lower():
                completed = True
                break

        assert completed, "Simulation should complete at cutoff"

        # Verify no decisions beyond cutoff
        resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers)
        assert resp.status_code == 200
        decisions = resp.json()
        for d in decisions:
            assert d["snapshot_date"] <= "2024-01-15", f"Decision {d['step_number']} exceeds cutoff"


class TestDeterministicSnapshotRelease:
    """Test 4: Snapshots released in chronological order."""

    def test_snapshots_in_order(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Order Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance and collect dates
        dates = []
        for _ in range(10):
            resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
            if resp.status_code == 400:
                break
            session = resp.json()["session"]
            if session["current_snapshot_date"]:
                dates.append(session["current_snapshot_date"])

        # Verify chronological order
        for i in range(1, len(dates)):
            assert dates[i] > dates[i-1], f"Date {dates[i]} not after {dates[i-1]}"


class TestNoActionStorage:
    """Test 5: No-action decisions are stored."""

    def test_no_action_stored(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "No Action Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "sma_crossover",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-15",
                "initial_cash": 10000,
                "signal_parameters": {"short_window": 3, "long_window": 7},
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance a few steps
        for _ in range(5):
            client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)

        # Check decisions
        resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers)
        decisions = resp.json()
        assert len(decisions) > 0, "Should have decisions"

        # Verify no_action decisions exist
        no_actions = [d for d in decisions if d["decision_type"] == "no_action"]
        assert len(no_actions) > 0, "Should have no_action decisions"

        # Verify no_action has required fields
        for na in no_actions:
            assert na["step_number"] > 0
            assert na["snapshot_date"]
            assert na["paper_equity"] >= 0
            assert na["engine_version"]


class TestIdempotentRelease:
    """Test 6: Releasing same snapshot twice is safe."""

    def test_idempotent_advance(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Idempotent Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance once
        resp1 = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
        assert resp1.status_code == 200
        date1 = resp1.json()["session"]["current_snapshot_date"]
        decisions1 = resp1.json()["session"]["total_decisions"]

        # Try to advance again — should move to next date, not duplicate
        resp2 = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
        assert resp2.status_code == 200
        date2 = resp2.json()["session"]["current_snapshot_date"]
        decisions2 = resp2.json()["session"]["total_decisions"]

        assert date2 > date1, "Should advance to next date"
        assert decisions2 == decisions1 + 1, "Should have one more decision"


class TestMissingPriceWarnings:
    """Test 7: Warnings when price data is missing."""

    def test_warmup_warnings(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Warning Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "sma_crossover",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
                "signal_parameters": {"short_window": 3, "long_window": 7},
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance and check for warmup warnings
        resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
        assert resp.status_code == 200
        decision = resp.json()["decision"]

        # Should have warning about warmup
        assert decision.get("warnings") is not None or decision["decision_type"] == "no_action"


class TestPrivacy:
    """Test 8: Simulation only accessible by owner."""

    def test_simulation_privacy(self, client, auth_headers):
        # Create first user's experiment
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Private Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Create second user
        client.post("/api/auth/register", json={
            "email": "other@alethra.io",
            "password": "otherpass123",
            "display_name": "Other User",
            "user_type": "individual",
        })
        other_login = client.post("/api/auth/login", json={
            "email": "other@alethra.io",
            "password": "otherpass123",
        })
        other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

        # Try to access first user's simulation — should fail
        resp = client.get(f"/api/v1/simulations/{session_id}", headers=other_headers)
        assert resp.status_code == 403 or resp.status_code == 404


class TestRetiredHistory:
    """Test 9: Retired sessions preserve decision history."""

    def test_retired_preserves_history(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Retire Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance a few times
        for _ in range(5):
            client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)

        # Get decisions before retire
        resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers)
        decisions_before = resp.json()
        assert len(decisions_before) > 0

        # Retire
        resp = client.post(f"/api/v1/simulations/{session_id}/retire", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "retired"

        # Verify decisions still accessible
        resp = client.get(f"/api/v1/simulations/{session_id}/decisions", headers=auth_headers)
        assert resp.status_code == 200
        decisions_after = resp.json()
        assert len(decisions_after) == len(decisions_before)


class TestDecisionMetadata:
    """Test 10: Decisions contain required metadata."""

    def test_decision_metadata(self, client, auth_headers):
        resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "Metadata Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 10000,
            }
        })
        exp_id = resp.json()["id"]
        version_id = resp.json()["current_version_id"]

        client.put(f"/api/v1/experiments/{exp_id}", headers=auth_headers, json={
            "scope": {"status": "locked"}
        })

        resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=auth_headers, json={
            "initial_virtual_cash": 10000
        })
        session_id = resp.json()["id"]

        # Advance
        resp = client.post(f"/api/v1/simulations/{session_id}/advance", headers=auth_headers)
        assert resp.status_code == 200
        decision = resp.json()["decision"]

        # Verify required metadata fields
        assert "id" in decision
        assert "step_number" in decision
        assert decision["step_number"] == 1
        assert "snapshot_date" in decision
        assert "decision_type" in decision
        assert "paper_cash" in decision
        assert "paper_equity" in decision
        assert "engine_version" in decision
        assert "data_cutoff_at" in decision
        assert "created_at" in decision
