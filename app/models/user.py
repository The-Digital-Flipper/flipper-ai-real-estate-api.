import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from app.database import Base, UUIDType


class User(Base):
    __tablename__ = "users"
    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    alerts = relationship("Alert", back_populates="user")
    saved_searches = relationship("SavedSearch", back_populates="user")
