"""ALETHRA-BEHAVIOR-02 B1: Record-only human decision events.

Tests A-J validate the human decision recording contract:
  A) Successful creation returns 201 with all server-derived fields
  B) Server-derived context matches session/version, not client input
  C) recorded_at (created_at) is server-generated UTC, not client-supplied
  D) Quantity validation: buy/sell positive finite, hold must be null
  E) Idempotency: same key + same content returns original event
  F) Idempotency: same key + different content returns 409 conflict
  G) Correction creates new event with supersedes_event_id
  H) Correction preserves original event payload unchanged
  I) Ownership enforcement: cannot access other user's decisions
  J) execution_status is always record_only
"""
import pytest
import math
from datetime import date, datetime, timezone

from tests.conftest import TestSession
from app.models.audit_event import AuditEvent
from app.models.human_decision import HumanDecisionEvent


# ---------------------------------------------------------------------------
# Deterministic synthetic market data — required precondition for
# SimulationSession creation (POST /simulations/versions/{id} queries
# MarketDataBar and raises 400 if no stored bars exist in the range).
# Seeded per-test inside this module only; no live provider calls.
# ---------------------------------------------------------------------------

_DEMO_ALPHA_TRADING_DAYS = [
    date(2024, 1, d) for d in (
        2, 3, 4, 5,
        8, 9, 10, 11, 12,
        15, 16, 17, 18, 19,
        22, 23, 24, 25, 26,
        29, 30, 31,
    )
]


@pytest.fixture(autouse=True)
def _seed_demo_market_data(setup_db):
    """Insert deterministic synthetic MarketDataBar rows for DEMO-ALPHA.

    The simulation session creation endpoint requires stored bars for the
    strategy's asset_id within the version date range. Human-decision tests
    exercise a record-only contract and never advance the simulation, so a
    minimal, reproducible series is enough. provider_id is 'demo' to match
    the handoff provider convention.
    """
    from app.models.market_data import MarketDataBar

    db = TestSession()
    try:
        for i, d in enumerate(_DEMO_ALPHA_TRADING_DAYS):
            price = 50.0 + float(i)  # deterministic linear ramp
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


