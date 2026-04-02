import bleach
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, field_validator


class ActiveListingCreate(BaseModel):
    address: str
    city: str
    state: str
    zip_code: str
    parcel_id: Optional[str] = None
    list_price: Decimal
    original_price: Optional[Decimal] = None
    property_type: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[Decimal] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    days_on_market: int = 0
    status: str = "ACTIVE"
    listed_at: Optional[datetime] = None
    source: str
    source_id: str

    @field_validator("address", "city", "state", "zip_code", "property_type", "status", "source", "source_id", mode="before")
    @classmethod
    def sanitize_strings(cls, v):
        if isinstance(v, str):
            return bleach.clean(v)
        return v


class ActiveListingRead(BaseModel):
    id: UUID
    address: str
    normalized_address: Optional[str] = None
    city: str
    state: str
    zip_code: str
    parcel_id: Optional[str] = None
    list_price: Decimal
    original_price: Optional[Decimal] = None
    property_type: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[Decimal] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    days_on_market: int
    status: str
    listed_at: datetime
    source: str
    source_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
