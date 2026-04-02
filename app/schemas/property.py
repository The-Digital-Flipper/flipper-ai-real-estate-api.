import bleach
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, field_validator


class DistressedPropertyCreate(BaseModel):
    address: str
    city: str
    state: str
    zip_code: str
    parcel_id: Optional[str] = None
    property_type: str
    foreclosure_stage: str
    list_price: Optional[Decimal] = None
    estimated_value: Optional[Decimal] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[Decimal] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    source: str
    source_id: str

    @field_validator("address", "city", "state", "zip_code", "property_type", "foreclosure_stage", "source", "source_id", mode="before")
    @classmethod
    def sanitize_strings(cls, v):
        if isinstance(v, str):
            return bleach.clean(v)
        return v


class DistressedPropertyRead(BaseModel):
    id: UUID
    address: str
    normalized_address: Optional[str] = None
    city: str
    state: str
    zip_code: str
    parcel_id: Optional[str] = None
    property_type: str
    foreclosure_stage: str
    list_price: Optional[Decimal] = None
    estimated_value: Optional[Decimal] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[Decimal] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    source: str
    source_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
