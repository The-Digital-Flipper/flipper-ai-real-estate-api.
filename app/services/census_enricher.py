"""
US Census Bureau ACS (American Community Survey) market data enrichment service.

What it does
------------
The Census ACS 5-Year dataset is a **completely free, open US government API**
(https://api.census.gov) that provides aggregate housing statistics by
ZIP Code Tabulation Area (ZCTA), including:

    B25077_001E  — Median value of owner-occupied housing units (dollars)
    B25064_001E  — Median gross rent (dollars/month)
    B25003_001E  — Total occupied housing units
    B25003_002E  — Owner-occupied housing units
    B25003_003E  — Renter-occupied housing units

This service queries the Census API for every distinct ZIP code that appears
in our ``distressed_properties`` table where ``estimated_value`` is NULL, then
sets ``estimated_value`` to the Census median home value for that ZIP.

This gives the deal-scoring engine a baseline market value even when no ATTOM
or RESO data is present — completely free and with no third-party dependency.

Configuration (.env)
~~~~~~~~~~~~~~~~~~~~~
    CENSUS_API_KEY=<optional-key>      # request at api.census.gov/data/key_signup.html
                                       # works without a key (500 req/day limit)
    CENSUS_ACS_YEAR=2022               # ACS 5-year dataset year (default 2022)

API details
-----------
Endpoint::

    GET https://api.census.gov/data/{year}/acs/acs5
        ?get=B25077_001E,B25064_001E,B25003_001E,B25003_002E,B25003_003E
        &for=zip+code+tabulation+area:{zip5}
        &key={api_key}        # omit if no key

Response (list-of-lists, first row = headers)::

    [
      ["B25077_001E","B25064_001E","B25003_001E","B25003_002E","B25003_003E",
       "zip code tabulation area"],
      ["456700", "1850", "12345", "7000", "5345", "90210"]
    ]

A value of "-666666666" means "not available" for that ZCTA.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import DistressedProperty

logger = logging.getLogger(__name__)

_CENSUS_BASE_URL = "https://api.census.gov/data"

# ACS variable codes
_VAR_MEDIAN_HOME_VALUE = "B25077_001E"   # median owner-occupied housing value
_VAR_MEDIAN_GROSS_RENT = "B25064_001E"   # median gross rent
_VAR_TOTAL_OCCUPIED = "B25003_001E"      # total occupied units
_VAR_OWNER_OCCUPIED = "B25003_002E"      # owner-occupied units
_VAR_RENTER_OCCUPIED = "B25003_003E"     # renter-occupied units

_NOT_AVAILABLE = "-666666666"  # Census sentinel value for missing data

# Concurrent ZIP lookups (Census allows up to ~500/day without a key)
_CONCURRENCY = 5
# Timeout per request (seconds)
_TIMEOUT = 15.0


# --------------------------------------------------------------------------- #
# Data class                                                                    #
# --------------------------------------------------------------------------- #
class ZipMarketStats:
    """Census ACS housing statistics for a single ZIP code."""

    __slots__ = (
        "zip_code",
        "median_home_value",
        "median_gross_rent",
        "total_occupied",
        "owner_occupied",
        "renter_occupied",
        "owner_ratio",
    )

    def __init__(
        self,
        zip_code: str,
        median_home_value: Optional[float],
        median_gross_rent: Optional[float],
        total_occupied: Optional[int],
        owner_occupied: Optional[int],
        renter_occupied: Optional[int],
    ) -> None:
        self.zip_code = zip_code
        self.median_home_value = median_home_value
        self.median_gross_rent = median_gross_rent
        self.total_occupied = total_occupied
        self.owner_occupied = owner_occupied
        self.renter_occupied = renter_occupied
        if total_occupied and total_occupied > 0 and owner_occupied is not None:
            self.owner_ratio = round(owner_occupied / total_occupied, 4)
        else:
            self.owner_ratio = None

    def __repr__(self) -> str:
        return (
            f"ZipMarketStats(zip={self.zip_code}, "
            f"home_value=${self.median_home_value:,.0f}, "
            f"rent=${self.median_gross_rent:,.0f}/mo)"
            if self.median_home_value and self.median_gross_rent
            else f"ZipMarketStats(zip={self.zip_code}, no data)"
        )


# --------------------------------------------------------------------------- #
# Census API client                                                             #
# --------------------------------------------------------------------------- #
class CensusEnricher:
    """
    Fetches US Census ACS housing statistics and enriches distressed properties
    that are missing an ``estimated_value``.

    Parameters
    ----------
    api_key:
        Optional Census API key.  Without a key the API still works but is
        limited to 500 requests/day.
    acs_year:
        ACS 5-year dataset year to query.  Default: ``2022``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        acs_year: int = 2022,
    ) -> None:
        self._api_key = api_key
        self._acs_year = acs_year
        self._client: Optional[httpx.AsyncClient] = None

    # ---------------------------------------------------------------------- #
    # Low-level Census API fetch                                               #
    # ---------------------------------------------------------------------- #
    async def _fetch_zip_stats(self, zip_code: str) -> Optional[ZipMarketStats]:
        """Query the Census ACS API for housing statistics for *zip_code*."""
        assert self._client is not None, "CensusEnricher used outside async context"

        variables = ",".join([
            _VAR_MEDIAN_HOME_VALUE,
            _VAR_MEDIAN_GROSS_RENT,
            _VAR_TOTAL_OCCUPIED,
            _VAR_OWNER_OCCUPIED,
            _VAR_RENTER_OCCUPIED,
        ])

        params: Dict[str, Any] = {
            "get": variables,
            "for": f"zip code tabulation area:{zip_code}",
        }
        if self._api_key:
            params["key"] = self._api_key

        url = f"{_CENSUS_BASE_URL}/{self._acs_year}/acs/acs5"
        try:
            resp = await self._client.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 204:
                return None  # No data for this ZIP
            logger.warning("[CENSUS] HTTP %d for ZIP %s: %s", exc.response.status_code, zip_code, exc)
            return None
        except httpx.RequestError as exc:
            logger.warning("[CENSUS] Request error for ZIP %s: %s", zip_code, exc)
            return None

        try:
            rows = resp.json()
        except Exception as exc:
            logger.warning("[CENSUS] JSON parse error for ZIP %s: %s", zip_code, exc)
            return None

        if not rows or len(rows) < 2:
            return None  # No data returned

        # rows[0] = header, rows[1] = first (and usually only) data row
        header = rows[0]
        data_row = rows[1]

        def _parse_val(field: str) -> Optional[float]:
            try:
                idx = header.index(field)
                raw = data_row[idx]
                if raw == _NOT_AVAILABLE or raw is None:
                    return None
                return float(raw)
            except (ValueError, IndexError):
                return None

        def _parse_int(field: str) -> Optional[int]:
            v = _parse_val(field)
            return int(v) if v is not None else None

        return ZipMarketStats(
            zip_code=zip_code,
            median_home_value=_parse_val(_VAR_MEDIAN_HOME_VALUE),
            median_gross_rent=_parse_val(_VAR_MEDIAN_GROSS_RENT),
            total_occupied=_parse_int(_VAR_TOTAL_OCCUPIED),
            owner_occupied=_parse_int(_VAR_OWNER_OCCUPIED),
            renter_occupied=_parse_int(_VAR_RENTER_OCCUPIED),
        )

    # ---------------------------------------------------------------------- #
    # Bulk ZIP lookup with concurrency control                                 #
    # ---------------------------------------------------------------------- #
    async def fetch_market_stats(
        self, zip_codes: List[str]
    ) -> Dict[str, ZipMarketStats]:
        """
        Fetch Census ACS stats for all *zip_codes* concurrently.

        Returns a dict keyed by ZIP code.  ZIPs with no Census data are absent
        from the result.
        """
        semaphore = asyncio.Semaphore(_CONCURRENCY)

        async def _guarded(z: str) -> Tuple[str, Optional[ZipMarketStats]]:
            async with semaphore:
                stats = await self._fetch_zip_stats(z)
                return z, stats

        pairs = await asyncio.gather(*(_guarded(z) for z in zip_codes))
        return {z: s for z, s in pairs if s is not None and s.median_home_value is not None}

    # ---------------------------------------------------------------------- #
    # DB enrichment                                                            #
    # ---------------------------------------------------------------------- #
    async def enrich_distressed_properties(self, db: AsyncSession) -> int:
        """
        Find distressed properties with a NULL ``estimated_value``, look up
        the Census median home value for their ZIP codes, and update the field.

        Returns the number of records updated.
        """
        # 1. Find distinct ZIP codes that need enrichment
        result = await db.execute(
            select(DistressedProperty.zip_code)
            .where(
                DistressedProperty.estimated_value.is_(None),
                DistressedProperty.is_active.is_(True),
            )
            .distinct()
        )
        zip_codes: List[str] = [row[0] for row in result.fetchall() if row[0]]

        if not zip_codes:
            logger.info("[CENSUS] No distressed properties need estimated_value enrichment")
            return 0

        logger.info("[CENSUS] Fetching ACS stats for %d ZIP codes", len(zip_codes))

        # 2. Fetch Census stats for all ZIPs
        stats_map = await self.fetch_market_stats(zip_codes)

        logger.info(
            "[CENSUS] Received data for %d / %d ZIP codes",
            len(stats_map),
            len(zip_codes),
        )

        if not stats_map:
            return 0

        # 3. Update properties
        updated = 0
        result = await db.execute(
            select(DistressedProperty).where(
                DistressedProperty.estimated_value.is_(None),
                DistressedProperty.is_active.is_(True),
                DistressedProperty.zip_code.in_(list(stats_map.keys())),
            )
        )
        properties = result.scalars().all()

        for prop in properties:
            stats = stats_map.get(prop.zip_code)
            if stats and stats.median_home_value:
                prop.estimated_value = stats.median_home_value
                updated += 1

        if updated > 0:
            await db.commit()
            logger.info(
                "[CENSUS] Updated estimated_value on %d distressed properties "
                "using Census ACS median home values",
                updated,
            )

        return updated

    # ---------------------------------------------------------------------- #
    # Async context manager                                                    #
    # ---------------------------------------------------------------------- #
    async def __aenter__(self) -> "CensusEnricher":
        self._client = httpx.AsyncClient(
            headers={"Accept": "application/json"},
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Module-level convenience function                                             #
# --------------------------------------------------------------------------- #
async def run_census_enrichment(
    db: AsyncSession,
    api_key: Optional[str] = None,
    acs_year: int = 2022,
) -> int:
    """
    Convenience wrapper: open an enricher, run enrichment, return update count.

    Intended for use from Celery tasks::

        count = await run_census_enrichment(db, api_key=settings.CENSUS_API_KEY)
    """
    async with CensusEnricher(api_key=api_key, acs_year=acs_year) as enricher:
        return await enricher.enrich_distressed_properties(db)
