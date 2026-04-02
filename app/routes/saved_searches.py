from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.saved_search import SavedSearch
from app.models.user import User
from app.schemas.saved_search import SavedSearchCreate, SavedSearchRead
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])


@router.get("", response_model=list[SavedSearchRead])
async def get_saved_searches(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(SavedSearch).where(SavedSearch.user_id == current_user.id))
    return result.scalars().all()


@router.post("", response_model=SavedSearchRead, status_code=201)
async def create_saved_search(
    search_in: SavedSearchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    search = SavedSearch(user_id=current_user.id, name=search_in.name, filters=search_in.filters)
    db.add(search)
    await db.commit()
    await db.refresh(search)
    return search


@router.delete("/{search_id}", status_code=204)
async def delete_saved_search(
    search_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(SavedSearch).where(SavedSearch.id == search_id, SavedSearch.user_id == current_user.id)
    )
    search = result.scalar_one_or_none()
    if not search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    await db.delete(search)
    await db.commit()
