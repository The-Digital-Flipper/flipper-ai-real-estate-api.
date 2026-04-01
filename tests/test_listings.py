import pytest
from datetime import datetime


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
    import uuid
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
