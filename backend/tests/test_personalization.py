"""Personalization contract: consent scope + dataset eligibility.

Capability C (a model trained on one owner's confirmed decisions) is not
implemented. These tests pin down the honest gate in front of it:

  * emptiness is refused with explicit, measured reasons
  * only a dedicated, scoped, revocable training consent unlocks events
  * superseded revisions and labels without a stated reason never count
  * owners are never mixed
  * future data (snapshot after the data cutoff) blocks the dataset
  * feature coverage is measured from stored bars, not assumed
"""
import uuid
from datetime import date, datetime

from tests.conftest import TestSession
from app.models.audit_event import AuditEvent
from app.models.human_decision import (
    HumanDecisionAction,
    HumanDecisionEvent,
    HumanDecisionExecutionStatus,
)
from app.models.market_data import MarketDataBar
from app.models.personalization import PersonalizationConsent
from app.models.user import User


def _current_user_id() -> uuid.UUID:
    db = TestSession()
    try:
        return db.query(User).filter(User.email == "test@alethra.io").one().id
    finally:
        db.close()


def _create_experiment(client, headers, title="Personalization"):
    resp = client.post(
        "/api/v1/experiments/",
        headers=headers,
        json={
            "title": title,
            "research_type": "quantitative",
            "strategy_spec": {
                "template_type": "buy_and_hold",
                "asset_ids": ["US:AAPL"],
                "start_date": "2024-01-02",
                "end_date": "2024-01-31",
                "initial_cash": 100000,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["current_version_id"]


def _add_event(
    owner_id,
    version_id,
    *,
    action=HumanDecisionAction.buy,
    rationale="plan says enter",
    session_id=None,
    instrument="US:AAPL",
    snapshot="2024-01-10",
    cutoff="2024-01-31",
    supersedes=None,
    key=None,
    actor_type="human",
):
    db = TestSession()
    try:
        event = HumanDecisionEvent(
            owner_id=owner_id,
            simulation_session_id=session_id or uuid.uuid4(),
            idempotency_key=key or str(uuid.uuid4()),
            action=action,
            quantity=10.0 if action != HumanDecisionAction.hold else None,
            rationale=rationale,
            server_strategy_version_id=version_id,
            server_instrument_id=instrument,
            server_step_number=1,
            server_snapshot_date=snapshot,
            server_data_cutoff_at=cutoff,
            execution_status=HumanDecisionExecutionStatus.record_only,
            supersedes_event_id=supersedes,
            actor_type=actor_type,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event.id
    finally:
        db.close()


def _add_bar(instrument, bar_date):
    db = TestSession()
    try:
        db.add(
            MarketDataBar(
                instrument_id=instrument,
                provider_id="demo",
                bar_date=bar_date,
                bar_timestamp=datetime.combine(bar_date, datetime.min.time()),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1_000_000,
            )
        )
        db.commit()
    finally:
        db.close()


def _codes(eligibility):
    return {reason["code"] for reason in eligibility["reasons"]}


class TestEligibilityRefusal:

    def test_empty_dataset_refused_with_explicit_reasons(self, client, auth_headers):
        resp = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["eligible"] is False
        assert body["status"] == "NOT_ELIGIBLE"
        assert body["capability_status"] == "AVAILABLE"
        assert "NO_RECORDED_DECISIONS" in _codes(body)
        assert "CONSENT_MISSING" in _codes(body)
        assert body["dataset"]["total_recorded_events"] == 0
        assert body["dataset"]["eligible_events"] == 0
        # Undefined coverage is null, never a fabricated 0.0.
        assert body["dataset"]["feature_coverage"]["ratio"] is None
        assert body["consent"]["granted"] is False
        assert all(reason["message_zh"] for reason in body["reasons"])

    def test_capabilities_keep_the_three_classes_separate(self, client, auth_headers):
        resp = client.get("/api/v1/ml/capabilities", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        caps = {item["key"]: item for item in resp.json()["capabilities"]}

        assert set(caps) == {"market_model", "rule_check", "personalization"}
        assert caps["market_model"]["status"] == "AVAILABLE"
        assert caps["rule_check"]["status"] == "AVAILABLE"
        assert caps["personalization"]["status"] == "AVAILABLE"

    def test_future_snapshot_blocks_dataset(self, client, auth_headers):
        owner_id = _current_user_id()
        _, version_id = _create_experiment(client, auth_headers)
        _add_event(owner_id, uuid.UUID(version_id), snapshot="2024-02-05", cutoff="2024-01-31")
        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_id], "confirm": True},
        )

        body = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()

        assert "FUTURE_DATA_LEAKAGE" in _codes(body)
        assert body["dataset"]["future_leakage_events"] == 1
        assert body["eligible"] is False


class TestConsentContract:

    def test_consent_requires_confirmation_and_ownership(self, client, auth_headers):
        _, version_id = _create_experiment(client, auth_headers)

        unconfirmed = client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_id], "confirm": False},
        )
        assert unconfirmed.status_code == 400

        unknown = client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [str(uuid.uuid4())], "confirm": True},
        )
        assert unknown.status_code == 404

        empty = client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [], "confirm": True},
        )
        assert empty.status_code == 422

    def test_grant_records_scope_and_audit_event(self, client, auth_headers):
        _, version_id = _create_experiment(client, auth_headers)

        resp = client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_id], "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["consent"]["scope"]["strategy_version_ids"] == [version_id]
        assert body["eligibility"]["consent"]["granted"] is True

        db = TestSession()
        try:
            rows = db.query(PersonalizationConsent).all()
            assert len(rows) == 1
            assert rows[0].revoked_at is None
            events = (
                db.query(AuditEvent)
                .filter(AuditEvent.event_type == "personalization_consent_granted")
                .all()
            )
            assert len(events) == 1
        finally:
            db.close()

    def test_new_grant_replaces_previous_consent(self, client, auth_headers):
        _, version_a = _create_experiment(client, auth_headers, "Scope A")
        _, version_b = _create_experiment(client, auth_headers, "Scope B")

        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_a], "confirm": True},
        )
        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_b], "confirm": True},
        )

        body = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()
        assert body["consent"]["scope"]["strategy_version_ids"] == [version_b]

        db = TestSession()
        try:
            active = (
                db.query(PersonalizationConsent)
                .filter(PersonalizationConsent.revoked_at.is_(None))
                .all()
            )
            assert len(active) == 1
        finally:
            db.close()

    def test_revoke_drops_scope_and_returns_to_refusal(self, client, auth_headers):
        owner_id = _current_user_id()
        _, version_id = _create_experiment(client, auth_headers)
        _add_event(owner_id, uuid.UUID(version_id))
        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [version_id], "confirm": True},
        )

        before = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()
        assert before["dataset"]["eligible_events"] == 1

        revoked = client.delete(
            "/api/v1/ml/personalization/consent", headers=auth_headers
        )
        assert revoked.status_code == 200, revoked.text
        body = revoked.json()
        assert body["revoked"] == 1
        assert body["eligibility"]["consent"]["granted"] is False
        assert body["eligibility"]["dataset"]["eligible_events"] == 0
        assert "CONSENT_MISSING" in _codes(body["eligibility"])


