from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.database import get_db
from app.models.property import DistressedProperty
from app.models.user import User
from app.schemas.property import DistressedPropertyRead
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/properties", tags=["properties"])


@router.get("", response_model=dict)
async def get_properties(
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    zip_code: Optional[str] = Query(None),
    min_price: Optional[float] = Query(None),
    max_price: Optional[float] = Query(None),
    property_type: Optional[str] = Query(None),
    foreclosure_stage: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = select(DistressedProperty).where(DistressedProperty.is_active.is_(True))
    if city:
        query = query.where(DistressedProperty.city.ilike(f"%{city}%"))
    if state:
        query = query.where(DistressedProperty.state == state.upper())
    if zip_code:
        query = query.where(DistressedProperty.zip_code == zip_code)
    if min_price is not None:
        query = query.where(DistressedProperty.list_price >= min_price)
    if max_price is not None:
        query = query.where(DistressedProperty.list_price <= max_price)
    if property_type:
        query = query.where(DistressedProperty.property_type == property_type.upper())
    if foreclosure_stage:
        query = query.where(DistressedProperty.foreclosure_stage == foreclosure_stage.upper())
    if keyword:
        query = query.where(
            or_(
                DistressedProperty.address.ilike(f"%{keyword}%"),
                DistressedProperty.city.ilike(f"%{keyword}%"),
            )
        )
    count_result = await db.execute(query)
    total = len(count_result.scalars().all())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    props = result.scalars().all()
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [DistressedPropertyRead.model_validate(p) for p in props],
    }


@router.get("/{property_id}", response_model=DistressedPropertyRead)
async def get_property(
    property_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(DistressedProperty).where(DistressedProperty.id == property_id))
    prop = result.scalar_one_or_none()
    if not prop:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop
