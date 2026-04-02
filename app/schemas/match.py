from datetime import datetime
from decimal import Decimal
from uuid import UUID
from typing import Optional
from pydantic import BaseModel
from app.schemas.property import DistressedPropertyRead
from app.schemas.listing import ActiveListingRead


class PropertyMatchRead(BaseModel):
    id: UUID
    distressed_property_id: UUID
    active_listing_id: UUID
    match_score: Decimal
    match_method: str
    deal_score: Optional[Decimal] = None
    estimated_value: Optional[Decimal] = None
    profit_potential: Optional[Decimal] = None
    price_discount_pct: Optional[Decimal] = None
    is_flagged: bool
    matched_at: datetime
    distressed_property: Optional[DistressedPropertyRead] = None
    active_listing: Optional[ActiveListingRead] = None

    model_config = {"from_attributes": True}
