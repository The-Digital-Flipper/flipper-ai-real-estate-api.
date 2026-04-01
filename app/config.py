from pydantic_settings import BaseSettings
from typing import Optional

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

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
