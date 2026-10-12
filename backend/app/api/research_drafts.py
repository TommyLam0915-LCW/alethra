"""Research draft API routes (Phase 2A Backend)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from ..database import get_db
from ..auth.jwt import get_current_user
from ..models.user import User
from ..models.experiment import Experiment
from ..models.research_drafts import (
    ResearchDraft,
    ResearchDraftRevision,
    ResearchDraftAuditEvent,
    DraftStatus,
)
from ..api.schemas import (
    ResearchDraftCreate,
    ResearchDraftUpdate,
    ResearchDraftResponse,
    ResearchDraftRevisionResponse,
    ResearchDraftApproval,
    ResearchDraftQuestionsResponse,
    ResearchDraftAnswer,
    ResearchDraftQuestion,
    ALLOWED_TEMPLATES,
)
from ..lib.mock_research_assistant import mock_research_assistant

router = APIRouter(prefix="/research-drafts", tags=["research-drafts"])

ANSWER_ALLOWED_FIELDS = {
    "title",
    "hypothesis",
    "falsification_criteria",
    "strategy_template",
    "asset_symbol",
    "benchmark_symbol",
    "initial_cash",
    "commission_rate",
    "slippage_rate",
    "research_rules",
}


def _emit_audit_event(
    db: Session,
    draft_id: UUID,
    user_id: UUID,
    event_type: str,
    event_data: dict = None,
):
    """Add an audit event to the session (does NOT commit)."""
    event = ResearchDraftAuditEvent(
        draft_id=draft_id,
        user_id=user_id,
        event_type=event_type,
        event_data=event_data or {},
    )
    db.add(event)


def _create_revision(draft: ResearchDraft, revision_number: str):
    """Build a revision snapshot (does NOT add to session — caller must db.add)."""
    return ResearchDraftRevision(
        draft_id=draft.id,
        revision_number=revision_number,
        title=draft.title,
        hypothesis=draft.hypothesis,
        falsification_criteria=draft.falsification_criteria,
        strategy_template=draft.strategy_template,
        asset_symbol=draft.asset_symbol,
        benchmark_symbol=draft.benchmark_symbol,
        initial_cash=draft.initial_cash,
        commission_rate=draft.commission_rate,
        slippage_rate=draft.slippage_rate,
        research_rules=draft.research_rules,
        status=draft.status,
        assistant_questions=draft.assistant_questions,
        assistant_responses=draft.assistant_responses,
    )


def _get_draft_or_404(db: Session, draft_id: UUID, current_user: User) -> ResearchDraft:
    """Fetch draft with ownership check. Raises 404/403."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this draft")
    return draft


def _validate_experiment_link(db: Session, experiment_id, current_user: User):
    """If experiment_id is provided, verify it exists and belongs to current user."""
    if experiment_id is None:
        return
    experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    if not experiment:
        raise HTTPException(status_code=404, detail="Linked experiment not found")
    if experiment.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Linked experiment does not belong to you")


def _validate_answers_via_schema(answers: dict):
    """Validate answer values through canonical Pydantic validators.

    Constructs a ResearchDraftUpdate from the answers dict so that all
    field validators (symbol format, strategy template, etc.) run.
    Returns the validated data dict.
    """
    filtered = {k: v for k, v in answers.items() if k in ANSWER_ALLOWED_FIELDS}
    if not filtered:
        return filtered
    validated = ResearchDraftUpdate(**filtered)
    return validated.model_dump(exclude_unset=True)


def _draft_fields_dict(draft: ResearchDraft) -> dict:
    return {
        "title": draft.title,
        "hypothesis": draft.hypothesis,
        "falsification_criteria": draft.falsification_criteria,
        "strategy_template": draft.strategy_template,
        "asset_symbol": draft.asset_symbol,
        "benchmark_symbol": draft.benchmark_symbol,
        "initial_cash": draft.initial_cash,
        "commission_rate": draft.commission_rate,
        "slippage_rate": draft.slippage_rate,
        "research_rules": draft.research_rules,
    }


