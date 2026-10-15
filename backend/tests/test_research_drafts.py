"""Tests for Phase 2A research drafts."""
import pytest
from uuid import uuid4


class TestResearchDraftCRUD:
    """Test research draft CRUD operations."""

    def test_create_research_draft(self, client, auth_headers):
        """Test creating a new research draft."""
        resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Test Hypothesis",
                "hypothesis": "Testing that DEMO-ALPHA will increase",
                "falsification_criteria": "If it decreases by 10%",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Hypothesis"
        assert data["status"] == "draft"
        assert "id" in data
        assert "created_at" in data

    def test_list_research_drafts(self, client, auth_headers):
        """Test listing research drafts."""
        client.post(
            "/api/v1/research-drafts/",
            json={"title": "Draft 1", "hypothesis": "Test 1"},
            headers=auth_headers,
        )
        client.post(
            "/api/v1/research-drafts/",
            json={"title": "Draft 2", "hypothesis": "Test 2"},
            headers=auth_headers,
        )
        resp = client.get("/api/v1/research-drafts/", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    def test_get_research_draft(self, client, auth_headers):
        """Test getting a specific research draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test Draft", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == draft_id

    def test_update_research_draft(self, client, auth_headers):
        """Test updating a research draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Original Title", "hypothesis": "Original"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Updated Title", "hypothesis": "Updated"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"
        assert resp.json()["hypothesis"] == "Updated"

    def test_delete_research_draft(self, client, auth_headers):
        """Test deleting a research draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "To Delete", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert resp.status_code == 204
        get_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert get_resp.status_code == 404


class TestResearchDraftOwnership:
    """Test draft ownership and access control."""

    def test_user_cannot_access_other_users_draft(self, client):
        """Test that users cannot access other users' drafts."""
        resp1 = client.post(
            "/api/auth/register",
            json={
                "email": "user1@test.com",
                "password": "pass1234",
                "display_name": "User 1",
                "user_type": "individual",
            },
        )
        headers1 = client.post(
            "/api/auth/login",
            json={"email": "user1@test.com", "password": "pass1234"},
        ).json()
        headers1 = {"Authorization": f"Bearer {headers1['access_token']}"}

        resp2 = client.post(
            "/api/auth/register",
            json={
                "email": "user2@test.com",
                "password": "pass1234",
                "display_name": "User 2",
                "user_type": "individual",
            },
        )
        headers2 = client.post(
            "/api/auth/login",
            json={"email": "user2@test.com", "password": "pass1234"},
        ).json()
        headers2 = {"Authorization": f"Bearer {headers2['access_token']}"}

        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "User 1 Draft", "hypothesis": "Test"},
            headers=headers1,
        )
        draft_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=headers2)
        assert get_resp.status_code == 403

    def test_user_cannot_modify_other_users_draft(self, client):
        """Test that users cannot modify other users' drafts."""
        resp1 = client.post(
            "/api/auth/register",
            json={
                "email": "owner@test.com",
                "password": "pass1234",
                "display_name": "Owner",
                "user_type": "individual",
            },
        )
        headers1 = client.post(
            "/api/auth/login",
            json={"email": "owner@test.com", "password": "pass1234"},
        ).json()
        headers1 = {"Authorization": f"Bearer {headers1['access_token']}"}

        resp2 = client.post(
            "/api/auth/register",
            json={
                "email": "other@test.com",
                "password": "pass1234",
                "display_name": "Other",
                "user_type": "individual",
            },
        )
        headers2 = client.post(
            "/api/auth/login",
            json={"email": "other@test.com", "password": "pass1234"},
        ).json()
        headers2 = {"Authorization": f"Bearer {headers2['access_token']}"}

        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Owner Draft", "hypothesis": "Test"},
            headers=headers1,
        )
        draft_id = create_resp.json()["id"]

        update_resp = client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Hacked"},
            headers=headers2,
        )
        assert update_resp.status_code == 403


