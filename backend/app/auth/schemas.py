from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    display_name: Optional[str] = None
    user_type: str = Field(default="individual", pattern="^(individual|educator|team|student)$")


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
