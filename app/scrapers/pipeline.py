"""
Scraper data pipeline — validation and normalisation.

Raw dicts coming from source adapters are validated by the Pydantic models
here and then converted to the canonical shapes expected by:
    ingestion_service.ingest_active_listings()
    ingestion_service.ingest_distressed_properties()

Any record that fails validation is logged and dropped so that one bad record
from a source never aborts the whole batch.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants                                                                     #
# --------------------------------------------------------------------------- #
_PROPERTY_TYPE_MAP: Dict[str, str] = {
    # single-family
    "sfr": "SFR",
    "single family": "SFR",
    "single-family": "SFR",
    "single family residential": "SFR",
    "detached": "SFR",
    "house": "SFR",
    # multi-family
    "multi": "MULTI",
    "multi-family": "MULTI",
    "multifamily": "MULTI",
    "duplex": "MULTI",
    "triplex": "MULTI",
    "quadplex": "MULTI",
    # condo / townhouse
    "condo": "CONDO",
    "condominium": "CONDO",
    "townhouse": "TOWNHOUSE",
    "townhome": "TOWNHOUSE",
    # land / commercial
    "land": "LAND",
    "lot": "LAND",
    "commercial": "COMMERCIAL",
    "mobile": "MOBILE",
    "manufactured": "MOBILE",
}

_FORECLOSURE_STAGE_MAP: Dict[str, str] = {
    "pre": "PRE_FORECLOSURE",
    "pre_foreclosure": "PRE_FORECLOSURE",
    "pre-foreclosure": "PRE_FORECLOSURE",
    "lis pendens": "PRE_FORECLOSURE",
    "auction": "AUCTION",
    "foreclosure auction": "AUCTION",
    "sheriff sale": "AUCTION",
    "reo": "REO",
    "bank owned": "REO",
    "bank-owned": "REO",
    "real estate owned": "REO",
    "hud": "REO",
    "hud owned": "REO",
}

_STATE_ABBREVIATIONS: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}


# --------------------------------------------------------------------------- #
# Normalisation helpers                                                         #
# --------------------------------------------------------------------------- #
def _normalise_property_type(raw: Optional[str]) -> str:
    if not raw:
        return "SFR"
    key = raw.strip().lower()
    return _PROPERTY_TYPE_MAP.get(key, "SFR")


def _normalise_foreclosure_stage(raw: Optional[str]) -> str:
    if not raw:
        return "PRE_FORECLOSURE"
    key = raw.strip().lower()
    return _FORECLOSURE_STAGE_MAP.get(key, "PRE_FORECLOSURE")


def _normalise_state(raw: Optional[str]) -> str:
    if not raw:
        return ""
    stripped = raw.strip()
    if len(stripped) == 2:
        return stripped.upper()
    return _STATE_ABBREVIATIONS.get(stripped.lower(), stripped.upper()[:2])


def _clean_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _clean_int(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(float(str(raw).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _clean_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _parse_datetime(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ):
        try:
            dt = datetime.strptime(str(raw).strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Pydantic validation models                                                    #
# --------------------------------------------------------------------------- #
class ScrapedListing(BaseModel):
    """Validated representation of one scraped active listing."""

    source: str
    source_id: str
    address: str
    city: str
    state: str
    zip_code: str
    list_price: float
    property_type: str = "SFR"
    status: str = "ACTIVE"
    days_on_market: int = 0
    listed_at: str  # ISO-8601 string accepted by ingestion_service

    parcel_id: Optional[str] = None
    original_price: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, v: Any) -> str:
        return _normalise_state(v)

    @field_validator("property_type", mode="before")
    @classmethod
    def _v_ptype(cls, v: Any) -> str:
        return _normalise_property_type(v)

    @field_validator("list_price", mode="before")
    @classmethod
    def _v_price(cls, v: Any) -> float:
        cleaned = _clean_price(v)
        if cleaned is None or cleaned <= 0:
            raise ValueError(f"Invalid list_price: {v!r}")
        return cleaned

    @field_validator("original_price", mode="before")
    @classmethod
    def _v_orig_price(cls, v: Any) -> Optional[float]:
        return _clean_price(v)

    @field_validator("bedrooms", mode="before")
    @classmethod
    def _v_beds(cls, v: Any) -> Optional[int]:
        return _clean_int(v)

    @field_validator("bathrooms", mode="before")
    @classmethod
    def _v_baths(cls, v: Any) -> Optional[float]:
        return _clean_float(v)

    @field_validator("sqft", mode="before")
    @classmethod
    def _v_sqft(cls, v: Any) -> Optional[int]:
        return _clean_int(v)

    @field_validator("year_built", mode="before")
    @classmethod
    def _v_year(cls, v: Any) -> Optional[int]:
        year = _clean_int(v)
        if year and (year < 1600 or year > datetime.now().year + 2):
            return None
        return year

    @field_validator("days_on_market", mode="before")
    @classmethod
    def _v_dom(cls, v: Any) -> int:
        dom = _clean_int(v)
        return max(0, dom) if dom is not None else 0

    @model_validator(mode="before")
    @classmethod
    def _coerce_listed_at(cls, values: Any) -> Any:
        if isinstance(values, dict):
            raw = values.get("listed_at")
            dt = _parse_datetime(raw)
            values["listed_at"] = (dt or datetime.now(timezone.utc)).isoformat()
        return values


class ScrapedProperty(BaseModel):
    """Validated representation of one scraped distressed property."""

    source: str
    source_id: str
    address: str
    city: str
    state: str
    zip_code: str
    foreclosure_stage: str = "PRE_FORECLOSURE"
    property_type: str = "SFR"

    parcel_id: Optional[str] = None
    list_price: Optional[float] = None
    estimated_value: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, v: Any) -> str:
        return _normalise_state(v)

    @field_validator("property_type", mode="before")
    @classmethod
    def _v_ptype(cls, v: Any) -> str:
        return _normalise_property_type(v)

    @field_validator("foreclosure_stage", mode="before")
    @classmethod
    def _v_stage(cls, v: Any) -> str:
        return _normalise_foreclosure_stage(v)

    @field_validator("list_price", "estimated_value", mode="before")
    @classmethod
    def _v_price(cls, v: Any) -> Optional[float]:
        return _clean_price(v)

    @field_validator("bedrooms", mode="before")
    @classmethod
    def _v_beds(cls, v: Any) -> Optional[int]:
        return _clean_int(v)

    @field_validator("bathrooms", mode="before")
    @classmethod
    def _v_baths(cls, v: Any) -> Optional[float]:
        return _clean_float(v)

    @field_validator("sqft", mode="before")
    @classmethod
    def _v_sqft(cls, v: Any) -> Optional[int]:
        return _clean_int(v)

    @field_validator("year_built", mode="before")
    @classmethod
    def _v_year(cls, v: Any) -> Optional[int]:
        year = _clean_int(v)
        if year and (year < 1600 or year > datetime.now().year + 2):
            return None
        return year


# --------------------------------------------------------------------------- #
# Public normalisation API                                                      #
# --------------------------------------------------------------------------- #
def normalise_listings(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and normalise a list of raw scraped dicts into the canonical
    shape accepted by ``ingest_active_listings()``.

    Invalid records are logged and skipped.
    """
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_records):
        try:
            validated = ScrapedListing.model_validate(raw)
            out.append(validated.model_dump())
        except Exception as exc:
            logger.warning("Dropping listing record [%d] — validation error: %s", i, exc)
    return out


def normalise_properties(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Validate and normalise a list of raw scraped dicts into the canonical
    shape accepted by ``ingest_distressed_properties()``.

    Invalid records are logged and skipped.
    """
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_records):
        try:
            validated = ScrapedProperty.model_validate(raw)
            out.append(validated.model_dump())
        except Exception as exc:
            logger.warning("Dropping property record [%d] — validation error: %s", i, exc)
    return out
