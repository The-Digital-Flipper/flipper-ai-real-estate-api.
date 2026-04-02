"""
RentCast API adapter — free-tier real estate listings.

Data source
-----------
RentCast (https://www.rentcast.io/api) provides access to 140M+ US
properties via a clean REST API.

Free-tier constraints
~~~~~~~~~~~~~~~~~~~~~
- 50 API calls per month on the Developer plan.
- This adapter respects ``config.max_api_calls`` (mapped from
  ``RENTCAST_MAX_CALLS_PER_RUN`` in settings) to protect the monthly quota.
  Default: 5 calls per Celery-task run.

Endpoints used
~~~~~~~~~~~~~~
GET /v1/listings/sale
    Returns active for-sale listings filterable by state, city, ZIP code,
    property type, price range, beds/baths, and more.

GET /v1/listings/rental/long-term
    Returns active long-term rental listings with identical filter support.
    These are ingested as ActiveListing records with ``status="ACTIVE"`` so
    that the matching engine can compare them against distressed properties.

Authentication
~~~~~~~~~~~~~~
Pass the API key in the ``X-Api-Key`` request header.

Configuration (.env)
~~~~~~~~~~~~~~~~~~~~~
    RENTCAST_API_KEY=<your-key>
    RENTCAST_STATES=CA,TX,FL          # states to query (one call per state)
    RENTCAST_MAX_CALLS_PER_RUN=5      # cap per Celery run (protects free quota)

Output
------
``scrape_listings()``   → list of dicts compatible with ingest_active_listings()
``scrape_distressed()`` → empty list  (RentCast does not expose foreclosure data)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.scrapers.base import BaseScraper, ScraperConfig, ScraperError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.rentcast.io/v1"
_RESULTS_PER_PAGE = 500     # RentCast max page size


def _make_default_config(
    api_key: str,
    proxy_url: Optional[str] = None,
    requests_per_second: float = 1.0,
) -> ScraperConfig:
    return ScraperConfig(
        source_name="RENTCAST",
        base_url=_BASE_URL,
        requests_per_second=requests_per_second,
        max_retries=3,
        retry_wait_min=2.0,
        retry_wait_max=30.0,
        timeout=30.0,
        rotate_user_agents=False,
        proxy_url=proxy_url,
        api_key=api_key,
        extra_headers={
            "X-Api-Key": api_key,
            "Accept": "application/json",
        },
    )


# ---------------------------------------------------------------------------  #
# RentCast property-type values → our canonical property_type codes             #
# ---------------------------------------------------------------------------  #
_PROPERTY_TYPE_MAP: Dict[str, str] = {
    "Single Family": "SFR",
    "Multi Family": "MULTI",
    "Condo": "CONDO",
    "Townhouse": "TOWNHOUSE",
    "Manufactured": "MOBILE",
    "Land": "LAND",
    "Commercial": "COMMERCIAL",
}


def _map_property_type(raw: Optional[str]) -> str:
    if not raw:
        return "SFR"
    return _PROPERTY_TYPE_MAP.get(raw, "SFR")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RentCastScraper(BaseScraper):
    """
    Scrapes RentCast for-sale and rental listings for configurable US states.

    Parameters
    ----------
    api_key:
        Your RentCast API key (from https://www.rentcast.io/api).
    states:
        Two-letter US state codes to query (e.g. ``["CA", "TX"]``).
        One API call is made per state per listing type.
    max_api_calls:
        Hard cap on the total number of API calls this run may make.
        Keeps you within the free-tier monthly quota.
    include_rentals:
        If True (default), also fetch long-term rental listings via
        ``/v1/listings/rental/long-term``.
    config:
        Optional full ScraperConfig override.
    proxy_url:
        Convenience shortcut for the default config.
    requests_per_second:
        Target request rate.
    """

    def __init__(
        self,
        api_key: str,
        states: Optional[List[str]] = None,
        max_api_calls: int = 5,
        include_rentals: bool = True,
        config: Optional[ScraperConfig] = None,
        proxy_url: Optional[str] = None,
        requests_per_second: float = 1.0,
    ) -> None:
        super().__init__(
            config or _make_default_config(api_key, proxy_url, requests_per_second)
        )
        self._states: List[str] = states or ["CA"]
        self._max_api_calls: int = max(1, max_api_calls)
        self._include_rentals = include_rentals
        self._calls_made: int = 0

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ---------------------------------------------------------------------- #
    def _budget_remaining(self) -> int:
        return self._max_api_calls - self._calls_made

    async def _fetch_state(
        self, endpoint: str, state: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch a single state's listings from *endpoint*.
        Returns an empty list and logs a warning if the API-call budget is
        already exhausted.
        """
        if self._budget_remaining() <= 0:
            logger.warning(
                "[RENTCAST] API call budget exhausted (max=%d) — skipping %s/%s",
                self._max_api_calls,
                endpoint,
                state,
            )
            return []

        url = f"{_BASE_URL}/{endpoint}"
        params: Dict[str, Any] = {
            "state": state,
            "status": "Active",
            "limit": _RESULTS_PER_PAGE,
            "offset": 0,
        }

        try:
            resp = await self._get(url, params=params)
            self._calls_made += 1
        except ScraperError as exc:
            self._calls_made += 1  # still counts against budget
            logger.warning("[RENTCAST] %s state=%s failed: %s", endpoint, state, exc)
            return []

        data = resp.json()

        # RentCast returns either a plain list or {"listings": [...]}
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("listings", data.get("data", []))
        else:
            items = []

        logger.info(
            "[RENTCAST] %s state=%s → %d listings (call %d/%d)",
            endpoint,
            state,
            len(items),
            self._calls_made,
            self._max_api_calls,
        )
        return items

    @staticmethod
    def _map_listing(item: Dict[str, Any], listing_type: str = "SALE") -> Optional[Dict[str, Any]]:
        """Map a RentCast listing object to our canonical ActiveListing shape."""
        price = item.get("price") or item.get("listPrice") or item.get("rentPrice")
        if not price:
            return None

        source_id = str(item.get("id") or item.get("mlsNumber") or "")
        if not source_id:
            return None

        address = (
            item.get("formattedAddress")
            or item.get("addressLine1", "")
        )
        if not address:
            return None

        zip_code = str(item.get("zipCode") or item.get("zip") or "")[:5]
        listed_at = item.get("listedDate") or item.get("lastSeenDate") or _iso_now()

        # Normalise DOM — RentCast may return None
        dom_raw = item.get("daysOnMarket")
        try:
            dom = max(0, int(dom_raw)) if dom_raw is not None else 0
        except (TypeError, ValueError):
            dom = 0

        return {
            "source": "RENTCAST",
            "source_id": f"RC_{listing_type}_{source_id}",
            "address": address[:255],
            "city": item.get("city", ""),
            "state": item.get("state", ""),
            "zip_code": zip_code,
            "parcel_id": item.get("apn"),
            "list_price": float(price),
            "original_price": item.get("originalListPrice"),
            "property_type": _map_property_type(item.get("propertyType")),
            "bedrooms": item.get("bedrooms"),
            "bathrooms": item.get("bathrooms"),
            "sqft": item.get("squareFootage"),
            "year_built": item.get("yearBuilt"),
            "days_on_market": dom,
            "status": "ACTIVE",
            "listed_at": listed_at if isinstance(listed_at, str) else _iso_now(),
        }

    # ---------------------------------------------------------------------- #
    # BaseScraper interface                                                     #
    # ---------------------------------------------------------------------- #
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        """Fetch for-sale (and optionally rental) active listings for each state."""
        if not self.config.api_key:
            logger.warning("[RENTCAST] No API key configured — skipping")
            return []

        self._calls_made = 0
        all_records: List[Dict[str, Any]] = []

        # --- For-sale listings ---
        for state in self._states:
            if self._budget_remaining() <= 0:
                break
            raw = await self._fetch_state("listings/sale", state)
            for item in raw:
                rec = self._map_listing(item, "SALE")
                if rec:
                    all_records.append(rec)

        # --- Long-term rental listings ---
        if self._include_rentals:
            for state in self._states:
                if self._budget_remaining() <= 0:
                    break
                raw = await self._fetch_state("listings/rental/long-term", state)
                for item in raw:
                    rec = self._map_listing(item, "RENTAL")
                    if rec:
                        all_records.append(rec)

        logger.info(
            "[RENTCAST] Scraped %d total listing records across %d state(s) (%d API calls used)",
            len(all_records),
            len(self._states),
            self._calls_made,
        )
        return all_records

    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        """RentCast does not expose foreclosure/distressed data."""
        return []