@router.post("/", response_model=ResearchDraftResponse, status_code=status.HTTP_201_CREATED)
def create_research_draft(
    payload: ResearchDraftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new research draft."""
    _validate_experiment_link(db, payload.experiment_id, current_user)

    draft = ResearchDraft(
        user_id=current_user.id,
        experiment_id=payload.experiment_id,
        title=payload.title,
        hypothesis=payload.hypothesis,
        falsification_criteria=payload.falsification_criteria,
        strategy_template=payload.strategy_template,
        asset_symbol=payload.asset_symbol,
        benchmark_symbol=payload.benchmark_symbol,
        initial_cash=payload.initial_cash,
        commission_rate=payload.commission_rate,
        slippage_rate=payload.slippage_rate,
        research_rules=payload.research_rules,
        status=DraftStatus.DRAFT,
    )
    db.add(draft)
    db.flush()

    db.add(_create_revision(draft, "1"))
    _emit_audit_event(db, draft.id, current_user.id, "research_draft_created", {"title": draft.title})

    db.commit()
    db.refresh(draft)
    return draft


@router.get("/", response_model=List[ResearchDraftResponse])
def list_research_drafts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all research drafts for the current user."""
    drafts = (
        db.query(ResearchDraft)
        .filter(ResearchDraft.user_id == current_user.id)
        .order_by(ResearchDraft.created_at.desc())
        .all()
    )
    return drafts


@router.get("/{draft_id}", response_model=ResearchDraftResponse)
def get_research_draft(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific research draft."""
    return _get_draft_or_404(db, draft_id, current_user)


@router.patch("/{draft_id}", response_model=ResearchDraftResponse)
def update_research_draft(
    draft_id: UUID,
    payload: ResearchDraftUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a research draft."""
    draft = _get_draft_or_404(db, draft_id, current_user)
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Cannot modify a finalized draft")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(draft, field, value)

    db.flush()

    revision_count = len(draft.revisions)
    db.add(_create_revision(draft, str(revision_count + 1)))
    _emit_audit_event(db, draft.id, current_user.id, "research_draft_updated", {"updated_fields": list(update_data.keys())})

    db.commit()
    db.refresh(draft)
    return draft


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_draft(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a research draft.

    Deletion cascades to all revisions and audit events (see model cascade).
    This is the intended data-retention behaviour: deleting a draft removes
    all associated research history for privacy.  Audit events are NOT
    immutable once the parent draft is deleted.
    """
    draft = _get_draft_or_404(db, draft_id, current_user)
    db.delete(draft)
    db.commit()


@router.get("/{draft_id}/revisions", response_model=List[ResearchDraftRevisionResponse])
def list_research_draft_revisions(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all revisions for a research draft."""
    _get_draft_or_404(db, draft_id, current_user)

    revisions = (
        db.query(ResearchDraftRevision)
        .filter(ResearchDraftRevision.draft_id == draft_id)
        .order_by(ResearchDraftRevision.created_at.asc())
        .all()
    )
    return revisions


@router.post("/{draft_id}/questions", response_model=ResearchDraftQuestionsResponse)
def generate_draft_questions(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate questions for missing fields in a draft.

    For finalized drafts (accepted/rejected), returns previously generated
    questions without modification — finalized content is not regenerated.
    """
    draft = _get_draft_or_404(db, draft_id, current_user)

    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        existing = draft.assistant_questions or []
        questions = [
            ResearchDraftQuestion(**q) if isinstance(q, dict) else q
            for q in existing
        ]
        return ResearchDraftQuestionsResponse(
            draft_id=draft.id,
            questions=questions,
            generated_at=draft.updated_at,
        )

    draft_fields = _draft_fields_dict(draft)
    questions = mock_research_assistant.generate_questions(draft_fields)

    draft.assistant_questions = [q.model_dump() for q in questions]
    _emit_audit_event(db, draft.id, current_user.id, "research_draft_questions_generated", {"question_count": len(questions)})
    db.commit()

    return ResearchDraftQuestionsResponse(
        draft_id=draft.id,
        questions=questions,
        generated_at=draft.updated_at,
    )


@router.post("/{draft_id}/answers", response_model=ResearchDraftResponse)
def submit_draft_answers(
    draft_id: UUID,
    payload: ResearchDraftAnswer,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit answers to assistant questions."""
    draft = _get_draft_or_404(db, draft_id, current_user)
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Cannot modify a finalized draft")

    try:
        validated = _validate_answers_via_schema(payload.answers)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid answer value: {exc}")
    draft.assistant_responses = payload.answers

    for field, value in validated.items():
        setattr(draft, field, value)

    db.flush()

    revision_count = len(draft.revisions)
    db.add(_create_revision(draft, str(revision_count + 1)))
    _emit_audit_event(db, draft.id, current_user.id, "research_draft_answers_submitted", {"answered_fields": list(payload.answers.keys())})

    db.commit()
    db.refresh(draft)
    return draft


@router.post("/{draft_id}/approve", response_model=ResearchDraftResponse)
def approve_research_draft(
    draft_id: UUID,
    payload: ResearchDraftApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve or reject a research draft.

    Note: an accepted research draft is a reviewed research note — NOT an
    executable StrategySpec.  Acceptance records that the hypothesis and
    methodology have been reviewed; it does not create a runnable strategy.
    """
    draft = _get_draft_or_404(db, draft_id, current_user)
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Draft has already been finalized")

    if payload.action == "accept":
        draft_fields = _draft_fields_dict(draft)
        validation = mock_research_assistant.validate_draft_completeness(draft_fields)
        if not validation["complete"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot accept draft: missing required fields: {', '.join(validation['missing_fields'])}",
            )

        try:
            ResearchDraftUpdate(**{k: v for k, v in draft_fields.items() if v is not None})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Cannot accept draft: {exc}")

        draft.status = DraftStatus.ACCEPTED
        event_type = "research_draft_accepted"
    else:
        draft.status = DraftStatus.REJECTED
        event_type = "research_draft_rejected"

    db.flush()

    revision_count = len(draft.revisions)
    db.add(_create_revision(draft, str(revision_count + 1)))
    _emit_audit_event(db, draft.id, current_user.id, event_type, {"notes": payload.notes})

    db.commit()
    db.refresh(draft)
    return draft


@router.get("/{draft_id}/audit-events", response_model=List[dict])
def list_draft_audit_events(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List audit events for a research draft."""
    _get_draft_or_404(db, draft_id, current_user)

    events = (
        db.query(ResearchDraftAuditEvent)
        .filter(ResearchDraftAuditEvent.draft_id == draft_id)
        .order_by(ResearchDraftAuditEvent.created_at.asc())
        .all()
    )

    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "event_data": e.event_data,
            "created_at": e.created_at.isoformat(),
        }
        for e in events
    ]
