from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.user import User
from app.models.property import DistressedProperty
from app.models.listing import ActiveListing
from app.models.match import PropertyMatch
from app.models.alert import Alert
from app.services.auth_service import get_current_admin_user
from app.services.ingestion_service import ingest_distressed_properties, ingest_active_listings
from app.services.matching_engine import run_matching
from typing import List

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    db_ok = False
    try:
        await db.execute(select(func.now()))
        db_ok = True
    except Exception:
        pass
    return {"status": "ok", "database": db_ok, "redis": True}


@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    props_count = (await db.execute(select(func.count()).select_from(DistressedProperty))).scalar()
    listings_count = (await db.execute(select(func.count()).select_from(ActiveListing))).scalar()
    matches_count = (await db.execute(select(func.count()).select_from(PropertyMatch))).scalar()
    alerts_count = (await db.execute(select(func.count()).select_from(Alert))).scalar()
    users_count = (await db.execute(select(func.count()).select_from(User))).scalar()
    return {
        "properties": props_count,
        "listings": listings_count,
        "matches": matches_count,
        "alerts": alerts_count,
        "users": users_count,
    }


@router.post("/ingest/properties")
async def ingest_properties(
    data: List[dict],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    count = await ingest_distressed_properties(data, db)
    return {"inserted": count, "message": f"Ingested {count} new properties"}


@router.post("/ingest/listings")
async def ingest_listings(
    data: List[dict],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    count = await ingest_active_listings(data, db)
    return {"inserted": count, "message": f"Ingested {count} new listings"}


@router.post("/match/run")
async def trigger_matching(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
):
    count = await run_matching(db)
    return {"new_matches": count, "message": f"Created {count} new matches"}
