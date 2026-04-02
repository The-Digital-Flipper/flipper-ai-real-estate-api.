"""
Tests for app/services/deal_analyzer.py — the investment analysis engine.

All tests are pure (no DB) except the async integration tests which use
the aiosqlite test fixture from conftest.py.
"""
import math
import uuid
from decimal import Decimal
from datetime import datetime
from typing import Optional

import pytest

from app.services.deal_analyzer import (
    _monthly_payment,
    _repair_cost,
    _rent_estimate,
    _deal_grade,
    _recommendation,
)


# ---- Unit tests for helper functions ----------------------------------------

class TestMonthlyPayment:
    def test_standard_30yr(self):
        """$300k loan at 7% 30-year should be ~$1,996/month."""
        pmt = _monthly_payment(300_000, 7.0, 360)
        assert 1990 < pmt < 2010

    def test_zero_rate(self):
        """Zero-rate loan: payment = principal / months."""
        pmt = _monthly_payment(120_000, 0.0, 360)
        assert abs(pmt - 120_000 / 360) < 0.01

    def test_small_loan(self):
        """Low principal should still return a positive payment."""
        pmt = _monthly_payment(50_000, 8.0, 360)
        assert pmt > 0


class TestRepairCost:
    def test_pre1970_sfr(self):
        """Old SFR: $35/sqft × 1.0 multiplier."""
        cost = _repair_cost(1960, 1000, "SFR")
        assert abs(cost - 35_000) < 1

    def test_modern_condo(self):
        """Newer condo: lower cost per sqft × 0.7 multiplier."""
        cost = _repair_cost(2015, 1000, "CONDO")
        # 2010 era: $12/sqft × 0.7 = $8.4/sqft → $8,400
        assert abs(cost - 8_400) < 1

    def test_no_sqft_fallback(self):
        """No sqft: falls back to flat estimate (positive)."""
        cost = _repair_cost(1980, None, "SFR")
        assert cost > 0

    def test_1970s_mfr(self):
        """1970s multi-family: $25/sqft × 1.3 multiplier."""
        cost = _repair_cost(1975, 2000, "MFR")
        assert abs(cost - 65_000) < 1

    def test_unknown_property_type(self):
        """Unknown type falls back to ×1.0 multiplier."""
        cost_unknown = _repair_cost(2000, 1000, "WAREHOUSE")
        cost_sfr = _repair_cost(2000, 1000, "SFR")
        assert abs(cost_unknown - cost_sfr) < 1


class TestRentEstimate:
    def test_basic_3br(self):
        """3br SFR: ARV $300k → rent > $0 and reasonable."""
        rent = _rent_estimate(300_000, 3, None)
        # 0.7% × 300k = $2,100 base, +10% for 3br = $2,310
        assert 2100 < rent < 2600

    def test_walk_score_premium_high(self):
        """Walk score ≥ 90 applies a 10% premium."""
        rent_low = _rent_estimate(300_000, 2, 40)
        rent_high = _rent_estimate(300_000, 2, 92)
        assert rent_high > rent_low * 1.08

    def test_studio_discount(self):
        """Studio (0 br) should be cheaper than 2br."""
        studio = _rent_estimate(200_000, 0, None)
        two_br = _rent_estimate(200_000, 2, None)
        assert studio < two_br

    def test_floor_applied(self):
        """Very cheap property: rent floor of $500/month must apply."""
        rent = _rent_estimate(10_000, 1, None)
        assert rent >= 500.0

    def test_none_bedrooms(self):
        """None bedrooms: no adjustment applied, still positive."""
        rent = _rent_estimate(250_000, None, None)
        assert rent > 0


class TestDealGrade:
    def test_grade_a(self):
        assert _deal_grade(35.0, 9.0) == "A"

    def test_grade_a_boundary(self):
        assert _deal_grade(30.0, 8.0) == "A"

    def test_grade_b_flip_only(self):
        """High flip ROI alone qualifies for B even with low cap rate."""
        assert _deal_grade(25.0, 3.0) == "B"

    def test_grade_b_cap_rate_only(self):
        """High cap rate alone qualifies for B even with low flip ROI."""
        assert _deal_grade(5.0, 7.0) == "B"

    def test_grade_c(self):
        assert _deal_grade(12.0, 3.0) == "C"

    def test_grade_d(self):
        assert _deal_grade(5.0, 2.0) == "D"


