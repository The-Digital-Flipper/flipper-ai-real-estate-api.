from typing import List
from statistics import median
from app.models.property import DistressedProperty
from app.models.listing import ActiveListing
from app.models.match import PropertyMatch
from sqlalchemy.ext.asyncio import AsyncSession


def estimate_market_value(listing: ActiveListing, comparable_listings: List[ActiveListing]) -> float:
    if not listing.sqft or listing.sqft <= 0:
        return float(listing.list_price) if listing.list_price else 0.0
    comps = []
    for comp in comparable_listings:
        if comp.id == listing.id:
            continue
        if comp.zip_code != listing.zip_code:
            continue
        if comp.property_type != listing.property_type:
            continue
        if comp.sqft:
            sqft_ratio = comp.sqft / listing.sqft
            if sqft_ratio < 0.8 or sqft_ratio > 1.2:
                continue
        if listing.bedrooms is not None and comp.bedrooms is not None:
            if abs(comp.bedrooms - listing.bedrooms) > 1:
                continue
        if comp.list_price and comp.sqft and comp.sqft > 0:
            comps.append(float(comp.list_price) / comp.sqft)
    if comps:
        median_price_per_sqft = median(comps)
        return median_price_per_sqft * listing.sqft
    return float(listing.list_price) if listing.list_price else 0.0


def calculate_price_discount(distressed_price: float, market_value: float) -> float:
    if market_value <= 0:
        return 0.0
    return ((market_value - distressed_price) / market_value) * 100


def calculate_profit_potential(market_value: float, distressed_price: float, repair_factor: float = 0.1) -> float:
    return market_value * (1 - repair_factor) - distressed_price


def calculate_deal_score(
    match: PropertyMatch,
    prop: DistressedProperty,
    listing: ActiveListing,
    comparables: List[ActiveListing],
) -> float:
    base_score = float(match.match_score) * 0.3
    market_value = estimate_market_value(listing, comparables)
    distressed_price = float(prop.list_price) if prop.list_price else float(listing.list_price)
    price_discount = calculate_price_discount(distressed_price, market_value) if market_value > 0 else 0.0
    discount_factor = min(price_discount / 30 * 40, 40) if price_discount > 0 else 0.0
    foreclosure_bonus = 0.0
    stage = prop.foreclosure_stage.upper() if prop.foreclosure_stage else ""
    if stage in ("REO", "BANK_OWNED"):
        foreclosure_bonus = 10.0
    elif stage == "AUCTION":
        foreclosure_bonus = 8.0
    elif stage == "PRE_FORECLOSURE":
        foreclosure_bonus = 5.0
    dom_bonus = 5.0 if listing.days_on_market and listing.days_on_market > 60 else 0.0
    total = base_score + discount_factor + foreclosure_bonus + dom_bonus
    return min(100.0, total)


async def flag_strong_deals(matches: List[PropertyMatch], db: AsyncSession) -> int:
    count = 0
    for match in matches:
        if match.deal_score is not None and float(match.deal_score) >= 70:
            match.is_flagged = True
            count += 1
    await db.commit()
    return count
