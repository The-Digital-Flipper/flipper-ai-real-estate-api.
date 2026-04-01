import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.main import app
from app.database import Base, get_db
from app.models import User, DistressedProperty, ActiveListing, PropertyMatch, Alert, SavedSearch
from app.services.auth_service import hash_password, create_access_token
from datetime import datetime
import uuid

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"


@pytest.fixture(scope="session")
async def engine():
    _engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest.fixture
async def db_session(engine):
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(db_session):
    from sqlalchemy import select as _select
    result = await db_session.execute(_select(User).where(User.email == "test@example.com"))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        hashed_password=hash_password("testpass123"),
        is_active=True,
        is_admin=False,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def test_admin(db_session):
    from sqlalchemy import select as _select
    result = await db_session.execute(_select(User).where(User.email == "admin@example.com"))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    user = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password=hash_password("adminpass123"),
        is_active=True,
        is_admin=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def user_token(test_user):
    return create_access_token(data={"sub": str(test_user.id)})


@pytest.fixture
async def admin_token(test_admin):
    return create_access_token(data={"sub": str(test_admin.id)})


@pytest.fixture
async def sample_property(db_session):
    source_id = f"TEST-{uuid.uuid4()}"
    prop = DistressedProperty(
        id=uuid.uuid4(),
        address="123 Main St",
        normalized_address="123 main street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        property_type="SFR",
        foreclosure_stage="PRE_FORECLOSURE",
        list_price=150000,
        source="TEST",
        source_id=source_id,
    )
    db_session.add(prop)
    await db_session.commit()
    await db_session.refresh(prop)
    return prop


@pytest.fixture
async def sample_listing(db_session):
    source_id = f"TLIST-{uuid.uuid4()}"
    listing = ActiveListing(
        id=uuid.uuid4(),
        address="123 Main Street",
        normalized_address="123 main street",
        city="Springfield",
        state="IL",
        zip_code="62701",
        list_price=200000,
        original_price=220000,
        property_type="SFR",
        bedrooms=3,
        bathrooms=2,
        sqft=1500,
        days_on_market=90,
        status="ACTIVE",
        listed_at=datetime.utcnow(),
        source="TEST",
        source_id=source_id,
    )
    db_session.add(listing)
    await db_session.commit()
    await db_session.refresh(listing)
    return listing
