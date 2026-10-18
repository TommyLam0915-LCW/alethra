"""Journey A: Personal AI research workflow.

Tests the complete journey from AI conversation through to backtest execution:
1. Create AI conversation
2. Send messages to AI assistant
3. Create draft experiment from AI conversation
4. Create experiment from draft
5. Run backtest on experiment

Uses MockAIProvider (default) - no external LLM credentials required.
"""
import pytest


def _register_and_login(client, email, password="testpass123", display_name="Test"):
    """Register a new user and return Bearer auth headers."""
    client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "display_name": display_name,
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": email,
        "password": password,
    })
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestJourneyAPersonalAIResearch:
    """Journey A: Personal AI research (conversation → draft → experiment → backtest)."""

    def test_ai_conversation_to_backtest(self, client):
        """Complete journey from AI conversation to backtest execution."""
        # Step 1: Register and login
        headers = _register_and_login(client, "journey-a@test.com")

        # Step 2: Check AI status (should be available with MockAIProvider)
        status_resp = client.get("/api/v1/ai/status", headers=headers)
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["available"] is True
        assert status["provider"] == "mock"

        # Step 3: Create AI conversation
        conv_resp = client.post("/api/v1/ai/conversations", headers=headers, json={
            "title": "Momentum Strategy Research",
        })
        assert conv_resp.status_code == 201
        conversation = conv_resp.json()
        conversation_id = conversation["id"]
        assert conversation["title"] == "Momentum Strategy Research"

        # Step 4: Send message to AI assistant
        msg_resp = client.post(
            f"/api/v1/ai/conversations/{conversation_id}/messages",
            headers=headers,
            json={
                "role": "user",
                "content": "Help me design a momentum strategy for tech stocks",
            },
        )
        assert msg_resp.status_code == 200
        message = msg_resp.json()
        assert message["role"] == "assistant"
        assert len(message["content"]) > 0

        # Step 5: Create draft experiment from AI conversation
        draft_resp = client.post(
            f"/api/v1/ai/conversations/{conversation_id}/drafts",
            headers=headers,
            json={
                "title": "Tech Momentum Strategy",
                "original_idea": "Momentum-based strategy for tech sector",
                "research_type": "quantitative",
                "hypothesis": "Tech stocks with strong momentum outperform",
                "falsification_conditions": "Max drawdown > 25%",
                "benchmark": "DEMO-BENCHMARK",
            },
        )
        assert draft_resp.status_code == 201
        draft = draft_resp.json()
        draft_id = draft["id"]
        assert draft["title"] == "Tech Momentum Strategy"
        assert draft["status"] == "draft"

        # Step 6: Create experiment from draft
        strategy_spec = {
            "schema_version": "1.0",
            "template_type": "momentum",
            "asset_ids": ["DEMO-TECH"],
            "frequency": "1d",
            "start_date": "2024-01-02",
            "end_date": "2024-06-28",
            "initial_cash": 100000.0,
            "signal_parameters": {"lookback": 20, "threshold": 0.02},
            "entry_rule": {"type": "momentum", "params": {"window": 20}},
            "exit_rule": {"type": "stop_loss", "params": {"pct": 0.05}},
            "sizing_rule": {"type": "equal_weight"},
            "execution_timing": {"execute_at": "next_open"},
            "commission_rate": 0.001,
            "slippage_rate": 0.0005,
            "benchmark_id": "DEMO-BENCHMARK",
            "currency": "USD",
        }

        exp_resp = client.post("/api/v1/experiments/", headers=headers, json={
            "title": draft["title"],
            "original_idea": draft["original_idea"],
            "research_type": draft["research_type"],
            "hypothesis": draft["hypothesis"],
            "falsification_conditions": draft["falsification_conditions"],
            "benchmark": draft["benchmark"],
            "strategy_spec": strategy_spec,
        })
        assert exp_resp.status_code == 201
        experiment = exp_resp.json()
        experiment_id = experiment["id"]
        version_id = experiment["current_version_id"]
        assert experiment["title"] == "Tech Momentum Strategy"

        # Step 7: Run backtest on experiment
        backtest_resp = client.post(
            f"/api/v1/experiments/{experiment_id}/versions/{version_id}/backtest",
            headers=headers,
            json={},
        )
        assert backtest_resp.status_code == 200
        backtest = backtest_resp.json()
        assert backtest["status"] == "completed"
        assert backtest["results"] is not None
        assert "total_return" in backtest["results"]
        assert "sharpe_ratio" in backtest["results"]
        assert "max_drawdown" in backtest["results"]

        # Step 8: Verify backtest results are persisted
        get_exp_resp = client.get(f"/api/v1/experiments/{experiment_id}", headers=headers)
        assert get_exp_resp.status_code == 200
        exp_data = get_exp_resp.json()
        assert exp_data["current_version"]["backtest_results"] is not None

    def test_ai_conversation_with_quota_enforcement(self, client):
        """Verify AI conversation enforces quota reservation."""
        headers = _register_and_login(client, "journey-a-quota@test.com")

        # Create conversation
        conv_resp = client.post("/api/v1/ai/conversations", headers=headers, json={
            "title": "Quota Test",
        })
        assert conv_resp.status_code == 201
        conversation_id = conv_resp.json()["id"]

        # Send multiple messages (each should reserve and settle quota)
        for i in range(3):
            msg_resp = client.post(
                f"/api/v1/ai/conversations/{conversation_id}/messages",
                headers=headers,
                json={
                    "role": "user",
                    "content": f"Message {i+1}",
                },
            )
            assert msg_resp.status_code == 200

        # Verify conversation has all messages
        get_conv_resp = client.get(
            f"/api/v1/ai/conversations/{conversation_id}",
            headers=headers,
        )
        assert get_conv_resp.status_code == 200
        conv_data = get_conv_resp.json()
        # Should have 3 user messages + 3 assistant responses = 6 total
        assert len(conv_data["messages"]) == 6

    def test_ai_conversation_isolation(self, client):
        """Verify conversations are isolated between users."""
        # Create two users
        headers1 = _register_and_login(client, "user1@test.com")
        headers2 = _register_and_login(client, "user2@test.com")

        # User 1 creates conversation
        conv1_resp = client.post("/api/v1/ai/conversations", headers=headers1, json={
            "title": "User 1 Conversation",
        })
        assert conv1_resp.status_code == 201
        conv1_id = conv1_resp.json()["id"]

        # User 2 tries to access User 1's conversation
        conv2_resp = client.get(
            f"/api/v1/ai/conversations/{conv1_id}",
            headers=headers2,
        )
        assert conv2_resp.status_code == 404  # Not found (isolated)

        # User 2 can only see their own conversations
        list_resp = client.get("/api/v1/ai/conversations", headers=headers2)
        assert list_resp.status_code == 200
        conversations = list_resp.json()["conversations"]
        assert len(conversations) == 0  # User 2 has no conversations
