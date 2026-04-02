"""
FRED (Federal Reserve Economic Data) service.

Data source
-----------
FRED is a free, open government API maintained by the Federal Reserve Bank of
St. Louis.  It exposes 800,000+ US economic time-series with observations going
back decades.

    API docs:   https://fred.stlouisfed.org/docs/api/fred/
    Free key:   https://fred.stlouisfed.org/docs/api/api_key.html
    No approval needed — instant self-serve registration.

Series tracked
~~~~~~~~~~~~~~
+------------------+--------------------------------------------+----------+
| Series ID        | Name                                       | Frequency|
+==================+============================================+==========+
| MORTGAGE30US     | 30-Year Fixed Rate Mortgage Average        | Weekly   |
| MORTGAGE15US     | 15-Year Fixed Rate Mortgage Average        | Weekly   |
| CSUSHPINSA       | Case-Shiller US National Home Price Index  | Monthly  |
| HOUST            | Housing Starts (000s of units)             | Monthly  |
| MSPUS            | Median Sales Price of Houses Sold          | Quarterly|
+------------------+--------------------------------------------+----------+

Usage
-----
The service fetches the 10 most-recent observations for each series and
upserts them into the ``market_indicators`` table.  The Celery beat task
``run_fred_task`` calls it weekly (FRED updates happen weekly or monthly).

Configuration (.env)
~~~~~~~~~~~~~~~~~~~~~
    FRED_API_KEY=<your-key>     # free key from fred.stlouisfed.org

Output
------
``run_fred_update(db)`` → int (number of rows upserted)
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_indicator import MarketIndicator

logger = logging.getLogger(__name__)

_FRED_BASE = "https://api.stlouisfed.org/fred"
_TIMEOUT = 20.0
_OBSERVATIONS_LIMIT = 10   # last N observations per series


class _SeriesMeta(NamedTuple):
    series_id: str
    name: str


_SERIES: List[_SeriesMeta] = [
    _SeriesMeta("MORTGAGE30US", "30-Year Fixed Rate Mortgage Average (%)"),
    _SeriesMeta("MORTGAGE15US", "15-Year Fixed Rate Mortgage Average (%)"),
    _SeriesMeta("CSUSHPINSA",   "Case-Shiller US National Home Price Index"),
    _SeriesMeta("HOUST",        "Housing Starts (000s of units, SAAR)"),
    _SeriesMeta("MSPUS",        "Median Sales Price of Houses Sold (USD)"),
]

# FRED returns "." when a value is missing; treat as None
_MISSING = "."


class FREDService:
    """
    Fetches FRED economic time-series and persists them to the DB.

    Parameters
    ----------
    api_key:
        Your FRED API key (free from fred.stlouisfed.org).
        If omitted the request will still work at lower rate limits.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    # ---------------------------------------------------------------------- #
    # Low-level fetch                                                          #
    # ---------------------------------------------------------------------- #
    async def _fetch_observations(
        self, series_id: str
    ) -> List[Tuple[date, Optional[float]]]:
        """
        Return up to ``_OBSERVATIONS_LIMIT`` most-recent observations for
        ``series_id`` as ``(observation_date, value)`` pairs.
        """
        assert self._client is not None, "FREDService used outside async context"

        params: Dict[str, Any] = {
            "series_id": series_id,
            "sort_order": "desc",
            "limit": _OBSERVATIONS_LIMIT,
            "file_type": "json",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        url = f"{_FRED_BASE}/series/observations"
        try:
            resp = await self._client.get(url, params=params, timeout=_TIMEOUT)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("[FRED] HTTP %d for %s: %s", exc.response.status_code, series_id, exc)
            return []
        except httpx.RequestError as exc:
            logger.warning("[FRED] Request error for %s: %s", series_id, exc)
            return []

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("[FRED] JSON parse error for %s: %s", series_id, exc)
            return []

        results: List[Tuple[date, Optional[float]]] = []
        for obs in data.get("observations", []):
            raw_date = obs.get("date")
            raw_val = obs.get("value")
            if not raw_date:
                continue
            try:
                obs_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            value: Optional[float] = None
            if raw_val and raw_val != _MISSING:
                try:
                    value = float(raw_val)
                except (TypeError, ValueError):
                    pass
            results.append((obs_date, value))

        return results

    # ---------------------------------------------------------------------- #
    # Upsert into DB                                                           #
    # ---------------------------------------------------------------------- #
    async def _upsert_series(
        self,
        db: AsyncSession,
        series_id: str,
        series_name: str,
        units: Optional[str],
        observations: List[Tuple[date, Optional[float]]],
    ) -> int:
        upserted = 0
        now = datetime.utcnow()
        for obs_date, value in observations:
            result = await db.execute(
                select(MarketIndicator).where(
                    MarketIndicator.series_id == series_id,
                    MarketIndicator.observation_date == obs_date,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.value = value
                existing.fetched_at = now
            else:
                db.add(
                    MarketIndicator(
                        series_id=series_id,
                        series_name=series_name,
                        observation_date=obs_date,
                        value=value,
                        units=units,
                        fetched_at=now,
                    )
                )
                upserted += 1
        return upserted

    async def _fetch_units(self, series_id: str) -> Optional[str]:
        """Fetch the units string for a FRED series."""
        assert self._client is not None
        params: Dict[str, Any] = {"series_id": series_id, "file_type": "json"}
        if self._api_key:
            params["api_key"] = self._api_key
        try:
            resp = await self._client.get(
                f"{_FRED_BASE}/series", params=params, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json().get("seriess", [{}])[0].get("units")
        except Exception:
            return None

    # ---------------------------------------------------------------------- #
    # Public API                                                               #
    # ---------------------------------------------------------------------- #
    async def run_update(self, db: AsyncSession) -> int:
        """
        Fetch the latest observations for all tracked FRED series and upsert
        them into the ``market_indicators`` table.

        Returns the number of new rows inserted.
        """
        if not self._api_key:
            logger.warning(
                "[FRED] No FRED_API_KEY configured — requests will work "
                "but may be rate-limited.  Get a free key at "
                "https://fred.stlouisfed.org/docs/api/api_key.html"
            )

        total_new = 0
        for meta in _SERIES:
            units = await self._fetch_units(meta.series_id)
            observations = await self._fetch_observations(meta.series_id)
            if not observations:
                logger.warning("[FRED] No observations returned for %s", meta.series_id)
                continue

            new_rows = await self._upsert_series(
                db, meta.series_id, meta.name, units, observations
            )
            total_new += new_rows
            logger.info(
                "[FRED] %s — fetched %d observations, %d new rows",
                meta.series_id,
                len(observations),
                new_rows,
            )

        await db.commit()
        logger.info("[FRED] Update complete — %d new market indicator rows", total_new)
        return total_new

    async def get_latest(
        self, db: AsyncSession
    ) -> List[Dict[str, Any]]:
        """
        Return the single most-recent observation for each tracked series.
        Useful for the `/market/indicators` API endpoint.
        """
        results = []
        for meta in _SERIES:
            result = await db.execute(
                select(MarketIndicator)
                .where(MarketIndicator.series_id == meta.series_id)
                .order_by(MarketIndicator.observation_date.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            results.append(
                {
                    "series_id": meta.series_id,
                    "name": meta.name,
                    "observation_date": row.observation_date.isoformat() if row else None,
                    "value": float(row.value) if row and row.value is not None else None,
                    "units": row.units if row else None,
                    "fetched_at": row.fetched_at.isoformat() if row else None,
                }
            )
        return results

    # ---------------------------------------------------------------------- #
    # Async context manager                                                    #
    # ---------------------------------------------------------------------- #
    async def __aenter__(self) -> "FREDService":
        self._client = httpx.AsyncClient(
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# --------------------------------------------------------------------------- #
# Convenience wrappers                                                          #
# --------------------------------------------------------------------------- #
async def run_fred_update(
    db: AsyncSession,
    api_key: Optional[str] = None,
) -> int:
    """Fetch all FRED series and upsert into DB.  Returns new-row count."""
    async with FREDService(api_key=api_key) as svc:
        return await svc.run_update(db)


async def get_market_indicators(
    db: AsyncSession,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return latest observation per series (does not call FRED API)."""
    svc = FREDService(api_key=api_key)
    return await svc.get_latest(db)
