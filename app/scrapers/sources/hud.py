"""
HUD Home Store scraper — public government REO/foreclosure data.

Data source
-----------
The U.S. Department of Housing and Urban Development (HUD) publishes a
downloadable CSV of all HUD-owned (REO) properties at:

    https://www.hudhomestore.hud.gov/HudHomes/DownloadProperties.aspx?download=csv

This is explicitly public government data intended for broad distribution.
No authentication is required.

Output
------
``scrape_listings()``   → empty list  (HUD properties are distressed, not MLS listings)
``scrape_distressed()`` → list of dicts compatible with ingest_distressed_properties()
"""
from __future__ import annotations

import csv
import io
import logging
from typing import Any, Dict, List, Optional

from app.scrapers.base import BaseScraper, ScraperConfig, ScraperError

logger = logging.getLogger(__name__)

# HUD's public CSV download endpoint
_HUD_CSV_URL = (
    "https://www.hudhomestore.hud.gov/HudHomes/DownloadProperties.aspx?download=csv"
)

# Map HUD CSV column names → our internal field names.
# Column names may vary slightly; we do a case-insensitive lookup.
_COLUMN_MAP: Dict[str, str] = {
    "property address": "address",
    "city": "city",
    "state": "state",
    "zip": "zip_code",
    "zip code": "zip_code",
    "county": "_county",         # informational
    "case number": "source_id",
    "asking price": "list_price",
    "list price": "list_price",
    "bedrooms": "bedrooms",
    "baths": "bathrooms",
    "bathrooms": "bathrooms",
    "sqft": "sqft",
    "square footage": "sqft",
    "year built": "year_built",
    "property type": "property_type",
    "foreclosure type": "foreclosure_stage",
    "status": "_status",
}


def _make_default_config(proxy_url: Optional[str] = None) -> ScraperConfig:
    return ScraperConfig(
        source_name="HUD",
        base_url="https://www.hudhomestore.hud.gov",
        requests_per_second=0.5,   # single CSV download — be gentle
        max_retries=3,
        timeout=60.0,              # large file; allow extra time
        rotate_user_agents=True,
        proxy_url=proxy_url,
        extra_headers={
            "Referer": "https://www.hudhomestore.hud.gov/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )


class HUDScraper(BaseScraper):
    """
    Downloads and parses the HUD Home Store REO property CSV.

    Parameters
    ----------
    config:
        Optional ScraperConfig override.  If omitted a sensible default is used.
    proxy_url:
        Convenience shortcut — passed to the default config when *config* is None.
    """

    def __init__(
        self,
        config: Optional[ScraperConfig] = None,
        proxy_url: Optional[str] = None,
    ) -> None:
        super().__init__(config or _make_default_config(proxy_url))

    # ---------------------------------------------------------------------- #
    # Internal helpers                                                          #
    # ---------------------------------------------------------------------- #
    @staticmethod
    def _normalise_header(raw: str) -> str:
        return raw.strip().lower()

    def _parse_csv(self, text: str) -> List[Dict[str, Any]]:
        """Parse the HUD CSV text into a list of raw dicts using our column map."""
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            logger.warning("[HUD] CSV has no headers — empty result")
            return []

        # Build a mapping from CSV column index/name → our internal name
        normalised_fieldnames = [self._normalise_header(f) for f in reader.fieldnames]
        col_remap = {
            orig: _COLUMN_MAP[norm]
            for orig, norm in zip(reader.fieldnames, normalised_fieldnames)
            if norm in _COLUMN_MAP
        }

        records: List[Dict[str, Any]] = []
        for row in reader:
            rec: Dict[str, Any] = {
                "source": "HUD",
                "foreclosure_stage": "REO",  # all HUD properties are REO
                "property_type": "SFR",
            }
            for csv_col, internal_key in col_remap.items():
                val = row.get(csv_col, "").strip()
                if val and not internal_key.startswith("_"):
                    rec[internal_key] = val

            # Skip rows without a case number or address
            if not rec.get("source_id") or not rec.get("address"):
                continue

            # Ensure zip_code is a 5-digit string (HUD sometimes has 9-digit)
            if rec.get("zip_code"):
                rec["zip_code"] = str(rec["zip_code"])[:5]

            records.append(rec)

        logger.info("[HUD] Parsed %d property records from CSV", len(records))
        return records

    # ---------------------------------------------------------------------- #
    # BaseScraper interface                                                     #
    # ---------------------------------------------------------------------- #
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        """HUD properties are distressed — no active listings produced."""
        return []

    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        """Download and parse the HUD Home Store REO CSV."""
        logger.info("[HUD] Fetching REO CSV from %s", _HUD_CSV_URL)
        try:
            response = await self._get(_HUD_CSV_URL)
        except ScraperError as exc:
            logger.error("[HUD] Failed to download CSV: %s", exc)
            return []

        # HUD returns CSV as text/plain or text/csv
        content_type = response.headers.get("content-type", "")
        if "html" in content_type.lower():
            logger.error(
                "[HUD] Unexpected HTML response — HUD site may have changed. "
                "Content-Type: %s",
                content_type,
            )
            return []

        return self._parse_csv(response.text)
