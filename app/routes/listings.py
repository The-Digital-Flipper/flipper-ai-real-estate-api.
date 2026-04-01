from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.listing import ActiveListing
from app.models.user import User
from app.schemas.listing import ActiveListingRead
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/listings", tags=["listings"])


@router.get("", response_model=dict)
async def get_listings(
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    zip_code: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    property_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(ActiveListing).where(ActiveListing.is_active.is_(True))
    if city:
        query = query.where(ActiveListing.city.ilike(f"%{city}%"))
    if state:
        query = query.where(ActiveListing.state == state.upper())
    if zip_code:
        query = query.where(ActiveListing.zip_code == zip_code)
    if min_price is not None:
        query = query.where(ActiveListing.list_price >= min_price)
    if max_price is not None:
        query = query.where(ActiveListing.list_price <= max_price)
    if property_type:
        query = query.where(ActiveListing.property_type == property_type.upper())
    if status:
        query = query.where(ActiveListing.status == status.upper())
    count_result = await db.execute(query)
    total = len(count_result.scalars().all())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    listings = result.scalars().all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [ActiveListingRead.model_validate(l) for l in listings],
    }


@router.get("/{listing_id}", response_model=ActiveListingRead)
async def get_listing(
    listing_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(ActiveListing).where(ActiveListing.id == listing_id))
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    return listing