class TestResearchDraftRevisions:
    """Test draft revision history."""

    def test_draft_creates_initial_revision(self, client, auth_headers):
        """Test that creating a draft creates revision 1."""
        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = resp.json()["id"]
        revisions_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions",
            headers=auth_headers,
        )
        assert revisions_resp.status_code == 200
        revisions = revisions_resp.json()
        assert len(revisions) == 1
        assert revisions[0]["revision_number"] == "1"

    def test_update_creates_new_revision(self, client, auth_headers):
        """Test that updating a draft creates a new revision."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Original", "hypothesis": "Original"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Updated"},
            headers=auth_headers,
        )

        revisions_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions",
            headers=auth_headers,
        )
        revisions = revisions_resp.json()
        assert len(revisions) == 2
        assert revisions[0]["revision_number"] == "1"
        assert revisions[0]["title"] == "Original"
        assert revisions[1]["revision_number"] == "2"
        assert revisions[1]["title"] == "Updated"


class TestMockResearchAssistant:
    """Test mock research assistant provider."""

    def test_generate_questions_for_missing_fields(self, client, auth_headers):
        """Test that assistant generates questions for missing required fields."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        assert len(data["questions"]) > 0
        fields_asked = [q["field"] for q in data["questions"]]
        assert "hypothesis" in fields_asked
        assert "falsification_criteria" in fields_asked

    def test_no_questions_for_complete_draft(self, client, auth_headers):
        """Test that assistant generates no questions for complete draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Complete Draft",
                "hypothesis": "Test hypothesis",
                "falsification_criteria": "Test falsification",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        required_fields = ["title", "hypothesis", "falsification_criteria", "strategy_template", "asset_symbol"]
        fields_asked = [q["field"] for q in data["questions"]]
        for field in required_fields:
            assert field not in fields_asked

    def test_submit_answers_updates_draft(self, client, auth_headers):
        """Test that submitting answers updates the draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={
                "answers": {
                    "hypothesis": "My hypothesis",
                    "falsification_criteria": "My falsification",
                }
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hypothesis"] == "My hypothesis"
        assert data["falsification_criteria"] == "My falsification"


class TestResearchDraftApproval:
    """Test draft approval workflow."""

    def test_accept_complete_draft(self, client, auth_headers):
        """Test accepting a complete draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Complete",
                "hypothesis": "Test",
                "falsification_criteria": "Test",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_cannot_accept_incomplete_draft(self, client, auth_headers):
        """Test that incomplete drafts cannot be accepted."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Incomplete"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "missing required fields" in resp.json()["detail"].lower()

    def test_reject_draft(self, client, auth_headers):
        """Test rejecting a draft."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "To Reject", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "reject", "notes": "Not viable"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_cannot_modify_accepted_draft(self, client, auth_headers):
        """Test that accepted drafts cannot be modified."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Complete",
                "hypothesis": "Test",
                "falsification_criteria": "Test",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )

        resp = client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Modified"},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestResearchDraftAuditEvents:
    """Test audit event logging."""

    def test_create_emits_audit_event(self, client, auth_headers):
        """Test that creating a draft emits an audit event."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        events_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events",
            headers=auth_headers,
        )
        assert events_resp.status_code == 200
        events = events_resp.json()
        assert len(events) >= 1
        assert events[0]["event_type"] == "research_draft_created"

    def test_update_emits_audit_event(self, client, auth_headers):
        """Test that updating a draft emits an audit event."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Updated"},
            headers=auth_headers,
        )

        events_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events",
            headers=auth_headers,
        )
        events = events_resp.json()
        event_types = [e["event_type"] for e in events]
        assert "research_draft_updated" in event_types

    def test_approve_emits_audit_event(self, client, auth_headers):
        """Test that approving a draft emits an audit event."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Complete",
                "hypothesis": "Test",
                "falsification_criteria": "Test",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )

        events_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events",
            headers=auth_headers,
        )
        events = events_resp.json()
        event_types = [e["event_type"] for e in events]
        assert "research_draft_accepted" in event_types


class TestResearchDraftSchemaValidation:
    """Test schema validation."""

    def test_symbol_format_validation(self, client, auth_headers):
        """Test that symbol format is validated."""
        resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Test",
                "asset_symbol": "invalid-symbol",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_strategy_template_validation(self, client, auth_headers):
        """Test that strategy template is validated."""
        resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Test",
                "strategy_template": "invalid_template",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_valid_strategy_templates(self, client, auth_headers):
        """Test that valid strategy templates are accepted."""
        for template in ["buy_and_hold", "sma_crossover", "price_change_fixed_hold"]:
            resp = client.post(
                "/api/v1/research-drafts/",
                json={
                    "title": f"Test {template}",
                    "strategy_template": template,
                },
                headers=auth_headers,
            )
            assert resp.status_code == 201


class TestMockAssistantDeterminism:
    """Test that mock assistant is deterministic."""

    def test_same_input_same_questions(self, client, auth_headers):
        """Test that same input produces same questions."""
        create_resp1 = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test 1"},
            headers=auth_headers,
        )
        draft_id1 = create_resp1.json()["id"]

        create_resp2 = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test 2"},
            headers=auth_headers,
        )
        draft_id2 = create_resp2.json()["id"]

        resp1 = client.post(
            f"/api/v1/research-drafts/{draft_id1}/questions",
            headers=auth_headers,
        )
        resp2 = client.post(
            f"/api/v1/research-drafts/{draft_id2}/questions",
            headers=auth_headers,
        )

        questions1 = [q["field"] for q in resp1.json()["questions"]]
        questions2 = [q["field"] for q in resp2.json()["questions"]]
        assert questions1 == questions2


class TestAPIPathRegression:
    """Regression tests for API path correctness."""

    def test_correct_api_path_works(self, client, auth_headers):
        """Test that the correct API path /api/v1/research-drafts/ works."""
        resp = client.get("/api/v1/research-drafts/", headers=auth_headers)
        assert resp.status_code == 200

    def test_duplicate_v1_path_returns_404(self, client, auth_headers):
        """Test that duplicate /api/v1/v1/research-drafts/ returns 404."""
        resp = client.get("/api/v1/v1/research-drafts/", headers=auth_headers)
        assert resp.status_code == 404

    def test_missing_v1_path_returns_404(self, client, auth_headers):
        """Test that /api/research-drafts/ without v1 returns 404."""
        resp = client.get("/api/research-drafts/", headers=auth_headers)
        assert resp.status_code == 404

    def test_unauthenticated_request_denied(self, client):
        """Test that unauthenticated requests are denied."""
        resp = client.get("/api/v1/research-drafts/")
        assert resp.status_code in [401, 403]

    def test_create_without_auth_denied(self, client):
        """Test that creating a draft without auth is denied."""
        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Test"},
        )
        assert resp.status_code in [401, 403]


def _make_experiment(client, headers, title="Exp"):
    """Helper: create an experiment and return its JSON body."""
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
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
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _register_and_login(client, email, password="pass1234", name="User"):
    """Helper: register + login, return auth headers dict."""
    client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": name, "user_type": "individual"},
    )
    token = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestAnswerValueValidation:
    """Fix 1: answers endpoint must validate values, not only field names."""

    def test_answers_reject_invalid_symbol(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={"answers": {"asset_symbol": "invalid-lowercase"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_answers_reject_invalid_strategy_template(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={"answers": {"strategy_template": "nonexistent_template"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_accept_rejects_draft_with_invalid_values(self, client, auth_headers):
        """Acceptance must revalidate typed fields, not just completeness."""
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "T",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_answers_accept_valid_values(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={"answers": {"strategy_template": "sma_crossover", "asset_symbol": "DEMO-BETA"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy_template"] == "sma_crossover"
        assert data["asset_symbol"] == "DEMO-BETA"


class TestFinalizedDraftQuestionsProtection:
    """Fix 2: /questions must not modify finalized drafts."""

    def _create_accepted_draft(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Final",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )
        return draft_id

    def test_questions_on_accepted_returns_existing(self, client, auth_headers):
        draft_id = self._create_accepted_draft(client, auth_headers)
        before = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers).json()

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        assert resp.status_code == 200

        after = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers).json()
        assert before["updated_at"] == after["updated_at"]

    def test_questions_on_finalized_no_new_audit_event(self, client, auth_headers):
        draft_id = self._create_accepted_draft(client, auth_headers)
        events_before = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()

        client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )

        events_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()
        assert len(events_after) == len(events_before)

    def test_questions_on_rejected_returns_readonly(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Reject", "hypothesis": "H"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]
        client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "reject"},
            headers=auth_headers,
        )
        before = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers).json()

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        assert resp.status_code == 200

        after = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers).json()
        assert before["updated_at"] == after["updated_at"]


class TestTransactionalConsistency:
    """Fix 3: draft change + revision + audit event commit together."""

    def test_create_produces_revision_and_audit(self, client, auth_headers):
        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Atomic", "hypothesis": "H"},
            headers=auth_headers,
        )
        draft_id = resp.json()["id"]

        revisions = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()
        assert len(revisions) == 1
        assert len(events) == 1
        assert events[0]["event_type"] == "research_draft_created"

    def test_failed_update_creates_no_revision_or_audit(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        revs_before = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events_before = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()

        bad = client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"asset_symbol": "invalid-lowercase"},
            headers=auth_headers,
        )
        assert bad.status_code == 422

        revs_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()
        assert len(revs_after) == len(revs_before)
        assert len(events_after) == len(events_before)

    def test_failed_answers_creates_no_revision_or_audit(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        revs_before = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events_before = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()

        bad = client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={"answers": {"strategy_template": "nonexistent"}},
            headers=auth_headers,
        )
        assert bad.status_code == 400

        revs_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()
        assert len(revs_after) == len(revs_before)
        assert len(events_after) == len(events_before)

    def test_answers_produces_exactly_one_revision_and_audit(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "T"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        client.post(
            f"/api/v1/research-drafts/{draft_id}/answers",
            json={"answers": {"hypothesis": "New hypothesis"}},
            headers=auth_headers,
        )

        revisions = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        events = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        ).json()
        assert len(revisions) == 2
        answer_events = [e for e in events if e["event_type"] == "research_draft_answers_submitted"]
        assert len(answer_events) == 1


class TestExperimentOwnershipValidation:
    """Fix 4: validate linked experiment exists and belongs to current user."""

    def test_create_with_own_experiment(self, client, auth_headers):
        exp = _make_experiment(client, auth_headers, "Own Exp")
        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Linked", "experiment_id": exp["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["experiment_id"] == exp["id"]

    def test_create_with_other_users_experiment(self, client):
        owner_h = _register_and_login(client, "expowner@test.com", name="Owner")
        other_h = _register_and_login(client, "expother@test.com", name="Other")

        exp = _make_experiment(client, owner_h, "Owner Exp")

        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Linked", "experiment_id": exp["id"]},
            headers=other_h,
        )
        assert resp.status_code == 403

    def test_create_with_nonexistent_experiment(self, client, auth_headers):
        fake_id = str(uuid4())
        resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Linked", "experiment_id": fake_id},
            headers=auth_headers,
        )
        assert resp.status_code == 404


class TestMissingFieldDetection:
    """Fix 5: zero values are valid, not missing."""

    def test_zero_commission_not_missing(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "ZeroComm",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
                "commission_rate": "0",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        fields_asked = [q["field"] for q in resp.json()["questions"]]
        assert "commission_rate" not in fields_asked

    def test_zero_slippage_not_missing(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "ZeroSlip",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
                "slippage_rate": "0.000",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        fields_asked = [q["field"] for q in resp.json()["questions"]]
        assert "slippage_rate" not in fields_asked

    def test_none_commission_is_missing(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "NoComm",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/questions",
            headers=auth_headers,
        )
        fields_asked = [q["field"] for q in resp.json()["questions"]]
        assert "commission_rate" in fields_asked

    def test_accept_draft_with_zero_cost(self, client, auth_headers):
        """Zero commission/slippage should not block acceptance."""
        create = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "ZeroCost",
                "hypothesis": "H",
                "falsification_criteria": "F",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
                "commission_rate": "0",
                "slippage_rate": "0",
            },
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    def test_validate_completeness_distinguishes_zero_from_missing(self):
        from app.lib.mock_research_assistant import mock_research_assistant

        result = mock_research_assistant.validate_draft_completeness({
            "title": "T",
            "hypothesis": "H",
            "falsification_criteria": "F",
            "strategy_template": "buy_and_hold",
            "asset_symbol": "DEMO-ALPHA",
            "commission_rate": "0",
            "slippage_rate": "0",
        })
        assert result["complete"] is True
        assert result["missing_fields"] == []
        assert "commission_rate" in result["zero_cost_warnings"]
        assert "slippage_rate" in result["zero_cost_warnings"]

    def test_validate_completeness_missing_fields(self):
        from app.lib.mock_research_assistant import mock_research_assistant

        result = mock_research_assistant.validate_draft_completeness({
            "title": "T",
            "hypothesis": None,
            "falsification_criteria": "",
            "strategy_template": "buy_and_hold",
            "asset_symbol": "DEMO-ALPHA",
        })
        assert result["complete"] is False
        assert "hypothesis" in result["missing_fields"]
        assert "falsification_criteria" in result["missing_fields"]


class TestDeletionCascade:
    """Inspect deletion cascade: deleting a draft removes revisions and audit events."""

    def test_delete_removes_revisions_and_audit_events(self, client, auth_headers):
        create = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Cascade", "hypothesis": "H"},
            headers=auth_headers,
        )
        draft_id = create.json()["id"]

        client.patch(
            f"/api/v1/research-drafts/{draft_id}",
            json={"title": "Updated"},
            headers=auth_headers,
        )

        revs = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        ).json()
        assert len(revs) >= 2

        del_resp = client.delete(
            f"/api/v1/research-drafts/{draft_id}", headers=auth_headers
        )
        assert del_resp.status_code == 204

        get_resp = client.get(
            f"/api/v1/research-drafts/{draft_id}", headers=auth_headers
        )
        assert get_resp.status_code == 404

        revs_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/revisions", headers=auth_headers
        )
        assert revs_after.status_code == 404

        events_after = client.get(
            f"/api/v1/research-drafts/{draft_id}/audit-events", headers=auth_headers
        )
        assert events_after.status_code == 404


class TestSecureFieldTransfer:
    """Test secure field transfer workflow for PR #3."""

    def test_draft_must_be_accepted_for_field_transfer(self, client, auth_headers):
        """Test that only accepted drafts can be used for field transfer."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Draft Status Test",
                "hypothesis": "Sensitive hypothesis text",
                "strategy_template": "sma_crossover",
                "asset_symbol": "DEMO-ALPHA",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "draft"

        get_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "draft"
        assert get_resp.json()["hypothesis"] == "Sensitive hypothesis text"

    def test_accepted_draft_can_be_fetched_for_transfer(self, client, auth_headers):
        """Test that accepted drafts can be fetched via authenticated API."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Accepted Draft",
                "hypothesis": "Test hypothesis",
                "falsification_criteria": "Test falsification",
                "strategy_template": "buy_and_hold",
                "asset_symbol": "DEMO-ALPHA",
                "initial_cash": "50000",
                "commission_rate": "0.001",
                "slippage_rate": "0.0005",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        approve_resp = client.post(
            f"/api/v1/research-drafts/{draft_id}/approve",
            json={"action": "accept", "notes": "Approved"},
            headers=auth_headers,
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "accepted"

        get_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["status"] == "accepted"
        assert data["title"] == "Accepted Draft"
        assert data["hypothesis"] == "Test hypothesis"
        assert data["initial_cash"] == "50000"
        assert data["commission_rate"] == "0.001"
        assert data["slippage_rate"] == "0.0005"

    def test_zero_values_preserved_in_draft(self, client, auth_headers):
        """Test that zero commission and slippage rates are preserved."""
        create_resp = client.post(
            "/api/v1/research-drafts/",
            json={
                "title": "Zero Values Test",
                "hypothesis": "Test",
                "commission_rate": "0",
                "slippage_rate": "0",
            },
            headers=auth_headers,
        )
        draft_id = create_resp.json()["id"]

        get_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["commission_rate"] == "0"
        assert data["slippage_rate"] == "0"

    def test_authentication_required_for_draft_fetch(self, client):
        """Test that authentication is required to fetch draft data."""
        create_resp = client.post(
            "/api/auth/register",
            json={
                "email": "authtest@test.com",
                "password": "pass1234",
                "display_name": "Auth Test",
                "user_type": "individual",
            },
        )
        user_id = create_resp.json()["id"]

        login_resp = client.post(
            "/api/auth/login",
            json={"email": "authtest@test.com", "password": "pass1234"},
        )
        token = login_resp.json()["access_token"]
        auth_headers = {"Authorization": f"Bearer {token}"}

        draft_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Auth Test Draft", "hypothesis": "Test"},
            headers=auth_headers,
        )
        draft_id = draft_resp.json()["id"]

        unauth_resp = client.get(f"/api/v1/research-drafts/{draft_id}")
        assert unauth_resp.status_code == 401

        auth_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=auth_headers)
        assert auth_resp.status_code == 200

    def test_owner_only_access_for_field_transfer(self, client):
        """Test that only the draft owner can fetch it for field transfer."""
        user1_resp = client.post(
            "/api/auth/register",
            json={
                "email": "owner@test.com",
                "password": "pass1234",
                "display_name": "Owner",
                "user_type": "individual",
            },
        )
        user1_login = client.post(
            "/api/auth/login",
            json={"email": "owner@test.com", "password": "pass1234"},
        )
        user1_headers = {"Authorization": f"Bearer {user1_login.json()['access_token']}"}

        user2_resp = client.post(
            "/api/auth/register",
            json={
                "email": "other@test.com",
                "password": "pass1234",
                "display_name": "Other",
                "user_type": "individual",
            },
        )
        user2_login = client.post(
            "/api/auth/login",
            json={"email": "other@test.com", "password": "pass1234"},
        )
        user2_headers = {"Authorization": f"Bearer {user2_login.json()['access_token']}"}

        draft_resp = client.post(
            "/api/v1/research-drafts/",
            json={"title": "Owner Draft", "hypothesis": "Private"},
            headers=user1_headers,
        )
        draft_id = draft_resp.json()["id"]

        owner_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=user1_headers)
        assert owner_resp.status_code == 200

        other_resp = client.get(f"/api/v1/research-drafts/{draft_id}", headers=user2_headers)
        assert other_resp.status_code == 403
