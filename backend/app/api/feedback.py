"""In-product feedback submission.

The /feedback page used to prepare text the user had to copy out by hand. This
is the persisted path: a logged-in user submits the same fields, the row is
stored against that user, and the page only reports success after reading the
stored row back from this API.

Nothing is collected automatically. The payload is exactly what the form
contains — no cookies, tokens, environment, request headers or account
secrets are attached server-side either.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.user_feedback import UserFeedback

router = APIRouter(tags=["feedback"])

# Kept in step with FEEDBACK_CATEGORIES in frontend/src/lib/feedbackDraft.ts.
FEEDBACK_CATEGORIES = (
    "research_results",
    "strategy_setup",
    "data_instruments",
    "paper_trading",
    "account_access",
    "other",
)

MAX_STEPS = 4000
MAX_ACTUAL = 4000
MAX_EXPECTED = 2000
MAX_PAGE = 200
MAX_ERROR_REFERENCE = 200
LIST_PAGE_SIZE_MAX = 100


class FeedbackCreate(BaseModel):
    category: str
    page: Optional[str] = Field(default=None, max_length=MAX_PAGE)
    steps: str = Field(..., min_length=1, max_length=MAX_STEPS)
    expected: Optional[str] = Field(default=None, max_length=MAX_EXPECTED)
    actual: str = Field(..., min_length=1, max_length=MAX_ACTUAL)
    error_reference: Optional[str] = Field(
        default=None, max_length=MAX_ERROR_REFERENCE
    )

    @field_validator("category")
    @classmethod
    def _validate_category(cls, value: str) -> str:
        if value not in FEEDBACK_CATEGORIES:
            raise ValueError(
                "category must be one of " + ", ".join(FEEDBACK_CATEGORIES)
            )
        return value

    @field_validator("steps", "actual")
    @classmethod
    def _require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This field cannot be blank")
        return value.strip()

    @field_validator("page", "expected", "error_reference")
    @classmethod
    def _normalise_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class FeedbackResponse(BaseModel):
    id: UUID
    category: str
    page: Optional[str]
    steps: str
    expected: Optional[str]
    actual: str
    error_reference: Optional[str]
    created_at: datetime
    # The row is attributed to the authenticated account; the response echoes
    # that attribution so the client can prove whose record it read back.
    submitted_by: UUID

    model_config = ConfigDict(from_attributes=True)


def _serialize(feedback: UserFeedback) -> FeedbackResponse:
    return FeedbackResponse(
        id=feedback.id,
        category=feedback.category,
        page=feedback.page,
        steps=feedback.steps,
        expected=feedback.expected,
        actual=feedback.actual,
        error_reference=feedback.error_reference,
        created_at=feedback.created_at,
        submitted_by=feedback.user_id,
    )


@router.post("/feedback", response_model=FeedbackResponse, status_code=201)
def submit_feedback(
    payload: FeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feedback = UserFeedback(
        user_id=current_user.id,
        category=payload.category,
        page=payload.page,
        steps=payload.steps,
        expected=payload.expected,
        actual=payload.actual,
        error_reference=payload.error_reference,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return _serialize(feedback)


@router.get("/feedback", response_model=List[FeedbackResponse])
def list_my_feedback(
    limit: int = Query(default=20, ge=1, le=LIST_PAGE_SIZE_MAX),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(UserFeedback)
        .filter(UserFeedback.user_id == current_user.id)
        .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize(row) for row in rows]


@router.get("/feedback/{feedback_id}", response_model=FeedbackResponse)
def read_feedback(
    feedback_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Scoping the row to the caller in the query means another account's
    # feedback and a feedback that does not exist produce the same answer.
    # A 403 on the owned row would confirm the id exists and belongs to
    # someone else, which is the leak the rest of the API avoids.
    feedback = (
        db.query(UserFeedback)
        .filter(
            UserFeedback.id == feedback_id,
            UserFeedback.user_id == current_user.id,
        )
        .first()
    )
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return _serialize(feedback)
