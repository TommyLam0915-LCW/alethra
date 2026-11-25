"""The agent-model paper lane, proven through the real door and read back.

Round-22 item 1 asks for one thing: an owner picks a skill account and a trained
job, approves that artifact *for that account*, and one paper decision runs —
with the server resolving every identity from the rows that hold it, and the
result verifiable afterwards through the formal reads rather than an HTTP 2xx.

A 409 from this door means one of two different things, and the two are kept
apart here rather than merged into a status code: a precondition refusal writes
nothing, while a refused *result* — the model said take and the book or the
shared policy would not — commits its ledger row and then refuses. See
``TestDurableRefusals``.

So nothing here mocks the loader, the approval decision or the persistence. Every
node builds the real chain (bars → locked spec → candidates → outcomes →
snapshot → consent → training job → activation approval) against the isolated
SQLite database and calls
``POST /agent-strategy/training/jobs/{id}/paper-execution``. Each refusal is then
confirmed by an independent read of the paper tables, not by the status code
alone: a refusal that leaves ``paper_orders`` empty is a refusal that never
created an executable intent.

The two fixture premises were measured, not assumed. The engineered dip→flat→rise
tail makes the newest candidate score ``take`` (p=1.0 against a 0.5 threshold);
the unmodified regime series scores ``skip`` (p≈0.002). Every node asserts which
side of the threshold its own chain landed on, so a model that stops agreeing
fails loudly instead of quietly exercising the wrong branch.
"""

import ast
import inspect
import threading
import time
import uuid

import pytest
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.api.conflicts import (
    AGENT_PAPER_EXECUTION_INTENT,
    PAPER_ORDER_IDEMPOTENCY,
    expected_unique_violation,
)
from app.engine.fill_math import quantity_precision_for
from app.models.agent_paper_execution import AgentPaperExecution
from app.models.agent_strategy import (
    AgentCandidate,
    AgentModelActivationApproval,
)
from app.models.billing import UsageRecord
from app.models.decision_label import DecisionLabel
from app.models.human_decision import HumanDecisionEvent
from app.models.kill_switch import KillSwitchScope
from app.models.paper_portfolio import (
    DecisionRecord,
    OrderSide,
    OrderStatus,
    PaperFill,
    PaperOrder,
    PaperPortfolio,
    PaperPosition,
)
from app.models.personal_model_selection import PersonalModelSelection
from app.services import agent_paper_execution as paper_service
from app.services import model_activation
from app.services import risk_policy
from app.services.agent_strategy import COMMISSION_RATE, EXECUTION_POLICY_VERSION, SLIPPAGE_RATE
from app.services.kill_switch import activate_kill_switch, deactivate_kill_switch
from tests.conftest import TestSession
from tests.postgresql_only import requires_postgresql
from tests.test_agent_strategy_slice import (
    EXPENSIVE,
    INSTRUMENT,
    _accept_consent,
    _create_locked_strategy,
    _enable_test_training_authorization,
    _login_other_owner,
    _owner_id,
    _regime_series,
    _seed_bars,
)

PAPER_ROUTE = "/api/v1/agent-strategy/training/jobs/{}/paper-execution"
LEDGER_ROUTE = "/api/v1/agent-strategy/training/jobs/{}/paper-executions"
INFERENCE_ROUTE = "/api/v1/agent-strategy/training/jobs/{}/inference"
ACTIVATION_ROUTE = "/api/v1/agent-strategy/training/jobs/{}/activation"

EMPTY_BOOK = {"decisions": 0, "orders": 0, "fills": 0, "positions": 0}


def take_series(base_days=170):
    """The measured ``take`` premise: a dip, a flat bottom, then a rise.

    The newest SMA cross fires on the way back up, which is the shape the
    forward-return label rewards; the plain regime series ends on a fresh dip and
    scores ``skip``.

    ``base_days`` changes the length of the series, which is what makes a second
    chain a second artifact: two chains on the same shape train byte-identical
    model and scaler files, however different their instruments and snapshots are.
    """
    base = _regime_series(base_days)
    last = base[-1]
    extra = (
        [last - 4 * i for i in range(1, 5)]
        + [last - 16 for _ in range(4)]
        + [last - 16 + 8 * i for i in range(1, 7)]
    )
    return [round(value, 4) for value in base + extra]


def _chain(client, headers, monkeypatch, tmp_path, *, instrument, closes, title):
    """Seed, lock, collect, label, freeze, consent and train. Returns the job."""
    _enable_test_training_authorization(monkeypatch, tmp_path)
    _seed_bars(instrument, closes)
    version_id = _create_locked_strategy(
        client, headers, instrument=instrument, title=title, window_days=len(closes)
    )
    body = {"strategy_version_id": str(version_id)}
    for route in (
        "/api/v1/agent-strategy/candidates/collect",
        "/api/v1/agent-strategy/candidates/outcomes",
    ):
        resp = client.post(route, headers=headers, json=body)
        assert resp.status_code == 200, (route, resp.text)
    snapshot = client.post(
        "/api/v1/agent-strategy/snapshots", headers=headers, json=body
    )
    assert snapshot.status_code == 200, snapshot.text
    assert _accept_consent(client, headers).status_code == 200
    job = client.post(
        "/api/v1/agent-strategy/training/jobs",
        headers=headers,
        json={"snapshot_id": snapshot.json()["id"]},
    )
    assert job.status_code == 200, job.text
    payload = job.json()
    assert payload["status"] == "completed", payload
    assert payload["artifact_checksum"], payload
    return {
        "job_id": payload["job_id"],
        "version_id": str(version_id),
        "snapshot_id": snapshot.json()["id"],
        "artifact_checksum": payload["artifact_checksum"],
        "dataset_checksum": payload["dataset_checksum"],
        "instrument": instrument,
    }


def _gate(client, headers, job_id):
    """The gate's own words: what an approval must echo, and what it now decides."""
    response = client.get(ACTIVATION_ROUTE.format(job_id), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _state(client, headers, job_id):
    return _gate(client, headers, job_id)["state"]


def _skill_unit(client, headers, *, cash=100000.0):
    resp = client.post(
        "/api/v1/paper-portfolio",
        headers=headers,
        json={"initial_virtual_cash": cash},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], float(resp.json()["current_virtual_cash"])


def _approve(client, headers, job_id, *, skill_unit_id=None):
    """Ask the gate what it needs, then answer it verbatim for this account.

    The four echoed fields come from ``GET .../activation`` so a helper that
    approved a different artifact could not pass. ``skill_unit_id=None`` records
    the job-only approval — the thing this lane must not read as permission for
    every account.
    """
    requirements = _gate(client, headers, job_id)["requirements"]
    body = {
        "artifact_checksum": requirements["artifact_checksum"],
        "policy_version": requirements["approval_policy_version"],
        "acknowledgement_text_version": requirements["acknowledgement_text_version"],
        "acknowledgement": requirements["acknowledgement_text_zh"],
    }
    if skill_unit_id is not None:
        body["skill_unit_id"] = skill_unit_id
    return client.put(ACTIVATION_ROUTE.format(job_id), headers=headers, json=body)


def _execute(client, headers, job, skill_unit_id, **claims):
    """Ask the door to trade. ``claims`` are the lies a caller might tell."""
    return client.post(
        PAPER_ROUTE.format(job),
        headers=headers,
        json={"skill_unit_id": skill_unit_id, **claims},
    )


def _refusal(response):
    assert response.status_code == 409, (response.status_code, response.text)
    return response.json()["detail"]


def _newest_candidate(version_id):
    db = TestSession()
    try:
        return (
            db.query(AgentCandidate)
            .filter(
                AgentCandidate.strategy_version_id == uuid.UUID(version_id)
            )
            .order_by(
                AgentCandidate.signal_date.desc(), AgentCandidate.created_at.desc()
            )
            .first()
        )
    finally:
        db.close()


def _book(portfolio_id):
    """Independent read of one skill account's paper book."""
    key = uuid.UUID(str(portfolio_id))
    db = TestSession()
    try:
        order_ids = [
            row.id
            for row in db.query(PaperOrder.id).filter(
                PaperOrder.portfolio_id == key
            )
        ]
        fills = (
            db.query(func.count(PaperFill.id))
            .filter(PaperFill.paper_order_id.in_(order_ids))
            .scalar()
            if order_ids
            else 0
        )
        return {
            "decisions": db.query(func.count(DecisionRecord.id))
            .filter(DecisionRecord.paper_portfolio_id == key)
            .scalar(),
            "orders": len(order_ids),
            "fills": fills,
            "positions": db.query(func.count(PaperPosition.id))
            .filter(PaperPosition.portfolio_id == key)
            .scalar(),
        }
    finally:
        db.close()


def _ledger_rows(*, job_id=None, approval_id=None, skill_unit_id=None):
    """The ledger rows one node established, read back on the keys it named.

    Every count in this file was a whole-table count, which is a claim about the
    other nodes in the module as much as about this one: the same disposable
    database carries whatever an earlier node committed (card A's PostgreSQL run
    went red on rows its own press never wrote), and on that lane 「the table is
    empty」 is not this node's fact to assert. A node reads back its own job,
    approval or skill account, so its verdict is about itself and stays true when
    a neighbour commits.

    Calling it with no key raises rather than counting everything: a helper that
    silently falls back to the whole table is how an unscoped count comes back.
    """
    named = {
        column: value
        for column, value in (
            ("job_id", job_id),
            ("approval_id", approval_id),
            ("skill_unit_id", skill_unit_id),
        )
        if value is not None
    }
    if not named:
        raise AssertionError(
            "_ledger_rows counts this node's rows on a key this node established; "
            "with no key it would count every node's rows instead."
        )
    db = TestSession()
    try:
        stmt = db.query(AgentPaperExecution)
        for column, value in named.items():
            stmt = stmt.filter(
                getattr(AgentPaperExecution, column) == uuid.UUID(str(value))
            )
        return stmt.order_by(AgentPaperExecution.created_at).all()
    finally:
        db.close()


def _ledger_row(execution_id):
    db = TestSession()
    try:
        return (
            db.query(AgentPaperExecution)
            .filter(AgentPaperExecution.id == uuid.UUID(execution_id))
            .one()
        )
    finally:
        db.close()


def _approval_rows():
    db = TestSession()
    try:
        return (
            db.query(AgentModelActivationApproval)
            .order_by(AgentModelActivationApproval.approved_at)
            .all()
        )
    finally:
        db.close()


def _approval_row(approval_id):
    db = TestSession()
    try:
        return (
            db.query(AgentModelActivationApproval)
            .filter(AgentModelActivationApproval.id == uuid.UUID(approval_id))
            .one()
        )
    finally:
        db.close()


def _census():
    """Row counts for the tables this lane must not touch.

    ``usage_records`` is the billing ledger the personal predict lane spends,
    ``human_decision_events`` the record of a human deciding, and
    ``personal_model_selections``/``decision_labels`` the personal-model family.
    An agent-model paper trade is none of those, so a count that moves here is
    contamination rather than a side effect.
    """
    db = TestSession()
    try:
        return {
            "usage_records": db.query(func.count(UsageRecord.id)).scalar(),
            "human_decision_events": db.query(
                func.count(HumanDecisionEvent.id)
            ).scalar(),
            "personal_model_selections": db.query(
                func.count(PersonalModelSelection.id)
            ).scalar(),
            "decision_labels": db.query(func.count(DecisionLabel.id)).scalar(),
        }
    finally:
        db.close()


def _formal_book(client, headers, portfolio_id):
    return {
        "decisions": client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/decisions", headers=headers
        ).json(),
        "orders": client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders", headers=headers
        ).json(),
        "portfolio": client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=headers
        ).json(),
    }


