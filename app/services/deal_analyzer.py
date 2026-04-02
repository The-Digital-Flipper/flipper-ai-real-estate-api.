"""
Deal Analyzer — Full Investment Return-on-Investment engine.

This module is the highest-value component of the Flipper AI platform.
Given a ``PropertyMatch`` (a distressed property cross-referenced against an
active listing), it produces a complete investment analysis covering:

    1. **Flip Scenario** — purchase → renovate → re-sell
       - ARV (After Repair Value) derived from comparable active listings
       - Repair cost estimate (based on age, sqft, property type)
       - 6-month holding costs (mortgage, tax, insurance) at current FRED rate
       - Buyer + seller closing costs and agent commissions
       - Net flip profit, flip ROI (%), annualized ROI (%)

    2. **Rental Scenario** — purchase → hold → rent
       - Estimated monthly market rent (ARV × rent multiplier, Walk Score
         premium, bedroom adjustment)
       - Monthly operating expenses (tax, insurance, vacancy, management,
         maintenance / CapEx reserve)
       - Monthly net operating income (NOI)
       - Cap rate (NOI / purchase price × 100)
       - Leveraged cash-on-cash return (20% down, FRED 30yr rate financing)
       - Gross Rent Multiplier (GRM)
       - Break-even months to recoup total cash invested

    3. **Deal Grade** (A / B / C / D) and **Recommendation**
       (STRONG BUY / BUY / WATCH / SKIP) derived from the financial metrics
       and the existing ``PropertyMatch.deal_score``.

All numeric inputs fall back gracefully when data is missing:
- FRED mortgage rate → 7.5% default
- Walk Score → neutral (no adjustment)
- Comparable listings → ARV = listing list_price
- Property sqft/age → conservative flat estimates
"""
from __future__ import annotations

import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ActiveListing
from app.models.market_indicator import MarketIndicator
from app.models.match import PropertyMatch
from app.models.property import DistressedProperty
from app.models.walk_score import WalkScore
from app.services.deal_detector import estimate_market_value

logger = logging.getLogger(__name__)

# ---- Constants ----------------------------------------------------------------
_DEFAULT_MORTGAGE_RATE = 7.5   # % — fallback when FRED data unavailable
_LTV = 0.80                    # loan-to-value for financing scenarios
_DOWN_PAYMENT_PCT = 0.20       # buyer down payment assumed
_LOAN_TERM_MONTHS = 360        # 30-year mortgage

# Repair cost per sqft (USD) by construction era
_REPAIR_COST_PER_SQFT: Dict[str, float] = {
    "pre1970":  35.0,
    "1970":     25.0,
    "1990":     18.0,
    "2010":     12.0,
}
_REPAIR_PROPERTY_TYPE_MULTIPLIER: Dict[str, float] = {
    "SFR":   1.0,
    "CONDO": 0.70,
    "MFR":   1.30,
    "MULTI": 1.30,
    "LAND":  0.10,
    "OTHER": 1.0,
}

# Holding period assumed for a flip (months)
_FLIP_HOLD_MONTHS = 6

# Closing cost assumptions
_BUY_CLOSING_PCT  = 0.02   # 2% of purchase price
_SELL_CLOSING_PCT = 0.08   # 8% of ARV (6% agent + 2% closing)

# Monthly rent as fraction of ARV (the "base rent multiplier")
_BASE_RENT_MULTIPLIER = 0.007   # ~0.7% rule (conservative)

# Operating expense ratios (annual, as fraction of ARV)
_PROPERTY_TAX_RATE     = 0.012   # 1.2% of ARV per year
_INSURANCE_RATE        = 0.005   # 0.5% of ARV per year
_MAINTENANCE_RATE      = 0.01    # 1.0% of ARV per year (CapEx reserve)
_VACANCY_RATE          = 0.05    # 5% of gross rent
_MANAGEMENT_FEE_RATE   = 0.10    # 10% of gross rent

# Grade thresholds
_GRADE_A = {"flip_roi": 30.0, "cap_rate": 8.0}
_GRADE_B = {"flip_roi": 20.0, "cap_rate": 6.0}
_GRADE_C = {"flip_roi": 10.0, "cap_rate": 4.0}


