"""Thin HTTP boundary around Bybit's public v5 REST API.

Isolates transport concerns (base URL, timeouts, the retCode response
envelope) so the rest of the app never issues a Bybit HTTP request directly.
Scoped to the public market-data endpoints used today; other REST operations
and a future WebSocket client are expected to live alongside this module
without requiring it to be redesigned.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

INSTRUMENTS_INFO_PATH = "/v5/market/instruments-info"
KLINE_PATH = "/v5/market/kline"
OPEN_INTEREST_PATH = "/v5/market/open-interest"
FUNDING_HISTORY_PATH = "/v5/market/funding/history"


class BybitApiError(Exception):
    """Raised when Bybit's public API is unreachable or returns a failure response."""


class BybitClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._transport = transport

    async def get_instruments_info(
        self, category: str, cursor: str | None = None, limit: int = 1000
    ) -> dict[str, Any]:
        """Fetch one page of `/v5/market/instruments-info` for `category`.

        Returns the raw `result` object (still containing `list` and
        `nextPageCursor`) — pagination is the caller's concern.
        """
        params: dict[str, Any] = {"category": category, "limit": limit}
        if cursor:
            params["cursor"] = cursor

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(INSTRUMENTS_INFO_PATH, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise BybitApiError(f"Bybit request failed: {exc}") from exc

        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitApiError(f"Bybit returned retCode={ret_code}: {payload.get('retMsg')}")

        return payload["result"]

    async def get_kline(
        self,
        category: str,
        symbol: str,
        interval: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Fetch one page of historical `/v5/market/kline` for `symbol`.

        `start`/`end` are epoch milliseconds (Bybit's native unit). Bybit
        returns at most `limit` rows (max 1000), newest first — paging
        further back in time is the caller's concern.
        """
        params: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(KLINE_PATH, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise BybitApiError(f"Bybit request failed: {exc}") from exc

        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitApiError(f"Bybit returned retCode={ret_code}: {payload.get('retMsg')}")

        return payload["result"]

    async def get_funding_rate_history(
        self,
        category: str,
        symbol: str,
        *,
        start: int | None = None,
        end: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Fetch one page of `/v5/market/funding/history` for `symbol`.

        Unlike `get_open_interest`, this endpoint has no fixed bucket
        granularity to request: each returned entry is an actual settled
        funding event at whatever interval that instrument's contract
        really uses (commonly 1h/2h/4h/8h, and not necessarily constant
        over an instrument's lifetime) — Bybit reports the real settlement
        timestamps directly, so MARKET never has to assume or hardcode an
        interval per symbol. Returns at most `limit` rows (max 200 per
        Bybit's own cap), newest first — paging further back is the
        caller's concern via `start`/`end`, the same shape as
        `get_open_interest`.
        """
        params: dict[str, Any] = {"category": category, "symbol": symbol, "limit": limit}
        if start is not None:
            params["startTime"] = start
        if end is not None:
            params["endTime"] = end

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(FUNDING_HISTORY_PATH, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise BybitApiError(f"Bybit request failed: {exc}") from exc

        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitApiError(f"Bybit returned retCode={ret_code}: {payload.get('retMsg')}")

        return payload["result"]

    async def get_open_interest(
        self,
        category: str,
        symbol: str,
        *,
        interval_time: str = "5min",
        start: int | None = None,
        end: int | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """Fetch one page of `/v5/market/open-interest` for `symbol`.

        Bybit buckets this at a fixed granularity (`interval_time`:
        `5min`/`15min`/`30min`/`1h`/`4h`/`1d`) — MARKET always requests
        `5min`, the finest Bybit offers, so every coarser OI lookback this
        app needs (15m/1h/4h/24h are all exact multiples of 5 minutes) can
        be satisfied by an exact-timestamp lookup against that one series,
        the same way `get_kline` only ever fetches `interval="1"`. Returns
        at most `limit` rows (max 200 per Bybit's own cap), newest first —
        paging further back is the caller's concern via `cursor`.
        """
        params: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "intervalTime": interval_time,
            "limit": limit,
        }
        if start is not None:
            params["startTime"] = start
        if end is not None:
            params["endTime"] = end
        if cursor:
            params["cursor"] = cursor

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(OPEN_INTEREST_PATH, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise BybitApiError(f"Bybit request failed: {exc}") from exc

        ret_code = payload.get("retCode")
        if ret_code != 0:
            raise BybitApiError(f"Bybit returned retCode={ret_code}: {payload.get('retMsg')}")

        return payload["result"]