def _create_experiment(client, headers, title="B1 Test"):
    resp = client.post("/api/v1/experiments/", headers=headers, json={
        "title": title,
        "research_type": "quantitative",
        "strategy_spec": {
            "template_type": "buy_and_hold",
            "asset_ids": ["DEMO-ALPHA"],
            "start_date": "2024-01-02",
            "end_date": "2024-01-31",
            "initial_cash": 100000,
        },
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], resp.json()["current_version_id"]


def _lock_experiment(client, headers, exp_id):
    resp = client.put(f"/api/v1/experiments/{exp_id}", headers=headers, json={
        "scope": {"status": "locked"},
    })
    assert resp.status_code == 200, resp.text


def _create_session(client, headers, version_id):
    resp = client.post(f"/api/v1/simulations/versions/{version_id}", headers=headers, json={
        "initial_virtual_cash": 100000,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestHumanDecisionRecording:

    def test_A_creation_returns_201_with_server_fields(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test A")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-a-key-001",
            "action": "buy",
            "quantity": 100.0,
            "rationale": "Strong bullish signal",
        })
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["owner_id"] is not None
        assert body["simulation_session_id"] == session_id
        assert body["idempotency_key"] == "test-a-key-001"
        assert body["action"] == "buy"
        assert body["quantity"] == 100.0
        assert body["rationale"] == "Strong bullish signal"
        assert body["server_strategy_version_id"] == version_id
        assert body["server_instrument_id"] == "DEMO-ALPHA"
        assert body["server_step_number"] >= 1
        assert body["server_snapshot_date"] is not None
        assert body["server_data_cutoff_at"] is not None
        assert body["execution_status"] == "record_only"
        assert body["supersedes_event_id"] is None
        assert body["id"] is not None
        assert body["created_at"] is not None

    def test_B_server_derived_context_not_client_supplied(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test B")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-b-key-001",
            "action": "buy",
            "quantity": 50.0,
        })
        assert resp.status_code == 201
        body = resp.json()

        assert body["server_strategy_version_id"] == version_id
        assert body["server_instrument_id"] == "DEMO-ALPHA"
        assert isinstance(body["server_step_number"], int)
        assert body["server_step_number"] >= 1

    def test_C_recorded_at_server_generated(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test C")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        before = datetime.now(timezone.utc)
        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-c-key-001",
            "action": "hold",
            "quantity": None,
        })
        after = datetime.now(timezone.utc)
        assert resp.status_code == 201
        body = resp.json()

        created_at = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        assert before <= created_at <= after

    def test_D_quantity_validation(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test D")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_buy_null = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-buy-null",
            "action": "buy",
            "quantity": None,
        })
        assert resp_buy_null.status_code == 422

        resp_buy_negative = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-buy-neg",
            "action": "buy",
            "quantity": -10.0,
        })
        assert resp_buy_negative.status_code == 422

        resp_buy_zero = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-buy-zero",
            "action": "buy",
            "quantity": 0.0,
        })
        assert resp_buy_zero.status_code == 422

        resp_hold_qty = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-hold-qty",
            "action": "hold",
            "quantity": 100.0,
        })
        assert resp_hold_qty.status_code == 422

        resp_hold_null = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-hold-ok",
            "action": "hold",
            "quantity": None,
        })
        assert resp_hold_null.status_code == 201

        resp_sell_ok = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-d-sell-ok",
            "action": "sell",
            "quantity": 50.0,
        })
        assert resp_sell_ok.status_code == 201

    def test_E_idempotency_same_content_returns_original(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test E")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        payload = {
            "simulation_session_id": session_id,
            "idempotency_key": "test-e-idem",
            "action": "buy",
            "quantity": 100.0,
            "rationale": "Same rationale",
        }
        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp1.status_code == 201
        original_id = resp1.json()["id"]

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["id"] == original_id

    def test_F_idempotency_conflict_different_content(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test F")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-f-conflict",
            "action": "buy",
            "quantity": 100.0,
        })
        assert resp1.status_code == 201

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-f-conflict",
            "action": "buy",
            "quantity": 200.0,
        })
        assert resp2.status_code == 409

    def test_G_correction_creates_new_event(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test G")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-g-orig",
            "action": "buy",
            "quantity": 100.0,
            "rationale": "Original decision",
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]

        resp_corr = client.post(f"/api/v1/human-decisions/{orig_id}/correct", headers=auth_headers, json={
            "action": "sell",
            "quantity": 50.0,
            "rationale": "Corrected decision",
            "correction_reason": "Changed mind after review",
        })
        assert resp_corr.status_code == 201
        corr_body = resp_corr.json()

        assert corr_body["id"] != orig_id
        assert corr_body["supersedes_event_id"] == orig_id
        assert corr_body["action"] == "sell"
        assert corr_body["quantity"] == 50.0
        assert corr_body["rationale"] == "Corrected decision"
        assert corr_body["metadata_json"]["correction_reason"] == "Changed mind after review"

    def test_H_correction_preserves_original(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test H")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-h-orig",
            "action": "buy",
            "quantity": 100.0,
            "rationale": "Original",
        })
        assert resp_orig.status_code == 201
        orig_body = resp_orig.json()
        orig_id = orig_body["id"]

        client.post(f"/api/v1/human-decisions/{orig_id}/correct", headers=auth_headers, json={
            "action": "hold",
            "quantity": None,
            "correction_reason": "Decided to wait",
        })

        resp_get = client.get(f"/api/v1/human-decisions/{orig_id}", headers=auth_headers)
        assert resp_get.status_code == 200
        fetched = resp_get.json()

        assert fetched["id"] == orig_id
        assert fetched["action"] == "buy"
        assert fetched["quantity"] == 100.0
        assert fetched["rationale"] == "Original"
        assert fetched["supersedes_event_id"] is None

    def test_I_ownership_enforcement(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test I")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-i-own",
            "action": "buy",
            "quantity": 100.0,
        })
        assert resp.status_code == 201
        event_id = resp.json()["id"]

        other_resp = client.post("/api/auth/register", json={
            "email": "other@alethra.io",
            "password": "otherpass123",
            "display_name": "Other User",
            "user_type": "individual",
        })
        other_login = client.post("/api/auth/login", json={
            "email": "other@alethra.io",
            "password": "otherpass123",
        })
        other_token = other_login.json()["access_token"]
        other_headers = {"Authorization": f"Bearer {other_token}"}

        resp_get = client.get(f"/api/v1/human-decisions/{event_id}", headers=other_headers)
        assert resp_get.status_code == 404

        resp_list = client.get("/api/v1/human-decisions", headers=other_headers)
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 0

    def test_J_execution_status_always_record_only(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Test J")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "test-j-status",
            "action": "buy",
            "quantity": 100.0,
        })
        assert resp.status_code == 201
        assert resp.json()["execution_status"] == "record_only"

        event_id = resp.json()["id"]
        resp_corr = client.post(f"/api/v1/human-decisions/{event_id}/correct", headers=auth_headers, json={
            "action": "sell",
            "quantity": 50.0,
            "correction_reason": "Changed strategy",
        })
        assert resp_corr.status_code == 201
        assert resp_corr.json()["execution_status"] == "record_only"

        resp_list = client.get(
            "/api/v1/human-decisions",
            headers=auth_headers,
            params={"simulation_session_id": session_id},
        )
        assert resp_list.status_code == 200
        events = resp_list.json()
        assert len(events) == 2
        for ev in events:
            assert ev["execution_status"] == "record_only"


