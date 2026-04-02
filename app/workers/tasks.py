import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.workers.tasks.run_ingestion_task")
def run_ingestion_task():
    from app.database import AsyncSessionLocal
    from app.services.ingestion_service import detect_price_drops

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await detect_price_drops(db)
            return count

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_matching_task")
def run_matching_task():
    from app.database import AsyncSessionLocal
    from app.services.matching_engine import run_matching

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await run_matching(db)
            return count

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_scraper_task")
def run_scraper_task():
    """
    Run all enabled source scrapers, ingest results, and return a summary dict.

    Scrapers that are active depend on which credentials are present in settings:
        - HUD Home Store         always enabled (public government data)
        - Craigslist RSS         always enabled (public feeds)
        - RentCast               enabled when RENTCAST_API_KEY is set (free tier)
        - ATTOM Data Solutions   enabled when ATTOM_API_KEY is set
        - RESO Web API           enabled when RESO_API_URL + RESO_API_KEY are set
    """
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.scrapers.manager import ScraperManager, build_scrapers

    async def _run():
        async with AsyncSessionLocal() as db:
            manager = ScraperManager(build_scrapers(settings))
            summary = await manager.run_all(db)
            logger.info("Scraper task summary: %s", summary)
            return summary

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_fred_task")
def run_fred_task():
    """
    Fetch the latest observations for tracked FRED economic series and upsert
    into the market_indicators table.

    Series fetched (all free, Federal Reserve Bank of St. Louis):
        - MORTGAGE30US  — 30-year fixed mortgage average
        - MORTGAGE15US  — 15-year fixed mortgage average
        - CSUSHPINSA    — Case-Shiller US national home price index
        - HOUST         — Housing starts (000s of units, SAAR)
        - MSPUS         — Median sales price of houses sold

    Schedule: weekly (FRED updates happen weekly/monthly).
    Free key:  https://fred.stlouisfed.org/docs/api/api_key.html
    """
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services.fred_service import run_fred_update

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await run_fred_update(db, api_key=settings.FRED_API_KEY)
            logger.info("[FRED] Task complete — %d new market indicator rows", count)
            return count

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_walk_score_task")
def run_walk_score_task():
    """
    Enrich unscored active listings and distressed properties with Walk Score,
    Transit Score, and Bike Score from the Walk Score API.

    Uses OpenStreetMap Nominatim (free, no key) to geocode addresses first,
    then calls Walk Score (free tier: 5,000 calls/day) for neighbourhood scores.

    Free Walk Score key: https://www.walkscore.com/professional/api.php
    """
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services.walk_score_enricher import run_walk_score_enrichment

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await run_walk_score_enrichment(
                db,
                api_key=settings.WALK_SCORE_API_KEY or "",
                max_per_run=settings.WALK_SCORE_MAX_PER_RUN,
            )
            logger.info("[WALKSCORE] Task complete — %d new score rows", count)
            return count

    return asyncio.run(_run())


@celery_app.task(name="app.workers.tasks.run_census_enrichment_task")
def run_census_enrichment_task():
    """
    Enrich distressed properties that have no estimated_value using the
    US Census Bureau ACS 5-year median home value for their ZIP code.

    This uses a completely free, open government API (api.census.gov) and
    works with or without a CENSUS_API_KEY (key gives higher rate limits).
    """
    from app.config import settings
    from app.database import AsyncSessionLocal
    from app.services.census_enricher import run_census_enrichment

    async def _run():
        async with AsyncSessionLocal() as db:
            count = await run_census_enrichment(
                db,
                api_key=settings.CENSUS_API_KEY,
                acs_year=settings.CENSUS_ACS_YEAR,
            )
            logger.info("[CENSUS] Enrichment complete — updated %d properties", count)
            return count

    return asyncio.run(_run())
