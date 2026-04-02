import bleach
from datetime import datetime
from uuid import UUID
from typing import Optional, Any
from pydantic import BaseModel, field_validator


class AlertCreate(BaseModel):
    user_id: Optional[UUID] = None
    match_id: Optional[UUID] = None
    alert_type: str
    message: str
    details: Optional[Any] = None

    @field_validator("alert_type", "message", mode="before")
    @classmethod
    def sanitize_strings(cls, v):
        if isinstance(v, str):
            return bleach.clean(v)
        return v


class AlertRead(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    match_id: Optional[UUID] = None
    alert_type: str
    message: str
    details: Optional[Any] = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}