# ---- Helper math functions ----------------------------------------------------

def _monthly_payment(principal: float, annual_rate_pct: float, n_months: int) -> float:
    """Standard fixed-rate monthly mortgage payment (PMT formula)."""
    if annual_rate_pct <= 0:
        return principal / n_months
    r = (annual_rate_pct / 100) / 12
    return principal * r * (1 + r) ** n_months / ((1 + r) ** n_months - 1)


def _repair_cost(year_built: Optional[int], sqft: Optional[int], property_type: str) -> float:
    """Estimate total repair/renovation cost based on age, size, and type."""
    era: str
    if year_built is None or year_built < 1970:
        era = "pre1970"
    elif year_built < 1990:
        era = "1970"
    elif year_built < 2010:
        era = "1990"
    else:
        era = "2010"

    cost_per_sqft = _REPAIR_COST_PER_SQFT[era]
    type_mult = _REPAIR_PROPERTY_TYPE_MULTIPLIER.get(property_type.upper(), 1.0)

    if sqft and sqft > 0:
        return sqft * cost_per_sqft * type_mult
    # No sqft: flat estimate
    return 12000.0 * type_mult


def _rent_estimate(
    arv: float,
    bedrooms: Optional[int],
    walk_score: Optional[int],
) -> float:
    """Estimate monthly market rent from ARV + bedrooms + Walk Score."""
    rent = arv * _BASE_RENT_MULTIPLIER

    # Walk Score premium
    if walk_score is not None:
        if walk_score >= 90:
            rent *= 1.10
        elif walk_score >= 70:
            rent *= 1.05

    # Bedroom adjustment relative to 2-bedroom baseline
    bedroom_adj: Dict[Optional[int], float] = {
        None: 0.0,
        0: -0.30,
        1: -0.15,
        2: 0.00,
        3: 0.10,
        4: 0.20,
    }
    adj = bedroom_adj.get(bedrooms, 0.20 if bedrooms and bedrooms >= 4 else 0.0)
    rent *= 1 + adj

    return max(rent, 500.0)   # floor: $500/month


def _deal_grade(flip_roi: float, cap_rate: float) -> str:
    if flip_roi >= _GRADE_A["flip_roi"] and cap_rate >= _GRADE_A["cap_rate"]:
        return "A"
    if flip_roi >= _GRADE_B["flip_roi"] or cap_rate >= _GRADE_B["cap_rate"]:
        return "B"
    if flip_roi >= _GRADE_C["flip_roi"] or cap_rate >= _GRADE_C["cap_rate"]:
        return "C"
    return "D"


def _recommendation(grade: str, deal_score: float) -> str:
    if grade == "A" and deal_score >= 75:
        return "STRONG BUY"
    if grade in ("A", "B") and deal_score >= 60:
        return "BUY"
    if grade in ("B", "C") and deal_score >= 50:
        return "WATCH"
    return "SKIP"


# ---- Database helpers ---------------------------------------------------------

