import re
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from rapidfuzz import fuzz
from app.models.property import DistressedProperty
from app.models.listing import ActiveListing
from app.models.match import PropertyMatch
from datetime import datetime

ADDRESS_ABBREVS = {
    r"\bst\b": "street",
    r"\bave\b": "avenue",
    r"\bblvd\b": "boulevard",
    r"\bdr\b": "drive",
    r"\brd\b": "road",
    r"\bln\b": "lane",
    r"\bct\b": "court",
    r"\bpl\b": "place",
    r"\bhwy\b": "highway",
    r"\bpkwy\b": "parkway",
    r"\bsq\b": "square",
    r"\bfte\b": "suite",
    r"\bapt\b": "apartment",
    r"\bn\b": "north",
    r"\bs\b": "south",
    r"\be\b": "east",
    r"\bw\b": "west",
}


def normalize_address(address: str) -> str:
    if not address:
        return ""
    addr = address.lower().strip()
    for abbrev, full in ADDRESS_ABBREVS.items():
        addr = re.sub(abbrev, full, addr)
    addr = re.sub(r"[^\w\s]", "", addr)
    addr = re.sub(r"\s+", " ", addr).strip()
    return addr


def calculate_fuzzy_score(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(a, b)


def match_properties(prop: DistressedProperty, listing: ActiveListing) -> tuple[float, str]:
    if prop.parcel_id and listing.parcel_id and prop.parcel_id == listing.parcel_id:
        return (100.0, "PARCEL")
    prop_norm = prop.normalized_address or normalize_address(prop.address)
    listing_norm = listing.normalized_address or normalize_address(listing.address)
    if prop_norm and listing_norm and prop_norm == listing_norm and prop.zip_code == listing.zip_code:
        return (95.0, "NORMALIZED")
    if prop.zip_code == listing.zip_code:
        score = calculate_fuzzy_score(prop_norm, listing_norm)
        if score >= 85:
            return (float(score), "FUZZY")
    return (0.0, "NONE")


async def run_matching(db: AsyncSession) -> int:
    props_result = await db.execute(select(DistressedProperty).where(DistressedProperty.is_active.is_(True)))
    props = props_result.scalars().all()
    listings_result = await db.execute(select(ActiveListing).where(ActiveListing.is_active.is_(True)))
    listings = listings_result.scalars().all()

    existing_result = await db.execute(
        select(PropertyMatch.distressed_property_id, PropertyMatch.active_listing_id)
    )
    existing_pairs = set((str(r[0]), str(r[1])) for r in existing_result.all())

    new_matches = 0
    for prop in props:
        for listing in listings:
            pair = (str(prop.id), str(listing.id))
            if pair in existing_pairs:
                continue
            score, method = match_properties(prop, listing)
            if score > 0:
                match = PropertyMatch(
                    distressed_property_id=prop.id,
                    active_listing_id=listing.id,
                    match_score=score,
                    match_method=method,
                    matched_at=datetime.utcnow(),
                )
                db.add(match)
                new_matches += 1
    await db.commit()
    return new_matches
