"""
Flipper AI real-estate scraper package.

Architecture
------------
BaseScraper (base.py)
    Provides: async HTTP with connection pooling, per-source rate limiting,
    exponential-backoff retry via tenacity, circuit breaker, user-agent
    rotation, and optional proxy support.

ScrapedListing / ScrapedProperty (pipeline.py)
    Pydantic models that validate raw scraped dicts and normalise them to
    the shape expected by ingestion_service.ingest_active_listings /
    ingest_distressed_properties.

Source adapters (sources/)
    HUDScraper      — HUD Home Store CSV (public government REO data)
    CraigslistScraper — Craigslist real-estate RSS feeds (multiple cities)
    ATTOMScraper    — ATTOM Data Solutions REST API (requires API key)
    RESOScraper     — RESO / OData Web API adapter (MLS-compatible)

ScraperManager (manager.py)
    Runs all enabled scrapers concurrently, feeds normalised records into the
    ingestion service, and returns a per-source summary.
"""
