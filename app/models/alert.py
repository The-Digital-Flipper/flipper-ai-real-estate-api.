import uuid
from datetime import datetime
from sqlalchemy import Column, Boolean, DateTime, String, ForeignKey
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from app.database import Base, UUIDType


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUIDType, ForeignKey("users.id"), nullable=True)
    match_id = Column(UUIDType, ForeignKey("property_matches.id"), nullable=True)
    alert_type = Column(String, nullable=False)
    message = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="alerts")
    match = relationship("PropertyMatch", back_populates="alerts")
