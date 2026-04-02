from pydantic_settings import BaseSettings
from typing import List, Optional

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://flipper:flipper_pass@localhost:5432/flipper_ai"
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = "change_this_to_a_very_long_random_secret_key_at_least_32_chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    PROPERTY_DATA_API_KEY: Optional[str] = None
    LISTING_API_KEY: Optional[str] = None
    RATE_LIMIT_PER_MINUTE: int = 60
    APP_NAME: str = "Flipper AI"
    DEBUG: bool = False

    # --- Scraper settings ---
    # ATTOM Data Solutions API key (https://api.developer.attomdata.com)
    ATTOM_API_KEY: Optional[str] = None
    # RESO Web API base URL (e.g. https://api.yourmls.com/reso/odata)
    RESO_API_URL: Optional[str] = None
    # RESO Web API bearer token or key
    RESO_API_KEY: Optional[str] = None
    # Comma-separated Craigslist subdomain cities to scrape
    # e.g. "chicago,losangeles,sfbay,newyork,dallas,houston"
    CRAIGSLIST_CITIES: List[str] = ["chicago", "losangeles", "sfbay", "newyork", "dallas", "houston"]
    # Optional HTTP/SOCKS proxy for all scrapers (e.g. "http://user:pass@host:port")
    SCRAPER_PROXY_URL: Optional[str] = None
    # Max outbound requests per second per scraper source
    SCRAPER_REQUESTS_PER_SECOND: float = 1.0

    # --- RentCast API (https://developers.rentcast.io) ---
    # Free tier: 50 API calls/month.  Sign up at https://www.rentcast.io/api
    RENTCAST_API_KEY: Optional[str] = None
    # Two-letter state codes to query for for-sale listings.
    # Keep this small on the free tier (each state = 1+ API calls).
    # e.g. ["CA", "TX", "FL"]
    RENTCAST_STATES: List[str] = ["CA", "TX", "FL"]
    # Hard cap on API calls per scraper run (protects free-tier quota).
    # Each paginated request counts as one call.
    RENTCAST_MAX_CALLS_PER_RUN: int = 5

    # --- US Census Bureau ACS API (https://api.census.gov) ---
    # Free, no approval needed.  Get a key at https://api.census.gov/data/key_signup.html
    # The API works without a key but is rate-limited to 500 requests/day without one.
    CENSUS_API_KEY: Optional[str] = None
    # ACS 5-year dataset year to query (update when Census releases a new year)
    CENSUS_ACS_YEAR: int = 2022

    # --- SMTP email notification settings ---
    # Configure these to enable email delivery of alerts to users.
    # Works with any SMTP provider (Gmail, SendGrid, Mailgun, AWS SES, etc.)
    # Leave SMTP_HOST empty to disable email delivery (alerts still stored in DB).
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: str = "noreply@flipperai.com"
    SMTP_FROM_NAME: str = "Flipper AI"
    # TLS=True uses STARTTLS (port 587).  Set False + port 465 for SSL.
    SMTP_USE_TLS: bool = True

    # --- FRED API (Federal Reserve Economic Data, https://fred.stlouisfed.org/docs/api/fred/) ---
    # Completely free — register for a key at https://fred.stlouisfed.org/docs/api/api_key.html
    # Provides 800,000+ US economic time-series including mortgage rates and home price indexes.
    FRED_API_KEY: Optional[str] = None

    # --- Walk Score API (https://www.walkscore.com/professional/api.php) ---
    # Free tier: 5,000 API calls/day.  Request a key at https://www.walkscore.com/professional/api.php
    # Returns Walk Score, Transit Score, and Bike Score for any US address.
    WALK_SCORE_API_KEY: Optional[str] = None
    # Properties per Celery-run to enrich with Walk Score (caps daily API usage).
    WALK_SCORE_MAX_PER_RUN: int = 100

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
