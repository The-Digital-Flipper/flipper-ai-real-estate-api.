from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models.match import PropertyMatch
from app.models.user import User
from app.schemas.match import PropertyMatchRead
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("", response_model=dict)
async def get_matches(
    is_flagged: Optional[bool] = Query(None),
    min_deal_score: Optional[float] = Query(None),
    zip_code: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        select(PropertyMatch)
        .options(
            selectinload(PropertyMatch.distressed_property),
            selectinload(PropertyMatch.active_listing),
        )
    )
    if is_flagged is not None:
        query = query.where(PropertyMatch.is_flagged == is_flagged)
    if min_deal_score is not None:
        query = query.where(PropertyMatch.deal_score >= min_deal_score)
    if zip_code:
        from app.models.listing import ActiveListing
        query = query.join(ActiveListing, PropertyMatch.active_listing_id == ActiveListing.id).where(
            ActiveListing.zip_code == zip_code
        )
    count_result = await db.execute(query)
    total = len(count_result.scalars().all())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    matches = result.scalars().all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [PropertyMatchRead.model_validate(m) for m in matches],
    }


@router.get("/{match_id}", response_model=PropertyMatchRead)
async def get_match(
    match_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PropertyMatch)
        .options(
            selectinload(PropertyMatch.distressed_property),
            selectinload(PropertyMatch.active_listing),
        )
        .where(PropertyMatch.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    return match
