from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.property import DistressedProperty
from app.models.listing import ActiveListing
from app.services.matching_engine import normalize_address
from app.services.alert_service import create_price_drop_alert


async def ingest_distressed_properties(data: List[Dict[str, Any]], db: AsyncSession) -> int:
    count = 0
    for item in data:
        source = item.get("source", "UNKNOWN")
        source_id = item.get("source_id", "")
        result = await db.execute(
            select(DistressedProperty).where(
                DistressedProperty.source == source,
                DistressedProperty.source_id == source_id,
            )
        )
        existing = result.scalar_one_or_none()
        address = item.get("address", "")
        if existing:
            existing.address = address
            existing.normalized_address = normalize_address(address)
            existing.city = item.get("city", existing.city)
            existing.state = item.get("state", existing.state)
            existing.zip_code = item.get("zip_code", existing.zip_code)
            existing.list_price = item.get("list_price", existing.list_price)
            existing.foreclosure_stage = item.get("foreclosure_stage", existing.foreclosure_stage)
            existing.updated_at = datetime.utcnow()
        else:
            prop = DistressedProperty(
                address=address,
                normalized_address=normalize_address(address),
                city=item.get("city", ""),
                state=item.get("state", ""),
                zip_code=item.get("zip_code", ""),
                parcel_id=item.get("parcel_id"),
                property_type=item.get("property_type", "SFR"),
                foreclosure_stage=item.get("foreclosure_stage", "PRE_FORECLOSURE"),
                list_price=item.get("list_price"),
                estimated_value=item.get("estimated_value"),
                bedrooms=item.get("bedrooms"),
                bathrooms=item.get("bathrooms"),
                sqft=item.get("sqft"),
                year_built=item.get("year_built"),
                source=source,
                source_id=source_id,
            )
            db.add(prop)
            count += 1
    await db.commit()
    return count


async def ingest_active_listings(data: List[Dict[str, Any]], db: AsyncSession) -> int:
    count = 0
    for item in data:
        source = item.get("source", "UNKNOWN")
        source_id = item.get("source_id", "")
        result = await db.execute(
            select(ActiveListing).where(
                ActiveListing.source == source,
                ActiveListing.source_id == source_id,
            )
        )
        existing = result.scalar_one_or_none()
        address = item.get("address", "")
        if existing:
            existing.address = address
            existing.normalized_address = normalize_address(address)
            existing.list_price = item.get("list_price", existing.list_price)
            existing.status = item.get("status", existing.status)
            existing.days_on_market = item.get("days_on_market", existing.days_on_market)
            existing.updated_at = datetime.utcnow()
        else:
            listed_at = item.get("listed_at")
            if isinstance(listed_at, str):
                try:
                    listed_at = datetime.fromisoformat(listed_at)
                except ValueError:
                    listed_at = datetime.utcnow()
            listing = ActiveListing(
                address=address,
                normalized_address=normalize_address(address),
                city=item.get("city", ""),
                state=item.get("state", ""),
                zip_code=item.get("zip_code", ""),
                parcel_id=item.get("parcel_id"),
                list_price=item.get("list_price", 0),
                original_price=item.get("original_price"),
                property_type=item.get("property_type", "SFR"),
                bedrooms=item.get("bedrooms"),
                bathrooms=item.get("bathrooms"),
                sqft=item.get("sqft"),
                year_built=item.get("year_built"),
                days_on_market=item.get("days_on_market", 0),
                status=item.get("status", "ACTIVE"),
                listed_at=listed_at or datetime.utcnow(),
                source=source,
                source_id=source_id,
            )
            db.add(listing)
            count += 1
    await db.commit()
    return count


async def detect_price_drops(db: AsyncSession) -> int:
    result = await db.execute(
        select(ActiveListing).where(
            ActiveListing.original_price.is_not(None),
            ActiveListing.is_active.is_(True),
        )
    )
    listings = result.scalars().all()
    count = 0
    for listing in listings:
        if listing.original_price and listing.list_price:
            if float(listing.list_price) < float(listing.original_price):
                await create_price_drop_alert(listing, float(listing.original_price), db)
                count += 1
    return count
