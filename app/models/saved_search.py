import uuid
from datetime import datetime
from sqlalchemy import Column, DateTime, String, ForeignKey
from sqlalchemy.types import JSON
from sqlalchemy.orm import relationship
from app.database import Base, UUIDType


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    user_id = Column(UUIDType, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    filters = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="saved_searches")
