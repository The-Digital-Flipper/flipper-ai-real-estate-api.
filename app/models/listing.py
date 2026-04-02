import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, Numeric
from sqlalchemy.orm import relationship
from app.database import Base, UUIDType


class ActiveListing(Base):
    __tablename__ = "active_listings"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    address = Column(String, nullable=False)
    normalized_address = Column(String, index=True)
    city = Column(String, nullable=False)
    state = Column(String(2), nullable=False)
    zip_code = Column(String(10), nullable=False, index=True)
    parcel_id = Column(String, nullable=True, index=True)
    list_price = Column(Numeric(12, 2), nullable=False)
    original_price = Column(Numeric(12, 2), nullable=True)
    property_type = Column(String, nullable=False)
    bedrooms = Column(Integer, nullable=True)
    bathrooms = Column(Numeric(3, 1), nullable=True)
    sqft = Column(Integer, nullable=True)
    year_built = Column(Integer, nullable=True)
    days_on_market = Column(Integer, default=0)
    status = Column(String, default="ACTIVE")
    listed_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String, nullable=False)
    source_id = Column(String, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    matches = relationship("PropertyMatch", back_populates="active_listing")
