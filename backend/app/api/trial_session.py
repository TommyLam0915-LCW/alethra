"""Single-use password-free entry for the local owner trial.

This exists for one local trial account on this machine. It is not the
customer sign-in path and it is not the candidate test-mode entry: no role can
be chosen, no synthetic account is implied, and the only identity it can ever
open is the one fixed in server configuration. Settings refuses to enable it
outside a local development process.

The link carries a random token that is stored in a 0600 file. A successful
exchange rotates that file, so the same link cannot be used twice. The session
it mints has no ``pwd`` authentication-method claim, so downstream provenance
stays ``unknown`` -- an opened link is not a password check and is never
labelled human.
"""
import fcntl
import hmac
import logging
import os
import secrets
import stat
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.candidate_access import is_loopback_host
from app.auth.jwt import create_access_token
from app.auth.rate_limit import rate_limit_dependency
from app.auth.schemas import TrialLoginRequest, TrialLoginResponse
from app.config import Settings, get_settings
from app.database import get_db
from app.models.user import User
from app.services import emit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

ENTRY_LABEL = "Local trial entry · single-use link"
TRIAL_LOGIN_ATTEMPTS_PER_WINDOW = 10
TRIAL_LOGIN_WINDOW_SECONDS = 60


class _TokenFileError(Exception):
    """The operator-side token file is missing, unsafe or unreadable."""


def _peer_is_loopback(request: Request) -> bool:
    # The TCP peer only. Host and X-Forwarded-* are caller supplied.
    return is_loopback_host(request.client.host if request.client else None)


def _consume_token(path: str, supplied: str) -> bool:
    """Compare the supplied token and, on a match, rotate it under a lock.

    Rotation happens while holding an exclusive lock on the same file, so two
    concurrent requests cannot both redeem one link.
    """
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _TokenFileError(f"cannot open token file: {exc.strerror}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _TokenFileError("token file is not a regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise _TokenFileError("token file is readable by group or other")
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
            current = handle.read().strip()
        candidate = (supplied or "").strip()
        # compare_digest refuses non-ASCII str, so screen that first; a wrong
        # length or charset is simply not a match.
        if (
            not current
            or not candidate
            or not candidate.isascii()
            or len(candidate) != len(current)
            or not hmac.compare_digest(current, candidate)
        ):
            return False
        fresh = secrets.token_urlsafe(32)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, (fresh + "\n").encode("utf-8"))
        os.fsync(fd)
        return True
    finally:
        os.close(fd)


@router.post(
    "/trial-login",
    response_model=TrialLoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange a single-use local trial link for the trial account's session",
)
def create_trial_session(
    payload: TrialLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _rate_limit=Depends(
        rate_limit_dependency(
            TRIAL_LOGIN_ATTEMPTS_PER_WINDOW,
            TRIAL_LOGIN_WINDOW_SECONDS,
            bucket="auth_trial_login",
        )
    ),
):
    if not settings.trial_passwordless_enabled:
        # A normal product deployment never advertises this entry.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trial sign-in is not available",
        )
    if not _peer_is_loopback(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trial sign-in is only available from this machine",
        )

    try:
        accepted = _consume_token(settings.trial_passwordless_token_file, payload.token)
    except _TokenFileError as exc:
        # Operator misconfiguration, not a client error; the detail goes to the
        # server log only and never echoes the token.
        logger.error("trial passwordless token file unusable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trial sign-in is not available",
        ) from exc
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This trial link is no longer valid",
        )

    email = settings.trial_passwordless_email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trial account is not available",
        )
    if user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trial sign-in never issues an admin session",
        )

    minutes = max(1, min(settings.trial_passwordless_session_minutes, 1440))
    # No `pwd` claim: the session was opened by a link, so actor provenance
    # downstream stays UNKNOWN rather than claiming a verified password entry.
    token = create_access_token(
        data={"sub": str(user.id), "amr": ["trial_link"]},
        expires_delta=timedelta(minutes=minutes),
    )
    emit_event(
        db,
        entity_type="user",
        entity_id=str(user.id),
        event_type="trial_passwordless_login",
        actor_id=user.id,
        actor_type="unknown",
        payload={
            "entry": "trial_link",
            "single_use": True,
            "session_minutes": minutes,
        },
    )
    db.commit()

    # Never log the token, the link or the email.
    logger.info("trial passwordless session issued expires_in=%ss", minutes * 60)
    return TrialLoginResponse(
        access_token=token,
        expires_in=minutes * 60,
        entry_label=ENTRY_LABEL,
        user=user,
    )
