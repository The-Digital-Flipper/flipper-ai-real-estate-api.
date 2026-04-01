import bleach
from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel, field_validator


class SavedSearchCreate(BaseModel):
    name: str
    filters: dict[str, Any] = {}

    @field_validator("name", mode="before")
    @classmethod
    def sanitize_name(cls, v):
        if isinstance(v, str):
            return bleach.clean(v)
        return v


class SavedSearchRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    filters: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}
