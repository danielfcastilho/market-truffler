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
