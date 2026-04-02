import uuid
from datetime import datetime
from sqlalchemy import Column, Boolean, DateTime, Numeric, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base, UUIDType


class PropertyMatch(Base):
    __tablename__ = "property_matches"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    distressed_property_id = Column(UUIDType, ForeignKey("distressed_properties.id"), nullable=False)
    active_listing_id = Column(UUIDType, ForeignKey("active_listings.id"), nullable=False)
    match_score = Column(Numeric(5, 2), nullable=False)
    match_method = Column(String, nullable=False)
    deal_score = Column(Numeric(5, 2), nullable=True)
    estimated_value = Column(Numeric(12, 2), nullable=True)
    profit_potential = Column(Numeric(12, 2), nullable=True)
    price_discount_pct = Column(Numeric(5, 2), nullable=True)
    is_flagged = Column(Boolean, default=False)
    matched_at = Column(DateTime, default=datetime.utcnow)
    distressed_property = relationship("DistressedProperty", back_populates="matches")
    active_listing = relationship("ActiveListing", back_populates="matches")
    alerts = relationship("Alert", back_populates="match")
