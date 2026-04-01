"""
BaseScraper — shared async HTTP infrastructure for every source adapter.

Features
--------
* async httpx.AsyncClient with connection pooling and configurable timeouts
* Token-bucket rate limiter (requests_per_second per instance)
* Exponential-backoff retry with full jitter via tenacity
* Circuit breaker — opens after N consecutive failures, resets after M seconds
* Optional HTTP/SOCKS proxy forwarded to every request
* User-agent rotation from a realistic browser pool
* Structured logging on every retry, circuit-open, and permanent failure
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep_log,
    RetryError,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# User-agent pool — realistic desktop browsers to reduce bot-detection blocks  #
# --------------------------------------------------------------------------- #
_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


# --------------------------------------------------------------------------- #
# Configuration model                                                           #
# --------------------------------------------------------------------------- #
class ScraperConfig(BaseModel):
    """Runtime configuration for a single scraper source."""

    source_name: str
    base_url: str

    # Rate limiting
    requests_per_second: float = 1.0

    # Retry
    max_retries: int = 4
    retry_wait_min: float = 1.0   # seconds
    retry_wait_max: float = 60.0  # seconds

    # HTTP
    timeout: float = 30.0
    rotate_user_agents: bool = True

    # Optional proxy  (e.g. "http://user:pass@proxy-host:port")
    proxy_url: Optional[str] = None

    # API auth
    api_key: Optional[str] = None

    # Extra headers merged on every request
    extra_headers: Dict[str, str] = {}

    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: float = 120.0


# --------------------------------------------------------------------------- #
# Custom exceptions                                                             #
# --------------------------------------------------------------------------- #
class ScraperError(Exception):
    """Base class for all scraper errors."""


class RateLimitError(ScraperError):
    """Raised when the remote server returns HTTP 429."""


class CircuitOpenError(ScraperError):
    """Raised when the circuit breaker is open."""


class TransientError(ScraperError):
    """Raised for errors that are safe to retry (5xx, timeouts, connection)."""


# --------------------------------------------------------------------------- #
# BaseScraper                                                                   #
# --------------------------------------------------------------------------- #
class BaseScraper(ABC):
    """
    Abstract base for all Flipper AI source adapters.

    Sub-classes must implement:
        scrape_listings()   -> List[Dict]  # for ingest_active_listings
        scrape_distressed() -> List[Dict]  # for ingest_distressed_properties

    Typical usage::

        async with MySourceScraper(config=cfg) as scraper:
            listings   = await scraper.scrape_listings()
            distressed = await scraper.scrape_distressed()
    """

    def __init__(self, config: ScraperConfig) -> None:
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None

        # Token-bucket state
        self._min_interval: float = 1.0 / max(config.requests_per_second, 0.01)
        self._last_request_time: float = 0.0
        self._rate_lock: asyncio.Lock = asyncio.Lock()

        # Circuit-breaker state
        self._failure_count: int = 0
        self._circuit_open_until: float = 0.0

    # ---------------------------------------------------------------------- #
    # Async context-manager                                                    #
    # ---------------------------------------------------------------------- #
    async def __aenter__(self) -> "BaseScraper":
        proxies = {"all://": self.config.proxy_url} if self.config.proxy_url else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            **({"proxies": proxies} if proxies else {}),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------------- #
    # Circuit-breaker helpers                                                  #
    # ---------------------------------------------------------------------- #
    @property
    def _is_circuit_open(self) -> bool:
        now = time.monotonic()
        if self._circuit_open_until and now < self._circuit_open_until:
            return True
        if self._circuit_open_until and now >= self._circuit_open_until:
            # Half-open: allow one probe
            self._circuit_open_until = 0.0
            self._failure_count = 0
        return False

    def _on_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.config.circuit_breaker_threshold:
            self._circuit_open_until = (
                time.monotonic() + self.config.circuit_breaker_reset_seconds
            )
            logger.warning(
                "[%s] Circuit breaker OPEN — will reset in %.0fs",
                self.config.source_name,
                self.config.circuit_breaker_reset_seconds,
            )

    def _on_success(self) -> None:
        self._failure_count = 0

    # ---------------------------------------------------------------------- #
    # Rate limiter                                                              #
    # ---------------------------------------------------------------------- #
    async def _throttle(self) -> None:
        """Block until at least min_interval seconds have passed since the last request."""
        async with self._rate_lock:
            now = time.monotonic()
            gap = self._min_interval - (now - self._last_request_time)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_request_time = time.monotonic()

    # ---------------------------------------------------------------------- #
    # Request helpers                                                           #
    # ---------------------------------------------------------------------- #
    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h: Dict[str, str] = {
            "Accept": "application/json, text/html, */*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if self.config.rotate_user_agents:
            h["User-Agent"] = random.choice(_USER_AGENTS)
        h.update(self.config.extra_headers)
        if extra:
            h.update(extra)
        return h

    async def _get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """
        Perform a GET request with rate limiting, retry, and circuit breaker.
        Raises ScraperError on permanent failure.
        """
        if self._is_circuit_open:
            raise CircuitOpenError(
                f"[{self.config.source_name}] Circuit breaker is open — skipping request"
            )

        if self._client is None:
            raise RuntimeError(
                f"[{self.config.source_name}] Scraper used outside of async context manager"
            )

        def _is_transient(exc: BaseException) -> bool:
            if isinstance(exc, TransientError):
                return True
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)):
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                return exc.response.status_code in (429, 500, 502, 503, 504)
            return False

        @retry(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(self.config.max_retries + 1),
            wait=wait_exponential_jitter(
                initial=self.config.retry_wait_min,
                max=self.config.retry_wait_max,
                jitter=2.0,
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=False,
        )
        async def _attempt() -> httpx.Response:
            await self._throttle()
            try:
                resp = await self._client.get(
                    url, params=params, headers=self._headers(headers)
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    logger.warning(
                        "[%s] HTTP 429 — backing off %ds",
                        self.config.source_name,
                        retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    raise TransientError("Rate limited by server")
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                self._on_failure()
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (500, 502, 503, 504):
                    self._on_failure()
                raise

        try:
            response = await _attempt()
            self._on_success()
            return response
        except RetryError as exc:
            self._on_failure()
            raise ScraperError(
                f"[{self.config.source_name}] All retries exhausted for {url}"
            ) from exc

    async def _post(
        self,
        url: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        """Perform a POST request — same retry/circuit-breaker semantics as _get."""
        if self._is_circuit_open:
            raise CircuitOpenError(
                f"[{self.config.source_name}] Circuit breaker is open — skipping request"
            )

        if self._client is None:
            raise RuntimeError(
                f"[{self.config.source_name}] Scraper used outside of async context manager"
            )

        def _is_transient(exc: BaseException) -> bool:
            if isinstance(exc, TransientError):
                return True
            if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                return exc.response.status_code in (429, 500, 502, 503, 504)
            return False

        @retry(
            retry=retry_if_exception(_is_transient),
            stop=stop_after_attempt(self.config.max_retries + 1),
            wait=wait_exponential_jitter(
                initial=self.config.retry_wait_min,
                max=self.config.retry_wait_max,
                jitter=2.0,
            ),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=False,
        )
        async def _attempt() -> httpx.Response:
            await self._throttle()
            try:
                resp = await self._client.post(
                    url, json=json, data=data, headers=self._headers(headers)
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    raise TransientError("Rate limited by server")
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                self._on_failure()
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (500, 502, 503, 504):
                    self._on_failure()
                raise

        try:
            response = await _attempt()
            self._on_success()
            return response
        except RetryError as exc:
            self._on_failure()
            raise ScraperError(
                f"[{self.config.source_name}] All retries exhausted for {url}"
            ) from exc

    # ---------------------------------------------------------------------- #
    # Abstract interface                                                        #
    # ---------------------------------------------------------------------- #
    @property
    def source_name(self) -> str:
        return self.config.source_name

    @abstractmethod
    async def scrape_listings(self) -> List[Dict[str, Any]]:
        """
        Return a list of listing dicts compatible with
        ``ingestion_service.ingest_active_listings()``.
        """

    @abstractmethod
    async def scrape_distressed(self) -> List[Dict[str, Any]]:
        """
        Return a list of property dicts compatible with
        ``ingestion_service.ingest_distressed_properties()``.
        """
