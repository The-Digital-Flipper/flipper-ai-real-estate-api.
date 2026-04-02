"""
Market indicators API route.

Exposes the latest FRED (Federal Reserve Economic Data) economic time-series
observations stored in the ``market_indicators`` table.  Data is refreshed
weekly by the ``run_fred_task`` Celery beat task.

Endpoints
---------
GET /market/indicators
    Returns the most-recent observation for each tracked FRED series:
        - MORTGAGE30US  — 30-year fixed mortgage rate
        - MORTGAGE15US  — 15-year fixed mortgage rate
        - CSUSHPINSA    — Case-Shiller national home price index
        - HOUST         — Housing starts (000s of units)
        - MSPUS         — Median sales price of houses sold
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.fred_service import get_market_indicators

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/indicators", response_model=List[Dict[str, Any]])
async def market_indicators(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the latest macroeconomic real estate market indicators from
    the FRED database (Federal Reserve Bank of St. Louis).

    Data is updated weekly via the background Celery task.  If no data
    has been fetched yet an empty list is returned.
    """
    return await get_market_indicators(db)
