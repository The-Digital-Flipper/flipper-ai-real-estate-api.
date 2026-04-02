"""
Walk Score enrichment service.

Data source
-----------
Walk Score (https://www.walkscore.com/professional/api.php) provides three
neighbourhood quality scores for any US address:

    Walk Score    (0–100)  — pedestrian-friendliness
    Transit Score (0–100)  — quality of public-transit access
    Bike Score    (0–100)  — bikeability

Free tier
~~~~~~~~~
- 5,000 API calls per day (resets daily).
- Requires a free API key from https://www.walkscore.com/professional/api.php.

Dependency
~~~~~~~~~~
Walk Score requires ``(lat, lon)`` in addition to an address string.
This service uses the :mod:`app.services.geocoder` (OpenStreetMap Nominatim)
to resolve coordinates when they are not already known.

Configuration (.env)
~~~~~~~~~~~~~~~~~~~~~
    WALK_SCORE_API_KEY=<your-key>
    WALK_SCORE_MAX_PER_RUN=100   # properties to score per Celery run

Workflow
--------
1. Query ``active_listings`` and ``distressed_properties`` that are not yet
   in the ``walk_scores`` table.
2. For each property, geocode its address via Nominatim → get (lat, lon).
3. Call the Walk Score API with address + lat + lon.
4. Upsert into ``walk_scores``.

Output
------
``run_walk_score_enrichment(db, api_key, max_per_run)`` → int (rows upserted)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import ActiveListing
from app.models.property import DistressedProperty
from app.models.walk_score import WalkScore
from app.services.geocoder import NominatimGeocoder

logger = logging.getLogger(__name__)

_WALK_SCORE_BASE = "https://api.walkscore.com/score"
_TIMEOUT = 15.0
# Walk Score API status codes
_WS_STATUS_OK = 1
_WS_STATUS_SCORE_UNAVAILABLE = 2   # address found but no score
_WS_STATUS_ADDRESS_NOT_FOUND = 30
_WS_STATUS_DAILY_LIMIT = 40


class WalkScoreEnricher:
    """
    Enriches property records with Walk Score / Transit Score / Bike Score.

    Parameters
    ----------
    api_key:
        Walk Score API key (free from walkscore.com/professional/api.php).
    max_per_run:
        Hard cap on properties to process in one run (protects daily quota).
    """

    def __init__(
        self,
        api_key: str,
        max_per_run: int = 100,
    ) -> None:
        self._api_key = api_key
        self._max_per_run = max(1, max_per_run)
        self._client: Optional[httpx.AsyncClient] = None
        self._geocoder: Optional[NominatimGeocoder] = None
        self._daily_limit_hit = False

    # ---------------------------------------------------------------------- #
    # Walk Score API call                                                      #
    # ---------------------------------------------------------------------- #
    async def _fetch_score(
        self, address: str, lat: float, lon: float
    ) -> Optional[Dict[str, Any]]:
        """Call Walk Score API. Returns raw JSON dict or None on error."""
        assert self._client is not None

        if self._daily_limit_hit:
            return None

        params: Dict[str, Any] = {
            "format": "json",
            "address": address,
            "lat": lat,
            "lon": lon,
            "transit": 1,
            "bike": 1,
            "wsapikey": self._api_key,
        }
        try:
            resp = await self._client.get(
                _WALK_SCORE_BASE, params=params, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning("[WALKSCORE] HTTP %d for '%s': %s", exc.response.status_code, address, exc)
            return None
        except Exception as exc:
            logger.warning("[WALKSCORE] Request error for '%s': %s", address, exc)
            return None

        status = data.get("status")
        if status == _WS_STATUS_DAILY_LIMIT:
            logger.warning("[WALKSCORE] Daily API limit reached — stopping enrichment")
            self._daily_limit_hit = True
            return None
        if status not in (_WS_STATUS_OK, _WS_STATUS_SCORE_UNAVAILABLE):
            logger.debug("[WALKSCORE] Status %s for '%s'", status, address)
            return None

        return data

    @staticmethod
    def _parse_response(
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract normalised score fields from the Walk Score API response."""
        def _int_or_none(val: Any) -> Optional[int]:
            try:
                return int(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        transit_block = data.get("transit") or {}
        bike_block = data.get("bike") or {}

        return {
            "walk_score": _int_or_none(data.get("walkscore")),
            "walk_description": data.get("description"),
            "transit_score": _int_or_none(transit_block.get("score")),
            "transit_description": transit_block.get("description"),
            "bike_score": _int_or_none(bike_block.get("score")),
            "bike_description": bike_block.get("description"),
        }

    # ---------------------------------------------------------------------- #
    # Helpers to load unscored properties                                      #
    # ---------------------------------------------------------------------- #
    async def _load_unscored_listings(
        self, db: AsyncSession, limit: int
    ) -> List[Tuple[str, str, str]]:
        """Return (source_id, address, state) tuples for unscored ActiveListings."""
        scored_ids_result = await db.execute(
            select(WalkScore.property_source_id).where(
                WalkScore.property_source == "LISTING"
            )
        )
        already_scored = {r[0] for r in scored_ids_result.fetchall()}

        result = await db.execute(
            select(ActiveListing.source_id, ActiveListing.address, ActiveListing.state)
            .where(ActiveListing.is_active.is_(True))
            .limit(limit * 3)  # over-fetch to compensate for geocoding failures
        )
        rows = result.fetchall()
        return [
            (r[0], r[1], r[2])
            for r in rows
            if r[0] not in already_scored
        ][:limit]

    async def _load_unscored_distressed(
        self, db: AsyncSession, limit: int
    ) -> List[Tuple[str, str, str]]:
        """Return (source_id, address, state) tuples for unscored DistressedProperty."""
        scored_ids_result = await db.execute(
            select(WalkScore.property_source_id).where(
                WalkScore.property_source == "DISTRESSED"
            )
        )
        already_scored = {r[0] for r in scored_ids_result.fetchall()}

        result = await db.execute(
            select(
                DistressedProperty.source_id,
                DistressedProperty.address,
                DistressedProperty.state,
            )
            .where(DistressedProperty.is_active.is_(True))
            .limit(limit * 3)
        )
        rows = result.fetchall()
        return [
            (r[0], r[1], r[2])
            for r in rows
            if r[0] not in already_scored
        ][:limit]

    # ---------------------------------------------------------------------- #
    # Main enrichment loop                                                     #
    # ---------------------------------------------------------------------- #
    async def _enrich_batch(
        self,
        db: AsyncSession,
        property_type: str,
        records: List[Tuple[str, str, str]],
    ) -> int:
        """Score a batch of (source_id, address, state) records."""
        upserted = 0
        now = datetime.utcnow()

        for source_id, address, state in records:
            if self._daily_limit_hit:
                break

            # 1. Geocode with Nominatim
            assert self._geocoder is not None
            full_address = f"{address}, {state}, USA"
            coords = await self._geocoder.geocode(full_address)
            if coords is None:
                logger.debug("[WALKSCORE] Could not geocode '%s'", full_address)
                continue

            lat, lon = coords

            # 2. Fetch Walk Score
            data = await self._fetch_score(full_address, lat, lon)
            if data is None:
                continue

            scores = self._parse_response(data)

            # 3. Upsert walk_scores row
            result = await db.execute(
                select(WalkScore).where(
                    WalkScore.property_source == property_type,
                    WalkScore.property_source_id == source_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.latitude = lat
                existing.longitude = lon
                existing.walk_score = scores["walk_score"]
                existing.walk_description = scores["walk_description"]
                existing.transit_score = scores["transit_score"]
                existing.transit_description = scores["transit_description"]
                existing.bike_score = scores["bike_score"]
                existing.bike_description = scores["bike_description"]
                existing.fetched_at = now
            else:
                db.add(
                    WalkScore(
                        property_source=property_type,
                        property_source_id=source_id,
                        latitude=lat,
                        longitude=lon,
                        fetched_at=now,
                        **scores,
                    )
                )
                upserted += 1

            logger.debug(
                "[WALKSCORE] %s %s — walk=%s transit=%s bike=%s",
                property_type,
                source_id,
                scores["walk_score"],
                scores["transit_score"],
                scores["bike_score"],
            )

            # Small courtesy sleep between Walk Score calls (not strictly needed
            # but avoids hammering the API unnecessarily)
            await asyncio.sleep(0.1)

        await db.commit()
        return upserted

    async def enrich(self, db: AsyncSession) -> int:
        """
        Score unscored active listings and distressed properties.

        Returns the total number of new ``WalkScore`` rows inserted.
        """
        half = max(1, self._max_per_run // 2)

        listings = await self._load_unscored_listings(db, half)
        distressed = await self._load_unscored_distressed(db, half)

        logger.info(
            "[WALKSCORE] Enriching %d listings + %d distressed properties",
            len(listings),
            len(distressed),
        )

        upserted = 0
        upserted += await self._enrich_batch(db, "LISTING", listings)
        if not self._daily_limit_hit:
            upserted += await self._enrich_batch(db, "DISTRESSED", distressed)

        logger.info("[WALKSCORE] Enrichment complete — %d new score rows", upserted)
        return upserted

    # ---------------------------------------------------------------------- #
    # Async context manager                                                    #
    # ---------------------------------------------------------------------- #
    async def __aenter__(self) -> "WalkScoreEnricher":
        self._client = httpx.AsyncClient(
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        self._geocoder = NominatimGeocoder()
        await self._geocoder.__aenter__()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._geocoder:
            await self._geocoder.__aexit__(None, None, None)
            self._geocoder = None
        if self._client:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Convenience wrapper                                                           #
# --------------------------------------------------------------------------- #
async def run_walk_score_enrichment(
    db: AsyncSession,
    api_key: str,
    max_per_run: int = 100,
) -> int:
    """Enrich unscored properties with Walk Score data.  Returns row count."""
    if not api_key:
        logger.warning(
            "[WALKSCORE] No WALK_SCORE_API_KEY configured — skipping enrichment. "
            "Request a free key at https://www.walkscore.com/professional/api.php"
        )
        return 0
    async with WalkScoreEnricher(api_key=api_key, max_per_run=max_per_run) as enricher:
        return await enricher.enrich(db)