class TestFiniteNumberValidation:
    """Schema-level NaN / +/-infinity rejection for quantity."""

    def test_buy_nan_quantity_rejected(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NaN Test")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        import json
        payload = {
            "simulation_session_id": str(session_id),
            "idempotency_key": "nan-key",
            "action": "buy",
            "quantity": "NaN",
        }
        resp = client.post(
            "/api/v1/human-decisions",
            headers=auth_headers,
            content=json.dumps(payload),
        )
        assert resp.status_code == 422

    def test_buy_positive_infinity_rejected(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Inf Test")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        import json
        payload = {
            "simulation_session_id": str(session_id),
            "idempotency_key": "inf-key",
            "action": "buy",
            "quantity": "Infinity",
        }
        resp = client.post(
            "/api/v1/human-decisions",
            headers=auth_headers,
            content=json.dumps(payload),
        )
        assert resp.status_code == 422

    def test_buy_negative_infinity_rejected(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NegInf Test")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        import json
        payload = {
            "simulation_session_id": str(session_id),
            "idempotency_key": "neginf-key",
            "action": "buy",
            "quantity": "-Infinity",
        }
        resp = client.post(
            "/api/v1/human-decisions",
            headers=auth_headers,
            content=json.dumps(payload),
        )
        assert resp.status_code == 422

    def test_buy_ordinary_positive_accepted(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Ordinary Test")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "ordinary-key",
            "action": "buy",
            "quantity": 42.5,
        })
        assert resp.status_code == 201
        assert resp.json()["quantity"] == 42.5


# ---------------------------------------------------------------------------
# Section 2A: Event / Audit Atomicity
# ---------------------------------------------------------------------------

class TestEventAuditAtomicity:
    """Verify that human decision create/correction is atomic w.r.t. events
    and audit records: one create -> exactly one event; idempotent retry
    produces no additional event; failure leaves no partial records."""

    def _count_events(self, client, auth_headers, session_id):
        resp = client.get(
            "/api/v1/human-decisions",
            headers=auth_headers,
            params={"simulation_session_id": session_id},
        )
        assert resp.status_code == 200
        return len(resp.json())

    def _count_audit_for_entity(self, client, auth_headers, entity_id):
        """AuditEvent rows whose entity_id matches the human-decision event."""
        resp = client.get(
            "/api/v1/experiments/audit-events",
            headers=auth_headers,
            params={"entity_ids": str(entity_id)},
        )
        # endpoint may or may not exist; fall back to direct DB check
        if resp.status_code == 200:
            return len(resp.json())
        return None  # signal: use DB path

    def test_K_create_produces_exactly_one_event(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Atomic K")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        before_count = self._count_events(client, auth_headers, session_id)
        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "atomic-k-001",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp.status_code == 201
        after_count = self._count_events(client, auth_headers, session_id)
        assert after_count == before_count + 1, (
            f"Expected exactly one new event, got {after_count - before_count}"
        )

    def test_L_idempotent_retry_produces_no_extra_event(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Atomic L")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        payload = {
            "simulation_session_id": session_id,
            "idempotency_key": "atomic-l-retry",
            "action": "sell",
            "quantity": 25.0,
            "rationale": "same",
        }
        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp1.status_code == 201
        original_id = resp1.json()["id"]

        count_after_first = self._count_events(client, auth_headers, session_id)

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["id"] == original_id

        count_after_retry = self._count_events(client, auth_headers, session_id)
        assert count_after_retry == count_after_first, (
            "Idempotent retry must not create additional events"
        )

    def test_M_correction_produces_exactly_one_new_event(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "Atomic M")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "atomic-m-orig",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]
        count_before = self._count_events(client, auth_headers, session_id)

        resp_corr = client.post(
            f"/api/v1/human-decisions/{orig_id}/correct",
            headers=auth_headers,
            json={"action": "hold", "quantity": None, "correction_reason": "rethink"},
        )
        assert resp_corr.status_code == 201
        count_after = self._count_events(client, auth_headers, session_id)
        assert count_after == count_before + 1, (
            "Correction must produce exactly one new event"
        )

    def test_N_no_partial_records_on_conflict(self, client, auth_headers):
        """A 409 conflict must not leave behind any event rows."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Atomic N")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "atomic-n-conflict",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp1.status_code == 201
        count_before = self._count_events(client, auth_headers, session_id)

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "atomic-n-conflict",
            "action": "sell",
            "quantity": 99.0,
        })
        assert resp2.status_code == 409
        count_after = self._count_events(client, auth_headers, session_id)
        assert count_after == count_before, (
            "409 conflict must not create any event rows"
        )


# ---------------------------------------------------------------------------
# Section 2B: Database Idempotency (unique constraint)
# ---------------------------------------------------------------------------

class TestDatabaseIdempotency:
    """Directly exercise the DB-level unique constraint
    uq_human_decision_owner_session_key."""

    def test_O_unique_constraint_rejects_duplicate(self, client, auth_headers):
        from app.models.human_decision import HumanDecisionEvent
        from app.database import get_db
        from tests.conftest import TestSession, override_get_db
        import uuid as _uuid

        exp_id, version_id = _create_experiment(client, auth_headers, "DB-Idem O")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "db-constraint-key",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp.status_code == 201
        owner_id = _uuid.UUID(resp.json()["owner_id"])
        session_uuid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        version_uuid = _uuid.UUID(version_id) if isinstance(version_id, str) else version_id

        # Direct DB insert with same (owner, session, key)
        db = TestSession()
        try:
            from app.models.human_decision import HumanDecisionAction, HumanDecisionExecutionStatus
            dup = HumanDecisionEvent(
                id=_uuid.uuid4(),
                owner_id=owner_id,
                simulation_session_id=session_uuid,
                idempotency_key="db-constraint-key",
                action=HumanDecisionAction.buy,
                quantity=10.0,
                server_strategy_version_id=version_uuid,
                server_instrument_id="DEMO-ALPHA",
                server_step_number=1,
                server_snapshot_date="2024-01-02",
                server_data_cutoff_at="2024-01-31",
                execution_status=HumanDecisionExecutionStatus.record_only,
            )
            db.add(dup)
            from sqlalchemy.exc import IntegrityError
            with pytest.raises(IntegrityError):
                db.commit()
        finally:
            db.rollback()
            db.close()

    def test_P_same_key_different_session_allowed(self, client, auth_headers):
        """The unique constraint scopes to (owner, session, key).
        Same key in a DIFFERENT session must succeed."""
        exp_id1, version_id1 = _create_experiment(client, auth_headers, "DB-Scoped P1")
        _lock_experiment(client, auth_headers, exp_id1)
        sid1 = _create_session(client, auth_headers, version_id1)

        exp_id2, version_id2 = _create_experiment(client, auth_headers, "DB-Scoped P2")
        _lock_experiment(client, auth_headers, exp_id2)
        sid2 = _create_session(client, auth_headers, version_id2)

        payload_template = {
            "idempotency_key": "shared-key-across-sessions",
            "action": "hold",
            "quantity": None,
        }

        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            **payload_template, "simulation_session_id": sid1,
        })
        assert resp1.status_code == 201

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            **payload_template, "simulation_session_id": sid2,
        })
        assert resp2.status_code == 201
        assert resp2.json()["id"] != resp1.json()["id"]


# ---------------------------------------------------------------------------
# Section 2C: No Economic Side Effects
# ---------------------------------------------------------------------------

class TestNoEconomicSideEffects:
    """Creating, retrying, or correcting a human decision must NOT advance
    the simulation session, create orders/fills, or move portfolio cash."""

    def _snapshot_session(self, client, auth_headers, session_id):
        """Fetch session state via the simulation endpoint."""
        resp = client.get(
            f"/api/v1/simulations/{session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        return {
            "observed_days": body.get("observed_days"),
            "total_decisions": body.get("total_decisions"),
            "status": body.get("status"),
            "current_snapshot_date": body.get("current_snapshot_date"),
            "data_cutoff_at": body.get("data_cutoff_at"),
            "initial_virtual_cash": body.get("initial_virtual_cash"),
        }

    def _count_orders_fills(self, session_id):
        """Direct DB count of PaperOrder / PaperFill linked to session."""
        import uuid as _uuid
        from app.models.paper_portfolio import PaperPortfolio, PaperOrder, PaperFill
        from tests.conftest import TestSession
        sid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        db = TestSession()
        try:
            portfolios = db.query(PaperPortfolio).filter(
                PaperPortfolio.simulation_session_id == sid
            ).all()
            order_count = 0
            fill_count = 0
            for p in portfolios:
                order_count += db.query(PaperOrder).filter(
                    PaperOrder.portfolio_id == p.id
                ).count()
                for o in db.query(PaperOrder).filter(PaperOrder.portfolio_id == p.id).all():
                    fill_count += db.query(PaperFill).filter(
                        PaperFill.paper_order_id == o.id
                    ).count()
            return order_count, fill_count
        finally:
            db.close()

    def _portfolio_cash(self, session_id):
        import uuid as _uuid
        from app.models.paper_portfolio import PaperPortfolio
        from tests.conftest import TestSession
        sid = _uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        db = TestSession()
        try:
            portfolios = db.query(PaperPortfolio).filter(
                PaperPortfolio.simulation_session_id == sid
            ).all()
            return [(p.id, p.current_virtual_cash) for p in portfolios]
        finally:
            db.close()

    def test_Q_buy_creates_no_orders_or_fills(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NoEcon Q")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        snap_before = self._snapshot_session(client, auth_headers, session_id)
        orders_before, fills_before = self._count_orders_fills(session_id)
        cash_before = self._portfolio_cash(session_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "noecon-buy",
            "action": "buy",
            "quantity": 500.0,
            "rationale": "test no economic effect",
        })
        assert resp.status_code == 201

        snap_after = self._snapshot_session(client, auth_headers, session_id)
        orders_after, fills_after = self._count_orders_fills(session_id)
        cash_after = self._portfolio_cash(session_id)

        # Session state must not advance
        assert snap_after["observed_days"] == snap_before["observed_days"]
        assert snap_after["status"] == snap_before["status"]
        assert snap_after["current_snapshot_date"] == snap_before["current_snapshot_date"]
        assert snap_after["data_cutoff_at"] == snap_before["data_cutoff_at"]
        # No orders / fills created
        assert orders_after == orders_before
        assert fills_after == fills_before
        # Cash unchanged
        assert cash_after == cash_before

    def test_R_sell_creates_no_orders_or_fills(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NoEcon R")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        snap_before = self._snapshot_session(client, auth_headers, session_id)
        orders_before, fills_before = self._count_orders_fills(session_id)
        cash_before = self._portfolio_cash(session_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "noecon-sell",
            "action": "sell",
            "quantity": 100.0,
        })
        assert resp.status_code == 201

        snap_after = self._snapshot_session(client, auth_headers, session_id)
        orders_after, fills_after = self._count_orders_fills(session_id)
        cash_after = self._portfolio_cash(session_id)

        assert snap_after["observed_days"] == snap_before["observed_days"]
        assert snap_after["status"] == snap_before["status"]
        assert orders_after == orders_before
        assert fills_after == fills_before
        assert cash_after == cash_before

    def test_S_hold_creates_no_economic_change(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NoEcon S")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        snap_before = self._snapshot_session(client, auth_headers, session_id)
        orders_before, fills_before = self._count_orders_fills(session_id)
        cash_before = self._portfolio_cash(session_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "noecon-hold",
            "action": "hold",
            "quantity": None,
        })
        assert resp.status_code == 201

        snap_after = self._snapshot_session(client, auth_headers, session_id)
        orders_after, fills_after = self._count_orders_fills(session_id)
        cash_after = self._portfolio_cash(session_id)

        assert snap_after["observed_days"] == snap_before["observed_days"]
        assert snap_after["total_decisions"] == snap_before["total_decisions"]
        assert snap_after["status"] == snap_before["status"]
        assert orders_after == orders_before
        assert fills_after == fills_before
        assert cash_after == cash_before

    def test_T_retry_creates_no_extra_economic_effect(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NoEcon T")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        payload = {
            "simulation_session_id": session_id,
            "idempotency_key": "noecon-retry",
            "action": "buy",
            "quantity": 10.0,
        }
        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp1.status_code == 201

        snap_before = self._snapshot_session(client, auth_headers, session_id)
        cash_before = self._portfolio_cash(session_id)

        # Idempotent retry
        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp2.status_code == 201
        assert resp2.json()["id"] == resp1.json()["id"]

        snap_after = self._snapshot_session(client, auth_headers, session_id)
        cash_after = self._portfolio_cash(session_id)

        assert snap_after == snap_before
        assert cash_after == cash_before

    def test_U_correction_creates_no_economic_change(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "NoEcon U")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "noecon-correct-orig",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]

        snap_before = self._snapshot_session(client, auth_headers, session_id)
        orders_before, fills_before = self._count_orders_fills(session_id)
        cash_before = self._portfolio_cash(session_id)

        resp_corr = client.post(
            f"/api/v1/human-decisions/{orig_id}/correct",
            headers=auth_headers,
            json={"action": "sell", "quantity": 5.0, "correction_reason": "no econ"},
        )
        assert resp_corr.status_code == 201

        snap_after = self._snapshot_session(client, auth_headers, session_id)
        orders_after, fills_after = self._count_orders_fills(session_id)
        cash_after = self._portfolio_cash(session_id)

        assert snap_after["observed_days"] == snap_before["observed_days"]
        assert snap_after["status"] == snap_before["status"]
        assert snap_after["total_decisions"] == snap_before["total_decisions"]
        assert orders_after == orders_before
        assert fills_after == fills_before
        assert cash_after == cash_before


# ---------------------------------------------------------------------------
# Section 2D: Cross-User Write Protection
# ---------------------------------------------------------------------------

class TestCrossUserWriteProtection:
    """A user must NOT be able to create decisions against another user's
    session, nor correct another user's events."""

    def _other_user_headers(self, client):
        other_resp = client.post("/api/auth/register", json={
            "email": "other-writer@alethra.io",
            "password": "otherpass123",
            "display_name": "Other Writer",
            "user_type": "individual",
        })
        other_login = client.post("/api/auth/login", json={
            "email": "other-writer@alethra.io",
            "password": "otherpass123",
        })
        other_token = other_login.json()["access_token"]
        return {"Authorization": f"Bearer {other_token}"}

    def test_V_cannot_create_against_other_session(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "CrossW V")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        other_headers = self._other_user_headers(client)

        resp = client.post("/api/v1/human-decisions", headers=other_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "cross-user-write",
            "action": "buy",
            "quantity": 10.0,
        })
        # Must be 403 or 404 — not 201
        assert resp.status_code in (403, 404), (
            f"Expected 403/404, got {resp.status_code}"
        )

        # Verify no event was created under the original user's session
        resp_list = client.get(
            "/api/v1/human-decisions",
            headers=auth_headers,
            params={"simulation_session_id": session_id},
        )
        assert resp_list.status_code == 200
        assert len(resp_list.json()) == 0, (
            "Cross-user write must not create events in victim session"
        )

    def test_W_cannot_correct_other_users_event(self, client, auth_headers):
        exp_id, version_id = _create_experiment(client, auth_headers, "CrossW W")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "cross-user-correct-orig",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]

        other_headers = self._other_user_headers(client)

        resp_corr = client.post(
            f"/api/v1/human-decisions/{orig_id}/correct",
            headers=other_headers,
            json={"action": "sell", "quantity": 5.0, "correction_reason": "unauthorized"},
        )
        assert resp_corr.status_code in (403, 404), (
            f"Expected 403/404, got {resp_corr.status_code}"
        )

        # Verify no correction event was created
        resp_list = client.get(
            "/api/v1/human-decisions",
            headers=auth_headers,
            params={"simulation_session_id": session_id},
        )
        assert resp_list.status_code == 200
        events = resp_list.json()
        assert len(events) == 1, (
            "Cross-user correction must not create additional events"
        )
        assert events[0]["id"] == orig_id
        assert events[0]["supersedes_event_id"] is None

    def test_X_forbidden_write_creates_no_audit_records(self, client, auth_headers):
        """Forbidden cross-user attempts must not leave audit or event rows."""
        from app.models.audit_event import AuditEvent
        from tests.conftest import TestSession

        exp_id, version_id = _create_experiment(client, auth_headers, "CrossW X")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        db_before = TestSession()
        audit_count_before = db_before.query(AuditEvent).count()
        db_before.close()

        other_headers = self._other_user_headers(client)

        # Attempt to create against another's session
        client.post("/api/v1/human-decisions", headers=other_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "cross-audit-check",
            "action": "buy",
            "quantity": 1.0,
        })

        db_after = TestSession()
        audit_count_after = db_after.query(AuditEvent).count()
        db_after.close()

        assert audit_count_after == audit_count_before, (
            "Forbidden cross-user write must not create audit records"
        )


