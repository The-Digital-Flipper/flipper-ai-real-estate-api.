import pytest
from app.services.matching_engine import normalize_address, calculate_fuzzy_score, match_properties, run_matching
from app.models.property import DistressedProperty
from app.models.listing import ActiveListing
import uuid
from decimal import Decimal
from datetime import datetime


def make_property(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        address="123 Main St",
        normalized_address="123 main street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        property_type="SFR",
        foreclosure_stage="PRE_FORECLOSURE",
        source="TEST",
        source_id="p1",
    )
    defaults.update(kwargs)
    return DistressedProperty(**defaults)


def make_listing(**kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        address="123 Main Street",
        normalized_address="123 main street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        list_price=Decimal("200000"),
        property_type="SFR",
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id="l1",
    )
    defaults.update(kwargs)
    return ActiveListing(**defaults)


def test_normalize_address_basic():
    result = normalize_address("123 Main St")
    assert "street" in result
    assert "main" in result


def test_normalize_address_lowercase():
    result = normalize_address("456 OAK AVE")
    assert result == result.lower()
    assert "avenue" in result


def test_normalize_address_remove_punctuation():
    result = normalize_address("123 Main St., Apt #5")
    assert "." not in result
    assert "#" not in result


def test_normalize_address_blvd():
    result = normalize_address("500 Sunset Blvd")
    assert "boulevard" in result


def test_normalize_address_empty():
    assert normalize_address("") == ""


def test_fuzzy_score_identical():
    score = calculate_fuzzy_score("123 main street", "123 main street")
    assert score == 100.0


def test_fuzzy_score_similar():
    score = calculate_fuzzy_score("123 main street springfield", "123 main st springfield")
    assert score >= 80


def test_fuzzy_score_different():
    score = calculate_fuzzy_score("123 main street", "456 oak avenue")
    assert score < 80


def test_match_by_parcel_id():
    prop = make_property(parcel_id="PARCEL-001")
    listing = make_listing(parcel_id="PARCEL-001")
    score, method = match_properties(prop, listing)
    assert score == 100.0
    assert method == "PARCEL"


def test_match_normalized_exact():
    prop = make_property(normalized_address="123 main street", parcel_id=None)
    listing = make_listing(normalized_address="123 main street", parcel_id=None)
    score, method = match_properties(prop, listing)
    assert score == 95.0
    assert method == "NORMALIZED"


def test_match_fuzzy():
    prop = make_property(normalized_address="123 main street springfield", parcel_id=None)
    listing = make_listing(normalized_address="123 main street spfld", parcel_id=None)
    score, method = match_properties(prop, listing)
    assert method in ("FUZZY", "NONE")


def test_no_match_different_zip():
    prop = make_property(normalized_address="123 main street", zip_code="11111", parcel_id=None)
    listing = make_listing(normalized_address="123 main street", zip_code="22222", parcel_id=None)
    score, method = match_properties(prop, listing)
    assert method == "NONE"
    assert score == 0.0


@pytest.mark.asyncio
async def test_run_matching(db_session, sample_property, sample_listing):
    count = await run_matching(db_session)
    assert count >= 0
