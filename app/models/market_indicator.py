"""
MarketIndicator model — stores FRED economic time-series observations.

Each row represents one observation (one date) for one FRED series.
The table is populated by the ``run_fred_task`` Celery task which calls
the St. Louis Fed FRED API (https://fred.stlouisfed.org/docs/api/fred/).
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Date, Numeric, DateTime, UniqueConstraint
from app.database import Base, UUIDType


class MarketIndicator(Base):
    __tablename__ = "market_indicators"

    id = Column(UUIDType, primary_key=True, default=uuid.uuid4)
    # FRED series identifier, e.g. "MORTGAGE30US"
    series_id = Column(String(64), nullable=False, index=True)
    # Human-readable name, e.g. "30-Year Fixed Mortgage Rate"
    series_name = Column(String(256), nullable=True)
    # Observation date (the date the value applies to)
    observation_date = Column(Date, nullable=False)
    # The data value
    value = Column(Numeric(18, 6), nullable=True)
    # Units string from FRED, e.g. "Percent" or "Index Jan 2000=100"
    units = Column(String(128), nullable=True)
    # When this record was fetched/refreshed
    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("series_id", "observation_date", name="uq_series_date"),
    )
