import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import settings
from app.database import engine, Base
from app.middleware.rate_limiter import limiter
from app.middleware.error_handler import http_exception_handler, validation_exception_handler, general_exception_handler
from app.middleware.logging_middleware import LoggingMiddleware
from app.routes import auth, listings, properties, matches, alerts, saved_searches, admin, market

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered real estate deal-finding platform for distressed property identification",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

app.add_middleware(LoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(listings.router)
app.include_router(properties.router)
app.include_router(matches.router)
app.include_router(alerts.router)
app.include_router(saved_searches.router)
app.include_router(admin.router)
app.include_router(market.router)


@app.get("/", tags=["root"])
async def root():
    return {"message": f"Welcome to {settings.APP_NAME}", "version": "1.0.0", "docs": "/docs"}