# ---------------------------------------------------------------------------
# Section 3: Trust Context — server fields cannot be overridden
# ---------------------------------------------------------------------------

class TestTrustContext:
    """Verify that client-supplied server_* fields are ignored or rejected
    by the request schema."""

    def test_Y_server_fields_not_in_create_schema(self, client, auth_headers):
        """The HumanDecisionCreate schema must not accept server_* fields.
        Sending them should be silently ignored (Pydantic extra='ignore')
        or rejected (extra='forbid')."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Trust Y")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "trust-y-override",
            "action": "buy",
            "quantity": 10.0,
            # Attempt to override server-derived fields
            "server_strategy_version_id": "00000000-0000-0000-0000-000000000000",
            "server_instrument_id": "FAKE-INSTRUMENT",
            "server_step_number": 9999,
            "server_snapshot_date": "1999-01-01",
            "server_data_cutoff_at": "1999-12-31",
            "execution_status": "executed",
        })
        # Must either reject (422) or ignore (201 with server values)
        if resp.status_code == 201:
            body = resp.json()
            # Server values must NOT match the spoofed values
            assert body["server_strategy_version_id"] == version_id
            assert body["server_instrument_id"] == "DEMO-ALPHA"
            assert body["server_step_number"] != 9999
            assert body["server_snapshot_date"] != "1999-01-01"
            assert body["execution_status"] == "record_only"
        else:
            assert resp.status_code == 422, (
                f"Expected 201 (ignore extra) or 422 (reject), got {resp.status_code}"
            )

    def test_Z_correction_ignores_server_fields(self, client, auth_headers):
        """HumanDecisionCorrect schema must not accept server_* fields."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Trust Z")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "trust-z-orig",
            "action": "buy",
            "quantity": 10.0,
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]

        resp_corr = client.post(
            f"/api/v1/human-decisions/{orig_id}/correct",
            headers=auth_headers,
            json={
                "action": "sell",
                "quantity": 5.0,
                "correction_reason": "trust check",
                "server_strategy_version_id": "00000000-0000-0000-0000-000000000000",
                "server_instrument_id": "SPOOFED",
                "execution_status": "filled",
            },
        )
        if resp_corr.status_code == 201:
            body = resp_corr.json()
            assert body["server_strategy_version_id"] == version_id
            assert body["server_instrument_id"] == "DEMO-ALPHA"
            assert body["execution_status"] == "record_only"
        else:
            assert resp_corr.status_code == 422


