from datetime import datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


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

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
