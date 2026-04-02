import pytest
from app.services.deal_detector import (
    estimate_market_value,
    calculate_price_discount,
    calculate_profit_potential,
    calculate_deal_score,
)
from app.models.listing import ActiveListing
from app.models.property import DistressedProperty
from app.models.match import PropertyMatch
from decimal import Decimal
import uuid
from datetime import datetime


def make_listing(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        address="123 Main St",
        normalized_address="123 main street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        list_price=Decimal("200000"),
        property_type="SFR",
        bedrooms=3,
        bathrooms=Decimal("2"),
        sqft=1500,
        days_on_market=30,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    defaults.update(kwargs)
    return ActiveListing(**defaults)


def make_property(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        address="123 Elm St",
        normalized_address="123 elm street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        property_type="SFR",
        foreclosure_stage="REO",
        list_price=Decimal("150000"),
        bedrooms=3,
        sqft=1500,
        source="TEST",
        source_id=str(uuid.uuid4()),
    )
    defaults.update(kwargs)
    return DistressedProperty(**defaults)


def make_match(prop, listing, score=80.0, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        distressed_property_id=prop.id,
        active_listing_id=listing.id,
        match_score=Decimal(str(score)),
        match_method="NORMALIZED",
        matched_at=datetime.utcnow(),
    )
    defaults.update(kwargs)
    return PropertyMatch(**defaults)


def test_estimate_market_value_with_comps():
    target = make_listing(sqft=1500, zip_code="62701", property_type="SFR", bedrooms=3, list_price=Decimal("200000"))
    comps = [
        make_listing(sqft=1400, zip_code="62701", property_type="SFR", bedrooms=3, list_price=Decimal("190000")),
        make_listing(sqft=1600, zip_code="62701", property_type="SFR", bedrooms=3, list_price=Decimal("210000")),
    ]
    value = estimate_market_value(target, comps)
    assert value > 0


def test_estimate_market_value_no_comps():
    target = make_listing(sqft=1500, list_price=Decimal("200000"))
    value = estimate_market_value(target, [])
    assert value == 200000.0


def test_estimate_market_value_no_sqft():
    target = make_listing(sqft=None, list_price=Decimal("200000"))
    value = estimate_market_value(target, [])
    assert value == 200000.0


def test_calculate_price_discount():
    discount = calculate_price_discount(150000, 200000)
    assert abs(discount - 25.0) < 0.01


def test_calculate_price_discount_zero_market():
    discount = calculate_price_discount(150000, 0)
    assert discount == 0.0


def test_calculate_profit_potential():
    profit = calculate_profit_potential(200000, 150000, repair_factor=0.1)
    assert profit == 200000 * 0.9 - 150000


def test_calculate_deal_score_reo():
    prop = make_property(foreclosure_stage="REO", list_price=Decimal("150000"))
    listing = make_listing(list_price=Decimal("200000"), days_on_market=90)
    match = make_match(prop, listing, score=80.0)
    score = calculate_deal_score(match, prop, listing, [])
    assert 0 <= score <= 100


def test_calculate_deal_score_auction():
    prop = make_property(foreclosure_stage="AUCTION", list_price=Decimal("100000"))
    listing = make_listing(list_price=Decimal("200000"), days_on_market=30)
    match = make_match(prop, listing, score=95.0)
    score = calculate_deal_score(match, prop, listing, [])
    assert 0 <= score <= 100


def test_deal_score_max_100():
    prop = make_property(foreclosure_stage="REO", list_price=Decimal("50000"))
    listing = make_listing(list_price=Decimal("300000"), days_on_market=100)
    match = make_match(prop, listing, score=100.0)
    score = calculate_deal_score(match, prop, listing, [])
    assert score <= 100