# ---------------------------------------------------------------------------
# Section 4: Audit Atomicity — decision + audit in same transaction
# ---------------------------------------------------------------------------

class TestAuditAtomicity:
    """Verify that human decision creation/correction and audit event emission
    happen atomically within the same database transaction."""

    def test_AA_create_emits_audit_in_same_transaction(self, client, auth_headers):
        """Creating a human decision must emit an audit event atomically.
        If the decision is committed, the audit must also be committed.
        If the decision fails, no audit should exist."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AA")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        db_before = TestSession()
        audit_count_before = db_before.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision"
        ).count()
        db_before.close()

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "audit-aa-create",
            "action": "buy",
            "quantity": 100.0,
            "rationale": "atomic audit test",
        })
        assert resp.status_code == 201, resp.text
        decision_id = resp.json()["id"]

        db_after = TestSession()
        audit_events = db_after.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.entity_id == decision_id,
            AuditEvent.event_type == "human_decision_created",
        ).all()
        db_after.close()

        assert len(audit_events) == 1, (
            f"Expected exactly 1 audit event for created decision, got {len(audit_events)}"
        )
        audit = audit_events[0]
        assert audit.actor_type == "human"
        assert audit.payload_json is not None
        assert audit.payload_json["action"] == "buy"
        assert audit.payload_json["quantity"] == 100.0
        assert audit.payload_json["idempotency_key"] == "audit-aa-create"
        assert audit.recorded_before_outcome is False

    def test_AB_correct_emits_audit_in_same_transaction(self, client, auth_headers):
        """Correcting a human decision must emit an audit event atomically."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AB")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "audit-ab-orig",
            "action": "buy",
            "quantity": 50.0,
        })
        assert resp_orig.status_code == 201
        orig_id = resp_orig.json()["id"]

        db_before = TestSession()
        audit_count_before = db_before.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.event_type == "human_decision_corrected",
        ).count()
        db_before.close()

        resp_corr = client.post(
            f"/api/v1/human-decisions/{orig_id}/correct",
            headers=auth_headers,
            json={
                "action": "sell",
                "quantity": 25.0,
                "correction_reason": "atomic audit correction test",
            },
        )
        assert resp_corr.status_code == 201, resp_corr.text
        correction_id = resp_corr.json()["id"]

        db_after = TestSession()
        audit_events = db_after.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.entity_id == correction_id,
            AuditEvent.event_type == "human_decision_corrected",
        ).all()
        db_after.close()

        assert len(audit_events) == 1, (
            f"Expected exactly 1 audit event for correction, got {len(audit_events)}"
        )
        audit = audit_events[0]
        assert audit.payload_json is not None
        assert audit.payload_json["action"] == "sell"
        assert audit.payload_json["quantity"] == 25.0
        assert audit.payload_json["supersedes_event_id"] == orig_id
        assert audit.payload_json["correction_reason"] == "atomic audit correction test"

    def test_AC_idempotent_retry_no_extra_audit(self, client, auth_headers):
        """Retrying with same idempotency key must not create extra audit events."""
        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AC")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        payload = {
            "simulation_session_id": session_id,
            "idempotency_key": "audit-ac-retry",
            "action": "hold",
            "quantity": None,
        }

        resp1 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp1.status_code == 201
        decision_id = resp1.json()["id"]

        resp2 = client.post("/api/v1/human-decisions", headers=auth_headers, json=payload)
        assert resp2.status_code in (200, 201), "Idempotent retry should return 200 or 201"
        assert resp2.json()["id"] == decision_id

        db = TestSession()
        audit_events = db.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.entity_id == decision_id,
            AuditEvent.event_type == "human_decision_created",
        ).all()
        db.close()

        assert len(audit_events) == 1, (
            f"Idempotent retry must not create extra audit events, got {len(audit_events)}"
        )

    def test_AD_audit_and_decision_same_transaction(self, client, auth_headers):
        """Verify that decision and audit are in the same transaction by checking
        that both exist after successful commit."""
        from uuid import UUID
        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AD")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "audit-ad-txn",
            "action": "buy",
            "quantity": 75.0,
        })
        assert resp.status_code == 201
        decision_id = UUID(resp.json()["id"])

        db = TestSession()
        decision = db.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.id == decision_id
        ).first()
        audit = db.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision",
            AuditEvent.entity_id == str(decision_id),
        ).first()
        db.close()

        assert decision is not None, "Decision must exist after commit"
        assert audit is not None, "Audit must exist after commit"
        assert decision.action.value == "buy"
        assert audit.event_type == "human_decision_created"

    def test_AE_create_audit_failure_rollback(self, client, auth_headers, monkeypatch):
        """When audit emission fails during create, both decision and audit must rollback."""
        from app.api import human_decisions as hd_module
        from app.services import emit_event as real_emit_event

        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AE")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        db_before = TestSession()
        decision_count_before = db_before.query(HumanDecisionEvent).count()
        audit_count_before = db_before.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision"
        ).count()
        db_before.close()

        emit_called = []

        def failing_emit_event(db, **kwargs):
            result = real_emit_event(db, **kwargs)
            emit_called.append(True)
            raise RuntimeError("Simulated audit failure after emit")

        monkeypatch.setattr(hd_module, "emit_event", failing_emit_event)

        with pytest.raises(RuntimeError, match="Simulated audit failure after emit"):
            client.post("/api/v1/human-decisions", headers=auth_headers, json={
                "simulation_session_id": session_id,
                "idempotency_key": "audit-ae-failure",
                "action": "buy",
                "quantity": 100.0,
            })

        assert len(emit_called) == 1, "emit_event must be called before failure"

        db_after = TestSession()
        decision_count_after = db_after.query(HumanDecisionEvent).count()
        audit_count_after = db_after.query(AuditEvent).filter(
            AuditEvent.entity_type == "human_decision"
        ).count()
        db_after.close()

        assert decision_count_after == decision_count_before, (
            "No decision should persist after audit failure"
        )
        assert audit_count_after == audit_count_before, (
            "No audit should persist after audit failure"
        )

    def test_AF_correct_audit_failure_rollback(self, client, auth_headers, monkeypatch):
        """When audit emission fails during correction, original must be unchanged
        and correction + audit must rollback."""
        from uuid import UUID
        from app.api import human_decisions as hd_module
        from app.services import emit_event as real_emit_event

        exp_id, version_id = _create_experiment(client, auth_headers, "Audit AF")
        _lock_experiment(client, auth_headers, exp_id)
        session_id = _create_session(client, auth_headers, version_id)

        resp_orig = client.post("/api/v1/human-decisions", headers=auth_headers, json={
            "simulation_session_id": session_id,
            "idempotency_key": "audit-af-orig",
            "action": "buy",
            "quantity": 50.0,
        })
        assert resp_orig.status_code == 201
        orig_id = UUID(resp_orig.json()["id"])

        db_before = TestSession()
        orig_decision = db_before.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.id == orig_id
        ).first()
        orig_audit = db_before.query(AuditEvent).filter(
            AuditEvent.entity_id == str(orig_id),
            AuditEvent.event_type == "human_decision_created",
        ).first()
        correction_count_before = db_before.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.supersedes_event_id == orig_id
        ).count()
        db_before.close()

        assert orig_decision is not None, "Original decision must exist"
        assert orig_audit is not None, "Original audit must exist"

        emit_called = []

        def failing_emit_event(db, **kwargs):
            result = real_emit_event(db, **kwargs)
            emit_called.append(True)
            raise RuntimeError("Simulated audit failure during correction")

        monkeypatch.setattr(hd_module, "emit_event", failing_emit_event)

        with pytest.raises(RuntimeError, match="Simulated audit failure during correction"):
            client.post(
                f"/api/v1/human-decisions/{orig_id}/correct",
                headers=auth_headers,
                json={
                    "action": "sell",
                    "quantity": 25.0,
                    "correction_reason": "should fail",
                },
            )

        assert len(emit_called) == 1, "emit_event must be called before failure"

        db_after = TestSession()
        orig_decision_after = db_after.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.id == orig_id
        ).first()
        orig_audit_after = db_after.query(AuditEvent).filter(
            AuditEvent.entity_id == str(orig_id),
            AuditEvent.event_type == "human_decision_created",
        ).first()
        correction_count_after = db_after.query(HumanDecisionEvent).filter(
            HumanDecisionEvent.supersedes_event_id == orig_id
        ).count()
        correction_audit_count = db_after.query(AuditEvent).filter(
            AuditEvent.event_type == "human_decision_corrected"
        ).count()
        db_after.close()

        assert orig_decision_after is not None, "Original decision must be unchanged"
        assert orig_decision_after.action.value == orig_decision.action.value
        assert orig_decision_after.quantity == orig_decision.quantity
        assert orig_audit_after is not None, "Original audit must be unchanged"
        assert correction_count_after == correction_count_before, (
            "No correction should persist after audit failure"
        )
        assert correction_audit_count == 0, (
            "No correction audit should persist after audit failure"
        )

