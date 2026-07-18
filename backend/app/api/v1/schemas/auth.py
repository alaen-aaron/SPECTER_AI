"""Pydantic v2 request/response schemas for authentication endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["alice@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["correct-horse-battery"])
    full_name: str | None = Field(default=None, examples=["Alice Smith"])


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["alice@example.com"])
    password: str = Field(examples=["correct-horse-battery"])


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
