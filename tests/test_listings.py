import pytest
from datetime import datetime, timedelta
import uuid


@pytest.mark.asyncio
async def test_get_listings_authenticated(client, user_token, sample_listing):
    response = await client.get("/listings", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_listings_unauthenticated(client):
    response = await client.get("/listings")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_listing_by_id(client, user_token, sample_listing):
    response = await client.get(f"/listings/{sample_listing.id}", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(sample_listing.id)


@pytest.mark.asyncio
async def test_get_listing_not_found(client, user_token):
    response = await client.get(f"/listings/{uuid.uuid4()}", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_filter_listings_by_city(client, user_token, sample_listing):
    response = await client.get("/listings?city=Springfield", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    data = response.json()
    assert any(item["city"] == "Springfield" for item in data["items"])


@pytest.mark.asyncio
async def test_filter_listings_by_zip(client, user_token, sample_listing):
    response = await client.get("/listings?zip_code=62701", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    data = response.json()
    assert all(item["zip_code"] == "62701" for item in data["items"])


@pytest.mark.asyncio
async def test_listings_pagination(client, user_token, sample_listing):
    response = await client.get("/listings?page=1&per_page=5", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["per_page"] == 5
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_filter_listings_listed_within_days_returns_new(client, user_token, db_session):
    """A listing with listed_at = now should appear when listed_within_days=1."""
    from app.models.listing import ActiveListing
    new_listing = ActiveListing(
        id=uuid.uuid4(),
        address="50 New St",
        normalized_address="50 new street",
        city="Chicago",
        state="IL",
        zip_code="60601",
        list_price=300000,
        property_type="SFR",
        days_on_market=0,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=f"NEW-{uuid.uuid4()}",
    )
    db_session.add(new_listing)
    await db_session.commit()

    response = await client.get(
        "/listings?listed_within_days=1",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert str(new_listing.id) in ids


@pytest.mark.asyncio
async def test_filter_listings_listed_within_days_excludes_old(client, user_token, db_session):
    """A listing with listed_at 60 days ago should not appear when listed_within_days=7."""
    from app.models.listing import ActiveListing
    old_listing = ActiveListing(
        id=uuid.uuid4(),
        address="1 Old Road",
        normalized_address="1 old road",
        city="Peoria",
        state="IL",
        zip_code="61602",
        list_price=120000,
        property_type="SFR",
        days_on_market=60,
        status="ACTIVE",
        listed_at=datetime.utcnow() - timedelta(days=60),
        source="TEST",
        source_id=f"OLD-{uuid.uuid4()}",
    )
    db_session.add(old_listing)
    await db_session.commit()

    response = await client.get(
        "/listings?listed_within_days=7",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert str(old_listing.id) not in ids


@pytest.mark.asyncio
async def test_filter_listings_max_days_on_market(client, user_token, db_session):
    """max_days_on_market=5 should include listings with 0 DOM but exclude those with 90 DOM."""
    from app.models.listing import ActiveListing
    fresh = ActiveListing(
        id=uuid.uuid4(),
        address="10 Fresh Ave",
        normalized_address="10 fresh avenue",
        city="Rockford",
        state="IL",
        zip_code="61101",
        list_price=180000,
        property_type="SFR",
        days_on_market=2,
        status="ACTIVE",
        listed_at=datetime.utcnow() - timedelta(days=2),
        source="TEST",
        source_id=f"FRESH-{uuid.uuid4()}",
    )
    stale = ActiveListing(
        id=uuid.uuid4(),
        address="99 Stale Blvd",
        normalized_address="99 stale boulevard",
        city="Rockford",
        state="IL",
        zip_code="61101",
        list_price=175000,
        property_type="SFR",
        days_on_market=90,
        status="ACTIVE",
        listed_at=datetime.utcnow() - timedelta(days=90),
        source="TEST",
        source_id=f"STALE-{uuid.uuid4()}",
    )
    db_session.add(fresh)
    db_session.add(stale)
    await db_session.commit()

    response = await client.get(
        "/listings?max_days_on_market=5",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert str(fresh.id) in ids
    assert str(stale.id) not in ids


@pytest.mark.asyncio
async def test_filter_listings_invalid_listed_within_days(client, user_token):
    """listed_within_days must be >= 1."""
    response = await client.get(
        "/listings?listed_within_days=0",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 422
