import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.auth.jwt import get_password_hash, create_access_token
from app.models.user import User
from app.models.pilot import (
    PilotApplication,
    PilotApplicationStatus,
    PilotEntitlement,
    UsageEvent,
    UsageEventType,
    CostRecord,
    CostType,
    CostSource,
    SupportLog,
    ResolutionStatus,
    ProductHypothesis,
    HypothesisStatus,
)


@pytest.fixture()
def admin_headers(client):
    from app.database import get_db
    db = next(get_db())
    admin = User(
        email="admin@alethra.io",
        password_hash=get_password_hash("adminpass123"),
        display_name="Admin User",
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    token = create_access_token(data={"sub": str(admin.id)})
    db.close()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_and_user(client):
    from app.database import get_db
    db = next(get_db())
    admin = User(
        email="admin2@alethra.io",
        password_hash=get_password_hash("adminpass123"),
        display_name="Admin",
        is_admin=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    resp = client.post("/api/auth/register", json={
        "email": "user@alethra.io",
        "password": "userpass123",
        "display_name": "Regular User",
        "user_type": "individual",
    })
    login = client.post("/api/auth/login", json={
        "email": "user@alethra.io",
        "password": "userpass123",
    })
    user_token = login.json()["access_token"]
    admin_token = create_access_token(data={"sub": str(admin.id)})
    db.close()
    return {
        "admin_headers": {"Authorization": f"Bearer {admin_token}"},
        "user_headers": {"Authorization": f"Bearer {user_token}"},
        "admin_id": str(admin.id),
    }


# ── Pilot Application Tests ──


class TestPilotApplication:
    def test_submit_application(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "individual_researcher",
            "current_research_workflow": "I use Excel for backtesting",
            "tools_currently_used": "Excel, TradingView",
            "primary_pain_point": "No systematic workflow",
            "expected_usage_frequency": "weekly",
            "willingness_to_interview": True,
            "consent_to_contact": True,
        }, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["intended_user_type"] == "individual_researcher"
        assert data["status"] == "submitted"
        assert data["willingness_to_interview"] is True
        assert data["consent_to_contact"] is True

    def test_duplicate_application_rejected(self, client, auth_headers):
        client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "educator",
            "consent_to_contact": True,
        }, headers=auth_headers)
        resp = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "educator",
            "consent_to_contact": True,
        }, headers=auth_headers)
        assert resp.status_code == 409

    def test_get_my_application(self, client, auth_headers):
        client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "creator",
            "consent_to_contact": True,
        }, headers=auth_headers)
        resp = client.get("/api/v1/pilot/applications/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["intended_user_type"] == "creator"

    def test_no_application_returns_404(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/applications/me", headers=auth_headers)
        assert resp.status_code == 404

    def test_invalid_user_type_rejected(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "invalid_type",
            "consent_to_contact": True,
        }, headers=auth_headers)
        assert resp.status_code == 422

    def test_no_brokerage_data_collected(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "individual_researcher",
            "consent_to_contact": True,
        }, headers=auth_headers)
        data = resp.json()
        assert "brokerage_account" not in data
        assert "financial_data" not in data
        assert "bank_account" not in data


