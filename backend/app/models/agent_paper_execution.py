"""One durable row for every paper decision an agent model was consulted about.

The agent screening slice already had a formal inference door
(``POST /training/jobs/{id}/inference``) and a formal owner approval door
(``PUT /training/jobs/{id}/activation``), but nothing joined them to a trade: a
score could be produced and a paper order could be placed, and no row anywhere
said this order came from that artifact under that approval on that skill
account. Without that row the approval has no consumable effect — revoking it
changes nothing a customer can read back.

This table is that join, written at the moment of execution from rows the
server re-read for itself. Every identity column is a foreign key to the record
that carried it (job, snapshot, strategy version, activation approval,
candidate) rather than a string a client repeated, so a formal read of one row
answers "which model, which artifact, which approval, which skill account"
without trusting the request that produced it.

Why a new table and not an existing column:

* ``decision_records.linked_inference_id`` is documented at export as "the
  ``usage_records.id`` of the model execution behind the decision"
  (``app/api/paper_portfolio.py`` ``JOURNAL_COLUMN_SEMANTICS``). An agent job id
  written there would be read back as a personal billing row that does not
  exist, and ``app/services/decision_lineage.py`` refuses such a row for good
  reason.
* ``source_run_id`` / ``source_trade_id`` name a research run and a trade; the
  agent job is neither.
* ``risk_flags`` is free JSON with no key contract and no read path, so it
  cannot carry an authorization the platform has to prove.

Uniqueness is the business key ``(skill_unit_id, job_id, candidate_id)``: one
skill account runs one artifact's judgment on one candidate once, so a retried
request returns the row that already exists instead of spending a second
intent.
"""

import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.lib.timeutil import utcnow

# The verdict the model returned, and what the lane did with it. ``skipped`` and
# ``blocked`` both mean no trade exists; they differ in who refused — the model
# said skip, or the shared risk policy / fill-time affordability said no.
EXECUTED = "executed"
SKIPPED = "skipped"
BLOCKED = "blocked"
EXECUTION_STATUSES = (EXECUTED, SKIPPED, BLOCKED)

# Recorded from the frozen snapshot's own split manifest, so an in-sample score
# can never be read back as an out-of-sample one.
SPLIT_MEMBERSHIPS = ("train", "validation", "test", "not_in_snapshot")


class AgentPaperExecution(Base):
    __tablename__ = "agent_paper_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # The skill account (paper portfolio) this execution trades, and the record
    # it must not be confused with: a foreign portfolio's approval is no
    # authorization here, so the scope is stored rather than implied.
    skill_unit_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_portfolios.id"), nullable=False
    )
    job_id = Column(
        UUID(as_uuid=True), ForeignKey("agent_strategy_training_jobs.id"), nullable=False
    )
    snapshot_id = Column(
        UUID(as_uuid=True), ForeignKey("agent_dataset_snapshots.id"), nullable=False
    )
    strategy_version_id = Column(
        UUID(as_uuid=True), ForeignKey("experiment_versions.id"), nullable=False
    )
    approval_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_model_activation_approvals.id"),
        nullable=False,
    )
    candidate_id = Column(
        UUID(as_uuid=True), ForeignKey("agent_candidates.id"), nullable=False
    )
    decision_record_id = Column(
        UUID(as_uuid=True), ForeignKey("decision_records.id"), nullable=True
    )
    order_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_orders.id"), nullable=True
    )
    fill_id = Column(
        UUID(as_uuid=True), ForeignKey("paper_fills.id"), nullable=True
    )

    instrument_id = Column(String(64), nullable=False)
    signal_date = Column(String(10), nullable=False)
    execution_date = Column(String(10), nullable=True)
    split_membership = Column(String(16), nullable=False)

    model_decision = Column(String(8), nullable=False)
    positive_probability = Column(Float, nullable=False)
    threshold = Column(Float, nullable=False)
    inference_policy_version = Column(String(64), nullable=False)

    training_contract_version = Column(String(64), nullable=False)
    dataset_checksum = Column(String(64), nullable=False)
    artifact_checksum = Column(String(64), nullable=False)
    execution_policy_version = Column(String(64), nullable=False)

    quantity = Column(Float, nullable=True)
    observed_price = Column(Float, nullable=True)
    execution_price = Column(Float, nullable=True)
    commission_rate = Column(Float, nullable=True)

    execution_status = Column(String(16), nullable=False)
    blocked_reason = Column(String(64), nullable=True)
    risk_check_json = Column(JSONB, nullable=True)
    # The verified credential channel of the request that pressed the button.
    # This is not who decided: the model decided, which is why the paper rows
    # carry ``actor_type="system"`` and this column keeps the operator label.
    actor_type = Column(String(16), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "skill_unit_id",
            "job_id",
            "candidate_id",
            name="uq_agent_paper_execution_intent",
        ),
        Index("ix_agent_paper_execution_owner", "owner_id"),
        Index("ix_agent_paper_execution_job", "job_id"),
        Index("ix_agent_paper_execution_skill", "skill_unit_id"),
    )


def execution_payload(row: AgentPaperExecution) -> dict:
    """The read shape of one execution, with the trade links spelled out."""
    return {
        "execution_id": str(row.id),
        "owner_id": str(row.owner_id),
        "skill_unit_id": str(row.skill_unit_id),
        "job_id": str(row.job_id),
        "snapshot_id": str(row.snapshot_id),
        "strategy_version_id": str(row.strategy_version_id),
        "approval_id": str(row.approval_id),
        "candidate_id": str(row.candidate_id),
        "decision_record_id": str(row.decision_record_id)
        if row.decision_record_id
        else None,
        "order_id": str(row.order_id) if row.order_id else None,
        "fill_id": str(row.fill_id) if row.fill_id else None,
        "instrument_id": row.instrument_id,
        "signal_date": row.signal_date,
        "execution_date": row.execution_date,
        "split_membership": row.split_membership,
        "model_decision": row.model_decision,
        "positive_probability": row.positive_probability,
        "threshold": row.threshold,
        "inference_policy_version": row.inference_policy_version,
        "training_contract_version": row.training_contract_version,
        "dataset_checksum": row.dataset_checksum,
        "artifact_checksum": row.artifact_checksum,
        "execution_policy_version": row.execution_policy_version,
        "quantity": row.quantity,
        "observed_price": row.observed_price,
        "execution_price": row.execution_price,
        "commission_rate": row.commission_rate,
        "execution_status": row.execution_status,
        "blocked_reason": row.blocked_reason,
        "risk_check": row.risk_check_json,
        "actor_type": row.actor_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        # The same three boundaries every door in this slice states: a paper
        # execution is a simulation inside one owner account.
        "paper_only": True,
        "runs_live_trading": False,
        "movable_to_customer_environment": False,
    }
