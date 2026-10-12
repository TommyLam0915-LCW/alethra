"""Research draft API routes (Phase 2A)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from ..database import get_db
from ..auth.jwt import get_current_user
from ..models.user import User
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
)
from ..lib.mock_research_assistant import mock_research_assistant

router = APIRouter(prefix="/research-drafts", tags=["research-drafts"])


def emit_draft_audit_event(
    db: Session,
    draft_id: UUID,
    user_id: UUID,
    event_type: str,
    event_data: dict = None,
):
    """Emit an audit event for a draft operation."""
    event = ResearchDraftAuditEvent(
        draft_id=draft_id,
        user_id=user_id,
        event_type=event_type,
        event_data=event_data or {},
    )
    db.add(event)
    db.commit()


def create_revision(db: Session, draft: ResearchDraft, revision_number: str):
    """Create a revision snapshot of the current draft state."""
    revision = ResearchDraftRevision(
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
    db.add(revision)
    return revision


@router.post("/", response_model=ResearchDraftResponse, status_code=status.HTTP_201_CREATED)
def create_research_draft(
    payload: ResearchDraftCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new research draft."""
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
    db.commit()
    db.refresh(draft)

    create_revision(db, draft, "1")
    db.commit()

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        "research_draft_created",
        {"title": draft.title},
    )

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
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this draft")
    return draft


@router.patch("/{draft_id}", response_model=ResearchDraftResponse)
def update_research_draft(
    draft_id: UUID,
    payload: ResearchDraftUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a research draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this draft")
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Cannot modify a finalized draft")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(draft, field, value)

    revision_count = len(draft.revisions)
    create_revision(db, draft, str(revision_count + 1))
    db.commit()
    db.refresh(draft)

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        "research_draft_updated",
        {"updated_fields": list(update_data.keys())},
    )

    return draft


@router.delete("/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_research_draft(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a research draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this draft")

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        "research_draft_deleted",
        {"title": draft.title},
    )

    db.delete(draft)
    db.commit()


@router.get("/{draft_id}/revisions", response_model=List[ResearchDraftRevisionResponse])
def list_research_draft_revisions(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all revisions for a research draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this draft")

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
    """Generate questions for missing fields in a draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this draft")

    draft_fields = {
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

    questions = mock_research_assistant.generate_questions(draft_fields)

    draft.assistant_questions = [q.model_dump() for q in questions]
    db.commit()

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        "research_draft_questions_generated",
        {"question_count": len(questions)},
    )

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
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to modify this draft")
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Cannot modify a finalized draft")

    draft.assistant_responses = payload.answers

    allowed_fields = {
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

    for field, value in payload.answers.items():
        if field in allowed_fields:
            setattr(draft, field, value)

    revision_count = len(draft.revisions)
    create_revision(db, draft, str(revision_count + 1))
    db.commit()
    db.refresh(draft)

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        "research_draft_answers_submitted",
        {"answered_fields": list(payload.answers.keys())},
    )

    return draft


@router.post("/{draft_id}/approve", response_model=ResearchDraftResponse)
def approve_research_draft(
    draft_id: UUID,
    payload: ResearchDraftApproval,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve or reject a research draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to approve this draft")
    if draft.status in [DraftStatus.ACCEPTED, DraftStatus.REJECTED]:
        raise HTTPException(status_code=400, detail="Draft has already been finalized")

    if payload.action == "accept":
        validation = mock_research_assistant.validate_draft_completeness(
            {
                "title": draft.title,
                "hypothesis": draft.hypothesis,
                "falsification_criteria": draft.falsification_criteria,
                "strategy_template": draft.strategy_template,
                "asset_symbol": draft.asset_symbol,
            }
        )
        if not validation["complete"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot accept draft: missing required fields: {', '.join(validation['missing_fields'])}",
            )
        draft.status = DraftStatus.ACCEPTED
        event_type = "research_draft_accepted"
    else:
        draft.status = DraftStatus.REJECTED
        event_type = "research_draft_rejected"

    revision_count = len(draft.revisions)
    create_revision(db, draft, str(revision_count + 1))
    db.commit()
    db.refresh(draft)

    emit_draft_audit_event(
        db,
        draft.id,
        current_user.id,
        event_type,
        {"notes": payload.notes},
    )

    return draft


@router.get("/{draft_id}/audit-events", response_model=List[dict])
def list_draft_audit_events(
    draft_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List audit events for a research draft."""
    draft = db.query(ResearchDraft).filter(ResearchDraft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to access this draft")

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
