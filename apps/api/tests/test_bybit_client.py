import httpx
import pytest

from app.integrations.bybit.client import BybitApiError, BybitClient

BASE_URL = "https://api.bybit.example"


def _client(handler) -> BybitClient:
    return BybitClient(
        base_url=BASE_URL, timeout_seconds=1.0, transport=httpx.MockTransport(handler)
    )


async def test_get_instruments_info_returns_result_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/instruments-info"
        assert request.url.params["category"] == "linear"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"category": "linear", "list": [], "nextPageCursor": ""},
            },
        )

    client = _client(handler)
    result = await client.get_instruments_info("linear")
    assert result == {"category": "linear", "list": [], "nextPageCursor": ""}


async def test_get_instruments_info_forwards_cursor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["cursor"] == "abc123"
        return httpx.Response(
            200,
            json={"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": []}},
        )

    client = _client(handler)
    await client.get_instruments_info("linear", cursor="abc123")


async def test_get_instruments_info_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_instruments_info("linear")


async def test_get_instruments_info_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_instruments_info("linear")


async def test_get_instruments_info_raises_on_nonzero_ret_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"retCode": 10001, "retMsg": "invalid category", "result": {}}
        )

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_instruments_info("linear")
