from uuid import UUID
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.alert import Alert
from app.models.match import PropertyMatch
from app.models.listing import ActiveListing
from datetime import datetime


async def create_match_alert(match: PropertyMatch, db: AsyncSession) -> Alert:
    alert = Alert(
        match_id=match.id,
        alert_type="NEW_MATCH",
        message=f"New property match found with deal score {match.deal_score}",
        details={"match_id": str(match.id), "deal_score": float(match.deal_score) if match.deal_score else None},
        created_at=datetime.utcnow(),
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def create_price_drop_alert(listing: ActiveListing, old_price: float, db: AsyncSession) -> Alert:
    new_price = float(listing.list_price)
    drop_pct = ((old_price - new_price) / old_price * 100) if old_price > 0 else 0
    alert = Alert(
        alert_type="PRICE_DROP",
        message=f"Price dropped from ${old_price:,.2f} to ${new_price:,.2f} ({drop_pct:.1f}% reduction)",
        details={"listing_id": str(listing.id), "old_price": old_price, "new_price": new_price, "drop_pct": round(drop_pct, 2)},
        created_at=datetime.utcnow(),
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def get_user_alerts(user_id: UUID, db: AsyncSession, skip: int = 0, limit: int = 50) -> List[Alert]:
    result = await db.execute(
        select(Alert)
        .where(Alert.user_id == user_id)
        .order_by(Alert.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


async def mark_alert_read(alert_id: UUID, user_id: UUID, db: AsyncSession) -> Alert:
    from fastapi import HTTPException
    result = await db.execute(select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_read = True
    await db.commit()
    await db.refresh(alert)
    return alert
