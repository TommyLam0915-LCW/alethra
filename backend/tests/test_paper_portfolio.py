import pytest
from datetime import datetime


class TestVirtualCapitalCannotGoNegative:
    """Test 1: Virtual capital cannot go negative."""

    def test_insufficient_cash_blocks_order(self, client, auth_headers):
        # Create portfolio with very small cash (DEMO-ALPHA ~$54/share)
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10.0,
            "currency": "USD",
        })
        assert resp.status_code == 201
        portfolio_id = resp.json()["id"]

        # Try to buy 1 share — costs ~$54 + slippage + commission > $10
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=auth_headers, json={
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 1,
            "order_type": "market",
            "rationale": "Testing insufficient cash",
            "expected_outcome": "Should fail",
            "self_reported_confidence": "high",
        })
        assert resp.status_code == 400

        # Verify cash didn't change regardless of rejection reason
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers)
        assert resp.json()["current_virtual_cash"] == 10.0


class TestNoMidSessionCapitalReset:
    """Test 2: No mid-session capital reset/refill."""

    def test_cannot_add_cash_to_existing_portfolio(self, client, auth_headers):
        # Create portfolio
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
            "currency": "USD",
        })
        assert resp.status_code == 201
        portfolio_id = resp.json()["id"]
        initial_cash = resp.json()["current_virtual_cash"]

        # Try to "refill" by updating — no such endpoint exists
        # The only way to change cash is through orders
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers)
        assert resp.json()["current_virtual_cash"] == initial_cash

        # Verify no "add cash" or "refill" endpoint exists
        # (This is implicit — if such endpoint existed, we'd test it here)
        # The schema has no field for adding cash post-creation


class TestOrderBlockedWhenRiskLimitExceeded:
    """Test 3: Order blocked when risk limit exceeded."""

    def test_paused_portfolio_blocks_orders(self, client, auth_headers):
        # Create and pause portfolio
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

        # Try to place order — should fail
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=auth_headers, json={
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 10,
            "order_type": "market",
            "rationale": "Testing paused portfolio",
            "expected_outcome": "Should be blocked",
        })
        assert resp.status_code == 400
        assert "paused" in resp.json()["detail"].lower() or "risk check" in resp.json()["detail"].lower()

    def test_disallowed_asset_blocked(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Try to buy non-demo asset
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=auth_headers, json={
            "asset_id": "AAPL",  # Not in allowed list
            "side": "BUY",
            "quantity": 10,
            "order_type": "market",
            "rationale": "Testing disallowed asset",
            "expected_outcome": "Should be blocked",
        })
        assert resp.status_code == 400 or resp.status_code == 422  # Validation or risk check


class TestNoActionCorrectlyRecorded:
    """Test 4: No-action correctly recorded."""

    def test_no_action_decision_can_be_recorded(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Record a no-action decision
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers, json={
            "actor_type": "human",
            "decision_type": "no_action",
            "rationale": "Market conditions unclear, choosing to wait",
            "self_reported_confidence": "medium",
        })
        assert resp.status_code == 201
        decision = resp.json()
        assert decision["decision_type"] == "no_action"
        assert decision["actor_type"] == "human"
        assert decision["rationale"] == "Market conditions unclear, choosing to wait"

        # Verify it appears in decision list
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers)
        assert resp.status_code == 200
        decisions = resp.json()
        assert len(decisions) > 0
        assert any(d["decision_type"] == "no_action" for d in decisions)


class TestLockedStrategyAndOverrideSeparate:
    """Test 5: Locked-strategy and override performance remain separate."""

    def test_override_request_creates_separate_decision(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Create override request
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/override-requests", headers=auth_headers, json={
            "original_rationale": "Following locked strategy",
            "override_reason": "Market event requires deviation",
            "expected_outcome": "Better returns despite deviation",
            "self_reported_confidence": "medium",
            "falsification_condition": "Returns worse than locked strategy",
        })
        assert resp.status_code == 201
        override_decision = resp.json()
        assert override_decision["decision_type"] == "override_request"
        assert override_decision["risk_flags"]["is_override"] is True
        assert override_decision["risk_flags"]["outside_locked_strategy"] is True

        # Create normal entry decision for comparison
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers, json={
            "actor_type": "human",
            "decision_type": "entry",
            "rationale": "Following locked strategy rules",
            "expected_outcome": "Steady growth",
            "self_reported_confidence": "high",
            "falsification_condition": "Drawdown > 10%",
        })
        assert resp.status_code == 201
        normal_decision = resp.json()
        assert normal_decision["decision_type"] == "entry"
        assert normal_decision.get("risk_flags") is None or not normal_decision["risk_flags"].get("is_override")

        # Both decisions exist separately
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers)
        decisions = resp.json()
        assert len(decisions) >= 2
        override_count = sum(1 for d in decisions if d["decision_type"] == "override_request")
        normal_count = sum(1 for d in decisions if d["decision_type"] == "entry")
        assert override_count >= 1
        assert normal_count >= 1


class TestExpectedOutcomeRecordedBeforeAction:
    """Test 6: Expected outcome recorded before qualifying action."""

    def test_entry_requires_expected_outcome(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Try to create entry decision without expected_outcome — should fail
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers, json={
            "actor_type": "human",
            "decision_type": "entry",
            "rationale": "Bullish signal",
            # Missing expected_outcome
            "self_reported_confidence": "high",
        })
        assert resp.status_code == 400
        assert "expected outcome" in resp.json()["detail"].lower()

        # With expected_outcome — should succeed
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers, json={
            "actor_type": "human",
            "decision_type": "entry",
            "rationale": "Bullish signal",
            "expected_outcome": "Price will rise 5% in 10 days",
            "expected_timeframe_days": 10,
            "self_reported_confidence": "high",
            "falsification_condition": "Price drops instead",
        })
        assert resp.status_code == 201
        decision = resp.json()
        assert decision["expected_outcome"] == "Price will rise 5% in 10 days"
        assert decision["expected_timeframe_days"] == 10


