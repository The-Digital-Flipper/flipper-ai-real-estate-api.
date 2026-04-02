"""
WalkScore model — stores Walk Score / Transit Score / Bike Score per property.

Rows are keyed by ``property_source`` + ``property_source_id`` to link back to
either an ``ActiveListing`` or a ``DistressedProperty`` without a foreign-key
dependency.  The Walk Score API is called via ``app/services/walk_score_enricher``.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Numeric, DateTime, UniqueConstraint
from app.database import Base, UUIDType


class WalkScore(Base):
    __tablename__ = "walk_scores"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    # Which model type the scores belong to: "LISTING" or "DISTRESSED"
    property_source = Column(String(64), nullable=False)
    # Matches ActiveListing.source_id or DistressedProperty.source_id
    property_source_id = Column(String(256), nullable=False)
    # Geocoded coordinates (from Nominatim) used for the Walk Score call
    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    # Walk Score (0-100): pedestrian-friendliness
    walk_score = Column(Integer, nullable=True)
    walk_description = Column(String(128), nullable=True)
    # Transit Score (0-100): public-transit quality
    transit_score = Column(Integer, nullable=True)
    transit_description = Column(String(128), nullable=True)
    # Bike Score (0-100): bikeability
    bike_score = Column(Integer, nullable=True)
    bike_description = Column(String(128), nullable=True)
    # When scores were last refreshed
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "property_source",
            "property_source_id",
            name="uq_walkscore_property",
        ),
    )