class TestRecommendation:
    def test_strong_buy(self):
        assert _recommendation("A", 80.0) == "STRONG BUY"

    def test_buy_grade_a_lower_score(self):
        assert _recommendation("A", 62.0) == "BUY"

    def test_buy_grade_b(self):
        assert _recommendation("B", 65.0) == "BUY"

    def test_watch_grade_b_low_score(self):
        assert _recommendation("B", 52.0) == "WATCH"

    def test_watch_grade_c(self):
        assert _recommendation("C", 55.0) == "WATCH"

    def test_skip_grade_d(self):
        assert _recommendation("D", 90.0) == "SKIP"

    def test_skip_grade_c_low_score(self):
        assert _recommendation("C", 40.0) == "SKIP"


# ---- Integration test — full analyze_deal pipeline --------------------------

@pytest.mark.asyncio
async def test_analyze_deal_end_to_end(db_session):
    """Runs analyze_deal against the in-memory SQLite test DB."""
    from app.services.deal_analyzer import analyze_deal
    from app.models.property import DistressedProperty
    from app.models.listing import ActiveListing
    from app.models.match import PropertyMatch

    prop = DistressedProperty(
        id=uuid.uuid4(),
        address="456 Oak Ave",
        normalized_address="456 oak avenue",
        city="Houston",
        state="TX",
        zip_code="77001",
        property_type="SFR",
        foreclosure_stage="REO",
        list_price=Decimal("130000"),
        bedrooms=3,
        bathrooms=Decimal("2"),
        sqft=1400,
        year_built=1985,
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    listing = ActiveListing(
        id=uuid.uuid4(),
        address="456 Oak Ave",
        normalized_address="456 oak avenue",
        city="Houston",
        state="TX",
        zip_code="77001",
        list_price=Decimal("185000"),
        property_type="SFR",
        bedrooms=3,
        bathrooms=Decimal("2"),
        sqft=1400,
        year_built=1985,
        days_on_market=45,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    match = PropertyMatch(
        id=uuid.uuid4(),
        distressed_property_id=prop.id,
        active_listing_id=listing.id,
        match_score=Decimal("85"),
        match_method="NORMALIZED",
        deal_score=Decimal("72"),
        matched_at=datetime.utcnow(),
    )
    db_session.add_all([prop, listing, match])
    await db_session.commit()

    result = await analyze_deal(match, db_session)

    # Structure assertions
    assert result["match_id"] == str(match.id)
    assert "flip_analysis" in result
    assert "rental_analysis" in result
    assert result["deal_grade"] in ("A", "B", "C", "D")
    assert result["recommendation"] in ("STRONG BUY", "BUY", "WATCH", "SKIP")

    flip = result["flip_analysis"]
    assert flip["purchase_price"] == 130_000.0
    assert flip["arv"] >= 130_000.0
    assert flip["repair_cost"] > 0
    assert isinstance(flip["net_profit"], float)
    assert isinstance(flip["roi_pct"], float)

    rental = result["rental_analysis"]
    assert rental["estimated_monthly_rent"] >= 500.0
    assert rental["cap_rate_pct"] is not None
    assert "break_even_months" in rental   # can be None if cash flow negative

    inputs = result["inputs"]
    assert inputs["mortgage_rate_pct"] > 0
    assert inputs["bedrooms"] == 3
    assert inputs["sqft"] == 1400


@pytest.mark.asyncio
async def test_analyze_deal_no_sqft_no_comps(db_session):
    """Analysis should still complete when sqft and comps are missing."""
    from app.services.deal_analyzer import analyze_deal
    from app.models.property import DistressedProperty
    from app.models.listing import ActiveListing
    from app.models.match import PropertyMatch

    prop = DistressedProperty(
        id=uuid.uuid4(),
        address="789 Pine St",
        normalized_address="789 pine street",
        city="Dallas",
        state="TX",
        zip_code="75201",
        property_type="SFR",
        foreclosure_stage="AUCTION",
        list_price=Decimal("80000"),
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    listing = ActiveListing(
        id=uuid.uuid4(),
        address="789 Pine St",
        normalized_address="789 pine street",
        city="Dallas",
        state="TX",
        zip_code="75201",
        list_price=Decimal("120000"),
        property_type="SFR",
        days_on_market=10,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    match = PropertyMatch(
        id=uuid.uuid4(),
        distressed_property_id=prop.id,
        active_listing_id=listing.id,
        match_score=Decimal("90"),
        match_method="PARCEL",
        deal_score=Decimal("65"),
        matched_at=datetime.utcnow(),
    )
    db_session.add_all([prop, listing, match])
    await db_session.commit()

    result = await analyze_deal(match, db_session)
    assert result["flip_analysis"]["purchase_price"] == 80_000.0
    assert result["rental_analysis"]["estimated_monthly_rent"] >= 500.0
    assert result["deal_grade"] in ("A", "B", "C", "D")
