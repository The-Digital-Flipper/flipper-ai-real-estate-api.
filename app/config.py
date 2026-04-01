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

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
