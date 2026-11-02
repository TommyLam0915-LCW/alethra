from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: Optional[str] = None
    user_type: str = Field(default="individual", pattern="^(individual|educator|team|student)$")
    # Defaulting to false rather than omitting the field keeps the refusal a
    # readable 400 instead of a bare validation error, and makes an account
    # impossible to create without the caller stating the disclosure was read.
    risk_acknowledged: bool = Field(
        default=False,
        description=(
            "Must be true. The account is refused unless the caller states the user has read the "
            "risk disclosure at /legal: Alethra is a research tool, paper fills are not live "
            "trades, and no return is guaranteed."
        ),
    )


class UserResponse(BaseModel):
    id: UUID
    email: str
    display_name: Optional[str]
    user_type: str
    created_at: datetime
    is_active: bool
    is_admin: bool

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class CandidateSessionAccount(BaseModel):
    """One allowlisted synthetic role; the account email is never exposed."""

    role: str
    label_zh: str


class CandidateSessionStatus(BaseModel):
    enabled: bool
    synthetic: bool = True
    label_zh: Optional[str] = None
    session_minutes: Optional[int] = None
    accounts: list[CandidateSessionAccount] = []


class CandidateSessionRequest(BaseModel):
    """The client may only choose a role. It can never name a user or email."""

    model_config = ConfigDict(extra="forbid")

    role: str = "owner"


class CandidateSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    mode_label_zh: str
    user: UserResponse


class TrialLoginRequest(BaseModel):
    """Only the link token. The account is fixed server-side and cannot be named."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=20, max_length=512)


class TrialLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    entry_label: str
    user: UserResponse
