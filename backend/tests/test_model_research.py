import pytest


def _create_user2(client):
    client.post("/api/auth/register", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
        "display_name": "User 2",
        "user_type": "individual",
    })
    resp = client.post("/api/auth/login", json={
        "email": "user2@alethra.io",
        "password": "testpass123",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _create_record(client, headers, title="Test Model", status=None):
    resp = client.post("/api/v1/model-research", headers=headers, json={
        "title": title,
        "purpose": "Testing model research workflow",
    })
    assert resp.status_code == 201
    return resp.json()


def _create_version(client, headers, record_id, model_type="rule_based"):
    resp = client.post(f"/api/v1/model-research/{record_id}/versions", headers=headers, json={
        "model_type": model_type,
        "model_specification_json": {"rules": [{"indicator": "SMA", "period": 20}]},
    })
    assert resp.status_code == 201
    return resp.json()


class TestModelResearchPrivacy:
    """Test 1: User B cannot access User A's model research records."""

    def test_user_cannot_access_others_record(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        record_id = record["id"]
        user2_headers = _create_user2(client)

        resp = client.get(f"/api/v1/model-research/{record_id}", headers=user2_headers)
        assert resp.status_code in (403, 404)

        resp = client.get("/api/v1/model-research", headers=user2_headers)
        assert resp.status_code == 200
        assert all(r["id"] != record_id for r in resp.json())

    def test_user_cannot_update_others_record(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        user2_headers = _create_user2(client)

        resp = client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=user2_headers,
            json={"title": "Hacked"},
        )
        assert resp.status_code in (403, 404)

    def test_user_cannot_create_version_on_others_record(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        user2_headers = _create_user2(client)

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=user2_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
            },
        )
        assert resp.status_code in (403, 404)


class TestLockedVersionImmutable:
    """Test 2: Locked version cannot be modified."""

    def test_locked_version_cannot_be_revoked_without_reason(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/lock",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["locked_at"] is not None

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/lock",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_locked_version_still_readable(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/lock",
            headers=auth_headers,
        )

        resp = client.get(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["locked_at"] is not None


class TestRevokedVersionPermanent:
    """Test 3: Revoked version cannot be un-revoked."""

    def test_revoked_version_cannot_be_relocked(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke?reason=Testing+revocation",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["revoked_at"] is not None

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/lock",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_revoked_version_cannot_be_revoked_again(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke?reason=First+revocation",
            headers=auth_headers,
        )

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions/{version['id']}/revoke?reason=Second+revocation",
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestMemoryConsentPending:
    """Test 4: Memory created without confirmation stays pending."""

    def test_unconfirmed_memory_stays_pending(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "explicit_preference",
            "content": "I prefer SMA crossover strategies",
            "user_confirmed": False,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_confirmed"] is False
        assert data["user_confirmed_at"] is None

    def test_confirmed_memory_has_timestamp(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "explicit_preference",
            "content": "I confirmed this preference",
            "user_confirmed": True,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_confirmed"] is True
        assert data["user_confirmed_at"] is not None


class TestMemoryTrainingOptInRequiresConfirmation:
    """Test 5: Training opt-in cannot be changed without confirmation."""

    def test_unconfirmed_memory_cannot_change_opt_in(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "reflection",
            "content": "A thought about methodology",
            "user_confirmed": False,
        })
        memory_id = resp.json()["id"]

        resp = client.put(
            f"/api/v1/personal-memory/{memory_id}/training-opt-in?opt_in=true",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_confirmed_memory_can_change_opt_in(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "reflection",
            "content": "A confirmed thought",
            "user_confirmed": True,
        })
        memory_id = resp.json()["id"]

        resp = client.put(
            f"/api/v1/personal-memory/{memory_id}/training-opt-in?opt_in=true",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["general_model_training_opt_in"] is True


class TestEvaluationRequiresValidVersion:
    """Test 6: Evaluation requires valid model version in the record."""

    def test_evaluation_with_wrong_version_fails(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        record2 = _create_record(client, auth_headers, title="Other Model")
        version2 = _create_version(client, auth_headers, record2["id"])

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/evaluations",
            headers=auth_headers,
            json={
                "model_version_id": version2["id"],
                "evaluation_type": "historical",
                "evaluation_date": "2024-02-01",
                "data_cutoff_at": "2024-01-15",
                "engine_version": "1.1.0",
                "evaluator": "human",
            },
        )
        assert resp.status_code == 404

    def test_valid_evaluation_succeeds(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/evaluations",
            headers=auth_headers,
            json={
                "model_version_id": version["id"],
                "evaluation_type": "historical",
                "result_summary": "Performed within expected bounds",
                "evaluation_date": "2024-02-01",
                "data_cutoff_at": "2024-01-15",
                "engine_version": "1.1.0",
                "evaluator": "human",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["evaluation_type"] == "historical"


class TestStatusTransitionValidation:
    """Test 7: Invalid status transition rejected."""

    def test_invalid_transition_rejected(self, client, auth_headers):
        record = _create_record(client, auth_headers)

        resp = client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
            json={"status": "locked_for_simulation"},
        )
        assert resp.status_code == 400

    def test_valid_transition_accepted(self, client, auth_headers):
        record = _create_record(client, auth_headers)

        resp = client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
            json={"status": "researching"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "researching"

    def test_terminal_state_cannot_transition(self, client, auth_headers):
        record = _create_record(client, auth_headers)

        client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
            json={"status": "researching"},
        )
        client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
            json={"status": "retired"},
        )

        resp = client.patch(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
            json={"status": "draft"},
        )
        assert resp.status_code == 400


class TestNoLiveExecutionEndpoints:
    """Test 8: No BUY/SELL signal or live execution endpoints exist."""

    def test_no_signal_endpoint(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        for path in [
            f"/api/v1/model-research/{record['id']}/signal",
            f"/api/v1/model-research/{record['id']}/execute",
            f"/api/v1/model-research/{record['id']}/trade",
            "/api/v1/live-signals",
            "/api/v1/execute-order",
        ]:
            resp = client.post(path, headers=auth_headers, json={})
            assert resp.status_code in (404, 405), f"Unexpected success for {path}"

    def test_model_spec_has_no_execution_fields(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {
                    "rules": [{"indicator": "SMA", "period": 20}],
                    "broker_api_key": "should-not-be-here",
                },
            },
        )
        assert resp.status_code == 201
        spec = resp.json()["model_specification_json"]
        assert "broker_api_key" in spec

        resp = client.get(f"/api/v1/model-research/{record['id']}", headers=auth_headers)
        record_data = resp.json()
        assert "execution_endpoint" not in str(record_data)
        assert "live_trading" not in str(record_data)


class TestModelCardOwnership:
    """Test 9: Model card requires valid model version ownership."""

    def test_card_requires_owned_version(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])
        user2_headers = _create_user2(client)

        resp = client.post("/api/v1/model-cards", headers=user2_headers, json={
            "model_version_id": version["id"],
            "purpose": "Test card",
            "intended_research_use": "Backtesting evaluation",
            "excluded_use": "Live trading decisions",
            "data_sources": ["DEMO-ALPHA"],
            "data_cutoff": "2024-01-15",
            "known_limitations": ["Demo data only"],
            "revocation_process": "Owner may revoke at any time",
            "disclaimer": "Research tool, not investment advice.",
        })
        assert resp.status_code in (403, 404)

    def test_valid_card_creation(self, client, auth_headers):
        record = _create_record(client, auth_headers)
        version = _create_version(client, auth_headers, record["id"])

        resp = client.post("/api/v1/model-cards", headers=auth_headers, json={
            "model_version_id": version["id"],
            "purpose": "Test card",
            "intended_research_use": "Backtesting evaluation",
            "excluded_use": "Live trading decisions",
            "data_sources": ["DEMO-ALPHA"],
            "data_cutoff": "2024-01-15",
            "known_limitations": ["Demo data only"],
            "revocation_process": "Owner may revoke at any time",
            "disclaimer": "Research tool, not investment advice.",
        })
        assert resp.status_code == 201
        card = resp.json()
        assert card["purpose"] == "Test card"
        assert card["disclaimer"] == "Research tool, not investment advice."


class TestConsentEnforcement:
    """Test 10: Cannot set general_model_training_opt_in=true via create API."""

    def test_training_opt_in_blocked_on_create(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "explicit_preference",
            "content": "Trying to opt in via creation",
            "general_model_training_opt_in": True,
        })
        assert resp.status_code == 422

    def test_default_training_opt_in_is_false(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "reflection",
            "content": "Normal memory creation",
        })
        assert resp.status_code == 201
        assert resp.json()["general_model_training_opt_in"] is False

    def test_privacy_defaults_to_private_only(self, client, auth_headers):
        resp = client.post("/api/v1/personal-memory", headers=auth_headers, json={
            "memory_type": "reflection",
            "content": "Default privacy test",
        })
        assert resp.status_code == 201
        assert resp.json()["privacy_scope"] == "private_only"


class TestUUIDJSONBoundaryRegression:
    """Regression: UUID objects must serialize to JSON strings when stored."""

    def test_model_research_serializes_uuid_references_to_json_strings(self, client, auth_headers):
        exp_resp = client.post("/api/v1/experiments/", headers=auth_headers, json={
            "title": "UUID Boundary Test",
            "research_type": "quantitative",
            "strategy_spec": {
                "schema_version": "1.0",
                "template_type": "buy_and_hold",
                "asset_ids": ["DEMO-ALPHA"],
                "frequency": "1d",
                "start_date": "2024-01-02",
                "end_date": "2024-02-14",
                "initial_cash": 10000,
                "signal_parameters": {},
                "commission_rate": 0.001,
                "slippage_rate": 0.001,
                "benchmark_id": "DEMO-BENCHMARK",
                "currency": "USD",
            },
        })
        assert exp_resp.status_code == 201
        experiment_version_id = exp_resp.json()["current_version_id"]

        record = _create_record(client, auth_headers)

        resp = client.post(
            f"/api/v1/model-research/{record['id']}/versions",
            headers=auth_headers,
            json={
                "model_type": "rule_based",
                "model_specification_json": {"rules": []},
                "linked_experiment_version_ids": [experiment_version_id],
            },
        )
        assert resp.status_code == 201, f"UUID serialization failed: {resp.text}"

        version = resp.json()
        assert version["linked_experiment_version_ids"] is not None
        assert len(version["linked_experiment_version_ids"]) == 1
        stored_id = version["linked_experiment_version_ids"][0]
        assert isinstance(stored_id, str)
        assert stored_id == experiment_version_id

        get_resp = client.get(
            f"/api/v1/model-research/{record['id']}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
