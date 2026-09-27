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


async def test_get_open_interest_returns_result_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/market/open-interest"
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "BTCUSDT"
        # Always requested at the finest granularity Bybit offers — see
        # BybitClient.get_open_interest's docstring for why.
        assert request.url.params["intervalTime"] == "5min"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "list": [{"openInterest": "56055.975", "timestamp": "1790526000000"}],
                    "nextPageCursor": "",
                },
            },
        )

    client = _client(handler)
    result = await client.get_open_interest("linear", "BTCUSDT")
    assert result["list"] == [{"openInterest": "56055.975", "timestamp": "1790526000000"}]


async def test_get_open_interest_forwards_time_range_and_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["startTime"] == "1000"
        assert request.url.params["endTime"] == "2000"
        assert request.url.params["limit"] == "50"
        return httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {"category": "linear", "symbol": "BTCUSDT", "list": []},
            },
        )

    client = _client(handler)
    await client.get_open_interest("linear", "BTCUSDT", start=1000, end=2000, limit=50)


async def test_get_open_interest_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_open_interest("linear", "BTCUSDT")


async def test_get_open_interest_raises_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_open_interest("linear", "BTCUSDT")


async def test_get_open_interest_raises_on_nonzero_ret_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"retCode": 10001, "retMsg": "bad symbol", "result": {}})

    client = _client(handler)
    with pytest.raises(BybitApiError):
        await client.get_open_interest("linear", "BTCUSDT")
