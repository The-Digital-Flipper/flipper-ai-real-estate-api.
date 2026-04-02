import bleach
from datetime import datetime
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("email", mode="before")
    @classmethod
    def sanitize_email(cls, v):
        return bleach.clean(str(v))


class UserUpdate(BaseModel):
    notification_email: Optional[EmailStr] = None
    email_alerts_enabled: Optional[bool] = None

    @field_validator("notification_email", mode="before")
    @classmethod
    def sanitize_notification_email(cls, v):
        if v is not None:
            return bleach.clean(str(v))
        return v


class UserRead(BaseModel):
    id: UUID
    email: str
    is_active: bool
    is_admin: bool
    notification_email: Optional[str] = None
    email_alerts_enabled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: str | None = None