class TestPaperExecutionPositivePath:
    """Real artifact + matching scoped approval → real paper decision."""

    def test_take_books_a_paper_trade_the_formal_reads_can_trace(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title="paper take",
        )
        job_id = chain["job_id"]
        portfolio_id, cash_before = _skill_unit(client, auth_headers)
        approved = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert approved.status_code == 200, approved.text
        assert approved.json()["activation_status"] == "APPROVED"
        approval_id = approved.json()["approval_id"]
        # The gate names the same artifact the job row carries, from its own read.
        assert (
            _gate(client, auth_headers, job_id)["requirements"]["artifact_checksum"]
            == chain["artifact_checksum"]
        )

        before = _census()
        assert _book(portfolio_id) == EMPTY_BOOK

        # The body is loaded with claims that name nothing real: another job, a
        # forged checksum, a forged approval, a hand-written feature vector and a
        # status the caller would like. Every one of them must be ignored.
        response = _execute(
            client,
            auth_headers,
            job_id,
            portfolio_id,
            job_id=str(uuid.uuid4()),
            artifact_checksum="0" * 64,
            approval_id=str(uuid.uuid4()),
            execution_status="executed",
            features={"sma_short_3": 999.0},
        )
        assert response.status_code == 200, response.text
        payload = response.json()

        newest = _newest_candidate(chain["version_id"])
        signal_date = newest.signal_date
        candidate_id = str(newest.id)

        scored = client.post(
            INFERENCE_ROUTE.format(job_id),
            headers=auth_headers,
            json={"features": dict(newest.features_json)},
        )
        assert scored.status_code == 200, scored.text
        assert scored.json()["decision"] == "take", scored.json()
        assert scored.json()["positive_probability"] == payload["positive_probability"]

        assert payload["execution_status"] == "executed"
        assert payload["model_decision"] == "take"
        assert payload["duplicate"] is False
        assert payload["blocked_reason"] is None
        # The client's claims are absent; the facts the door read are present.
        assert payload["job_id"] == job_id
        assert payload["artifact_checksum"] == chain["artifact_checksum"]
        assert payload["dataset_checksum"] == chain["dataset_checksum"]
        assert payload["approval_id"] == approval_id
        assert payload["snapshot_id"] == chain["snapshot_id"]
        assert payload["strategy_version_id"] == chain["version_id"]
        assert payload["candidate_id"] == candidate_id
        assert payload["signal_date"] == signal_date
        assert payload["instrument_id"] == INSTRUMENT
        assert payload["owner_id"] == str(_owner_id())
        assert payload["skill_unit_id"] == portfolio_id
        assert payload["execution_policy_version"] == EXECUTION_POLICY_VERSION
        assert payload["positive_probability"] >= payload["threshold"]
        assert payload["paper_only"] is True
        assert payload["runs_live_trading"] is False
        assert payload["movable_to_customer_environment"] is False

        decision_id = payload["decision_record_id"]
        order_id = payload["order_id"]
        fill_id = payload["fill_id"]
        assert decision_id and order_id and fill_id, payload

        # The ledger row is the durable copy of the same identity, read outside
        # the request that wrote it.
        row = _ledger_row(payload["execution_id"])
        assert str(row.id) == payload["execution_id"]
        assert row.execution_status == "executed"
        assert str(row.decision_record_id) == decision_id
        assert str(row.order_id) == order_id
        assert str(row.fill_id) == fill_id
        assert str(row.approval_id) == approval_id
        assert str(row.job_id) == job_id
        assert str(row.snapshot_id) == chain["snapshot_id"]
        assert str(row.strategy_version_id) == chain["version_id"]
        assert str(row.candidate_id) == candidate_id
        assert row.artifact_checksum == chain["artifact_checksum"]
        # The person pressed the button; the machine placed the order. Both facts
        # are kept, in the columns that mean them.
        assert row.actor_type == "human"
        # The candidate is the newest durable signal for the locked version, and
        # the split membership agrees with the frozen snapshot's own manifest
        # rather than with a label this module invented.
        manifest = client.get(
            f"/api/v1/agent-strategy/snapshots/{chain['snapshot_id']}",
            headers=auth_headers,
        ).json()["split_manifest"]["splits"]
        expected_membership = next(
            (
                name
                for name in ("train", "validation", "test")
                if candidate_id in manifest[name]["candidate_ids"]
            ),
            "not_in_snapshot",
        )
        assert row.split_membership == expected_membership

        book = _formal_book(client, auth_headers, portfolio_id)
        decision = next(d for d in book["decisions"] if d["id"] == decision_id)
        order = next(o for o in book["orders"] if o["id"] == order_id)

        assert decision["actor_type"] == "system"
        assert order["actor_type"] == "system"
        assert decision["decision_type"] == "entry"
        assert decision["data_cutoff_at"] == signal_date
        assert decision["linked_experiment_version_id"] == chain["version_id"]
        # Not the personal-inference column: an agent model must not be readable
        # as a personal usage event.
        assert decision["linked_inference_id"] is None
        assert decision["outcome_status"] == "pending"
        flags = decision["risk_flags"]
        assert flags["lane"] == "agent_model"
        assert flags["agent_job_id"] == job_id
        assert flags["agent_approval_id"] == approval_id
        assert flags["agent_candidate_id"] == candidate_id
        assert job_id in decision["rationale"]
        assert approval_id in decision["rationale"]
        assert chain["artifact_checksum"] in decision["rationale"]
        assert decision["rationale"].lower().endswith("not a recommendation.")

        assert order["side"] == "BUY"
        assert order["status"] == OrderStatus.filled.value
        assert order["asset_id"] == INSTRUMENT
        assert order["decision_id"] == decision_id
        assert order["portfolio_id"] == portfolio_id
        assert order["data_cutoff_at"] == signal_date
        assert order["model_or_strategy_version_id"] == chain["version_id"]
        assert order["idempotency_key"] == f"agent-model-{job_id}-{candidate_id}"
        assert order["risk_check_status"] == "passed"
        assert order["quantity"] == payload["quantity"]

        fills = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders/{order_id}/fills",
            headers=auth_headers,
        ).json()
        assert len(fills) == 1
        fill = fills[0]
        assert fill["id"] == fill_id
        assert fill["paper_order_id"] == order_id
        assert fill["quantity"] == order["quantity"] == payload["quantity"]
        assert fill["simulated_fill_reason"] == "agent_model_entry"
        # The shared fill convention, checked against the row that was written:
        # the booked price is the observed bar price moved against the buyer, and
        # the execution date is the next stored bar after the signal.
        assert fill["observed_price"] == round(payload["observed_price"], 4)
        assert fill["execution_price_before_costs"] == round(payload["execution_price"], 4)
        assert fill["execution_price_before_costs"] == round(
            fill["observed_price"] * (1 + SLIPPAGE_RATE), 4
        )
        assert fill["execution_date"] == payload["execution_date"]
        assert fill["execution_date"] > payload["signal_date"]
        assert fill["gross_amount"] == round(
            fill["quantity"] * fill["execution_price_before_costs"], 4
        )
        assert fill["commission_amount"] == round(fill["gross_amount"] * COMMISSION_RATE, 4)
        assert fill["net_cash_change"] == round(
            -(fill["gross_amount"] + fill["commission_amount"]), 4
        )
        # Sizing and filling used the same price: the book paid for exactly what it
        # asked for, and the cash left is the fill's own net change off the start.
        cash_after = book["portfolio"]["current_virtual_cash"]
        assert cash_after == round(cash_before + fill["net_cash_change"], 4)
        assert cash_after >= 0
        spent = (
            fill["quantity"]
            * fill["execution_price_before_costs"]
            * (1 + COMMISSION_RATE)
        )
        assert spent <= cash_before + 1e-6
        assert quantity_precision_for(INSTRUMENT) == 0
        assert float(fill["quantity"]) == float(int(fill["quantity"]))

        positions = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}/positions", headers=auth_headers
        ).json()
        assert len(positions) == 1
        assert positions[0]["status"] == "open"
        assert positions[0]["asset_id"] == INSTRUMENT
        assert positions[0]["quantity"] == fill["quantity"]
        assert positions[0]["average_entry_price"] == fill["execution_price_before_costs"]

        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }

        # The lane's own formal read finds the execution without being told its id.
        listed = client.get(LEDGER_ROUTE.format(job_id), headers=auth_headers).json()
        assert listed["count"] == 1
        assert listed["lane"] == "agent_model"
        assert listed["runs_live_trading"] is False
        assert listed["executions"][0]["execution_id"] == payload["execution_id"]
        assert (
            client.get(
                LEDGER_ROUTE.format(job_id),
                headers=auth_headers,
                params={"skill_unit_id": portfolio_id},
            ).json()["count"]
            == 1
        )

        # Resending the same intent returns the same row and spends no second slot.
        repeated = _execute(client, auth_headers, job_id, portfolio_id)
        assert repeated.status_code == 200, repeated.text
        again = repeated.json()
        assert again["duplicate"] is True
        assert again["execution_id"] == payload["execution_id"]
        assert again["decision_record_id"] == decision_id
        assert again["fill_id"] == fill_id
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1
        assert (
            client.get(
                f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
            ).json()["current_virtual_cash"]
            == cash_after
        )

        # Nothing in the personal/usage family grew because an agent model traded.
        assert _census() == before

    def test_model_decline_is_durable_and_books_nothing(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The other side of the threshold: ``skipped`` is a result, not a bug."""
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=_regime_series(180),
            title="paper skip",
        )
        job_id = chain["job_id"]
        portfolio_id, cash_before = _skill_unit(client, auth_headers)
        assert (
            _approve(
                client, auth_headers, job_id, skill_unit_id=portfolio_id
            ).status_code
            == 200
        )

        response = _execute(client, auth_headers, job_id, portfolio_id)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["model_decision"] == "skip", payload
        assert payload["positive_probability"] < payload["threshold"]
        assert payload["execution_status"] == "skipped"
        assert payload["decision_record_id"] is None
        assert payload["order_id"] is None
        assert payload["fill_id"] is None
        assert payload["blocked_reason"] is None
        assert payload["paper_only"] is True

        assert _book(portfolio_id) == EMPTY_BOOK
        book = _formal_book(client, auth_headers, portfolio_id)
        assert book["decisions"] == []
        assert book["orders"] == []
        assert book["portfolio"]["current_virtual_cash"] == cash_before

        repeated = _execute(client, auth_headers, job_id, portfolio_id)
        assert repeated.json()["duplicate"] is True
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1


class TestApprovalScopeRefusals:
    """job-only, another account, another job, revoked, absent — each by name."""

    def _chain_and_unit(self, client, auth_headers, monkeypatch, tmp_path, *, title):
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title=title,
        )
        portfolio_id, _ = _skill_unit(client, auth_headers)
        return chain, portfolio_id

    def test_job_only_approval_authorizes_inference_but_not_execution(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="job-only scope"
        )
        job_id = chain["job_id"]
        approved = _approve(client, auth_headers, job_id)
        assert approved.status_code == 200, approved.text
        assert approved.json()["activation_status"] == "APPROVED"
        approval_id = approved.json()["approval_id"]

        scored = client.post(
            INFERENCE_ROUTE.format(job_id),
            headers=auth_headers,
            json={"features": dict(_newest_candidate(chain["version_id"]).features_json)},
        )
        assert scored.status_code == 200, scored.text
        assert scored.json()["model_activated"] is True

        refusal = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        details = refusal["details"]
        assert details["reason_code"] == "skill_unit_mismatch"
        assert details["approval_id"] == approval_id
        # NULL scope reads as "this artifact in isolation", never "every account".
        assert details["approval_skill_unit_id"] is None
        assert details["requested_skill_unit_id"] == portfolio_id

        assert _ledger_rows(job_id=chain["job_id"]) == []
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _formal_book(client, auth_headers, portfolio_id)["decisions"] == []

        # The refusal may not quietly rewrite the approval it just refused: the
        # owner's job-only act stands exactly as it was recorded.
        row = _approval_row(approval_id)
        assert row.decision == "approved"
        assert row.revoked_at is None
        assert row.skill_unit_id is None
        assert row.artifact_checksum == chain["artifact_checksum"]
        assert len(_approval_rows()) == 1

        # One live approval per job is the recorded constraint, so the owner
        # withdraws the job-only act before approving for the account; this lane
        # asks for that and never performs it.
        already = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert already.status_code == 409, already.text
        assert (
            already.json()["detail"]["code"] == "agent_activation_already_approved"
        )

        revoked = client.post(
            f"{ACTIVATION_ROUTE.format(job_id)}/revoke",
            headers=auth_headers,
            json={"reason": "owner is switching to a scoped approval"},
        )
        assert revoked.status_code == 200, revoked.text
        scoped = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert scoped.status_code == 200, scoped.text
        executed = _execute(client, auth_headers, job_id, portfolio_id)
        assert executed.status_code == 200, executed.text
        assert executed.json()["execution_status"] == "executed"
        assert _book(portfolio_id)["fills"] == 1

    def test_withdrawal_then_scoped_approval_keeps_both_records(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The switch an owner really makes, read back as two durable records.

        Round-24 item 2. The node above proves the door refuses; this one proves
        the sequence behind the door: the job-only act blocks a scoped one and
        survives the attempt untouched, the withdrawal updates that same row
        rather than adding a second live permission, and the new approval covers
        the account it names and no other. Nothing here is inferred from a 2xx —
        every claim is a read of ``agent_model_activation_approvals``.
        """
        chain, portfolio_id = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="withdraw then scope"
        )
        other_unit, _ = _skill_unit(client, auth_headers)
        job_id = chain["job_id"]
        job_only = _approve(client, auth_headers, job_id).json()["approval_id"]

        blocked = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "agent_activation_already_approved"
        # A refused act leaves the record it refused exactly as the owner made it:
        # the lane may not quietly scope, revoke or duplicate an approval the
        # caller had no right to change.
        first = _approval_row(job_only)
        assert first.decision == "approved"
        assert first.revoked_at is None
        assert first.skill_unit_id is None
        assert first.artifact_checksum == chain["artifact_checksum"]
        assert len(_approval_rows()) == 1
        state = _state(client, auth_headers, job_id)
        assert state["approval_id"] == job_only
        assert state["skill_unit_id"] is None
        assert state["skill_unit_scope"] == "none"
        assert _refusal(_execute(client, auth_headers, job_id, portfolio_id))[
            "code"
        ] == "paper_execution_approval_scope_not_granted"
        assert _ledger_rows(job_id=chain["job_id"]) == []

        withdrawn = client.post(
            f"{ACTIVATION_ROUTE.format(job_id)}/revoke",
            headers=auth_headers,
            json={"reason": "switching to one named account"},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        # The withdrawal is the same row, not a new one, and it does not rewrite
        # the scope the owner originally recorded.
        history = _approval_row(job_only)
        assert history.decision == "revoked"
        assert history.revoked_at is not None
        assert history.revoke_reason == "switching to one named account"
        assert history.skill_unit_id is None
        assert len(_approval_rows()) == 1
        assert (
            _state(client, auth_headers, job_id)["activation_status"] == "NOT_APPROVED"
        )

        scoped = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert scoped.status_code == 200, scoped.text
        scoped_id = scoped.json()["approval_id"]
        assert scoped_id != job_only
        assert scoped.json()["skill_unit_id"] == portfolio_id
        assert scoped.json()["skill_unit_scope"] == "specific"

        rows = _approval_rows()
        assert len(rows) == 2, "the new permission erased the recorded withdrawal"
        live = [row for row in rows if row.revoked_at is None]
        assert [str(row.id) for row in live] == [scoped_id]
        assert str(live[0].skill_unit_id) == portfolio_id
        assert _approval_row(job_only).decision == "revoked"

        # One live approval, and it authorizes exactly the account it names.
        executed = _execute(client, auth_headers, job_id, portfolio_id)
        assert executed.status_code == 200, executed.text
        assert executed.json()["execution_status"] == "executed"
        assert str(_ledger_row(executed.json()["execution_id"]).approval_id) == scoped_id
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }

        refusal = _refusal(_execute(client, auth_headers, job_id, other_unit))
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        assert refusal["details"]["reason_code"] == "skill_unit_mismatch"
        assert refusal["details"]["approval_id"] == scoped_id
        assert refusal["details"]["approval_skill_unit_id"] == portfolio_id
        assert refusal["details"]["requested_skill_unit_id"] == other_unit
        assert _book(other_unit) == EMPTY_BOOK
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1
        assert (
            client.get(
                LEDGER_ROUTE.format(job_id),
                headers=auth_headers,
                params={"skill_unit_id": other_unit},
            ).json()["count"]
            == 0
        )
        # The withdrawal still reads as history after the account has traded.
        assert _approval_row(job_only).decision == "revoked"
        assert len(_approval_rows()) == 2

    def test_approval_for_another_skill_unit_is_refused(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, approved_for = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="other account scope"
        )
        other_unit, _ = _skill_unit(client, auth_headers)
        assert other_unit != approved_for
        job_id = chain["job_id"]
        assert (
            _approve(
                client, auth_headers, job_id, skill_unit_id=approved_for
            ).status_code
            == 200
        )

        refusal = _refusal(_execute(client, auth_headers, job_id, other_unit))
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        details = refusal["details"]
        assert details["reason_code"] == "skill_unit_mismatch"
        assert details["approval_skill_unit_id"] == approved_for
        assert details["requested_skill_unit_id"] == other_unit

        assert _book(other_unit) == EMPTY_BOOK
        assert _book(approved_for) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []

    def test_second_job_cannot_borrow_the_first_jobs_approval(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """Naming a different job must not attach the newest approved model."""
        chain_a, portfolio_id = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="second artifact A"
        )
        chain_b = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=EXPENSIVE,
            closes=take_series(base_days=186),
            title="second artifact B",
        )
        assert chain_b["artifact_checksum"] != chain_a["artifact_checksum"]
        assert (
            _approve(
                client, auth_headers, chain_a["job_id"], skill_unit_id=portfolio_id
            ).status_code
            == 200
        )
        assert (
            _state(client, auth_headers, chain_b["job_id"])["activation_status"]
            != "APPROVED"
        )

        refusal = _refusal(
            _execute(client, auth_headers, chain_b["job_id"], portfolio_id)
        )
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        assert refusal["details"]["reason_code"] == "no_approval"
        assert refusal["details"]["approval_id"] is None
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain_b["job_id"]) == []

        # The approved job still executes; the unapproved one still does not.
        assert (
            _execute(
                client, auth_headers, chain_a["job_id"], portfolio_id
            ).json()["execution_status"]
            == "executed"
        )
        assert (
            _refusal(_execute(client, auth_headers, chain_b["job_id"], portfolio_id))[
                "code"
            ]
            == "paper_execution_approval_scope_not_granted"
        )
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }
        assert len(_ledger_rows(job_id=chain_a["job_id"])) == 1
        assert _ledger_rows(job_id=chain_b["job_id"]) == [], (
            "the unapproved job wrote a ledger row behind its own refusal"
        )

    def test_revoked_approval_refuses_and_the_recorded_execution_survives(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="revoke leg"
        )
        job_id = chain["job_id"]
        assert (
            _approve(
                client, auth_headers, job_id, skill_unit_id=portfolio_id
            ).status_code
            == 200
        )
        executed = _execute(client, auth_headers, job_id, portfolio_id).json()
        assert executed["execution_status"] == "executed"
        booked = _book(portfolio_id)
        assert booked == {"decisions": 1, "orders": 1, "fills": 1, "positions": 1}

        revoked = client.post(
            f"{ACTIVATION_ROUTE.format(job_id)}/revoke",
            headers=auth_headers,
            json={"reason": "owner turned the model off"},
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["revoked"] is True
        assert (
            _state(client, auth_headers, job_id)["activation_status"]
            == "NOT_APPROVED"
        )

        # The gate is consulted before the dedupe read, so a withdrawn approval
        # cannot be replayed past on any account — not a fresh one, and not the
        # one that already traded.
        fresh_unit, _ = _skill_unit(client, auth_headers)
        refusal = _refusal(_execute(client, auth_headers, job_id, fresh_unit))
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        assert refusal["details"]["reason_code"] == "no_approval"
        # The gate answers "there is no live approval" and names no row. What the
        # owner withdrew is still durable, and still points at this job.
        assert refusal["details"]["approval_id"] is None
        withdrawal = _approval_row(executed["approval_id"])
        assert withdrawal.decision == "revoked"
        assert withdrawal.revoked_at is not None
        assert withdrawal.revoke_reason == "owner turned the model off"
        assert str(withdrawal.job_id) == job_id
        assert _book(fresh_unit) == EMPTY_BOOK
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1

        # Refusing again does not undo, rewrite or duplicate what the owner
        # already let happen while the approval was live.
        replay = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert replay["details"]["reason_code"] == "no_approval"
        assert _book(portfolio_id) == booked
        assert (
            _formal_book(client, auth_headers, portfolio_id)["orders"][0]["id"]
            == executed["order_id"]
        )
        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1
        assert rows[0].execution_status == "executed"
        assert str(rows[0].approval_id) == executed["approval_id"]

    def test_no_approval_at_all_refuses_before_the_model_loads(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id = self._chain_and_unit(
            client, auth_headers, monkeypatch, tmp_path, title="no approval"
        )
        job_id = chain["job_id"]
        state = _state(client, auth_headers, job_id)
        assert state["activation_status"] != "APPROVED"
        assert state["reason_code"] == "no_recorded_approval"
        assert state["approval_id"] is None

        refusal = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert refusal["code"] == "paper_execution_approval_scope_not_granted"
        assert refusal["details"]["reason_code"] == "no_approval"
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []
        assert _approval_rows() == []


class TestDurableRefusals:
    """Two families of 409, because a status code cannot tell them apart.

    Round-24 item 3. ``execute_paper_decision`` refuses a precondition by raising
    before anything is written (``app/services/agent_paper_execution.py:323``),
    and refuses a *result* by committing the ledger row first and only then
    raising, from ``_commit_ledger`` (:709). Both answers arrive at the caller as
    HTTP 409 with a ``code`` and ``details``, so a client — and a test — that
    looks only at the status cannot tell "nothing happened" from "the model
    decided and the book refused". These nodes keep the families apart by reading
    the tables and the lane's own GET back: the blocked press yields a real
    ``execution_id`` whose ``decision_record_id`` / ``order_id`` / ``fill_id`` are
    honestly absent, while the scope refusal yields no id at all and no row.
    """

    def test_blocked_press_is_durable_while_a_scope_refusal_writes_nothing(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title="unfunded block",
        )
        job_id = chain["job_id"]
        starved, cash_before = _skill_unit(client, auth_headers, cash=1.0)
        other_unit, _ = _skill_unit(client, auth_headers)
        assert (
            _approve(client, auth_headers, job_id, skill_unit_id=starved).status_code
            == 200
        )

        refusal = _refusal(_execute(client, auth_headers, job_id, starved))
        assert refusal["code"] == "paper_execution_insufficient_cash"
        assert refusal["details"]["blocked_reason"] == "insufficient_cash"
        assert refusal["details"]["execution_status"] == "blocked"
        execution_id = refusal["details"]["execution_id"]
        assert uuid.UUID(execution_id)

        # The score the model produced is kept next to the book's refusal: the
        # judgment happened, the trade did not.
        row = _ledger_row(execution_id)
        assert row.model_decision == "take"
        assert row.execution_status == "blocked"
        assert row.blocked_reason == "insufficient_cash"
        assert row.decision_record_id is None
        assert row.order_id is None
        assert row.fill_id is None
        assert row.quantity == 0.0
        # Refused while sizing, so the shared policy was never asked — and the
        # read says that, instead of inventing a clean risk answer.
        assert row.risk_check_json is None
        assert row.artifact_checksum == chain["artifact_checksum"]

        listed = client.get(LEDGER_ROUTE.format(job_id), headers=auth_headers).json()
        assert listed["count"] == 1
        entry = listed["executions"][0]
        assert entry["execution_id"] == execution_id
        assert entry["execution_status"] == "blocked"
        assert entry["blocked_reason"] == "insufficient_cash"
        assert entry["decision_record_id"] is None
        assert entry["order_id"] is None
        assert entry["fill_id"] is None
        assert entry["risk_check"] is None

        assert _book(starved) == EMPTY_BOOK
        book = _formal_book(client, auth_headers, starved)
        assert book["decisions"] == []
        assert book["orders"] == []
        assert book["portfolio"]["current_virtual_cash"] == cash_before

        # An intent already read back as blocked is not pressed a second time: the
        # lane returns the recorded answer rather than re-running the model on the
        # same signal and spending a second judgment on one candidate.
        repeated = _execute(client, auth_headers, job_id, starved)
        assert repeated.status_code == 200, repeated.text
        again = repeated.json()
        assert again["duplicate"] is True
        assert again["execution_id"] == execution_id
        assert again["execution_status"] == "blocked"
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1
        assert _book(starved) == EMPTY_BOOK

        # The other family: the approval refuses, and the lane writes nothing.
        scope_refusal = _refusal(_execute(client, auth_headers, job_id, other_unit))
        assert scope_refusal["code"] == "paper_execution_approval_scope_not_granted"
        assert scope_refusal["details"]["reason_code"] == "skill_unit_mismatch"
        assert "execution_id" not in scope_refusal["details"]
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1
        assert _book(other_unit) == EMPTY_BOOK
        assert (
            client.get(
                LEDGER_ROUTE.format(job_id),
                headers=auth_headers,
                params={"skill_unit_id": other_unit},
            ).json()["count"]
            == 0
        )

    def test_risk_policy_block_keeps_the_policy_answer_that_refused_it(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The durable family that carries the policy's own facts in the row.

        The refusal comes from narrowing the shared policy the way a deployment
        does — ``risk_policy.get_policy()`` is read per decision, and this lane
        asks the same ``evaluate_risk`` as the rule and direct lanes — not from a
        fixture that says no on the lane's behalf.
        """
        monkeypatch.setattr(
            risk_policy,
            "DEFAULT_RISK_POLICY",
            risk_policy.RiskPolicy(max_daily_entries=0),
        )
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title="risk block",
        )
        job_id = chain["job_id"]
        portfolio_id, _ = _skill_unit(client, auth_headers)
        assert (
            _approve(
                client, auth_headers, job_id, skill_unit_id=portfolio_id
            ).status_code
            == 200
        )

        refusal = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert refusal["code"] == "paper_execution_risk_blocked"
        assert refusal["details"]["blocked_reason"] == "daily_entry_limit"
        risk_check = refusal["details"]["risk_check"]
        assert risk_check["allowed"] is False
        assert risk_check["code"] == "daily_entry_limit"
        assert risk_check["lane"] == "agent_model"
        assert risk_check["policy"]["max_daily_entries"] == 0
        execution_id = refusal["details"]["execution_id"]

        row = _ledger_row(execution_id)
        assert row.execution_status == "blocked"
        assert row.blocked_reason == "daily_entry_limit"
        assert row.risk_check_json == risk_check
        assert row.decision_record_id is None and row.order_id is None
        # Sizing succeeded here, so the row carries the very ask the policy
        # refused, which is what makes the block re-checkable after the fact.
        assert row.quantity > 0

        entry = client.get(LEDGER_ROUTE.format(job_id), headers=auth_headers).json()[
            "executions"
        ][0]
        assert entry["execution_id"] == execution_id
        assert entry["risk_check"]["code"] == "daily_entry_limit"
        assert entry["decision_record_id"] is None
        assert _book(portfolio_id) == EMPTY_BOOK
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1

        assert (
            _execute(client, auth_headers, job_id, portfolio_id).json()["duplicate"]
            is True
        )
        assert len(_ledger_rows(job_id=chain["job_id"])) == 1


class TestBindingRefusals:
    """The approval must bind this artifact, this dataset and this version."""

    def _approved(self, client, auth_headers, monkeypatch, tmp_path, *, title):
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title=title,
        )
        portfolio_id, _ = _skill_unit(client, auth_headers)
        approved = _approve(
            client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
        )
        assert approved.status_code == 200, approved.text
        return chain, portfolio_id, approved.json()["approval_id"]

    @pytest.mark.parametrize(
        "column,forged,reason_code",
        [
            ("artifact_checksum", "f" * 64, "approval_artifact_mismatch"),
            ("dataset_checksum", "e" * 64, "approval_dataset_mismatch"),
            (
                "training_contract_version",
                "agent-strategy-training-contract-v0",
                "approval_contract_mismatch",
            ),
        ],
    )
    def test_approval_that_no_longer_binds_the_artifact_is_refused(
        self, client, auth_headers, monkeypatch, tmp_path, column, forged, reason_code
    ):
        """A stale binding is refused even though the client claimed nothing false.

        The row is edited under the owner's own hands to stand for the ordinary
        product fact that a job can be retrained: the approval still names the
        account, so the scope leg passes, and it is the loader's own
        re-verification of the bytes that has to catch it.
        """
        chain, portfolio_id, approval_id = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title=f"binding {column}"
        )
        db = TestSession()
        try:
            row = (
                db.query(AgentModelActivationApproval)
                .filter(AgentModelActivationApproval.id == uuid.UUID(approval_id))
                .one()
            )
            setattr(row, column, forged)
            db.commit()
        finally:
            db.close()
        # The mirrored column still says APPROVED, which is exactly why the gate
        # recomputes instead of trusting it.
        assert (
            _state(client, auth_headers, chain["job_id"])["activation_status"]
            == "NOT_APPROVED"
        )

        refusal = _refusal(_execute(client, auth_headers, chain["job_id"], portfolio_id))
        assert refusal["code"] == "agent_model_not_activated", refusal
        assert refusal["details"]["activation_reason_code"] == reason_code
        assert refusal["details"]["approval_id"] == approval_id
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []

    def test_approval_bound_to_another_strategy_version_is_refused(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id, approval_id = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title="version leg"
        )
        other_version = _create_locked_strategy(
            client,
            auth_headers,
            instrument=INSTRUMENT,
            title="another version",
            window_days=len(take_series()),
        )
        db = TestSession()
        try:
            row = (
                db.query(AgentModelActivationApproval)
                .filter(AgentModelActivationApproval.id == uuid.UUID(approval_id))
                .one()
            )
            row.strategy_version_id = other_version
            db.commit()
        finally:
            db.close()

        refusal = _refusal(_execute(client, auth_headers, chain["job_id"], portfolio_id))
        assert refusal["code"] == "paper_execution_version_mismatch"
        assert refusal["details"]["job_strategy_version_id"] == chain["version_id"]
        assert refusal["details"]["approval_strategy_version_id"] == str(other_version)
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []

    def test_paused_retired_and_completed_accounts_refuse_entries_by_name(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id, _ = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title="account state"
        )
        for status_value, code in (
            ("paused", "paper_execution_skill_unit_paused"),
            ("retired", "paper_execution_skill_unit_retired"),
            ("completed", "paper_execution_skill_unit_completed"),
        ):
            db = TestSession()
            try:
                portfolio = (
                    db.query(PaperPortfolio)
                    .filter(PaperPortfolio.id == uuid.UUID(portfolio_id))
                    .one()
                )
                portfolio.status = status_value
                db.commit()
            finally:
                db.close()
            refusal = _refusal(
                _execute(client, auth_headers, chain["job_id"], portfolio_id)
            )
            assert refusal["code"] == code, (status_value, refusal)
            assert refusal["details"]["status"] == status_value
            assert _ledger_rows(job_id=chain["job_id"]) == []
            assert _book(portfolio_id) == EMPTY_BOOK


class TestSafetySwitchesAndOwnership:
    """Kill switches, consent and ownership — each a separate named answer."""

    def _approved(self, client, auth_headers, monkeypatch, tmp_path, *, title):
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title=title,
        )
        portfolio_id, _ = _skill_unit(client, auth_headers)
        approved = _approve(
            client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
        )
        assert approved.status_code == 200, approved.text
        return chain, portfolio_id

    @pytest.mark.parametrize(
        "scope,code",
        [
            (KillSwitchScope.global_, "paper_execution_kill_switch_global"),
            (KillSwitchScope.inference, "paper_execution_kill_switch_inference"),
            (KillSwitchScope.execution, "paper_execution_kill_switch_execution"),
        ],
    )
    def test_each_kill_switch_refuses_under_its_own_name(
        self, client, auth_headers, monkeypatch, tmp_path, scope, code
    ):
        chain, portfolio_id = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title=f"switch {scope.value}"
        )
        db = TestSession()
        try:
            activate_kill_switch(db, scope, "test", reason="owner pulled it")
        finally:
            db.close()

        refusal = _refusal(_execute(client, auth_headers, chain["job_id"], portfolio_id))
        assert refusal["code"] == code, refusal
        assert refusal["details"]["scope"] == scope.value
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []

        db = TestSession()
        try:
            deactivate_kill_switch(db, scope)
        finally:
            db.close()
        executed = _execute(client, auth_headers, chain["job_id"], portfolio_id)
        assert executed.status_code == 200, executed.text
        assert executed.json()["execution_status"] == "executed"
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }

    def test_withdrawn_consent_refuses_the_paper_lane(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title="consent withdrawn"
        )
        revoked = client.post(
            "/api/v1/agent-strategy/consent/revoke",
            headers=auth_headers,
            json={"reason": "owner withdrew permission"},
        )
        assert revoked.status_code == 200, revoked.text
        policy = client.get(
            "/api/v1/agent-strategy/consent-policy", headers=auth_headers
        ).json()
        assert policy["status"]["status"] != "active"

        refusal = _refusal(_execute(client, auth_headers, chain["job_id"], portfolio_id))
        assert refusal["code"] == "paper_execution_owner_consent_not_active"
        assert refusal["details"]["consent_status"] != "active"
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []

    def test_another_owner_names_neither_the_job_nor_the_account(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id = self._approved(
            client, auth_headers, monkeypatch, tmp_path, title="foreign owner"
        )
        other_headers = _login_other_owner(client)
        other_unit, _ = _skill_unit(client, other_headers)

        # Their own account, this owner's job.
        missing_job = client.post(
            PAPER_ROUTE.format(chain["job_id"]),
            headers=other_headers,
            json={"skill_unit_id": other_unit},
        )
        assert missing_job.status_code == 404, missing_job.text
        assert missing_job.json()["detail"]["code"] == "paper_execution_job_not_found"

        # This owner's account, named by somebody else.
        missing_unit = client.post(
            PAPER_ROUTE.format(chain["job_id"]),
            headers=other_headers,
            json={"skill_unit_id": portfolio_id},
        )
        assert missing_unit.status_code == 404, missing_unit.text
        assert (
            missing_unit.json()["detail"]["code"]
            == "paper_execution_skill_unit_not_found"
        )

        # Reading this owner's ledger back is absent, not empty-but-listed.
        assert (
            client.get(LEDGER_ROUTE.format(chain["job_id"]), headers=other_headers).status_code
            == 404
        )

        assert _book(portfolio_id) == EMPTY_BOOK
        assert _book(other_unit) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []


class TestRevokeVsSubmitOrdering:
    """撤回與提交交疊時，以哪一筆交易的結果判定先後。

    Round-24 item 2 names a promise the door's shape did not keep. The scope leg
    reads the live approval without locking it
    (``app/services/agent_paper_execution.py:356`` and ``:370``), while the owner's
    withdrawal locks that same row before flipping it
    (``app/services/model_activation.py:573-580``). A revoke that committed between
    the door's read and its ``db.commit()`` (``:721``) was therefore answered with a
    trade the owner had already switched off — and no rule existed for which of the
    two records the ledger would agree with afterwards.

    The rule, and the minimal fix that enforces it: the transaction that acquires
    the row lock on ``agent_model_activation_approvals.id`` first decides.
    ``_approval_slot_is_live`` (``:657``, called at ``:718``) takes the same lock at
    the lane's one commit point, so a withdrawal that won the race is met with a
    named refusal and nothing at all durable — no ledger row, no decision, no order,
    no fill, no spent cash — while a submission that won leaves its rows readable
    and the withdrawal lands on an approval whose effect is already in the book.
    No sleep, no retry and no post-hoc readback is asked to reconstruct an order the
    database did not enforce.

    What the SQLite nodes below can and cannot say: SQLite renders no ``FOR UPDATE``
    clause at all, so these nodes prove the *decision* re-read fires (a committed
    withdrawal on a second connection is seen, and its rows are rolled back). The
    mutual exclusion — one of the two transactions waiting on the other — is a
    PostgreSQL behaviour, is claimed by the last node only, and that node is PG-ONLY
    and NOT RUN here.
    """

    def _withdraw_behind_the_loader(self, monkeypatch, approval_id):
        """Arm a real withdrawal that commits while the door is still open.

        The hook goes behind ``run_inference`` because that call is the last shared
        step before ``_book_entry`` and the single commit: the scope leg has
        already read the approval, so a revoke committed from here lands in the
        window the fix is about. It is the lane's own revoke service on a second
        ``Session`` — the same function ``POST .../activation/revoke`` calls — not a
        stub that pretends to be one.
        """
        real_inference = paper_service.run_inference
        state = {"armed": True, "inferences": 0, "revoked": None}

        def withdraw_midflight(*args, **kwargs):
            verdict = real_inference(*args, **kwargs)
            state["inferences"] += 1
            if state["armed"]:
                other = TestSession()
                try:
                    state["revoked"] = model_activation.revoke_activation_approval(
                        other,
                        _owner_id(),
                        approval_id=uuid.UUID(approval_id),
                        reason="owner withdrew while the door was open",
                    )
                finally:
                    other.close()
            return verdict

        monkeypatch.setattr(paper_service, "run_inference", withdraw_midflight)
        return state

    def _armed_and_approved(self, client, auth_headers, monkeypatch, tmp_path, *,
                            title, closes):
        chain = _chain(
            client, auth_headers, monkeypatch, tmp_path,
            instrument=INSTRUMENT, closes=closes, title=title,
        )
        portfolio_id, _ = _skill_unit(client, auth_headers)
        approved = _approve(
            client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
        )
        assert approved.status_code == 200, approved.text
        approval_id = approved.json()["approval_id"]
        state = self._withdraw_behind_the_loader(monkeypatch, approval_id)
        return chain, portfolio_id, approval_id, state

    def test_revocation_between_the_read_and_the_commit_writes_nothing(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        chain, portfolio_id, approval_id, state = self._armed_and_approved(
            client, auth_headers, monkeypatch, tmp_path,
            title="revoke midflight", closes=take_series(),
        )
        job_id = chain["job_id"]
        cash_at_the_door = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()["current_virtual_cash"]
        before = _census()

        refusal = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert state["inferences"] == 1, "the door never reached the window"
        assert state["revoked"]["revoked"] is True
        assert refusal["code"] == "paper_execution_approval_revoked_midflight"
        details = refusal["details"]
        assert details["approval_id"] == approval_id
        assert details["approval_decision"] == "revoked"
        assert details["job_id"] == job_id
        assert details["skill_unit_id"] == portfolio_id
        assert details["candidate_id"] == str(
            _newest_candidate(chain["version_id"]).id
        )
        # This chain scores ``take``, so the door had already reached
        # ``book_fill`` and flushed its decision, order and fill. None of it was
        # committed, and no ledger row survived either.
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _ledger_rows(job_id=chain["job_id"]) == []
        assert client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()["current_virtual_cash"] == cash_at_the_door
        assert _census() == before

        # 舊列保留: the withdrawal is the durable fact, with the owner's own reason.
        withdrawal = _approval_row(approval_id)
        assert withdrawal.decision == "revoked"
        assert withdrawal.revoked_at is not None
        assert withdrawal.revoke_reason == "owner withdrew while the door was open"
        assert _state(client, auth_headers, job_id)["activation_status"] == "NOT_APPROVED"

        # The discriminating pair for the harness itself: same door, same job, same
        # account, same wrapper — only the withdrawal switched off. If this leg also
        # refused, the node above would be proving the wrapper, not the ordering.
        state["armed"] = False
        again = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert again.status_code == 200, again.text
        second_approval_id = again.json()["approval_id"]
        assert second_approval_id != approval_id
        executed = _execute(client, auth_headers, job_id, portfolio_id)
        assert executed.status_code == 200, executed.text
        payload = executed.json()
        assert payload["execution_status"] == "executed"
        assert payload["approval_id"] == second_approval_id
        assert _book(portfolio_id) == {
            "decisions": 1, "orders": 1, "fills": 1, "positions": 1,
        }
        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1
        assert str(rows[0].approval_id) == second_approval_id
        # The refusal above was not a repeat intent either: the second press wrote
        # the first row rather than re-reading one.
        assert payload["duplicate"] is False
        assert _census() == before

    def test_a_declined_model_writes_no_ledger_row_when_the_revocation_won(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The guard sits at the commit point, not in the entry path.

        ``decision != "take"`` reaches ``_commit_ledger`` straight from
        ``execute_paper_decision`` (``app/services/agent_paper_execution.py:471``),
        so it is a second, independent way into the one commit — and a ``skipped``
        row bound to a withdrawn approval is exactly the kind of record someone
        later reads back as an authorization.
        """
        chain, portfolio_id, approval_id, state = self._armed_and_approved(
            client, auth_headers, monkeypatch, tmp_path,
            title="skip midflight", closes=_regime_series(180),
        )
        job_id = chain["job_id"]
        refusal = _refusal(_execute(client, auth_headers, job_id, portfolio_id))
        assert refusal["code"] == "paper_execution_approval_revoked_midflight"
        assert _ledger_rows(job_id=chain["job_id"]) == []
        assert _book(portfolio_id) == EMPTY_BOOK
        assert _approval_row(approval_id).decision == "revoked"

        state["armed"] = False
        again = _approve(client, auth_headers, job_id, skill_unit_id=portfolio_id)
        assert again.status_code == 200, again.text
        response = _execute(client, auth_headers, job_id, portfolio_id)
        assert response.status_code == 200, response.text
        skipped = response.json()
        assert skipped["model_decision"] == "skip", skipped
        assert skipped["execution_status"] == "skipped"
        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1
        assert str(rows[0].approval_id) == again.json()["approval_id"]

    def test_the_guard_locks_the_row_the_withdrawal_locks(self):
        """The fix is checked against the code that ships, offline.

        ``requires_postgresql`` keeps the race itself unproven here, so what this
        node can still refuse is the quieter failure: a guard that re-reads a
        different table, reads without the lock, reads through the session's
        identity map (which hands back the cached ``approved`` copy), or runs after
        the commit it is supposed to precede. Each is measured on the function
        objects and the parsed module, not on prose.
        """
        guard = inspect.getsource(paper_service._approval_slot_is_live)
        assert "select(AgentModelActivationApproval)" in guard.replace("\n", ""), (
            "the guard does not re-read the approval row the withdrawal writes"
        )
        assert ".with_for_update()" in guard, "the guard reads without the lock"
        assert "populate_existing=True" in guard, (
            "the re-read would answer from the identity map, not the row"
        )
        assert '"approved"' in guard or "'approved'" in guard

        revoker = inspect.getsource(model_activation.revoke_activation_approval)
        assert "select(AgentModelActivationApproval)" in revoker.replace("\n", "")
        assert ".with_for_update()" in revoker, (
            "the withdrawal no longer locks what the guard locks, so neither waits"
        )

        commit_body = inspect.getsource(paper_service._commit_ledger)
        assert "_approval_slot_is_live(" in commit_body, (
            "the commit point no longer asks the approval row what it decided"
        )
        assert commit_body.index("_approval_slot_is_live(") < commit_body.index(
            "db.add(ledger)"
        ), "the guard runs after the row is added, so the withdrawal loses"

        tree = ast.parse(inspect.getsource(paper_service))
        commits = []
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "db"
                ):
                    commits.append(func.name)
        assert commits == ["_commit_ledger"], (
            f"the lane promised one commit point, found {commits}"
        )

    def test_revoke_and_submit_racing_on_one_approval_row(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        requires_postgresql(
            "asks two transactions to take the same row lock on one server; the "
            "SQLite lane renders no FOR UPDATE clause at all, so there is nothing "
            "for the two to contend on and a green node here would be a node that "
            "never raced. ALETHRA_PG_TEST_URL is unset -- NOT RUN, not passed."
        )
        chain = _chain(
            client, auth_headers, monkeypatch, tmp_path,
            instrument=INSTRUMENT, closes=take_series(), title="revoke race",
        )
        job_id = uuid.UUID(chain["job_id"])
        portfolio_id, _ = _skill_unit(client, auth_headers)
        approved = _approve(
            client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
        )
        assert approved.status_code == 200, approved.text
        approval_id = uuid.UUID(approved.json()["approval_id"])

        door_at_lock = threading.Event()
        real_guard = paper_service._approval_slot_is_live
        timings = {}

        def at_the_lock(db, ledger, identity):
            # The submit thread has reached the lane's only commit point. Release
            # the withdrawal here so the two transactions are genuinely open at the
            # same moment rather than queued one after the other.
            door_at_lock.set()
            return real_guard(db, ledger, identity)

        monkeypatch.setattr(paper_service, "_approval_slot_is_live", at_the_lock)

        results = {}

        def submit():
            db = TestSession()
            try:
                timings["submit_start"] = time.monotonic()
                results["submit"] = paper_service.execute_paper_decision(
                    db, _owner_id(), job_id=job_id,
                    skill_unit_id=uuid.UUID(portfolio_id), actor_type="human",
                )
            except Exception as exc:  # kept, never swallowed
                results["submit"] = exc
            finally:
                timings["submit_end"] = time.monotonic()
                db.close()

        def withdraw():
            assert door_at_lock.wait(timeout=60), "the door never reached the lock"
            db = TestSession()
            try:
                timings["revoke_start"] = time.monotonic()
                results["revoke"] = model_activation.revoke_activation_approval(
                    db, _owner_id(), approval_id=approval_id, reason="raced the press"
                )
            except Exception as exc:
                results["revoke"] = exc
            finally:
                timings["revoke_end"] = time.monotonic()
                db.close()

        threads = [
            threading.Thread(target=submit),
            threading.Thread(target=withdraw),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not [t for t in threads if t.is_alive()], "a transaction hung"
        assert set(results) == {"submit", "revoke"}, results
        # INVALID, not a pass: the two must have been open together, or nothing was
        # raced and the verdict below would be the sequential one already proven.
        assert timings["revoke_start"] < timings["submit_end"], (
            f"no overlap: {timings}"
        )

        revoke = results["revoke"]
        assert isinstance(revoke, dict) and revoke["revoked"] is True, revoke
        assert _approval_row(str(approval_id)).decision == "revoked"

        submit_result = results["submit"]
        rows = _ledger_rows(job_id=chain["job_id"])
        book = _book(portfolio_id)
        if isinstance(submit_result, paper_service.AgentStrategyRefused):
            # The withdrawal took the lock first, so the press had to leave nothing.
            assert submit_result.code == "paper_execution_approval_revoked_midflight"
            assert rows == [], "refused by name yet a row committed"
            assert book == EMPTY_BOOK, "refused by name yet the book moved"
            order = "revoke-won"
        elif isinstance(submit_result, dict):
            # The press took it first: its rows stand and stay readable, and the
            # withdrawal lands on an approval that already produced a trade.
            assert submit_result["execution_status"] == "executed", submit_result
            assert len(rows) == 1 and str(rows[0].approval_id) == str(approval_id)
            assert book == {"decisions": 1, "orders": 1, "fills": 1, "positions": 1}
            order = "submit-won"
        else:
            pytest.fail(f"unexpected submit outcome: {submit_result!r}")
        # Whichever ordering the server produced, the ledger agrees with it. One run
        # lands on one branch; the branch is recorded rather than assumed, so a
        # lane that only ever shows one of the two says so instead of claiming both.
        print(f"[revoke-vs-submit race] ordering observed: {order}")


class TestRacingPressCollapse:
    """Where two presses of one intent actually part ways, and what each answer is.

    Card A ran the PG-only node below against an isolated PostgreSQL 16.2 and
    measured the defect these nodes pin: the two presses never reached the
    ledger's unique key. ``_book_entry`` writes the ``PaperOrder`` row
    (``app/services/agent_paper_execution.py:625``) long before ``_commit_ledger``
    inserts the ledger row (``:785``, ``:787``), so the losing press collided on
    ``uq_paper_order_portfolio_idempotency`` — and the only collapse branch that
    existed keyed on ``uq_agent_paper_execution_intent``, so the raw
    ``UniqueViolation`` walked out of the door as an unhandled error while the
    trade the customer was racing for had already committed. That server's own
    log line reads::

        duplicate key value violates unique constraint
        "uq_paper_order_portfolio_idempotency"

    What these SQLite nodes do and do not claim. There is no second transaction
    and nothing waits: a competing press is made to *commit* inside the door's
    own read-then-write window, so SQLite raises the collision against a row that
    really is committed. The ``IntegrityError``, its ``.orig`` wording, the branch
    the recognizer takes and the writes the rollback undoes are all the product's
    own; only the interleaving is engineered. Mutual exclusion stays the PG-only
    node below, NOT RUN on this lane.

    The seam is chosen per node, and the choice is the point. A press that trades
    must be raced after ``evaluate_risk`` (``:549``) and before the order flush,
    because in a real race the rival's cash has not landed yet when this press
    asks the policy — racing at the loader instead would let the door read the
    rival's emptied cash, get refused by the policy, and collide at the ledger key
    for the wrong reason. A press that declines never reaches the policy, so its
    rival is armed at ``run_inference`` (``:428``), which is the last shared step
    before its only write.

    The two directions are kept apart deliberately. A collision on a key this lane
    owns, with a committed intent row behind it, answers with that row and says it
    is a duplicate. A collision on a named key with no intent row behind it, and an
    integrity error this lane does not own at all, both travel out unchanged:
    renaming the platform's own failure into ``duplicate=True`` would hand the
    customer an answer about a trade that does not exist.
    """

    def _chain_and_approval(self, client, auth_headers, monkeypatch, tmp_path, *,
                            title, closes):
        chain = _chain(
            client, auth_headers, monkeypatch, tmp_path,
            instrument=INSTRUMENT, closes=closes, title=title,
        )
        portfolio_id, cash_before = _skill_unit(client, auth_headers)
        approved = _approve(
            client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
        )
        assert approved.status_code == 200, approved.text
        return chain, portfolio_id, cash_before, approved.json()["approval_id"]

    def _rival_at(self, monkeypatch, seam, rival):
        """Let ``rival(db)`` commit while this press is standing inside ``seam``.

        One-shot on purpose: the rival runs the same lane, so its own call walks
        through this wrapper too, and a second competitor would no longer be
        racing the first press but the second.
        """
        real = getattr(paper_service, seam)
        state = {"armed": True, "hits": 0, "rival": None}

        def hook(*args, **kwargs):
            verdict = real(*args, **kwargs)
            if state["armed"]:
                state["armed"] = False
                state["hits"] += 1
                other = TestSession()
                try:
                    state["rival"] = rival(other)
                finally:
                    other.close()
            return verdict

        monkeypatch.setattr(paper_service, seam, hook)
        return state

    def _real_press(self, db, job_id, portfolio_id):
        """A competing press through the same service, on its own session."""
        return paper_service.execute_paper_decision(
            db,
            _owner_id(),
            job_id=job_id,
            skill_unit_id=uuid.UUID(portfolio_id),
            actor_type="human",
        )

    def _cash(self, client, auth_headers, portfolio_id):
        body = client.get(
            f"/api/v1/paper-portfolio/{portfolio_id}", headers=auth_headers
        ).json()
        return float(body["current_virtual_cash"])

    def _watch_collapse(self, monkeypatch, state):
        """Record which key the real collapse branch was handed, and pass it on.

        Without this the two collision sites are indistinguishable from their
        answers: both read back the winner's row and both say
        ``concurrent_insert_collapsed``, so a node named for the order key would
        pass just as happily after colliding on the ledger key. The wrapper calls
        the branch itself, so nothing about the behaviour changes except that the
        error object survives for the node to classify.
        """
        real = paper_service._collapse_racing_press

        def watch(db, identity, exc):
            state["caught"] = exc
            return real(db, identity, exc)

        monkeypatch.setattr(paper_service, "_collapse_racing_press", watch)

    def test_a_rival_trade_collapses_the_later_press_at_the_order_key(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The escape card A measured: the collision is answered, not raised.

        The rival here is the real lane on a second session, committed in the
        window after this press asked the shared policy and before it wrote its
        order — so the row this press loses to is a genuine one, and the error
        comes from the database rather than from a stand-in exception.
        """
        chain, portfolio_id, cash_before, approval_id = self._chain_and_approval(
            client, auth_headers, monkeypatch, tmp_path,
            title="racing presses", closes=take_series(),
        )
        job_id = uuid.UUID(chain["job_id"])
        rival_cash = {}

        def rival(db):
            booked = self._real_press(db, job_id, portfolio_id)
            # The winner's own cash, read after its commit, is the number the
            # losing press must not move a second time.
            rival_cash["after"] = float(db.query(PaperPortfolio).filter(
                PaperPortfolio.id == uuid.UUID(portfolio_id)
            ).one().current_virtual_cash)
            return booked

        state = self._rival_at(monkeypatch, "evaluate_risk", rival)
        self._watch_collapse(monkeypatch, state)
        before = _census()
        response = _execute(client, auth_headers, chain["job_id"], portfolio_id)

        assert state["hits"] == 1, "the door never reached the shared policy"
        winner = state["rival"]
        assert winner["duplicate"] is False, "the rival did not book the trade itself"
        assert winner["execution_status"] == "executed", winner
        # A 500 was the defect: the customer's second press is a duplicate, and it
        # is answered with the row that won rather than with an unhandled failure.
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["duplicate"] is True
        assert payload["concurrent_insert_collapsed"] is True
        assert payload["execution_id"] == winner["execution_id"]
        assert payload["order_id"] == winner["order_id"]
        assert payload["fill_id"] == winner["fill_id"]
        assert payload["approval_id"] == approval_id
        assert payload["execution_status"] == "executed"
        # The door parted ways on the order's idempotency key — the collision card
        # A saw escape. Had it reached the ledger key instead, this node would be
        # measuring the branch that already existed.
        caught = state.get("caught")
        assert caught is not None, "no collision ever reached the collapse branch"
        assert expected_unique_violation(caught) == PAPER_ORDER_IDEMPOTENCY, (
            f"measured wording: {caught.orig}"
        )

        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1 and str(rows[0].id) == winner["execution_id"], rows
        assert _book(portfolio_id) == {
            "decisions": 1, "orders": 1, "fills": 1, "positions": 1,
        }
        cash_now = self._cash(client, auth_headers, portfolio_id)
        assert rival_cash["after"] < cash_before, "the rival booked no trade at all"
        assert cash_now == rival_cash["after"], (
            f"the losing press spent a second time: {cash_now} vs {rival_cash}"
        )
        assert _census() == before

    def test_a_rival_decline_collapses_the_later_press_at_the_ledger_key(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """The branch that existed all along, now with a row in front of it.

        Both presses decline, so neither writes an order and the only key left to
        collide on is the ledger's own business key. The ``skip`` shape is the
        measured one (``_regime_series`` scores p≈0.002 against a 0.5 threshold),
        which keeps both rows the product's rather than hand-inserted.
        """
        chain, portfolio_id, cash_before, approval_id = self._chain_and_approval(
            client, auth_headers, monkeypatch, tmp_path,
            title="racing declines", closes=_regime_series(180),
        )
        job_id = uuid.UUID(chain["job_id"])
        state = self._rival_at(
            monkeypatch, "run_inference",
            lambda db: self._real_press(db, job_id, portfolio_id),
        )
        self._watch_collapse(monkeypatch, state)
        before = _census()
        response = _execute(client, auth_headers, chain["job_id"], portfolio_id)

        assert state["hits"] == 1, "the door never reached the window"
        winner = state["rival"]
        assert winner["execution_status"] == "skipped", winner
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["duplicate"] is True
        assert payload["concurrent_insert_collapsed"] is True
        assert payload["execution_id"] == winner["execution_id"]
        assert payload["execution_status"] == "skipped"
        assert payload["order_id"] is None and payload["fill_id"] is None
        caught = state.get("caught")
        assert caught is not None, "no collision ever reached the collapse branch"
        assert expected_unique_violation(caught) == AGENT_PAPER_EXECUTION_INTENT, (
            f"measured wording: {caught.orig}"
        )

        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1 and str(rows[0].id) == winner["execution_id"], rows
        assert _book(portfolio_id) == EMPTY_BOOK
        assert self._cash(client, auth_headers, portfolio_id) == cash_before
        assert _census() == before

    def test_a_named_key_with_no_intent_row_behind_it_still_escapes(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """Naming the index is not the same as having an answer to give.

        The competing row here is an order with this lane's idempotency key and no
        ledger row behind it. The recognizer can read that collision — and the
        door must still raise, because ``duplicate=True`` would describe an
        execution that exists nowhere. What it may not do is leave its own
        half-written booking behind on the way out.
        """
        chain, portfolio_id, cash_before, approval_id = self._chain_and_approval(
            client, auth_headers, monkeypatch, tmp_path,
            title="orphan order key", closes=take_series(),
        )
        job_id = uuid.UUID(chain["job_id"])
        candidate = _newest_candidate(chain["version_id"])
        key = f"agent-model-{job_id}-{candidate.id}"

        def rival(db):
            db.add(PaperOrder(
                portfolio_id=uuid.UUID(portfolio_id), asset_id=INSTRUMENT,
                side=OrderSide.BUY, quantity=1.0, idempotency_key=key,
                actor_type="human",
            ))
            db.commit()
            return key

        state = self._rival_at(monkeypatch, "evaluate_risk", rival)
        before = _census()
        with pytest.raises(IntegrityError) as raised:
            _execute(client, auth_headers, chain["job_id"], portfolio_id)
        error = raised.value

        assert state["hits"] == 1, "the door never reached the shared policy"
        assert state["rival"] == key
        # The recognizer DID name this constraint, so the escape is the missing
        # intent row and not an unreadable error shape.
        assert expected_unique_violation(error) == PAPER_ORDER_IDEMPOTENCY, (
            f"measured wording: {error.orig}"
        )
        assert _ledger_rows(job_id=chain["job_id"]) == []
        # Only the rival's order stands: this press's decision and order were
        # flushed and then rolled back, and nothing was committed around them.
        assert _book(portfolio_id) == {
            "decisions": 0, "orders": 1, "fills": 0, "positions": 0,
        }
        assert self._cash(client, auth_headers, portfolio_id) == cash_before
        assert _census() == before

    def test_an_integrity_error_this_lane_does_not_own_escapes_unchanged(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        """A real ``NOT NULL`` from inside the fill writer, and the door keeps it.

        Nothing is raced here: the row the fill writer adds is missing its
        ``paper_order_id``, so SQLite refuses it in its own words and the
        collapse branch has to recognise that this is not a duplicate of anything.
        """
        chain, portfolio_id, cash_before, approval_id = self._chain_and_approval(
            client, auth_headers, monkeypatch, tmp_path,
            title="broken fill writer", closes=take_series(),
        )
        real_book_fill = paper_service.book_fill

        def broken_fill(db, **kwargs):
            db.add(PaperFill(
                execution_date="2024-01-02", observed_price=1.0,
                execution_price_before_costs=1.0, slippage_amount=0.0,
                commission_amount=0.0, quantity=1.0, gross_amount=1.0,
                net_cash_change=-1.0,
            ))
            db.flush()

        monkeypatch.setattr(paper_service, "book_fill", broken_fill)
        before = _census()
        with pytest.raises(IntegrityError) as raised:
            _execute(client, auth_headers, chain["job_id"], portfolio_id)
        error = raised.value

        assert "NOT NULL" in str(error.orig).upper(), f"measured wording: {error.orig}"
        assert expected_unique_violation(error) is None, (
            f"measured wording: {error.orig}"
        )
        assert _ledger_rows(job_id=chain["job_id"]) == []
        assert _book(portfolio_id) == EMPTY_BOOK
        assert self._cash(client, auth_headers, portfolio_id) == cash_before

        # 正控: same chain, same approval, same door — only the fill writer
        # un-patched. Without this leg the raise above is equally consistent with
        # a lane that cannot book a trade at all.
        monkeypatch.setattr(paper_service, "book_fill", real_book_fill)
        executed = _execute(client, auth_headers, chain["job_id"], portfolio_id)
        assert executed.status_code == 200, executed.text
        assert executed.json()["duplicate"] is False
        assert _book(portfolio_id) == {
            "decisions": 1, "orders": 1, "fills": 1, "positions": 1,
        }
        assert self._cash(client, auth_headers, portfolio_id) < cash_before
        assert _census() == before


class TestConcurrentIntentOnPostgreSQL:
    """One intent, two presses, one row — an answer this lane cannot give.

    Round-24 item 3 asks for the two-transaction race to be *prepared*, not
    asserted from here. Where two presses of one intent part ways is now in the
    product: :func:`_collapse_racing_press`
    (``app/services/agent_paper_execution.py:745-772``) rolls the losing
    transaction back, re-reads the winner by business key and answers
    ``duplicate=True`` / ``concurrent_insert_collapsed``, and both of the door's
    writes route through it — the ``PaperOrder`` flush at ``:479-486`` and the
    ledger commit at ``:788-795``. Card A measured the earlier state, where only
    the second site was keyed, so the collision on
    ``uq_paper_order_portfolio_idempotency`` walked out as an unhandled error.

    The branch is therefore no longer unexecuted: ``TestRacingPressCollapse``
    (``:1784``) runs it on the SQLite lane against a rival row that really is
    committed, once per key, with the two escape directions kept separate. What
    it cannot do is *race*: there is one transaction per press, nothing ever
    waits, and this file decides which press arrives second. Mutual exclusion —
    two live transactions where the second blocks on the first's unique key and
    then finds the winner instead of a lock error — is the answer only a server
    gives, and SQLite serialises the two writers where PostgreSQL answers an
    integrity error, so a green node here would be a node that never raced.

    The node below therefore stays PG-ONLY and NOT RUN. Its skip names the
    command that would execute it; until someone reports that command's own exit
    code, one-row-per-intent under real contention is a branch that has been
    exercised in one sense and a guarantee that has not been observed in the
    other.
    """

    def test_two_presses_on_one_intent_collapse_into_one_row(
        self, client, auth_headers, monkeypatch, tmp_path
    ):
        requires_postgresql(
            "runs two transactions against one PostgreSQL server and lets the "
            "unique business key decide which press loses; SQLite serialises the "
            "writers and raises a lock error instead of an integrity error, so "
            "this branch cannot be reached on the SQLite lane"
        )
        chain = _chain(
            client,
            auth_headers,
            monkeypatch,
            tmp_path,
            instrument=INSTRUMENT,
            closes=take_series(),
            title="concurrent presses",
        )
        job_id = uuid.UUID(chain["job_id"])
        portfolio_id, _ = _skill_unit(client, auth_headers)
        assert (
            _approve(
                client, auth_headers, chain["job_id"], skill_unit_id=portfolio_id
            ).status_code
            == 200
        )

        # Both presses must be past the lock-free duplicate read (the SELECT at
        # app/services/agent_paper_execution.py:409) before either writes, or the
        # second simply returns the first's row through the fast path and nothing
        # races. The loader call is the last shared step before ``_book_entry``,
        # so the barrier goes behind it.
        barrier = threading.Barrier(2)
        real_inference = paper_service.run_inference

        def lined_up(*args, **kwargs):
            verdict = real_inference(*args, **kwargs)
            barrier.wait(timeout=60)
            return verdict

        monkeypatch.setattr(paper_service, "run_inference", lined_up)

        results = {}

        def press(slot):
            db = TestSession()
            try:
                results[slot] = paper_service.execute_paper_decision(
                    db,
                    _owner_id(),
                    job_id=job_id,
                    skill_unit_id=uuid.UUID(portfolio_id),
                    actor_type="human",
                )
            except Exception as exc:  # kept, never swallowed: the node reports it
                results[slot] = exc
            finally:
                db.close()

        threads = [threading.Thread(target=press, args=(slot,)) for slot in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert not [thread for thread in threads if thread.is_alive()], "a press hung"
        assert set(results) == {0, 1}, results
        payloads = [results[0], results[1]]
        assert all(isinstance(payload, dict) for payload in payloads), payloads

        # One intent, one row, one trade — and the loser says which happened.
        rows = _ledger_rows(job_id=chain["job_id"])
        assert len(rows) == 1
        assert rows[0].execution_status == "executed"
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }
        assert len({payload["execution_id"] for payload in payloads}) == 1
        assert sorted(bool(payload["duplicate"]) for payload in payloads) == [
            False,
            True,
        ]
        collapsed = [payload for payload in payloads if payload["duplicate"]]
        assert len(collapsed) == 1
        assert collapsed[0]["concurrent_insert_collapsed"] is True
        assert collapsed[0]["execution_status"] == "executed"


class TestOtherLanesUnaffected:
    """The direct paper lane still behaves exactly as it did."""

    def test_direct_manual_order_still_books_a_trade(self, client, auth_headers):
        portfolio_id, cash_before = _skill_unit(client, auth_headers)
        created = client.post(
            f"/api/v1/paper-portfolio/{portfolio_id}/orders",
            headers=auth_headers,
            json={
                "asset_id": INSTRUMENT,
                "side": "BUY",
                "order_type": "market",
                "quantity": 10.0,
                "rationale": "manual direct order",
            },
        )
        assert created.status_code == 201, created.text
        order = created.json()
        assert order["status"] == OrderStatus.filled.value
        book = _formal_book(client, auth_headers, portfolio_id)
        assert book["portfolio"]["current_virtual_cash"] < cash_before
        assert _book(portfolio_id) == {
            "decisions": 1,
            "orders": 1,
            "fills": 1,
            "positions": 1,
        }
        # The new lane wrote nothing into the shared tables on its own.
        assert _ledger_rows(skill_unit_id=portfolio_id) == []
