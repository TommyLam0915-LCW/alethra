"""Actor provenance is decided by the credential the server verified.

The paper-trading lanes used to accept a client-declared ``actor_type`` (or
treated every non-API-key request as human). These tests pin the repaired rule:

  * a password session is ``human`` (credential channel, not biometric proof)
  * an API key is ``agent``
  * anything else — a minted token with no ``amr`` claim, the passwordless
    candidate session — is ``unknown`` and never upgraded
  * a request body claiming ``actor_type`` cannot relabel the stored row
  * background/service events default to ``unknown`` unless a caller states
    its own provenance

They also check that the agent-strategy collector inherits the credential and
that the personalization dataset continues to refuse non-human history (run in
``test_decision_provenance.py``). Nothing here rewrites a historical row.
"""
import uuid

import pytest

from tests.conftest import TestSession
from tests.test_agent_strategy_slice import (
    INSTRUMENT,
    _create_locked_strategy,
    _enable_test_snapshot_authorization,
    _seed_bars,
    _zigzag,
)
from app.auth.api_keys import generate_api_key
from app.auth.jwt import create_access_token
from app.models.audit_event import AuditEvent
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    PaperDecisionActorType,
    PaperOrder,
    PaperPortfolio,
    decision_actor_type,
)
from app.models.user import User
from app.services import emit_event
from app.services.agent_strategy import AgentCandidate


ORDER_BODY = {
    "asset_id": "DEMO-ALPHA",
    "side": "BUY",
    "quantity": 1,
    "order_type": "market",
    "rationale": "provenance test entry",
    "expected_outcome": "checks the stored actor label",
    "self_reported_confidence": "low",
}


def _owner() -> User:
    db = TestSession()
    try:
        return db.query(User).filter(User.email == "test@alethra.io").one()
    finally:
        db.close()


def _api_key_headers(name: str = "provenance") -> dict:
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        _, raw_key = generate_api_key(db, user.id, name, scopes=["read", "write"])
    finally:
        db.close()
    return {"Authorization": f"Bearer {raw_key}"}


def _minted_token_headers() -> dict:
    db = TestSession()
    try:
        user = db.query(User).filter(User.email == "test@alethra.io").one()
        token = create_access_token(data={"sub": str(user.id)})
    finally:
        db.close()
    return {"Authorization": f"Bearer {token}"}