class TestPilotApplicationAdmin:
    def test_admin_list_applications(self, client, admin_and_user):
        client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "educator",
            "consent_to_contact": True,
        }, headers=admin_and_user["user_headers"])
        resp = client.get("/api/v1/pilot/applications", headers=admin_and_user["admin_headers"])
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_standard_user_cannot_list_applications(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/applications", headers=auth_headers)
        assert resp.status_code == 403

    def test_admin_update_status(self, client, admin_and_user):
        submit = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "ai_builder",
            "consent_to_contact": True,
        }, headers=admin_and_user["user_headers"])
        app_id = submit.json()["id"]
        resp = client.put(
            f"/api/v1/pilot/applications/{app_id}/status?new_status=approved",
            headers=admin_and_user["admin_headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_invalid_status_rejected(self, client, admin_and_user):
        submit = client.post("/api/v1/pilot/applications", json={
            "intended_user_type": "educator",
            "consent_to_contact": True,
        }, headers=admin_and_user["user_headers"])
        app_id = submit.json()["id"]
        resp = client.put(
            f"/api/v1/pilot/applications/{app_id}/status?new_status=invalid",
            headers=admin_and_user["admin_headers"],
        )
        assert resp.status_code == 422


# ── Pilot Entitlement Tests ──


class TestPilotEntitlement:
    def test_admin_grant_entitlement(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        resp = client.post("/api/v1/pilot/entitlements", json={
            "user_id": user_id,
            "pilot_access": True,
            "research_run_limit": 100,
            "ai_usage_limit": 50,
            "export_limit": 10,
            "forward_simulation_limit": 20,
            "support_tier": "priority",
        }, headers=admin_and_user["admin_headers"])
        assert resp.status_code == 201
        data = resp.json()
        assert data["research_run_limit"] == 100
        assert data["support_tier"] == "priority"
        assert data["pilot_access"] is True

    def test_standard_user_cannot_grant(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/entitlements", json={
            "user_id": str(uuid4()),
            "pilot_access": True,
        }, headers=auth_headers)
        assert resp.status_code == 403

    def test_get_my_entitlement(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        client.post("/api/v1/pilot/entitlements", json={
            "user_id": user_id,
        }, headers=admin_and_user["admin_headers"])

        resp = client.get("/api/v1/pilot/entitlements/me", headers=admin_and_user["user_headers"])
        assert resp.status_code == 200
        assert resp.json()["pilot_access"] is True

    def test_expired_entitlement_returns_403(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        past_date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)).strftime("%Y-%m-%d")
        client.post("/api/v1/pilot/entitlements", json={
            "user_id": user_id,
            "expiry_date": past_date,
        }, headers=admin_and_user["admin_headers"])

        resp = client.get("/api/v1/pilot/entitlements/me", headers=admin_and_user["user_headers"])
        assert resp.status_code == 403
        assert "expired" in resp.json()["detail"].lower()

    def test_disabled_access_returns_403(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        client.post("/api/v1/pilot/entitlements", json={
            "user_id": user_id,
            "pilot_access": False,
        }, headers=admin_and_user["admin_headers"])

        resp = client.get("/api/v1/pilot/entitlements/me", headers=admin_and_user["user_headers"])
        assert resp.status_code == 403

    def test_duplicate_entitlement_rejected(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        client.post("/api/v1/pilot/entitlements", json={"user_id": user_id}, headers=admin_and_user["admin_headers"])
        resp = client.post("/api/v1/pilot/entitlements", json={"user_id": user_id}, headers=admin_and_user["admin_headers"])
        assert resp.status_code == 409

    def test_admin_update_entitlement(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        grant = client.post("/api/v1/pilot/entitlements", json={"user_id": user_id}, headers=admin_and_user["admin_headers"])
        ent_id = grant.json()["id"]

        resp = client.put(f"/api/v1/pilot/entitlements/{ent_id}", json={
            "research_run_limit": 200,
            "support_tier": "dedicated",
        }, headers=admin_and_user["admin_headers"])
        assert resp.status_code == 200
        assert resp.json()["research_run_limit"] == 200
        assert resp.json()["support_tier"] == "dedicated"


# ── Usage Event Tests ──


class TestUsageEvents:
    def test_record_usage_event(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/usage-events", json={
            "event_type": "signup_completed",
        }, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["event_type"] == "signup_completed"

    def test_record_event_with_metadata(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/usage-events", json={
            "event_type": "first_experiment_created",
            "metadata_json": {"experiment_id": "test-123"},
        }, headers=auth_headers)
        assert resp.status_code == 201
        assert resp.json()["metadata_json"]["experiment_id"] == "test-123"

    def test_invalid_event_type_rejected(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/usage-events", json={
            "event_type": "invalid_event",
        }, headers=auth_headers)
        assert resp.status_code == 422

    def test_all_11_event_types_accepted(self, client, auth_headers):
        event_types = [
            "signup_completed", "pilot_application_submitted",
            "first_experiment_created", "first_run_completed",
            "strategy_locked", "forward_simulation_started",
            "paper_simulation_revisited", "second_experiment_created",
            "ai_draft_approved", "export_generated", "support_request_created",
        ]
        for et in event_types:
            resp = client.post("/api/v1/pilot/usage-events", json={
                "event_type": et,
            }, headers=auth_headers)
            assert resp.status_code == 201, f"Failed for {et}"

    def test_get_my_usage_events(self, client, auth_headers):
        client.post("/api/v1/pilot/usage-events", json={"event_type": "signup_completed"}, headers=auth_headers)
        client.post("/api/v1/pilot/usage-events", json={"event_type": "first_run_completed"}, headers=auth_headers)
        resp = client.get("/api/v1/pilot/usage-events/me", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_usage_events_do_not_expose_private_research(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/usage-events", json={
            "event_type": "first_experiment_created",
            "metadata_json": {"experiment_id": "private-data"},
        }, headers=auth_headers)
        assert resp.status_code == 201
        event_data = resp.json()
        assert "strategy_spec" not in event_data
        assert "metrics" not in event_data
        assert "password" not in str(event_data)

    def test_standard_user_cannot_list_all_events(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/usage-events", headers=auth_headers)
        assert resp.status_code == 403


# ── Cost Record Tests ──


class TestCostRecords:
    def test_create_actual_cost(self, client, admin_headers):
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "ai_inference",
            "amount": 12.50,
            "currency": "USD",
            "source": "actual",
            "description": "OpenAI API costs for October",
        }, headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["source"] == "actual"
        assert data["amount"] == 12.50
        assert data["method_of_estimation"] is None

    def test_create_estimate_requires_method(self, client, admin_headers):
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "compute",
            "amount": 50.0,
            "source": "estimate",
        }, headers=admin_headers)
        assert resp.status_code == 422

    def test_create_estimate_with_method(self, client, admin_headers):
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "compute",
            "amount": 50.0,
            "source": "estimate",
            "method_of_estimation": "Based on AWS pricing page, estimated 100 hours of t3.medium",
        }, headers=admin_headers)
        assert resp.status_code == 201
        assert resp.json()["source"] == "estimate"
        assert resp.json()["method_of_estimation"] is not None

    def test_actual_cost_no_method_needed(self, client, admin_headers):
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "data",
            "amount": 200.0,
            "source": "actual",
        }, headers=admin_headers)
        assert resp.status_code == 201

    def test_standard_user_cannot_create_costs(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "storage",
            "amount": 5.0,
            "source": "actual",
        }, headers=auth_headers)
        assert resp.status_code == 403

    def test_list_costs_admin_only(self, client, auth_headers, admin_headers):
        client.post("/api/v1/pilot/costs", json={
            "cost_type": "ai_inference",
            "amount": 10.0,
            "source": "actual",
        }, headers=admin_headers)
        resp = client.get("/api/v1/pilot/costs", headers=auth_headers)
        assert resp.status_code == 403

        resp = client.get("/api/v1/pilot/costs", headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_cost_records_distinguish_actual_vs_estimate(self, client, admin_headers):
        client.post("/api/v1/pilot/costs", json={
            "cost_type": "ai_inference",
            "amount": 10.0,
            "source": "actual",
        }, headers=admin_headers)
        client.post("/api/v1/pilot/costs", json={
            "cost_type": "compute",
            "amount": 20.0,
            "source": "estimate",
            "method_of_estimation": "Estimated from usage patterns",
        }, headers=admin_headers)

        actual_resp = client.get("/api/v1/pilot/costs?source=actual", headers=admin_headers)
        estimate_resp = client.get("/api/v1/pilot/costs?source=estimate", headers=admin_headers)

        actual_items = actual_resp.json()
        estimate_items = estimate_resp.json()

        assert all(item["source"] == "actual" for item in actual_items)
        assert all(item["source"] == "estimate" for item in estimate_items)


# ── Support Log Tests ──


class TestSupportLogs:
    def test_create_support_log(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        resp = client.post("/api/v1/pilot/support-logs", json={
            "user_id": user_id,
            "category": "onboarding",
            "time_spent_minutes": 30,
            "resolution_status": "resolved",
            "recurring_issue_tag": "setup_confusion",
            "notes": "User had trouble finding the experiment creation page",
        }, headers=admin_and_user["admin_headers"])
        assert resp.status_code == 201
        data = resp.json()
        assert data["category"] == "onboarding"
        assert data["time_spent_minutes"] == 30
        assert data["resolution_status"] == "resolved"

    def test_standard_user_cannot_create_logs(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/support-logs", json={
            "user_id": str(uuid4()),
            "category": "bug",
            "time_spent_minutes": 15,
        }, headers=auth_headers)
        assert resp.status_code == 403

    def test_update_support_log(self, client, admin_and_user):
        from app.database import get_db
        db = next(get_db())
        user = db.query(User).filter(User.email == "user@alethra.io").first()
        user_id = str(user.id)
        db.close()

        create = client.post("/api/v1/pilot/support-logs", json={
            "user_id": user_id,
            "category": "technical",
            "time_spent_minutes": 15,
        }, headers=admin_and_user["admin_headers"])
        log_id = create.json()["id"]

        resp = client.put(f"/api/v1/pilot/support-logs/{log_id}", json={
            "resolution_status": "resolved",
            "time_spent_minutes": 25,
        }, headers=admin_and_user["admin_headers"])
        assert resp.status_code == 200
        assert resp.json()["resolution_status"] == "resolved"
        assert resp.json()["time_spent_minutes"] == 25

    def test_support_logs_protected(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/support-logs", headers=auth_headers)
        assert resp.status_code == 403


# ── Product Hypothesis Tests ──


class TestProductHypothesis:
    def test_create_hypothesis(self, client, admin_headers):
        resp = client.post("/api/v1/pilot/hypotheses", json={
            "statement": "Users are willing to pay for a research tool",
            "evidence_criteria": "At least 5 pilot users express willingness to pay",
        }, headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "untested"
        assert data["statement"] == "Users are willing to pay for a research tool"

    def test_update_hypothesis_status(self, client, admin_headers):
        create = client.post("/api/v1/pilot/hypotheses", json={
            "statement": "Educators need team features",
            "evidence_criteria": "3+ educator pilot users request team features",
        }, headers=admin_headers)
        hyp_id = create.json()["id"]

        resp = client.put(f"/api/v1/pilot/hypotheses/{hyp_id}", json={
            "status": "evidence_emerging",
            "evidence_notes": "2 out of 3 educators have asked about team features",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "evidence_emerging"

    def test_no_hypothesis_can_be_labeled_proven(self, client, admin_headers):
        create = client.post("/api/v1/pilot/hypotheses", json={
            "statement": "Test hypothesis",
        }, headers=admin_headers)
        hyp_id = create.json()["id"]

        resp = client.put(f"/api/v1/pilot/hypotheses/{hyp_id}", json={
            "status": "proven",
        }, headers=admin_headers)
        assert resp.status_code == 422

    def test_standard_user_cannot_manage_hypotheses(self, client, auth_headers):
        resp = client.post("/api/v1/pilot/hypotheses", json={
            "statement": "Test",
        }, headers=auth_headers)
        assert resp.status_code == 403

    def test_list_hypotheses(self, client, admin_headers):
        client.post("/api/v1/pilot/hypotheses", json={"statement": "H1"}, headers=admin_headers)
        client.post("/api/v1/pilot/hypotheses", json={"statement": "H2"}, headers=admin_headers)
        resp = client.get("/api/v1/pilot/hypotheses", headers=admin_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2


# ── Admin Dashboard Tests ──


class TestAdminDashboard:
    def test_dashboard_no_data(self, client, admin_headers):
        resp = client.get("/api/v1/pilot/admin/dashboard", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["funnel"]["total_users"] >= 1
        assert data["activation"]["total_signed_up"] >= 1
        assert data["activation"]["activation_rate"] == 0.0
        assert data["cost_summary"]["total_cost"] == 0.0
        assert "disclaimer" in data

    def test_dashboard_standard_user_forbidden(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/admin/dashboard", headers=auth_headers)
        assert resp.status_code == 403

    def test_dashboard_includes_disclaimer(self, client, admin_headers):
        resp = client.get("/api/v1/pilot/admin/dashboard", headers=admin_headers)
        data = resp.json()
        assert "not public pricing" in data["disclaimer"].lower() or "not market validated" in data["disclaimer"].lower()

    def test_dashboard_with_usage_data(self, client, admin_and_user):
        client.post("/api/v1/pilot/usage-events", json={"event_type": "signup_completed"}, headers=admin_and_user["user_headers"])
        client.post("/api/v1/pilot/usage-events", json={"event_type": "first_experiment_created"}, headers=admin_and_user["user_headers"])
        client.post("/api/v1/pilot/usage-events", json={"event_type": "first_run_completed"}, headers=admin_and_user["user_headers"])

        resp = client.get("/api/v1/pilot/admin/dashboard", headers=admin_and_user["admin_headers"])
        data = resp.json()
        assert data["funnel"]["first_experiment_created"] >= 1
        assert data["funnel"]["first_run_completed"] >= 1
        assert data["activation"]["activated_at_least_one_experiment"] >= 1

    def test_dashboard_cost_per_user_no_divide_by_zero(self, client, admin_headers):
        resp = client.get("/api/v1/pilot/admin/dashboard", headers=admin_headers)
        data = resp.json()
        assert isinstance(data["cost_summary"]["cost_per_user"], float)
        assert isinstance(data["cost_summary"]["cost_per_active_user"], float)


class TestHypothesesDashboard:
    def test_hypotheses_dashboard_no_data(self, client, admin_headers):
        resp = client.get("/api/v1/pilot/admin/hypotheses-dashboard", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["by_status"]["untested"] == 0
        assert "disclaimer" in data

    def test_hypotheses_dashboard_with_data(self, client, admin_headers):
        client.post("/api/v1/pilot/hypotheses", json={"statement": "H1"}, headers=admin_headers)
        client.post("/api/v1/pilot/hypotheses", json={"statement": "H2"}, headers=admin_headers)

        resp = client.get("/api/v1/pilot/admin/hypotheses-dashboard", headers=admin_headers)
        data = resp.json()
        assert data["total"] == 2
        assert data["by_status"]["untested"] == 2

    def test_hypotheses_dashboard_standard_user_forbidden(self, client, auth_headers):
        resp = client.get("/api/v1/pilot/admin/hypotheses-dashboard", headers=auth_headers)
        assert resp.status_code == 403


# ── Cross-cutting Security Tests ──


class TestSecurityBoundaries:
    def test_pilot_data_access_controlled(self, client, auth_headers, admin_headers):
        """Standard users cannot access admin-only pilot endpoints."""
        admin_only_endpoints = [
            ("GET", "/api/v1/pilot/applications"),
            ("GET", "/api/v1/pilot/entitlements"),
            ("GET", "/api/v1/pilot/usage-events"),
            ("GET", "/api/v1/pilot/costs"),
            ("GET", "/api/v1/pilot/support-logs"),
            ("GET", "/api/v1/pilot/hypotheses"),
            ("GET", "/api/v1/pilot/admin/dashboard"),
            ("GET", "/api/v1/pilot/admin/hypotheses-dashboard"),
        ]
        for method, endpoint in admin_only_endpoints:
            if method == "GET":
                resp = client.get(endpoint, headers=auth_headers)
            assert resp.status_code == 403, f"{method} {endpoint} should be 403 for standard users"

    def test_admin_data_inaccessible_to_standard_users(self, client, auth_headers):
        """No admin data is accessible to non-admin users."""
        resp = client.get("/api/v1/pilot/admin/dashboard", headers=auth_headers)
        assert resp.status_code == 403

    def test_no_payment_processor_keys(self, client, admin_headers):
        """No Stripe or payment processor integration exists."""
        resp = client.post("/api/v1/pilot/costs", json={
            "cost_type": "other",
            "amount": 1.0,
            "source": "actual",
        }, headers=admin_headers)
        data = resp.json()
        assert "stripe" not in str(data).lower()
        assert "checkout" not in str(data).lower()
        assert "payment_intent" not in str(data).lower()
