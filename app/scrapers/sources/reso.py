"""
RESO Web API adapter — industry-standard MLS data access.

Data source
-----------
The Real Estate Standards Organization (RESO) Web API is the industry
standard for accessing MLS listing data.  It is an OData REST API; most
modern MLSes expose a RESO-compliant endpoint.

Endpoint examples:
    GET /Property?$filter=StandardStatus eq 'Active'&$top=200&$skip=0
    GET /Property?$filter=PropertyType eq 'Residential' and StandardStatus eq 'Active'

Authentication varies by MLS:
    - Bearer token  (most common)
    - Basic auth
    - Query-string API key

Configuration
-------------
Set in your ``.env``::

    RESO_API_URL  = https://api.yourmls.com/reso/odata
    RESO_API_KEY  = <bearer-token-or-api-key>

The adapter detects the auth style from the key format:
    - Keys starting with "Basic " use Basic auth header.
    - All other keys are sent as ``Authorization: Bearer <key>``.

Output
------
``scrape_listings()``   → list of dicts compatible with ingest_active_listings()
``scrape_distressed()`` → empty list  (MLS data is active listings, not distressed)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.scrapers.base import BaseScraper, ScraperConfig, ScraperError

logger = logging.getLogger(__name__)

_PAGE_SIZE = 200   # RESO servers typically support up to 200–500
_MAX_PAGES = 50    # hard cap: 10 000 records per run


def _make_default_config(
    base_url: str,
    api_key: str,
    proxy_url: Optional[str] = None,
    requests_per_second: float = 2.0,
    provider_name: str = "MLS",
) -> ScraperConfig:
    # Determine auth header style
    if api_key.startswith("Basic "):
        auth_header = api_key
    else:
        auth_header = f"Bearer {api_key}"

    return ScraperConfig(
        source_name=f"RESO_{provider_name.upper()}",
        base_url=base_url.rstrip("/"),
        requests_per_second=requests_per_second,
        max_retries=4,
        retry_wait_min=1.0,
        retry_wait_max=60.0,
        timeout=30.0,
        rotate_user_agents=False,
        proxy_url=proxy_url,
        api_key=api_key,
        extra_headers={
            "Authorization": auth_header,
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
        },
    )


class RESOScraper(BaseScraper):
    """
    Generic RESO Web API (OData) adapter.

    Works with any MLS that exposes a RESO-compliant endpoint, e.g.:
        - Spark API (FBS)
        - Bridge Interactive
        - Trestle (CoreLogic)
        - Any self-hosted RESO server

    Parameters
    ----------
    base_url:
        OData service root (e.g. ``https://api.yourmls.com/reso/odata``).
    api_key:
        Bearer token or Basic auth credential.
    property_types:
        RESO PropertyType values to include.  Defaults to residential types.
    statuses:
        RESO StandardStatus values to include.  Defaults to active.
    state_filter:
        Optional two-letter state code to restrict results.
    provider_name:
        Short label used in ``source_name`` and DB records.
    config:
        Optional full ScraperConfig override.
    proxy_url:
        Convenience shortcut for the default config.
    requests_per_second:
        Target throughput.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        property_types: Optional[List[str]] = None,
        statuses: Optional[List[str]] = None,
        state_filter: Optional[str] = None,
        provider_name: str = "MLS",
        config: Optional[ScraperConfig] = None,
        proxy_url: Optional[str] = None,
        requests_per_second: float = 2.0,
    ) -> None:
        super().__init__(
            config
            or _make_default_config(
                base_url, api_key, proxy_url, requests_per_second, provider_name
            )
        )
        self._base_url = base_url.rstrip("/")
        self._property_types: List[str] = property_types or [
            "Residential",
            "ResidentialIncome",
        ]
        self._statuses: List[str] = statuses or ["Active", "ActiveUnderContract"]
        self._state_filter: Optional[str] = state_filter
        self._provider_name = provider_name

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ---------------------------------------------------------------------- #
    def _build_filter(self) -> str:
        """Construct the OData $filter expression."""
        clauses: List[str] = []

        if self._statuses:
            status_filter = " or ".join(
                f"StandardStatus eq '{s}'" for s in self._statuses
            )
            clauses.append(f"({status_filter})")

        if self._property_types:
            type_filter = " or ".join(
                f"PropertyType eq '{t}'" for t in self._property_types
            )
            clauses.append(f"({type_filter})")

        if self._state_filter:
            clauses.append(f"StateOrProvince eq '{self._state_filter}'")

        return " and ".join(clauses)

    @staticmethod
    def _parse_date(raw: Any) -> str:
        if not raw:
            return datetime.now(timezone.utc).isoformat()
        if isinstance(raw, str):
            return raw
        if isinstance(raw, datetime):
            return raw.isoformat()
        return str(raw)

    def _map_listing(self, prop: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map a RESO Property resource to our listing shape."""
        # RESO standard field names (https://ddwiki.reso.org/display/DDW17)
        list_price = prop.get("ListPrice") or prop.get("OriginalListPrice")
        if not list_price:
            return None

        address_parts = [
            prop.get("StreetNumber", ""),
            prop.get("StreetName", ""),
            prop.get("StreetSuffix", ""),
            prop.get("UnitNumber", ""),
        ]
        address = " ".join(p for p in address_parts if p).strip()
        if not address:
            address = prop.get("UnparsedAddress", "")
        if not address:
            return None

        source_id = (
            prop.get("ListingKey")
            or prop.get("ListingId")
            or prop.get("MlsStatus")
            or ""
        )
        if not source_id:
            return None

        dom = prop.get("DaysOnMarket") or prop.get("CumulativeDaysOnMarket") or 0

        return {
            "source": self.config.source_name,
            "source_id": str(source_id),
            "address": address[:255],
            "city": prop.get("City", ""),
            "state": prop.get("StateOrProvince", ""),
            "zip_code": str(prop.get("PostalCode", ""))[:5],
            "parcel_id": prop.get("ParcelNumber"),
            "list_price": float(list_price),
            "original_price": prop.get("OriginalListPrice"),
            "property_type": prop.get("PropertyType", "SFR"),
            "bedrooms": prop.get("BedroomsTotal"),
            "bathrooms": prop.get("BathroomsTotalInteger")
            or prop.get("BathroomsFull"),
            "sqft": prop.get("LivingArea") or prop.get("AboveGradeFinishedArea"),
            "year_built": prop.get("YearBuilt"),
            "days_on_market": int(dom) if dom else 0,
            "status": prop.get("StandardStatus", "ACTIVE").upper(),
            "listed_at": self._parse_date(
                prop.get("OnMarketDate") or prop.get("ListingContractDate")
            ),
        }

    async def _fetch_page(self, skip: int) -> List[Dict[str, Any]]:
        url = f"{self._base_url}/Property"
        params: Dict[str, Any] = {
            "$filter": self._build_filter(),
            "$top": _PAGE_SIZE,
            "$skip": skip,
            "$orderby": "ModificationTimestamp desc",
            "$count": "false",
        }
        try:
            resp = await self._get(url, params=params)
        except ScraperError as exc:
            logger.warning("[%s] Page skip=%d failed: %s", self.config.source_name, skip, exc)
            return []

        data = resp.json()
        # RESO OData responses wrap results in "value"
        return data.get("value", [])

    # ---------------------------------------------------------------------- #
    # BaseScraper interface                                                     #
    # ---------------------------------------------------------------------- #
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        if not self.config.api_key:
            logger.warning("[%s] No API key configured — skipping", self.config.source_name)
            return []

        all_records: List[Dict[str, Any]] = []

        for page in range(_MAX_PAGES):
            skip = page * _PAGE_SIZE
            raw_props = await self._fetch_page(skip)
            if not raw_props:
                break

            for prop in raw_props:
                rec = self._map_listing(prop)
                if rec:
                    all_records.append(rec)

            logger.info(
                "[%s] Page %d → %d listings (total so far: %d)",
                self.config.source_name,
                page + 1,
                len(raw_props),
                len(all_records),
            )

            if len(raw_props) < _PAGE_SIZE:
                break  # last page

        return all_records

    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        """MLS active listings are not distressed properties."""
        return []