def _create_portfolio(client, headers, cash: float = 100000.0) -> str:
    resp = client.post(
        "/api/v1/paper-portfolio",
        headers=headers,
        json={"initial_virtual_cash": cash},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _stored_order(order_id: str) -> PaperOrder:
    db = TestSession()
    try:
        return db.query(PaperOrder).filter(PaperOrder.id == uuid.UUID(str(order_id))).one()
    finally:
        db.close()


def _stored_decision(decision_id: str) -> DecisionRecord:
    db = TestSession()
    try:
        return (
            db.query(DecisionRecord)
            .filter(DecisionRecord.id == uuid.UUID(str(decision_id)))
            .one()
        )
    finally:
        db.close()


def _audit(entity_type: str, entity_id: str, event_type: str) -> AuditEvent:
    db = TestSession()
    try:
        return (
            db.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == entity_type,
                AuditEvent.entity_id == str(entity_id),
                AuditEvent.event_type == event_type,
            )
            .one()
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Paper orders inherit the credential channel.
# ---------------------------------------------------------------------------


class TestPaperOrderProvenance:

    def test_password_session_order_is_human(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=auth_headers,
            json=ORDER_BODY,
        )
        assert resp.status_code == 201, resp.text
        order = resp.json()
        assert order["actor_type"] == "human"

        assert _stored_order(order["id"]).actor_type == "human"
        audit = _audit("paper_portfolio", portfolio_id, "order_created")
        assert audit.actor_type == "human"
        assert audit.payload_json["actor_type"] == "human"

        decision = _stored_decision(order["decision_id"])
        assert decision.actor_type is PaperDecisionActorType.human

    def test_api_key_order_is_agent(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        agent = _api_key_headers("order-agent")
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=agent,
            json={**ORDER_BODY, "idempotency_key": "prov-agent-order"},
        )
        assert resp.status_code == 201, resp.text
        order = resp.json()
        assert order["actor_type"] == "agent"

        assert _stored_order(order["id"]).actor_type == "agent"
        audit = _audit("paper_portfolio", portfolio_id, "order_created")
        assert audit.actor_type == "agent"
        assert audit.payload_json["actor_type"] == "agent"

        decision = _stored_decision(order["decision_id"])
        assert decision.actor_type is PaperDecisionActorType.agent

    def test_token_without_auth_method_is_unknown(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=_minted_token_headers(),
            json={**ORDER_BODY, "idempotency_key": "prov-minted-order"},
        )
        assert resp.status_code == 201, resp.text
        order = resp.json()
        assert order["actor_type"] == "unknown"

        assert _stored_order(order["id"]).actor_type == "unknown"
        assert _audit("paper_portfolio", portfolio_id, "order_created").actor_type == "unknown"
        assert (
            _stored_decision(order["decision_id"]).actor_type
            is PaperDecisionActorType.unknown
        )


# ---------------------------------------------------------------------------
# 2. A client body cannot choose its own provenance.
# ---------------------------------------------------------------------------


class TestBodyCannotRelabel:

    def test_api_key_decision_declaring_human_stays_agent(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
            headers=_api_key_headers("forge-human"),
            json={
                "actor_type": "human",
                "decision_type": "no_action",
                "rationale": "an automation claiming to be the owner",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["actor_type"] == "agent"
        assert _stored_decision(body["id"]).actor_type is PaperDecisionActorType.agent

        audit = _audit("decision_record", body["id"], "decision_recorded")
        assert audit.actor_type == "agent"
        assert audit.payload_json["actor_type"] == "agent"
        # The declaration is preserved as client data, not trusted as identity.
        assert audit.payload_json["client_declared_actor_type"] == "human"

    def test_password_decision_declaring_agent_stays_human(self, client, auth_headers):
        portfolio_id = _create_portfolio(client, auth_headers)
        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
            headers=auth_headers,
            json={
                "actor_type": "system",
                "decision_type": "no_action",
                "rationale": "an owner session declaring system provenance",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["actor_type"] == "human"
        assert _stored_decision(body["id"]).actor_type is PaperDecisionActorType.human

        audit = _audit("decision_record", body["id"], "decision_recorded")
        assert audit.actor_type == "human"
        assert audit.payload_json["client_declared_actor_type"] == "system"


# ---------------------------------------------------------------------------
# 3. Judgement acts are attributed to whoever judged.
# ---------------------------------------------------------------------------


class TestOutcomeProvenance:

    def test_outcome_evaluation_records_the_evaluating_credential(
        self, client, auth_headers
    ):
        portfolio_id = _create_portfolio(client, auth_headers)
        decision = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions",
            headers=auth_headers,
            json={
                "actor_type": "human",
                "decision_type": "no_action",
                "rationale": "wait for a clearer entry",
            },
        )
        assert decision.status_code == 201, decision.text
        decision_id = decision.json()["id"]

        resp = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions/{decision_id}/outcome",
            headers=_api_key_headers("outcome-agent"),
            json={
                "actual_outcome_summary": "price moved sideways",
                "hypothesis_status": "evidence_insufficient",
                "evaluation_method": "paper replay",
            },
        )
        assert resp.status_code == 201, resp.text

        # The decision keeps its original author; the judgement act carries the
        # credential that wrote it.
        assert _stored_decision(decision_id).actor_type is PaperDecisionActorType.human
        audit = _audit("decision_record", decision_id, "outcome_evaluated")
        assert audit.actor_type == "agent"
        assert audit.payload_json["actor_type"] == "agent"


# ---------------------------------------------------------------------------
# 4. Background paths fail closed unless they state their identity.
# ---------------------------------------------------------------------------


class TestBackgroundDefaults:

    def test_emit_event_defaults_to_unknown(self):
        db = TestSession()
        try:
            event = emit_event(
                db,
                entity_type="simulation_session",
                entity_id="background-1",
                event_type="simulation_completed",
            )
            db.commit()
            assert event.actor_type == "unknown"
        finally:
            db.close()

    def test_emit_event_preserves_an_explicit_system_label(self):
        db = TestSession()
        try:
            event = emit_event(
                db,
                entity_type="simulation_session",
                entity_id="background-2",
                event_type="simulation_completed",
                actor_type="system",
            )
            db.commit()
            assert event.actor_type == "system"
        finally:
            db.close()

    def test_paper_order_orm_default_is_unknown(self, client, auth_headers):
        owner = _owner()
        db = TestSession()
        try:
            portfolio = PaperPortfolio(
                owner_id=owner.id,
                initial_virtual_cash=100.0,
                current_virtual_cash=100.0,
            )
            db.add(portfolio)
            db.flush()
            order = PaperOrder(
                portfolio_id=portfolio.id,
                asset_id="DEMO-ALPHA",
                side=OrderSide.BUY,
                quantity=1,
            )
            db.add(order)
            db.commit()
            db.refresh(order)
            assert order.actor_type == "unknown"
        finally:
            db.close()

    def test_unverified_channel_maps_to_unknown_decision_actor(self):
        assert decision_actor_type("human") is PaperDecisionActorType.human
        assert decision_actor_type("agent") is PaperDecisionActorType.agent
        # ``system`` is never inferred from a credential; only a background
        # writer states it explicitly.
        assert decision_actor_type("system") is PaperDecisionActorType.unknown
        assert decision_actor_type("") is PaperDecisionActorType.unknown


# ---------------------------------------------------------------------------
# 5. The agent-strategy collector inherits the collecting credential.
# ---------------------------------------------------------------------------


class TestAgentStrategyCollectionProvenance:

    def _collect(self, client, headers, version_id):
        resp = client.post(
            "/api/v1/agent-strategy/candidates/collect",
            headers=headers,
            json={"strategy_version_id": str(version_id)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] > 0, body
        return body["candidates"]

    def _stored_actor_types(self, candidate_ids):
        db = TestSession()
        try:
            return {
                row.created_by_actor_type
                for row in db.query(AgentCandidate).filter(
                    AgentCandidate.id.in_([uuid.UUID(c) for c in candidate_ids])
                )
            }
        finally:
            db.close()

    def test_api_key_collector_creates_agent_candidates(
        self, client, auth_headers, monkeypatch
    ):
        _enable_test_snapshot_authorization(monkeypatch)
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers, title="agent-collect")
        candidates = self._collect(client, _api_key_headers("collect-agent"), version_id)
        ids = [c["id"] for c in candidates]
        assert self._stored_actor_types(ids) == {"agent"}

    def test_password_collector_creates_human_candidates(
        self, client, auth_headers, monkeypatch
    ):
        _enable_test_snapshot_authorization(monkeypatch)
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers, title="human-collect")
        candidates = self._collect(client, auth_headers, version_id)
        ids = [c["id"] for c in candidates]
        assert self._stored_actor_types(ids) == {"human"}

    def test_unattributed_collector_creates_unknown_candidates(
        self, client, auth_headers, monkeypatch
    ):
        _enable_test_snapshot_authorization(monkeypatch)
        _seed_bars(INSTRUMENT, _zigzag())
        version_id = _create_locked_strategy(client, auth_headers, title="unknown-collect")
        candidates = self._collect(client, _minted_token_headers(), version_id)
        ids = [c["id"] for c in candidates]
        assert self._stored_actor_types(ids) == {"unknown"}
