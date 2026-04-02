from typing import Optional
from pydantic import BaseModel


class ListingFilter(BaseModel):
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    property_type: Optional[str] = None
    status: Optional[str] = None
    page: int = 1
    per_page: int = 20


class PropertyFilter(BaseModel):
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    property_type: Optional[str] = None
    foreclosure_stage: Optional[str] = None
    keyword: Optional[str] = None
    page: int = 1
    per_page: int = 20