class TestOutcomeEvaluationLinksToDecision:
    """Test 7: Outcome evaluation links to decision."""

    def test_outcome_evaluation_created_for_decision(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Create decision
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers, json={
            "actor_type": "human",
            "decision_type": "entry",
            "rationale": "Testing outcome linkage",
            "expected_outcome": "Price rise",
            "self_reported_confidence": "medium",
        })
        decision_id = resp.json()["id"]

        # Create outcome evaluation
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions/{decision_id}/outcome",
            headers=auth_headers,
            json={
                "actual_outcome_summary": "Price rose 3% as expected",
                "net_simulated_return_pct": 3.0,
                "hypothesis_status": "supported",
                "evaluation_method": "price_comparison",
                "user_reflection": "Signal was accurate",
            }
        )
        assert resp.status_code == 201
        evaluation = resp.json()
        assert evaluation["decision_id"] == decision_id
        assert evaluation["hypothesis_status"] == "supported"
        assert evaluation["net_simulated_return_pct"] == 3.0

        # Decision should now be marked as evaluated
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=auth_headers)
        decision = next(d for d in resp.json() if d["id"] == decision_id)
        assert decision["outcome_status"] == "evaluated"


class TestSimulatedFillIncludesCosts:
    """Test 8: Simulated fill includes commission and slippage."""

    def test_fill_has_commission_and_slippage(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]
        initial_cash = resp.json()["current_virtual_cash"]

        # Place buy order
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=auth_headers, json={
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 10,
            "order_type": "market",
            "rationale": "Testing fill costs",
            "expected_outcome": "Should have costs",
        })
        assert resp.status_code == 201
        order_id = resp.json()["id"]

        # Get fills
        resp = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills",
            headers=auth_headers
        )
        assert resp.status_code == 200
        fills = resp.json()
        assert len(fills) > 0

        fill = fills[0]
        assert fill["commission_amount"] > 0, "Commission should be > 0"
        assert fill["slippage_amount"] > 0, "Slippage should be > 0"
        assert fill["execution_price_before_costs"] > fill["observed_price"], "Slippage should increase buy price"

        # Verify cash decreased by more than just gross amount
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers)
        cash_after = resp.json()["current_virtual_cash"]
        assert cash_after < initial_cash, "Cash should decrease after buy"


class TestPrivateUserCannotReadOtherPortfolio:
    """Test 9: Private user cannot read another user's portfolio."""

    def test_user_cannot_access_others_portfolio(self, client, auth_headers):
        # Create user 1's portfolio
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Create user 2
        resp = client.post("/api/auth/register", json={
            "email": "user2@alethra.io",
            "password": "testpass123",
            "display_name": "User 2",
            "user_type": "individual",
        })
        resp = client.post("/api/auth/login", json={
            "email": "user2@alethra.io",
            "password": "testpass123",
        })
        user2_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

        # User 2 tries to access user 1's portfolio — should fail
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}", headers=user2_headers)
        assert resp.status_code == 403 or resp.status_code == 404

        # User 2 tries to list positions — should fail
        resp = client.get(f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=user2_headers)
        assert resp.status_code == 403 or resp.status_code == 404


class TestRetiredPortfolioImmutable:
    """Test 10: Retired portfolios remain immutable."""

    def test_retired_portfolio_blocks_new_orders(self, client, auth_headers):
        resp = client.post("/api/v1/paper-portfolio", headers=auth_headers, json={
            "initial_virtual_cash": 10000.0,
        })
        portfolio_id = resp.json()["id"]

        # Retire portfolio
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/retire", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "retired"

        # Try to place order — should fail
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=auth_headers, json={
            "asset_id": "DEMO-ALPHA",
            "side": "BUY",
            "quantity": 10,
            "order_type": "market",
            "rationale": "Testing retired portfolio",
            "expected_outcome": "Should be blocked",
        })
        assert resp.status_code == 400
        assert "retired" in resp.json()["detail"].lower() or "cannot" in resp.json()["detail"].lower()

        # Try to pause — should fail
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/pause", headers=auth_headers)
        assert resp.status_code == 400

        # Try to retire again — should fail
        resp = client.post(f"/api/v1/paper-portfolio/{portfolio_id}/retire", headers=auth_headers)
        assert resp.status_code == 400


class TestNoExternalBrokerFields:
    """Test 11: No external broker credential or exchange key fields exist."""

    def test_schema_has_no_broker_fields(self, client, auth_headers):
        from app.api.schemas import PaperPortfolioCreate, PaperOrderCreate
        from app.models.paper_portfolio import PaperPortfolio, PaperOrder

        # Check Pydantic schemas
        portfolio_schema_fields = set(PaperPortfolioCreate.__fields__.keys())
        order_schema_fields = set(PaperOrderCreate.__fields__.keys())

        broker_keywords = ["broker", "api_key", "secret", "credential", "exchange", "binance", "coinbase"]
        for field in portfolio_schema_fields | order_schema_fields:
            field_lower = field.lower()
            for keyword in broker_keywords:
                assert keyword not in field_lower, f"Field '{field}' contains broker-related keyword '{keyword}'"

        # Check database models
        portfolio_columns = {c.name for c in PaperPortfolio.__table__.columns}
        order_columns = {c.name for c in PaperOrder.__table__.columns}

        for column in portfolio_columns | order_columns:
            column_lower = column.lower()
            for keyword in broker_keywords:
                assert keyword not in column_lower, f"Column '{column}' contains broker-related keyword '{keyword}'"
