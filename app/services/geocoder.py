"""
Nominatim geocoder — free address-to-coordinates service.

Data source
-----------
OpenStreetMap's Nominatim API (https://nominatim.org/) converts postal
addresses into geographic coordinates (latitude / longitude) and vice
versa using open, crowd-sourced map data.

    API docs:   https://nominatim.org/release-docs/develop/api/Search/
    No API key required.
    Rate limit: 1 request/second per IP (public endpoint).
    Terms of use: https://operations.osmfoundation.org/policies/nominatim/

This module is a thin async wrapper used internally by other services
(e.g. ``walk_score_enricher``) to resolve property addresses before
calling APIs that need coordinates.

Usage
-----
::

    from app.services.geocoder import geocode_address, reverse_geocode

    lat, lon = await geocode_address("1600 Pennsylvania Ave NW, Washington DC")
    address  = await reverse_geocode(38.8976, -77.0366)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
_USER_AGENT = "FlipperAI/1.0 (https://github.com/The-Digital-Flipper/flipper-ai-real-estate-api)"
_TIMEOUT = 15.0

# Nominatim TOS: max 1 request/second from a single IP
_REQUEST_INTERVAL = 1.1   # seconds between consecutive calls


class NominatimGeocoder:
    """
    Async geocoder backed by the public OpenStreetMap Nominatim API.

    Respects the 1-req/s rate limit using a simple asyncio.sleep between
    sequential calls.  For bulk geocoding use the ``geocode_batch`` method
    which applies the delay automatically.
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._last_call: float = 0.0

    async def _throttle(self) -> None:
        """Ensure at least ``_REQUEST_INTERVAL`` seconds between calls."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_call
        if elapsed < _REQUEST_INTERVAL:
            await asyncio.sleep(_REQUEST_INTERVAL - elapsed)
        self._last_call = asyncio.get_event_loop().time()

    async def geocode(self, address: str) -> Optional[Tuple[float, float]]:
        """
        Convert *address* to ``(latitude, longitude)``.

        Returns ``None`` if the address cannot be found.
        """
        assert self._client is not None, "NominatimGeocoder used outside async context"
        await self._throttle()

        params: Dict[str, Any] = {
            "q": address,
            "format": "json",
            "limit": 1,
            "addressdetails": 0,
            "countrycodes": "us",   # focus on US properties
        }
        try:
            resp = await self._client.get(
                f"{_NOMINATIM_BASE}/search", params=params, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            results: List[Dict[str, Any]] = resp.json()
        except Exception as exc:
            logger.warning("[GEOCODER] Geocoding failed for '%s': %s", address, exc)
            return None

        if not results:
            return None

        try:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            return lat, lon
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("[GEOCODER] Unexpected response for '%s': %s", address, exc)
            return None

    async def reverse_geocode(self, lat: float, lon: float) -> Optional[str]:
        """
        Convert ``(lat, lon)`` to a human-readable address string.

        Returns ``None`` if no address is found.
        """
        assert self._client is not None, "NominatimGeocoder used outside async context"
        await self._throttle()

        params: Dict[str, Any] = {
            "lat": lat,
            "lon": lon,
            "format": "json",
        }
        try:
            resp = await self._client.get(
                f"{_NOMINATIM_BASE}/reverse", params=params, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("display_name")
        except Exception as exc:
            logger.warning("[GEOCODER] Reverse geocoding failed for (%s, %s): %s", lat, lon, exc)
            return None

    async def geocode_batch(
        self, addresses: List[str]
    ) -> Dict[str, Optional[Tuple[float, float]]]:
        """
        Geocode a list of addresses sequentially (respecting the 1-req/s limit).

        Returns a dict mapping each input address to its ``(lat, lon)`` or
        ``None`` if unresolvable.
        """
        results: Dict[str, Optional[Tuple[float, float]]] = {}
        for addr in addresses:
            coords = await self.geocode(addr)
            results[addr] = coords
            if coords:
                logger.debug("[GEOCODER] %s → (%.5f, %.5f)", addr, coords[0], coords[1])
        return results

    async def __aenter__(self) -> "NominatimGeocoder":
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Module-level convenience functions                                            #
# --------------------------------------------------------------------------- #
async def geocode_address(address: str) -> Optional[Tuple[float, float]]:
    """
    One-shot geocoding.  Opens and closes an HTTP client for a single call.

    Prefer ``NominatimGeocoder`` as a context manager for batch work to avoid
    opening a new connection for every address.
    """
    async with NominatimGeocoder() as geocoder:
        return await geocoder.geocode(address)


async def reverse_geocode(lat: float, lon: float) -> Optional[str]:
    """One-shot reverse geocoding."""
    async with NominatimGeocoder() as geocoder:
        return await geocoder.reverse_geocode(lat, lon)