async def _get_mortgage_rate(db: AsyncSession) -> float:
    """Return latest 30-year fixed mortgage rate from FRED, or default."""
    result = await db.execute(
        select(MarketIndicator)
        .where(MarketIndicator.series_id == "MORTGAGE30US")
        .order_by(MarketIndicator.observation_date.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row and row.value is not None:
        return float(row.value)
    return _DEFAULT_MORTGAGE_RATE


async def _get_walk_score(db: AsyncSession, source_id: str, source: str) -> Optional[int]:
    """Return Walk Score for a property (either LISTING or DISTRESSED source)."""
    result = await db.execute(
        select(WalkScore).where(
            WalkScore.property_source == source,
            WalkScore.property_source_id == source_id,
        )
    )
    row = result.scalar_one_or_none()
    return row.walk_score if row else None


async def _get_comparables(
    db: AsyncSession,
    listing: ActiveListing,
    n: int = 20,
) -> List[ActiveListing]:
    """Return comparable active listings in the same ZIP / property type / size range."""
    result = await db.execute(
        select(ActiveListing)
        .where(
            ActiveListing.is_active.is_(True),
            ActiveListing.zip_code == listing.zip_code,
            ActiveListing.property_type == listing.property_type,
            ActiveListing.id != listing.id,
        )
        .limit(n)
    )
    return result.scalars().all()


# ---- Main public function -----------------------------------------------------

async def analyze_deal(
    match: PropertyMatch,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Generate a full investment analysis for a ``PropertyMatch``.

    Returns a dictionary suitable for serialisation as a JSON response.
    All monetary amounts are rounded to 2 decimal places.
    """
    # --- Load related objects --------------------------------------------------
    prop_result = await db.execute(
        select(DistressedProperty).where(DistressedProperty.id == match.distressed_property_id)
    )
    prop: DistressedProperty = prop_result.scalar_one()

    listing_result = await db.execute(
        select(ActiveListing).where(ActiveListing.id == match.active_listing_id)
    )
    listing: ActiveListing = listing_result.scalar_one()

    comparables = await _get_comparables(db, listing)
    mortgage_rate = await _get_mortgage_rate(db)
    walk_score = await _get_walk_score(db, str(listing.source_id), "LISTING")

    # --- Core inputs -----------------------------------------------------------
    purchase_price = float(prop.list_price or listing.list_price)
    arv = estimate_market_value(listing, comparables)
    if arv <= purchase_price:
        # ARV should be at least as high as list price if no strong comps
        arv = max(arv, float(listing.list_price))

    sqft = prop.sqft or listing.sqft
    year_built = prop.year_built or listing.year_built
    bedrooms = prop.bedrooms or listing.bedrooms
    property_type = (prop.property_type or listing.property_type or "SFR").upper()

    repair_cost = _repair_cost(year_built, sqft, property_type)

    # =========================================================================
    # FLIP ANALYSIS
    # =========================================================================
    buy_closing = purchase_price * _BUY_CLOSING_PCT
    sell_closing = arv * _SELL_CLOSING_PCT

    # Holding costs: 6 months of mortgage (80% LTV on purchase) + tax + insurance
    loan_amount = purchase_price * _LTV
    monthly_pi = _monthly_payment(loan_amount, mortgage_rate, _LOAN_TERM_MONTHS)
    monthly_tax = arv * _PROPERTY_TAX_RATE / 12
    monthly_insurance = max(100.0, arv * _INSURANCE_RATE / 12)
    monthly_holding = monthly_pi + monthly_tax + monthly_insurance
    total_holding = monthly_holding * _FLIP_HOLD_MONTHS

    total_invested_flip = purchase_price + repair_cost + buy_closing + total_holding
    net_flip_profit = arv - sell_closing - total_invested_flip
    flip_roi = (net_flip_profit / total_invested_flip * 100) if total_invested_flip > 0 else 0.0
    # Annualise based on assumed 6-month hold
    annualized_flip_roi = flip_roi * (12 / _FLIP_HOLD_MONTHS)

    # =========================================================================
    # RENTAL ANALYSIS
    # =========================================================================
    monthly_rent = _rent_estimate(arv, bedrooms, walk_score)

    # Annual operating expenses
    annual_tax = arv * _PROPERTY_TAX_RATE
    annual_insurance = max(1200.0, arv * _INSURANCE_RATE)
    annual_maintenance = arv * _MAINTENANCE_RATE
    annual_vacancy = monthly_rent * 12 * _VACANCY_RATE
    annual_management = monthly_rent * 12 * _MANAGEMENT_FEE_RATE
    annual_opex = annual_tax + annual_insurance + annual_maintenance + annual_vacancy + annual_management

    annual_gross_rent = monthly_rent * 12
    annual_noi = annual_gross_rent - annual_opex
    monthly_noi = annual_noi / 12

    cap_rate = (annual_noi / purchase_price * 100) if purchase_price > 0 else 0.0
    grm = (purchase_price / annual_gross_rent) if annual_gross_rent > 0 else None

    # Leveraged cash-on-cash (20% down, borrow 80% at FRED rate)
    down_payment = purchase_price * _DOWN_PAYMENT_PCT
    rental_loan = purchase_price * _LTV
    monthly_rental_pi = _monthly_payment(rental_loan, mortgage_rate, _LOAN_TERM_MONTHS)
    annual_debt_service = monthly_rental_pi * 12
    annual_levered_cf = annual_noi - annual_debt_service
    monthly_levered_cf = annual_levered_cf / 12
    total_cash_in_rental = down_payment + repair_cost + buy_closing
    cash_on_cash = (
        (annual_levered_cf / total_cash_in_rental * 100)
        if total_cash_in_rental > 0
        else 0.0
    )

    # Break-even months (levered)
    break_even_months: Optional[float] = None
    if monthly_levered_cf > 0:
        break_even_months = round(total_cash_in_rental / monthly_levered_cf, 1)

    # =========================================================================
    # DEAL GRADE + RECOMMENDATION
    # =========================================================================
    grade = _deal_grade(flip_roi, cap_rate)
    deal_score_val = float(match.deal_score) if match.deal_score is not None else 0.0
    recommendation = _recommendation(grade, deal_score_val)

    return {
        "match_id": str(match.id),
        "property_address": prop.address,
        "listing_address": listing.address,
        "city": listing.city,
        "state": listing.state,
        "zip_code": listing.zip_code,
        "deal_score": round(deal_score_val, 2),
        "deal_grade": grade,
        "recommendation": recommendation,
        "inputs": {
            "purchase_price": round(purchase_price, 2),
            "arv": round(arv, 2),
            "repair_cost": round(repair_cost, 2),
            "sqft": sqft,
            "year_built": year_built,
            "bedrooms": bedrooms,
            "property_type": property_type,
            "mortgage_rate_pct": round(mortgage_rate, 3),
            "walk_score": walk_score,
            "foreclosure_stage": prop.foreclosure_stage,
            "days_on_market": listing.days_on_market,
            "comparable_count": len(comparables),
        },
        "flip_analysis": {
            "arv": round(arv, 2),
            "purchase_price": round(purchase_price, 2),
            "repair_cost": round(repair_cost, 2),
            "buy_closing_costs": round(buy_closing, 2),
            "holding_costs_6mo": round(total_holding, 2),
            "sell_closing_costs_and_commission": round(sell_closing, 2),
            "total_invested": round(total_invested_flip, 2),
            "net_profit": round(net_flip_profit, 2),
            "roi_pct": round(flip_roi, 2),
            "annualized_roi_pct": round(annualized_flip_roi, 2),
        },
        "rental_analysis": {
            "estimated_monthly_rent": round(monthly_rent, 2),
            "annual_gross_rent": round(annual_gross_rent, 2),
            "annual_operating_expenses": round(annual_opex, 2),
            "annual_noi": round(annual_noi, 2),
            "monthly_noi": round(monthly_noi, 2),
            "cap_rate_pct": round(cap_rate, 2),
            "gross_rent_multiplier": round(grm, 2) if grm else None,
            "financing": {
                "down_payment": round(down_payment, 2),
                "loan_amount": round(rental_loan, 2),
                "monthly_pi_payment": round(monthly_rental_pi, 2),
                "total_cash_invested": round(total_cash_in_rental, 2),
            },
            "monthly_cash_flow_levered": round(monthly_levered_cf, 2),
            "annual_cash_flow_levered": round(annual_levered_cf, 2),
            "cash_on_cash_return_pct": round(cash_on_cash, 2),
            "break_even_months": break_even_months,
        },
    }


async def analyze_match_and_store(match: PropertyMatch, db: AsyncSession) -> Dict[str, Any]:
    """Run analysis and cache key metrics back onto the PropertyMatch row."""
    analysis = await analyze_deal(match, db)

    # Update stored metrics on the PropertyMatch row so deal_score queries benefit
    flip = analysis["flip_analysis"]
    rental = analysis["rental_analysis"]

    if match.estimated_value is None:
        match.estimated_value = flip["arv"]
    if match.profit_potential is None:
        match.profit_potential = flip["net_profit"]
    if match.price_discount_pct is None:
        purchase = flip["purchase_price"]
        arv = flip["arv"]
        if arv > 0:
            match.price_discount_pct = (arv - purchase) / arv * 100

    await db.commit()
    return analysis