class TestDatasetIntegrity:

    def test_scope_revisions_and_reasons_are_applied(self, client, auth_headers):
        owner_id = _current_user_id()
        _, version_a = _create_experiment(client, auth_headers, "In scope")
        _, version_b = _create_experiment(client, auth_headers, "Out of scope")
        version_a = uuid.UUID(version_a)
        version_b = uuid.UUID(version_b)
        session_id = uuid.uuid4()

        original = _add_event(owner_id, version_a, session_id=session_id, key="orig")
        _add_event(
            owner_id,
            version_a,
            session_id=session_id,
            supersedes=original,
            rationale="corrected after reviewing the bar",
            key="correction",
        )
        _add_event(owner_id, version_a, session_id=session_id, rationale=None, key="no-reason")
        _add_event(owner_id, version_a, action=HumanDecisionAction.sell, session_id=session_id, key="sell")
        _add_event(owner_id, version_b, session_id=uuid.uuid4(), key="other-strategy")

        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [str(version_a)], "confirm": True},
        )
        body = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()

        assert body["dataset"]["total_recorded_events"] == 5
        assert body["dataset"]["excluded"]["superseded_revisions"] == 1
        assert body["dataset"]["excluded"]["outside_consent_scope"] == 1
        assert body["dataset"]["excluded"]["missing_reason"] == 1
        assert body["dataset"]["eligible_events"] == 2
        assert body["dataset"]["action_counts"] == {"buy": 1, "sell": 1}

        strategies = {row["strategy_version_id"]: row for row in body["strategies"]}
        assert strategies[str(version_a)]["in_consent_scope"] is True
        assert strategies[str(version_b)]["in_consent_scope"] is False
        assert strategies[str(version_a)]["confirmed_decisions"] == 2

        codes = _codes(body)
        assert "INSUFFICIENT_CONFIRMED_DECISIONS" in codes
        assert "UNLABELLED_WITHOUT_REASON" in codes
        assert "OUTSIDE_CONSENT_SCOPE" in codes

    def test_other_owner_events_are_never_counted(self, client, auth_headers):
        owner_id = _current_user_id()
        _, version_id = _create_experiment(client, auth_headers)
        version_id = uuid.UUID(version_id)
        _add_event(owner_id, version_id, key="mine")

        other_owner = uuid.uuid4()
        db = TestSession()
        try:
            db.add(
                User(
                    id=other_owner,
                    email="other-person@alethra.io",
                    password_hash="x",
                )
            )
            db.commit()
        finally:
            db.close()
        _add_event(other_owner, version_id, key="theirs")

        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [str(version_id)], "confirm": True},
        )
        body = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()

        assert body["dataset"]["total_recorded_events"] == 1
        assert body["dataset"]["eligible_events"] == 1

    def test_feature_coverage_measured_from_stored_bars(self, client, auth_headers):
        owner_id = _current_user_id()
        _, version_id = _create_experiment(client, auth_headers)
        version_id = uuid.UUID(version_id)
        _add_event(owner_id, version_id, snapshot="2024-01-10", key="covered")
        _add_event(owner_id, version_id, snapshot="2024-01-11", key="missing")
        _add_bar("US:AAPL", date(2024, 1, 10))

        client.put(
            "/api/v1/ml/personalization/consent",
            headers=auth_headers,
            json={"strategy_version_ids": [str(version_id)], "confirm": True},
        )
        body = client.get(
            "/api/v1/ml/personalization/eligibility", headers=auth_headers
        ).json()

        coverage = body["dataset"]["feature_coverage"]
        assert coverage == {"covered": 1, "considered": 2, "ratio": 0.5}
        assert "INSUFFICIENT_FEATURE_COVERAGE" in _codes(body)

    def test_eligibility_requires_authentication(self, client):
        resp = client.get("/api/v1/ml/personalization/eligibility")
        assert resp.status_code in (401, 403)
