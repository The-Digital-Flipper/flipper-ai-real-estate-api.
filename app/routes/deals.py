"""
Deal analysis endpoints.

These are the highest-value endpoints in the Flipper AI platform — they turn
a raw ``PropertyMatch`` into a full investment analysis with flip ROI, cap
rate, cash-on-cash return, deal grade, and a buy/skip recommendation.

Endpoints
---------
GET /deals/{match_id}/analyze
    Full investment analysis for a single matched deal.

GET /deals/top
    Top N deals by deal_score, each with a brief analysis summary.
    Useful for a "hot deals" dashboard widget.
"""
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.match import PropertyMatch
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.deal_analyzer import analyze_deal

router = APIRouter(prefix="/deals", tags=["deals"])


@router.get("/top", response_model=List[Dict[str, Any]])
async def top_deals(
    limit: int = Query(10, ge=1, le=50, description="Number of top deals to return"),
    min_deal_score: float = Query(50.0, ge=0, le=100, description="Minimum deal score"),
    state: Optional[str] = Query(None, description="Filter by state (2-letter code)"),
    zip_code: Optional[str] = Query(None, description="Filter by ZIP code"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the top deals ranked by deal score, each with a complete
    investment analysis.  This is the core "hot deals" feed.

    All returned deals have ``deal_score ≥ min_deal_score`` and are sorted
    highest score first.  Use ``state`` or ``zip_code`` to focus on a market.
    """
    from app.models.listing import ActiveListing

    query = (
        select(PropertyMatch)
        .options(
            selectinload(PropertyMatch.distressed_property),
            selectinload(PropertyMatch.active_listing),
        )
        .where(PropertyMatch.deal_score.is_not(None))
        .where(PropertyMatch.deal_score >= min_deal_score)
    )

    if state or zip_code:
        query = query.join(ActiveListing, PropertyMatch.active_listing_id == ActiveListing.id)
        if state:
            query = query.where(ActiveListing.state == state.upper())
        if zip_code:
            query = query.where(ActiveListing.zip_code == zip_code)

    query = query.order_by(PropertyMatch.deal_score.desc()).limit(limit)
    result = await db.execute(query)
    matches = result.scalars().all()

    analyses = []
    for match in matches:
        try:
            analysis = await analyze_deal(match, db)
            analyses.append(analysis)
        except Exception as exc:
            # Don't fail the whole list if one analysis errors
            analyses.append({
                "match_id": str(match.id),
                "error": str(exc),
                "deal_score": float(match.deal_score) if match.deal_score else None,
            })

    return analyses


@router.get("/{match_id}/analyze", response_model=Dict[str, Any])
async def analyze_match(
    match_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return a complete investment analysis for a specific deal match.

    The response includes:
    - **Flip Analysis**: ARV, repair cost, holding costs, closing costs,
      net profit, ROI%, annualised ROI%
    - **Rental Analysis**: estimated rent, NOI, cap rate, cash-on-cash
      return, GRM, break-even months
    - **Deal Grade** (A / B / C / D) and **Recommendation**
      (STRONG BUY / BUY / WATCH / SKIP)
    - **Inputs** used in every calculation (transparent, auditable)

    Financial assumptions are derived from live data where available:
    - Current 30-yr mortgage rate from FRED
    - Walk Score neighbourhood premium from Walk Score API
    - ARV from comparable active listings in the same ZIP code
    """
    result = await db.execute(
        select(PropertyMatch).where(PropertyMatch.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        return await analyze_deal(match, db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc
