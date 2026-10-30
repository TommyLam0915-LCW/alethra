"""Password-free entry for the disposable local candidate stack.

The endpoints only exist for a stack booted with CANDIDATE_PASSWORDLESS_MODE,
which Settings refuses outside a local development environment. They mint a
short-lived ordinary JWT for one synthetic account named by the *server-side*
allowlist, so every downstream authorization check still runs against a real
identity. Nothing here writes passwords, replaces hashes, or widens a role.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.candidate_access import is_loopback_host, parse_account_allowlist, role_label
from app.auth.jwt import create_access_token
from app.auth.schemas import (
    CandidateSessionAccount,
    CandidateSessionRequest,
    CandidateSessionResponse,
    CandidateSessionStatus,
)
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

MODE_LABEL_ZH = "本機測試模式 · 合成資料"


def _allowlist(settings: Settings) -> dict[str, str]:
    if not settings.candidate_passwordless_mode:
        return {}
    try:
        return parse_account_allowlist(settings.candidate_passwordless_accounts)
    except ValueError:
        # Startup validation should have refused this; never fall back to a
        # broader identity set at request time.
        logger.error("candidate passwordless allowlist is invalid; refusing all sessions")
        return {}


def _peer_is_loopback(request: Request) -> bool:
    # request.client.host is the TCP peer. Host / X-Forwarded-* are caller
    # supplied and are deliberately never consulted here.
    return is_loopback_host(request.client.host if request.client else None)


@router.get("/candidate-session", response_model=CandidateSessionStatus)
def candidate_session_status(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    # A non-loopback caller learns nothing beyond "not available".
    if not _peer_is_loopback(request):
        return CandidateSessionStatus(enabled=False)
    accounts = _allowlist(settings)
    if not accounts:
        return CandidateSessionStatus(enabled=False)
    return CandidateSessionStatus(
        enabled=True,
        label_zh=MODE_LABEL_ZH,
        session_minutes=settings.candidate_passwordless_session_minutes,
        accounts=[
            CandidateSessionAccount(role=role, label_zh=role_label(role))
            for role in accounts
        ],
    )


@router.post(
    "/candidate-session",
    response_model=CandidateSessionResponse,
    status_code=status.HTTP_200_OK,
)
def create_candidate_session(
    payload: CandidateSessionRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    accounts = _allowlist(settings)
    if not accounts:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate test mode is not enabled",
        )
    if not _peer_is_loopback(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate test mode is only available from this machine",
        )

    email = accounts.get(payload.role)
    if email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown candidate test role",
        )

    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate test account is not available",
        )
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Candidate test mode never issues an admin session",
        )

    minutes = max(1, min(settings.candidate_passwordless_session_minutes, 120))
    token = create_access_token(
        data={"sub": str(user.id), "cand": "local_passwordless", "role": payload.role},
        expires_delta=timedelta(minutes=minutes),
    )
    # Never log the token, the email, or the hash: role only.
    logger.info("candidate passwordless session issued role=%s expires_in=%ss", payload.role, minutes * 60)
    return CandidateSessionResponse(
        access_token=token,
        expires_in=minutes * 60,
        role=payload.role,
        mode_label_zh=MODE_LABEL_ZH,
        user=user,
    )
