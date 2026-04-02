import pytest
from app.services.alert_service import create_match_alert, create_price_drop_alert, get_user_alerts, mark_alert_read
from app.models.match import PropertyMatch
from app.models.listing import ActiveListing
from app.models.alert import Alert
from decimal import Decimal
import uuid
from datetime import datetime
from fastapi import HTTPException


def make_match(prop_id=None, listing_id=None):
    return PropertyMatch(
        id=uuid.uuid4(),
        distressed_property_id=prop_id or uuid.uuid4(),
        active_listing_id=listing_id or uuid.uuid4(),
        match_score=Decimal("85.0"),
        match_method="NORMALIZED",
        deal_score=Decimal("72.0"),
        matched_at=datetime.utcnow(),
    )


def make_listing():
    return ActiveListing(
        id=uuid.uuid4(),
        address="456 Elm St",
        normalized_address="456 elm street",
        city="Chicago",
        state="IL",
        zip_code="60601",
        list_price=Decimal("180000"),
        original_price=Decimal("220000"),
        property_type="SFR",
        days_on_market=45,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=str(uuid.uuid4()),
    )


@pytest.mark.asyncio
async def test_create_match_alert(db_session, sample_property, sample_listing):
    match = make_match(sample_property.id, sample_listing.id)
    db_session.add(match)
    await db_session.commit()
    alert = await create_match_alert(match, db_session)
    assert alert.alert_type == "NEW_MATCH"
    assert alert.match_id == match.id


@pytest.mark.asyncio
async def test_create_price_drop_alert(db_session, sample_listing):
    alert = await create_price_drop_alert(sample_listing, 220000.0, db_session)
    assert alert.alert_type == "PRICE_DROP"
    assert "price" in alert.message.lower() or "dropped" in alert.message.lower()


@pytest.mark.asyncio
async def test_get_user_alerts(db_session, test_user):
    alert = Alert(
        id=uuid.uuid4(),
        user_id=test_user.id,
        alert_type="NEW_MATCH",
        message="Test alert",
        created_at=datetime.utcnow(),
    )
    db_session.add(alert)
    await db_session.commit()
    alerts = await get_user_alerts(test_user.id, db_session)
    assert len(alerts) >= 1


@pytest.mark.asyncio
async def test_mark_alert_read(db_session, test_user):
    alert = Alert(
        id=uuid.uuid4(),
        user_id=test_user.id,
        alert_type="NEW_MATCH",
        message="Unread alert",
        is_read=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(alert)
    await db_session.commit()
    updated = await mark_alert_read(alert.id, test_user.id, db_session)
    assert updated.is_read is True


@pytest.mark.asyncio
async def test_mark_alert_read_not_found(db_session, test_user):
    with pytest.raises(HTTPException) as exc_info:
        await mark_alert_read(uuid.uuid4(), test_user.id, db_session)
    assert exc_info.value.status_code == 404
