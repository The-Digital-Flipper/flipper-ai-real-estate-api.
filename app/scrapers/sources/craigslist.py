"""
Craigslist real-estate scraper — active listings via public RSS feeds.

Data source
-----------
Craigslist exposes RSS/Atom feeds specifically for automated consumption:

    https://{city}.craigslist.org/search/rea?format=rss

The feed returns up to 120 results per page and supports ``s`` (offset)
for pagination.  We honour the ``cl:price`` Atom extension element as well as
title/description parsing for fields not in the feed.

For new listings (those not yet in our system), an optional detail-page
fetch extracts bedrooms, bathrooms, sqft, and map coordinates from the
listing HTML.  Detail fetches are rate-limited independently to stay well
within Craigslist's request tolerance.

Output
------
``scrape_listings()``   → list of dicts compatible with ingest_active_listings()
``scrape_distressed()`` → empty list  (Craigslist listings are not distressed)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from app.scrapers.base import BaseScraper, ScraperConfig, ScraperError

logger = logging.getLogger(__name__)

# Namespaces used in Craigslist's RSS/Atom feed
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "cl": "http://www.craigslist.org/about/cl-base",
    "dc": "http://purl.org/dc/elements/1.1/",
    "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
}

_RESULTS_PER_PAGE = 120
_MAX_PAGES = 3  # cap at 360 results per city per run

# Regexes for extracting data from Craigslist title / HTML
_PRICE_RE = re.compile(r"\$\s*([\d,]+)")
_BR_RE = re.compile(r"(\d+)\s*br", re.IGNORECASE)
_BA_RE = re.compile(r"(\d+(?:\.\d)?)\s*ba", re.IGNORECASE)
_SQFT_RE = re.compile(r"([\d,]+)\s*(?:sq\s*ft|sqft|square\s*feet)", re.IGNORECASE)
_ZIP_RE = re.compile(r"\b(\d{5})\b")


def _make_default_config(
    proxy_url: Optional[str] = None,
    requests_per_second: float = 0.5,
) -> ScraperConfig:
    return ScraperConfig(
        source_name="CRAIGSLIST",
        base_url="https://craigslist.org",
        requests_per_second=requests_per_second,
        max_retries=3,
        retry_wait_min=2.0,
        retry_wait_max=30.0,
        timeout=20.0,
        rotate_user_agents=True,
        proxy_url=proxy_url,
        extra_headers={
            "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
        },
    )


class CraigslistScraper(BaseScraper):
    """
    Scrapes Craigslist real-estate RSS feeds across multiple cities.

    Parameters
    ----------
    cities:
        List of Craigslist subdomain names (e.g. ``["chicago", "losangeles"]``).
    fetch_details:
        If True (default), follow the listing URL for new items to extract
        bedrooms, bathrooms, sqft, etc. from the detail page.
    config:
        Optional ScraperConfig override.
    proxy_url:
        Convenience shortcut for the default config.
    requests_per_second:
        Rate limit applied to both feed and detail-page requests.
    """

    def __init__(
        self,
        cities: Optional[List[str]] = None,
        fetch_details: bool = True,
        config: Optional[ScraperConfig] = None,
        proxy_url: Optional[str] = None,
        requests_per_second: float = 0.5,
    ) -> None:
        super().__init__(
            config or _make_default_config(proxy_url, requests_per_second)
        )
        self._cities: List[str] = cities or ["chicago"]
        self._fetch_details = fetch_details

    # ---------------------------------------------------------------------- #
    # Feed parsing                                                              #
    # ---------------------------------------------------------------------- #
    def _parse_feed(self, xml_text: str, city: str) -> List[Dict[str, Any]]:
        """Parse a Craigslist RSS/Atom XML payload into raw listing dicts."""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("[CRAIGSLIST:%s] XML parse error: %s", city, exc)
            return []

        # Support both RSS (<item>) and Atom (<entry>)
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

        records: List[Dict[str, Any]] = []
        for item in items:
            rec = self._parse_item(item, city)
            if rec:
                records.append(rec)
        return records

    def _text(self, el: Optional[ET.Element]) -> str:
        return el.text.strip() if el is not None and el.text else ""

    def _parse_item(
        self, item: ET.Element, city: str
    ) -> Optional[Dict[str, Any]]:
        # --- URL / source_id ---
        link_el = item.find("link") or item.find(
            "{http://www.w3.org/2005/Atom}link"
        )
        if link_el is not None:
            url = (link_el.text or "").strip() or link_el.get("href", "").strip()
        else:
            url = ""
        if not url:
            return None
        # Craigslist listing IDs are the numeric part of the URL path
        id_match = re.search(r"/(\d{10,})\.html", url)
        source_id = id_match.group(1) if id_match else url

        # --- Title ---
        title = self._text(
            item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        )

        # --- Published date ---
        date_el = (
            item.find("pubDate")
            or item.find("{http://purl.org/dc/elements/1.1/}date")
            or item.find("{http://www.w3.org/2005/Atom}updated")
            or item.find("{http://www.w3.org/2005/Atom}published")
        )
        listed_at = self._text(date_el) or datetime.now(timezone.utc).isoformat()

        # --- Price from <cl:price> or title ---
        price_el = item.find("{http://www.craigslist.org/about/cl-base}price")
        price_str = self._text(price_el) if price_el is not None else ""
        if not price_str:
            m = _PRICE_RE.search(title)
            price_str = m.group(0) if m else ""

        price = self._parse_price(price_str)
        if not price:
            return None  # skip free / price-not-set listings

        # --- Extract bedrooms / bathrooms from title ---
        bedrooms = self._parse_int(_BR_RE, title)
        bathrooms = self._parse_float(_BA_RE, title)

        # --- Geographic hint from geo:lat/long ---
        lat_el = item.find("{http://www.w3.org/2003/01/geo/wgs84_pos#}lat")
        lng_el = item.find("{http://www.w3.org/2003/01/geo/wgs84_pos#}long")
        lat = float(self._text(lat_el)) if lat_el is not None and self._text(lat_el) else None
        lng = float(self._text(lng_el)) if lng_el is not None and self._text(lng_el) else None

        return {
            "source": "CRAIGSLIST",
            "source_id": f"CL_{source_id}",
            "address": title[:200],     # best we have until detail page is fetched
            "city": city.replace("-", " ").title(),
            "state": "",                # filled in by detail fetch or left blank
            "zip_code": "",             # filled in by detail fetch
            "list_price": price,
            "property_type": "SFR",
            "status": "ACTIVE",
            "days_on_market": 0,
            "listed_at": listed_at,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "_url": url,                # internal — used for detail fetch
            "_lat": lat,
            "_lng": lng,
        }

    @staticmethod
    def _parse_price(raw: str) -> Optional[float]:
        cleaned = re.sub(r"[^\d.]", "", raw)
        try:
            v = float(cleaned)
            return v if v > 0 else None
        except ValueError:
            return None

    @staticmethod
    def _parse_int(pattern: re.Pattern[str], text: str) -> Optional[int]:
        m = pattern.search(text)
        if m:
            try:
                return int(m.group(1))
            except (IndexError, ValueError):
                pass
        return None

    @staticmethod
    def _parse_float(pattern: re.Pattern[str], text: str) -> Optional[float]:
        m = pattern.search(text)
        if m:
            try:
                return float(m.group(1))
            except (IndexError, ValueError):
                pass
        return None

    # ---------------------------------------------------------------------- #
    # Detail-page enrichment                                                    #
    # ---------------------------------------------------------------------- #
    async def _enrich_from_detail(self, rec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch the Craigslist listing page and extract structured fields that
        are not in the RSS feed (address, zip, sqft, bedrooms, bathrooms).
        """
        url = rec.pop("_url", "")
        rec.pop("_lat", None)
        rec.pop("_lng", None)

        if not url:
            return rec
        try:
            resp = await self._get(
                url,
                headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            )
        except ScraperError as exc:
            logger.debug("[CRAIGSLIST] Detail fetch failed for %s: %s", url, exc)
            return rec

        soup = BeautifulSoup(resp.text, "html.parser")

        # Address / map
        map_el = soup.find("div", class_="mapaddress")
        if map_el:
            addr_text = map_el.get_text(strip=True)
            rec["address"] = addr_text[:200]
            zip_m = _ZIP_RE.search(addr_text)
            if zip_m:
                rec["zip_code"] = zip_m.group(1)

        # Structured attributes table
        attrs_section = soup.find("div", class_="attrgroup")
        if attrs_section:
            for span in attrs_section.find_all("span"):
                text = span.get_text(strip=True)
                br_m = _BR_RE.match(text)
                ba_m = _BA_RE.match(text)
                sqft_m = _SQFT_RE.search(text)
                if br_m and rec.get("bedrooms") is None:
                    rec["bedrooms"] = int(br_m.group(1))
                if ba_m and rec.get("bathrooms") is None:
                    rec["bathrooms"] = float(ba_m.group(1))
                if sqft_m and rec.get("sqft") is None:
                    rec["sqft"] = int(sqft_m.group(1).replace(",", ""))

        # State from breadcrumb (e.g. "chicago > real estate > ...")
        breadcrumb = soup.find("ul", id="breadcrumbs")
        if breadcrumb and not rec.get("state"):
            # Try the <title> tag: "craigslist: chicago, IL real estate..."
            title_tag = soup.find("title")
            if title_tag:
                m = re.search(r",\s*([A-Z]{2})\b", title_tag.get_text())
                if m:
                    rec["state"] = m.group(1)

        return rec

    # ---------------------------------------------------------------------- #
    # Pagination helper                                                         #
    # ---------------------------------------------------------------------- #
    async def _scrape_city(self, city: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for page in range(_MAX_PAGES):
            offset = page * _RESULTS_PER_PAGE
            feed_url = f"https://{city}.craigslist.org/search/rea"
            params: Dict[str, Any] = {"format": "rss", "s": offset}
            try:
                resp = await self._get(feed_url, params=params)
            except ScraperError as exc:
                logger.warning("[CRAIGSLIST:%s] Feed fetch failed (page %d): %s", city, page, exc)
                break

            page_records = self._parse_feed(resp.text, city)
            if not page_records:
                break   # no more results

            records.extend(page_records)

            if len(page_records) < _RESULTS_PER_PAGE:
                break   # last page

        logger.info("[CRAIGSLIST:%s] Found %d raw listings", city, len(records))
        return records

    # ---------------------------------------------------------------------- #
    # BaseScraper interface                                                     #
    # ---------------------------------------------------------------------- #
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        all_records: List[Dict[str, Any]] = []

        for city in self._cities:
            city_records = await self._scrape_city(city)

            if self._fetch_details:
                enriched: List[Dict[str, Any]] = []
                for rec in city_records:
                    enriched.append(await self._enrich_from_detail(rec))
                city_records = enriched
            else:
                for rec in city_records:
                    rec.pop("_url", None)
                    rec.pop("_lat", None)
                    rec.pop("_lng", None)

            all_records.extend(city_records)

        return all_records

    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        """Craigslist listings are not distressed properties."""
        return []
