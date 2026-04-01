"""
ScraperManager — orchestrates all source adapters, validates output, and
feeds data into the ingestion service.

Usage (from a Celery task or one-off script)::

    from app.scrapers.manager import build_scrapers, ScraperManager
    from app.config import settings

    async with AsyncSessionLocal() as db:
        manager = ScraperManager(build_scrapers(settings))
        summary = await manager.run_all(db)
        # summary = {"HUD_distressed": 42, "CRAIGSLIST_listings": 187, ...}

``build_scrapers(settings)`` reads the application settings and constructs
only the scrapers whose required credentials are present.  This means the
same code runs in development (Craigslist + HUD only) and in production
(all sources enabled).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.scrapers.base import BaseScraper, ScraperError
from app.scrapers.pipeline import normalise_listings, normalise_properties
from app.services.ingestion_service import (
    ingest_active_listings,
    ingest_distressed_properties,
)

logger = logging.getLogger(__name__)


class ScraperManager:
    """
    Runs a collection of scrapers, validates their output, and persists
    results via the ingestion service.

    Parameters
    ----------
    scrapers:
        List of BaseScraper instances to run.
    concurrency:
        Maximum number of scrapers to run simultaneously.  Defaults to 3.
    """

    def __init__(
        self,
        scrapers: List[BaseScraper],
        concurrency: int = 3,
    ) -> None:
        self._scrapers = scrapers
        self._concurrency = concurrency

    async def _run_one(
        self, scraper: BaseScraper, db: AsyncSession
    ) -> Dict[str, int]:
        """Run a single scraper and return its ingestion counts."""
        summary: Dict[str, int] = {}
        source = scraper.source_name

        try:
            async with scraper:
                # --- Listings ---
                try:
                    raw_listings = await scraper.scrape_listings()
                    valid_listings = normalise_listings(raw_listings)
                    if valid_listings:
                        count = await ingest_active_listings(valid_listings, db)
                        summary[f"{source}_listings"] = count
                        logger.info(
                            "[%s] Ingested %d new listings (%d valid / %d raw)",
                            source,
                            count,
                            len(valid_listings),
                            len(raw_listings),
                        )
                    else:
                        summary[f"{source}_listings"] = 0
                except ScraperError as exc:
                    logger.error("[%s] scrape_listings() failed: %s", source, exc)
                    summary[f"{source}_listings"] = -1

                # --- Distressed properties ---
                try:
                    raw_properties = await scraper.scrape_distressed()
                    valid_properties = normalise_properties(raw_properties)
                    if valid_properties:
                        count = await ingest_distressed_properties(valid_properties, db)
                        summary[f"{source}_distressed"] = count
                        logger.info(
                            "[%s] Ingested %d new distressed properties (%d valid / %d raw)",
                            source,
                            count,
                            len(valid_properties),
                            len(raw_properties),
                        )
                    else:
                        summary[f"{source}_distressed"] = 0
                except ScraperError as exc:
                    logger.error("[%s] scrape_distressed() failed: %s", source, exc)
                    summary[f"{source}_distressed"] = -1

        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Scraper crashed: %s", source, exc, exc_info=True)
            summary[f"{source}_error"] = 1

        return summary

    async def run_all(self, db: AsyncSession) -> Dict[str, int]:
        """
        Run all scrapers concurrently (up to ``concurrency`` at a time) and
        return a merged summary dict.
        """
        semaphore = asyncio.Semaphore(self._concurrency)

        async def _guarded(scraper: BaseScraper) -> Dict[str, int]:
            async with semaphore:
                return await self._run_one(scraper, db)

        results = await asyncio.gather(
            *(_guarded(s) for s in self._scrapers),
            return_exceptions=False,
        )

        merged: Dict[str, int] = {}
        for r in results:
            merged.update(r)

        total_listings = sum(v for k, v in merged.items() if k.endswith("_listings") and v > 0)
        total_distressed = sum(v for k, v in merged.items() if k.endswith("_distressed") and v > 0)
        logger.info(
            "ScraperManager run complete — %d new listings, %d new distressed properties",
            total_listings,
            total_distressed,
        )
        return merged


# --------------------------------------------------------------------------- #
# Factory — build enabled scrapers from application settings                   #
# --------------------------------------------------------------------------- #
def build_scrapers(settings: Any) -> List[BaseScraper]:
    """
    Instantiate every scraper whose required credentials are present in
    *settings*.  Sources without credentials are silently omitted.

    Returns
    -------
    List[BaseScraper]
        Ready-to-use scraper instances (not yet entered as context managers).
    """
    from app.scrapers.sources.hud import HUDScraper
    from app.scrapers.sources.craigslist import CraigslistScraper
    from app.scrapers.sources.attom import ATTOMScraper
    from app.scrapers.sources.reso import RESOScraper

    proxy: Optional[str] = getattr(settings, "SCRAPER_PROXY_URL", None)
    rps: float = float(getattr(settings, "SCRAPER_REQUESTS_PER_SECOND", 1.0))

    scrapers: List[BaseScraper] = []

    # HUD — always enabled (public government data, no key required)
    scrapers.append(HUDScraper(proxy_url=proxy))
    logger.info("Scraper enabled: HUD Home Store")

    # Craigslist — always enabled (public RSS feeds, no key required)
    cities: List[str] = list(getattr(settings, "CRAIGSLIST_CITIES", ["chicago"]))
    scrapers.append(
        CraigslistScraper(
            cities=cities,
            fetch_details=True,
            proxy_url=proxy,
            requests_per_second=min(rps, 0.5),  # be conservative with CL
        )
    )
    logger.info("Scraper enabled: Craigslist (%d cities)", len(cities))

    # ATTOM — only when API key is configured
    attom_key: Optional[str] = getattr(settings, "ATTOM_API_KEY", None)
    if attom_key:
        # Use zip codes from CRAIGSLIST_CITIES as a rough geographic proxy if no
        # explicit zip list is configured.  Operators can override via a custom
        # config object.
        scrapers.append(
            ATTOMScraper(
                api_key=attom_key,
                proxy_url=proxy,
                requests_per_second=min(rps, 2.0),
            )
        )
        logger.info("Scraper enabled: ATTOM Data Solutions")

    # RESO — only when both URL and key are configured
    reso_url: Optional[str] = getattr(settings, "RESO_API_URL", None)
    reso_key: Optional[str] = getattr(settings, "RESO_API_KEY", None)
    if reso_url and reso_key:
        scrapers.append(
            RESOScraper(
                base_url=reso_url,
                api_key=reso_key,
                proxy_url=proxy,
                requests_per_second=min(rps, 2.0),
            )
        )
        logger.info("Scraper enabled: RESO Web API (%s)", reso_url)

    return scrapers
