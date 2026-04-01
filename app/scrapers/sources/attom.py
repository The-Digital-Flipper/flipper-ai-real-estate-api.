"""
ATTOM Data Solutions API adapter.

Data source
-----------
ATTOM (https://api.developer.attomdata.com) is a commercial property-data
provider.  This adapter uses two endpoints:

    Property/zip          — active market listings filtered by ZIP code
    Property/foreclosure  — pre-foreclosure and REO records

Authentication is via the ``apikey`` request header.

Configuration
-------------
Set ``ATTOM_API_KEY`` in your ``.env`` file.  Without a key this scraper
returns empty results and logs a warning.

Output
------
``scrape_listings()``   → list of dicts compatible with ingest_active_listings()
``scrape_distressed()`` → list of dicts compatible with ingest_distressed_properties()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.scrapers.base import BaseScraper, ScraperConfig, ScraperError

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.gateway.attomdata.com/propertyapi/v1.0.0"
_PAGE_SIZE = 100


def _make_default_config(
    api_key: str,
    proxy_url: Optional[str] = None,
    requests_per_second: float = 2.0,
) -> ScraperConfig:
    return ScraperConfig(
        source_name="ATTOM",
        base_url=_BASE_URL,
        requests_per_second=requests_per_second,
        max_retries=4,
        retry_wait_min=1.0,
        retry_wait_max=60.0,
        timeout=30.0,
        rotate_user_agents=False,   # ATTOM doesn't care about UA
        proxy_url=proxy_url,
        api_key=api_key,
        extra_headers={
            "apikey": api_key,
            "Accept": "application/json",
        },
    )


class ATTOMScraper(BaseScraper):
    """
    ATTOM Data Solutions REST API adapter.

    Parameters
    ----------
    api_key:
        Your ATTOM API key.
    zip_codes:
        List of ZIP codes to query.  Required — ATTOM's endpoints are
        geographically scoped.
    config:
        Optional ScraperConfig override.
    proxy_url:
        Convenience shortcut for the default config.
    requests_per_second:
        ATTOM allows up to 10 req/s on most plans; defaults to 2.
    """

    def __init__(
        self,
        api_key: str,
        zip_codes: Optional[List[str]] = None,
        config: Optional[ScraperConfig] = None,
        proxy_url: Optional[str] = None,
        requests_per_second: float = 2.0,
    ) -> None:
        super().__init__(
            config or _make_default_config(api_key, proxy_url, requests_per_second)
        )
        self._zip_codes: List[str] = zip_codes or []

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ---------------------------------------------------------------------- #
    @staticmethod
    def _iso_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _listing_from_attom(self, prop: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map an ATTOM ``Property/zip`` response object to our listing shape."""
        address = prop.get("address", {})
        building = prop.get("building", {})
        rooms = building.get("rooms", {})
        size = building.get("size", {})
        summary = prop.get("summary", {})
        sale = prop.get("sale", {})

        street = (
            f"{address.get('line1', '')} {address.get('line2', '')}".strip()
        )
        if not street:
            return None

        # ATTOM sale amount lives in saleamt → saleAmt
        raw_price = (
            sale.get("saleCRScore")
            or prop.get("assessment", {}).get("market", {}).get("mktTtlValue")
            or 0
        )
        if not raw_price:
            return None

        return {
            "source": "ATTOM",
            "source_id": str(prop.get("identifier", {}).get("attomId", "")),
            "address": street,
            "city": address.get("locality", ""),
            "state": address.get("countrySubd", ""),
            "zip_code": str(address.get("postal1", ""))[:5],
            "parcel_id": prop.get("identifier", {}).get("apn", ""),
            "list_price": float(raw_price),
            "property_type": summary.get("proptype", "SFR"),
            "bedrooms": rooms.get("beds"),
            "bathrooms": rooms.get("bathsfull"),
            "sqft": size.get("livingsize"),
            "year_built": summary.get("yearbuilt"),
            "days_on_market": 0,
            "status": "ACTIVE",
            "listed_at": self._iso_now(),
        }

    def _distressed_from_attom(self, prop: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map an ATTOM ``Property/foreclosure`` record to our distressed shape."""
        address = prop.get("address", {})
        building = prop.get("building", {})
        rooms = building.get("rooms", {})
        size = building.get("size", {})
        summary = prop.get("summary", {})
        foreclosure = prop.get("foreclosure", {})

        street = (
            f"{address.get('line1', '')} {address.get('line2', '')}".strip()
        )
        if not street:
            return None

        stage_raw = foreclosure.get("statusDesc", "PRE_FORECLOSURE")

        return {
            "source": "ATTOM",
            "source_id": str(prop.get("identifier", {}).get("attomId", "")),
            "address": street,
            "city": address.get("locality", ""),
            "state": address.get("countrySubd", ""),
            "zip_code": str(address.get("postal1", ""))[:5],
            "parcel_id": prop.get("identifier", {}).get("apn", ""),
            "property_type": summary.get("proptype", "SFR"),
            "foreclosure_stage": stage_raw,
            "list_price": foreclosure.get("openingBid"),
            "estimated_value": prop.get("assessment", {})
            .get("market", {})
            .get("mktTtlValue"),
            "bedrooms": rooms.get("beds"),
            "bathrooms": rooms.get("bathsfull"),
            "sqft": size.get("livingsize"),
            "year_built": summary.get("yearbuilt"),
        }

    async def _fetch_page(
        self, endpoint: str, zip_code: str, page: int
    ) -> List[Dict[str, Any]]:
        url = f"{_BASE_URL}/{endpoint}"
        params: Dict[str, Any] = {
            "postalcode": zip_code,
            "page": page,
            "pagesize": _PAGE_SIZE,
        }
        try:
            resp = await self._get(url, params=params)
        except ScraperError as exc:
            logger.warning("[ATTOM] %s ZIP=%s page=%d failed: %s", endpoint, zip_code, page, exc)
            return []

        data = resp.json()
        # ATTOM wraps results in {"property": [...]}
        return data.get("property", [])

    async def _paginate(
        self, endpoint: str, zip_code: str
    ) -> List[Dict[str, Any]]:
        all_items: List[Dict[str, Any]] = []
        page = 1
        while True:
            items = await self._fetch_page(endpoint, zip_code, page)
            if not items:
                break
            all_items.extend(items)
            if len(items) < _PAGE_SIZE:
                break
            page += 1
        return all_items

    # ---------------------------------------------------------------------- #
    # BaseScraper interface                                                     #
    # ---------------------------------------------------------------------- #
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        if not self.config.api_key:
            logger.warning("[ATTOM] No API key configured — skipping")
            return []
        if not self._zip_codes:
            logger.warning("[ATTOM] No ZIP codes configured — skipping")
            return []

        results: List[Dict[str, Any]] = []
        for zip_code in self._zip_codes:
            raw = await self._paginate("property/zip", zip_code)
            for prop in raw:
                rec = self._listing_from_attom(prop)
                if rec:
                    results.append(rec)
            logger.info("[ATTOM] ZIP %s → %d listing candidates", zip_code, len(raw))

        return results

    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        if not self.config.api_key:
            logger.warning("[ATTOM] No API key configured — skipping")
            return []
        if not self._zip_codes:
            logger.warning("[ATTOM] No ZIP codes configured — skipping")
            return []

        results: List[Dict[str, Any]] = []
        for zip_code in self._zip_codes:
            raw = await self._paginate("property/foreclosure", zip_code)
            for prop in raw:
                rec = self._distressed_from_attom(prop)
                if rec:
                    results.append(rec)
            logger.info("[ATTOM] ZIP %s → %d distressed candidates", zip_code, len(raw))

        return results
