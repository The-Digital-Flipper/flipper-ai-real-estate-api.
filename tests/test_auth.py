import pytest


@pytest.mark.asyncio
async def test_register_success(client):
    response = await client.post("/auth/register", json={"email": "newuser@example.com", "password": "securepass123"})
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "newuser@example.com"
    assert "id" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    await client.post("/auth/register", json={"email": "dup@example.com", "password": "securepass123"})
    response = await client.post("/auth/register", json={"email": "dup@example.com", "password": "securepass123"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_register_short_password(client):
    response = await client.post("/auth/register", json={"email": "shortpw@example.com", "password": "short"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_success(client):
    await client.post("/auth/register", json={"email": "logintest@example.com", "password": "securepass123"})
    response = await client.post("/auth/login", json={"email": "logintest@example.com", "password": "securepass123"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/auth/register", json={"email": "wrongpw@example.com", "password": "securepass123"})
    response = await client.post("/auth/login", json={"email": "wrongpw@example.com", "password": "wrongpassword"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client):
    response = await client.post("/auth/login", json={"email": "nonexistent@example.com", "password": "password123"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_jwt_validation(client, user_token):
    response = await client.get("/listings", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_invalid_jwt(client):
    response = await client.get("/listings", headers={"Authorization": "Bearer invalidtoken"})
    assert response.status_code == 401
